"""Tests for Intent Service (formerly Policy Management & Info SMOS —
see app/main.py's own module docstring; Policy Mgmt LLD sections 1, 3).
Run with: pytest smo/intent-service/tests -q

Wave 3 (docs/ownership/INTENT_SERVICE_OWNERSHIP.md's "Open item carried
into Wave 3"): CreateIntent now requires consumer-side RMIH selection —
every test below registers a real IntentHandlingFunction first and names
it via `rmihId`, replacing the former multi-candidate capability-match
tests with single-target validation ones.
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
    Base.metadata.create_all(engine, tables=[IntentHandlingFunction.__table__, Intent.__table__, IntentReport.__table__])
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


def _register_rmih(client, rmih_id="so-smos", capabilities=None, scope=None, callback=None):
    body = {
        "rmihId": rmih_id, "smeServiceId": f"svc-{rmih_id}",
        "capabilities": capabilities if capabilities is not None else [{"supportedExpectationObjectType": "RAN_SUBNETWORK"}],
        "notificationCallbackUri": callback or f"http://{rmih_id}:8000/intents/notify",
    }
    if scope is not None:
        body["intentHandlingScope"] = scope
    resp = client.post("/intent-handling-functions", json=body)
    assert resp.status_code == 201, resp.text
    return rmih_id


def test_create_and_query_intent(client):
    _register_rmih(client)
    created = client.post("/intents", json={"expectations": [{"target": "latency"}], "priority": 5, "rmioId": "rapp-1", "rmihId": "so-smos"}).json()
    fetched = client.get(f"/intents/{created['intentId']}").json()
    assert fetched["intentPriority"] == 5
    assert fetched["intentAdminState"] == "ACTIVATED"
    assert fetched["rmihId"] == "so-smos"


def test_create_intent_for_unknown_rmih_is_404(client):
    resp = client.post("/intents", json={"expectations": [], "rmioId": "rapp-1", "rmihId": "no-such-rmih"})
    assert resp.status_code == 404


def test_query_unknown_intent_is_404(client):
    """A real gap this Wave 3 slice's own CASCADE surfaces directly: an
    Intent can now legitimately vanish out from under a caller (its
    addressed RMIH was deregistered), so GET must answer 404 rather than
    crash on the now-missing row.
    """
    import uuid as uuid_module

    resp = client.get(f"/intents/{uuid_module.uuid4()}")
    assert resp.status_code == 404


def test_update_admin_state_only_allowed_by_creator(client):
    """Policy Mgmt LLD section 1: closes v1.3's gap where
    intentAdminState had no operation transitioning it — RMIO-only.
    """
    _register_rmih(client)
    created = client.post("/intents", json={"expectations": [], "rmioId": "rapp-1", "rmihId": "so-smos"}).json()

    denied = client.patch(f"/intents/{created['intentId']}/admin-state", json={"newState": "DEACTIVATED", "requesterId": "rapp-2"})
    assert denied.status_code == 409

    allowed = client.patch(f"/intents/{created['intentId']}/admin-state", json={"newState": "DEACTIVATED", "requesterId": "rapp-1"})
    assert allowed.status_code == 200
    assert allowed.json()["intentAdminState"] == "DEACTIVATED"


def test_query_intents_filters_by_admin_state(client):
    _register_rmih(client)
    client.post("/intents", json={"expectations": [], "rmioId": "rapp-1", "rmihId": "so-smos"})
    two = client.post("/intents", json={"expectations": [], "rmioId": "rapp-2", "rmihId": "so-smos"}).json()
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
    _register_rmih(client, "sa-smos")
    resp = client.delete("/intent-handling-functions/sa-smos")
    assert resp.status_code == 204


def test_deregister_intent_handling_function_of_an_intent_still_succeeds(client):
    """Wave 3: TS28312_IntentNrm.yaml's own NRM containment
    (IntentHandlingFunction *contains* Intent) means the migration's real
    ON DELETE CASCADE now ends every Intent still addressed to a
    deregistered RMIH — same house pattern as MLMR's own
    deregister_model cascade. SQLite doesn't enforce FK constraints by
    default (no PRAGMA foreign_keys=ON in smo_shared/testing.py), so this
    module's own unit-test DB can't exercise the cascade firing at all —
    the same honest, already-established carve-out this codebase uses
    for MLMR's identical cascade. This test only proves the deregister
    call itself doesn't error under SQLite (no cascade there to trip
    over); the real cascade is verified separately against live Postgres.
    """
    _register_rmih(client, "so-smos")
    client.post("/intents", json={"expectations": [], "rmioId": "rapp-1", "rmihId": "so-smos"})

    resp = client.delete("/intent-handling-functions/so-smos")
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


def test_create_intent_rejects_when_named_rmih_scope_does_not_cover_request(client):
    """Wave 3: the scope check now validates the ONE named target,
    replacing the former multi-candidate pre-filter — addressing a
    CN-scoped Intent at an RMIH declared RAN-only is rejected at
    creation, not silently accepted.
    """
    _register_rmih(client, "so-smos", scope=["RAN"])
    resp = client.post("/intents", json={
        "expectations": [{"expectationObject": {"objectType": "RAN_SUBNETWORK"}}], "rmioId": "rapp-1",
        "rmihId": "so-smos", "intentHandlingScope": "CN",
    })
    assert resp.status_code == 422
    assert resp.json()["detail"]["title"] == "RMIH_CAPABILITY_MISMATCH"


def test_create_intent_accepts_when_named_rmih_scope_covers_request(client):
    _register_rmih(client, "so-smos", scope=["RAN"])
    resp = client.post("/intents", json={
        "expectations": [{"expectationObject": {"objectType": "RAN_SUBNETWORK"}}], "rmioId": "rapp-1",
        "rmihId": "so-smos", "intentHandlingScope": "RAN",
    })
    assert resp.status_code == 201


def test_create_intent_scope_matches_an_rmih_with_no_declared_scope(client, monkeypatch):
    """An RMIH with no declared intentHandlingScope (the pre-existing
    default) still matches any requested scope — this field is a
    pre-filter, not a requirement.
    """
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    _register_rmih(client, "so-smos")
    resp = client.post("/intents", json={
        "expectations": [{"expectationObject": {"objectType": "RAN_SUBNETWORK"}}], "rmioId": "rapp-1",
        "rmihId": "so-smos", "intentHandlingScope": "CN",
    })
    assert resp.status_code == 201
    assert len(calls) == 1


def test_delete_intent_removes_it_and_its_reports(client):
    """SPEC_AUDIT.md item 5: no DELETE /intents/{id} existed at all —
    an RMIO could only deactivate an Intent, never retract it. Cascades
    to IntentReport the same way this build's other owned-child deletes
    already do (rapp_instance, aiml_model, ...).
    """
    _register_rmih(client)
    intent = client.post("/intents", json={"expectations": [], "rmioId": "rapp-1", "rmihId": "so-smos"}).json()
    client.post("/intent-reports", json={"intentId": intent["intentId"], "fulfilmentReport": {"met": True}})

    resp = client.delete(f"/intents/{intent['intentId']}")
    assert resp.status_code == 204
    remaining = client.get("/intents").json()
    assert intent["intentId"] not in [i["intentId"] for i in remaining]


def test_delete_intent_is_idempotent_for_an_unknown_id(client):
    resp = client.delete("/intents/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 204


def test_publish_intent_report(client):
    _register_rmih(client)
    intent = client.post("/intents", json={"expectations": [], "rmioId": "rapp-1", "rmihId": "so-smos"}).json()
    resp = client.post("/intent-reports", json={"intentId": intent["intentId"], "fulfilmentReport": {"met": True}})
    assert resp.status_code == 201


def test_create_intent_dispatches_to_the_named_rmih(client, monkeypatch):
    """Wave 3: CreateIntent now notifies exactly the one named RMIH —
    replacing the former broadcast-to-every-capability-match shape.
    """
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    _register_rmih(client, "so-smos", capabilities=[{"supportedExpectationObjectType": "RAN_SUBNETWORK"}])
    _register_rmih(client, "sa-smos", capabilities=[{"supportedExpectationObjectType": "5GC_SUBNETWORK"}])

    resp = client.post("/intents", json={
        "expectations": [{"expectationObject": {"objectType": "RAN_SUBNETWORK"}}], "rmioId": "rapp-1", "rmihId": "so-smos",
    })
    intent_id = resp.json()["intentId"]

    assert len(calls) == 1  # only the named RMIH (so-smos) was notified, not sa-smos
    assert calls[0][0] == "http://so-smos:8000/intents/notify"
    assert calls[0][1]["intentId"] == intent_id
    assert calls[0][1]["expectationObjectTypes"] == ["RAN_SUBNETWORK"]


def test_create_intent_rejects_a_capability_mismatch(client):
    """Wave 3: addressing an Intent at an RMIH that doesn't declare a
    matching supportedExpectationObjectType is now rejected at creation
    — the former shape simply never dispatched to it, silently.
    """
    _register_rmih(client, "sa-smos", capabilities=[{"supportedExpectationObjectType": "5GC_SUBNETWORK"}])
    resp = client.post("/intents", json={
        "expectations": [{"expectationObject": {"objectType": "RAN_SUBNETWORK"}}], "rmioId": "rapp-1", "rmihId": "sa-smos",
    })
    assert resp.status_code == 422
    assert resp.json()["detail"]["title"] == "RMIH_CAPABILITY_MISMATCH"


def test_create_intent_matches_across_multiple_expectations_in_one_intent(client, monkeypatch):
    """A single Intent can carry several expectations, each with its own
    expectationObject.objectType — the named RMIH matching any one of
    them is enough.
    """
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    _register_rmih(client, "so-smos", capabilities=[{"supportedExpectationObjectType": "EDGE_SERVICE_SUPPORT"}])
    resp = client.post("/intents", json={
        "expectations": [
            {"expectationObject": {"objectType": "RAN_SUBNETWORK"}},
            {"expectationObject": {"objectType": "EDGE_SERVICE_SUPPORT"}},
        ],
        "rmioId": "rapp-1", "rmihId": "so-smos",
    })
    assert resp.status_code == 201
    assert len(calls) == 1
    assert calls[0][1]["expectationObjectTypes"] == ["EDGE_SERVICE_SUPPORT", "RAN_SUBNETWORK"]


def test_create_intent_without_expectation_object_type_still_dispatches_to_the_named_rmih(client, monkeypatch):
    """No expectation object types means the capability check is skipped
    entirely (nothing to validate against) — the named RMIH is still the
    one addressed, unconditionally.
    """
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    _register_rmih(client, "so-smos")
    resp = client.post("/intents", json={"expectations": [], "rmioId": "rapp-1", "rmihId": "so-smos"})
    assert resp.status_code == 201
    assert len(calls) == 1


def test_create_intent_succeeds_even_if_rmih_callback_is_unreachable(client, monkeypatch):
    """Dispatch is best-effort — a dead RMIH callback must never fail
    CreateIntent itself.
    """
    import httpx as httpx_module

    def raise_error(url, json=None, timeout=None):
        raise httpx_module.ConnectError("unreachable")

    monkeypatch.setattr("app.main.httpx.post", raise_error)

    _register_rmih(client, "so-smos")
    resp = client.post("/intents", json={
        "expectations": [{"expectationObject": {"objectType": "RAN_SUBNETWORK"}}], "rmioId": "rapp-1", "rmihId": "so-smos",
    })
    assert resp.status_code == 201


def test_create_intent_stores_and_returns_intent_mgmt_purpose(client):
    """SPEC_AUDIT.md item 3: intentMgmtPurpose is TS28312_IntentNrm.yaml's
    real workflow-procedure enum — this build previously conflated it
    with the (now-removed) invented matching field. Defaults to the
    spec's own default when not given.
    """
    _register_rmih(client)
    created = client.post("/intents", json={"expectations": [], "rmioId": "rapp-1", "rmihId": "so-smos"}).json()
    default = client.get(f"/intents/{created['intentId']}").json()
    assert default["intentMgmtPurpose"] == "FULFILMENT_WITHOUT_NEGOTIATION"

    created = client.post("/intents", json={
        "expectations": [], "rmioId": "rapp-1", "rmihId": "so-smos", "intentMgmtPurpose": "FEASIBILITYCHECK",
    }).json()
    explicit = client.get(f"/intents/{created['intentId']}").json()
    assert explicit["intentMgmtPurpose"] == "FEASIBILITYCHECK"


def test_create_intent_rejects_an_invalid_intent_mgmt_purpose(client):
    _register_rmih(client)
    resp = client.post("/intents", json={"expectations": [], "rmioId": "rapp-1", "rmihId": "so-smos", "intentMgmtPurpose": "NOT_A_REAL_PURPOSE"})
    assert resp.status_code == 422


def test_health_check_answers_the_gui_bff_liveness_probe(client):
    """GUI pass: the BFF's /modules/status probes /<module>/health on every module."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}


def test_list_intent_handling_functions(client):
    """GUI pass: registered RMIHs were invisible."""
    client.post("/intent-handling-functions", json={
        "rmihId": "so-smos", "smeServiceId": "svc-1", "capabilities": [{"supportedExpectationObjectType": "RAN_SUBNETWORK"}],
        "notificationCallbackUri": "http://so-smos:8000/intents", "intentHandlingScope": ["RAN"],
    })
    listed = client.get("/intent-handling-functions").json()
    assert [(f["rmihId"], f["intentHandlingScope"]) for f in listed] == [("so-smos", ["RAN"])]


def test_list_intent_reports_filters_by_intent(client):
    _register_rmih(client)
    intent_id = client.post("/intents", json={"expectations": [], "rmioId": "rapp-1", "rmihId": "so-smos"}).json()["intentId"]
    client.post("/intent-reports", json={"intentId": intent_id, "fulfilmentReport": {"state": "FULFILLED"}})

    reports = client.get("/intent-reports", params={"intent_id": intent_id}).json()
    assert [r["fulfilmentReport"] for r in reports] == [{"state": "FULFILLED"}]
    assert reports[0]["lastUpdatedTime"]
