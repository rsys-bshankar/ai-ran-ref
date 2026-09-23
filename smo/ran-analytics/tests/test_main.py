"""Tests for RAN Analytics SMOS (RAN Analytics LLD section 1).
Run with: pytest smo/ran-analytics/tests -q
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from smo_shared.db import Base, get_session
from smo_shared.testing import make_test_engine

from app.main import app
from app.models import MDAFProducer, MDAFReport, MDASubscription


@pytest.fixture
def client(monkeypatch):
    engine = make_test_engine()
    Base.metadata.create_all(engine, tables=[MDAFProducer.__table__, MDAFReport.__table__, MDASubscription.__table__])
    TestSession = sessionmaker(bind=engine)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: None)
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_register_analytics_producer_closes_the_v1_3_gap(client):
    """RAN Analytics LLD section 1: v1.3 had Subscribe/Unsubscribe/Query
    but no way for a producer to register at all.
    """
    dme_type = str(uuid.uuid4())
    resp = client.post("/producers", params={"producer_id": "rapp-mdaf-1", "analytics_type": "coverage-issue-analysis"},
                        json={"dme_input_types": [dme_type], "output_schema": {"type": "object"}})
    assert resp.status_code == 201


def test_publish_and_query_report_by_analytics_type(client):
    dme_type = str(uuid.uuid4())
    resp = client.post("/reports", params={"analytics_type": "resource-utilization"},
                        json={"output": {"utilization": 0.7}, "input_sources": [dme_type]})
    assert resp.status_code == 201

    listing = client.get("/reports", params={"analytics_type": "resource-utilization"})
    assert len(listing.json()) == 1
    assert listing.json()[0]["output"]["utilization"] == 0.7

    empty = client.get("/reports", params={"analytics_type": "failure-prediction"})
    assert empty.json() == []


def test_subscribe_and_unsubscribe_analytics(client):
    sub = client.post("/subscriptions", params={"analytics_type": "coverage-issue-analysis", "requested_by": "sa-smos"}).json()
    assert "subscriptionId" in sub
    resp = client.delete(f"/subscriptions/{sub['subscriptionId']}")
    assert resp.status_code == 204
