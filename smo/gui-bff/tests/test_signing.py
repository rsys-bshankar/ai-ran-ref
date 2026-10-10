"""PR-SEC-5: the session token signed with RS256 or ES256, the key set for rotation, and the JWKS route.

HS256 stays the default and is not changed by any of this: the first group of tests pins that (the header of an issued token, a token issued by the release
before this one, the empty key set). The others generate throwaway keys; none is a fixture file.
"""

import base64
import hashlib
import hmac
import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Database
from app.main import SESSION_COOKIE, create_app, seed_users
from app.security import issue_jwt
from app.signing import AsymmetricSigner, HmacSigner, _b64, build_signer
from app.smo_client import R1Gateway
from test_main import PASSWORDS, R1, FakeSmo
from test_mfa import KEY, enrol, sign_in_with_code

SECRET = "test-secret"


def pem_private(key) -> str:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()


def pem_public(key) -> str:
    return key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def new_key(algorithm):
    return rsa.generate_private_key(public_exponent=65537, key_size=2048) if algorithm == "RS256" else ec.generate_private_key(ec.SECP256R1())


@pytest.fixture(scope="module")
def keys():
    return {"RS256": [new_key("RS256"), new_key("RS256")], "ES256": [new_key("ES256"), new_key("ES256")]}


def settings(**over) -> Settings:
    """`Settings` for an HS256 BFF with the usual test users and secret; `over` changes any field."""
    base = dict(r1_url=R1, jwt_secret=SECRET, cookie_secure=False, admin_password=PASSWORDS["admin"], operator_password=PASSWORDS["operator"],
                viewer_password=PASSWORDS["viewer"])
    base.update(over)
    return Settings(**base)


def asym_settings(keys, algorithm, *, previous=(), **over) -> Settings:
    return settings(jwt_algorithm=algorithm, jwt_private_key=pem_private(keys[algorithm][0]),
                    jwt_previous_keys=[(f"previous {i}", pem) for i, pem in enumerate(previous)], **over)


def start(cfg, db=None, smo=None):
    """Seeds the users and builds the BFF app for `cfg` on `db` (a new in-memory database by default) with the fake SMO behind the gateway.
    """
    db = db or Database("sqlite://")
    seed_users(db, cfg)
    return create_app(cfg, db=db, gateway=R1Gateway(R1, db, transport=httpx.MockTransport((smo or FakeSmo()).handler)))


def sign_in(app, username="viewer") -> TestClient:
    """Signs `username` in by password and returns a client holding the session cookie, with the CSRF header set."""
    client = TestClient(app)
    resp = client.post("/api/login", json={"username": username, "password": PASSWORDS[username]})
    assert resp.status_code == 200, resp.text
    client.headers["X-CSRF-Token"] = resp.json()["csrfToken"]
    return client


def header_of(token: str) -> dict:
    """The decoded JOSE header of a compact JWT, read without verifying it."""
    segment = token.split(".")[0]
    return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))


def forged(header: dict, claims: dict, signature: bytes = b"x") -> str:
    """Builds a compact token from any `header` and `claims` and an arbitrary `signature`, for the cases where the test must send something no signer would issue.
    """
    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()
    return f"{b64(json.dumps(header).encode())}.{b64(json.dumps(claims).encode())}.{b64(signature)}"


# ---------------------------------------------------------------- HS256 is the default and does not change

def test_the_default_is_hs256_with_the_header_the_release_before_issued():
    """With no setting the algorithm is HS256 and the token header is exactly `{alg, typ}` with no `kid`, as the previous release issued it.
    """
    cfg = settings()
    assert cfg.jwt_algorithm == "HS256" and cfg.jwt_private_key == "" and cfg.jwt_previous_keys == []
    client = sign_in(start(cfg))
    assert header_of(client.cookies[SESSION_COOKIE]) == {"alg": "HS256", "typ": "JWT"}       # no kid: byte-for-byte the header of before


def test_a_session_issued_before_the_upgrade_still_works_under_the_default():
    """A session token made the way the previous release made it is still accepted after the upgrade, so users stay signed in."""
    old = issue_jwt({"sub": "viewer", "ver": 0, "csrf": "c", "jti": "j"}, SECRET, 3600)     # what the previous release's issue_session signed
    client = TestClient(start(settings()))
    client.cookies.set(SESSION_COOKIE, old)
    assert client.get("/api/me").json()["username"] == "viewer"


def test_hs256_publishes_an_empty_key_set_and_never_the_secret():
    """Under HS256 the JWKS route answers an empty key list and nothing of the secret."""
    resp = TestClient(start(settings())).get("/.well-known/jwks.json")
    assert resp.status_code == 200 and resp.json() == {"keys": []}
    assert SECRET not in resp.text


def test_hs256_signer_follows_a_secret_replaced_after_the_app_is_built():
    """The HS256 signer reads the secret on each call, so the key adopted from the database at start-up applies and tokens made with the earlier value stop verifying.
    """
    cfg = settings()
    signer = build_signer(cfg)
    assert isinstance(signer, HmacSigner)
    token = signer.issue({"sub": "a"}, 60)
    cfg.jwt_secret = "stored-in-the-database"          # the lifespan does this when GUI_JWT_SECRET is unset
    assert signer.decode(token) is None
    assert signer.decode(signer.issue({"sub": "a"}, 60))["sub"] == "a"


# ---------------------------------------------------------------- RS256 and ES256

# The test runs once for RS256 and once for ES256, so both asymmetric algorithms meet the same rule.
# For each: the session and Bearer tokens carry the algorithm and a `kid`, work, and are revoked by logout.
@pytest.mark.parametrize("algorithm", ["RS256", "ES256"])
def test_login_me_bearer_and_logout_work_with_each_algorithm(keys, algorithm):
    app = start(asym_settings(keys, algorithm))
    client = sign_in(app, "operator")
    token = client.cookies[SESSION_COOKIE]
    header = header_of(token)
    assert header["alg"] == algorithm and header["typ"] == "JWT" and header["kid"]
    assert client.get("/api/me").json()["role"] == "operator"
    bearer = TestClient(app)
    grant = bearer.post("/api/token", data={"grant_type": "password", "username": "viewer", "password": PASSWORDS["viewer"]})
    assert grant.status_code == 200
    assert header_of(grant.json()["access_token"])["alg"] == algorithm
    assert bearer.get("/api/me", headers={"Authorization": f"Bearer {grant.json()['access_token']}"}).json()["username"] == "viewer"
    assert client.post("/api/logout").status_code == 200
    stale = TestClient(app)
    stale.cookies.set(SESSION_COOKIE, token)
    assert stale.get("/api/me").status_code == 401          # the revocation list still works (jti)


# The test runs once for RS256 and once for ES256, so both asymmetric algorithms meet the same rule.
# A standard JWT library verifies the session token with the key from the JWKS route.
@pytest.mark.parametrize("algorithm", ["RS256", "ES256"])
def test_the_token_is_a_standard_jwt_that_verifies_against_the_published_key(keys, algorithm):
    app = start(asym_settings(keys, algorithm))
    token = sign_in(app).cookies[SESSION_COOKIE]
    published = TestClient(app).get("/.well-known/jwks.json").json()["keys"]
    jwk = next(k for k in published if k["kid"] == header_of(token)["kid"])
    claims = jwt.decode(token, jwt.PyJWK(jwk).key, algorithms=[algorithm])
    assert claims["sub"] == "viewer" and claims["jti"] and claims["csrf"]


# The test runs once for RS256 and once for ES256, so both asymmetric algorithms meet the same rule.
# The one-time-code sign-in works with an asymmetric session key, and the login challenge stays an HS256 token that is not accepted as a session.
@pytest.mark.parametrize("algorithm", ["RS256", "ES256"])
def test_mfa_sign_in_works_with_each_algorithm_and_the_challenge_is_not_a_session(keys, algorithm, monkeypatch):
    real = time.time
    offset = [0.0]
    monkeypatch.setattr(time, "time", lambda: real() + offset[0])

    class Clock:
        def advance(self, seconds):
            offset[0] += seconds

    app = start(asym_settings(keys, algorithm, totp_key=KEY))
    secret, _ = enrol(app, Clock(), "operator")
    client = sign_in_with_code(app, Clock(), "operator", secret)
    assert header_of(client.cookies[SESSION_COOKIE])["alg"] == algorithm
    assert client.get("/api/me").status_code == 200
    # the login challenge is an HS256 token under a key derived from GUI_JWT_SECRET, whatever the session algorithm is
    first = TestClient(app).post("/api/login", json={"username": "operator", "password": PASSWORDS["operator"]}).json()
    assert first["mfaRequired"] is True and header_of(first["challenge"])["alg"] == "HS256"
    probe = TestClient(app)
    probe.cookies.set(SESSION_COOKIE, first["challenge"])
    assert probe.get("/api/me").status_code == 401


# ---------------------------------------------------------------- what a verifier refuses

# The test runs once for RS256 and once for ES256, so both asymmetric algorithms meet the same rule.
# Tokens with `alg` none, a stripped signature, the other asymmetric algorithm or any HMAC algorithm are refused before a key is used.
@pytest.mark.parametrize("algorithm", ["RS256", "ES256"])
def test_alg_none_and_a_wrong_algorithm_are_refused(keys, algorithm):
    signer = build_signer(asym_settings(keys, algorithm))
    good = signer.issue({"sub": "viewer"}, 60)
    assert signer.decode(good)["sub"] == "viewer"
    claims = {"sub": "admin", "exp": int(time.time()) + 600}
    assert signer.decode(forged({"alg": "none", "typ": "JWT", "kid": signer.kid}, claims, b"")) is None
    assert signer.decode(f"{good.split('.')[0]}.{good.split('.')[1]}.") is None                 # signature stripped
    other = "ES256" if algorithm == "RS256" else "RS256"
    foreign = jwt.encode(claims, pem_private(keys[other][0]), algorithm=other, headers={"kid": signer.kid})
    assert signer.decode(foreign) is None                                                     # the other asymmetric algorithm, even under a known kid
    for alg in ("HS256", "HS384", "HS512", "None", "", None, 5):
        assert signer.decode(forged({"alg": alg, "typ": "JWT", "kid": signer.kid}, claims)) is None


# The test runs once for RS256 and once for ES256, so both asymmetric algorithms meet the same rule.
# The classic confusion attack: an HS256 token keyed with the published public key, in several encodings, is refused by the signer and by the app.
@pytest.mark.parametrize("algorithm", ["RS256", "ES256"])
def test_algorithm_confusion_an_hs256_token_signed_with_the_public_key_is_refused(keys, algorithm):
    signer = build_signer(asym_settings(keys, algorithm))
    public_pem = pem_public(keys[algorithm][0])
    kid = signer.kid
    claims = {"sub": "admin", "ver": 0, "csrf": "c", "jti": "j", "exp": int(time.time()) + 600}

    def hs256_with(secret: bytes, header: dict) -> str:
        head = forged(header, claims).rsplit(".", 1)[0]
        return f"{head}.{_b64(hmac.new(secret, head.encode(), hashlib.sha256).digest())}"

    der = base64.b64decode("".join(public_pem.splitlines()[1:-1]))
    for secret in (public_pem.encode(), public_pem.strip().encode(), der):
        for header in ({"alg": "HS256", "typ": "JWT", "kid": kid}, {"alg": "HS256", "typ": "JWT"}):
            assert signer.decode(hs256_with(secret, header)) is None
    app = start(asym_settings(keys, algorithm))
    forgery = hs256_with(public_pem.encode(), {"alg": "HS256", "typ": "JWT", "kid": kid})
    assert TestClient(app).get("/api/me", headers={"Authorization": f"Bearer {forgery}"}).status_code == 401
    assert TestClient(app).get("/api/me", headers={"Authorization": f"Bearer {hs256_with(SECRET.encode(), {'alg': 'HS256', 'typ': 'JWT'})}"}).status_code == 401


# The test runs once for RS256 and once for ES256, so both asymmetric algorithms meet the same rule.
# A token with no `kid`, an unknown or non-string `kid`, or a known `kid` signed by another key is refused.
@pytest.mark.parametrize("algorithm", ["RS256", "ES256"])
def test_unknown_missing_or_odd_kid_and_a_foreign_key_are_refused(keys, algorithm):
    signer = build_signer(asym_settings(keys, algorithm))
    claims = {"sub": "viewer", "exp": int(time.time()) + 600}
    mine = pem_private(keys[algorithm][0])
    other = pem_private(keys[algorithm][1])
    assert signer.decode(jwt.encode(claims, mine, algorithm=algorithm)) is None                                   # no kid
    assert signer.decode(jwt.encode(claims, mine, algorithm=algorithm, headers={"kid": "unknown"})) is None
    assert signer.decode(forged({"alg": algorithm, "typ": "JWT", "kid": ["a"]}, claims)) is None                   # a kid that is not a string (and not hashable)
    assert signer.decode(jwt.encode(claims, other, algorithm=algorithm, headers={"kid": signer.kid})) is None     # right kid, wrong key
    assert signer.decode(jwt.encode(claims, mine, algorithm=algorithm, headers={"kid": signer.kid}))["sub"] == "viewer"


# The test runs once for RS256 and once for ES256, so both asymmetric algorithms meet the same rule.
# Expired tokens, ones without an integer `exp` and malformed strings give None, never an exception; `iat` in the future is tolerated so a replica with a fast clock does not refuse another's session.
@pytest.mark.parametrize("algorithm", ["RS256", "ES256"])
def test_expired_and_malformed_tokens_are_refused_without_raising(keys, algorithm):
    signer = build_signer(asym_settings(keys, algorithm))
    pem = pem_private(keys[algorithm][0])
    headers = {"kid": signer.kid}
    now = int(time.time())
    assert signer.decode(jwt.encode({"sub": "a", "exp": now - 1}, pem, algorithm=algorithm, headers=headers)) is None
    assert signer.decode(jwt.encode({"sub": "a"}, pem, algorithm=algorithm, headers=headers)) is None                  # no exp
    assert signer.decode(jwt.encode({"sub": "a", "exp": float(now + 60)}, pem, algorithm=algorithm, headers=headers)) is None   # exp not an integer
    assert signer.decode(jwt.encode({"sub": "a", "exp": True}, pem, algorithm=algorithm, headers=headers)) is None
    # iat and nbf are not tested, as under HS256: a replica whose clock is ahead must not refuse a session another replica just issued
    assert signer.decode(jwt.encode({"sub": "a", "exp": now + 60, "iat": now + 3600}, pem, algorithm=algorithm, headers=headers))["sub"] == "a"
    for junk in ("", "a", "a.b", "a.b.c", "....", "é.é.é", "e30.e30.e30"):
        assert signer.decode(junk) is None


def test_a_token_that_carries_its_own_key_is_not_trusted(keys):
    """`jwk`, `jku` and `x5c` in the header are ignored: the key comes from the configured set by `kid` only."""
    signer = build_signer(asym_settings(keys, "RS256"))
    attacker = keys["RS256"][1]
    own = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(attacker.public_key()))
    token = jwt.encode({"sub": "admin", "exp": int(time.time()) + 600}, pem_private(attacker), algorithm="RS256",
                       headers={"kid": signer.kid, "jwk": own, "jku": "https://attacker.example/jwks.json"})
    assert signer.decode(token) is None


# ---------------------------------------------------------------- switching algorithm

def test_switching_from_hs256_to_a_key_pair_ends_existing_sessions_and_a_new_login_works(keys):
    """Changing from HS256 to a key pair ends the existing sessions, although the shared secret is unchanged, and a new sign-in works.
    """
    db = Database("sqlite://")
    old = sign_in(start(settings(), db))
    assert old.get("/api/me").status_code == 200
    new_app = start(asym_settings(keys, "RS256"), db)
    carried = TestClient(new_app)
    carried.cookies.update(old.cookies)
    assert carried.get("/api/me").status_code == 401           # the HS256 session is refused although GUI_JWT_SECRET is the same
    assert sign_in(new_app).get("/api/me").status_code == 200


def test_switching_back_to_hs256_ends_sessions_of_the_key_pair_too(keys):
    """Changing back from a key pair to HS256 ends the key pair's sessions too."""
    db = Database("sqlite://")
    pair = sign_in(start(asym_settings(keys, "ES256"), db))
    back = TestClient(start(settings(), db))
    back.cookies.update(pair.cookies)
    assert back.get("/api/me").status_code == 401


# ---------------------------------------------------------------- rotation (SEC-5.2)

# The test runs once for RS256 and once for ES256, so both asymmetric algorithms meet the same rule.
# After a rotation a session signed with the old key still verifies while its public key is listed (and the JWKS publishes both), and stops when the key is removed.
@pytest.mark.parametrize("algorithm", ["RS256", "ES256"])
def test_a_token_signed_with_the_old_key_verifies_while_the_old_public_key_is_listed(keys, algorithm):
    db = Database("sqlite://")
    old_key, new_key_ = keys[algorithm]
    before = sign_in(start(settings(jwt_algorithm=algorithm, jwt_private_key=pem_private(old_key)), db))
    old_kid = header_of(before.cookies[SESSION_COOKIE])["kid"]

    rotated = settings(jwt_algorithm=algorithm, jwt_private_key=pem_private(new_key_), jwt_previous_keys=[("old", pem_public(old_key))])
    app = start(rotated, db)
    carried = TestClient(app)
    carried.cookies.update(before.cookies)
    assert carried.get("/api/me").status_code == 200                                         # still verifies, with a public key only
    fresh = sign_in(app)
    fresh_kid = header_of(fresh.cookies[SESSION_COOKIE])["kid"]
    assert fresh_kid != old_kid                                                              # new sessions use the new key
    published = TestClient(app).get("/.well-known/jwks.json").json()["keys"]
    assert [k["kid"] for k in published] == [fresh_kid, old_kid]

    dropped = settings(jwt_algorithm=algorithm, jwt_private_key=pem_private(new_key_))        # the old key removed from the set
    gone = TestClient(start(dropped, db))
    gone.cookies.update(before.cookies)
    assert gone.get("/api/me").status_code == 401
    still = TestClient(start(dropped, db))
    still.cookies.update(fresh.cookies)
    assert still.get("/api/me").status_code == 200


def test_a_previous_key_is_published_and_verifies_but_never_signs(keys):
    """A previous key is published and verifies tokens, but new tokens are always signed with the current key."""
    old_key, new_key_ = keys["ES256"]
    signer = AsymmetricSigner("ES256", pem_private(new_key_), [("old", pem_private(old_key))])           # even a private previous key is used to verify only
    assert header_of(signer.issue({"sub": "a"}, 60))["kid"] == signer.kid
    published = signer.jwks()["keys"]
    assert len(published) == 2 and published[0]["kid"] == signer.kid
    only_old = jwt.encode({"sub": "a", "exp": int(time.time()) + 60}, pem_private(old_key), algorithm="ES256", headers={"kid": published[1]["kid"]})
    assert signer.decode(only_old)["sub"] == "a"


def test_the_same_key_listed_twice_or_as_the_current_one_is_published_once(keys):
    """A key listed twice, or the current key repeated among the previous ones, appears once in the key set."""
    key = keys["RS256"][0]
    signer = AsymmetricSigner("RS256", pem_private(key), [("a", pem_public(key)), ("b", pem_public(keys["RS256"][1])), ("c", pem_public(keys["RS256"][1]))])
    assert len(signer.jwks()["keys"]) == 2


# ---------------------------------------------------------------- JWKS (SEC-5.3)

# The test runs once for RS256 and once for ES256, so both asymmetric algorithms meet the same rule.
# The JWKS route needs no session, is cacheable JSON, and publishes only public members (no private key parameters).
@pytest.mark.parametrize("algorithm", ["RS256", "ES256"])
def test_the_jwks_route_is_open_json_with_public_members_only(keys, algorithm):
    app = start(asym_settings(keys, algorithm, previous=[pem_public(keys[algorithm][1])]))
    resp = TestClient(app).get("/.well-known/jwks.json")          # no cookie, no header
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/json"
    assert resp.headers["cache-control"] == "public, max-age=300"
    assert resp.headers["x-content-type-options"] == "nosniff"
    body = resp.json()
    assert len(body["keys"]) == 2
    private_members = {"d", "p", "q", "dp", "dq", "qi", "oth", "k"}
    for jwk in body["keys"]:
        assert not private_members & set(jwk)
        assert jwk["use"] == "sig" and jwk["alg"] == algorithm and jwk["kid"]
        assert jwk["kty"] == ("RSA" if algorithm == "RS256" else "EC")
    assert "PRIVATE" not in resp.text


def test_the_jwks_kid_is_the_rfc7638_thumbprint(keys):
    """RFC 7638 section 3.1's example key and thumbprint, then a generated key."""
    n = ("0vx7agoebGcQSuuPiLJXZptN9nndrQmbXEps2aiAFbWhM78LhWx4cbbfAAtVT86zwu1RK7aPFFxuhDR1L6tSoc_BJECPebWKRXjBZCiFV4n3oknjhMstn64tZ_2W-5JsGY4Hc5n9yBXArwl93lqt7_RN5w6Cf0h4QyQ5v-65YGjQR0_FDW2QvzqY368QQMicAtaSqzs8KJZgnYb9c7d0zgdAZHzu6qMQvRL5hajrn1n91CbOpbISD08qNLyrdkt-bFTWhAI4vMQFh6WeZu0fM4lFd2NcRwr3XPksINHaQ-G_xBniIqbw0Ls1jF44-csFCur-kEgU8awapJzKnqDKgw")
    canonical = json.dumps({"e": "AQAB", "kty": "RSA", "n": n}, separators=(",", ":"), sort_keys=True)
    assert _b64(hashlib.sha256(canonical.encode()).digest()) == "NzbLsXh8uDCcd-6MNwXF4W_7noWXFZAfHkxZsRGC9Xs"
    jwk = build_signer(settings(jwt_algorithm="RS256", jwt_private_key=pem_private(keys["RS256"][0]))).jwks()["keys"][0]
    canonical = json.dumps({m: jwk[m] for m in ("e", "kty", "n")}, separators=(",", ":"), sort_keys=True)
    assert jwk["kid"] == _b64(hashlib.sha256(canonical.encode()).digest())
    ec_jwk = build_signer(settings(jwt_algorithm="ES256", jwt_private_key=pem_private(keys["ES256"][0]))).jwks()["keys"][0]
    canonical = json.dumps({m: ec_jwk[m] for m in ("crv", "kty", "x", "y")}, separators=(",", ":"), sort_keys=True)
    assert ec_jwk["crv"] == "P-256" and ec_jwk["kid"] == _b64(hashlib.sha256(canonical.encode()).digest())


def test_the_jwks_route_is_the_only_new_route_that_answers_without_a_session():
    """Probing every parameterless GET route without a session shows that only the JWKS route and the sign-in, OIDC start and OpenAPI routes answer without one, so a new open route must be a deliberate change here.
    """
    app = start(settings())
    anonymous = TestClient(app)
    assert anonymous.get("/.well-known/jwks.json").status_code == 200
    open_paths = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path or "{" in path or "GET" not in (getattr(route, "methods", None) or set()):
            continue
        if anonymous.get(path, follow_redirects=False).status_code != 401:
            open_paths.add(path)
    assert "/.well-known/jwks.json" in open_paths
    assert open_paths <= {"/.well-known/jwks.json", "/api/auth/config", "/api/oidc/login", "/api/oidc/callback", "/api/openapi.json"}, open_paths


# ---------------------------------------------------------------- configuration

def test_settings_read_the_environment(monkeypatch, tmp_path, keys):
    """`Settings` reads the algorithm, the private key file and the previous key files, tolerates spaces and empty entries, and names the variable when a file cannot be read or the algorithm is unknown.
    """
    private = tmp_path / "private.pem"
    private.write_text(pem_private(keys["ES256"][0]))
    previous = tmp_path / "previous.pem"
    previous.write_text(pem_public(keys["ES256"][1]))
    monkeypatch.setenv("GUI_JWT_ALGORITHM", " es256 ")
    monkeypatch.setenv("GUI_JWT_PRIVATE_KEY_FILE", str(private))
    monkeypatch.setenv("GUI_JWT_PREVIOUS_KEY_FILES", f"{previous}, ,")
    cfg = Settings()
    assert cfg.jwt_algorithm == "ES256" and "PRIVATE KEY" in cfg.jwt_private_key
    assert [pem for _, pem in cfg.jwt_previous_keys] == [previous.read_text()]
    assert len(build_signer(cfg).jwks()["keys"]) == 2
    monkeypatch.setenv("GUI_JWT_PRIVATE_KEY_FILE", str(tmp_path / "missing.pem"))
    with pytest.raises(ValueError, match="GUI_JWT_PRIVATE_KEY_FILE=.*cannot be read"):
        Settings()
    monkeypatch.setenv("GUI_JWT_PRIVATE_KEY_FILE", str(private))
    monkeypatch.setenv("GUI_JWT_PREVIOUS_KEY_FILES", str(tmp_path / "missing.pem"))
    with pytest.raises(ValueError, match="GUI_JWT_PREVIOUS_KEY_FILES=.*cannot be read"):
        Settings()
    monkeypatch.setenv("GUI_JWT_ALGORITHM", "HS512")
    with pytest.raises(ValueError, match="not one of HS256, RS256, ES256"):
        Settings()


def test_unset_environment_is_the_old_behaviour(monkeypatch):
    """With none of the signing variables set the settings are HS256 with no key files."""
    for name in ("GUI_JWT_ALGORITHM", "GUI_JWT_PRIVATE_KEY_FILE", "GUI_JWT_PREVIOUS_KEY_FILES"):
        monkeypatch.delenv(name, raising=False)
    cfg = Settings()
    assert (cfg.jwt_algorithm, cfg.jwt_private_key, cfg.jwt_previous_keys) == ("HS256", "", [])


def test_start_up_refuses_a_combination_that_would_be_silently_wrong(keys):
    """`build_signer` raises ValueError for each unsafe or contradictory combination: missing key, key files under HS256, unknown algorithm, wrong key type, weak RSA, wrong curve, unreadable or encrypted keys, and a public key where a private one is needed.
    """
    pem = pem_private(keys["RS256"][0])
    with pytest.raises(ValueError, match="needs GUI_JWT_PRIVATE_KEY_FILE"):
        build_signer(settings(jwt_algorithm="RS256"))
    with pytest.raises(ValueError, match="GUI_JWT_ALGORITHM is HS256"):
        build_signer(settings(jwt_private_key=pem))                                   # a key file with the default algorithm: the operator thinks it is in use
    with pytest.raises(ValueError, match="GUI_JWT_ALGORITHM is HS256"):
        build_signer(settings(jwt_previous_keys=[("p", pem)]))
    with pytest.raises(ValueError, match="not one of"):
        build_signer(settings(jwt_algorithm="none"))
    with pytest.raises(ValueError, match="not an RSA key"):
        build_signer(settings(jwt_algorithm="RS256", jwt_private_key=pem_private(keys["ES256"][0])))
    with pytest.raises(ValueError, match="not an EC key on curve P-256"):
        build_signer(settings(jwt_algorithm="ES256", jwt_private_key=pem))
    with pytest.raises(ValueError, match="not an EC key on curve P-256"):
        build_signer(settings(jwt_algorithm="ES256", jwt_private_key=pem_private(ec.generate_private_key(ec.SECP384R1()))))
    with pytest.raises(ValueError, match="at least 2048"):
        build_signer(settings(jwt_algorithm="RS256", jwt_private_key=pem_private(rsa.generate_private_key(public_exponent=65537, key_size=1024))))
    with pytest.raises(ValueError, match="unencrypted PEM private key"):
        build_signer(settings(jwt_algorithm="RS256", jwt_private_key="not a key"))
    with pytest.raises(ValueError, match="unencrypted PEM private key"):
        build_signer(settings(jwt_algorithm="RS256", jwt_private_key=pem_public(keys["RS256"][0])))             # the current key must be private
    with pytest.raises(ValueError, match="neither a PEM private key nor a PEM public key"):
        build_signer(settings(jwt_algorithm="RS256", jwt_private_key=pem, jwt_previous_keys=[("p", "garbage")]))
    with pytest.raises(ValueError, match="not an RSA key"):
        build_signer(settings(jwt_algorithm="RS256", jwt_private_key=pem, jwt_previous_keys=[("p", pem_public(keys["ES256"][0]))]))
    encrypted = keys["RS256"][0].private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.BestAvailableEncryption(b"pw")).decode()
    with pytest.raises(ValueError, match="unencrypted PEM private key"):
        build_signer(settings(jwt_algorithm="RS256", jwt_private_key=encrypted))
    with pytest.raises(ValueError, match="is not an asymmetric algorithm"):
        AsymmetricSigner("HS256", pem)


def test_the_error_never_contains_the_key(keys):
    """A rejected key's error text never contains the key material."""
    pem = pem_private(keys["ES256"][0])
    body = "".join(pem.splitlines()[1:-1])
    with pytest.raises(ValueError) as caught:
        build_signer(settings(jwt_algorithm="RS256", jwt_private_key=pem))
    assert body not in str(caught.value)
