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
                       ("DELETE", "/ran-nf-oam/kpi-definitions/k"), ("POST", "/ran-nf-oam/config-history/purge")]


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


@pytest.mark.parametrize("method, path", [("GET", "/ran-nf-oam/rapp-limits/x"), ("GET", "/ran-nf-oam/kpi-definitions"), ("GET", "/ran-nf-oam/kpis/k"),
                                          ("POST", "/ran-nf-oam/config-jobs"), ("PUT", "/ran-nf-oam/rapp-limits/x/y"), ("PUT", "/sme/rapp-limits/x")])
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
