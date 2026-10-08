"""The key that signs the GUI session token (PR-SEC-5).

Three algorithms, one of them chosen by `GUI_JWT_ALGORITHM`:

  HS256 (default)  the shared secret `GUI_JWT_SECRET`, signed by `security.issue_jwt` exactly as before this module existed. The token header is
                   `{"alg":"HS256","typ":"JWT"}` with no `kid`, so a session issued by the release before this one keeps working across the upgrade.
  RS256 / ES256    an asymmetric key pair: the private key from the file `GUI_JWT_PRIVATE_KEY_FILE` signs, the public half verifies and is published at
                   `/.well-known/jwks.json`. Every token carries a `kid` (the RFC 7638 thumbprint of the public key). Keys of an earlier rotation are listed
                   in `GUI_JWT_PREVIOUS_KEY_FILES`: they only verify, never sign, and a token carrying their `kid` is accepted while they stay listed.

What a verifier accepts, in asymmetric mode: a token whose header `alg` is exactly the configured one (so `none`, `HS256` signed with the public key as if it
were a secret, and the other asymmetric algorithm are all refused before any key is touched), whose `kid` names a key of the set (the header never supplies a key:
`jwk`, `jku` and `x5c` are ignored), whose signature verifies, and whose `exp` is an integer in the future. Anything else is `None`, never an exception.

Switching the algorithm ends every session that was issued under the other one: an HS256 token is not accepted by an RS256 BFF, even though the BFF still holds
`GUI_JWT_SECRET` (it keeps signing the short login challenge of the one-time-code step). Accepting it would keep a shared secret alive as a second way to forge
a session, which is what moving to a key pair is meant to end.
"""

import base64
import hashlib
import json
import time
from typing import Protocol

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from .security import decode_jwt, issue_jwt

ALGORITHMS = ("HS256", "RS256", "ES256")
MIN_RSA_BITS = 2048


class Signer(Protocol):
    algorithm: str

    def issue(self, claims: dict, ttl_seconds: int) -> str: ...

    def decode(self, token: str) -> dict | None: ...

    def jwks(self) -> dict: ...


class HmacSigner:
    """HS256 with the shared secret. The secret is read from the settings object on every call, because a BFF started without `GUI_JWT_SECRET` replaces its
    per-process value with the one stored in the database after the app is built (`main.py`, the lifespan)."""

    algorithm = "HS256"

    def __init__(self, cfg) -> None:
        self._cfg = cfg

    def issue(self, claims: dict, ttl_seconds: int) -> str:
        return issue_jwt(claims, self._cfg.jwt_secret, ttl_seconds)

    def decode(self, token: str) -> dict | None:
        return decode_jwt(token, self._cfg.jwt_secret)

    def jwks(self) -> dict:
        return {"keys": []}               # a shared secret has no public half: never published


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _load(pem: str, algorithm: str, *, must_be_private: bool, label: str):
    """The key in `pem` (a PEM private key, or for a previous key a public one), checked against `algorithm`. Returns (private key or None, public key)."""
    data = pem.encode()
    private = None
    try:
        private = serialization.load_pem_private_key(data, password=None)
        public = private.public_key()
    except Exception as exc:       # noqa: BLE001 (cryptography raises ValueError, TypeError and UnsupportedAlgorithm: any of them is "not a usable private key")
        if must_be_private:
            raise ValueError(f"{label} does not hold an unencrypted PEM private key ({type(exc).__name__})") from exc
        try:
            public = serialization.load_pem_public_key(data)
        except Exception as exc2:                          # noqa: BLE001
            raise ValueError(f"{label} holds neither a PEM private key nor a PEM public key ({type(exc2).__name__})") from exc2
    if algorithm == "RS256":
        if not isinstance(public, rsa.RSAPublicKey):
            raise ValueError(f"{label} is not an RSA key, which GUI_JWT_ALGORITHM=RS256 needs")
        if public.key_size < MIN_RSA_BITS:
            raise ValueError(f"{label} is an RSA key of {public.key_size} bits: at least {MIN_RSA_BITS} are needed")
    else:
        if not isinstance(public, ec.EllipticCurvePublicKey) or not isinstance(public.curve, ec.SECP256R1):
            raise ValueError(f"{label} is not an EC key on curve P-256 (prime256v1), which GUI_JWT_ALGORITHM=ES256 needs")
    return private, public


def _jwk(public, algorithm: str) -> dict:
    """The public key as a JWK with its RFC 7638 thumbprint as `kid`."""
    algo = jwt.algorithms.RSAAlgorithm if algorithm == "RS256" else jwt.algorithms.ECAlgorithm
    jwk = json.loads(algo.to_jwk(public))
    members = ("e", "kty", "n") if algorithm == "RS256" else ("crv", "kty", "x", "y")
    canonical = json.dumps({m: jwk[m] for m in members}, separators=(",", ":"), sort_keys=True)
    kid = _b64(hashlib.sha256(canonical.encode()).digest())
    return {**{m: jwk[m] for m in members}, "use": "sig", "alg": algorithm, "kid": kid}


class AsymmetricSigner:
    """RS256 or ES256: sign with one private key, verify with the public keys of the current and the previous rotations."""

    def __init__(self, algorithm: str, private_pem: str, previous_pems: list[tuple[str, str]] | None = None) -> None:
        if algorithm not in ("RS256", "ES256"):
            raise ValueError(f"{algorithm} is not an asymmetric algorithm")
        self.algorithm = algorithm
        private, public = _load(private_pem, algorithm, must_be_private=True, label="GUI_JWT_PRIVATE_KEY_FILE")
        self._private = private
        current = _jwk(public, algorithm)
        self.kid: str = current["kid"]
        self._jwks: list[dict] = [current]
        self._public: dict[str, object] = {self.kid: public}
        for label, pem in previous_pems or []:
            _, old_public = _load(pem, algorithm, must_be_private=False, label=label)
            old = _jwk(old_public, algorithm)
            if old["kid"] in self._public:
                continue                                    # the same key listed twice, or the current key left in the list
            self._jwks.append(old)
            self._public[old["kid"]] = old_public

    def issue(self, claims: dict, ttl_seconds: int) -> str:
        now = int(time.time())
        payload = {**claims, "iat": now, "exp": now + ttl_seconds}
        return jwt.encode(payload, self._private, algorithm=self.algorithm, headers={"kid": self.kid})

    def decode(self, token: str) -> dict | None:
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != self.algorithm:        # none, HS256 under the public key as a secret, or the other asymmetric algorithm
                return None
            kid = header.get("kid")
            key = self._public.get(kid) if isinstance(kid, str) else None
            if key is None:
                return None
            # Signature only: the lifetime is checked below, with the same rule as the HS256 verifier (an integer `exp`, no `iat` / `nbf` test, so a replica whose clock is
            # a second ahead does not refuse a session another replica just issued).
            claims = jwt.decode(token, key, algorithms=[self.algorithm],       # type: ignore[arg-type]
                                options={"verify_exp": False, "verify_iat": False, "verify_nbf": False, "verify_aud": False, "verify_iss": False})
        except (jwt.PyJWTError, ValueError, TypeError):
            return None
        if not isinstance(claims, dict) or not isinstance(claims.get("exp"), int) or isinstance(claims["exp"], bool) or claims["exp"] <= time.time():
            return None
        return claims

    def jwks(self) -> dict:
        return {"keys": [dict(k) for k in self._jwks]}


def build_signer(cfg) -> Signer:
    """The session signer for these settings; raises ValueError (so the start is refused) for a combination that would otherwise be silently wrong."""
    algorithm = cfg.jwt_algorithm
    if algorithm not in ALGORITHMS:
        raise ValueError(f"GUI_JWT_ALGORITHM={algorithm!r} is not one of {', '.join(ALGORITHMS)}")
    if algorithm == "HS256":
        if cfg.jwt_private_key or cfg.jwt_previous_keys:
            raise ValueError("GUI_JWT_PRIVATE_KEY_FILE / GUI_JWT_PREVIOUS_KEY_FILES are set but GUI_JWT_ALGORITHM is HS256: set GUI_JWT_ALGORITHM=RS256 or ES256, or unset the key files")
        return HmacSigner(cfg)
    if not cfg.jwt_private_key:
        raise ValueError(f"GUI_JWT_ALGORITHM={algorithm} needs GUI_JWT_PRIVATE_KEY_FILE (a PEM private key)")
    return AsymmetricSigner(algorithm, cfg.jwt_private_key, list(cfg.jwt_previous_keys))
