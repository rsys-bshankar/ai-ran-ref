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
