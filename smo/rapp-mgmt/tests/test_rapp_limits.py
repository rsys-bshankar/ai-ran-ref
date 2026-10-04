"""AI-10.1/10.2: the limits a package declares are put in force at RAN NF OAM when its instance finishes bootstrapping, and fail closed."""

import uuid

import httpx
import pytest

from test_main import FakeR1Response, client, db_session_factory  # noqa: F401  (pytest fixtures)

from app.models import RAppInstance


def _wire(monkeypatch, limits, put_outcome=200):
    """R1: Onboarding says the package declares `limits`; RAN NF OAM answers a limit push with `put_outcome` (a status, or an exception to raise)."""
    calls = {"put": [], "delete": []}

    def get(self, path, **kw):
        return FakeR1Response(200, {"state": "AVAILABLE", "nfDeploymentDescriptorId": str(uuid.uuid4()), "smeDeclarations": None,
                                    "aiCapabilities": {"limits": limits} if limits else None})

    def post(self, path, json=None, **kw):
        if "/nfo/deployments" in path:
            return FakeR1Response(200, {"nfDeploymentId": str(uuid.uuid4())})
        if "/usage/start" in path:
            return FakeR1Response(200, {"registrationId": str(uuid.uuid4())})
        if path == "/sme/invoker-registrations":
            return FakeR1Response(201, {"apiInvokerId": f"api-invoker-{uuid.uuid4()}", "onboardingSecret": "s", "role": "rapp"})
        return FakeR1Response(200, {"status": "stopped"})

    def put(self, path, json=None, **kw):
        calls["put"].append((path, json))
        if isinstance(put_outcome, Exception):
            raise put_outcome
        return FakeR1Response(put_outcome, {})

    def delete(self, path, **kw):
        calls["delete"].append(path)
        return FakeR1Response(204, {})

    for name, fn in (("get", get), ("post", post), ("put", put), ("delete", delete)):
        monkeypatch.setattr(f"app.main.R1Client.{name}", fn)
    return calls


def _create(client):
    return client.post("/instances", json={"packageId": str(uuid.uuid4()), "config": {}}).json()


def test_bootstrap_pushes_the_declared_limit_under_the_instance_client_id(client, monkeypatch):
    calls = _wire(monkeypatch, {"configJobsPerHour": 7})
    created = _create(client)
    resp = client.post(f"/instances/{created['instanceId']}/bootstrap-complete")
    assert resp.status_code == 200 and resp.json()["state"] == "RUNNING"
    assert calls["put"] == [(f"/ran-nf-oam/rapp-limits/{created['oauthClientId']}", {"maxConfigJobsPerHour": 7})]


def test_every_declared_limit_is_pushed(client, monkeypatch):
    calls = _wire(monkeypatch, {"configJobsPerHour": 7, "maxElementsPerJob": 3, "maxChangePercent": 12.5})
    created = _create(client)
    assert client.post(f"/instances/{created['instanceId']}/bootstrap-complete").status_code == 200
    assert calls["put"] == [(f"/ran-nf-oam/rapp-limits/{created['oauthClientId']}",
                             {"maxConfigJobsPerHour": 7, "maxElementsPerJob": 3, "maxChangePercent": 12.5})]


def test_only_the_declared_limits_are_pushed(client, monkeypatch):
    calls = _wire(monkeypatch, {"maxElementsPerJob": 2})
    created = _create(client)
    client.post(f"/instances/{created['instanceId']}/bootstrap-complete")
    assert calls["put"][0][1] == {"maxElementsPerJob": 2}


def test_a_package_without_limits_makes_no_call(client, monkeypatch):
    calls = _wire(monkeypatch, None)
    created = _create(client)
    assert client.post(f"/instances/{created['instanceId']}/bootstrap-complete").status_code == 200
    assert calls["put"] == []
    client.post(f"/instances/{created['instanceId']}/terminate")
    assert [p for p in calls["delete"] if "rapp-limits" in p] == []        # teardown has no limit to remove


@pytest.mark.parametrize("outcome", [500, 404, httpx.ConnectError("down")])
def test_a_limit_that_cannot_be_put_in_force_keeps_the_instance_from_running(client, db_session_factory, monkeypatch, outcome):
    _wire(monkeypatch, {"configJobsPerHour": 7}, put_outcome=outcome)
    created = _create(client)
    resp = client.post(f"/instances/{created['instanceId']}/bootstrap-complete")
    assert resp.status_code == 503
    assert client.get(f"/instances/{created['instanceId']}").json()["state"] == "DEPLOYING"


def test_bootstrap_can_be_retried_once_the_limit_can_be_pushed(client, monkeypatch):
    _wire(monkeypatch, {"configJobsPerHour": 7}, put_outcome=503)
    created = _create(client)
    assert client.post(f"/instances/{created['instanceId']}/bootstrap-complete").status_code == 503
    _wire(monkeypatch, {"configJobsPerHour": 7})
    assert client.post(f"/instances/{created['instanceId']}/bootstrap-complete").json()["state"] == "RUNNING"


def test_terminate_removes_the_limit(client, monkeypatch):
    calls = _wire(monkeypatch, {"configJobsPerHour": 7})
    created = _create(client)
    client.post(f"/instances/{created['instanceId']}/bootstrap-complete")
    assert client.post(f"/instances/{created['instanceId']}/terminate").status_code == 200
    assert calls["delete"].count(f"/ran-nf-oam/rapp-limits/{created['oauthClientId']}") == 1


# ---- PR-SEC-14: the instance's own invoker identity at SME

def _wire_invokers(monkeypatch):
    seen = {"post": [], "delete": []}

    def post(self, path, json=None, **kw):
        seen["post"].append((path, json))
        if path == "/sme/invoker-registrations":
            return FakeR1Response(201, {"apiInvokerId": f"api-invoker-{len(seen['post'])}", "onboardingSecret": f"secret-{len(seen['post'])}", "role": "rapp"})
        if "/nfo/deployments" in path:
            return FakeR1Response(200, {"nfDeploymentId": str(uuid.uuid4())})
        if "/usage/start" in path:
            return FakeR1Response(200, {"registrationId": str(uuid.uuid4())})
        return FakeR1Response(200, {})

    monkeypatch.setattr("app.main.R1Client.get", lambda self, path, **kw: FakeR1Response(200, {"state": "AVAILABLE", "nfDeploymentDescriptorId": str(uuid.uuid4())}))
    monkeypatch.setattr("app.main.R1Client.post", post)
    monkeypatch.setattr("app.main.R1Client.delete", lambda self, path, **kw: (seen["delete"].append(path), FakeR1Response(204, {}))[1])
    return seen


def test_an_instance_gets_its_own_sme_invoker_and_that_is_its_client_id(client, monkeypatch):
    seen = _wire_invokers(monkeypatch)
    created = _create(client)
    registration = next(p for p in seen["post"] if p[0] == "/sme/invoker-registrations")
    assert registration[1] == {"apiInvokerPublicKey": f"rapp-instance:{created['instanceId']}"}      # no enrollment header: SME records an rApp
    assert created["oauthClientId"].startswith("api-invoker-")
    assert "oauthClientSecret" not in created                                                          # an idempotent answer is stored: no secret in it


def test_creating_an_instance_fails_if_sme_will_not_register_its_identity(client, monkeypatch):
    seen = _wire_invokers(monkeypatch)
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: FakeR1Response(503, {}) if path == "/sme/invoker-registrations"
                        else FakeR1Response(200, {"nfDeploymentId": "x"}))
    assert client.post("/instances", json={"packageId": str(uuid.uuid4()), "config": {}}).status_code == 503
    assert seen["delete"] == []


def test_credentials_are_issued_once_replace_the_invoker_and_are_not_cached(client, db_session_factory, monkeypatch):
    seen = _wire_invokers(monkeypatch)
    created = _create(client)
    first = client.post(f"/instances/{created['instanceId']}/credentials")
    assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
    body = first.json()
    assert body["oauthClientId"] != created["oauthClientId"] and body["oauthClientSecret"].startswith("secret-")
    assert f"/sme/invoker-registrations/{created['oauthClientId']}" in seen["delete"]                   # the one made at create is deregistered
    with db_session_factory() as session:
        assert session.get(RAppInstance, uuid.UUID(created["instanceId"])).oauth_client_id == body["oauthClientId"]
    second = client.post(f"/instances/{created['instanceId']}/credentials").json()                      # rotate
    assert second["oauthClientId"] not in (created["oauthClientId"], body["oauthClientId"])
    assert f"/sme/invoker-registrations/{body['oauthClientId']}" in seen["delete"]


def test_credentials_are_only_issued_while_the_instance_is_deploying(client, monkeypatch):
    _wire_invokers(monkeypatch)
    created = _create(client)
    client.post(f"/instances/{created['instanceId']}/bootstrap-complete")
    assert client.post(f"/instances/{created['instanceId']}/credentials").status_code == 409
    assert client.post(f"/instances/{uuid.uuid4()}/credentials").status_code == 404


# ---- AI-10.4: the kill switch as an operator action on an instance

def _wire_kill(monkeypatch, outcome=200):
    seen = _wire_invokers(monkeypatch)
    seen["put"] = []

    def put(self, path, json=None, **kw):
        seen["put"].append((path, json))
        if isinstance(outcome, Exception):
            raise outcome
        return FakeR1Response(outcome, {"invokerId": "x", "killedBy": json["requestedBy"], "reason": json["reason"], "killedAt": "2026-10-04T00:00:00Z"})

    monkeypatch.setattr("app.main.R1Client.put", put)
    return seen


def test_killing_an_instance_stops_its_invoker_at_ran_nf_oam(client, monkeypatch):
    seen = _wire_kill(monkeypatch)
    created = _create(client)
    resp = client.put(f"/instances/{created['instanceId']}/kill", json={"requestedBy": "alice", "reason": "oscillating"})
    assert resp.status_code == 200 and resp.json()["killed"] is True and resp.json()["killedBy"] == "alice"
    assert seen["put"] == [(f"/ran-nf-oam/rapp-kill/{created['oauthClientId']}", {"requestedBy": "alice", "reason": "oscillating"})]


@pytest.mark.parametrize("outcome", [500, httpx.ConnectError("down")])
def test_a_switch_that_could_not_be_thrown_is_reported_not_done(client, monkeypatch, outcome):
    _wire_kill(monkeypatch, outcome)
    created = _create(client)
    resp = client.put(f"/instances/{created['instanceId']}/kill", json={"requestedBy": "alice"})
    assert resp.status_code == 503 and "nothing was changed" in resp.json()["detail"]["detail"]


def test_lifting_is_idempotent_and_goes_to_the_same_key(client, monkeypatch):
    seen = _wire_kill(monkeypatch)
    created = _create(client)
    seen["delete"].clear()
    resp = client.delete(f"/instances/{created['instanceId']}/kill")
    assert resp.status_code == 200 and resp.json()["killed"] is False
    assert seen["delete"] == [f"/ran-nf-oam/rapp-kill/{created['oauthClientId']}"]


def test_an_unknown_instance_is_404_and_a_terminated_one_has_nothing_to_kill(client, monkeypatch):
    _wire_kill(monkeypatch)
    assert client.put(f"/instances/{uuid.uuid4()}/kill", json={"requestedBy": "a"}).status_code == 404
    created = _create(client)
    client.post(f"/instances/{created['instanceId']}/bootstrap-complete")
    client.post(f"/instances/{created['instanceId']}/terminate")
    assert client.put(f"/instances/{created['instanceId']}/kill", json={"requestedBy": "a"}).status_code == 404
