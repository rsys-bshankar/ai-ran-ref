"""Password hashing and the GUI session token.

Password hashing is salted scrypt — the same stdlib construction SME already
uses for invoker onboarding secrets (sme/app/main.py's _hash_secret).

The session token is a compact HS256 JWT (RFC 7519). Signed with stdlib hmac
rather than a JWT library: HS256 is the only algorithm this BFF ever issues
or accepts, so the whole verifier is a fixed header check plus one
constant-time HMAC compare — no `alg` negotiation to get wrong.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P, _SCRYPT_DKLEN = 2**14, 8, 1, 32
_JWT_HEADER = {"alg": "HS256", "typ": "JWT"}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN)
    return f"{salt.hex()}:{digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split(":", 1)
        salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
    except ValueError:
        return False
    actual = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=len(expected))
    return hmac.compare_digest(actual, expected)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _sign(signing_input: bytes, secret: str) -> bytes:
    return hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()


def issue_jwt(claims: dict, secret: str, ttl_seconds: int) -> str:
    now = int(time.time())
    payload = {**claims, "iat": now, "exp": now + ttl_seconds}
    signing_input = f"{_b64(json.dumps(_JWT_HEADER, separators=(',', ':')).encode())}.{_b64(json.dumps(payload, separators=(',', ':')).encode())}"
    return f"{signing_input}.{_b64(_sign(signing_input.encode(), secret))}"


def decode_jwt(token: str, secret: str) -> dict | None:
    """Returns the claims of a valid, unexpired token signed with `secret`,
    else None. Never raises on malformed input.
    """
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
        if json.loads(_unb64(header_b64)) != _JWT_HEADER:
            return None
        if not hmac.compare_digest(_unb64(sig_b64), _sign(f"{header_b64}.{payload_b64}".encode(), secret)):
            return None
        claims = json.loads(_unb64(payload_b64))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(claims, dict) or not isinstance(claims.get("exp"), int) or claims["exp"] <= time.time():
        return None
    return claims
