"""Tests for FOCOM SMOS (NFO+FOCOM LLD section 1). Run with:
pytest smo/focom/tests -q
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from smo_shared.db import Base, get_session

from app.main import app, PHASE1_CLUSTER_ID
from app.models import OCloudAlarm, OCloudPerformanceMetric


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[OCloudAlarm.__table__, OCloudPerformanceMetric.__table__])
    TestSession = sessionmaker(bind=engine)
    return TestSession


@pytest.fixture
def client(db_session):
    def override_get_session():
        session = db_session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_query_inventory_returns_degenerate_cluster(client):
    resp = client.get("/inventory")
    assert resp.json()["clusterId"] == PHASE1_CLUSTER_ID


def test_query_inventory_echoes_requested_resource_type(client):
    """NFO+FOCOM LLD section 4: NFO's Instantiate passes resource_type
    through to shape the returned resource pool — this is the query
    parameter name NFO must use (resource_type, not resourceType; a
    silent bug this exact mismatch caused before it was fixed).
    """
    resp = client.get("/inventory", params={"resource_type": "gpu-l40"})
    assert resp.json()["resourcePools"][0]["resourceTypeId"] == "gpu-l40"


def test_subscribe_inventory_changes_is_a_no_op(client):
    """Phase 1: a single degenerate cluster never changes, so this is
    intentionally a no-op rather than unimplemented (D-DEPLOY-FOCOM-1).
    """
    resp = client.post("/inventory/subscriptions")
    assert resp.json()["status"] == "subscribed"


def test_provision_and_deprovision_resource(client):
    provisioned = client.post("/resources/provision", json={"cpu": 4, "memory": "16Gi"})
    assert provisioned.status_code == 200
    body = provisioned.json()
    assert body["clusterId"] == PHASE1_CLUSTER_ID
    assert "resourceId" in body

    deprovisioned = client.delete(f"/resources/{body['resourceId']}")
    assert deprovisioned.json()["status"] == "deprovisioned"


def test_monitor_resource_reports_healthy(client):
    resp = client.get("/resources/some-resource-id/status")
    assert resp.json() == {"resourceId": "some-resource-id", "status": "healthy"}


def test_ingested_alarm_is_queryable(client):
    """FOCOM's own alarm domain — infrastructure, distinct from RAN NF
    OAM's RAN-function Alarm (NFO+FOCOM LLD section 1).
    """
    client.post("/alarms/ingest", params={"resource_ref": "host-1", "severity": "critical"})
    resp = client.get("/alarms")
    assert len(resp.json()) == 1
    assert resp.json()[0]["resourceRef"] == "host-1"


def test_performance_metrics_filterable_by_resource(client, db_session):
    """No POST /performance route exists — metrics arrive via O2ims
    collection, not an rApp-facing write — so this seeds directly against
    the same DB the app is wired to, rather than skipping the test.
    """
    session = db_session()
    session.add(OCloudPerformanceMetric(resource_ref="host-1", metric_name="cpu", value=0.5))
    session.add(OCloudPerformanceMetric(resource_ref="host-2", metric_name="cpu", value=0.9))
    session.commit()
    session.close()

    resp = client.get("/performance", params={"resource_ref": "host-2"})
    assert len(resp.json()) == 1
    assert resp.json()[0]["value"] == 0.9


def test_performance_metrics_without_filter_returns_all(client, db_session):
    """The unfiltered path (no resource_ref) was never actually exercised
    — every previous test passed a filter.
    """
    session = db_session()
    session.add(OCloudPerformanceMetric(resource_ref="host-1", metric_name="cpu", value=0.5))
    session.add(OCloudPerformanceMetric(resource_ref="host-2", metric_name="cpu", value=0.9))
    session.commit()
    session.close()

    resp = client.get("/performance")
    assert len(resp.json()) == 2


def test_query_performance_returns_empty_list_when_none_seeded(client):
    resp = client.get("/performance")
    assert resp.json() == []


def test_query_inventory_defaults_resource_type_to_generic(client):
    """The no-resource_type fallback ("generic") was only ever exercised
    implicitly through test_query_inventory_returns_degenerate_cluster,
    which never actually checked resourcePools' resourceTypeId.
    """
    resp = client.get("/inventory")
    assert resp.json()["resourcePools"][0]["resourceTypeId"] == "generic"


def test_query_alarms_returns_empty_list_when_none_ingested(client):
    resp = client.get("/alarms")
    assert resp.json() == []


def test_multiple_alarms_are_all_returned(client):
    """test_ingested_alarm_is_queryable only ever ingested one alarm — a
    second one arriving must not overwrite or drop the first.
    """
    client.post("/alarms/ingest", params={"resource_ref": "host-1", "severity": "critical"})
    client.post("/alarms/ingest", params={"resource_ref": "host-2", "severity": "minor"})

    resp = client.get("/alarms")
    assert len(resp.json()) == 2
    refs = {a["resourceRef"] for a in resp.json()}
    assert refs == {"host-1", "host-2"}


def test_deprovision_arbitrary_unprovisioned_resource_succeeds(client):
    """Phase 1: deprovision_resource is a shape-only stub that never
    checks whether the resource_id was ever actually provisioned — a
    real, explicit behavior worth asserting directly rather than only
    exercising it incidentally through the provision-then-deprovision
    happy path.
    """
    resp = client.delete("/resources/never-provisioned-id")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deprovisioned"
