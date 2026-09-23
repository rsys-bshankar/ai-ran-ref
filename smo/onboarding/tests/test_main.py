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
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    # nf_deployment_descriptor lives in the nfo module — stand in a minimal
    # table so ApplicationPackage's FK resolves, same pattern as nfo/tests'
    # application_package stub.
    if "nf_deployment_descriptor" not in Base.metadata.tables:
        Table("nf_deployment_descriptor", Base.metadata, Column("nf_deployment_descriptor_id", Uuid, primary_key=True))
    Base.metadata.create_all(engine, tables=[ApplicationPackage.__table__, Artifact.__table__, PackageUsageRegistration.__table__])
    TestSession = sessionmaker(bind=engine)

    def override_get_session():
        session = TestSession()
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
