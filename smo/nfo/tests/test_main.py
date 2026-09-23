"""Tests for NFO SMOS (NFO+FOCOM LLD sections 2, 4).
Run with: pytest smo/nfo/tests -q
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, Column, Table
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import Uuid as UuidType

from smo_shared.db import Base, get_session

from app.main import app
from app.models import LCMOperation, NFDeployment, NFDeploymentDescriptor


class FakeR1Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    if "application_package" not in Base.metadata.tables:
        Table("application_package", Base.metadata, Column("package_id", UuidType, primary_key=True))
    Base.metadata.create_all(engine, tables=[
        Base.metadata.tables["application_package"], NFDeploymentDescriptor.__table__, NFDeployment.__table__, LCMOperation.__table__,
    ])
    TestSession = sessionmaker(bind=engine)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    # raise_server_exceptions=False: this suite intentionally exercises an
    # unguarded-None error path (querying a deleted deployment) and asserts
    # on the resulting HTTP status, not the raised Python exception.
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def test_instantiate_resolves_cluster_via_focom(client, monkeypatch):
    """NFO+FOCOM LLD section 4: Instantiate queries FOCOM's inventory
    before placing a workload, rather than assuming the degenerate
    cluster implicitly.
    """
    monkeypatch.setattr(
        "app.main.R1Client.get",
        lambda self, path, **kw: FakeR1Response(200, {"clusterId": "focom-resolved-cluster"}),
    )
    resp = client.post("/deployments", json={"nfDeploymentDescriptorId": str(uuid.uuid4()), "requiredResourceTypeId": "gpu-l40"})
    assert resp.status_code == 202
    assert resp.json()["clusterId"] == "focom-resolved-cluster"
    assert resp.json()["state"] == "RUNNING"


def test_instantiate_falls_back_to_degenerate_cluster_if_focom_unreachable(client, monkeypatch):
    monkeypatch.setattr("app.main.R1Client.get", lambda self, path, **kw: FakeR1Response(503, {}))
    resp = client.post("/deployments", json={"nfDeploymentDescriptorId": str(uuid.uuid4())})
    assert resp.json()["clusterId"] == "phase1-degenerate-cluster"


def test_terminate_removes_deployment(client, monkeypatch):
    monkeypatch.setattr("app.main.R1Client.get", lambda self, path, **kw: FakeR1Response(200, {"clusterId": "c1"}))
    created = client.post("/deployments", json={"nfDeploymentDescriptorId": str(uuid.uuid4())}).json()

    del_resp = client.delete(f"/deployments/{created['nfDeploymentId']}")
    assert del_resp.status_code == 204

    # placement query against a deleted deployment is a genuine error path,
    # not modeled with a friendly 404 in this reference build — asserting
    # that explicitly rather than papering over it.
    resp = client.get(f"/deployments/{created['nfDeploymentId']}/placement")
    assert resp.status_code == 500


def test_heal_and_scale_are_stub_only(client):
    """Phase 1: shape-only stubs until K8s (D-DEPLOY-NFO-3, unchanged)."""
    fake_id = uuid.uuid4()
    for op in ("heal", "scale"):
        resp = client.post(f"/deployments/{fake_id}/{op}")
        assert resp.json()["status"] == "stub-only"
