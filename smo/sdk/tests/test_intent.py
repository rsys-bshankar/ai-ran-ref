"""Tests of `sdk.intent` (`IntentClient` and `energy_saving_expectation`): that each method calls its Intent Service route with the expected verb, path and body, that the energy-saving template has the expected shape, and that an error status becomes `SdkError`. The autonomy dispatch calls are in `test_wave10_wrappers.py`.

Run with `cd sdk && PYTHONPATH=.:../shared python -m pytest tests/test_intent.py -q`; no network and no database. The `r1` fixture (`conftest.py`) is a recording fake of `R1Client`: every call is kept as `{verb, path, params, json, files}` and answered from `r1.script(...)` (default: 200 with `{}`).
"""

import uuid

import pytest

from smo_sdk._common import SdkError
from smo_sdk.intent import IntentClient


@pytest.fixture
def client(r1):
    """An `IntentClient` over the recording fake."""
    return IntentClient(r1)


def test_create_intent(client, r1):
    """`create_intent` posts the spec-named body with the default report control (60 s observation) and the default management purpose."""
    expectation = {"expectationId": "e1", "expectationObject": {"objectType": "RAN_SUBNETWORK"},
                   "expectationTargets": [{"targetName": "x", "targetCondition": "IS_LESS_THAN", "targetValueRange": 1}]}
    client.create_intent([expectation], "rmih-1", "my intent", intent_priority=2, rmio_id="rmio-1")
    call = r1.calls[0]
    assert call["verb"] == "post"
    assert call["path"] == "/intent-service/intents"
    assert call["json"] == {
        "userLabel": "my intent", "intentExpectations": [expectation], "intentPriority": 2,
        "intentReportControl": [{"observationPeriod": 60}], "rmioId": "rmio-1", "rmihId": "rmih-1",
        "intentMgmtPurpose": "FULFILMENT_WITHOUT_NEGOTIATION",
    }


def test_energy_saving_expectation_template():
    """The energy-saving template carries the Cell context, the RANEnergyConsumption target and a daily 00:00 to 05:00 guarantee period with full-time values."""
    from smo_sdk.intent import energy_saving_expectation
    exp = energy_saving_expectation("SubNetwork=1", cells=[{"nCI": 101}], max_energy_consumption=500)
    assert exp["expectationObject"]["objectContexts"][0]["contextAttribute"] == "Cell"
    assert exp["expectationTargets"] == [{"targetName": "RANEnergyConsumption", "targetCondition": "IS_LESS_THAN", "targetValueRange": 500}]
    assert exp["guaranteePeriods"][0]["contextValueRange"] == {"timeIntervals": [{"intervalStart": "00:00:00Z", "intervalEnd": "05:00:00Z"}]}


def test_get_intent(client, r1):
    """`get_intent` reads one intent by id."""
    intent_id = uuid.uuid4()
    client.get_intent(intent_id)
    assert r1.calls[0] == {"verb": "get", "path": f"/intent-service/intents/{intent_id}", "params": None}


def test_list_intents(client, r1):
    """`list_intents` filters by administrative state."""
    client.list_intents(admin_state="LOCKED")
    assert r1.calls[0] == {"verb": "get", "path": "/intent-service/intents", "params": {"admin_state": "LOCKED"}}


def test_update_intent_admin_state(client, r1):
    """`update_intent_admin_state` patches the state with the requester id."""
    intent_id = uuid.uuid4()
    client.update_intent_admin_state(intent_id, "UNLOCKED", "requester-1")
    assert r1.calls[0]["verb"] == "patch"
    assert r1.calls[0]["path"] == f"/intent-service/intents/{intent_id}/admin-state"
    assert r1.calls[0]["json"] == {"newState": "UNLOCKED", "requesterId": "requester-1"}


def test_delete_intent(client, r1):
    """`delete_intent` deletes the intent by id."""
    intent_id = uuid.uuid4()
    client.delete_intent(intent_id)
    assert r1.calls[0] == {"verb": "delete", "path": f"/intent-service/intents/{intent_id}", "params": None}


def test_publish_intent_report(client, r1):
    """`publish_intent_report` sets `intentReference` from the intent id and passes the report by its spec name."""
    intent_id = uuid.uuid4()
    report = {"intentFulfilmentInfo": {"fulfilmentStatus": "FULFILLED"}}
    client.publish_intent_report(intent_id, intentFulfilmentReport=report)
    call = r1.calls[0]
    assert call["path"] == "/intent-service/intent-reports"
    assert call["json"] == {"intentReference": str(intent_id), "intentFulfilmentReport": report}


def test_list_intent_reports(client, r1):
    """`list_intent_reports` filters by intent id."""
    intent_id = uuid.uuid4()
    client.list_intent_reports(intent_id=intent_id)
    assert r1.calls[0] == {"verb": "get", "path": "/intent-service/intent-reports", "params": {"intent_id": intent_id}}


def test_register_intent_handling_function(client, r1):
    """`register_intent_handling_function` posts the capability list and the notification destination, and leaves the optional scope out."""
    caps = [{"intentHandlingCapabilityId": "c1", "supportedExpectationObjectType": "RAN_SUBNETWORK",
             "supportedExpectationTargetInfoList": [{"supportedTargetName": "RANEnergyConsumption"}]}]
    client.register_intent_handling_function("rmih-1", "sme-svc-1", caps, "http://x/notify")
    call = r1.calls[0]
    assert call["path"] == "/intent-service/intent-handling-functions"
    assert call["json"] == {
        "rmihId": "rmih-1", "smeServiceId": "sme-svc-1", "intentHandlingCapabilityList": caps,
        "notificationDestination": "http://x/notify",
    }


def test_deregister_intent_handling_function(client, r1):
    """`deregister_intent_handling_function` deletes the function by its RMIH id."""
    client.deregister_intent_handling_function("rmih-1")
    assert r1.calls[0] == {"verb": "delete", "path": "/intent-service/intent-handling-functions/rmih-1", "params": None}


def test_list_intent_handling_functions(client, r1):
    """`list_intent_handling_functions` reads the collection."""
    client.list_intent_handling_functions()
    assert r1.calls[0] == {"verb": "get", "path": "/intent-service/intent-handling-functions", "params": None}


def test_raises_sdk_error_on_a_4xx_response(client, r1):
    """A 404 answer is raised as `SdkError` carrying the status code and the body."""
    r1.script(404, {"detail": "no such intent"})
    with pytest.raises(SdkError) as exc_info:
        client.get_intent(uuid.uuid4())
    assert exc_info.value.status_code == 404
    assert exc_info.value.body == {"detail": "no such intent"}
