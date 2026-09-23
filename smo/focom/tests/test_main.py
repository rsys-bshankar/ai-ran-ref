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
