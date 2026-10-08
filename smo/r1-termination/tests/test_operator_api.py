"""GUI-8.3: the gateway's dynamic prefix `/rapps/{instanceId}/operator/<route>` resolves to the base the instance registered at rApp Management."""

import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app import operator_api
from app.main import ROUTES, app

client = TestClient(app)
AUTH = {"Authorization": "Bearer t"}
INTROSPECT_URL = f"{ROUTES['/sme']}/oauth2/introspect"
INSTANCE = str(uuid.uuid4())
RAPP_MGMT_LOOKUP = f"{ROUTES['/rapp-mgmt']}/instances/{INSTANCE}/operator-api"


class FakeResponse:
    def __init__(self, content=b"{}", status_code=200, headers=None):
        self.content, self.status_code, self.headers = content, status_code, headers or {}

    def json(self):
        return json.loads(self.content)


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    from app.main import _limiter
    _limiter.clear()
    operator_api.clear_cache()
    monkeypatch.delenv("R1_OPERATOR_API_CACHE_SECONDS", raising=False)
    yield
    _limiter.clear()
    operator_api.clear_cache()


@pytest.fixture
def world(monkeypatch):
    """SME says who calls; rApp Management says where the rApp is; the rApp records what it was sent."""
    state = {"sme_says": {"active": True, "client_id": "smo-gui-inv", "role": "internal"},
             "rapp_mgmt": (200, {"instanceId": INSTANCE, "state": "RUNNING", "operatorApiBase": "http://my-rapp:8000"}),
             "rapp_answer": FakeResponse(b'{"cells": []}', 200, {"content-type": "application/json", "set-cookie": "x=1", "x-rapp": "y"}),
             "rapp_error": None, "lookups": 0, "rapp_calls": [], "other": []}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, **kwargs):
            if url == INTROSPECT_URL:
                return FakeResponse(json.dumps(state["sme_says"]).encode())
            if url.startswith(ROUTES["/rapp-mgmt"]):
                state["lookups"] += 1
                if isinstance(state["rapp_mgmt"], Exception):
                    raise state["rapp_mgmt"]
                code, payload = state["rapp_mgmt"]
                return FakeResponse(json.dumps(payload).encode(), code)
            if url.startswith(("http://my-rapp:8000", "https://rapp.example")) or url.startswith("http://127.") or url.startswith("http://169.254."):
                state["rapp_calls"].append((method, url, kwargs))
                if state["rapp_error"]:
                    raise state["rapp_error"]
                return state["rapp_answer"]
            state["other"].append((method, url))
            return FakeResponse()

    monkeypatch.setattr("app.main.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.delenv("SMO_ROLE_ENFORCEMENT", raising=False)
    return state


def test_the_prefix_reaches_the_registered_base_with_the_rest_of_the_path_and_the_query(world):
    resp = client.get(f"/rapps/{INSTANCE}/operator/instances/{INSTANCE}/dashboard?points=48", headers=AUTH)
    assert resp.status_code == 200 and resp.json() == {"cells": []}
    method, url, kwargs = world["rapp_calls"][0]
    assert (method, url) == ("GET", f"http://my-rapp:8000/instances/{INSTANCE}/dashboard")
    assert dict(kwargs["params"]) == {"points": "48"}
    assert world["other"] == []                                   # nothing went to a static route
    assert "set-cookie" not in resp.headers and resp.headers["x-rapp"] == "y"


def test_a_change_is_forwarded_with_its_method_and_body(world):
    resp = client.post(f"/rapps/{INSTANCE}/operator/instances/{INSTANCE}/cells/C1/override", headers={**AUTH, "Content-Type": "application/json"},
                       content=b'{"operator": "smo-gui:alice"}')
    assert resp.status_code == 200
    method, url, kwargs = world["rapp_calls"][0]
    assert method == "POST" and url.endswith(f"/instances/{INSTANCE}/cells/C1/override") and kwargs["content"] == b'{"operator": "smo-gui:alice"}'


def test_the_callers_credentials_never_reach_the_rapp(world):
    client.get(f"/rapps/{INSTANCE}/operator/instances/x", headers={**AUTH, "Cookie": "smo_session=secret", "X-Evil": "1", "X-R1-Role": "internal"})
    headers = {k.lower(): v for k, v in world["rapp_calls"][0][2]["headers"].items()}
    assert "authorization" not in headers and "cookie" not in headers and "x-evil" not in headers
    assert headers["x-r1-role"] == "internal" and headers["x-r1-invoker-id"] == "smo-gui-inv"      # the identity the gateway vouches for
    assert "x-correlation-id" in headers


def test_the_base_may_carry_a_path_prefix(world):
    world["rapp_mgmt"] = (200, {"instanceId": INSTANCE, "state": "RUNNING", "operatorApiBase": "https://rapp.example/ops/"})
    client.get(f"/rapps/{INSTANCE}/operator/x/y", headers=AUTH)
    assert world["rapp_calls"][0][1] == "https://rapp.example/ops/x/y"


def test_an_encoded_slash_is_decoded_once_by_the_framework_and_then_checked_as_the_path_it_is(world):
    client.get(f"/rapps/{INSTANCE}/operator/a%2Fb", headers=AUTH)
    assert world["rapp_calls"][0][1] == "http://my-rapp:8000/a/b"
    assert client.get(f"/rapps/{INSTANCE}/operator/%2e%2e/x", headers=AUTH).status_code == 400


def test_no_registered_base_is_a_404_with_a_fixed_title(world):
    world["rapp_mgmt"] = (200, {"instanceId": INSTANCE, "state": "RUNNING", "operatorApiBase": None})
    resp = client.get(f"/rapps/{INSTANCE}/operator/instances/x", headers=AUTH)
    assert resp.status_code == 404 and resp.json()["title"] == "OPERATOR_API_NOT_REGISTERED"
    assert world["rapp_calls"] == []


def test_an_unknown_instance_is_the_same_404(world):
    world["rapp_mgmt"] = (404, {"detail": "no such RAppInstance"})
    resp = client.get(f"/rapps/{INSTANCE}/operator/instances/x", headers=AUTH)
    assert resp.status_code == 404 and resp.json()["title"] == "OPERATOR_API_NOT_REGISTERED"


@pytest.mark.parametrize("path", [
    "/rapps/not-a-uuid/operator/x", f"/rapps/{INSTANCE}", f"/rapps/{INSTANCE}/other/x", f"/rapps/{INSTANCE}/operatorx/x",
    f"/rapps/{INSTANCE}/operator/a b", f"/rapps/{INSTANCE}/operator/a;b", f"/rapps/{INSTANCE}/operator/a:b"])
def test_a_path_that_is_not_that_shape_is_refused_before_anything_is_looked_up(world, path):
    resp = client.get(path, headers=AUTH)
    assert resp.status_code == 404 and resp.json()["title"] == "NO_ROUTE"
    assert world["lookups"] == 0 and world["rapp_calls"] == []


@pytest.mark.parametrize("path", [f"/rapps/{INSTANCE}/operator/../x", f"/rapps/{INSTANCE}/operator//x"])
def test_dot_segments_and_empty_segments_are_refused_by_the_gateways_path_check(world, path):
    resp = client.request("GET", path, headers=AUTH)
    assert resp.status_code in (400, 404)
    assert world["rapp_calls"] == []


def test_the_lookup_is_cached_briefly_and_zero_turns_the_cache_off(world, monkeypatch):
    for _ in range(3):
        client.get(f"/rapps/{INSTANCE}/operator/x", headers=AUTH)
    assert world["lookups"] == 1
    monkeypatch.setenv("R1_OPERATOR_API_CACHE_SECONDS", "0")
    client.get(f"/rapps/{INSTANCE}/operator/x", headers=AUTH)
    client.get(f"/rapps/{INSTANCE}/operator/x", headers=AUTH)
    assert world["lookups"] == 3


def test_a_changed_registration_is_picked_up_when_the_cache_expires(world, monkeypatch):
    monkeypatch.setenv("R1_OPERATOR_API_CACHE_SECONDS", "0")
    client.get(f"/rapps/{INSTANCE}/operator/x", headers=AUTH)
    world["rapp_mgmt"] = (200, {"instanceId": INSTANCE, "state": "RUNNING", "operatorApiBase": None})
    assert client.get(f"/rapps/{INSTANCE}/operator/x", headers=AUTH).status_code == 404


def test_rapp_management_down_is_a_503_not_a_guess(world):
    world["rapp_mgmt"] = httpx.ConnectError("boom: password=hunter2")
    resp = client.get(f"/rapps/{INSTANCE}/operator/x", headers=AUTH)
    assert resp.status_code == 503 and resp.json()["title"] == "OPERATOR_API_UNRESOLVED" and resp.headers["Retry-After"] == "5"
    assert "hunter2" not in resp.text and "boom" not in resp.text
    world["rapp_mgmt"] = (500, {})
    assert client.get(f"/rapps/{INSTANCE}/operator/x", headers=AUTH).status_code == 503


def test_an_answer_a_little_old_is_used_when_rapp_management_goes_down(world, monkeypatch):
    monkeypatch.setenv("R1_OPERATOR_API_CACHE_SECONDS", "0")
    assert client.get(f"/rapps/{INSTANCE}/operator/x", headers=AUTH).status_code == 200
    world["rapp_mgmt"] = httpx.ConnectError("down")
    assert client.get(f"/rapps/{INSTANCE}/operator/x", headers=AUTH).status_code == 200


def test_an_unreachable_rapp_is_a_502_and_a_slow_one_a_504_with_no_exception_text(world):
    world["rapp_error"] = httpx.ConnectError("connect to 10.9.9.9:8000 failed")
    resp = client.get(f"/rapps/{INSTANCE}/operator/x", headers=AUTH)
    assert resp.status_code == 502 and resp.json()["title"] == "UPSTREAM_UNAVAILABLE"
    assert "10.9.9.9" not in resp.text and "my-rapp" not in resp.text
    world["rapp_error"] = httpx.ReadTimeout("read timed out after 60 s at my-rapp")
    resp = client.get(f"/rapps/{INSTANCE}/operator/x", headers=AUTH)
    assert resp.status_code == 504 and resp.json()["title"] == "UPSTREAM_TIMEOUT" and "my-rapp" not in resp.text


@pytest.mark.parametrize("base", ["http://127.0.0.1:8000", "http://169.254.169.254", "file:///etc/passwd", "http://u:p@my-rapp:8000"])
def test_a_registered_base_that_fails_the_guard_is_never_called(world, base):
    """rApp Management checks the base when it is stored; the gateway does not trust the stored value: a row written another way is not called."""
    world["rapp_mgmt"] = (200, {"instanceId": INSTANCE, "state": "RUNNING", "operatorApiBase": base})
    resp = client.get(f"/rapps/{INSTANCE}/operator/x", headers=AUTH)
    assert resp.status_code == 404 and resp.json()["title"] == "OPERATOR_API_NOT_REGISTERED"
    assert world["rapp_calls"] == []


def test_a_rapp_may_read_another_rapps_operator_api_but_never_change_it(world):
    """Reads are open to every valid token, as for every module (the sample rApps coordinate this way: a cell list another rApp publishes); a change by an
    rApp is refused by the same allow-list as everywhere (the prefix is not a module an rApp may change)."""
    world["sme_says"] = {"active": True, "client_id": "inv-1", "role": "rapp"}
    assert client.get(f"/rapps/{INSTANCE}/operator/instances/{INSTANCE}/cells", headers=AUTH).status_code == 200
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        resp = client.request(method, f"/rapps/{INSTANCE}/operator/x", headers=AUTH)
        assert resp.status_code == 403 and resp.json()["title"] == "ROLE_NOT_PERMITTED", method
    assert [c[0] for c in world["rapp_calls"]] == ["GET"]


def test_a_rapp_may_register_the_operator_api_of_its_own_instance_through_rapp_management(world):
    world["sme_says"] = {"active": True, "client_id": "inv-1", "role": "rapp"}
    assert client.put(f"/rapp-mgmt/instances/{INSTANCE}/operator-api", headers=AUTH, json={"operatorApiBase": "http://my-rapp:8000"}).status_code == 200
    assert client.delete(f"/rapp-mgmt/instances/{INSTANCE}/operator-api", headers=AUTH).status_code == 200


def test_no_token_is_a_401_and_nothing_is_looked_up(world):
    assert client.get(f"/rapps/{INSTANCE}/operator/x").status_code == 401
    assert world["lookups"] == 0


def test_a_change_through_the_prefix_is_audited_like_any_other(world, gateway_database):
    from sqlalchemy import text
    client.post(f"/rapps/{INSTANCE}/operator/instances/{INSTANCE}/evaluate", headers=AUTH)
    with gateway_database.connect() as conn:
        rows = conn.execute(text("SELECT actor, action, target, result FROM audit_log")).all()
    assert rows and rows[-1][1] == "POST" and rows[-1][2] == f"/rapps/{INSTANCE}/operator/instances/{INSTANCE}/evaluate"


def test_the_stopped_rapp_rule_does_not_apply_to_the_operators_gui(world):
    """The GUI is an internal caller acting for nobody: a stopped rApp's page can still be read and its buttons pressed (an operator may need to)."""
    assert client.post(f"/rapps/{INSTANCE}/operator/x", headers=AUTH).status_code == 200
