"""Tests for Policy Management & Info SMOS (Policy Mgmt LLD sections 1, 3).
Run with: pytest smo/policy-mgmt/tests -q
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from smo_shared.db import Base, get_session
from smo_shared.testing import make_test_engine

from app.main import app
from app.models import Intent, IntentHandlingFunction, IntentReport


@pytest.fixture
def client():
    engine = make_test_engine()
    Base.metadata.create_all(engine, tables=[Intent.__table__, IntentReport.__table__, IntentHandlingFunction.__table__])
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


def test_create_and_query_intent(client):
    created = client.post("/intents", json={"expectations": [{"target": "latency"}], "priority": 5, "rmioId": "rapp-1"}).json()
    fetched = client.get(f"/intents/{created['intentId']}").json()
    assert fetched["intentPriority"] == 5
    assert fetched["intentAdminState"] == "ACTIVATED"


def test_update_admin_state_only_allowed_by_creator(client):
    """Policy Mgmt LLD section 1: closes v1.3's gap where
    intentAdminState had no operation transitioning it — RMIO-only.
    """
    created = client.post("/intents", json={"expectations": [], "rmioId": "rapp-1"}).json()

    denied = client.patch(f"/intents/{created['intentId']}/admin-state", json={"newState": "DEACTIVATED", "requesterId": "rapp-2"})
    assert denied.status_code == 409

    allowed = client.patch(f"/intents/{created['intentId']}/admin-state", json={"newState": "DEACTIVATED", "requesterId": "rapp-1"})
    assert allowed.status_code == 200
    assert allowed.json()["intentAdminState"] == "DEACTIVATED"


def test_query_intents_filters_by_admin_state(client):
    client.post("/intents", json={"expectations": [], "rmioId": "rapp-1"})
    two = client.post("/intents", json={"expectations": [], "rmioId": "rapp-2"}).json()
    client.patch(f"/intents/{two['intentId']}/admin-state", json={"newState": "DEACTIVATED", "requesterId": "rapp-2"})

    active = client.get("/intents", params={"admin_state": "ACTIVATED"}).json()
    assert len(active) == 1


def test_register_intent_handling_function_rejects_external_rapp_caller(client):
    """D-SEC-POLICY-1, unchanged: external callers (rApp UUIDs) may never
    hold an rmihId. identity.py's is_framework_internal_identity gates it.
    """
    resp = client.post("/intent-handling-functions", json={
        "rmihId": "550e8400-e29b-41d4-a716-446655440000", "smeServiceId": "svc-1", "capabilities": [{"scope": "config"}],
    })
    assert resp.status_code == 409
    assert resp.json()["detail"]["title"] == "SERVICE_NAME_CONFLICT"


def test_register_intent_handling_function_accepts_framework_internal_caller(client):
    resp = client.post("/intent-handling-functions", json={
        "rmihId": "so-smos", "smeServiceId": "svc-1", "capabilities": [{"scope": "config"}],
    })
    assert resp.status_code == 201


def test_deregister_intent_handling_function_symmetric_with_register(client):
    client.post("/intent-handling-functions", json={"rmihId": "sa-smos", "smeServiceId": "svc-2", "capabilities": [{}]})
    resp = client.delete("/intent-handling-functions/sa-smos")
    assert resp.status_code == 204


def test_publish_intent_report(client):
    intent = client.post("/intents", json={"expectations": [], "rmioId": "rapp-1"}).json()
    resp = client.post("/intent-reports", json={"intentId": intent["intentId"], "fulfilmentReport": {"met": True}})
    assert resp.status_code == 201
