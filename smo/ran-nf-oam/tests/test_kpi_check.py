"""AI-10.5: revert on a KPI regression: the KPI before and after a job, per element it changed, and the rollback of the elements where it got worse."""

import datetime
import json

import pytest

from test_main import client, db_session_factory  # noqa: F401  (pytest fixtures)

from app.models import ManagedEntity, O1AdaptorEndpoint, PMFile, WriteConfigJob
from app.netconf_client import EditResult

ELEMENTS = ["ME-1", "ME-2"]
NOW = datetime.datetime.now(datetime.UTC)


@pytest.fixture
def fleet(db_session_factory, monkeypatch):
    db = db_session_factory()
    for ref in ELEMENTS:
        endpoint = O1AdaptorEndpoint(managed_element_ref=ref, adaptor_uri="http://adaptor:9000/netconf", protocol_support=["NETCONF"], health_status="ACTIVE")
        db.add(endpoint)
        db.flush()
        db.add(ManagedEntity(managed_element_ref=ref, entity_type="O-DU", o1_protocol="NETCONF", o1_adaptor_endpoint_id=endpoint.endpoint_id))
    db.commit()
    db.close()
    state = {"values": {ref: "10" for ref in ELEMENTS}, "edits": [], "factory": db_session_factory}

    def read(adaptor_uri, target_ref, message_id, managed_function_ref=None):
        return {"txPower": state["values"][target_ref]}

    def edit(adaptor_uri, target_ref, attribute_changes, message_id, operation="merge", managed_function_ref=None):
        state["edits"].append(target_ref)
        state["values"][target_ref] = str(attribute_changes["txPower"])
        return EditResult(True)

    monkeypatch.setattr("app.main.send_get_config", read)
    monkeypatch.setattr("app.main.send_edit_config", edit)
    return state


def _pm(fleet, element, minutes_from_job, value):
    """A PM sample of the success counter `ok` (out of 100 attempts `n`) `minutes_from_job` after the job (negative: before)."""
    db = fleet["factory"]()
    at = (fleet["anchor"] + datetime.timedelta(minutes=minutes_from_job)).isoformat()
    content = json.dumps({"managedElementRef": element, "counterType": "SUCC", "measurements": [
        {"cellId": "1", "timestamp": at, "values": {"ok": value, "n": 100}}]})
    db.add(PMFile(managed_element_ref=element, counter_type="SUCC", content=content, file_size=len(content)))
    db.commit()
    db.close()


def _job(client, fleet, refs=ELEMENTS):
    resp = client.post("/config-jobs", json={"requestedBy": "es-rapp", "scope": "cell", "changes": [
        {"managedElementRef": r, "attributeChanges": {"txPower": 20}} for r in refs]})
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["jobId"]
    db = fleet["factory"]()
    fleet["anchor"] = datetime.datetime.now(datetime.UTC)
    row = db.get(WriteConfigJob, __import__("uuid").UUID(job_id))
    row.schema_validated_at = fleet["anchor"]                                                  # the moment the change was made, as the check reads it
    db.commit()
    db.close()
    return job_id


def _check(client, job_id, **extra):
    return client.post(f"/config-jobs/{job_id}/kpi-check", json={"requestedBy": "autonomy", "kpi": "succ", **extra})


@pytest.fixture(autouse=True)
def success_kpi(client):
    client.put("/kpi-definitions/succ", json={"formula": "100 * ok / n", "unit": "%"})


def test_a_kpi_that_held_is_ok_and_nothing_is_reverted(client, fleet):
    job = _job(client, fleet)
    for element in ELEMENTS:
        _pm(fleet, element, -30, 95)
        _pm(fleet, element, 10, 94)                                                          # a drop of 1.05%, within the 10% allowed
    answer = _check(client, job, revert=True).json()
    assert answer["verdict"] == "OK" and answer["reverted"] is False and fleet["edits"] == ["ME-1", "ME-2"]
    assert all(e["verdict"] == "OK" and e["baseline"] == 95.0 and e["observed"] == 94.0 for e in answer["elements"])


def test_a_regression_on_one_element_reverts_only_that_element(client, fleet):
    job = _job(client, fleet)
    _pm(fleet, "ME-1", -30, 95)
    _pm(fleet, "ME-1", 10, 60)                                                               # 95 -> 60: a 36.8% drop
    _pm(fleet, "ME-2", -30, 95)
    _pm(fleet, "ME-2", 10, 95)
    answer = _check(client, job, revert=True).json()
    assert answer["verdict"] == "REGRESSED" and answer["reverted"] is True
    by_element = {e["managedElementRef"]: e for e in answer["elements"]}
    assert by_element["ME-1"]["verdict"] == "REGRESSED" and by_element["ME-1"]["changePercent"] == pytest.approx(-36.8421, abs=1e-3)
    assert by_element["ME-2"]["verdict"] == "OK"
    assert fleet["values"] == {"ME-1": "10", "ME-2": "20"}                                   # ME-1 put back, ME-2 left
    undo = client.get(f"/config-jobs/{answer['revertJobId']}").json()
    assert undo["rollbackOf"] == job and undo["requestedBy"] == "autonomy" and undo["status"] == "COMPLETED"


def test_a_regression_without_revert_only_reports(client, fleet):
    job = _job(client, fleet, ["ME-1"])
    _pm(fleet, "ME-1", -30, 95)
    _pm(fleet, "ME-1", 10, 60)
    answer = _check(client, job).json()
    assert answer["verdict"] == "REGRESSED" and answer["reverted"] is False and fleet["values"]["ME-1"] == "20"


def test_the_direction_says_which_way_is_worse(client, fleet):
    client.put("/kpi-definitions/drops", json={"formula": "100 * lost / n"})
    job = _job(client, fleet, ["ME-1"])
    db = fleet["factory"]()
    for minutes, lost in ((-30, 1), (10, 5)):
        content = json.dumps({"measurements": [{"cellId": "1", "timestamp": (fleet["anchor"] + datetime.timedelta(minutes=minutes)).isoformat(),
                                                "values": {"lost": lost, "n": 100}}]})
        db.add(PMFile(managed_element_ref="ME-1", counter_type="DROP", content=content, file_size=len(content)))
    db.commit()
    db.close()
    assert _check(client, job, kpi="drops").json()["verdict"] == "OK"                        # higher is better: a rise from 1 to 5 is an improvement
    assert _check(client, job, kpi="drops", direction="lower").json()["verdict"] == "REGRESSED"


def test_too_little_data_is_never_a_verdict_and_never_a_revert(client, fleet):
    job = _job(client, fleet, ["ME-1"])
    _pm(fleet, "ME-1", -30, 95)                                                              # nothing after the job yet
    answer = _check(client, job, revert=True).json()
    assert answer["verdict"] == "INSUFFICIENT_DATA" and answer["reverted"] is False and fleet["values"]["ME-1"] == "20"
    _pm(fleet, "ME-1", 5, 10)
    assert _check(client, job, revert=True, minSamples=2).json()["verdict"] == "INSUFFICIENT_DATA"      # one sample is fewer than asked for


def test_a_zero_baseline_has_no_relative_change(client, fleet):
    job = _job(client, fleet, ["ME-1"])
    _pm(fleet, "ME-1", -30, 0)
    _pm(fleet, "ME-1", 10, 50)
    answer = _check(client, job).json()
    assert answer["verdict"] == "INSUFFICIENT_DATA" and answer["elements"][0]["reason"] == "BASELINE_ZERO"


def test_a_revert_that_would_overwrite_a_later_change_is_refused_unless_forced(client, fleet):
    job = _job(client, fleet, ["ME-1"])
    _pm(fleet, "ME-1", -30, 95)
    _pm(fleet, "ME-1", 10, 60)
    fleet["values"]["ME-1"] = "33"                                                           # somebody changed it after the job
    refused = _check(client, job, revert=True)
    assert refused.status_code == 409 and "CONFIG_CHANGED_SINCE" in refused.text and fleet["values"]["ME-1"] == "33"
    forced = _check(client, job, revert=True, force=True).json()
    assert forced["reverted"] is True and fleet["values"]["ME-1"] == "10"


def test_unknown_job_and_unknown_kpi(client, fleet):
    assert _check(client, __import__("uuid").uuid4()).status_code == 404
    job = _job(client, fleet, ["ME-1"])
    assert _check(client, job, kpi="nope").status_code == 404
    assert client.post(f"/config-jobs/{job}/kpi-check", json={"requestedBy": "a", "kpi": "succ", "baselineMinutes": 0}).status_code == 422
