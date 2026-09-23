"""Tests for the OnboardPackage route (Onboarding/rApp Mgmt LLD sections
1-2), covering NFO's CreateDescriptor wiring — the actual fix for the
NFDeploymentDescriptor gap OPEN_ITEMS.md flagged as the top item.
Run with: pytest smo/onboarding/tests -q
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Column, Table, Uuid, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from smo_shared.db import Base, get_session

from app.main import app
from app.models import ApplicationPackage, Artifact, PackageUsageRegistration


class FakeR1Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def db_session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    # nf_deployment_descriptor lives in the nfo module — stand in a minimal
    # table so ApplicationPackage's FK resolves, same pattern as nfo/tests'
    # application_package stub.
    if "nf_deployment_descriptor" not in Base.metadata.tables:
        Table("nf_deployment_descriptor", Base.metadata, Column("nf_deployment_descriptor_id", Uuid, primary_key=True))
    Base.metadata.create_all(engine, tables=[ApplicationPackage.__table__, Artifact.__table__, PackageUsageRegistration.__table__])
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


def test_onboard_success_creates_nf_deployment_descriptor_via_nfo(client, monkeypatch):
    """The actual fix: successful validation now calls NFO's
    CreateDescriptor and stores the real nfDeploymentDescriptorId on the
    package, instead of leaving rApp Management to pass packageId where
    NFO expects a genuine descriptor.
    """
    monkeypatch.setattr("app.main._validate_package", lambda location: ("Definitions/main.yaml", [], "deadbeef"))
    descriptor_id = uuid.uuid4()
    monkeypatch.setattr(
        "app.main.R1Client.post",
        lambda self, path, json=None, **kw: FakeR1Response(201, {"nfDeploymentDescriptorId": str(descriptor_id)}),
    )

    resp = client.post("/packages", json={"location": "http://example/pkg.csar"})
    package_id = resp.json()["packageId"]

    status = client.get(f"/packages/{package_id}/onboarding-status")
    assert status.json()["state"] == "AVAILABLE"
    assert status.json()["nfDeploymentDescriptorId"] == str(descriptor_id)


def test_onboard_routes_to_failed_when_nfo_descriptor_creation_fails(client, monkeypatch):
    """DescriptorCreationFailed folds into the same VALIDATE_FAILED path as
    a malformed zip or an unreachable location — NFO being unavailable at
    onboarding time is a real, expected failure mode, not a crash.
    """
    monkeypatch.setattr("app.main._validate_package", lambda location: ("Definitions/main.yaml", [], "deadbeef"))
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: FakeR1Response(503, {}))

    resp = client.post("/packages", json={"location": "http://example/pkg.csar"})
    package_id = resp.json()["packageId"]

    status = client.get(f"/packages/{package_id}/onboarding-status")
    assert status.json()["state"] == "FAILED"
    assert status.json()["nfDeploymentDescriptorId"] is None


def test_onboard_routes_to_failed_on_a_real_malformed_zip(client, monkeypatch):
    """_validate_package itself was never exercised before this pass —
    every prior test mocked it away entirely. This drives the real
    validation code against genuinely malformed zip bytes, only mocking
    the network fetch underneath it.
    """
    class FakeHttpResponse:
        content = b"not a real zip file"
        def raise_for_status(self):
            pass

    monkeypatch.setattr("app.main.httpx.get", lambda location, timeout=None: FakeHttpResponse())

    resp = client.post("/packages", json={"location": "http://example/pkg.csar"})
    package_id = resp.json()["packageId"]

    status = client.get(f"/packages/{package_id}/onboarding-status")
    assert status.json()["state"] == "FAILED"


def test_onboarding_status_for_unknown_package_reuses_dme_type_version_conflict(client):
    """query_onboarding_status's own comment calls this a "404-shaped
    reuse", but DME_TYPE_VERSION_CONFLICT is actually a 409
    (smo_shared/errors.py) — asserting the real status code, not the
    comment's description of it.
    """
    resp = client.get(f"/packages/{uuid.uuid4()}/onboarding-status")
    assert resp.status_code == 409
    assert resp.json()["detail"]["title"] == "DME_TYPE_VERSION_CONFLICT"


def _make_available_package(client, monkeypatch) -> str:
    monkeypatch.setattr("app.main._validate_package", lambda location: ("Definitions/main.yaml", [], "deadbeef"))
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: FakeR1Response(201, {"nfDeploymentDescriptorId": str(uuid.uuid4())}))
    return client.post("/packages", json={"location": "http://example/pkg.csar"}).json()["packageId"]


def test_query_packages_lists_and_filters_by_state(client, monkeypatch):
    available_id = _make_available_package(client, monkeypatch)
    monkeypatch.setattr("app.main._validate_package", lambda location: (_ for _ in ()).throw(KeyError("Definitions/missing.yaml")))
    client.post("/packages", json={"location": "http://example/other.csar"})  # routes to FAILED

    all_packages = client.get("/packages").json()
    assert len(all_packages) == 2

    available_only = client.get("/packages", params={"state": "AVAILABLE"}).json()
    assert [p["packageId"] for p in available_only] == [available_id]


def test_deprecate_then_cancel_delete_round_trip(client, monkeypatch):
    """DEPRECATE (AVAILABLE -> DEPRECATED) and CANCEL_DELETE (DEPRECATED ->
    AVAILABLE) had no test coverage at all before this pass.
    """
    package_id = _make_available_package(client, monkeypatch)

    deprecated = client.post(f"/packages/{package_id}/deprecate")
    assert deprecated.status_code == 200
    assert deprecated.json()["state"] == "DEPRECATED"

    restored = client.post(f"/packages/{package_id}/cancel-delete")
    assert restored.status_code == 200
    assert restored.json()["state"] == "AVAILABLE"


def test_delete_failed_package_skips_cascade_check(client, monkeypatch):
    """The module's own docstring calls this out as a deliberate shortcut:
    a FAILED package never reached AVAILABLE, so nothing could depend on
    it — DELETE must not even run the cascade query. Never tested before
    this pass despite being explicitly documented behavior.
    """
    monkeypatch.setattr("app.main._validate_package", lambda location: (_ for _ in ()).throw(KeyError("boom")))
    package_id = client.post("/packages", json={"location": "http://example/pkg.csar"}).json()["packageId"]

    resp = client.delete(f"/packages/{package_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"


def test_delete_available_package_with_no_dependents_succeeds(client, monkeypatch):
    package_id = _make_available_package(client, monkeypatch)
    resp = client.delete(f"/packages/{package_id}")
    assert resp.status_code == 200
    assert resp.json()["state"] == "DELETING"


def test_delete_blocked_by_dependent_child_package(client, db_session_factory, monkeypatch):
    """Onboarding/rApp Mgmt LLD section 4's cascade-delete guard, one half:
    an AVAILABLE/DEPRECATED child package blocks its parent's deletion.
    """
    parent_id = uuid.UUID(_make_available_package(client, monkeypatch))
    child_id = uuid.UUID(_make_available_package(client, monkeypatch))

    with db_session_factory() as session:
        child = session.get(ApplicationPackage, child_id)
        child.parent_package_id = parent_id
        session.commit()

    resp = client.delete(f"/packages/{parent_id}")
    assert resp.status_code == 409
    assert resp.json()["detail"]["title"] == "SERVICE_NAME_CONFLICT"


def test_delete_blocked_by_active_usage_registration(client, monkeypatch):
    """The cascade-delete guard's other half: a usage registration with no
    stopped_at blocks deletion — the actual condition
    TerminateInstance's usage/stop call (rapp-mgmt) exists to clear.
    """
    package_id = _make_available_package(client, monkeypatch)
    client.post(f"/packages/{package_id}/usage/start", params={"consumer_id": "instance-1"})

    resp = client.delete(f"/packages/{package_id}")
    assert resp.status_code == 409
    assert resp.json()["detail"]["title"] == "SERVICE_NAME_CONFLICT"


def test_delete_succeeds_once_usage_registration_is_stopped(client, monkeypatch):
    package_id = _make_available_package(client, monkeypatch)
    reg = client.post(f"/packages/{package_id}/usage/start", params={"consumer_id": "instance-1"}).json()

    stop_resp = client.post(f"/packages/{package_id}/usage/{reg['registrationId']}/stop")
    assert stop_resp.status_code == 200

    resp = client.delete(f"/packages/{package_id}")
    assert resp.status_code == 200
    assert resp.json()["state"] == "DELETING"
