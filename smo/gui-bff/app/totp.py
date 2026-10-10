"""One-time codes for local GUI accounts (PR-SEC-7): TOTP (RFC 6238), the secret's encryption at rest, and recovery codes.

TOTP itself is the standard library: HMAC-SHA1 (RFC 4226 dynamic truncation), a 30 s step, 6 digits, base32 secrets (the form every
authenticator app takes). A code is accepted for the step before, the current one and the step after (a clock a few seconds off), and
only once: the caller keeps the last step used and `verify` refuses a step that is not newer, so a code seen on the wire or over a
shoulder does not work a second time.

The secret is stored encrypted with AES-256-GCM (`cryptography`, already a locked dependency through `pyjwt[crypto]`). The key is derived
from `GUI_TOTP_KEY` with HKDF-SHA256, one derivation per purpose, so the key that encrypts secrets is not the one that keys the recovery
code hashes. The user name is the AEAD's associated data: a ciphertext copied to another user's row does not decrypt.

Recovery codes are about 79 bits of randomness (16 characters of an unambiguous base32 alphabet, shown as four groups). A hash of something that
random cannot be searched offline, so a slow password hash (scrypt) would buy nothing and cost a second of CPU on every sign-in that tries
each stored code; HMAC-SHA256 under a key derived from `GUI_TOTP_KEY` is stored instead, which also means a copy of the database alone
cannot even be tested against guesses.
"""

import base64
import hashlib
import hmac
import secrets
import struct
from urllib.parse import quote, urlencode

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

STEP_SECONDS = 30
DIGITS = 6
WINDOW = 1                      # steps either side of the current one
SECRET_BYTES = 20               # 160 bits, the HMAC-SHA1 block recommendation of RFC 4226
RECOVERY_CODE_COUNT = 10
_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"      # no i, l, o, 0, 1: 31 symbols, ~4.95 bits each
_RECOVERY_CHARS = 16
_ENVELOPE = "v1:"


class TotpKeyError(RuntimeError):
    """No usable key, or a stored secret that does not decrypt under it."""


def hotp(secret: bytes, counter: int, digits: int = DIGITS) -> str:
    """RFC 4226: HMAC-SHA1 over the 8-byte counter, dynamic truncation, `digits` decimal digits."""
    digest = hmac.new(secret, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10 ** digits).zfill(digits)


def new_secret() -> str:
    """A fresh base32 secret without padding (what authenticator apps expect)."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode().rstrip("=")


def _decode_secret(secret_b32: str) -> bytes:
    return base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8), casefold=True)


def time_step(now: float) -> int:
    return int(now // STEP_SECONDS)


def code_at(secret_b32: str, now: float) -> str:
    return hotp(_decode_secret(secret_b32), time_step(now))


def verify(secret_b32: str, code: str, now: float, last_step: int | None) -> int | None:
    """The time step `code` is valid for, or None. Steps at or before `last_step` (a code already used) are refused, so a code works once.
    All candidates are compared in constant time, and the whole window is always evaluated."""
    code = code.strip().replace(" ", "")
    if len(code) != DIGITS or not code.isascii() or not code.isdigit():
        return None
    secret = _decode_secret(secret_b32)
    current, found = time_step(now), None
    for step in range(current - WINDOW, current + WINDOW + 1):
        if hmac.compare_digest(hotp(secret, step), code) and (last_step is None or step > last_step):
            found = step if found is None else max(found, step)
    return found


def provisioning_uri(issuer: str, account: str, secret_b32: str) -> str:
    """The otpauth:// URI an authenticator app reads (the Key URI Format): typed in by hand, there is no QR image (no rendering dependency)."""
    label = quote(f"{issuer}:{account}", safe="")
    return f"otpauth://totp/{label}?" + urlencode({"secret": secret_b32, "issuer": issuer, "algorithm": "SHA1", "digits": DIGITS, "period": STEP_SECONDS},
                                                  quote_via=quote)


# ---------------------------------------------------------------- keys and the secret at rest

def _derive(key: str, purpose: bytes) -> bytes:
    if not key:
        raise TotpKeyError("GUI_TOTP_KEY is not configured")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"smo-gui-totp/" + purpose).derive(key.encode())


def encrypt_secret(key: str, username: str, secret_b32: str) -> str:
    """The envelope (`v1:` plus base64 of nonce and AES-256-GCM ciphertext) that stores `secret_b32` at rest. A fresh random nonce is used for every call, and `username` is the
    authenticated associated data, so the ciphertext only decrypts for the same user. Raises TotpKeyError when `key` is empty.
    """
    nonce = secrets.token_bytes(12)
    sealed = AESGCM(_derive(key, b"secret")).encrypt(nonce, secret_b32.encode(), username.encode())
    return _ENVELOPE + base64.urlsafe_b64encode(nonce + sealed).decode()


def decrypt_secret(key: str, username: str, stored: str) -> str:
    """The base32 secret inside `stored` for `username`. Raises TotpKeyError, one error for every cause, when `key` is empty, the envelope is not `v1:`, or the
    ciphertext does not authenticate: a wrong key, a changed row, or a row copied from another user.
    """
    try:
        if not stored.startswith(_ENVELOPE):
            raise ValueError("unknown format")
        raw = base64.urlsafe_b64decode(stored[len(_ENVELOPE):])
        return AESGCM(_derive(key, b"secret")).decrypt(raw[:12], raw[12:], username.encode()).decode()
    except (InvalidTag, ValueError) as exc:
        raise TotpKeyError("the stored one-time-code secret cannot be decrypted with GUI_TOTP_KEY") from exc


# ---------------------------------------------------------------- recovery codes

def new_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """`count` fresh recovery codes in the form `xxxx-xxxx-xxxx-xxxx`, from a cryptographically secure source and an alphabet without look-alike characters. Only their keyed
    hashes (`hash_recovery_code`) are ever stored; the codes are shown to the user once.
    """
    codes = []
    for _ in range(count):
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(_RECOVERY_CHARS))
        codes.append("-".join(raw[i:i + 4] for i in range(0, _RECOVERY_CHARS, 4)))
    return codes


def normalise_recovery_code(code: str) -> str:
    return code.strip().lower().replace("-", "").replace(" ", "")


def looks_like_recovery_code(code: str) -> bool:
    """True when `code`, ignoring case, dashes and spaces, has the shape of a recovery code (16 characters of its alphabet). Used only to choose which check to run;
    it says nothing about whether the code is valid.
    """
    plain = normalise_recovery_code(code)
    return len(plain) == _RECOVERY_CHARS and all(c in _RECOVERY_ALPHABET for c in plain)


def hash_recovery_code(key: str, username: str, code: str) -> str:
    return hmac.new(_derive(key, b"recovery"), f"{username}\0{normalise_recovery_code(code)}".encode(), hashlib.sha256).hexdigest()
