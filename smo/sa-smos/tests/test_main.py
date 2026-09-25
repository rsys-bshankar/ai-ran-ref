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
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


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


def test_rollback_is_explicitly_unsupported_not_ambiguous(client):
    """ROLLBACK stays unsupported, but for a concrete, checked reason now
    — rApp Management retains no version history to roll back to at all
    — not a vague "ambiguous meaning" refusal (LLD section 2.1).
    """
    monitor = client.post("/monitors", params={}, json={}).json()
    resp = client.post(f"/monitors/{monitor['monitorId']}/remedial-actions", params={"action_type": "ROLLBACK"})
    assert resp.status_code == 501
    assert resp.json()["detail"]["title"] == "ROLLBACK_HISTORY_UNAVAILABLE"


def test_reconnect_resolves_deployment_via_order_and_heals(client, monkeypatch):
    """The actual fix: RECONNECT is no longer refused — it resolves the
    monitor's target_order_id into a concrete nfDeploymentId via SO
    SMOS's own order record, then dispatches to NFO's Heal.
    """
    order_id = uuid.uuid4()
    nf_deployment_id = str(uuid.uuid4())
    calls = []

    def fake_get(self, path, **kw):
        assert path == f"/so-smos/orders/{order_id}"
        return FakeR1Response(200, {"steps": [
            {"stepType": "DEPLOY", "status": "COMPLETED", "result": {"nfDeploymentId": nf_deployment_id}},
        ]})

    def fake_post(self, path, json=None, **kw):
        calls.append(path)
        return FakeR1Response(200)

    monkeypatch.setattr("app.main.R1Client.get", fake_get)
    monkeypatch.setattr("app.main.R1Client.post", fake_post)

    monitor = client.post("/monitors", params={"target_order_id": str(order_id)}, json={}).json()
    resp = client.post(f"/monitors/{monitor['monitorId']}/remedial-actions", params={"action_type": "RECONNECT"})
    assert resp.status_code == 201
    assert resp.json()["outcome"] == "RESOLVED"
    assert calls == [f"/nfo/deployments/{nf_deployment_id}/heal"]


def test_reconnect_escalates_when_monitor_has_no_target_order(client):
    monitor = client.post("/monitors", params={}, json={}).json()  # no target_order_id at all
    resp = client.post(f"/monitors/{monitor['monitorId']}/remedial-actions", params={"action_type": "RECONNECT"})
    assert resp.json()["outcome"] == "ESCALATED"


def test_reconnect_escalates_when_order_has_no_completed_deploy_step(client, monkeypatch):
    order_id = uuid.uuid4()
    monkeypatch.setattr("app.main.R1Client.get", lambda self, path, **kw: FakeR1Response(200, {"steps": [
        {"stepType": "CONFIG", "status": "COMPLETED", "result": {}},
    ]}))
    monitor = client.post("/monitors", params={"target_order_id": str(order_id)}, json={}).json()
    resp = client.post(f"/monitors/{monitor['monitorId']}/remedial-actions", params={"action_type": "RECONNECT"})
    assert resp.json()["outcome"] == "ESCALATED"


def test_group_scoped_monitor_dispatches_retrain_regardless_of_action_type(client, monkeypatch):
    """MLModelCoordinationGroup x SA SMOS convergence (OPEN_ITEMS.md
    section 1): a coordination-group-scoped monitor bypasses
    CONFIG_CHANGE/SCALE/RECONNECT/ROLLBACK entirely — those are
    NF-deployment concepts that don't map onto a model group — and
    always dispatches a group retrain via AI/ML Workflow instead,
    whatever actionType was requested.
    """
    group_id = uuid.uuid4()
    calls = []

    def fake_post(self, path, json=None, **kw):
        calls.append((path, json))
        return FakeR1Response(200)

    monkeypatch.setattr("app.main.R1Client.post", fake_post)

    monitor = client.post("/monitors", params={"target_coordination_group_id": str(group_id)}, json={}).json()
    resp = client.post(f"/monitors/{monitor['monitorId']}/remedial-actions", params={"action_type": "CONFIG_CHANGE"})
    assert resp.status_code == 201
    assert resp.json()["outcome"] == "RESOLVED"
    assert calls == [("/ai-ml-workflow/training-jobs", {"modelCoordinationGroupId": str(group_id), "producerId": "sa-smos"})]


def test_group_scoped_monitor_escalates_when_ai_ml_workflow_rejects(client, monkeypatch):
    group_id = uuid.uuid4()
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: FakeR1Response(409))

    monitor = client.post("/monitors", params={"target_coordination_group_id": str(group_id)}, json={}).json()
    resp = client.post(f"/monitors/{monitor['monitorId']}/remedial-actions", params={"action_type": "RECONNECT"})
    assert resp.json()["outcome"] == "ESCALATED"


def test_escalate_to_operator(client):
    monitor = client.post("/monitors", params={}, json={}).json()
    resp = client.post(f"/monitors/{monitor['monitorId']}/escalate", params={"reason": "no auto-remediation available"})
    assert resp.status_code == 200
    assert resp.json()["outcome"] == "ESCALATED"


def test_health_check_answers_the_gui_bff_liveness_probe(client):
    """GUI pass: the BFF's /modules/status probes /<module>/health on every module."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}


def test_list_and_get_monitors(client):
    """GUI pass: AssuranceMonitor was write-only."""
    monitor_id = client.post("/monitors", json={"accuracy": 0.9}).json()["monitorId"]
    assert [(m["monitorId"], m["thresholds"]) for m in client.get("/monitors").json()] == [(monitor_id, {"accuracy": 0.9})]
    assert client.get(f"/monitors/{monitor_id}").json()["monitorId"] == monitor_id
    assert client.get(f"/monitors/{uuid.uuid4()}").status_code == 404


def test_list_remedial_actions_filters_escalations(client):
    monitor_id = client.post("/monitors", json={}).json()["monitorId"]
    resolved = client.post(f"/monitors/{monitor_id}/remedial-actions", params={"action_type": "CONFIG_CHANGE"}).json()
    escalated = client.post(f"/monitors/{monitor_id}/escalate", params={"reason": "manual"}).json()

    assert {a["actionId"] for a in client.get("/remedial-actions").json()} == {resolved["actionId"], escalated["actionId"]}
    queue = client.get("/remedial-actions", params={"outcome": "ESCALATED"}).json()
    assert [a["actionId"] for a in queue] == [escalated["actionId"]]
    assert client.get("/remedial-actions", params={"monitor_id": str(uuid.uuid4())}).json() == []
