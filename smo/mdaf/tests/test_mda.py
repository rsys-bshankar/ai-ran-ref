"""Tests for MDAF's TS 28.104 MDA NRM resources (Wave 5, app/mda.py).
Run with: pytest smo/mdaf/tests -q
"""

import datetime
import uuid

from test_main import client, db_session_factory  # noqa: F401  (pytest fixtures)

ES = "MDA_ASSISTED_ENERGY_SAVING_ENERGY_SAVING_ANALYSIS"
PM = "PREDICTIONS_PM_DATA"


def _capture_webhooks(monkeypatch):
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None, **kw: calls.append((url, json)))
    return calls


def _pm_report(value, cell="cell-101", **extra):
    return {"mDAOutputs": [{"mDAType": PM, "mDAOutputList": {
        "pmPredictions": [{"pmName": "RRU.PrbUsedDl", "pmPredictedValue": value}]}, "confidenceDegree": 0.9}],
        "managedEntitiesScope": [cell], **extra}


def test_mda_function_capabilities_gate_requests(client):
    function = client.post("/mda-functions", json={"supportedMDACapabilities": [PM], "supportedMDADomain": "RAN"}).json()
    assert function["attributes"]["supportedMDADomain"] == "RAN"
    resp = client.post("/mda-requests", json={"mDAFunctionRef": function["id"], "reportingMethod": "STREAMING",
                                              "requestedMDAOutputs": [{"mDAType": ES}]})
    assert resp.status_code == 422 and resp.json()["detail"]["title"] == "MDA_CAPABILITY_NOT_SUPPORTED"
    resp = client.post("/mda-requests", json={"mDAFunctionRef": function["id"], "reportingMethod": "STREAMING",
                                              "requestedMDAOutputs": [{"mDAType": PM}]})
    assert resp.status_code == 201
    assert client.post("/mda-functions", json={"supportedMDACapabilities": ["NOT_AN_MDA_TYPE"]}).status_code == 422


def test_request_validation(client):
    assert client.post("/mda-requests", json={"reportingMethod": "NOTIFICATION",
                                              "requestedMDAOutputs": [{"mDAType": PM}]}).status_code == 422
    assert client.post("/mda-requests", json={"reportingMethod": "STREAMING", "requestedMDAOutputs": []}).status_code == 422
    assert client.post("/mda-requests", json={"reportingMethod": "STREAMING", "requestedMDAOutputs": [{"mDAType": PM}],
                                              "analyticsScope": {"managedEntitiesScope": ["a"], "areaScope": [{}]}}).status_code == 422
    assert client.post("/mda-requests", json={"reportingMethod": "STREAMING", "requestedMDAOutputs": [{"mDAType": PM}],
                                              "startTime": "2026-01-02T00:00:00Z", "stopTime": "2026-01-01T00:00:00Z"}).status_code == 422


def test_typed_report_outputs_are_validated_per_mda_type(client):
    ok = client.post("/mda-reports", json=_pm_report(3.2))
    assert ok.status_code == 201
    attrs = ok.json()["attributes"]
    assert attrs["reportKind"] == "PREDICTION"
    assert attrs["mDAOutputs"][0]["mDAOutputList"]["pmPredictions"][0]["pmPredictedValue"] == 3.2
    # a PMDataOutput field outside the spec
    bad = client.post("/mda-reports", json={"mDAOutputs": [{"mDAType": PM, "mDAOutputList": {"notAField": 1}}]})
    assert bad.status_code == 422
    # an MDAType with no typed output takes MDAOutputEntry pairs only
    bad = client.post("/mda-reports", json={"mDAOutputs": [{"mDAType": ES, "mDAOutputList": {"x": 1}}]})
    assert bad.status_code == 422
    ok = client.post("/mda-reports", json={"mDAOutputs": [{"mDAType": ES, "mDAOutputList": [
        {"mDAOutputIEName": "energySavingCandidate", "mDAOutputIEValue": "cell-101"}]}]})
    assert ok.json()["attributes"]["reportKind"] == "ANALYTICS"


def test_notification_request_receives_matching_reports_only(client, monkeypatch):
    calls = _capture_webhooks(monkeypatch)
    request_id = client.post("/mda-requests", json={
        "reportingMethod": "NOTIFICATION", "reportingTarget": "http://es-rapp:8080/mda",
        "requestedMDAOutputs": [{"mDAType": PM}], "analyticsScope": {"managedEntitiesScope": ["cell-101"]}}).json()["id"]
    client.post("/mda-reports", json=_pm_report(3.0, cell="cell-202"))   # other cell
    client.post("/mda-reports", json={"mDAOutputs": [{"mDAType": ES, "mDAOutputList": []}]})  # other type
    report = client.post("/mda-reports", json=_pm_report(3.0)).json()
    assert [url for url, _ in calls] == ["http://es-rapp:8080/mda"]
    assert calls[0][1]["notificationType"] == "notifyMDAReport" and calls[0][1]["mDARequestRef"] == request_id
    assert report["attributes"]["deliveredToRequestRefList"] == [request_id]
    listed = client.get("/mda-reports", params={"mda_request_id": request_id}).json()
    assert [r["id"] for r in listed["items"]] == [report["id"]]


def test_ie_threshold_filter_is_edge_triggered(client, monkeypatch):
    calls = _capture_webhooks(monkeypatch)
    client.post("/mda-requests", json={
        "reportingMethod": "NOTIFICATION", "reportingTarget": "http://es-rapp/mda",
        "requestedMDAOutputs": [{"mDAType": PM, "mDAOutputIEFilters": [{"mDAOutputIEName": "RRU.PrbUsedDl", "threshold": [
            {"monitoredMDAOutputIE": "RRU.PrbUsedDl", "thresholdDirection": "UP", "thresholdValue": 15, "hysteresis": 2}]}]}]})
    for value in (4, 16, 17, 10, 16):
        client.post("/mda-reports", json=_pm_report(value))
    # crosses up at 16; 17 stays above; 10 drops below the band; 16 crosses again
    assert len(calls) == 2


def test_filter_value_and_time_window(client, monkeypatch):
    calls = _capture_webhooks(monkeypatch)
    past = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=2)).isoformat()
    ended = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)).isoformat()
    client.post("/mda-requests", json={"reportingMethod": "NOTIFICATION", "reportingTarget": "http://old/mda",
                                       "requestedMDAOutputs": [{"mDAType": ES}], "startTime": past, "stopTime": ended})
    client.post("/mda-requests", json={"reportingMethod": "NOTIFICATION", "reportingTarget": "http://filtered/mda",
                                       "requestedMDAOutputs": [{"mDAType": ES, "mDAOutputIEFilters": [
                                           {"mDAOutputIEName": "decision", "filterValue": "SLEEP"}]}]})
    for decision in ("NO_CHANGE", "SLEEP"):
        client.post("/mda-reports", json={"mDAOutputs": [{"mDAType": ES, "mDAOutputList": [
            {"mDAOutputIEName": "decision", "mDAOutputIEValue": decision}]}]})
    assert [url for url, _ in calls] == ["http://filtered/mda"]
    active = {r["attributes"]["reportingTarget"]: r["attributes"]["active"] for r in client.get("/mda-requests").json()["items"]}
    assert active == {"http://old/mda": False, "http://filtered/mda": True}


def test_file_reporting_method_and_download(client, monkeypatch):
    calls = _capture_webhooks(monkeypatch)
    client.post("/mda-requests", json={"reportingMethod": "FILE", "reportingTarget": "http://collector/files",
                                       "requestedMDAOutputs": [{"mDAType": PM}]})
    report = client.post("/mda-reports", json=_pm_report(2.0)).json()
    assert calls[0][1]["notificationType"] == "notifyFileReady"
    location = calls[0][1]["fileInfoList"][0]["fileLocation"]
    assert location == f"/mdaf/mda-reports/{report['id']}/file"
    downloaded = client.get(f"/mda-reports/{report['id']}/file")
    assert downloaded.headers["content-disposition"].startswith("attachment")
    assert downloaded.json()["attributes"]["mDAReportID"] == report["id"]


def test_report_answering_one_request_and_legacy_report_matching(client, monkeypatch):
    calls = _capture_webhooks(monkeypatch)
    a = client.post("/mda-requests", json={"reportingMethod": "NOTIFICATION", "reportingTarget": "http://a/mda",
                                           "requestedMDAOutputs": [{"mDAType": PM}]}).json()["id"]
    client.post("/mda-requests", json={"reportingMethod": "NOTIFICATION", "reportingTarget": "http://b/mda",
                                       "requestedMDAOutputs": [{"mDAType": PM}]})
    report = client.post("/mda-reports", json=_pm_report(1.0, mDARequestRef=a)).json()
    assert report["attributes"]["mDARequestRef"] == a
    assert [url for url, _ in calls] == ["http://a/mda"]
    # a legacy producer-push report under the same analytics type matches both
    calls.clear()
    client.post("/reports", params={"analytics_type": PM}, json={"output": {"RRU.PrbUsedDl": 3}, "input_sources": []})
    assert sorted(url for url, _ in calls) == ["http://a/mda", "http://b/mda"]
    legacy = client.get("/mda-reports", params={"mda_type": PM, "report_kind": "ANALYTICS"}).json()["items"][0]
    assert legacy["attributes"]["mDAOutputs"][0]["mDAOutputList"] == [{"mDAOutputIEName": "RRU.PrbUsedDl", "mDAOutputIEValue": 3}]


def test_drift_report_is_forwarded_to_the_models_mlmf_subscriptions(client, monkeypatch):
    model_id = str(uuid.uuid4())
    posted = []

    class Resp:
        def __init__(self, status_code, payload=None):
            self.status_code, self._payload = status_code, payload

        def json(self):
            return self._payload

    def fake_get(self, path, **kw):
        if path == "/aimgf/mlmf/subscriptions":
            assert kw["params"] == {"model_id": model_id}
            return Resp(200, {"items": [{"subscriptionId": "sub-1"}]})
        return Resp(200)

    monkeypatch.setattr("app.main.R1Client.get", fake_get)
    monkeypatch.setattr("app.main.R1Client.post", lambda self, path, json=None, **kw: posted.append((path, json)) or Resp(200))
    report = client.post("/mda-reports", json={"reportKind": "DRIFT", "mDAOutputs": [{"mDAType": "CORRELATION_ANALYTICS_TRAINING_DATA_ANALYSIS",
        "mDAOutputList": [{"mDAOutputIEName": "mLModelRef", "mDAOutputIEValue": model_id},
                          {"mDAOutputIEName": "accuracy", "mDAOutputIEValue": 0.61}]}]})
    assert report.json()["attributes"]["reportKind"] == "DRIFT"
    assert posted == [("/aimgf/mlmf/subscriptions/sub-1/reports", {"accuracy": 0.61})]


def test_unknown_objects_404(client):
    for path in ("mda-functions", "mda-requests", "mda-reports"):
        resp = client.get(f"/{path}/{uuid.uuid4()}")
        assert resp.status_code == 404 and resp.json()["detail"]["title"] == "NRM_OBJECT_NOT_FOUND"
    assert client.post("/mda-reports", json=_pm_report(1.0, mDARequestRef=str(uuid.uuid4()))).status_code == 404
