"""The operator API base an instance registers (PR-GUI-8, GUI-8.3; docs/adr/0004-operator-ui-declaration.md, 4)."""

import uuid

import pytest

from test_main import _route_r1_get_post, client, db_session_factory, fake_r1_delete  # noqa: F401  (fixtures)
from app.models import RAppInstance


def _create(client, monkeypatch, **extra) -> dict:
    """Creates an instance through the route with R1 stubbed (`extra` adds request fields such as `operatorApiBase`) and returns the answer.
    """
    fake_get, fake_post = _route_r1_get_post()
    monkeypatch.setattr("app.main.R1Client.get", fake_get)
    monkeypatch.setattr("app.main.R1Client.post", fake_post)
    resp = client.post("/instances", json={"packageId": str(uuid.uuid4()), **extra})
    assert resp.status_code == 202, resp.text
    return resp.json()


def test_a_new_instance_has_no_operator_api(client, monkeypatch):
    """A new instance has no operator API registered, in its detail, its operator-api read and the list."""
    created = _create(client, monkeypatch)
    assert client.get(f"/instances/{created['instanceId']}").json()["operatorApiBase"] is None
    assert client.get(f"/instances/{created['instanceId']}/operator-api").json() == {
        "instanceId": created["instanceId"], "state": "DEPLOYING", "operatorApiBase": None}
    assert client.get("/instances").json()["items"][0]["operatorApiBase"] is None


def test_an_operator_may_give_the_base_at_create_and_it_is_stored_without_a_trailing_slash(client, db_session_factory, monkeypatch):
    """A base given at create is stored without the trailing slash and shown on the instance."""
    created = _create(client, monkeypatch, operatorApiBase="http://energy-saving-rapp:8000/")
    with db_session_factory() as session:
        assert session.get(RAppInstance, uuid.UUID(created["instanceId"])).operator_api_base == "http://energy-saving-rapp:8000"
    assert client.get(f"/instances/{created['instanceId']}").json()["operatorApiBase"] == "http://energy-saving-rapp:8000"


def test_create_refuses_an_unsafe_base_and_creates_nothing(client, db_session_factory, monkeypatch):
    """A metadata-address base at create is 422 OPERATOR_API_BASE_INVALID and no instance is created."""
    fake_get, fake_post = _route_r1_get_post()
    monkeypatch.setattr("app.main.R1Client.get", fake_get)
    monkeypatch.setattr("app.main.R1Client.post", fake_post)
    resp = client.post("/instances", json={"packageId": str(uuid.uuid4()), "operatorApiBase": "http://169.254.169.254/latest"})
    assert resp.status_code == 422 and resp.json()["detail"]["title"] == "OPERATOR_API_BASE_INVALID"
    assert client.get("/instances").json()["items"] == []


# Each row is a base that must be refused with 422 OPERATOR_API_BASE_INVALID: loopback, link-local and metadata addresses, other schemes, credentials, query, fragment, traversal or empty segments, no scheme, surrounding space, over-long values and port 0. Nothing is stored.
@pytest.mark.parametrize("value", [
    "http://127.0.0.1:8000", "http://localhost:8000", "http://169.254.169.254", "http://[::1]:8000", "file:///etc/passwd", "ftp://rapp/x",
    "http://user:pw@rapp:8000", "http://rapp:8000/x?y=1", "http://rapp:8000/#frag", "http://rapp:8000/../x", "http://rapp:8000//x",
    "http://rapp:8000/%2e%2e", "rapp:8000", "", " http://rapp:8000", "http://rapp:8000/" + "a" * 300, "http://rapp:0"])
def test_the_base_must_be_a_safe_http_origin(client, monkeypatch, value):
    created = _create(client, monkeypatch)
    resp = client.put(f"/instances/{created['instanceId']}/operator-api", json={"operatorApiBase": value})
    assert resp.status_code == 422, value
    assert resp.json()["detail"]["title"] == "OPERATOR_API_BASE_INVALID"
    assert client.get(f"/instances/{created['instanceId']}/operator-api").json()["operatorApiBase"] is None


def test_an_operator_registers_replaces_and_clears_the_base(client, monkeypatch):
    """An operator can register, replace and clear the base (clearing twice is fine), and a trailing slash is dropped."""
    created = _create(client, monkeypatch)
    url = f"/instances/{created['instanceId']}/operator-api"
    assert client.put(url, json={"operatorApiBase": "https://rapp.example:8443/api/"}).json() == {
        "instanceId": created["instanceId"], "operatorApiBase": "https://rapp.example:8443/api"}
    assert client.put(url, json={"operatorApiBase": "http://rapp-2:8000"}).json()["operatorApiBase"] == "http://rapp-2:8000"
    assert client.get(url).json()["operatorApiBase"] == "http://rapp-2:8000"
    assert client.delete(url).status_code == 204 and client.delete(url).status_code == 204     # idempotent
    assert client.get(url).json()["operatorApiBase"] is None


def test_unknown_instance_is_404_on_every_route(client):
    """GET, PUT and DELETE of the operator-api of an unknown instance are 404."""
    url = f"/instances/{uuid.uuid4()}/operator-api"
    assert client.get(url).status_code == 404
    assert client.put(url, json={"operatorApiBase": "http://rapp:8000"}).status_code == 404
    assert client.delete(url).status_code == 404


def test_a_rapp_may_register_for_itself_only(client, monkeypatch):
    """A caller with the rApp role may register only for the instance whose credential it holds, while an internal caller may set any instance's.
    """
    mine = _create(client, monkeypatch)
    other = _create(client, monkeypatch)
    own_header = {"X-R1-Role": "rapp", "X-R1-Invoker-Id": mine["oauthClientId"]}
    body = {"operatorApiBase": "http://my-rapp:8000"}
    assert client.put(f"/instances/{mine['instanceId']}/operator-api", json=body, headers=own_header).status_code == 200
    # another rApp's instance, a missing invoker id, and an operator-looking header from a rApp are all refused
    assert client.put(f"/instances/{other['instanceId']}/operator-api", json=body, headers=own_header).status_code == 403
    assert client.put(f"/instances/{mine['instanceId']}/operator-api", json=body, headers={"X-R1-Role": "rapp"}).status_code == 403
    assert client.delete(f"/instances/{other['instanceId']}/operator-api", headers=own_header).status_code == 403
    assert client.get(f"/instances/{other['instanceId']}/operator-api").json()["operatorApiBase"] is None
    # an internal caller (the operator's GUI through the gateway) may set any instance's
    internal = {"X-R1-Role": "internal", "X-R1-Invoker-Id": "smo-gui"}
    assert client.put(f"/instances/{other['instanceId']}/operator-api", json=body, headers=internal).status_code == 200


def test_a_terminated_instance_has_nothing_to_serve(client, monkeypatch):
    """After terminate the operator-api read shows null and a new registration is 409."""
    created = _create(client, monkeypatch)
    url = f"/instances/{created['instanceId']}/operator-api"
    client.put(url, json={"operatorApiBase": "http://rapp:8000"})
    assert client.post(f"/instances/{created['instanceId']}/terminate").status_code == 200
    assert client.get(url).json() == {"instanceId": created["instanceId"], "state": "UNDEPLOYED", "operatorApiBase": None}
    assert client.put(url, json={"operatorApiBase": "http://rapp:8000"}).status_code == 409
