"""Tests for Intent Service (formerly Policy Management & Info SMOS —
see app/main.py's own module docstring; Policy Mgmt LLD sections 1, 3).
Run with: pytest smo/intent-service/tests -q

Wave 3: CreateIntent requires consumer-side RMIH selection — every test
registers a real IntentHandlingFunction first and names it via `rmihId`.
Wave 6: Intents, IntentHandlingFunctions and IntentReports are strict
TS 28.312 (agreed: every caller migrated) — the helpers below build
spec-valid bodies, and the tests at the end cover the family validation,
feasibility, conflict and report-delivery behaviour.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from smo_shared.db import Base, get_session
from smo_shared.testing import make_test_engine

from app import models as intent_models
from app.main import app


@pytest.fixture
def client():
    engine = make_test_engine()
    Base.metadata.create_all(engine, tables=[
        cls.__table__ for cls in vars(intent_models).values()
        if isinstance(cls, type) and issubclass(cls, Base) and cls is not Base and cls.__module__ == intent_models.__name__
    ])
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


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeRappMgmt:
    """A minimal double for rApp Mgmt's own GET /instances/{id} — enough
    to drive request_autonomy_dispatch's cross-module autonomyMode/
    regionScope read exactly as the real service would.
    """

    def __init__(self):
        self.instances: dict[str, dict] = {}

    def add_instance(self, instance_id=None, autonomy_mode="SHADOW", region_scope=None) -> uuid.UUID:
        instance_id = instance_id or uuid.uuid4()
        self.instances[str(instance_id)] = {"instanceId": str(instance_id), "autonomyMode": autonomy_mode, "regionScope": region_scope}
        return instance_id

    def get(self, path, **kw):
        instance_id = path.rsplit("/", 1)[-1]
        instance = self.instances.get(instance_id)
        return FakeResponse(200, instance) if instance is not None else FakeResponse(
            404, {"detail": {"type": "about:blank", "title": "RAPP_INSTANCE_NOT_FOUND", "status": 404, "detail": "no such instance"}}
        )


@pytest.fixture
def rapp_mgmt(monkeypatch):
    fake = FakeRappMgmt()
    monkeypatch.setattr("app.main.R1Client.get", lambda self, path, **kw: fake.get(path, **kw))
    return fake


@pytest.fixture
def webhooks(monkeypatch):
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))
    return calls


# ---------------------------------------------------------------- spec-valid builders

TARGETS = {
    "RAN_SUBNETWORK": ("RANEnergyConsumption", "IS_LESS_THAN", 500),
    "5GC_SUBNETWORK": ("MaxNumberofPDUsessions", "IS_LESS_THAN", 100),
    "EDGE_SERVICE_SUPPORT": ("maxNumberofUEs", "IS_LESS_THAN", 100),
    "RADIO_SERVICE": ("NumberofUEs", "IS_LESS_THAN", 100),
}


def _target(object_type="RAN_SUBNETWORK"):
    name, condition, value = TARGETS[object_type]
    return {"targetName": name, "targetCondition": condition, "targetValueRange": value}


def _expectation(object_type="RAN_SUBNETWORK", expectation_id="e1", instance=None, targets=None):
    obj = {"objectType": object_type}
    if instance:
        obj["objectInstance"] = instance
    return {"expectationId": expectation_id, "expectationVerb": "DELIVER", "expectationObject": obj,
            "expectationTargets": targets if targets is not None else [_target(object_type)]}


def _intent(rmih="so-smos", expectations=None, rmio="rapp-1", **extra):
    return {"userLabel": "test intent", "intentExpectations": expectations or [_expectation()],
            "intentReportControl": [{"observationPeriod": 60}], "rmioId": rmio, "rmihId": rmih, **extra}


def _capability(object_type="RAN_SUBNETWORK", target_names=None):
    names = target_names if target_names is not None else [TARGETS[object_type][0]]
    return {"intentHandlingCapabilityId": f"cap-{object_type}", "supportedExpectationObjectType": object_type,
            "supportedExpectationTargetInfoList": [{"supportedTargetName": n} for n in names]}


def _register_rmih(client, rmih_id="so-smos", capabilities=None, scope=None, callback=None, negotiation=None):
    body = {
        "rmihId": rmih_id, "smeServiceId": f"svc-{rmih_id}",
        "intentHandlingCapabilityList": capabilities if capabilities is not None else [_capability()],
        "notificationDestination": callback or f"http://{rmih_id}:8000/intents/notify",
    }
    if scope is not None:
        body["intentHandlingScope"] = scope
    if negotiation is not None:
        body["supportedNegotiationFunctionalities"] = negotiation
    resp = client.post("/intent-handling-functions", json=body)
    assert resp.status_code == 201, resp.text
    return rmih_id


def _fulfilment(status="FULFILLED"):
    return {"intentFulfilmentInfo": {"fulfilmentStatus": status}}


# ---------------------------------------------------------------- Intent lifecycle

def test_create_and_query_intent(client):
    _register_rmih(client)
    created = client.post("/intents", json=_intent(intentPriority=5)).json()
    fetched = client.get(f"/intents/{created['intentId']}").json()
    assert fetched["intentPriority"] == 5
    assert fetched["intentAdminState"] == "ACTIVATED"
    assert fetched["rmihId"] == "so-smos"
    assert fetched["attributes"]["intentExpectations"][0]["expectationId"] == "e1"
    assert fetched["attributes"]["intentReportReference"] == created["intentReportReference"]


def test_create_intent_for_unknown_rmih_is_404(client):
    resp = client.post("/intents", json=_intent(rmih="no-such-rmih"))
    assert resp.status_code == 404


def test_query_unknown_intent_is_404(client):
    """An Intent can legitimately vanish (its RMIH was deregistered —
    ON DELETE CASCADE), so GET must answer 404 rather than crash."""
    resp = client.get(f"/intents/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_update_admin_state_only_allowed_by_creator(client):
    """Policy Mgmt LLD section 1: RMIO-only."""
    _register_rmih(client)
    created = client.post("/intents", json=_intent()).json()

    denied = client.patch(f"/intents/{created['intentId']}/admin-state", json={"newState": "DEACTIVATED", "requesterId": "rapp-2"})
    assert denied.status_code == 409

    allowed = client.patch(f"/intents/{created['intentId']}/admin-state", json={"newState": "DEACTIVATED", "requesterId": "rapp-1"})
    assert allowed.status_code == 200
    assert allowed.json()["intentAdminState"] == "DEACTIVATED"
    assert client.patch(f"/intents/{created['intentId']}/admin-state",
                        json={"newState": "PAUSED", "requesterId": "rapp-1"}).status_code == 422
    assert client.patch(f"/intents/{uuid.uuid4()}/admin-state",
                        json={"newState": "ACTIVATED", "requesterId": "rapp-1"}).status_code == 404


def test_query_intents_filters_by_admin_state(client):
    _register_rmih(client)
    client.post("/intents", json=_intent())
    two = client.post("/intents", json=_intent(rmio="rapp-2")).json()
    client.patch(f"/intents/{two['intentId']}/admin-state", json={"newState": "DEACTIVATED", "requesterId": "rapp-2"})

    active = client.get("/intents", params={"admin_state": "ACTIVATED"}).json()["items"]
    assert len(active) == 1


def test_delete_intent_removes_it(client):
    _register_rmih(client)
    intent = client.post("/intents", json=_intent()).json()
    client.post("/intent-reports", json={"intentReference": intent["intentId"], "intentFulfilmentReport": _fulfilment()})

    resp = client.delete(f"/intents/{intent['intentId']}")
    assert resp.status_code == 204
    remaining = client.get("/intents").json()["items"]
    assert intent["intentId"] not in [i["intentId"] for i in remaining]


def test_delete_intent_is_idempotent_for_an_unknown_id(client):
    resp = client.delete("/intents/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 204


def test_create_intent_stores_and_returns_intent_mgmt_purpose(client):
    """SPEC_AUDIT.md item 3: intentMgmtPurpose is the spec's
    workflow-procedure enum, defaulting to FULFILMENT_WITHOUT_NEGOTIATION."""
    _register_rmih(client)
    created = client.post("/intents", json=_intent()).json()
    assert client.get(f"/intents/{created['intentId']}").json()["intentMgmtPurpose"] == "FULFILMENT_WITHOUT_NEGOTIATION"

    created = client.post("/intents", json=_intent(intentMgmtPurpose="FEASIBILITYCHECK")).json()
    assert client.get(f"/intents/{created['intentId']}").json()["intentMgmtPurpose"] == "FEASIBILITYCHECK"


def test_create_intent_rejects_an_invalid_intent_mgmt_purpose(client):
    _register_rmih(client)
    resp = client.post("/intents", json=_intent(intentMgmtPurpose="NOT_A_REAL_PURPOSE"))
    assert resp.status_code == 422


def test_health_check_answers_the_gui_bff_liveness_probe(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}


# ---------------------------------------------------------------- IntentHandlingFunction

def test_register_intent_handling_function_rejects_external_rapp_caller(client):
    """D-SEC-POLICY-1, unchanged: external callers (rApp UUIDs) may never hold an rmihId."""
    resp = client.post("/intent-handling-functions", json={
        "rmihId": "550e8400-e29b-41d4-a716-446655440000", "smeServiceId": "svc-1",
        "intentHandlingCapabilityList": [_capability()], "notificationDestination": "http://so-smos:8000/intents/notify",
    })
    assert resp.status_code == 409
    assert resp.json()["detail"]["title"] == "SERVICE_NAME_CONFLICT"


def test_register_intent_handling_function_validates_spec_capabilities(client):
    ok = client.post("/intent-handling-functions", json={
        "rmihId": "so-smos", "smeServiceId": "svc-1", "intentHandlingCapabilityList": [_capability()],
        "notificationDestination": "http://so-smos:8000/intents/notify", "intentHandlingScope": ["RAN"],
        "supportedNegotiationFunctionalities": ["FEASIBILITY_CHECK"],
    })
    assert ok.status_code == 201
    assert ok.json()["intentHandlingScope"] == ["RAN"]
    assert ok.json()["attributes"]["supportedNegotiationFunctionalities"] == ["FEASIBILITY_CHECK"]
    for bad in ([{"supportedExpectationObjectType": "RAN_SUBNETWORK"}],      # missing required id/targets
                [{**_capability(), "supportedExpectationObjectType": "SUBNETWORK"}],  # not a supported object type
                []):
        resp = client.post("/intent-handling-functions", json={
            "rmihId": "sa-smos", "smeServiceId": "svc-2", "intentHandlingCapabilityList": bad,
            "notificationDestination": "http://sa-smos:8000/intents/notify"})
        assert resp.status_code == 422, bad
    invalid_scope = client.post("/intent-handling-functions", json={
        "rmihId": "sa-smos", "smeServiceId": "svc-2", "intentHandlingCapabilityList": [_capability()],
        "notificationDestination": "http://sa-smos:8000/intents/notify", "intentHandlingScope": ["NOT_A_REAL_SCOPE"]})
    assert invalid_scope.status_code == 422


def test_deregister_intent_handling_function_symmetric_with_register(client):
    _register_rmih(client, "sa-smos")
    resp = client.delete("/intent-handling-functions/sa-smos")
    assert resp.status_code == 204


def test_deregister_intent_handling_function_of_an_intent_still_succeeds(client):
    """The real ON DELETE CASCADE is verified against live Postgres (SQLite
    doesn't enforce FKs here); this only proves the call doesn't error."""
    _register_rmih(client, "so-smos")
    client.post("/intents", json=_intent())
    resp = client.delete("/intent-handling-functions/so-smos")
    assert resp.status_code == 204


def test_list_intent_handling_functions(client):
    _register_rmih(client, scope=["RAN"])
    listed = client.get("/intent-handling-functions").json()["items"]
    assert [(f["rmihId"], f["intentHandlingScope"]) for f in listed] == [("so-smos", ["RAN"])]
    assert listed[0]["attributes"]["intentHandlingCapabilityList"][0]["supportedExpectationObjectType"] == "RAN_SUBNETWORK"


# ---------------------------------------------------------------- capability / scope / dispatch

def test_create_intent_rejects_when_named_rmih_scope_does_not_cover_request(client):
    _register_rmih(client, "so-smos", scope=["RAN"])
    resp = client.post("/intents", json=_intent(intentHandlingScope="CN"))
    assert resp.status_code == 422
    assert resp.json()["detail"]["title"] == "RMIH_CAPABILITY_MISMATCH"


def test_create_intent_accepts_when_named_rmih_scope_covers_request(client):
    _register_rmih(client, "so-smos", scope=["RAN"])
    assert client.post("/intents", json=_intent(intentHandlingScope="RAN")).status_code == 201


def test_create_intent_scope_matches_an_rmih_with_no_declared_scope(client, webhooks):
    _register_rmih(client, "so-smos")
    resp = client.post("/intents", json=_intent(intentHandlingScope="CN"))
    assert resp.status_code == 201
    assert len(webhooks) == 1


def test_create_intent_dispatches_to_the_named_rmih(client, webhooks):
    _register_rmih(client, "so-smos", capabilities=[_capability("RAN_SUBNETWORK")])
    _register_rmih(client, "sa-smos", capabilities=[_capability("5GC_SUBNETWORK")])

    intent_id = client.post("/intents", json=_intent()).json()["intentId"]

    assert len(webhooks) == 1  # only the named RMIH, not sa-smos; no report recipient configured
    assert webhooks[0][0] == "http://so-smos:8000/intents/notify"
    assert webhooks[0][1]["intentId"] == intent_id
    assert webhooks[0][1]["expectationObjectTypes"] == ["RAN_SUBNETWORK"]


def test_create_intent_rejects_a_capability_mismatch(client):
    _register_rmih(client, "sa-smos", capabilities=[_capability("5GC_SUBNETWORK")])
    resp = client.post("/intents", json=_intent(rmih="sa-smos"))
    assert resp.status_code == 422
    assert resp.json()["detail"]["title"] == "RMIH_CAPABILITY_MISMATCH"


def test_every_expectation_object_type_must_be_supported(client, webhooks):
    """Wave 6 (strict): every expectation's object type needs a capability —
    previously one matching type was enough."""
    _register_rmih(client, capabilities=[_capability("EDGE_SERVICE_SUPPORT")])
    mixed = [_expectation("RAN_SUBNETWORK", "e1"), _expectation("EDGE_SERVICE_SUPPORT", "e2")]
    assert client.post("/intents", json=_intent(expectations=mixed)).status_code == 422

    _register_rmih(client, "sa-smos", capabilities=[_capability("EDGE_SERVICE_SUPPORT"), _capability("RAN_SUBNETWORK")])
    resp = client.post("/intents", json=_intent(rmih="sa-smos", expectations=mixed))
    assert resp.status_code == 201
    assert webhooks[-1][1]["expectationObjectTypes"] == ["EDGE_SERVICE_SUPPORT", "RAN_SUBNETWORK"]


def test_create_intent_succeeds_even_if_rmih_callback_is_unreachable(client, monkeypatch):
    import httpx as httpx_module

    def raise_error(url, json=None, timeout=None):
        raise httpx_module.ConnectError("unreachable")

    monkeypatch.setattr("app.main.httpx.post", raise_error)
    _register_rmih(client, "so-smos")
    assert client.post("/intents", json=_intent()).status_code == 201


# ---------------------------------------------------------------- Wave 6: strict TS 28.312 shape

@pytest.mark.parametrize("bad", [
    {"intentExpectations": []},                                               # minItems 1
    {"intentExpectations": [{"expectationObject": {"objectType": "RAN_SUBNETWORK"}}]},  # no id / targets
    {"intentExpectations": [_expectation(targets=[])]},                       # targets minItems 1
    {"intentReportControl": []},                                              # required
    {"intentReportControl": [{"reportRecipientAddress": "http://x"}]},        # observationPeriod required
    {"intentPriority": 0},
    {"notASpecAttribute": True},
])
def test_create_intent_rejects_non_spec_shapes(client, bad):
    _register_rmih(client)
    assert client.post("/intents", json={**_intent(), **bad}).status_code == 422


def test_user_label_is_required(client):
    _register_rmih(client)
    body = _intent()
    del body["userLabel"]
    assert client.post("/intents", json=body).status_code == 422


def test_family_constraints_on_known_targets_and_contexts(client):
    """RadioNetworkExpectation's AveDLPrbLoad target is IS_LESS_THAN with an
    integer 0..100; its Cell context is IS_ALL_OF. Unknown names are the
    generic ExpectationTarget/Context and only checked structurally."""
    _register_rmih(client, capabilities=[_capability(target_names=["AveDLPrbLoad", "MyVendorTarget"])])

    def post(target, contexts=None):
        exp = _expectation(targets=[target])
        if contexts:
            exp["expectationContexts"] = contexts
        return client.post("/intents", json=_intent(expectations=[exp])).status_code

    assert post({"targetName": "AveDLPrbLoad", "targetCondition": "IS_LESS_THAN", "targetValueRange": 30}) == 201
    assert post({"targetName": "AveDLPrbLoad", "targetCondition": "IS_GREATER_THAN", "targetValueRange": 30}) == 422
    assert post({"targetName": "AveDLPrbLoad", "targetCondition": "IS_LESS_THAN", "targetValueRange": 130}) == 422
    assert post({"targetName": "MyVendorTarget", "targetCondition": "IS_GREATER_THAN", "targetValueRange": "x"}) == 201
    assert post({"targetName": "AveDLPrbLoad", "targetCondition": "IS_LESS_THAN", "targetValueRange": 30},
                [{"contextAttribute": "Cell", "contextCondition": "IS_EQUAL_TO", "contextValueRange": []}]) == 422


def test_infeasible_target_rejected_for_fulfilment_but_reported_for_feasibility_check(client):
    _register_rmih(client, capabilities=[_capability(target_names=["RANEnergyConsumption"])])
    infeasible = _expectation(targets=[{"targetName": "AveDLPrbLoad", "targetCondition": "IS_LESS_THAN", "targetValueRange": 30}])
    resp = client.post("/intents", json=_intent(expectations=[infeasible]))
    assert resp.status_code == 422 and resp.json()["detail"]["title"] == "RMIH_CAPABILITY_MISMATCH"

    created = client.post("/intents", json=_intent(expectations=[infeasible], intentMgmtPurpose="FEASIBILITYCHECK")).json()
    report = client.get(f"/intent-reports/{created['intentReportReference']}").json()["attributes"]
    feasibility = report["intentFeasibilityCheckReport"]
    assert feasibility["feasibilityCheckResult"] == "INFEASIBLE"
    assert feasibility["inFeasibleExpectationInfos"] == [{"expectationId": "e1", "inFeasibleTargets": [{"targetName": "AveDLPrbLoad"}]}]


def test_feasibility_purposes_need_the_negotiation_functionality(client):
    _register_rmih(client, negotiation=["EXPLORATION"])
    assert client.post("/intents", json=_intent(intentMgmtPurpose="FEASIBILITYCHECK")).status_code == 422
    assert client.post("/intents", json=_intent(intentMgmtPurpose="EXPLORATION")).status_code == 201


def test_initial_report_is_received_and_conflicts_are_reported(client):
    _register_rmih(client)
    first = client.post("/intents", json=_intent(expectations=[_expectation(instance="SubNetwork=1")])).json()
    report = client.get(f"/intent-reports/{first['intentReportReference']}").json()["attributes"]
    assert report["intentFulfilmentReport"]["intentFulfilmentInfo"] == {"fulfilmentStatus": "NOT_FULFILLED", "notFullfilledState": "RECEIVED"}
    assert "intentConflictReports" not in report

    other_value = _expectation(instance="SubNetwork=1", targets=[{"targetName": "RANEnergyConsumption",
                                                                 "targetCondition": "IS_LESS_THAN", "targetValueRange": 300}])
    second = client.post("/intents", json=_intent(expectations=[other_value])).json()
    conflicts = client.get(f"/intent-reports/{second['intentReportReference']}").json()["attributes"]["intentConflictReports"]
    assert conflicts[0]["conflictType"] == "TARGET_CONFLICT"
    assert conflicts[0]["conflictingIntent"] == first["intentId"]
    assert conflicts[0]["conflictingTarget"] == "RANEnergyConsumption"


def test_reports_are_delivered_per_intent_report_control(client, webhooks):
    _register_rmih(client)
    control = [{"observationPeriod": 60, "reportRecipientAddress": "http://rapp/reports",
                "expectedReportTypes": ["INTENT_FULFILMENT_REPORT"]},
               {"observationPeriod": 60, "reportRecipientAddress": "http://audit/conflicts",
                "expectedReportTypes": ["INTENT_CONFLICT_REPORT"]}]
    intent = client.post("/intents", json=_intent(intentReportControl=control)).json()
    assert [u for u, _ in webhooks if u.startswith("http://rapp")] == ["http://rapp/reports"]
    assert not [u for u, _ in webhooks if u.startswith("http://audit")]

    webhooks.clear()
    published = client.post("/intent-reports", json={
        "intentReference": intent["intentId"], "intentFulfilmentReport": _fulfilment("FULFILLED")})
    assert published.status_code == 201
    assert [u for u, _ in webhooks] == ["http://rapp/reports"]
    assert webhooks[0][1]["notificationType"] == "notifyIntentReport"
    fetched = client.get(f"/intents/{intent['intentId']}").json()
    assert fetched["attributes"]["intentReportReference"] == published.json()["reportId"]


def test_deactivation_reports_suspended(client):
    _register_rmih(client)
    intent = client.post("/intents", json=_intent()).json()
    updated = client.patch(f"/intents/{intent['intentId']}/admin-state", json={"newState": "DEACTIVATED", "requesterId": "rapp-1"}).json()
    report = client.get(f"/intent-reports/{updated['attributes']['intentReportReference']}").json()["attributes"]
    assert report["intentFulfilmentReport"]["intentFulfilmentInfo"]["notFullfilledState"] == "SUSPENDED"


def test_publish_report_validates_every_report_kind(client):
    _register_rmih(client)
    intent_id = client.post("/intents", json=_intent()).json()["intentId"]
    full = {
        "intentReference": intent_id,
        "intentFulfilmentReport": {"intentFulfilmentInfo": {"fulfilmentStatus": "FULFILLED"}, "expectationFulfilmentResult": [
            {"expectaitonId": "e1", "expectationFulfilmentInfo": {"fulfilmentStatus": "FULFILLED"},
             "targetFulfilmentResults": [{"targetName": "RANEnergyConsumption", "targetFulfilmentInfo": {"fulfilmentStatus": "FULFILLED"},
                                          "targetAchievedValue": 420}]}]},
        "intentConflictReports": [{"conflictId": "c1", "conflictType": "INTENT_CONFLICT"}],
        "intentFeasibilityCheckReport": {"feasibilityCheckResult": "FEASIBLE", "infeasibilityReasons": []},
        "intentExplorationReport": {"expectationExplorationResults": [
            {"expectationId": "e1", "targetExplorationResults": [{"targetName": "RANEnergyConsumption", "targetValueRange": 400}]}],
            "expectationExplorationStatus": "FINISHED"},
        "intentUtilityReports": [{"utilityResultList": [{"utilityFunctionId": "u1", "utilityResult": 0.8}]}],
        "intentFulfilmentNegotiationReport": {"possibleIntentOutcomeList": [
            {"possibleIntentOutcomeId": 1, "intentFulfilmentInfo": {"fulfilmentStatus": "NOT_FULFILLED"}}]},
        "intentDecompositionReport": {"intentDecompositionResults": [{"intentHandlingFunctionID": "sa-smos", "intentID": "x"}]},
    }
    resp = client.post("/intent-reports", json=full)
    assert resp.status_code == 201, resp.text
    assert set(resp.json()["attributes"]) >= {"intentFulfilmentReport", "intentExplorationReport", "intentDecompositionReport"}
    assert client.post("/intent-reports", json={"intentReference": intent_id}).status_code == 422
    assert client.post("/intent-reports", json={"intentReference": intent_id, "intentFulfilmentReport": {
        "intentFulfilmentInfo": {"fulfilmentStatus": "DONE"}}}).status_code == 422
    assert client.post("/intent-reports", json={"intentReference": str(uuid.uuid4()),
                                                "intentFulfilmentReport": _fulfilment()}).status_code == 404

    reports = client.get("/intent-reports", params={"intent_id": intent_id}).json()["items"]
    assert reports[0]["attributes"]["intentUtilityReports"][0]["utilityResultList"][0]["utilityResult"] == 0.8
    assert reports[0]["attributes"]["lastUpdatedTime"]


def test_negotiation_feedback_answers_an_offered_outcome(client):
    _register_rmih(client)
    intent_id = client.post("/intents", json=_intent()).json()["intentId"]
    assert client.post(f"/intents/{intent_id}/negotiation-feedback", json={"referredIntentOutcomeId": 1}).status_code == 404
    client.post("/intent-reports", json={"intentReference": intent_id, "intentFulfilmentNegotiationReport": {
        "possibleIntentOutcomeList": [{"possibleIntentOutcomeId": 1, "intentFulfilmentInfo": {"fulfilmentStatus": "NOT_FULFILLED"}}]}})
    assert client.post(f"/intents/{intent_id}/negotiation-feedback", json={"referredIntentOutcomeId": 9}).status_code == 422
    answered = client.post(f"/intents/{intent_id}/negotiation-feedback", json={"referredIntentOutcomeId": 1, "consumerSatisfactionIndex": 7})
    negotiation = answered.json()["attributes"]["intentFulfilmentNegotiationReport"]
    assert negotiation["intentFulfilmentNegotiationConsumerFeedback"] == {"referredIntentOutcomeId": 1, "consumerSatisfactionIndex": 7}


def test_intent_utility_formula_ioc(client):
    _register_rmih(client)
    formula = client.post("/intent-utility-formulas", json={
        "utilityFunctionId": "energy-vs-throughput", "utilityParameterList": [{"parameterName": "RANEnergyConsumption", "parameterWeight": 0.7}]})
    assert formula.status_code == 201
    formula_id = formula.json()["id"]
    assert formula.json()["attributes"]["utilityScale"] == 1
    intent = client.post("/intents", json=_intent(intentUtilityFormulaRef=formula_id)).json()
    assert client.get(f"/intents/{intent['intentId']}").json()["attributes"]["intentUtilityFormulaRef"] == formula_id
    assert client.post("/intents", json=_intent(intentUtilityFormulaRef=str(uuid.uuid4()))).status_code == 404
    assert client.get("/intent-utility-formulas").json()["total"] == 1
    assert client.delete(f"/intent-utility-formulas/{formula_id}").status_code == 204
    assert client.get(f"/intent-utility-formulas/{formula_id}").status_code == 404


# ---------------------------------------------------------------- OPEN_ITEMS.md section 6.3: rApp Autonomy Modes

def _dispatch(instance_id, rmih="so-smos", expectations=None, **extra):
    return {"instanceId": str(instance_id), "expectations": expectations or [_expectation()], "rmihId": rmih, **extra}


def test_autonomous_dispatch_creates_a_real_intent_immediately(client, rapp_mgmt):
    _register_rmih(client)
    instance_id = rapp_mgmt.add_instance(autonomy_mode="AUTONOMOUS", region_scope={"nodeIds": ["ne-1"]})

    resp = client.post("/autonomy-dispatches", json=_dispatch(instance_id))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "DISPATCHED"
    assert body["autonomyMode"] == "AUTONOMOUS"
    assert body["regionScope"] == {"nodeIds": ["ne-1"]}
    assert body["intentId"] is not None

    intent = client.get(f"/intents/{body['intentId']}").json()
    assert intent["rmihId"] == "so-smos"
    assert intent["rmioId"] == str(instance_id)
    assert intent["userLabel"].startswith("autonomy-dispatch ")


def test_assist_dispatch_awaits_operator_scope_then_resolve_creates_the_intent(client, rapp_mgmt):
    _register_rmih(client)
    instance_id = rapp_mgmt.add_instance(autonomy_mode="ASSIST")

    created = client.post("/autonomy-dispatches", json=_dispatch(instance_id)).json()
    assert created["status"] == "AWAITING_SCOPE"
    assert created["intentId"] is None

    resolved = client.post(f"/autonomy-dispatches/{created['dispatchId']}/resolve", json={"regionScope": {"cellIds": ["c-1"]}}).json()
    assert resolved["status"] == "DISPATCHED"
    assert resolved["regionScope"] == {"cellIds": ["c-1"]}
    assert resolved["intentId"] is not None


def test_resolve_rejects_a_dispatch_not_awaiting_scope(client, rapp_mgmt):
    _register_rmih(client)
    instance_id = rapp_mgmt.add_instance(autonomy_mode="AUTONOMOUS", region_scope={})
    dispatched = client.post("/autonomy-dispatches", json=_dispatch(instance_id)).json()

    resp = client.post(f"/autonomy-dispatches/{dispatched['dispatchId']}/resolve", json={"regionScope": {}})
    assert resp.status_code == 409
    assert resp.json()["detail"]["title"] == "AUTONOMY_DISPATCH_NOT_AWAITING_SCOPE"


def test_shadow_dispatch_never_creates_an_intent(client, rapp_mgmt):
    _register_rmih(client)
    instance_id = rapp_mgmt.add_instance(autonomy_mode="SHADOW")

    body = client.post("/autonomy-dispatches", json=_dispatch(instance_id)).json()
    assert body["status"] == "SHADOWED"
    assert body["intentId"] is None
    assert body["regionScope"] is None
    assert client.get("/intents").json()["total"] == 0


def test_dispatch_expectations_are_strict_ts28312(client, rapp_mgmt):
    _register_rmih(client)
    instance_id = rapp_mgmt.add_instance(autonomy_mode="SHADOW")
    resp = client.post("/autonomy-dispatches", json=_dispatch(instance_id, expectations=[{"target": "latency"}]))
    assert resp.status_code == 422


def test_all_three_modes_notify_the_operator(client, rapp_mgmt, webhooks):
    _register_rmih(client)
    for mode in ["AUTONOMOUS", "ASSIST", "SHADOW"]:
        instance_id = rapp_mgmt.add_instance(autonomy_mode=mode, region_scope={} if mode == "AUTONOMOUS" else None)
        client.post("/autonomy-dispatches", json=_dispatch(instance_id, notificationDestination=f"http://operator/{mode.lower()}"))

    # AUTONOMOUS also notifies the RMIH, and its Intent's reports go to the
    # operator destination too (notifyIntentReport) — only the dispatch
    # notifications are counted here.
    operator_calls = [(url, body) for url, body in webhooks
                      if url.startswith("http://operator/") and "autonomyMode" in body]
    assert sorted(c[0] for c in operator_calls) == ["http://operator/assist", "http://operator/autonomous", "http://operator/shadow"]
    for url, body in operator_calls:
        assert body["autonomyMode"] == url.rsplit("/", 1)[-1].upper()


def test_autonomy_dispatch_without_notification_destination_never_calls_out(client, rapp_mgmt, webhooks):
    _register_rmih(client)
    instance_id = rapp_mgmt.add_instance(autonomy_mode="SHADOW")
    client.post("/autonomy-dispatches", json=_dispatch(instance_id))
    assert webhooks == []


def test_autonomy_dispatch_notification_rejects_a_non_http_scheme(client, rapp_mgmt, webhooks):
    """CodeQL py/full-ssrf: only http(s) destinations are ever called."""
    _register_rmih(client)
    instance_id = rapp_mgmt.add_instance(autonomy_mode="SHADOW")
    resp = client.post("/autonomy-dispatches", json=_dispatch(instance_id, notificationDestination="file:///etc/passwd"))
    assert resp.status_code == 201
    assert webhooks == []


def test_request_autonomy_dispatch_for_unknown_instance_is_404(client, rapp_mgmt):
    _register_rmih(client)
    resp = client.post("/autonomy-dispatches", json=_dispatch(uuid.uuid4()))
    assert resp.status_code == 404
    assert resp.json()["detail"]["title"] == "RAPP_INSTANCE_NOT_FOUND"


def test_autonomous_dispatch_for_unknown_rmih_is_404(client, rapp_mgmt):
    instance_id = rapp_mgmt.add_instance(autonomy_mode="AUTONOMOUS", region_scope={})
    resp = client.post("/autonomy-dispatches", json=_dispatch(instance_id, rmih="no-such-rmih"))
    assert resp.status_code == 404
    assert resp.json()["detail"]["title"] == "INTENT_HANDLING_FUNCTION_NOT_FOUND"


@pytest.mark.parametrize("mode", ["ASSIST", "SHADOW"])
def test_dispatch_validates_rmih_capability_up_front(client, rapp_mgmt, mode):
    """Rejected at creation in every mode — even SHADOW "computes" the
    Intent it would have produced."""
    _register_rmih(client, capabilities=[_capability("RAN_SUBNETWORK")])
    instance_id = rapp_mgmt.add_instance(autonomy_mode=mode)
    resp = client.post("/autonomy-dispatches", json=_dispatch(instance_id, expectations=[_expectation("5GC_SUBNETWORK")]))
    assert resp.status_code == 422
    assert resp.json()["detail"]["title"] == "RMIH_CAPABILITY_MISMATCH"


def test_list_and_get_autonomy_dispatches(client, rapp_mgmt):
    _register_rmih(client)
    instance_id = rapp_mgmt.add_instance(autonomy_mode="SHADOW")
    created = client.post("/autonomy-dispatches", json=_dispatch(instance_id)).json()

    fetched = client.get(f"/autonomy-dispatches/{created['dispatchId']}").json()
    assert fetched["dispatchId"] == created["dispatchId"]

    listed = client.get("/autonomy-dispatches", params={"instance_id": str(instance_id)}).json()["items"]
    assert [d["dispatchId"] for d in listed] == [created["dispatchId"]]

    assert client.get(f"/autonomy-dispatches/{uuid.uuid4()}").status_code == 404


# ---------------------------------------------------------------- Wave 8: ASSIST reject (W8-08) and region scope

def test_assist_dispatch_can_be_rejected_and_then_neither_resolved_nor_rejected_again(client, rapp_mgmt, webhooks):
    _register_rmih(client)
    instance_id = rapp_mgmt.add_instance(autonomy_mode="ASSIST")
    created = client.post("/autonomy-dispatches", json=_dispatch(instance_id, notificationDestination="http://operator/assist")).json()

    rejected = client.post(f"/autonomy-dispatches/{created['dispatchId']}/reject",
                           json={"rejectedBy": "operator-1", "reason": "maintenance window"})
    assert rejected.status_code == 200
    body = rejected.json()
    assert (body["status"], body["rejectedBy"], body["rejectionReason"], body["intentId"]) == ("REJECTED", "operator-1", "maintenance window", None)
    assert [b["status"] for u, b in webhooks if u == "http://operator/assist"] == ["AWAITING_SCOPE", "REJECTED"]
    assert client.get("/intents").json()["total"] == 0

    for path, payload in (("resolve", {"regionScope": {}}), ("reject", {"rejectedBy": "x"})):
        resp = client.post(f"/autonomy-dispatches/{created['dispatchId']}/{path}", json=payload)
        assert resp.status_code == 409 and resp.json()["detail"]["title"] == "AUTONOMY_DISPATCH_NOT_AWAITING_SCOPE"
    assert client.get("/autonomy-dispatches", params={"status": "REJECTED"}).json()["total"] == 1


def test_reject_only_from_awaiting_scope_and_unknown_is_404(client, rapp_mgmt):
    _register_rmih(client)
    shadow = client.post("/autonomy-dispatches", json=_dispatch(rapp_mgmt.add_instance(autonomy_mode="SHADOW"))).json()
    assert client.post(f"/autonomy-dispatches/{shadow['dispatchId']}/reject", json={"rejectedBy": "op"}).status_code == 409
    assert client.post(f"/autonomy-dispatches/{uuid.uuid4()}/reject", json={"rejectedBy": "op"}).status_code == 404


def test_region_scope_is_folded_into_the_dispatched_intent(client, rapp_mgmt):
    """AUTONOMOUS: the instance's pre-configured regionScope; ASSIST: the
    operator's resolve — either becomes the Intent's objectInstance and
    Cell object context."""
    _register_rmih(client)
    auto = rapp_mgmt.add_instance(autonomy_mode="AUTONOMOUS", region_scope={"objectInstance": "gnb-du-01", "cells": ["101", "102"]})
    intent_id = client.post("/autonomy-dispatches", json=_dispatch(auto)).json()["intentId"]
    obj = client.get(f"/intents/{intent_id}").json()["attributes"]["intentExpectations"][0]["expectationObject"]
    assert obj["objectInstance"] == "gnb-du-01"
    assert obj["objectContexts"] == [{"contextAttribute": "Cell", "contextCondition": "IS_ALL_OF",
                                      "contextValueRange": ["101", "102"], "contextInvariant": False}]

    assist = rapp_mgmt.add_instance(autonomy_mode="ASSIST")
    dispatch = client.post("/autonomy-dispatches", json=_dispatch(assist)).json()
    resolved = client.post(f"/autonomy-dispatches/{dispatch['dispatchId']}/resolve",
                           json={"regionScope": {"objectInstance": "gnb-du-02", "cells": ["201"]}}).json()
    obj = client.get(f"/intents/{resolved['intentId']}").json()["attributes"]["intentExpectations"][0]["expectationObject"]
    assert (obj["objectInstance"], obj["objectContexts"][0]["contextValueRange"]) == ("gnb-du-02", ["201"])


def test_an_expectation_naming_its_cells_is_bounded_by_the_region_scope(client, rapp_mgmt):
    """Wave 10.1: an rApp that decided to act on one cell gets exactly that
    cell — the instance's region scope bounds it, never widens it to every
    cell in the region; a cell (or element) outside the region is refused."""
    _register_rmih(client)
    auto = rapp_mgmt.add_instance(autonomy_mode="AUTONOMOUS", region_scope={"objectInstance": "gnb-du-01", "cells": ["101", "102"]})

    def expectation(instance, cells):
        e = _expectation()
        e["expectationObject"] = {**e["expectationObject"], "objectInstance": instance, "objectContexts": [
            {"contextAttribute": "Cell", "contextCondition": "IS_ALL_OF", "contextValueRange": cells}]}
        return e

    intent_id = client.post("/autonomy-dispatches", json=_dispatch(auto, expectations=[expectation("gnb-du-01", ["102", "999"])])).json()["intentId"]
    obj = client.get(f"/intents/{intent_id}").json()["attributes"]["intentExpectations"][0]["expectationObject"]
    assert [c["contextValueRange"] for c in obj["objectContexts"]] == [["102"]]
    for exp in (expectation("gnb-du-01", ["999"]), expectation("gnb-du-09", ["101"])):
        resp = client.post("/autonomy-dispatches", json=_dispatch(auto, expectations=[exp]))
        assert resp.status_code == 422 and "outside the dispatch's regionScope" in resp.json()["detail"]["detail"]
