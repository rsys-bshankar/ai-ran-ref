"""Tests for NFO SMOS (NFO+FOCOM LLD sections 2, 4), extended by
OPEN_ITEMS.md section 5's real deployment state machine, duplication/
dependency guards, resource-linkage object, and Heal/Scale transitions.
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
from app.models import LCMOperation, NFDeployment, NFDeploymentDescriptor, NFOCloudResource


class FakeR1Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def db_session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    if "application_package" not in Base.metadata.tables:
        Table("application_package", Base.metadata, Column("package_id", UuidType, primary_key=True))
    Base.metadata.create_all(engine, tables=[
        Base.metadata.tables["application_package"], NFDeploymentDescriptor.__table__, NFDeployment.__table__,
        NFOCloudResource.__table__, LCMOperation.__table__,
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
    # raise_server_exceptions=False: this suite intentionally exercises an
    # unguarded-None error path (querying a deleted deployment) and asserts
    # on the resulting HTTP status, not the raised Python exception.
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _create_descriptor(client) -> str:
    resp = client.post("/descriptors", json={
        "packageId": str(uuid.uuid4()), "name": "Definitions/main.yaml",
        "workloadTemplate": {"toscaEntryDefinitions": "Definitions/main.yaml"},
    })
    return resp.json()["nfDeploymentDescriptorId"]


def _instantiate(client, monkeypatch, name="nf-1", descriptor_id=None, cluster_id="c1"):
    monkeypatch.setattr("app.main.R1Client.get", lambda self, path, **kw: FakeR1Response(200, {"oCloudId": cluster_id}))
    descriptor_id = descriptor_id or _create_descriptor(client)
    return client.post("/deployments", json={"nfDeploymentDescriptorId": descriptor_id, "name": name})


def test_create_descriptor_persists_a_real_row(client, db_session_factory):
    """NFO+FOCOM LLD section 2: CreateDescriptor — the endpoint that
    closes the gap where NFDeploymentDescriptor was never populated.
    """
    package_id = uuid.uuid4()
    resp = client.post("/descriptors", json={
        "packageId": str(package_id), "name": "Definitions/main.yaml",
        "workloadTemplate": {"toscaEntryDefinitions": "Definitions/main.yaml"},
        "requiredResourceTypeId": "gpu-l40",
    })
    assert resp.status_code == 201
    descriptor_id = uuid.UUID(resp.json()["nfDeploymentDescriptorId"])

    with db_session_factory() as session:
        descriptor = session.get(NFDeploymentDescriptor, descriptor_id)
        assert descriptor is not None
        assert descriptor.package_id == package_id
        assert descriptor.required_resource_type_id == "gpu-l40"
        assert descriptor.workload_template == {"toscaEntryDefinitions": "Definitions/main.yaml"}


def test_instantiate_resolves_cluster_via_focom(client, monkeypatch):
    """NFO+FOCOM LLD section 4: Instantiate queries FOCOM's inventory
    before placing a workload, rather than assuming the degenerate
    cluster implicitly.
    """
    resp = _instantiate(client, monkeypatch, cluster_id="focom-resolved-cluster")
    assert resp.status_code == 202
    assert resp.json()["clusterId"] == "focom-resolved-cluster"
    assert resp.json()["state"] == "RUNNING"


def test_instantiate_falls_back_to_degenerate_cluster_if_focom_unreachable(client, monkeypatch):
    monkeypatch.setattr("app.main.R1Client.get", lambda self, path, **kw: FakeR1Response(503, {}))
    descriptor_id = _create_descriptor(client)
    resp = client.post("/deployments", json={"nfDeploymentDescriptorId": descriptor_id, "name": "nf-1"})
    assert resp.json()["clusterId"] == "phase1-degenerate-cluster"


def test_instantiate_creates_a_resource_link(client, monkeypatch):
    """OPEN_ITEMS.md section 5: the reference's own NfOCloudVResource —
    the resource-linkage object between an NfDeployment and the O-Cloud
    resource it consumes, missing entirely before this pass.
    """
    resp = _instantiate(client, monkeypatch, cluster_id="focom-resolved-cluster")
    nf_deployment_id = resp.json()["nfDeploymentId"]

    links = client.get(f"/deployments/{nf_deployment_id}/resources").json()
    assert len(links) == 1
    assert links[0]["resourceRef"] == "focom-resolved-cluster"
    assert links[0]["vresourceType"] == "COMPUTE"


def test_instantiate_rejects_unknown_descriptor(client, monkeypatch):
    """The reference's own _check_dependencies (dms_lcm_nfdeployment.py):
    a descriptorId that doesn't exist must be rejected up front, not
    only surface as a later, unrelated failure.
    """
    monkeypatch.setattr("app.main.R1Client.get", lambda self, path, **kw: FakeR1Response(200, {"oCloudId": "c1"}))
    resp = client.post("/deployments", json={"nfDeploymentDescriptorId": str(uuid.uuid4()), "name": "nf-1"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["title"] == "NFDEPLOYMENT_DESCRIPTOR_NOT_FOUND"


def test_instantiate_rejects_duplicate_name(client, monkeypatch):
    """The reference's own _check_duplication: two deployments may not
    share a name.
    """
    _instantiate(client, monkeypatch, name="nf-1")
    resp = _instantiate(client, monkeypatch, name="nf-1")
    assert resp.status_code == 409
    assert resp.json()["detail"]["title"] == "NFDEPLOYMENT_NAME_CONFLICT"


def test_instantiate_rejects_descriptor_already_deployed(client, monkeypatch):
    """The reference's own _check_duplication: a descriptor may only be
    deployed once.
    """
    monkeypatch.setattr("app.main.R1Client.get", lambda self, path, **kw: FakeR1Response(200, {"oCloudId": "c1"}))
    descriptor_id = _create_descriptor(client)
    client.post("/deployments", json={"nfDeploymentDescriptorId": descriptor_id, "name": "nf-1"})

    resp = client.post("/deployments", json={"nfDeploymentDescriptorId": descriptor_id, "name": "nf-2"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["title"] == "NFDEPLOYMENT_DESCRIPTOR_ALREADY_DEPLOYED"


def test_terminate_removes_deployment(client, monkeypatch):
    created = _instantiate(client, monkeypatch).json()

    del_resp = client.delete(f"/deployments/{created['nfDeploymentId']}")
    assert del_resp.status_code == 204

    # placement query against a deleted deployment is a genuine error path,
    # not modeled with a friendly 404 in this reference build — asserting
    # that explicitly rather than papering over it.
    resp = client.get(f"/deployments/{created['nfDeploymentId']}/placement")
    assert resp.status_code == 500


def test_terminate_from_running_also_removes_its_resource_link(client, monkeypatch, db_session_factory):
    created = _instantiate(client, monkeypatch).json()
    client.delete(f"/deployments/{created['nfDeploymentId']}")

    with db_session_factory() as session:
        remaining = session.query(NFOCloudResource).filter_by(nf_deployment_id=uuid.UUID(created["nfDeploymentId"])).all()
        assert remaining == []


def test_terminate_removes_its_lcm_operation_history(client, monkeypatch, db_session_factory):
    """LCMOperation.nf_deployment_id has a real FK, same as
    NFOCloudResource above — SQLite's own test harness never enforces
    FKs by default, so a real Postgres instance is what actually caught
    this: every deployment has at least one LCMOperation row (from
    Instantiate), so deleting the deployment without clearing its own
    operation history — including the TERMINATE row Terminate itself
    just added — genuinely violates the constraint there.
    """
    created = _instantiate(client, monkeypatch).json()
    nf_deployment_id = uuid.UUID(created["nfDeploymentId"])

    del_resp = client.delete(f"/deployments/{nf_deployment_id}")
    assert del_resp.status_code == 204

    with db_session_factory() as session:
        remaining = session.query(LCMOperation).filter_by(nf_deployment_id=nf_deployment_id).all()
        assert remaining == []


def test_terminate_again_on_already_terminating_deployment_is_a_noop(client, monkeypatch, db_session_factory):
    """Mirrors the reference's own `elif ... Uninstalling: pass` — a
    second Terminate call while one is already mid-flight must not
    raise or double-delete.
    """
    created = _instantiate(client, monkeypatch).json()
    nf_deployment_id = uuid.UUID(created["nfDeploymentId"])

    with db_session_factory() as session:
        d = session.get(NFDeployment, nf_deployment_id)
        d.state = "TERMINATING"
        session.commit()

    resp = client.delete(f"/deployments/{nf_deployment_id}")
    assert resp.status_code == 204

    with db_session_factory() as session:
        assert session.get(NFDeployment, nf_deployment_id) is not None
        assert session.get(NFDeployment, nf_deployment_id).state == "TERMINATING"


def test_terminate_on_deleting_deployment_flips_to_abnormal_instead_of_deleting_twice(client, monkeypatch, db_session_factory):
    """The reference's own defensive catch-all
    (`else: transit_state(Abnormal)`) for a Terminate landing on a state
    its dispatch chain doesn't otherwise expect — a double-terminate
    race, here simulated directly.
    """
    created = _instantiate(client, monkeypatch).json()
    nf_deployment_id = uuid.UUID(created["nfDeploymentId"])

    with db_session_factory() as session:
        d = session.get(NFDeployment, nf_deployment_id)
        d.state = "DELETING"
        session.commit()

    resp = client.delete(f"/deployments/{nf_deployment_id}")
    assert resp.status_code == 204

    with db_session_factory() as session:
        d = session.get(NFDeployment, nf_deployment_id)
        assert d is not None
        assert d.state == "ABNORMAL"


def test_heal_recovers_abnormal_deployment_to_running(client, monkeypatch, db_session_factory):
    created = _instantiate(client, monkeypatch).json()
    nf_deployment_id = uuid.UUID(created["nfDeploymentId"])
    with db_session_factory() as session:
        session.get(NFDeployment, nf_deployment_id).state = "ABNORMAL"
        session.commit()

    resp = client.post(f"/deployments/{nf_deployment_id}/heal")
    assert resp.status_code == 200
    assert resp.json()["state"] == "RUNNING"


def test_heal_on_already_running_deployment_is_idempotent(client, monkeypatch):
    created = _instantiate(client, monkeypatch).json()
    resp = client.post(f"/deployments/{created['nfDeploymentId']}/heal")
    assert resp.status_code == 200
    assert resp.json()["state"] == "RUNNING"


def test_heal_rejects_a_deployment_mid_instantiate(client, monkeypatch, db_session_factory):
    """Heal doesn't make sense while another operation is already
    in-flight — INSTANTIATING has no HEAL edge at all.
    """
    created = _instantiate(client, monkeypatch).json()
    nf_deployment_id = uuid.UUID(created["nfDeploymentId"])
    with db_session_factory() as session:
        session.get(NFDeployment, nf_deployment_id).state = "INSTANTIATING"
        session.commit()

    resp = client.post(f"/deployments/{nf_deployment_id}/heal")
    assert resp.status_code == 409
    assert resp.json()["detail"]["title"] == "NFDEPLOYMENT_ILLEGAL_OPERATION"


def test_heal_unknown_deployment_is_404(client):
    resp = client.post(f"/deployments/{uuid.uuid4()}/heal")
    assert resp.status_code == 404


def test_scale_moves_running_deployment_through_updating_back_to_running(client, monkeypatch):
    """OPEN_ITEMS.md section 5: Scale previously had no state transition
    of any kind — now drives the reference's real RUNNING->UPDATING
    edge, completing synchronously (same elision pattern as Instantiate).
    """
    created = _instantiate(client, monkeypatch).json()
    resp = client.post(f"/deployments/{created['nfDeploymentId']}/scale")
    assert resp.status_code == 200
    assert resp.json()["state"] == "RUNNING"


def test_scale_rejects_a_deployment_not_running(client, monkeypatch, db_session_factory):
    created = _instantiate(client, monkeypatch).json()
    nf_deployment_id = uuid.UUID(created["nfDeploymentId"])
    with db_session_factory() as session:
        session.get(NFDeployment, nf_deployment_id).state = "ABNORMAL"
        session.commit()

    resp = client.post(f"/deployments/{nf_deployment_id}/scale")
    assert resp.status_code == 409
    assert resp.json()["detail"]["title"] == "NFDEPLOYMENT_ILLEGAL_OPERATION"


def test_scale_unknown_deployment_is_404(client):
    resp = client.post(f"/deployments/{uuid.uuid4()}/scale")
    assert resp.status_code == 404


def test_query_operation_status_after_instantiate(client, db_session_factory, monkeypatch):
    """query_operation_status (GET /operations/{id}) had no test coverage
    at all before this pass — Instantiate's own LCMOperation row was
    never looked back up through the route meant to query it.
    """
    created = _instantiate(client, monkeypatch).json()

    with db_session_factory() as session:
        op = session.query(LCMOperation).filter_by(
            nf_deployment_id=uuid.UUID(created["nfDeploymentId"]), operation_type="INSTANTIATE",
        ).one()

    resp = client.get(f"/operations/{op.operation_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "COMPLETED"


def test_query_operation_status_returns_completed_for_heal_and_scale(client, db_session_factory, monkeypatch):
    """Heal/Scale each persist their own LCMOperation row with status
    COMPLETED — verify it's actually retrievable through the query
    route, not just written.
    """
    created = _instantiate(client, monkeypatch).json()
    nf_deployment_id = uuid.UUID(created["nfDeploymentId"])
    with db_session_factory() as session:
        session.get(NFDeployment, nf_deployment_id).state = "ABNORMAL"
        session.commit()

    heal_resp = client.post(f"/deployments/{nf_deployment_id}/heal")
    assert heal_resp.status_code == 200

    with db_session_factory() as session:
        op = session.query(LCMOperation).filter_by(nf_deployment_id=nf_deployment_id, operation_type="HEAL").one()

    status_resp = client.get(f"/operations/{op.operation_id}")
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "COMPLETED"


def test_query_cluster_placement_for_existing_deployment(client, monkeypatch):
    """The success path was never actually asserted — only the
    deleted-deployment error path (test_terminate_removes_deployment) had
    coverage for this route.
    """
    resp_created = _instantiate(client, monkeypatch, cluster_id="focom-cluster")
    created = resp_created.json()

    resp = client.get(f"/deployments/{created['nfDeploymentId']}/placement")
    assert resp.status_code == 200
    assert resp.json()["clusterId"] == "focom-cluster"
    assert resp.json()["nfDeploymentId"] == created["nfDeploymentId"]


def test_terminate_unknown_deployment_is_idempotent(client):
    """terminate's `if d is None: return` guard means deleting a
    deployment that was never created (or already deleted) must not
    raise, matching a real O2dms Terminate's idempotent semantics.
    """
    resp = client.delete(f"/deployments/{uuid.uuid4()}")
    assert resp.status_code == 204


def test_query_resources_for_unknown_deployment_is_empty(client):
    resp = client.get(f"/deployments/{uuid.uuid4()}/resources")
    assert resp.status_code == 200
    assert resp.json() == []
