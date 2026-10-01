"""Tests for SA SMOS's generic O1-CM intent handler (Wave 8, app/o1cm.py).
Run with: pytest smo/sa-smos/tests -q

Intent Service and DME are faked at the R1Client boundary; the real round
trip (Intent Service -> SA SMOS -> DME -> RAN NF OAM -> mock O1 adaptor) is
tests_integration/test_cross_service.py's own O1-CM test.
"""

import uuid

import pytest

from test_main import FakeR1Response, client  # noqa: F401  (pytest fixture)


def _intent(intent_id, targets, instance="gnb-du-01", cells=("101",), admin="ACTIVATED", rmih="sa-smos"):
    obj = {"objectType": "RAN_SUBNETWORK"}
    if instance:
        obj["objectInstance"] = instance
    if cells:
        obj["objectContexts"] = [{"contextAttribute": "Cell", "contextCondition": "IS_ALL_OF", "contextValueRange": list(cells)}]
    return {"intentId": str(intent_id), "rmihId": rmih, "rmioId": "es-rapp", "intentAdminState": admin,
            "attributes": {"intentExpectations": [{"expectationId": "e1", "expectationObject": obj, "expectationTargets": targets}]}}


@pytest.fixture
def platform(monkeypatch):
    """Fake Intent Service + DME: serves intents, records actions/reports."""
    state = {"intents": {}, "posts": [], "deletes": [], "action_status": "COMPLETED"}

    def get(self, path, **kw):
        if path == "/intent-service/intent-handling-functions":
            return FakeR1Response(200, {"items": []})  # unregistered -> handler defaults
        intent = state["intents"].get(path.rsplit("/", 1)[-1])
        return FakeR1Response(200, intent) if intent else FakeR1Response(404, {})

    def post(self, path, json=None, **kw):
        state["posts"].append((path, json))
        if path == "/dme/actions":
            return FakeR1Response(202, {"actionId": str(uuid.uuid4()), "forwardedJobId": str(uuid.uuid4()),
                                        "status": state["action_status"]})
        if path == "/intent-service/intent-reports":
            return FakeR1Response(201, {"reportId": str(uuid.uuid4())})
        return FakeR1Response(201, {})

    monkeypatch.setattr("app.o1cm.R1Client.get", get)
    monkeypatch.setattr("app.o1cm.R1Client.post", post)
    monkeypatch.setattr("app.o1cm.R1Client.delete", lambda self, path, **kw: state["deletes"].append(path) or FakeR1Response(204))
    return state


LOCK = {"targetName": "NRCellDU.administrativeState", "targetCondition": "IS_EQUAL_TO", "targetValueRange": "LOCKED"}


def test_registration_declares_the_cm_targets(client, platform):
    resp = client.post("/o1-cm-handler/registration", json={})
    assert resp.status_code == 201
    path, body = platform["posts"][0]
    assert path == "/intent-service/intent-handling-functions" and body["rmihId"] == "sa-smos"
    names = [t["supportedTargetName"] for t in body["intentHandlingCapabilityList"][0]["supportedExpectationTargetInfoList"]]
    assert names == ["NRCellDU.administrativeState", "CESManagementFunction.energySavingControl",
                     "NRCellRelation.cellIndividualOffset", "CommonBeamformingFunction.digitalTilt",
                     "NRSectorCarrier.configuredMaxTxPower"]
    assert platform["deletes"] == ["/intent-service/intent-handling-functions/sa-smos"]  # idempotent re-register
    assert client.post("/o1-cm-handler/registration", json={"cmTargets": {"noDot": []}}).status_code == 422


def test_enacts_each_cell_and_reports_fulfilled(client, platform):
    intent_id = uuid.uuid4()
    platform["intents"][str(intent_id)] = _intent(intent_id, [LOCK], cells=("101", "102"))
    enactment = client.post("/o1-cm-handler/intents", json={"intentId": str(intent_id)}).json()
    assert enactment["status"] == "FULFILLED"
    action = next(body for path, body in platform["posts"] if path == "/dme/actions")
    assert action["changes"] == [
        {"managedElementRef": "gnb-du-01", "className": "NRCellDU", "attributeChanges": {"administrativeState": "LOCKED"},
         "managedFunctionRef": "NRCellDU=101"},
        {"managedElementRef": "gnb-du-01", "className": "NRCellDU", "attributeChanges": {"administrativeState": "LOCKED"},
         "managedFunctionRef": "NRCellDU=102"}]
    assert action["sourceContext"] == {"intentId": str(intent_id), "expectationId": "e1", "rmioId": "es-rapp"}
    report = next(body for path, body in platform["posts"] if path == "/intent-service/intent-reports")
    result = report["intentFulfilmentReport"]["expectationFulfilmentResult"][0]
    assert result["targetFulfilmentResults"][0]["targetAchievedValue"] == "LOCKED"


def test_failed_config_job_reports_degraded(client, platform):
    platform["action_status"] = "PARTIAL_SUCCESS"
    intent_id = uuid.uuid4()
    platform["intents"][str(intent_id)] = _intent(intent_id, [LOCK])
    enactment = client.post("/o1-cm-handler/intents", json={"intentId": str(intent_id)}).json()
    assert enactment["status"] == "NOT_FULFILLED"
    info = next(b for p, b in platform["posts"] if p == "/intent-service/intent-reports")["intentFulfilmentReport"]["intentFulfilmentInfo"]
    assert info["notFullfilledState"] == "DEGRADED" and "PARTIAL_SUCCESS" in info["notFulfilledReasons"][0]


@pytest.mark.parametrize("target, instance, reason", [
    ({"targetName": "RANEnergyConsumption", "targetCondition": "IS_LESS_THAN", "targetValueRange": 1}, "gnb", "not a CM target"),
    ({**LOCK, "targetCondition": "IS_ONE_OF"}, "gnb", "IS_EQUAL_TO"),
    ({**LOCK, "targetValueRange": "HALF_LOCKED"}, "gnb", "one of"),
    (LOCK, None, "objectInstance"),
])
def test_unenactable_targets_are_reported_not_written(client, platform, target, instance, reason):
    intent_id = uuid.uuid4()
    platform["intents"][str(intent_id)] = _intent(intent_id, [target], instance=instance)
    enactment = client.post("/o1-cm-handler/intents", json={"intentId": str(intent_id)}).json()
    assert enactment["status"] == "NOT_FULFILLED"
    assert reason in enactment["unsupportedTargets"][0]["reason"]
    assert not [p for p, _ in platform["posts"] if p == "/dme/actions"]


def test_deactivated_foreign_and_unknown_intents(client, platform):
    a, b = uuid.uuid4(), uuid.uuid4()
    platform["intents"][str(a)] = _intent(a, [LOCK], admin="DEACTIVATED")
    platform["intents"][str(b)] = _intent(b, [LOCK], rmih="so-smos")
    assert client.post("/o1-cm-handler/intents", json={"intentId": str(a)}).json()["status"] == "SKIPPED"
    assert client.post("/o1-cm-handler/intents", json={"intentId": str(b)}).status_code == 422
    assert client.post("/o1-cm-handler/intents", json={"intentId": str(uuid.uuid4())}).status_code == 404
    assert client.get("/o1-cm-handler/enactments").json()["total"] == 0


def test_a_re_pushed_intent_replays_the_same_action_id(client, platform, monkeypatch):
    """Wave 10.1 (W10-18): the DME action id is derived from the intent and
    expectation; when DME reports the replay IGNORED, the original outcome
    still decides fulfilment."""
    intent_id = uuid.uuid4()
    platform["intents"][str(intent_id)] = _intent(intent_id, [LOCK], cells=("101",))
    client.post("/o1-cm-handler/intents", json={"intentId": str(intent_id)})

    def replay(self, path, json=None, **kw):
        platform["posts"].append((path, json))
        if path == "/dme/actions":
            return FakeR1Response(200, {"actionId": json["actionId"], "status": "IGNORED", "originalStatus": "COMPLETED"})
        return FakeR1Response(201, {"reportId": str(uuid.uuid4())})

    import app.o1cm
    monkeypatch.setattr("app.o1cm.R1Client.post", replay)
    enactment = client.post("/o1-cm-handler/intents", json={"intentId": str(intent_id)}).json()
    ids = [body["actionId"] for path, body in platform["posts"] if path == "/dme/actions"]
    assert len(ids) == 2 and ids[0] == ids[1] == str(uuid.uuid5(app.o1cm.ACTION_ID_NAMESPACE, f"{intent_id}:e1"))
    assert enactment["status"] == "FULFILLED" and enactment["actions"][0]["replayed"] is True


def test_a_cio_target_writes_the_offset_on_each_named_relation(client, platform):
    """Wave 10.2: Cell-context values name the relations (NRCellRelation=<id>)."""
    intent_id = uuid.uuid4()
    cio = {"targetName": "NRCellRelation.cellIndividualOffset", "targetCondition": "IS_EQUAL_TO",
           "targetValueRange": [2, 2, 2, 2, 2, 2]}
    platform["intents"][str(intent_id)] = _intent(intent_id, [cio], cells=("201-202",))
    assert client.post("/o1-cm-handler/intents", json={"intentId": str(intent_id)}).json()["status"] == "FULFILLED"
    action = next(body for path, body in platform["posts"] if path == "/dme/actions")
    assert action["changes"][0]["managedFunctionRef"] == "NRCellRelation=201-202"
    assert action["changes"][0]["attributeChanges"] == {"cellIndividualOffset": [2, 2, 2, 2, 2, 2]}


def test_tilt_and_power_targets_write_each_named_cell(client, platform):
    """Wave 10.3: one integer value per expectation, on the IOC named by the target."""
    intent_id = uuid.uuid4()
    tilt = {"targetName": "CommonBeamformingFunction.digitalTilt", "targetCondition": "IS_EQUAL_TO", "targetValueRange": 70}
    platform["intents"][str(intent_id)] = _intent(intent_id, [tilt], cells=("301",))
    assert client.post("/o1-cm-handler/intents", json={"intentId": str(intent_id)}).json()["status"] == "FULFILLED"
    action = next(body for path, body in platform["posts"] if path == "/dme/actions")
    assert action["changes"][0]["managedFunctionRef"] == "CommonBeamformingFunction=301"
    assert action["changes"][0]["attributeChanges"] == {"digitalTilt": 70}
