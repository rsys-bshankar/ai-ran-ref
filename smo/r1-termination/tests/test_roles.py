"""PR-SEC-14: the role policy at the gateway: an rApp is refused on the internal-only routes, an SMO module is not, and what a backend reads as the
caller's role is only ever what SME said."""

import json

import pytest
from fastapi.testclient import TestClient

from smo_shared import roles

from app.main import ROUTES, app

client = TestClient(app)
AUTH = {"Authorization": "Bearer t"}
INTROSPECT_URL = f"{ROUTES['/sme']}/oauth2/introspect"


@pytest.fixture(autouse=True)
def fresh_rate_limiter():
    from app.main import _limiter
    _limiter.clear()
    yield
    _limiter.clear()


class FakeResponse:
    def __init__(self, content=b"{}", status_code=200):
        self.content, self.status_code, self.headers = content, status_code, {}

    def json(self):
        return json.loads(self.content)


@pytest.fixture
def gateway(monkeypatch):
    """SME says what `sme_says` holds; backends record what they were sent."""
    state = {"sme_says": {"active": True, "client_id": "inv-1", "role": "rapp"}, "forwarded": []}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, **kwargs):
            if url == INTROSPECT_URL:
                return FakeResponse(json.dumps(state["sme_says"]).encode())
            state["forwarded"].append((method, url, kwargs.get("headers", {})))
            return FakeResponse()

    monkeypatch.setattr("app.main.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.delenv("SMO_ROLE_ENFORCEMENT", raising=False)
    return state


INTERNAL_ONLY_CALLS = [("PUT", "/ran-nf-oam/rapp-limits/x"), ("DELETE", "/ran-nf-oam/rapp-limits/x"), ("PUT", "/ran-nf-oam/kpi-definitions/k"),
                       ("DELETE", "/ran-nf-oam/kpi-definitions/k"), ("POST", "/ran-nf-oam/config-history/purge"), ("PUT", "/ran-nf-oam/rapp-kill/x"),
                       ("DELETE", "/ran-nf-oam/rapp-kill/x"), ("GET", "/ran-nf-oam/rapp-kill"), ("PUT", "/rapp-mgmt/instances/i/kill"),
                       ("DELETE", "/rapp-mgmt/instances/i/kill"), ("POST", "/ran-nf-oam/kpi-definitions/standard"), ("POST", "/ran-nf-oam/kpis/k/publish"), ("GET", "/ran-nf-oam/safeguard-refusals"), ("GET", "/ran-nf-oam/safeguard-subscriptions"),
                       ("POST", "/ran-nf-oam/safeguard-subscriptions"), ("DELETE", "/ran-nf-oam/safeguard-subscriptions/s"),
                       # AI-11 / AI-13: who decides, who must be asked, and the lists that name other rApps' actions
                       ("PUT", "/ran-nf-oam/rapp-approval-policy/x"), ("DELETE", "/ran-nf-oam/rapp-approval-policy/x"), ("POST", "/ran-nf-oam/rapp-approvals/a/approve"),
                       ("POST", "/ran-nf-oam/rapp-approvals/a/reject"), ("POST", "/ran-nf-oam/rapp-approvals/expire-due"), ("GET", "/ran-nf-oam/rapp-approvals"),
                       ("GET", "/ran-nf-oam/approval-subscriptions"), ("POST", "/ran-nf-oam/approval-subscriptions"), ("DELETE", "/ran-nf-oam/approval-subscriptions/s"),
                       ("GET", "/ran-nf-oam/decision-records")]


@pytest.mark.parametrize("method, path", INTERNAL_ONLY_CALLS)
def test_an_rapp_is_refused_on_an_internal_only_route_and_the_backend_is_never_called(gateway, method, path):
    resp = client.request(method, path, headers=AUTH)
    assert resp.status_code == 403 and resp.json()["title"] == "ROLE_NOT_PERMITTED"
    assert gateway["forwarded"] == []


@pytest.mark.parametrize("method, path", INTERNAL_ONLY_CALLS)
def test_an_smo_module_is_not_refused_there(gateway, method, path):
    gateway["sme_says"]["role"] = "internal"
    assert client.request(method, path, headers=AUTH).status_code == 200
    assert len(gateway["forwarded"]) == 1


@pytest.mark.parametrize("method, path", [("GET", "/ran-nf-oam/rapp-limits/x"), ("GET", "/ran-nf-oam/rapp-kill/x"), ("GET", "/ran-nf-oam/kpi-definitions"), ("GET", "/ran-nf-oam/kpi-definitions/standard"),
                                          ("GET", "/ran-nf-oam/rapp-approvals/a"), ("GET", "/ran-nf-oam/decision-records/d"), ("GET", "/ran-nf-oam/rapp-approval-policy/x"), ("GET", "/ran-nf-oam/kpis/k"),
                                          ("POST", "/ran-nf-oam/config-jobs"), ("POST", "/ran-nf-oam/config-jobs/j/rollback"), ("POST", "/dme/actions"), ("POST", "/aimgf/training-jobs"),
                                          ("DELETE", "/sme/provider-registrations/a"), ("GET", "/onboarding/packages"), ("GET", "/rapp-mgmt/instances"), ("GET", "/sme/trusted-invokers")])
def test_other_routes_are_open_to_an_rapp_as_before(gateway, method, path):
    assert client.request(method, path, headers=AUTH).status_code == 200


def test_audit_mode_lets_the_call_through(gateway, monkeypatch):
    monkeypatch.setenv("SMO_ROLE_ENFORCEMENT", "audit")
    assert client.put("/ran-nf-oam/rapp-limits/x", headers=AUTH).status_code == 200
    assert len(gateway["forwarded"]) == 1


def test_a_mistyped_mode_still_enforces(gateway, monkeypatch):
    monkeypatch.setenv("SMO_ROLE_ENFORCEMENT", "of")
    assert client.put("/ran-nf-oam/rapp-limits/x", headers=AUTH).status_code == 403


def test_the_role_a_backend_sees_is_the_one_sme_gave_never_the_callers(gateway):
    client.get("/ran-nf-oam/health", headers={**AUTH, roles.ROLE_HEADER: "internal", "X-R1-Invoker-Id": "someone-else"})
    headers = {k.lower(): v for k, v in gateway["forwarded"][0][2].items()}
    assert headers[roles.ROLE_HEADER.lower()] == "rapp" and headers["x-r1-invoker-id"] == "inv-1"


def test_an_sme_that_does_not_say_is_read_by_the_scope(gateway):
    gateway["sme_says"] = {"active": True, "client_id": "old", "scope": "smo-internal"}
    assert client.put("/ran-nf-oam/rapp-limits/x", headers=AUTH).status_code == 200
    gateway["sme_says"] = {"active": True, "client_id": "old", "scope": "3gpp#aef:api"}
    assert client.put("/ran-nf-oam/rapp-limits/x", headers=AUTH).status_code == 403
    gateway["sme_says"] = {"active": True, "client_id": "old"}
    assert client.put("/ran-nf-oam/rapp-limits/x", headers=AUTH).status_code == 403            # no role, no scope: an rApp


def test_refusals_are_counted(gateway):
    from prometheus_client import REGISTRY
    before = REGISTRY.get_sample_value("smo_role_refusals_total", {"module": "ran-nf-oam", "action": "refused"}) or 0
    client.put("/ran-nf-oam/rapp-limits/x", headers=AUTH)
    assert REGISTRY.get_sample_value("smo_role_refusals_total", {"module": "ran-nf-oam", "action": "refused"}) == before + 1


def _forwarded(gateway):
    return {k.lower(): v for k, v in gateway["forwarded"][0][2].items()}


def test_an_internal_module_may_say_whom_it_acts_for(gateway):
    """X-R1-On-Behalf-Of: DME acting for an rApp tells RAN NF OAM, so the rApp's own safeguards apply to what DME writes."""
    gateway["sme_says"] = {"active": True, "client_id": "dme-client", "role": "internal"}
    client.get("/ran-nf-oam/health", headers={**AUTH, "X-R1-On-Behalf-Of": "es-client"})
    headers = _forwarded(gateway)
    assert headers["x-r1-on-behalf-of"] == "es-client" and headers["x-r1-invoker-id"] == "dme-client" and headers[roles.ROLE_HEADER.lower()] == "internal"


def test_an_rapp_cannot_pose_as_another_rapp(gateway):
    """The default caller is an rApp: its own claim of whom it acts for is dropped, not forwarded."""
    client.get("/ran-nf-oam/health", headers={**AUTH, "X-R1-On-Behalf-Of": "someone-else"})
    headers = _forwarded(gateway)
    assert "x-r1-on-behalf-of" not in headers and headers["x-r1-invoker-id"] == "inv-1"


def test_a_module_that_names_nobody_forwards_nothing(gateway):
    gateway["sme_says"] = {"active": True, "client_id": "dme-client", "role": "internal"}
    client.get("/ran-nf-oam/health", headers=AUTH)
    assert "x-r1-on-behalf-of" not in _forwarded(gateway)


# --- the rApp change allow-list -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("method, path", [
    ("POST", "/onboarding/packages"), ("DELETE", "/onboarding/packages/p"), ("POST", "/rapp-mgmt/instances"), ("POST", "/rapp-mgmt/instances/i/terminate"),
    ("POST", "/nfo/deployments"), ("POST", "/focom/provisioning-requests"), ("POST", "/so-smos/anything"), ("POST", "/rapps/00000000-0000-0000-0000-000000000000/operator/instances/i/start"),
    ("PUT", "/sme/invoker-registrations/x"), ("DELETE", "/sme/invoker-registrations/x"), ("POST", "/sme/invoker-registrations/purge-stale"),
    ("PUT", "/sme/trusted-invokers/x"), ("POST", "/aimgf/ml-training-functions"), ("POST", "/aimgf/execution-timeouts/sweep"),
    ("POST", "/aimgf/models/m/runtime/terminate"), ("POST", "/mlmr/storages"), ("POST", "/mdaf/mda-functions"), ("PUT", "/ran-nf-oam/rapp-limits/x"),
    ("POST", "/ran-nf-oam/config-jobs/j/abort"), ("PUT", "/ran-nf-oam/kpi-schedules/s"), ("POST", "/ran-nf-oam/managed-elements"),
    # GUI-8.3: an rApp registers (and forgets) the operator API of an instance, with those two methods and that one path
    ("POST", "/rapp-mgmt/instances/i/operator-api"), ("PATCH", "/rapp-mgmt/instances/i/operator-api"), ("PUT", "/rapp-mgmt/instances/i/operator-api/x"),
    ("PUT", "/rapp-mgmt/instances/i/j/operator-api"), ("DELETE", "/rapp-mgmt/instances/operator-api"),
])
def test_an_rapp_may_not_change_what_it_does_not_use(gateway, method, path):
    resp = client.request(method, path, headers=AUTH)
    assert resp.status_code == 403 and resp.json()["title"] == "ROLE_NOT_PERMITTED"
    assert gateway["forwarded"] == []


@pytest.mark.parametrize("method, path", [
    ("POST", "/ran-nf-oam/config-jobs"), ("POST", "/dme/actions"), ("PUT", "/dme/data-jobs/j"), ("POST", "/aimgf/models/m/advance"),
    ("POST", "/aimgf/ml-training-requests"), ("PATCH", "/intent-service/intents/i/admin-state"), ("POST", "/sme/oauth2/token"),
    ("POST", "/mlmr/models/m/artifact"), ("DELETE", "/mdaf/subscriptions/s"), ("PUT", "/rapp-mgmt/instances/i/operator-api"), ("DELETE", "/rapp-mgmt/instances/i/operator-api"),
])
def test_an_rapp_may_change_what_it_uses(gateway, method, path):
    assert client.request(method, path, headers=AUTH).status_code == 200


def test_every_change_is_decided_for_an_internal_module_too_and_it_is_never_refused(gateway):
    gateway["sme_says"]["role"] = roles.ROLE_INTERNAL
    assert client.post("/onboarding/packages", headers=AUTH).status_code == 200


def test_audit_mode_lets_an_unlisted_change_through(gateway, monkeypatch):
    monkeypatch.setenv("SMO_ROLE_ENFORCEMENT", "audit")
    assert client.post("/onboarding/packages", headers=AUTH).status_code == 200


def test_the_allow_list_names_only_modules_the_gateway_routes_to():
    from app.main import ROUTES
    assert set(roles.RAPP_MAY_CHANGE) <= set(ROUTES)
