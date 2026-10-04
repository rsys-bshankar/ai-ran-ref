"""PR-MSG-4: a KPI guard declared with a CM job, checked and (if asked) reverted by the worker once the observation window has passed."""

import datetime
import uuid

import pytest

from test_main import client, db_session_factory  # noqa: F401  (pytest fixtures)
from test_kpi_check import ELEMENTS, _pm, fleet  # noqa: F401  (fixtures: two elements and a fake NF; a PM sample helper)

from app import main, tasks
from app.models import WriteConfigJob

GUARD = {"kpi": "succ", "baselineMinutes": 60, "observationMinutes": 60, "maxRegressionPercent": 10, "revert": True}


@pytest.fixture(autouse=True)
def success_kpi(client):
    client.put("/kpi-definitions/succ", json={"formula": "100 * ok / n", "unit": "%"})


def _guarded(client, fleet, guard=GUARD, refs=ELEMENTS):
    resp = client.post("/config-jobs", json={"requestedBy": "es-rapp", "scope": "cell", "kpiGuard": guard, "changes": [
        {"managedElementRef": r, "attributeChanges": {"txPower": 20}} for r in refs]})
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["jobId"]
    with fleet["factory"]() as db:
        fleet["anchor"] = datetime.datetime.now(datetime.UTC)
        db.get(WriteConfigJob, uuid.UUID(job_id)).schema_validated_at = fleet["anchor"]
        db.commit()
    return job_id


def _run(fleet, minutes_after_job):
    with fleet["factory"]() as db:
        return main.run_due_kpi_guards(db, fleet["anchor"] + datetime.timedelta(minutes=minutes_after_job))


def _job(client, job_id):
    return client.get(f"/config-jobs/{job_id}").json()


def test_the_guard_is_kept_with_the_job_and_a_dry_run_or_an_unknown_kpi_creates_nothing(client, fleet):
    job = _job(client, _guarded(client, fleet))
    assert job["kpiGuard"]["kpi"] == "succ" and job["kpiGuard"]["revert"] is True and job["kpiGuardResult"] is None and job["kpiGuardCheckedAt"] is None
    assert client.post("/config-jobs", json={"requestedBy": "x", "scope": "cell", "kpiGuard": {**GUARD, "kpi": "nope"}, "changes": [
        {"managedElementRef": "ME-1", "attributeChanges": {"txPower": 20}}]}).status_code == 404
    dry = client.post("/config-jobs", json={"requestedBy": "x", "scope": "cell", "dryRun": True, "kpiGuard": GUARD, "changes": [
        {"managedElementRef": "ME-1", "attributeChanges": {"txPower": 20}}]})
    assert dry.status_code == 200 and dry.json()["dryRun"] is True


def test_a_job_without_a_guard_is_never_looked_at(client, fleet):
    client.post("/config-jobs", json={"requestedBy": "x", "scope": "cell", "changes": [{"managedElementRef": "ME-1", "attributeChanges": {"txPower": 20}}]})
    with fleet["factory"]() as db:
        assert main.run_due_kpi_guards(db, datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=1)) == []


def test_nothing_happens_before_the_observation_window_has_passed(client, fleet):
    _guarded(client, fleet)
    for element in ELEMENTS:
        _pm(fleet, element, -30, 95)
        _pm(fleet, element, 10, 50)
    assert _run(fleet, 30) == []


def test_a_regression_is_reverted_once_and_the_answer_stays_on_the_job(client, fleet):
    job_id = _guarded(client, fleet)
    for element in ELEMENTS:
        _pm(fleet, element, -30, 95)
        _pm(fleet, element, 10, 60)                                                          # 95 -> 60: a drop of about 37%
    assert fleet["values"] == {"ME-1": "20", "ME-2": "20"}
    [ran] = _run(fleet, 61)
    assert ran == {"jobId": job_id, "verdict": "REGRESSED", "final": True, "reverted": True}
    assert fleet["values"] == {"ME-1": "10", "ME-2": "10"}                                   # restored
    view = _job(client, job_id)
    result = view["kpiGuardResult"]
    assert result["verdict"] == "REGRESSED" and result["reverted"] is True and result["revertJobId"] and view["kpiGuardCheckedAt"]
    rollback = _job(client, result["revertJobId"])
    assert rollback["rollbackOf"] == job_id and rollback["requestedBy"] == "kpi-guard:es-rapp"
    assert _run(fleet, 120) == []                                                           # final: not checked, not reverted again


def test_a_kpi_that_held_is_ok_and_nothing_is_reverted(client, fleet):
    job_id = _guarded(client, fleet)
    for element in ELEMENTS:
        _pm(fleet, element, -30, 95)
        _pm(fleet, element, 10, 94)
    [ran] = _run(fleet, 61)
    assert ran["verdict"] == "OK" and ran["final"] is True and ran["reverted"] is False
    assert fleet["values"] == {"ME-1": "20", "ME-2": "20"} and _job(client, job_id)["kpiGuardCheckedAt"]


def test_without_revert_a_regression_is_reported_and_left_alone(client, fleet):
    job_id = _guarded(client, fleet, {**GUARD, "revert": False})
    for element in ELEMENTS:
        _pm(fleet, element, -30, 95)
        _pm(fleet, element, 10, 60)
    [ran] = _run(fleet, 61)
    assert ran["verdict"] == "REGRESSED" and ran["reverted"] is False and fleet["values"] == {"ME-1": "20", "ME-2": "20"}
    assert _job(client, job_id)["kpiGuardResult"]["revertJobId"] is None


def test_only_the_regressed_element_is_reverted(client, fleet):
    _guarded(client, fleet)
    _pm(fleet, "ME-1", -30, 95)
    _pm(fleet, "ME-1", 10, 60)
    _pm(fleet, "ME-2", -30, 95)
    _pm(fleet, "ME-2", 10, 95)
    _run(fleet, 61)
    assert fleet["values"] == {"ME-1": "10", "ME-2": "20"}


def test_too_little_data_is_retried_until_the_grace_has_passed_then_final(client, fleet, monkeypatch):
    monkeypatch.setattr(main, "KPI_GUARD_GRACE_MINUTES", 30)
    job_id = _guarded(client, fleet)
    [ran] = _run(fleet, 61)                                                                  # no PM at all
    assert ran["verdict"] == "INSUFFICIENT_DATA" and ran["final"] is False
    view = _job(client, job_id)
    assert view["kpiGuardResult"]["verdict"] == "INSUFFICIENT_DATA" and view["kpiGuardCheckedAt"] is None
    assert _run(fleet, 80)[0]["final"] is False                                              # still within the grace: tried again
    for element in ELEMENTS:                                                                 # the data arrives late
        _pm(fleet, element, -30, 95)
        _pm(fleet, element, 10, 50)
    [ran] = _run(fleet, 85)
    assert ran["verdict"] == "REGRESSED" and ran["final"] is True and ran["reverted"] is True


def test_data_that_never_comes_ends_the_checking(client, fleet, monkeypatch):
    monkeypatch.setattr(main, "KPI_GUARD_GRACE_MINUTES", 30)
    job_id = _guarded(client, fleet)
    assert _run(fleet, 95)[0]["final"] is True                                               # past window + grace
    assert _job(client, job_id)["kpiGuardCheckedAt"] and _run(fleet, 200) == []


def test_a_revert_never_overwrites_a_later_change(client, fleet):
    job_id = _guarded(client, fleet)
    for element in ELEMENTS:
        _pm(fleet, element, -30, 95)
        _pm(fleet, element, 10, 60)
    fleet["values"]["ME-1"] = "99"                                                           # somebody changed it after the job
    [ran] = _run(fleet, 61)
    assert ran["verdict"] == "REGRESSED" and ran["reverted"] is False and ran["final"] is True
    assert fleet["values"]["ME-1"] == "99"                                                   # not forced
    assert "differ" in _job(client, job_id)["kpiGuardResult"]["error"]


def test_a_job_that_did_not_finish_is_not_guarded(client, fleet):
    job_id = _guarded(client, fleet)
    with fleet["factory"]() as db:
        db.get(WriteConfigJob, uuid.UUID(job_id)).status = "FAILED"
        db.commit()
    assert _run(fleet, 61) == []


def test_the_worker_task_runs_the_guards(client, fleet, monkeypatch):
    monkeypatch.setattr(tasks, "SessionLocal", fleet["factory"])
    _guarded(client, fleet)
    assert "run-kpi-guards" in {t.name for t in tasks.TASKS}
    tasks.run_kpi_guards()                                                                   # nothing due yet: runs without error
