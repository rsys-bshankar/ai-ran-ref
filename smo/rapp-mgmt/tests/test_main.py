"""Tests for rApp Management's routes (Onboarding/rApp Mgmt LLD section 5-6)
— CreateInstance's usage-registration wiring, TerminateInstance's
usage/stop call, and the RECOVER route, none of which had route-level
coverage before (only the FSM itself, in test_upgrade.py).
Run with: pytest smo/rapp-mgmt/tests -q
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Column, Table, Uuid as UuidType, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from smo_shared.db import Base, get_session

from app.main import app
from app.models import RAppFaultReport, RAppInstance, RAppPerformanceReport
from app.statemachine import InstanceState


class FakeR1Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def db_session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    # application_package and package_usage_registration live in the onboarding
    # module, out of scope for this test package — stand in minimal tables so
    # RAppInstance's FKs resolve, same pattern as rapp-mgmt/tests/test_upgrade.py.
    if "application_package" not in Base.metadata.tables:
        Table("application_package", Base.metadata, Column("package_id", UuidType, primary_key=True))
    if "package_usage_registration" not in Base.metadata.tables:
        Table("package_usage_registration", Base.metadata, Column("id", UuidType, primary_key=True))
    Base.metadata.create_all(engine, tables=[
        Base.metadata.tables["application_package"], Base.metadata.tables["package_usage_registration"],
        RAppInstance.__table__, RAppFaultReport.__table__, RAppPerformanceReport.__table__,
    ])
    return sessionmaker(bind=engine)


@pytest.fixture
def client(db_session_factory):
    def override_get_session():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _route_r1_get_post(*, onboarding_status="AVAILABLE", registration_id=None):
    """Builds a fake R1Client.get/post pair that answers each of
    CreateInstance's three downstream calls (onboarding-status, NFO
    deploy, usage/start) based on the path, since they all go through
    the same R1Client instance. onboarding-status includes a
    nfDeploymentDescriptorId — CreateInstance now requires it (the
    NFDeploymentDescriptor fix), so a fake response without it would
    incorrectly 409 before ever reaching the usage-registration wiring
    this suite actually tests.
    """
    reg_id = registration_id or uuid.uuid4()

    def fake_get(self, path, **kw):
        assert "/onboarding-status" in path
        return FakeR1Response(200, {"state": onboarding_status, "nfDeploymentDescriptorId": str(uuid.uuid4())})

    def fake_post(self, path, json=None, **kw):
        if "/nfo/deployments" in path:
            return FakeR1Response(200, {"nfDeploymentId": str(uuid.uuid4())})
        if "/usage/start" in path:
            return FakeR1Response(200, {"registrationId": str(reg_id)})
        if "/usage/" in path and path.endswith("/stop"):
            return FakeR1Response(200, {"status": "stopped"})
        raise AssertionError(f"unexpected R1 POST to {path}")

    return fake_get, fake_post


def test_create_instance_registers_package_usage(client, db_session_factory, monkeypatch):
    """The actual fix: CreateInstance now calls Onboarding's usage/start
    and stores the real registrationId — previously nothing called it at
    all, so the cascade-delete guard's active-usage condition could never
    fire from ordinary rApp deployment.
    """
    reg_id = uuid.uuid4()
    fake_get, fake_post = _route_r1_get_post(registration_id=reg_id)
    monkeypatch.setattr("app.main.R1Client.get", fake_get)
    monkeypatch.setattr("app.main.R1Client.post", fake_post)

    resp = client.post("/instances", json={"packageId": str(uuid.uuid4()), "config": {}})
    assert resp.status_code == 202
    instance_id = uuid.UUID(resp.json()["instanceId"])

    with db_session_factory() as session:
        inst = session.get(RAppInstance, instance_id)
        assert inst.package_usage_registration_id == reg_id


def test_terminate_instance_calls_usage_stop(client, monkeypatch):
    """The other half of the fix: TerminateInstance must stop the usage
    registration CreateInstance started, or the guard sees permanently
    active usage even after the instance is long gone.
    """
    reg_id = uuid.uuid4()
    fake_get, fake_post = _route_r1_get_post(registration_id=reg_id)
    calls = []

    def recording_post(self, path, json=None, **kw):
        calls.append(path)
        return fake_post(self, path, json=json, **kw)

    monkeypatch.setattr("app.main.R1Client.get", fake_get)
    monkeypatch.setattr("app.main.R1Client.post", recording_post)

    created = client.post("/instances", json={"packageId": str(uuid.uuid4()), "config": {}}).json()
    client.post(f"/instances/{created['instanceId']}/bootstrap-complete")  # DEPLOYING -> RUNNING; TERMINATE needs RUNNING
    calls.clear()

    resp = client.post(f"/instances/{created['instanceId']}/terminate")
    assert resp.status_code == 200
    assert any(f"/usage/{reg_id}/stop" in c for c in calls)


def test_terminate_instance_skips_usage_stop_when_never_registered(client, monkeypatch):
    """CreateInstance's usage/start call can fail without failing the
    whole request (best-effort); TerminateInstance must not crash trying
    to stop a registration that was never recorded.
    """
    def fake_get(self, path, **kw):
        return FakeR1Response(200, {"state": "AVAILABLE", "nfDeploymentDescriptorId": str(uuid.uuid4())})

    def fake_post(self, path, json=None, **kw):
        if "/nfo/deployments" in path:
            return FakeR1Response(200, {"nfDeploymentId": str(uuid.uuid4())})
        if "/usage/start" in path:
            return FakeR1Response(503, {})  # onboarding unreachable
        raise AssertionError(f"unexpected R1 POST to {path}")

    monkeypatch.setattr("app.main.R1Client.get", fake_get)
    monkeypatch.setattr("app.main.R1Client.post", fake_post)

    created = client.post("/instances", json={"packageId": str(uuid.uuid4()), "config": {}}).json()
    client.post(f"/instances/{created['instanceId']}/bootstrap-complete")  # DEPLOYING -> RUNNING; TERMINATE needs RUNNING
    resp = client.post(f"/instances/{created['instanceId']}/terminate")
    assert resp.status_code == 200


def test_terminate_instance_deregisters_dme_producer(client, monkeypatch):
    """rApp-as-producer reconsideration trigger (OPEN_ITEMS.md section 1):
    TERMINATE must reach DME too, deregistering every DMEType this
    instance's own oauth_client_id (== its DME producerId) registered —
    not just revoke the local credential.
    """
    fake_get, fake_post = _route_r1_get_post()
    calls = []

    def recording_delete(self, path, params=None, **kw):
        calls.append((path, params))
        return FakeR1Response(204, {})

    monkeypatch.setattr("app.main.R1Client.get", fake_get)
    monkeypatch.setattr("app.main.R1Client.post", fake_post)
    monkeypatch.setattr("app.statemachine.R1Client.delete", recording_delete)

    created = client.post("/instances", json={"packageId": str(uuid.uuid4()), "config": {}}).json()
    client.post(f"/instances/{created['instanceId']}/bootstrap-complete")

    resp = client.post(f"/instances/{created['instanceId']}/terminate")
    assert resp.status_code == 200
    assert len(calls) == 1
    path, params = calls[0]
    assert path == "/dme/production-capabilities"
    assert params == {"producer_id": created["oauthClientId"]}


def test_crash_via_critical_fault_deregisters_dme_producer(client, monkeypatch):
    fake_get, fake_post = _route_r1_get_post()
    calls = []
    monkeypatch.setattr("app.main.R1Client.get", fake_get)
    monkeypatch.setattr("app.main.R1Client.post", fake_post)
    monkeypatch.setattr("app.statemachine.R1Client.delete", lambda self, path, params=None, **kw: calls.append(params))

    created = client.post("/instances", json={"packageId": str(uuid.uuid4()), "config": {}}).json()
    client.post(f"/instances/{created['instanceId']}/bootstrap-complete")

    resp = client.post(f"/instances/{created['instanceId']}/fault", params={"severity": "critical"})
    assert resp.status_code == 200
    assert resp.json()["instanceState"] == "FAULTED"
    assert calls == [{"producer_id": created["oauthClientId"]}]


def test_terminate_instance_survives_unreachable_dme(client, monkeypatch):
    """Best-effort — a DME outage must never block TERMINATE itself, same
    "unreachable callback never fails the primary operation" precedent
    Policy Mgmt's CreateIntent dispatch uses.
    """
    import httpx as httpx_module

    fake_get, fake_post = _route_r1_get_post()

    def raise_error(self, path, params=None, **kw):
        raise httpx_module.ConnectError("dme unreachable")

    monkeypatch.setattr("app.main.R1Client.get", fake_get)
    monkeypatch.setattr("app.main.R1Client.post", fake_post)
    monkeypatch.setattr("app.statemachine.R1Client.delete", raise_error)

    created = client.post("/instances", json={"packageId": str(uuid.uuid4()), "config": {}}).json()
    client.post(f"/instances/{created['instanceId']}/bootstrap-complete")

    resp = client.post(f"/instances/{created['instanceId']}/terminate")
    assert resp.status_code == 200


def test_recover_route_fires_recover_transition(client, db_session_factory):
    """RECOVER — previously unreachable via any route at all (the FSM
    transition existed, nothing called it).
    """
    inst_id = uuid.uuid4()
    with db_session_factory() as session:
        session.add(RAppInstance(instance_id=inst_id, package_id=uuid.uuid4(), state=InstanceState.FAULTED, oauth_client_id=None))
        session.commit()

    resp = client.post(f"/instances/{inst_id}/recover")
    assert resp.status_code == 200
    assert resp.json()["state"] == "DEPLOYING"
