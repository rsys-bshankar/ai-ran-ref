"""Tests for SO SMOS's routes (SO SMOS LLD section 1) — submit/query/cancel
an order — none of which had test coverage before; only the dispatch
table's execution semantics (test_dispatch.py) did.
Run with: pytest smo/so-smos/tests -q
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from smo_shared.db import Base, get_session

from app.main import app
from app.models import ServiceOrder


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[ServiceOrder.__table__])
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


def test_submit_order_persists_and_returns_executed_steps(client, monkeypatch):
    monkeypatch.setattr("app.main.execute_order", lambda r1, steps: [{**s, "status": "COMPLETED", "result": {}} for s in steps])

    resp = client.post("/orders", json={"scope": "policy-rollout", "steps": [{"stepType": "POLICY", "targetModule": "A1_RELATED"}]})
    assert resp.status_code == 202
    body = resp.json()
    assert body["steps"][0]["status"] == "COMPLETED"
    assert "orderId" in body


def test_query_order_status_returns_the_persisted_order(client, monkeypatch):
    monkeypatch.setattr("app.main.execute_order", lambda r1, steps: [{**s, "status": "COMPLETED", "result": {}} for s in steps])
    created = client.post("/orders", json={"scope": "policy-rollout", "steps": [{"stepType": "POLICY", "targetModule": "A1_RELATED"}]}).json()

    resp = client.get(f"/orders/{created['orderId']}")
    assert resp.json()["orderId"] == created["orderId"]
    assert resp.json()["steps"] == created["steps"]


def test_cancel_order_marks_only_pending_steps_cancelled(client, monkeypatch):
    """cancel_order must leave already-COMPLETED/FAILED steps alone —
    only steps still PENDING (never attempted, per the fail-fast halt)
    move to CANCELLED.
    """
    monkeypatch.setattr("app.main.execute_order", lambda r1, steps: [
        {**steps[0], "status": "COMPLETED"},
        {**steps[1], "status": "FAILED"},
        {**steps[2], "status": "PENDING"},
    ])
    created = client.post("/orders", json={"scope": "mixed", "steps": [
        {"stepType": "POLICY", "targetModule": "A1_RELATED"},
        {"stepType": "CONFIG", "targetModule": "RAN_NF_OAM"},
        {"stepType": "DEPLOY", "targetModule": "NFO"},
    ]}).json()

    resp = client.post(f"/orders/{created['orderId']}/cancel")
    statuses = [s["status"] for s in resp.json()["steps"]]
    assert statuses == ["COMPLETED", "FAILED", "CANCELLED"]


def test_health_check_answers_the_gui_bff_liveness_probe(client):
    """GUI pass: the BFF's /modules/status probes /<module>/health on every module."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}


def test_list_orders_returns_every_persisted_order(client, monkeypatch):
    """GUI pass: only GET /orders/{id} existed."""
    monkeypatch.setattr("app.main.execute_order", lambda r1, steps: [{**s, "status": "COMPLETED", "result": {}} for s in steps])
    assert client.get("/orders").json() == []
    created = client.post("/orders", json={"scope": "policy-rollout", "steps": [{"stepType": "POLICY", "targetModule": "A1_RELATED"}]}).json()

    listed = client.get("/orders").json()
    assert [(o["orderId"], o["scope"], o["steps"]) for o in listed] == [(created["orderId"], "policy-rollout", created["steps"])]
