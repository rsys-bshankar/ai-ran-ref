"""Tests for SA SMOS (SO/SA SMOS LLD section 2). Run with:
pytest smo/sa-smos/tests -q
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Column, Table
from sqlalchemy import Uuid as UuidType
from sqlalchemy.orm import sessionmaker

from smo_shared.db import Base, get_session
from smo_shared.testing import make_test_engine

from app.main import app
from app.models import AssuranceMonitor, RemedialAction


class FakeR1Response:
    def __init__(self, status_code):
        self.status_code = status_code


@pytest.fixture
def client(monkeypatch):
    engine = make_test_engine()
    # service_order and ml_model_coordination_group live in other modules —
    # stand in minimal tables so AssuranceMonitor's FKs resolve, same pattern
    # as rapp-mgmt/tests/test_upgrade.py. Production runs against the full
    # consolidated migration (001_init.sql).
    for name in ("service_order", "ml_model_coordination_group", "mda_subscription"):
        if name not in Base.metadata.tables:
            Table(name, Base.metadata, Column(
                "order_id" if name == "service_order" else "group_id" if name == "ml_model_coordination_group" else "subscription_id",
                UuidType, primary_key=True,
            ))
    Base.metadata.create_all(engine, tables=[
        Base.metadata.tables["service_order"], Base.metadata.tables["ml_model_coordination_group"],
        Base.metadata.tables["mda_subscription"], AssuranceMonitor.__table__, RemedialAction.__table__,
    ])
    TestSession = sessionmaker(bind=engine)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: FakeR1Response(200))
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_register_monitor_rejects_both_targets_set(client):
    """SO/SA SMOS LLD section 2.2's one_target_only constraint, checked
    at the route layer (the DB CHECK constraint is the backstop).
    """
    resp = client.post("/monitors", params={
        "target_order_id": str(uuid.uuid4()), "target_coordination_group_id": str(uuid.uuid4()),
    }, json={})
    assert resp.status_code == 422


def test_register_monitor_with_single_target(client):
    # `thresholds` is a bare dict body param — the JSON body IS the thresholds
    # dict directly, not wrapped under a "thresholds" key.
    resp = client.post("/monitors", params={"target_order_id": str(uuid.uuid4())}, json={"latency": 100})
    assert resp.status_code == 201


def test_evaluate_thresholds_reports_breaches(client):
    monitor = client.post("/monitors", params={}, json={"latency": 100, "throughput": 50}).json()
    # current_metrics is a bare `dict` body param — the JSON body IS the dict,
    # not wrapped in a key.
    resp = client.post(f"/monitors/{monitor['monitorId']}/evaluate", json={"latency": 80, "throughput": 60})
    assert resp.json()["breaches"] == {"latency": 100}


def test_config_change_dispatches_and_resolves(client):
    monitor = client.post("/monitors", params={}, json={}).json()
    resp = client.post(f"/monitors/{monitor['monitorId']}/remedial-actions", params={"action_type": "CONFIG_CHANGE"})
    assert resp.status_code == 201
    assert resp.json()["outcome"] == "RESOLVED"


def test_scale_always_escalates_phase1_stub(client):
    """SCALE inherits NFO's own Phase 1 stub status — cannot do anything
    real yet, and that's the documented, correct outcome (LLD section 2.1).
    """
    monitor = client.post("/monitors", params={}, json={}).json()
    resp = client.post(f"/monitors/{monitor['monitorId']}/remedial-actions", params={"action_type": "SCALE"})
    assert resp.json()["outcome"] == "ESCALATED"


@pytest.mark.parametrize("action_type", ["RECONNECT", "ROLLBACK"])
def test_ambiguous_action_types_are_not_silently_resolved(client, action_type):
    """The core decision under test: rather than guess which of several
    plausible meanings RECONNECT/ROLLBACK has, the route raises a clear
    error naming the ambiguity (LLD section 2.1).
    """
    monitor = client.post("/monitors", params={}, json={}).json()
    resp = client.post(f"/monitors/{monitor['monitorId']}/remedial-actions", params={"action_type": action_type})
    assert resp.status_code == 422
    assert "ambiguous" in resp.json()["detail"]["detail"]


def test_escalate_to_operator(client):
    monitor = client.post("/monitors", params={}, json={}).json()
    resp = client.post(f"/monitors/{monitor['monitorId']}/escalate", params={"reason": "no auto-remediation available"})
    assert resp.status_code == 200
    assert resp.json()["outcome"] == "ESCALATED"
