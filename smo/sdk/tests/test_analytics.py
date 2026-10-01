import uuid

import pytest

from smo_sdk._common import SdkError
from smo_sdk.analytics import AnalyticsClient


@pytest.fixture
def client(r1):
    return AnalyticsClient(r1)


def test_register_producer(client, r1):
    type_id = uuid.uuid4()
    client.register_producer("prod-1", "RAN.Coverage", [type_id], {"type": "object"})
    assert r1.calls[0] == {
        "verb": "post", "path": "/ran-analytics/producers",
        "params": {"producer_id": "prod-1", "analytics_type": "RAN.Coverage", "mda_type": None},
        "files": None,
        "json": {"dme_input_types": [str(type_id)], "output_schema": {"type": "object"}},
    }


def test_register_producer_with_an_explicit_mda_type(client, r1):
    client.register_producer("prod-1", "RAN.Coverage", [], {}, mda_type="PREDICTIONS_PM_DATA")
    assert r1.calls[0]["params"]["mda_type"] == "PREDICTIONS_PM_DATA"


def test_list_producers(client, r1):
    client.list_producers(analytics_type="RAN.Coverage")
    assert r1.calls[0] == {
        "verb": "get", "path": "/ran-analytics/producers",
        "params": {"analytics_type": "RAN.Coverage", "producer_id": None},
    }


def test_publish_report(client, r1):
    source_id = uuid.uuid4()
    client.publish_report("RAN.Coverage", {"value": 1}, input_sources=[source_id], scope={"cell": "a"})
    call = r1.calls[0]
    assert call["verb"] == "post"
    assert call["path"] == "/mdaf/reports"
    assert call["params"] == {"analytics_type": "RAN.Coverage"}
    assert call["json"] == {"output": {"value": 1}, "input_sources": [str(source_id)], "scope": {"cell": "a"}}


def test_query_reports(client, r1):
    client.query_reports(analytics_type="RAN.Coverage")
    assert r1.calls[0] == {"verb": "get", "path": "/mdaf/reports", "params": {"analytics_type": "RAN.Coverage"}}


def test_subscribe_sends_scope_and_threshold_info_body(client, r1):
    """Wave 3: the body is now {"notificationDestination": ..., "scope": ...,
    "thresholdInfo": ...} — `scope` stopped being the bare unwrapped body
    once `thresholdInfo` (TS28.104 ThresholdInfo) needed a body field
    alongside it, and `notification_destination` moved from a query param
    into this same body (cross-cutting standardization, Subscriptions) —
    unified with every other subscription-shaped resource's own
    notificationDestination body field. `analytics_type`/`requested_by`
    are plain strings and still go in query params.
    Confirmed against MDAF's own live OpenAPI schema before writing this."""
    client.subscribe("RAN.Coverage", "rapp-1", notification_destination="http://x/notify", scope={"cell": "a"},
                      threshold_info=[{"monitoredMDAOutputIE": "utilization", "thresholdDirection": "UP", "thresholdValue": 0.8}])
    assert r1.calls[0] == {
        "verb": "post", "path": "/mdaf/subscriptions",
        "params": {"analytics_type": "RAN.Coverage", "requested_by": "rapp-1"},
        "files": None,
        "json": {"notificationDestination": "http://x/notify", "scope": {"cell": "a"},
                  "thresholdInfo": [{"monitoredMDAOutputIE": "utilization", "thresholdDirection": "UP", "thresholdValue": 0.8}]},
    }


def test_unsubscribe(client, r1):
    sub_id = uuid.uuid4()
    client.unsubscribe(sub_id)
    assert r1.calls[0] == {"verb": "delete", "path": f"/mdaf/subscriptions/{sub_id}", "params": None}


def test_list_subscriptions(client, r1):
    client.list_subscriptions(requested_by="rapp-1")
    assert r1.calls[0] == {
        "verb": "get", "path": "/mdaf/subscriptions",
        "params": {"analytics_type": None, "requested_by": "rapp-1"},
    }


def test_raises_sdk_error_on_a_4xx_response(client, r1):
    r1.script(422, {"detail": "bad scope"})
    with pytest.raises(SdkError) as exc_info:
        client.subscribe("RAN.Coverage", "rapp-1", scope={"cell": "a"})
    assert exc_info.value.status_code == 422
    assert exc_info.value.body == {"detail": "bad scope"}


# ---------------------------------------------------------------- Wave 5: TS 28.104 MDA NRM

def test_create_mda_request_drops_unset_fields(client, r1):
    client.create_mda_request([{"mDAType": "PREDICTIONS_PM_DATA"}], "NOTIFICATION",
                              reporting_target="http://rapp/mda", analytics_scope={"managedEntitiesScope": ["cell-1"]})
    assert r1.calls[0] == {"verb": "post", "path": "/mdaf/mda-requests", "params": None, "files": None, "json": {
        "requestedMDAOutputs": [{"mDAType": "PREDICTIONS_PM_DATA"}], "reportingMethod": "NOTIFICATION",
        "reportingTarget": "http://rapp/mda", "analyticsScope": {"managedEntitiesScope": ["cell-1"]}}}


def test_publish_mda_report(client, r1):
    client.publish_mda_report([{"mDAType": "PREDICTIONS_PM_DATA", "mDAOutputList": {}}], managed_entities=["cell-1"],
                              report_kind="PREDICTION")
    assert r1.calls[0]["path"] == "/mdaf/mda-reports"
    assert r1.calls[0]["json"]["managedEntitiesScope"] == ["cell-1"] and r1.calls[0]["json"]["reportKind"] == "PREDICTION"


def test_get_prediction_returns_the_named_pm_prediction(client, r1):
    r1.script(200, {"items": [{"id": "r1", "attributes": {"mDAOutputs": [{"mDAType": "PREDICTIONS_PM_DATA", "mDAOutputList": {
        "pmPredictions": [{"pmName": "RRU.PrbUsedDl", "pmPredictedValue": 2.8}]}}]}}]})
    assert client.get_prediction("cell-1", pm_name="RRU.PrbUsedDl") == {"pmName": "RRU.PrbUsedDl", "pmPredictedValue": 2.8}
    assert r1.calls[0]["params"] == {"report_kind": "PREDICTION", "managed_entity": "cell-1"}  # unset filters left out
    assert client.get_prediction("cell-1", pm_name="Other") is None


def test_get_prediction_none_without_reports(client, r1):
    r1.script(200, {"items": []})
    assert client.get_prediction("cell-1") is None
