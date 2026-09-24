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
        "notificationCallbackUri": "http://so-smos:8000/intents/notify",
    })
    assert resp.status_code == 409
    assert resp.json()["detail"]["title"] == "SERVICE_NAME_CONFLICT"


def test_register_intent_handling_function_accepts_framework_internal_caller(client):
    resp = client.post("/intent-handling-functions", json={
        "rmihId": "so-smos", "smeServiceId": "svc-1", "capabilities": [{"scope": "config"}],
        "notificationCallbackUri": "http://so-smos:8000/intents/notify",
    })
    assert resp.status_code == 201


def test_deregister_intent_handling_function_symmetric_with_register(client):
    client.post("/intent-handling-functions", json={
        "rmihId": "sa-smos", "smeServiceId": "svc-2", "capabilities": [{}],
        "notificationCallbackUri": "http://sa-smos:8000/intents/notify",
    })
    resp = client.delete("/intent-handling-functions/sa-smos")
    assert resp.status_code == 204


def test_register_intent_handling_function_persists_and_rejects_invalid_scope(client):
    """SPEC_AUDIT.md item 5: TS28312_IntentNrm.yaml's IntentHandlingScope
    is a closed 2-value enum (RAN/CN), previously untyped JSON never set
    by any caller.
    """
    resp = client.post("/intent-handling-functions", json={
        "rmihId": "so-smos", "smeServiceId": "svc-1", "capabilities": [{}],
        "notificationCallbackUri": "http://so-smos:8000/intents/notify", "intentHandlingScope": ["RAN"],
    })
    assert resp.status_code == 201
    assert resp.json()["intentHandlingScope"] == ["RAN"]

    invalid = client.post("/intent-handling-functions", json={
        "rmihId": "sa-smos", "smeServiceId": "svc-2", "capabilities": [{}],
        "notificationCallbackUri": "http://sa-smos:8000/intents/notify", "intentHandlingScope": ["NOT_A_REAL_SCOPE"],
    })
    assert invalid.status_code == 422


def test_create_intent_scope_pre_filters_matching_rmihs(client, monkeypatch):
    """SPEC_AUDIT.md item 5: intentHandlingScope was previously never
    read at match time either — an RMIH declaring RAN-only scope must
    not be dispatched a CN-scoped Intent even if its declared
    supportedExpectationObjectType matches.
    """
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    client.post("/intent-handling-functions", json={
        "rmihId": "so-smos", "smeServiceId": "svc-1", "capabilities": [{"supportedExpectationObjectType": "RAN_SUBNETWORK"}],
        "notificationCallbackUri": "http://so-smos:8000/intents/notify", "intentHandlingScope": ["RAN"],
    })
    client.post("/intent-handling-functions", json={
        "rmihId": "sa-smos", "smeServiceId": "svc-2", "capabilities": [{"supportedExpectationObjectType": "RAN_SUBNETWORK"}],
        "notificationCallbackUri": "http://sa-smos:8000/intents/notify", "intentHandlingScope": ["CN"],
    })

    client.post("/intents", json={
        "expectations": [{"expectationObject": {"objectType": "RAN_SUBNETWORK"}}], "rmioId": "rapp-1", "intentHandlingScope": "CN",
    })

    assert len(calls) == 1
    assert calls[0][0] == "http://sa-smos:8000/intents/notify"


def test_create_intent_scope_matches_an_rmih_with_no_declared_scope(client, monkeypatch):
    """An RMIH with no declared intentHandlingScope (the pre-existing
    default) still matches any requested scope — this field is a
    pre-filter, not a requirement.
    """
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    client.post("/intent-handling-functions", json={
        "rmihId": "so-smos", "smeServiceId": "svc-1", "capabilities": [{"supportedExpectationObjectType": "RAN_SUBNETWORK"}],
        "notificationCallbackUri": "http://so-smos:8000/intents/notify",
    })
    client.post("/intents", json={
        "expectations": [{"expectationObject": {"objectType": "RAN_SUBNETWORK"}}], "rmioId": "rapp-1", "intentHandlingScope": "CN",
    })
    assert len(calls) == 1


def test_delete_intent_removes_it_and_its_reports(client):
    """SPEC_AUDIT.md item 5: no DELETE /intents/{id} existed at all —
    an RMIO could only deactivate an Intent, never retract it. Cascades
    to IntentReport the same way this build's other owned-child deletes
    already do (rapp_instance, aiml_model, ...).
    """
    intent = client.post("/intents", json={"expectations": [], "rmioId": "rapp-1"}).json()
    client.post("/intent-reports", json={"intentId": intent["intentId"], "fulfilmentReport": {"met": True}})

    resp = client.delete(f"/intents/{intent['intentId']}")
    assert resp.status_code == 204
    remaining = client.get("/intents").json()
    assert intent["intentId"] not in [i["intentId"] for i in remaining]


def test_delete_intent_is_idempotent_for_an_unknown_id(client):
    resp = client.delete("/intents/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 204


def test_publish_intent_report(client):
    intent = client.post("/intents", json={"expectations": [], "rmioId": "rapp-1"}).json()
    resp = client.post("/intent-reports", json={"intentId": intent["intentId"], "fulfilmentReport": {"met": True}})
    assert resp.status_code == 201


def test_create_intent_dispatches_to_matching_rmih(client, monkeypatch):
    """SPEC_AUDIT.md items 2-3: CreateIntent now notifies any RMIH whose
    intent_handling_capability_list declares a matching
    supportedExpectationObjectType (TS28312_IntentNrm.yaml's real
    IntentHandlingCapability field), read from the Intent's own
    expectations[].expectationObject.objectType — not an invented
    top-level intentType string with no shared spec vocabulary.
    """
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    client.post("/intent-handling-functions", json={
        "rmihId": "so-smos", "smeServiceId": "svc-1",
        "capabilities": [{"supportedExpectationObjectType": "RAN_SUBNETWORK"}],
        "notificationCallbackUri": "http://so-smos:8000/intents/notify",
    })
    client.post("/intent-handling-functions", json={
        "rmihId": "sa-smos", "smeServiceId": "svc-2",
        "capabilities": [{"supportedExpectationObjectType": "5GC_SUBNETWORK"}],
        "notificationCallbackUri": "http://sa-smos:8000/intents/notify",
    })

    resp = client.post("/intents", json={
        "expectations": [{"expectationObject": {"objectType": "RAN_SUBNETWORK"}}], "rmioId": "rapp-1",
    })
    intent_id = resp.json()["intentId"]

    assert len(calls) == 1  # only the matching RMIH (so-smos) was notified, not sa-smos
    assert calls[0][0] == "http://so-smos:8000/intents/notify"
    assert calls[0][1]["intentId"] == intent_id
    assert calls[0][1]["expectationObjectTypes"] == ["RAN_SUBNETWORK"]


def test_create_intent_matches_across_multiple_expectations_in_one_intent(client, monkeypatch):
    """A single Intent can carry several expectations, each with its own
    expectationObject.objectType — an RMIH matching any one of them
    should be notified.
    """
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    client.post("/intent-handling-functions", json={
        "rmihId": "so-smos", "smeServiceId": "svc-1",
        "capabilities": [{"supportedExpectationObjectType": "EDGE_SERVICE_SUPPORT"}],
        "notificationCallbackUri": "http://so-smos:8000/intents/notify",
    })
    client.post("/intents", json={
        "expectations": [
            {"expectationObject": {"objectType": "RAN_SUBNETWORK"}},
            {"expectationObject": {"objectType": "EDGE_SERVICE_SUPPORT"}},
        ],
        "rmioId": "rapp-1",
    })
    assert len(calls) == 1
    assert calls[0][1]["expectationObjectTypes"] == ["EDGE_SERVICE_SUPPORT", "RAN_SUBNETWORK"]


def test_create_intent_without_expectation_object_type_dispatches_to_no_one(client, monkeypatch):
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    client.post("/intent-handling-functions", json={
        "rmihId": "so-smos", "smeServiceId": "svc-1", "capabilities": [{"supportedExpectationObjectType": "RAN_SUBNETWORK"}],
        "notificationCallbackUri": "http://so-smos:8000/intents/notify",
    })
    client.post("/intents", json={"expectations": [], "rmioId": "rapp-1"})
    assert calls == []


def test_create_intent_succeeds_even_if_rmih_callback_is_unreachable(client, monkeypatch):
    """Dispatch is best-effort — a dead RMIH callback must never fail
    CreateIntent itself.
    """
    import httpx as httpx_module

    def raise_error(url, json=None, timeout=None):
        raise httpx_module.ConnectError("unreachable")

    monkeypatch.setattr("app.main.httpx.post", raise_error)

    client.post("/intent-handling-functions", json={
        "rmihId": "so-smos", "smeServiceId": "svc-1", "capabilities": [{"supportedExpectationObjectType": "RAN_SUBNETWORK"}],
        "notificationCallbackUri": "http://so-smos:8000/intents/notify",
    })
    resp = client.post("/intents", json={
        "expectations": [{"expectationObject": {"objectType": "RAN_SUBNETWORK"}}], "rmioId": "rapp-1",
    })
    assert resp.status_code == 201


def test_create_intent_stores_and_returns_intent_mgmt_purpose(client):
    """SPEC_AUDIT.md item 3: intentMgmtPurpose is TS28312_IntentNrm.yaml's
    real workflow-procedure enum — this build previously conflated it
    with the (now-removed) invented matching field. Defaults to the
    spec's own default when not given.
    """
    created = client.post("/intents", json={"expectations": [], "rmioId": "rapp-1"}).json()
    default = client.get(f"/intents/{created['intentId']}").json()
    assert default["intentMgmtPurpose"] == "FULFILMENT_WITHOUT_NEGOTIATION"

    created = client.post("/intents", json={
        "expectations": [], "rmioId": "rapp-1", "intentMgmtPurpose": "FEASIBILITYCHECK",
    }).json()
    explicit = client.get(f"/intents/{created['intentId']}").json()
    assert explicit["intentMgmtPurpose"] == "FEASIBILITYCHECK"


def test_create_intent_rejects_an_invalid_intent_mgmt_purpose(client):
    resp = client.post("/intents", json={"expectations": [], "rmioId": "rapp-1", "intentMgmtPurpose": "NOT_A_REAL_PURPOSE"})
    assert resp.status_code == 422
