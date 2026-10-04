"""AI-10.3: what one config job may do for an rApp: how many managed elements it may touch (blast radius) and how far a value may move (magnitude)."""

import pytest

from test_main import client, db_session_factory  # noqa: F401  (pytest fixtures)
from test_waves import ELEMENTS, fleet  # noqa: F401

from app.models import WriteConfigJob

ES = {"X-R1-Invoker-Id": "es-client"}


def _limit(client, **limits):
    resp = client.put("/rapp-limits/es-client", json=limits)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _write(client, refs=ELEMENTS[:1], value=12, headers=ES, **extra):
    changes = [{"managedElementRef": ref, "attributeChanges": {"txPower": value}} for ref in refs]
    return client.post("/config-jobs", headers=headers, json={"requestedBy": "r", "scope": "cell", "changes": changes, **extra})


def _title(resp):
    return resp.json()["detail"]["title"]


# ---- blast radius

def test_a_job_naming_more_elements_than_allowed_is_refused_before_anything_is_sent(client, fleet):
    _limit(client, maxElementsPerJob=2)
    refused = _write(client, ELEMENTS[:3])
    assert refused.status_code == 403 and _title(refused) == "RAPP_BLAST_RADIUS_EXCEEDED"
    assert "2 managed elements" in refused.json()["detail"]["detail"] and "names 3" in refused.json()["detail"]["detail"]
    assert fleet["edits"] == []
    with fleet["db"]() as db:
        assert db.query(WriteConfigJob).count() == 0


def test_a_job_within_the_blast_radius_goes_through_and_elements_count_once(client, fleet):
    _limit(client, maxElementsPerJob=2)
    assert _write(client, ELEMENTS[:2]).status_code == 202
    twice = client.post("/config-jobs", headers=ES, json={"requestedBy": "r", "scope": "cell", "changes": [
        {"managedElementRef": "ME-1", "attributeChanges": {"txPower": 11}}, {"managedElementRef": "ME-1", "attributeChanges": {"txPower": 12}}]})
    assert twice.status_code == 202


def test_a_blast_radius_alone_does_not_limit_how_many_jobs(client, fleet):
    _limit(client, maxElementsPerJob=1)
    assert [_write(client, value=10 + i % 2).status_code for i in range(5)] == [202] * 5


# ---- magnitude

@pytest.mark.parametrize("value, allowed", [(12, True), (8, True), (10, True), (12.1, False), (7.9, False), (30, False), ("12", True), ("13", False)])
def test_a_value_may_move_by_the_percentage_allowed_and_no_further(client, fleet, value, allowed):
    _limit(client, maxChangePercent=20)
    resp = _write(client, value=value)
    assert (resp.status_code == 202) is allowed, resp.text
    if not allowed:
        assert resp.status_code == 403 and _title(resp) == "RAPP_MAGNITUDE_EXCEEDED"
        assert fleet["edits"] == [] and fleet["values"]["ME-1"] == "10"


def test_the_refusal_says_what_would_have_moved_and_by_how_much(client, fleet):
    _limit(client, maxChangePercent=20)
    detail = _write(client, value=15).json()["detail"]["detail"]
    assert "ME-1 txPower: 10 to 15 is a change of 50.0%" in detail and "20% at most" in detail


def test_a_value_of_zero_may_only_stay_zero(client, fleet):
    _limit(client, maxChangePercent=50)
    fleet["values"]["ME-1"] = "0"
    assert _write(client, value=0).status_code == 202
    refused = _write(client, value=1)
    assert refused.status_code == 403 and "more than any" in refused.json()["detail"]["detail"]


def test_what_is_not_a_number_is_not_measured_but_what_cannot_be_read_is_refused(client, fleet, monkeypatch):
    _limit(client, maxChangePercent=10)
    named = client.post("/config-jobs", headers=ES, json={"requestedBy": "r", "scope": "cell", "changes": [
        {"managedElementRef": "ME-1", "attributeChanges": {"txPower": "ON"}}]})
    assert named.status_code == 202                                          # nothing numeric to measure
    fleet["values"]["ME-1"] = "10"
    monkeypatch.setattr("app.main.send_get_config", lambda *a, **k: None)     # the NF does not answer
    unreadable = _write(client, value=10)
    assert unreadable.status_code == 403 and _title(unreadable) == "RAPP_MAGNITUDE_EXCEEDED"
    assert "cannot be checked" in unreadable.json()["detail"]["detail"]
    monkeypatch.setattr("app.main.send_get_config", lambda *a, **k: {"somethingElse": "1"})
    absent = _write(client, value=10)
    assert absent.status_code == 403 and "not a number" in absent.json()["detail"]["detail"]


def test_one_change_over_the_limit_refuses_the_whole_job(client, fleet):
    _limit(client, maxChangePercent=20)
    changes = [{"managedElementRef": "ME-1", "attributeChanges": {"txPower": 11}}, {"managedElementRef": "ME-2", "attributeChanges": {"txPower": 40}}]
    resp = client.post("/config-jobs", headers=ES, json={"requestedBy": "r", "scope": "cell", "changes": changes})
    assert resp.status_code == 403 and "ME-2" in resp.json()["detail"]["detail"]
    assert fleet["edits"] == [] and fleet["values"]["ME-1"] == "10"


def test_a_dry_run_is_checked_too(client, fleet):
    _limit(client, maxElementsPerJob=1, maxChangePercent=20)
    assert _write(client, ELEMENTS[:2], dryRun=True).status_code == 403
    assert _write(client, value=40, dryRun=True).status_code == 403
    assert _write(client, value=11, dryRun=True).status_code == 200


# ---- who is limited

def test_other_callers_and_unidentified_ones_are_not_limited(client, fleet):
    _limit(client, maxElementsPerJob=1, maxChangePercent=1)
    assert _write(client, ELEMENTS[:3], value=50, headers={"X-R1-Invoker-Id": "someone-else"}).status_code == 202
    assert _write(client, ELEMENTS[:3], value=60, headers={}).status_code == 202


def test_undoing_a_change_is_not_limited(client, fleet):
    job = _write(client, value=20).json()["jobId"] if _limit(client, maxConfigJobsPerHour=10) else None     # a 100% change, before any magnitude limit
    _limit(client, maxChangePercent=5, maxElementsPerJob=1)
    resp = client.post(f"/config-jobs/{job}/rollback", json={"requestedBy": "ops"}, headers=ES)
    assert resp.status_code == 202 and fleet["values"]["ME-1"] == "10"


# ---- the limits themselves

def test_a_put_replaces_the_whole_set_and_needs_at_least_one(client, fleet):
    first = _limit(client, maxConfigJobsPerHour=3, maxElementsPerJob=2, maxChangePercent=15.5)
    assert (first["maxConfigJobsPerHour"], first["maxElementsPerJob"], first["maxChangePercent"]) == (3, 2, 15.5)
    second = _limit(client, maxElementsPerJob=4)
    assert (second["maxConfigJobsPerHour"], second["maxElementsPerJob"], second["maxChangePercent"]) == (None, 4, None)
    assert client.get("/rapp-limits/es-client").json()["maxElementsPerJob"] == 4
    assert client.put("/rapp-limits/es-client", json={}).status_code == 422


@pytest.mark.parametrize("body", [{"maxElementsPerJob": 0}, {"maxElementsPerJob": 10_001}, {"maxChangePercent": 0}, {"maxChangePercent": -5},
                                  {"maxChangePercent": 10_001}, {"maxChangePercent": "lots"}, {"maxElementsPerJob": 1.5}])
def test_the_values_are_validated(client, body):
    assert client.put("/rapp-limits/x", json=body).status_code == 422
