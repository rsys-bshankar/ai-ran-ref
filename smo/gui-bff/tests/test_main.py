"""Tests for the SMO Operator GUI BFF: login/session/CSRF, the RBAC-gated
proxy to R1 Termination, the BFF's own OAuth2 client flow against SME,
hop-by-hop header stripping, health aggregation, and user/audit admin.

R1 Termination and SME are faked with one httpx.MockTransport, so the
BFF's real R1Gateway runs unmodified: bootstrap discovery, invoker
onboarding, client_credentials, and the 401 refresh are all exercised.
Run with: cd smo/gui-bff && PYTHONPATH=. python -m pytest tests -q
"""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import AuditEntry, Database, GuiUser
from app.main import CSRF_COOKIE, SESSION_COOKIE, STATUS_MODULES, create_app, seed_users
from app.smo_client import R1Gateway

R1 = "http://r1-termination:8000"
SME = "http://sme:8000"
PASSWORDS = {"admin": "admin-pass-1", "operator": "operator-pass-1", "viewer": "viewer-pass-1"}


class FakeSmo:
    """R1 Termination + SME, just enough of each: R1's /bootstrap and
    /health, SME's invoker onboarding + token endpoint, and R1's proxy,
    which (like the real one) 401s anything without a live Bearer token.
    """

    def __init__(self):
        self.issued: list[str] = []
        self.revoked: set[str] = set()
        self.invokers = 0
        self.proxied: list[httpx.Request] = []
        self.down_modules: set[str] = set()
        self.next_response: httpx.Response | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == f"{R1}/bootstrap":
            return httpx.Response(200, json={"apiEndpoints": [{"apiName": "service-apis", "tokenEndPoint": {"uri": f"{SME}/oauth2/token"}}]})
        if url == f"{R1}/health":
            return httpx.Response(200, json={"status": "healthy"})
        if url == f"{SME}/invoker-registrations":
            self.invokers += 1
            return httpx.Response(201, json={"apiInvokerId": f"api-invoker-{self.invokers}", "onboardingSecret": "s3cret"})
        if url == f"{SME}/oauth2/token":
            body = json.loads(request.content)
            assert body["grant_type"] == "client_credentials" and body["client_secret"] == "s3cret"
            token = f"tok-{len(self.issued) + 1}"
            self.issued.append(token)
            return httpx.Response(200, json={"access_token": token, "expires_in": 3600, "token_type": "Bearer"})
        # everything else is R1's token-gated proxy
        auth = request.headers.get("authorization", "")
        token = auth.removeprefix("Bearer ")
        if token not in self.issued or token in self.revoked:
            return httpx.Response(401, json={"title": "UNAUTHORIZED"})
        module = request.url.path.split("/")[1]
        if request.url.path.endswith("/health"):
            if module in self.down_modules:
                raise httpx.ConnectError("down")
            return httpx.Response(200, json={"status": "healthy"})
        self.proxied.append(request)
        if self.next_response is not None:
            resp, self.next_response = self.next_response, None
            return resp
        return httpx.Response(200, json={"echo": request.url.path}, headers={
            "Connection": "close", "Keep-Alive": "timeout=5", "Set-Cookie": "upstream=1", "X-Upstream": "yes",
        })


@pytest.fixture
def smo():
    return FakeSmo()


@pytest.fixture
def db():
    return Database("sqlite://")


@pytest.fixture
def cfg():
    return Settings(r1_url=R1, jwt_secret="test-secret", cookie_secure=False, admin_password=PASSWORDS["admin"],
                    operator_password=PASSWORDS["operator"], viewer_password=PASSWORDS["viewer"])


@pytest.fixture
def app(cfg, db, smo):
    seed_users(db, cfg)
    return create_app(cfg, db=db, gateway=R1Gateway(R1, db, transport=httpx.MockTransport(smo.handler)))


def login(app, username) -> TestClient:
    client = TestClient(app)
    resp = client.post("/api/login", json={"username": username, "password": PASSWORDS[username]})
    assert resp.status_code == 200, resp.text
    client.headers["X-CSRF-Token"] = resp.json()["csrfToken"]
    return client


def audit_rows(db, action=None):
    with db.session() as s:
        rows = s.query(AuditEntry).all()
    return [r for r in rows if action is None or r.action == action]


# ---------------------------------------------------------------- login / session

def test_login_sets_httponly_session_and_readable_csrf_cookie(app):
    resp = TestClient(app).post("/api/login", json={"username": "operator", "password": PASSWORDS["operator"]})
    assert resp.status_code == 200
    assert resp.json()["role"] == "operator"
    set_cookies = resp.headers.get_list("set-cookie")
    session = next(c for c in set_cookies if c.startswith(f"{SESSION_COOKIE}="))
    csrf = next(c for c in set_cookies if c.startswith(f"{CSRF_COOKIE}="))
    assert "HttpOnly" in session and "SameSite=strict" in session and "Path=/api" in session
    assert "HttpOnly" not in csrf


def test_cookies_are_secure_by_default(cfg, db, smo):
    cfg.cookie_secure = True
    seed_users(db, cfg)
    app = create_app(cfg, db=db, gateway=R1Gateway(R1, db, transport=httpx.MockTransport(smo.handler)))
    resp = TestClient(app).post("/api/login", json={"username": "admin", "password": PASSWORDS["admin"]})
    assert all("Secure" in c for c in resp.headers.get_list("set-cookie"))


def test_wrong_password_is_rejected_and_audited(app, db):
    resp = TestClient(app).post("/api/login", json={"username": "viewer", "password": "nope"})
    assert resp.status_code == 401
    assert [r.username for r in audit_rows(db, "LOGIN_FAILED")] == ["viewer"]


def test_repeated_failures_lock_the_account(app):
    client = TestClient(app)
    for _ in range(5):
        assert client.post("/api/login", json={"username": "viewer", "password": "nope"}).status_code == 401
    # even the right password is refused while locked
    assert client.post("/api/login", json={"username": "viewer", "password": PASSWORDS["viewer"]}).status_code == 429


def test_me_requires_a_session(app):
    assert TestClient(app).get("/api/me").status_code == 401
    assert login(app, "viewer").get("/api/me").json()["role"] == "viewer"


def test_a_tampered_session_token_is_rejected(app):
    client = login(app, "viewer")
    token = client.cookies.get(SESSION_COOKIE)
    header, payload, sig = token.split(".")
    client.cookies.set(SESSION_COOKIE, f"{header}.{payload}.{sig[:-2]}AA", path="/api")
    assert client.get("/api/me").status_code == 401


def test_logout_clears_the_session(app):
    client = login(app, "viewer")
    assert client.post("/api/logout").status_code == 200
    assert client.get("/api/me").status_code == 401


def test_seed_needs_no_password_in_git(db, tmp_path, caplog):
    """No GUI_ADMIN_PASSWORD set: admin is still seeded, with a random
    password written to an owner-only file and never logged; operator/viewer
    are not seeded."""
    password_file = tmp_path / "initial-admin-password"
    cfg = Settings(r1_url=R1, jwt_secret="x", cookie_secure=False, initial_password_file=str(password_file))
    with caplog.at_level("DEBUG"):
        seed_users(db, cfg)
    with db.session() as s:
        assert [u.username for u in s.query(GuiUser).all()] == ["admin"]
    generated = password_file.read_text().strip()
    assert oct(password_file.stat().st_mode & 0o777) == "0o600"
    assert generated not in caplog.text and str(password_file) in caplog.text
    app = create_app(cfg, db=db, gateway=R1Gateway(R1, db, transport=httpx.MockTransport(FakeSmo().handler)))
    assert TestClient(app).post("/api/login", json={"username": "admin", "password": generated}).status_code == 200


def test_seeding_never_touches_an_existing_user_table(db, cfg):
    seed_users(db, cfg)
    cfg.admin_password = "changed-in-env"
    seed_users(db, cfg)
    client = TestClient(create_app(cfg, db=db, gateway=R1Gateway(R1, db, transport=httpx.MockTransport(FakeSmo().handler))))
    assert client.post("/api/login", json={"username": "admin", "password": PASSWORDS["admin"]}).status_code == 200


# ---------------------------------------------------------------- RBAC through the proxy

def test_viewer_can_read(app, smo):
    resp = login(app, "viewer").get("/api/smo/rapp-mgmt/instances", params={"state": "RUNNING"})
    assert resp.status_code == 200
    [req] = smo.proxied
    assert req.url.path == "/rapp-mgmt/instances" and req.url.params["state"] == "RUNNING"


def test_viewer_is_blocked_on_post_and_nothing_reaches_r1(app, smo, db):
    resp = login(app, "viewer").post("/api/smo/aimgf/training-jobs", json={"modelId": "m", "producerId": "gui"})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "requires role operator"
    assert smo.proxied == []
    [denied] = audit_rows(db, "DENIED")
    assert (denied.username, denied.method, denied.path) == ("viewer", "POST", "/aimgf/training-jobs")


def test_operator_can_train_and_ack(app, smo):
    client = login(app, "operator")
    assert client.post("/api/smo/aimgf/training-jobs", json={"modelId": "m", "producerId": "gui"}).status_code == 200
    assert client.patch("/api/smo/ran-nf-oam/alarms/a-1/ack", params={"new_state": "ACKNOWLEDGED"}).status_code == 200
    assert [r.url.path for r in smo.proxied] == ["/aimgf/training-jobs", "/ran-nf-oam/alarms/a-1/ack"]
    assert json.loads(smo.proxied[0].content) == {"modelId": "m", "producerId": "gui"}


def test_alarm_ack_user_is_the_gui_user_not_whatever_the_browser_sent(app, smo):
    login(app, "operator").patch("/api/smo/ran-nf-oam/alarms/a-1/ack",
                                 params={"new_state": "ACKNOWLEDGED", "ack_user_id": "someone-else"})
    params = smo.proxied[0].url.params
    assert params.get_list("ack_user_id") == ["operator"]
    assert params["new_state"] == "ACKNOWLEDGED"


def test_operator_cannot_terminate_but_admin_can(app, smo):
    assert login(app, "operator").post("/api/smo/rapp-mgmt/instances/i-1/terminate").status_code == 403
    assert smo.proxied == []
    assert login(app, "admin").post("/api/smo/rapp-mgmt/instances/i-1/terminate").status_code == 200
    assert [r.url.path for r in smo.proxied] == ["/rapp-mgmt/instances/i-1/terminate"]


def test_model_deprecation_is_admin_only_even_with_a_duplicated_param(app, smo):
    operator = login(app, "operator")
    assert operator.post("/api/smo/aimgf/models/m-1/advance?event=CERTIFY").status_code == 200
    assert operator.post("/api/smo/aimgf/models/m-1/advance?event=DEPRECATE").status_code == 403
    assert operator.post("/api/smo/aimgf/models/m-1/advance?event=CERTIFY&event=DEPRECATE").status_code == 403
    assert login(app, "admin").post("/api/smo/aimgf/models/m-1/advance?event=DEPRECATE").status_code == 200


def test_package_delete_is_admin_only(app):
    assert login(app, "operator").delete("/api/smo/onboarding/packages/p-1").status_code == 403
    assert login(app, "admin").delete("/api/smo/onboarding/packages/p-1").status_code == 200


def test_routes_not_in_the_table_are_refused_even_for_admin(app, smo):
    admin = login(app, "admin")
    assert admin.post("/api/smo/sme/oauth2/token", json={}).status_code == 403
    assert admin.post("/api/smo/nfo/deployments", json={}).status_code == 403
    assert admin.get("/api/smo/not-a-module/x").status_code == 403
    assert smo.proxied == []


def test_remedial_action_admin_flag_is_derived_from_the_gui_role(app, smo):
    login(app, "operator").post("/api/smo/sa-smos/monitors/m-1/remedial-actions",
                                params={"action_type": "SCALE", "requester_is_admin": "true"})
    login(app, "admin").post("/api/smo/sa-smos/monitors/m-1/remedial-actions", params={"action_type": "SCALE"})
    assert [r.url.params.get_list("requester_is_admin") for r in smo.proxied] == [["false"], ["true"]]


def test_gui_created_intents_carry_the_gui_rmio_identity(app, smo):
    client = login(app, "operator")
    client.post("/api/smo/intent-service/intents", json={"expectations": [], "rmioId": "spoofed-rapp"})
    client.patch("/api/smo/intent-service/intents/i-1/admin-state", json={"newState": "DEACTIVATED", "requesterId": "spoofed-rapp"})
    assert json.loads(smo.proxied[0].content)["rmioId"] == "smo-gui"
    assert json.loads(smo.proxied[1].content) == {"newState": "DEACTIVATED", "requesterId": "smo-gui"}


def test_cm_write_identity_and_msac_tier_come_from_the_gui_role(app, smo):
    body = {"scope": "entire-RAN", "changes": [], "requestedBy": "someone", "msacRole": "admin"}
    login(app, "operator").post("/api/smo/ran-nf-oam/config-jobs", json=body)
    login(app, "admin").post("/api/smo/ran-nf-oam/config-jobs", json=body)
    sent = [json.loads(r.content) for r in smo.proxied]
    assert [(b["requestedBy"], b["msacRole"]) for b in sent] == [("smo-gui:operator", None), ("smo-gui:admin", "admin")]


def test_role_change_applies_on_the_next_request(app, db):
    operator = login(app, "operator")
    assert operator.post("/api/smo/so-smos/orders", json={"scope": "s", "steps": []}).status_code == 200
    login(app, "admin").patch("/api/admin/users/operator", json={"role": "viewer"})
    assert operator.post("/api/smo/so-smos/orders", json={"scope": "s", "steps": []}).status_code == 403


# ---------------------------------------------------------------- CSRF / bearer

def test_cookie_session_mutation_without_csrf_header_is_refused(app, smo):
    client = login(app, "operator")
    del client.headers["X-CSRF-Token"]
    assert client.post("/api/smo/so-smos/orders", json={}).status_code == 403
    client.headers["X-CSRF-Token"] = "wrong"
    assert client.post("/api/smo/so-smos/orders", json={}).status_code == 403
    assert smo.proxied == []


def test_oauth2_password_grant_bearer_token_needs_no_csrf(app, smo):
    client = TestClient(app)
    resp = client.post("/api/token", data={"grant_type": "password", "username": "operator", "password": PASSWORDS["operator"]})
    assert resp.status_code == 200 and resp.json()["token_type"] == "Bearer"
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    assert client.post("/api/smo/so-smos/orders", json={"scope": "s", "steps": []}, headers=headers).status_code == 200
    bad = client.post("/api/token", data={"grant_type": "password", "username": "operator", "password": "x"})
    assert bad.status_code == 400 and bad.json() == {"error": "invalid_grant"}


# ---------------------------------------------------------------- proxy mechanics

def test_proxy_forwards_the_bff_token_never_the_browser_credentials(app, smo):
    client = login(app, "viewer")
    client.get("/api/smo/onboarding/packages", headers={"Authorization-Hint": "x", "X-Custom": "y"})
    req = smo.proxied[0]
    assert req.headers["authorization"] == "Bearer tok-1"
    assert "cookie" not in req.headers and "x-csrf-token" not in req.headers and "x-custom" not in req.headers
    assert smo.invokers == 1   # onboarded once at SME, via the bootstrap-advertised endpoint


def test_proxy_strips_hop_by_hop_and_upstream_set_cookie(app):
    resp = login(app, "viewer").get("/api/smo/onboarding/packages")
    assert resp.headers["x-upstream"] == "yes"
    assert "keep-alive" not in resp.headers
    assert resp.headers.get("connection") != "close"
    assert "upstream=1" not in resp.headers.get("set-cookie", "")


def test_smo_auth_failure_detail_does_not_leak_exception_text(cfg, db):
    def sme_down(request):
        raise httpx.ConnectError("refused: internal-host-10.0.0.7:8000")

    seed_users(db, cfg)
    app = create_app(cfg, db=db, gateway=R1Gateway(R1, db, transport=httpx.MockTransport(sme_down)))
    resp = login(app, "viewer").get("/api/smo/onboarding/packages")
    assert resp.status_code == 502 and resp.json()["title"] == "SMO_AUTH_FAILED"
    assert "internal-host" not in resp.text and "ConnectError" not in resp.text


def test_proxy_passes_upstream_errors_through(app, smo):
    smo.next_response = httpx.Response(409, json={"title": "MODEL_NOT_CERTIFIED"})
    resp = login(app, "operator").post("/api/smo/rapp-mgmt/instances", json={"packageId": "p"})
    assert resp.status_code == 409 and resp.json() == {"title": "MODEL_NOT_CERTIFIED"}


def test_expired_smo_token_is_refreshed_once(app, smo):
    client = login(app, "viewer")
    client.get("/api/smo/onboarding/packages")
    smo.revoked.add("tok-1")
    assert client.get("/api/smo/onboarding/packages").status_code == 200
    assert smo.issued == ["tok-1", "tok-2"]
    assert smo.invokers == 1   # the stored invoker credential was reused


def test_mutations_are_audited_with_status(app, db):
    login(app, "operator").post("/api/smo/aimgf/training-jobs", json={"modelId": "m", "producerId": "gui"})
    [row] = audit_rows(db, "PROXY")
    assert (row.username, row.role, row.method, row.path, row.status_code) == \
        ("operator", "operator", "POST", "/aimgf/training-jobs", 200)


def test_reads_are_not_audited(app, db):
    login(app, "viewer").get("/api/smo/onboarding/packages")
    assert audit_rows(db, "PROXY") == []


def test_audit_log_is_append_only(app, db):
    login(app, "viewer")
    with db.session() as s:
        row = s.query(AuditEntry).first()
        row.detail = "rewritten"
        with pytest.raises(PermissionError):
            s.commit()


def test_security_headers_on_every_bff_response(app):
    resp = TestClient(app).get("/api/me")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in resp.headers["content-security-policy"]
    assert resp.headers["x-frame-options"] == "DENY"


# ---------------------------------------------------------------- health

def test_modules_status_probes_every_module_via_r1(app, smo):
    smo.down_modules.add("nfo")
    body = login(app, "viewer").get("/api/modules/status").json()
    by_module = {m["module"]: m for m in body["modules"]}
    assert list(by_module) == STATUS_MODULES and len(STATUS_MODULES) == 17
    assert by_module["nfo"]["healthy"] is False and by_module["nfo"]["error"] == "unreachable"
    assert all(m["healthy"] for name, m in by_module.items() if name != "nfo")
    assert all(isinstance(m["latencyMs"], float) for m in body["modules"])


def test_modules_status_reports_smo_auth_failure_without_crashing(cfg, db):
    def sme_down(request):
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "healthy"})
        raise httpx.ConnectError("refused")

    seed_users(db, cfg)
    app = create_app(cfg, db=db, gateway=R1Gateway(R1, db, transport=httpx.MockTransport(sme_down)))
    body = login(app, "viewer").get("/api/modules/status").json()
    by_module = {m["module"]: m for m in body["modules"]}
    assert by_module["r1-termination"]["healthy"] is True
    assert by_module["sme"]["healthy"] is False and by_module["sme"]["error"] == "auth: no SMO access token"


# ---------------------------------------------------------------- admin

def test_user_admin_is_admin_only(app):
    assert login(app, "operator").get("/api/admin/users").status_code == 403
    assert login(app, "operator").get("/api/admin/audit").status_code == 403


def test_admin_creates_updates_and_deletes_a_user(app):
    admin = login(app, "admin")
    created = admin.post("/api/admin/users", json={"username": "noc1", "password": "long-enough", "role": "viewer"})
    assert created.status_code == 201 and created.json()["role"] == "viewer"
    assert admin.post("/api/admin/users", json={"username": "noc1", "password": "long-enough", "role": "viewer"}).status_code == 409
    assert admin.post("/api/admin/users", json={"username": "Bad Name", "password": "long-enough", "role": "viewer"}).status_code == 400
    assert admin.post("/api/admin/users", json={"username": "noc2", "password": "short", "role": "viewer"}).status_code == 422

    assert admin.patch("/api/admin/users/noc1", json={"role": "operator"}).json()["role"] == "operator"
    assert admin.delete("/api/admin/users/noc1").status_code == 204
    assert "noc1" not in [u["username"] for u in admin.get("/api/admin/users").json()]


def test_password_reset_and_deactivation_revoke_existing_sessions(app):
    viewer = login(app, "viewer")
    login(app, "admin").patch("/api/admin/users/viewer", json={"password": "a-new-password"})
    assert viewer.get("/api/me").status_code == 401


def test_the_last_active_admin_cannot_be_removed_or_demoted(app):
    admin = login(app, "admin")
    assert admin.patch("/api/admin/users/admin", json={"role": "operator"}).status_code == 409
    assert admin.delete("/api/admin/users/admin").status_code == 409
    admin.post("/api/admin/users", json={"username": "admin2", "password": "long-enough", "role": "admin"})
    assert admin.delete("/api/admin/users/admin2").status_code == 204


def test_change_own_password_keeps_the_current_session(app):
    client = login(app, "operator")
    resp = client.post("/api/me/password", json={"currentPassword": PASSWORDS["operator"], "newPassword": "brand-new-pass"})
    assert resp.status_code == 200
    client.headers["X-CSRF-Token"] = resp.json()["csrfToken"]
    assert client.get("/api/me").status_code == 200
    assert TestClient(app).post("/api/login", json={"username": "operator", "password": "brand-new-pass"}).status_code == 200


def test_audit_endpoint_lists_newest_first_and_filters(app):
    admin = login(app, "admin")
    admin.post("/api/admin/users", json={"username": "noc1", "password": "long-enough", "role": "viewer"})
    entries = admin.get("/api/admin/audit").json()
    assert [e["action"] for e in entries][:2] == ["USER_CREATED", "LOGIN"]
    assert {e["action"] for e in admin.get("/api/admin/audit", params={"action": "LOGIN"}).json()} == {"LOGIN"}


def test_permissions_endpoint_exposes_the_rbac_table(app):
    body = login(app, "viewer").get("/api/permissions").json()
    assert body["role"] == "viewer"
    assert {"method": "POST", "pattern": "^/rapp-mgmt/instances/[^/]+/terminate$", "role": "admin", "queryMatch": {}} in body["rules"]
