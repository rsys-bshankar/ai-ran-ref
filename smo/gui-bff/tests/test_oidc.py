"""PR-SEC-6: OIDC login of the operator GUI, against a fake identity provider.

The provider is an `httpx.MockTransport` that serves a discovery document, a JWKS and a token endpoint, with an RSA key generated here. It checks what a
real one checks (the redirect URI, the client's credentials, the PKCE verifier against the challenge the sign-in started with), so the BFF's real flow
runs unmodified: login redirect, callback, code exchange, ID-token validation, user provisioning, session cookies, audit.
"""

import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.db import AuditEntry, Database, GuiUser, OidcLogin
from app.main import CSRF_COOKIE, OIDC_COOKIE, SESSION_COOKIE, create_app, seed_users
from app.oidc import OidcClient, OidcConfig, OidcError, claim_at, groups_from, pkce_challenge
from app.smo_client import R1Gateway
from test_main import PASSWORDS, R1, FakeSmo

ISSUER = "https://idp.example.com/realms/smo"
CLIENT_ID = "smo-gui"
CLIENT_CREDENTIAL = "client-credential-1"
REDIRECT = "https://gui.example.com/api/oidc/callback"
GROUP_MAP = "smo-admins=admin,smo-ops=operator,smo-viewers=viewer"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _jwk(public_key, kid: str) -> dict:
    numbers = public_key.public_numbers()
    to_bytes = lambda n: n.to_bytes((n.bit_length() + 7) // 8, "big")   # noqa: E731
    return {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid, "n": _b64(to_bytes(numbers.n)), "e": _b64(to_bytes(numbers.e))}


class FakeIdp:
    """A provider with one signing key (`rotate()` adds another), one pending code at a time per sign-in."""

    def __init__(self):
        self.key, self.kid = rsa.generate_private_key(public_exponent=65537, key_size=2048), "key-1"
        self.published = {self.kid: self.key.public_key()}
        self.codes: dict[str, dict] = {}
        self.discovery_hits = 0
        self.jwks_hits = 0
        self.down = False
        self.token_status = 200
        self.end_session = True
        self.discovery_issuer = ISSUER
        self.sub, self.groups = "user-1", ["smo-ops"]
        self.mutate: dict = {}               # claims overridden or (value None) removed in the next token
        self.at_hash: str | None = None
        self.last_token_request: dict = {}
        self.auth_header = ""

    def pem(self) -> bytes:
        return self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())

    def rotate(self, publish_old: bool = True) -> None:
        old = self.published
        self.key, self.kid = rsa.generate_private_key(public_exponent=65537, key_size=2048), f"key-{len(old) + 1}"
        self.published = {**(old if publish_old else {}), self.kid: self.key.public_key()}

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("down")
        url = str(request.url)
        if url == f"{ISSUER}/.well-known/openid-configuration":
            self.discovery_hits += 1
            doc = {"issuer": self.discovery_issuer, "authorization_endpoint": f"{ISSUER}/protocol/openid-connect/auth",
                   "token_endpoint": f"{ISSUER}/protocol/openid-connect/token", "jwks_uri": f"{ISSUER}/protocol/openid-connect/certs",
                   "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"]}
            if self.end_session:
                doc["end_session_endpoint"] = f"{ISSUER}/protocol/openid-connect/logout"
            return httpx.Response(200, json=doc)
        if url == f"{ISSUER}/protocol/openid-connect/certs":
            self.jwks_hits += 1
            return httpx.Response(200, json={"keys": [_jwk(k, kid) for kid, k in self.published.items()]})
        if url == f"{ISSUER}/protocol/openid-connect/token":
            return self.token(request)
        return httpx.Response(404)

    # what the browser does at the provider: log in and come back with a code
    def authorize(self, location: str, **claims) -> str:
        query = {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}
        assert location.startswith(f"{ISSUER}/protocol/openid-connect/auth?")
        assert query["response_type"] == "code" and query["client_id"] == CLIENT_ID and query["redirect_uri"] == REDIRECT
        assert query["code_challenge_method"] == "S256" and "state" in query and "nonce" in query
        assert "openid" in query["scope"].split()
        code = f"code-{len(self.codes) + 1}"
        self.codes[code] = {"nonce": query["nonce"], "challenge": query["code_challenge"], "claims": claims}
        return code

    def id_token(self, nonce: str, **claims) -> str:
        now = int(time.time())
        body = {"iss": ISSUER, "aud": CLIENT_ID, "sub": self.sub, "iat": now, "exp": now + 300, "nonce": nonce, "azp": CLIENT_ID, "groups": self.groups}
        body.update(claims)
        body.update(self.mutate)
        body = {k: v for k, v in body.items() if v is not None}
        return jwt.encode(body, self.pem(), algorithm="RS256", headers={"kid": self.kid})

    def token(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.last_token_request = form
        self.auth_header = request.headers.get("authorization", "")
        if self.token_status != 200:
            return httpx.Response(self.token_status, json={"error": "invalid_grant"})
        pending = self.codes.pop(form.get("code", ""), None)
        basic = "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_CREDENTIAL}".encode()).decode()
        if pending is None or form.get("redirect_uri") != REDIRECT or self.auth_header != basic:
            return httpx.Response(400, json={"error": "invalid_grant"})
        if pkce_challenge(form.get("code_verifier", "")) != pending["challenge"]:      # PKCE: the verifier must hash to the challenge
            return httpx.Response(400, json={"error": "invalid_grant", "error_description": "PKCE verification failed"})
        token = {"access_token": "access-1", "token_type": "Bearer", "expires_in": 300, "id_token": self.id_token(pending["nonce"], **pending["claims"])}
        if self.at_hash:
            token["id_token"] = self.id_token(pending["nonce"], at_hash=self.at_hash, **pending["claims"])
        return httpx.Response(200, json=token)


@pytest.fixture
def idp():
    return FakeIdp()


@pytest.fixture
def db():
    return Database("sqlite://")


def make_cfg(**over) -> Settings:
    base = dict(r1_url=R1, jwt_secret="test-secret", cookie_secure=False, admin_password=PASSWORDS["admin"], operator_password=PASSWORDS["operator"],
                viewer_password=PASSWORDS["viewer"], oidc_enabled=True, oidc_issuer=ISSUER, oidc_client_id=CLIENT_ID,
                oidc_client_credential=CLIENT_CREDENTIAL, oidc_redirect_uri=REDIRECT, oidc_group_role_map=GROUP_MAP, oidc_provider_name="Keycloak")
    base.update(over)
    return Settings(**base)


def make_app(idp, db, cfg=None):
    cfg = cfg or make_cfg()
    seed_users(db, cfg)
    return create_app(cfg, db=db, gateway=R1Gateway(R1, db, transport=httpx.MockTransport(FakeSmo().handler)), oidc_transport=httpx.MockTransport(idp.handler))


@pytest.fixture
def app(idp, db):
    return make_app(idp, db)


@pytest.fixture
def client(app):
    return TestClient(app, follow_redirects=False)


def audit_rows(db, action=None):
    with db.session() as s:
        rows = s.query(AuditEntry).all()
    return [r for r in rows if action is None or r.action == action]


def start(client) -> tuple[str, str]:
    """GET /api/oidc/login: (the provider URL the browser is sent to, the state)."""
    resp = client.get("/api/oidc/login")
    assert resp.status_code == 302, resp.text
    location = resp.headers["location"]
    return location, parse_qs(urlsplit(location).query)["state"][0]


def sign_in(client, idp, state_override=None, **claims):
    location, state = start(client)
    code = idp.authorize(location, **claims)
    return client.get("/api/oidc/callback", params={"code": code, "state": state_override or state})


def failure(resp) -> str:
    assert resp.status_code == 303, resp.text
    location = resp.headers["location"]
    assert location.startswith("/login?oidc_error="), location
    assert SESSION_COOKIE not in resp.headers.get("set-cookie", "")
    return parse_qs(urlsplit(location).query)["oidc_error"][0]


# ---------------------------------------------------------------- what the sign-in page may offer

def test_auth_config_reports_oidc_off_by_default(db, idp):
    cfg = make_cfg(oidc_enabled=False)
    app = make_app(idp, db, cfg)
    body = TestClient(app).get("/api/auth/config").json()
    assert body == {"localLogin": True, "oidc": {"enabled": False}}
    assert TestClient(app, follow_redirects=False).get("/api/oidc/login").status_code == 404
    assert TestClient(app, follow_redirects=False).get("/api/oidc/callback", params={"code": "x", "state": "y"}).status_code == 404


def test_auth_config_when_enabled_names_the_provider_and_needs_no_session(client):
    body = client.get("/api/auth/config").json()
    assert body == {"localLogin": True, "oidc": {"enabled": True, "providerName": "Keycloak", "loginUrl": "/api/oidc/login"}}


# ---------------------------------------------------------------- the happy path

def test_login_redirects_to_the_provider_with_state_nonce_and_a_pkce_challenge(client, db):
    resp = client.get("/api/oidc/login")
    assert resp.status_code == 302
    query = {k: v[0] for k, v in parse_qs(urlsplit(resp.headers["location"]).query).items()}
    assert query["response_type"] == "code" and query["code_challenge_method"] == "S256"
    assert query["scope"] == "openid profile email" and query["redirect_uri"] == REDIRECT
    with db.session() as s:
        row = s.get(OidcLogin, query["state"])
    assert row is not None and row.nonce == query["nonce"] and pkce_challenge(row.verifier) == query["code_challenge"]
    cookie = next(c for c in resp.headers.get_list("set-cookie") if c.startswith(f"{OIDC_COOKIE}="))
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/api/oidc" in cookie
    assert OIDC_COOKIE in client.cookies and client.cookies[OIDC_COOKIE] not in resp.headers["location"]


def test_sign_in_creates_the_user_without_a_password_and_issues_the_normal_session(client, idp, db):
    resp = sign_in(client, idp)
    assert resp.status_code == 303 and resp.headers["location"] == "/"
    set_cookies = resp.headers.get_list("set-cookie")
    session = next(c for c in set_cookies if c.startswith(f"{SESSION_COOKIE}="))
    assert "HttpOnly" in session and "SameSite=strict" in session and "Path=/api" in session
    assert any(c.startswith(f"{CSRF_COOKIE}=") for c in set_cookies)
    me = client.get("/api/me")
    assert me.status_code == 200 and me.json()["username"] == "oidc:user-1" and me.json()["role"] == "operator"
    with db.session() as s:
        user = s.get(GuiUser, "oidc:user-1")
    assert user.password_hash == "!" and user.role == "operator" and user.active
    assert idp.auth_header.startswith("Basic ")                      # the client authenticated; the verifier went with the code
    assert idp.last_token_request["grant_type"] == "authorization_code"
    row = audit_rows(db, "OIDC_LOGIN")[0]
    assert row.username == "oidc:user-1" and row.role == "operator" and f"iss={ISSUER}" in row.detail and "created" in row.detail


def test_the_session_works_for_unsafe_calls_with_the_csrf_header_only(client, idp):
    sign_in(client, idp, groups=["smo-admins"])
    assert client.post("/api/logout").status_code == 200       # logout needs no CSRF header, like a local one
    sign_in(client, idp, groups=["smo-admins"])
    body = {"username": "made-by-oidc", "password": "long-enough-1", "role": "viewer"}
    assert client.post("/api/admin/users", json=body).status_code == 403
    assert client.post("/api/admin/users", json=body, headers={"X-CSRF-Token": client.cookies[CSRF_COOKIE]}).status_code == 201


def test_a_user_with_no_password_cannot_sign_in_locally(client, idp, app):
    sign_in(client, idp)
    for password in ("!", "", "anything-at-all"):
        assert TestClient(app).post("/api/login", json={"username": "oidc:user-1", "password": password}).status_code == 401


def test_admin_cannot_set_a_password_on_an_oidc_user(client, idp, app):
    sign_in(client, idp)
    admin = TestClient(app)
    resp = admin.post("/api/login", json={"username": "admin", "password": PASSWORDS["admin"]})
    headers = {"X-CSRF-Token": resp.json()["csrfToken"]}
    reset = admin.patch("/api/admin/users/oidc:user-1", json={"password": "a-new-password-1"}, headers=headers)
    assert reset.status_code == 409 and reset.json()["title"] == "OIDC_USER"
    assert admin.patch("/api/admin/users/oidc:user-1", json={"role": "viewer"}, headers=headers).status_code == 200


# ---------------------------------------------------------------- roles

@pytest.mark.parametrize("groups, role", [(["smo-viewers"], "viewer"), (["smo-ops"], "operator"), (["smo-admins"], "admin"),
                                          (["smo-viewers", "smo-admins", "unrelated"], "admin"), (["unrelated", "smo-ops"], "operator")])
def test_the_highest_mapped_group_decides_the_role(client, idp, groups, role):
    idp.groups = groups
    sign_in(client, idp)
    assert client.get("/api/me").json()["role"] == role


def test_the_role_is_evaluated_again_at_every_sign_in(client, idp, db):
    sign_in(client, idp, groups=["smo-admins"])
    assert client.get("/api/me").json()["role"] == "admin"
    sign_in(client, idp, groups=["smo-viewers"])
    assert client.get("/api/me").json()["role"] == "viewer"
    with db.session() as s:
        assert s.get(GuiUser, "oidc:user-1").role == "viewer" and len(s.scalars(select(GuiUser).where(GuiUser.username.like("oidc:%"))).all()) == 1
    assert "role admin->viewer" in audit_rows(db, "OIDC_LOGIN")[-1].detail


def test_an_unmapped_group_is_denied_and_no_user_is_created(client, idp, db):
    idp.groups = ["marketing"]
    assert failure(sign_in(client, idp)) == "no_role"
    with db.session() as s:
        assert s.get(GuiUser, "oidc:user-1") is None
    row = audit_rows(db, "OIDC_LOGIN_FAILED")[0]
    assert row.username == "oidc:user-1" and row.detail.startswith("no_role")
    assert client.get("/api/me").status_code == 401


def test_a_token_with_no_groups_claim_is_denied(client, idp):
    idp.mutate = {"groups": None}
    assert failure(sign_in(client, idp)) == "no_role"


def test_a_default_role_admits_people_with_no_mapped_group(idp, db):
    client = TestClient(make_app(idp, db, make_cfg(oidc_default_role="viewer")), follow_redirects=False)
    idp.groups = ["marketing"]
    sign_in(client, idp)
    assert client.get("/api/me").json()["role"] == "viewer"


def test_losing_every_group_ends_the_earlier_sessions_of_that_person(client, idp):
    sign_in(client, idp)
    assert client.get("/api/me").status_code == 200
    old = dict(client.cookies)
    idp.groups = []
    other = TestClient(client.app, follow_redirects=False)
    assert failure(sign_in(other, idp)) == "no_role"
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, old[SESSION_COOKIE], path="/api")
    assert client.get("/api/me").status_code == 401


def test_groups_can_be_a_nested_claim_or_a_string():
    claims = {"realm_access": {"roles": ["a", "b"]}, "scope_groups": "x, y  z", "plain": ["only", 3, None], "dotted.name": ["d"]}
    assert groups_from(claims, "realm_access.roles") == ["a", "b"]
    assert groups_from(claims, "scope_groups") == ["x", "y", "z"]
    assert groups_from(claims, "plain") == ["only"] and groups_from(claims, "dotted.name") == ["d"] and groups_from(claims, "missing.path") == []
    assert claim_at(claims, "realm_access.roles.deeper") is None


def test_a_disabled_user_is_refused(client, idp, db):
    sign_in(client, idp)
    with db.session() as s:
        s.get(GuiUser, "oidc:user-1").active = False
        s.commit()
    assert failure(sign_in(TestClient(client.app, follow_redirects=False), idp)) == "account_disabled"
    assert audit_rows(db, "OIDC_LOGIN_FAILED")[-1].username == "oidc:user-1"


# ---------------------------------------------------------------- state, nonce, PKCE

def test_an_unknown_state_is_refused(client, idp, db):
    start(client)
    assert failure(client.get("/api/oidc/callback", params={"code": "c", "state": "never-issued"})) == "invalid_state"
    assert failure(client.get("/api/oidc/callback", params={"code": "c"})) == "invalid_state"
    assert audit_rows(db, "OIDC_LOGIN_FAILED")


def test_a_state_works_once(client, idp):
    location, state = start(client)
    code = idp.authorize(location)
    assert client.get("/api/oidc/callback", params={"code": code, "state": state}).status_code == 303
    again = client.get("/api/oidc/callback", params={"code": code, "state": state})
    assert failure(again) == "invalid_state"


def test_a_callback_from_another_browser_is_refused(client, idp, app):
    location, state = start(client)
    code = idp.authorize(location)
    attacker_view = TestClient(app, follow_redirects=False)           # no binding cookie: a callback URL planted in the victim's browser
    assert failure(attacker_view.get("/api/oidc/callback", params={"code": code, "state": state})) == "invalid_state"
    wrong = TestClient(app, follow_redirects=False, cookies={OIDC_COOKIE: "someone-elses"})
    location, state = start(client)
    assert failure(wrong.get("/api/oidc/callback", params={"code": idp.authorize(location), "state": state})) == "invalid_state"


def test_an_expired_sign_in_is_refused(client, idp, db):
    location, state = start(client)
    with db.session() as s:
        s.get(OidcLogin, state).expires_at = time.time() - 1
        s.commit()
    assert failure(client.get("/api/oidc/callback", params={"code": idp.authorize(location), "state": state})) == "invalid_state"


def test_a_wrong_nonce_is_refused(client, idp, db):
    idp.mutate = {"nonce": "not-the-nonce-we-sent"}
    assert failure(sign_in(client, idp)) == "token_invalid"
    assert "nonce mismatch" in audit_rows(db, "OIDC_LOGIN_FAILED")[-1].detail


def test_a_token_without_a_nonce_is_refused(client, idp):
    idp.mutate = {"nonce": None}
    assert failure(sign_in(client, idp)) == "token_invalid"


def test_pkce_the_provider_refuses_a_verifier_that_does_not_match(client, idp, db):
    location, state = start(client)
    code = idp.authorize(location)
    with db.session() as s:
        s.get(OidcLogin, state).verifier = "x" * 64          # as if the callback ran with a different verifier
        s.commit()
    assert failure(client.get("/api/oidc/callback", params={"code": code, "state": state})) == "token_exchange_failed"


def test_the_provider_reporting_an_error_is_shown_as_a_code_not_its_text(client, idp, db):
    _, state = start(client)
    resp = client.get("/api/oidc/callback", params={"error": "access_denied", "error_description": "<script>alert(1)</script>", "state": state})
    assert failure(resp) == "access_denied" and "script" not in resp.headers["location"]
    _, state = start(client)
    assert failure(client.get("/api/oidc/callback", params={"error": "server_error", "state": state})) == "idp_error"
    assert "<script>" not in " ".join(r.detail or "" for r in audit_rows(db, "OIDC_LOGIN_FAILED"))


def test_the_pending_table_is_bounded(client, monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, "MAX_PENDING_LOGINS", 2)
    start(client), start(client)
    assert failure(client.get("/api/oidc/login")) == "too_many_logins"


# ---------------------------------------------------------------- the ID token

def test_a_wrong_audience_is_refused(client, idp, db):
    assert failure(sign_in(client, idp, aud="some-other-client", azp="some-other-client")) == "token_invalid"
    assert "InvalidAudienceError" in audit_rows(db, "OIDC_LOGIN_FAILED")[-1].detail


def test_several_audiences_need_this_client_as_authorized_party(client, idp):
    assert failure(sign_in(client, idp, aud=[CLIENT_ID, "other"], azp="other")) == "token_invalid"
    assert sign_in(TestClient(client.app, follow_redirects=False), idp, aud=[CLIENT_ID, "other"], azp=CLIENT_ID).status_code == 303


def test_a_wrong_issuer_is_refused(client, idp, db):
    assert failure(sign_in(client, idp, iss="https://evil.example.com")) == "token_invalid"
    assert "InvalidIssuerError" in audit_rows(db, "OIDC_LOGIN_FAILED")[-1].detail


def test_an_expired_token_is_refused(client, idp, db):
    now = int(time.time())
    assert failure(sign_in(client, idp, iat=now - 7200, exp=now - 3600)) == "token_invalid"
    assert "ExpiredSignatureError" in audit_rows(db, "OIDC_LOGIN_FAILED")[-1].detail


def test_a_token_missing_exp_or_sub_is_refused(client, idp):
    idp.mutate = {"exp": None}
    assert failure(sign_in(client, idp)) == "token_invalid"
    idp.mutate = {"sub": None}
    assert failure(sign_in(TestClient(client.app, follow_redirects=False), idp)) == "token_invalid"


def test_a_tampered_token_is_refused(client, idp, db):
    genuine = idp.id_token

    def tampered(nonce, **claims):
        header, payload, signature = genuine(nonce, **claims).split(".")
        body = json.loads(base64.urlsafe_b64decode(payload + "=="))
        body["groups"] = ["smo-admins"]                     # the attacker promotes themselves; the signature is the old one
        return ".".join([header, _b64(json.dumps(body).encode()), signature])
    idp.id_token = tampered
    idp.groups = ["smo-viewers"]
    assert failure(sign_in(client, idp)) == "token_invalid"
    assert "InvalidSignatureError" in audit_rows(db, "OIDC_LOGIN_FAILED")[-1].detail


def test_a_token_signed_by_another_key_is_refused(client, idp, db):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = lambda nonce, **claims: jwt.encode({"iss": ISSUER, "aud": CLIENT_ID, "sub": "x", "iat": int(time.time()), "exp": int(time.time()) + 60,  # noqa: E731
                                                 "nonce": nonce, "groups": ["smo-admins"]}, other, algorithm="RS256", headers={"kid": idp.kid})
    idp.id_token = forged
    assert failure(sign_in(client, idp)) == "token_invalid"
    assert "InvalidSignatureError" in audit_rows(db, "OIDC_LOGIN_FAILED")[-1].detail


def test_alg_none_is_refused(client, idp, db):
    def unsigned(nonce, **claims):
        now = int(time.time())
        header = _b64(json.dumps({"alg": "none", "typ": "JWT", "kid": idp.kid}).encode())
        body = _b64(json.dumps({"iss": ISSUER, "aud": CLIENT_ID, "sub": "x", "iat": now, "exp": now + 60, "nonce": nonce, "groups": ["smo-admins"]}).encode())
        return f"{header}.{body}."
    idp.id_token = unsigned
    assert failure(sign_in(client, idp)) == "token_invalid"
    assert "not accepted" in audit_rows(db, "OIDC_LOGIN_FAILED")[-1].detail


def test_an_hmac_token_keyed_with_the_public_key_is_refused(client, idp, db):
    """The classic algorithm-confusion attack: HS256 with the provider's public key (a public document) as the secret."""
    public_pem = idp.key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)

    def confused(nonce, **claims):
        now = int(time.time())
        body = {"iss": ISSUER, "aud": CLIENT_ID, "sub": "x", "iat": now, "exp": now + 60, "nonce": nonce, "groups": ["smo-admins"]}
        header = _b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": idp.kid}).encode())
        signing_input = f"{header}.{_b64(json.dumps(body).encode())}"
        import hmac
        return f"{signing_input}.{_b64(hmac.new(public_pem, signing_input.encode(), hashlib.sha256).digest())}"
    idp.id_token = confused
    assert failure(sign_in(client, idp)) == "token_invalid"
    assert "not accepted" in audit_rows(db, "OIDC_LOGIN_FAILED")[-1].detail


def test_a_token_that_is_not_a_jwt_is_refused(client, idp):
    idp.id_token = lambda nonce, **claims: "not.a.jwt"
    assert failure(sign_in(client, idp)) == "token_invalid"


def test_a_wrong_at_hash_is_refused_and_a_right_one_accepted(client, idp):
    idp.at_hash = "AAAAAAAAAAAAAAAAAAAAAA"
    assert failure(sign_in(client, idp)) == "token_invalid"
    digest = hashlib.sha256(b"access-1").digest()
    idp.at_hash = _b64(digest[:16])
    assert sign_in(TestClient(client.app, follow_redirects=False), idp).status_code == 303


# ---------------------------------------------------------------- keys and discovery

def test_a_rotated_signing_key_is_picked_up_and_discovery_is_cached(client, idp):
    sign_in(client, idp)
    assert (idp.discovery_hits, idp.jwks_hits) == (1, 1)
    sign_in(TestClient(client.app, follow_redirects=False), idp)
    assert (idp.discovery_hits, idp.jwks_hits) == (1, 1)            # both documents came from the cache
    idp.rotate()
    other = TestClient(client.app, follow_redirects=False)
    _oidc_client(client.app)._keys_attempt_at -= 60               # the key id is new: one refetch is allowed once the throttle has passed
    assert sign_in(other, idp).status_code == 303 and idp.jwks_hits == 2


def test_a_forged_key_id_does_not_make_the_backend_refetch_the_keys_each_time(client, idp):
    sign_in(client, idp)
    hits = idp.jwks_hits
    idp.mutate = {}
    idp.kid = "never-published"
    for _ in range(3):
        assert failure(sign_in(TestClient(client.app, follow_redirects=False), idp)) == "token_invalid"
    assert idp.jwks_hits <= hits + 1


def test_an_unreachable_provider_is_reported_and_the_last_good_discovery_keeps_serving(client, idp, db):
    idp.down = True
    assert failure(client.get("/api/oidc/login")) == "idp_unavailable"
    idp.down = False
    sign_in(client, idp)
    idp.down = True
    _oidc_client(client.app)._discovery_at -= 7200                   # expired, and the provider is down: the stale copy is used
    assert client.get("/api/oidc/login").status_code == 302


def test_a_discovery_document_for_another_issuer_is_refused(client, idp):
    idp.discovery_issuer = "https://evil.example.com"
    assert failure(client.get("/api/oidc/login")) == "idp_error"


def test_the_code_exchange_failing_is_reported(client, idp):
    location, state = start(client)
    code = idp.authorize(location)
    idp.token_status = 500
    assert failure(client.get("/api/oidc/callback", params={"code": code, "state": state})) == "token_exchange_failed"


def test_client_secret_post_is_used_when_the_provider_offers_only_that(idp):
    doc_handler = idp.handler

    def only_post(request):
        resp = doc_handler(request)
        if str(request.url).endswith("openid-configuration"):
            body = resp.json()
            body["token_endpoint_auth_methods_supported"] = ["client_secret_post"]
            return httpx.Response(200, json=body)
        return resp
    seen = {}

    def token(request):
        seen.update({k: v[0] for k, v in parse_qs(request.content.decode()).items()}, auth=request.headers.get("authorization"))
        return httpx.Response(400, json={"error": "invalid_grant"})
    transport = httpx.MockTransport(lambda r: token(r) if str(r.url).endswith("/token") else only_post(r))
    client = OidcClient(OidcConfig.from_settings(make_cfg()), transport=transport)
    with pytest.raises(OidcError) as exc:
        client.exchange_code("c", "v" * 64)
    assert exc.value.code == "token_exchange_failed"
    assert seen["client_id"] == CLIENT_ID and seen["client_secret"] == CLIENT_CREDENTIAL and seen["auth"] is None


def _oidc_client(app) -> OidcClient:
    return app.state.oidc


# ---------------------------------------------------------------- logout

def test_logout_of_an_oidc_user_revokes_the_session_and_offers_the_provider_logout(client, idp, db):
    sign_in(client, idp)
    token = client.cookies[SESSION_COOKIE]
    resp = client.post("/api/logout")
    body = resp.json()
    assert body["status"] == "logged out"
    query = parse_qs(urlsplit(body["endSessionUrl"]).query)
    assert body["endSessionUrl"].startswith(f"{ISSUER}/protocol/openid-connect/logout?") and query["client_id"] == [CLIENT_ID]
    assert TestClient(client.app).get("/api/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert audit_rows(db, "LOGOUT")[-1].username == "oidc:user-1"


def test_logout_without_an_end_session_endpoint_or_for_a_local_user_has_no_redirect(idp, db):
    idp.end_session = False
    app = make_app(idp, db, make_cfg(oidc_post_logout_redirect_uri="https://gui.example.com/login"))
    client = TestClient(app, follow_redirects=False)
    sign_in(client, idp)
    assert "endSessionUrl" not in client.post("/api/logout").json()
    idp.end_session = True
    _oidc_client(app)._discovery_at -= 7200
    sign_in(client, idp)
    url = client.post("/api/logout").json()["endSessionUrl"]
    assert parse_qs(urlsplit(url).query)["post_logout_redirect_uri"] == ["https://gui.example.com/login"]
    local = TestClient(app)
    local.post("/api/login", json={"username": "viewer", "password": PASSWORDS["viewer"]})
    assert local.post("/api/logout").json() == {"status": "logged out"}


# ---------------------------------------------------------------- local login stays, unless switched off

def test_local_login_still_works_with_oidc_on(app):
    resp = TestClient(app).post("/api/login", json={"username": "admin", "password": PASSWORDS["admin"]})
    assert resp.status_code == 200 and resp.json()["role"] == "admin"


def test_local_login_can_be_switched_off(idp, db):
    app = make_app(idp, db, make_cfg(local_login_enabled=False))
    client = TestClient(app)
    assert client.get("/api/auth/config").json()["localLogin"] is False
    resp = client.post("/api/login", json={"username": "admin", "password": PASSWORDS["admin"]})
    assert resp.status_code == 403 and resp.json()["title"] == "LOCAL_LOGIN_DISABLED"
    grant = client.post("/api/token", data={"grant_type": "password", "username": "admin", "password": PASSWORDS["admin"]})
    assert grant.status_code == 403
    oidc = TestClient(app, follow_redirects=False)
    assert sign_in(oidc, idp).status_code == 303


def test_local_login_off_without_oidc_stops_the_start(db):
    with pytest.raises(ValueError, match="GUI_LOCAL_LOGIN_ENABLED=false needs GUI_OIDC_ENABLED=true"):
        create_app(make_cfg(oidc_enabled=False, local_login_enabled=False), db=db)


# ---------------------------------------------------------------- configuration is validated at start

@pytest.mark.parametrize("over, message", [
    ({"oidc_issuer": ""}, "GUI_OIDC_ISSUER is required"),
    ({"oidc_client_id": ""}, "GUI_OIDC_CLIENT_ID is required"),
    ({"oidc_redirect_uri": ""}, "GUI_OIDC_REDIRECT_URI is required"),
    ({"oidc_issuer": "http://idp.example.com/realms/x"}, "GUI_OIDC_ISSUER must be an absolute https URL"),
    ({"oidc_redirect_uri": "/api/oidc/callback"}, "GUI_OIDC_REDIRECT_URI must be an absolute https URL"),
    ({"oidc_scopes": "profile email"}, "must include openid"),
    ({"oidc_group_role_map": "", "oidc_default_role": ""}, "nobody could sign in"),
    ({"oidc_group_role_map": "g=superuser"}, "not group=viewer|operator|admin"),
    ({"oidc_group_role_map": "=admin"}, "no group name"),
    ({"oidc_default_role": "root"}, "GUI_OIDC_DEFAULT_ROLE must be"),
    ({"oidc_groups_claim": ""}, "GUI_OIDC_GROUPS_CLAIM"),
    ({"oidc_timeout_seconds": 0}, "GUI_OIDC_TIMEOUT_SECONDS"),
])
def test_bad_oidc_configuration_stops_the_start(db, over, message):
    with pytest.raises(ValueError, match=message):
        create_app(make_cfg(**over), db=db)


def test_http_is_accepted_for_localhost_or_when_allowed_and_the_map_parses():
    OidcConfig.from_settings(make_cfg(oidc_issuer="http://localhost:8080/realms/x", oidc_redirect_uri="http://127.0.0.1:3000/api/oidc/callback"))
    with pytest.raises(ValueError):
        OidcConfig.from_settings(make_cfg(oidc_issuer="http://keycloak:8080/realms/x"))
    cfg = OidcConfig.from_settings(make_cfg(oidc_issuer="http://keycloak:8080/realms/x", oidc_allow_http=True,
                                            oidc_group_role_map=" /smo admins = admin , x=y=viewer ,, "))
    assert {k: str(v) for k, v in cfg.group_roles.items()} == {"/smo admins": "admin", "x=y": "viewer"}


def test_oidc_off_needs_none_of_its_settings(db):
    create_app(make_cfg(oidc_enabled=False, oidc_issuer="", oidc_client_id="", oidc_group_role_map="x=bad"), db=db)


def test_the_client_credential_comes_from_a_file_and_never_from_both(tmp_path, monkeypatch):
    from app.config import _client_credential
    path = tmp_path / "credential"
    path.write_text("from-the-file\n")
    monkeypatch.setenv("GUI_OIDC_CLIENT_SECRET_FILE", str(path))
    assert _client_credential() == "from-the-file"
    monkeypatch.setenv("GUI_OIDC_CLIENT_SECRET", "and-from-env")
    with pytest.raises(ValueError, match="set only one"):
        _client_credential()
    monkeypatch.delenv("GUI_OIDC_CLIENT_SECRET")
    monkeypatch.setenv("GUI_OIDC_CLIENT_SECRET_FILE", str(tmp_path / "missing"))
    with pytest.raises(ValueError, match="cannot be read"):
        _client_credential()


def test_pkce_challenge_matches_the_rfc_7636_example():
    assert pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


# ---------------------------------------------------------------- several instances (PR-ST-5)

def test_a_sign_in_started_on_one_instance_finishes_on_another(idp, tmp_path):
    path = tmp_path / "gui.db"
    first = make_app(idp, Database(f"sqlite:///{path}"))
    second = make_app(idp, Database(f"sqlite:///{path}"))
    a, b = TestClient(first, follow_redirects=False), TestClient(second, follow_redirects=False)
    location, state = start(a)
    b.cookies.set(OIDC_COOKIE, a.cookies[OIDC_COOKIE], path="/api/oidc")
    resp = b.get("/api/oidc/callback", params={"code": idp.authorize(location), "state": state})
    assert resp.status_code == 303 and resp.headers["location"] == "/"
    a.cookies.set(SESSION_COOKIE, b.cookies[SESSION_COOKIE], path="/api")
    assert a.get("/api/me").json()["username"] == "oidc:user-1"          # the session signed on one instance is good on the other
    assert failure(a.get("/api/oidc/callback", params={"code": "x", "state": state})) == "invalid_state"      # and the state is spent for both


# ---------------------------------------------------------------- the CI realm agrees with the workflow and the script

def test_the_keycloak_realm_of_the_browser_check_matches_the_workflow_and_the_script():
    from pathlib import Path
    smo = Path(__file__).resolve().parents[2]
    realm = json.loads((smo / "gui-bff" / "tests" / "keycloak" / "smo-realm.json").read_text())
    client = next(c for c in realm["clients"] if c["clientId"] == "smo-gui")
    workflow = (smo.parent / ".github" / "workflows" / "smo-gui-e2e.yml").read_text()
    script = (smo / "scripts" / "gui_oidc_e2e.py").read_text()
    assert f"GUI_OIDC_REDIRECT_URI: {client['redirectUris'][0]}" in workflow
    assert f"GUI_OIDC_CLIENT_SECRET: {client['secret']}" in workflow and "GUI_OIDC_ISSUER: http://keycloak:8080/realms/smo" in workflow
    assert client["attributes"]["pkce.code.challenge.method"] == "S256" and client["publicClient"] is False
    assert f"GUI_OIDC_POST_LOGOUT_REDIRECT_URI: {client['attributes']['post.logout.redirect.uris']}" in workflow
    mapper = client["protocolMappers"][0]["config"]
    assert mapper["claim.name"] == "groups" and mapper["id.token.claim"] == "true" and mapper["full.path"] == "false"
    groups = {g["name"] for g in realm["groups"]}
    assert f"GUI_OIDC_GROUP_ROLE_MAP: smo-admins=admin,smo-ops=operator,smo-viewers=viewer" in workflow and groups == {"smo-admins", "smo-ops", "smo-viewers"}
    users = {u["username"]: u for u in realm["users"]}
    for name, _role, password in [("oidc-admin", "", "ci-oidc-admin-1"), ("oidc-operator", "", "ci-oidc-operator-1"), ("oidc-viewer", "", "ci-oidc-viewer-1"),
                                  ("oidc-nogroup", "", "ci-oidc-nogroup-1")]:
        assert users[name]["credentials"][0]["value"] == password and password in script
    assert not users["oidc-nogroup"].get("groups")
