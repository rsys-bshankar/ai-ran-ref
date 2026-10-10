"""PR-MGT-14.7, MGT-15.6, MGT-15.7: notifications of a failed onboarding, a halted campaign and a failed rollback; a timeout for a software job that never reports;
a rollback in reverse wave order. All of it is opt in: with no subscription nothing is enqueued, without `jobTimeoutSeconds` the sweep never fails a job, and
`rollbackOrder` is "all" (every revert job at once) unless asked."""

import datetime
import uuid

import pytest
from sqlalchemy import select

from smo_shared.outbox import NotificationOutbox

from test_main import client, db_session_factory  # noqa: F401  (pytest fixtures)
from test_waves import ELEMENTS, fleet  # noqa: F401
from test_onboarding import _apply, _register, _template, nf  # noqa: F401
from test_campaigns import _campaign, _complete, _events, _report, _reverts, _run_wave, _step, _view, _wave_jobs

from app.models import SoftwareCampaign

WATCHER = "http://noc.example:9000/events"
OTHER = "http://billing.example:9000/events"


@pytest.fixture(autouse=True)
def no_inline_sending(monkeypatch):
    monkeypatch.setenv("SMO_OUTBOX_INLINE_DRAIN", "false")          # the outbox rows are what is under test, not the network


def _subscribe(client, uri=WATCHER, events=None):
    body = {"callbackUri": uri, **({"events": events} if events is not None else {})}
    resp = client.post("/lifecycle-subscriptions", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _sent(db_session_factory, uri=WATCHER):
    with db_session_factory() as db:
        return [row.payload for row in db.scalars(select(NotificationOutbox).where(NotificationOutbox.destination == uri).order_by(NotificationOutbox.created_at)).all()]


def _age_wave(db_session_factory, cid, seconds):
    """The wave (or rollback step) started `seconds` ago."""
    with db_session_factory() as db:
        db.get(SoftwareCampaign, uuid.UUID(cid)).wave_started_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=seconds)
        db.commit()


def _sweep(client):
    return client.post("/software-campaigns/advance-due").json()["advanced"]


# ---- the subscription (14.7, 15.6)

def test_a_subscription_is_made_listed_and_removed_and_a_bad_destination_or_event_is_refused(client):
    """A subscription is stored with its events de-duplicated, listed oldest first and deleted once (a second delete is a 404); a private destination, an unknown event
    or an unknown field is refused with 422, so a bad watcher fails at the door and not silently later."""
    made = _subscribe(client, events=["CAMPAIGN_HALTED", "CAMPAIGN_HALTED"])
    assert made["callbackUri"] == WATCHER and made["events"] == ["CAMPAIGN_HALTED"] and made["createdAt"]
    all_events = _subscribe(client, OTHER)
    assert all_events["events"] == []
    assert [s["subscriptionId"] for s in client.get("/lifecycle-subscriptions").json()["items"]] == [made["subscriptionId"], all_events["subscriptionId"]]
    assert client.post("/lifecycle-subscriptions", json={"callbackUri": "http://127.0.0.1:9/x"}).status_code == 422            # the SSRF guard, at the door
    assert client.post("/lifecycle-subscriptions", json={"callbackUri": WATCHER, "events": ["NOPE"]}).status_code == 422
    assert client.post("/lifecycle-subscriptions", json={"callbackUri": WATCHER, "bogus": 1}).status_code == 422
    assert client.delete(f"/lifecycle-subscriptions/{made['subscriptionId']}").status_code == 204
    gone = client.delete(f"/lifecycle-subscriptions/{made['subscriptionId']}")
    assert gone.status_code == 404 and gone.json()["detail"]["title"] == "LIFECYCLE_SUBSCRIPTION_NOT_FOUND"
    assert len(client.get("/lifecycle-subscriptions").json()["items"]) == 1


# ---- 14.7 a failed onboarding

def test_a_failed_onboarding_tells_each_subscriber_that_wants_it_and_nobody_else(client, db_session_factory, nf):
    """A FAILED onboarding enqueues one ONBOARDING_FAILED notice per subscriber that wants that event, carrying the element, template, job and detail, and a later
    success tells nobody."""
    _subscribe(client)
    _subscribe(client, OTHER, events=["CAMPAIGN_HALTED"])
    _template(client)
    _register(client)
    nf["fail"] = True
    row = _apply(client).json()
    assert row["status"] == "FAILED"
    [event] = _sent(db_session_factory)
    assert event["eventType"] == "ONBOARDING_FAILED" and event["managedElementRef"] == "ME-1" and event["templateName"] == "du-basic"
    assert event["href"] == "/ran-nf-oam/element-onboarding/ME-1" and event["configJobId"] == row["configJobId"] and event["detail"] == row["detail"] and event["occurredAt"]
    assert _sent(db_session_factory, OTHER) == []
    nf["fail"] = False
    assert _apply(client).json()["status"] == "ONBOARDED"
    assert len(_sent(db_session_factory)) == 1                                  # a success tells nobody


def test_a_baseline_that_stops_the_apply_is_a_failed_onboarding_too(client, db_session_factory, nf):
    """A required software baseline that does not match fails the onboarding before any config job exists, and that failure is announced too (with no job id)."""
    _subscribe(client)
    _template(client, softwareBaseline="2.0", requireBaseline=True)
    _register(client, softwareVersion="1.0")
    assert _apply(client).json()["status"] == "FAILED"
    [event] = _sent(db_session_factory)
    assert "SOFTWARE_BASELINE_MISMATCH" in event["detail"] and event["configJobId"] is None


def test_an_unexpected_error_is_announced_without_its_text(client, db_session_factory, monkeypatch):
    """An unexpected exception while applying is announced by its class name only: the notice must not leak the exception's text to an external webhook."""
    _subscribe(client)
    _template(client)
    _register(client)

    def boom(*a, **kw):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr("app.main._execute_write", boom)
    with pytest.raises(RuntimeError):
        _apply(client)
    [event] = _sent(db_session_factory)
    assert "RuntimeError" in event["detail"] and "disk on fire" not in str(event)


def test_without_a_subscription_a_failed_onboarding_enqueues_nothing(client, db_session_factory, nf):
    """With nobody subscribed a failed onboarding adds no outbox row: the feature is opt in and costs nothing when unused."""
    _template(client)
    _register(client)
    nf["fail"] = True
    assert _apply(client).json()["status"] == "FAILED"
    with db_session_factory() as db:
        assert db.scalars(select(NotificationOutbox)).all() == []


# ---- 15.6 a halted campaign, a failed rollback

def test_a_failed_gate_that_halts_the_campaign_is_announced(client, db_session_factory, fleet):
    """A campaign halted by a failed health gate enqueues one CAMPAIGN_HALTED notice with the campaign, the reason GATE_FAILED, the wave and the link to it."""
    _subscribe(client)
    cid = _campaign(client, waveSize=2)
    _run_wave(client, cid, 1, fail={"ME-2"})
    assert _view(client, cid)["status"] == "HALTED"
    [event] = _sent(db_session_factory)
    assert event["eventType"] == "CAMPAIGN_HALTED" and event["campaignId"] == cid and event["name"] == "r3-upgrade" and event["status"] == "HALTED"
    assert event["reason"] == "GATE_FAILED" and "failed" in event["detail"] and event["wave"] == 1 and event["waveCount"] == 2
    assert event["href"] == f"/ran-nf-oam/software-campaigns/{cid}"


def test_an_operator_halt_is_announced_and_the_routine_pause_between_waves_is_not(client, db_session_factory, fleet):
    """An operator's halt (of a running campaign, or of one held by its wave pause) is announced, but the routine pause between waves and a repeated halt of an
    already halted campaign are not."""
    _subscribe(client, events=["CAMPAIGN_HALTED"])
    paused = _campaign(client, waveSize=2, wavePauseSeconds=3600)
    _run_wave(client, paused, 1)
    assert _view(client, paused)["haltedReason"] == "WAVE_PAUSE"
    assert _sent(db_session_factory) == []                                      # a pause is not news
    client.post(f"/software-campaigns/{paused}/halt", json={"requestedBy": "bob"})              # the operator turns the pause into a hold
    running = _campaign(client, ["ME-3"])
    client.post(f"/software-campaigns/{running}/halt", json={"requestedBy": "carol"})
    events = _sent(db_session_factory)
    assert [(e["campaignId"], e["reason"]) for e in events] == [(paused, "OPERATOR_HALT"), (running, "OPERATOR_HALT")]
    assert events[1]["detail"] == "halted by carol"
    client.post(f"/software-campaigns/{running}/halt", json={"requestedBy": "carol"})            # halting a halted campaign changes nothing and says nothing
    assert len(_sent(db_session_factory)) == 2


def test_a_gate_failure_that_rolls_back_by_itself_announces_only_a_failed_rollback(client, db_session_factory, fleet):
    """When a failed gate starts a rollback itself nothing is announced, and a revert job that then fails announces CAMPAIGN_ROLLBACK_FAILED once; a successful retry adds no notice."""
    _subscribe(client)
    cid = _campaign(client, waveSize=2, onGateFailure="rollback")
    _run_wave(client, cid, 1, fail={"ME-2"})
    assert _view(client, cid)["status"] == "ROLLING_BACK" and _sent(db_session_factory) == []
    _step(client, _reverts(client, cid)[0]["jobId"], ok=False)
    [event] = _sent(db_session_factory)
    assert event["eventType"] == "CAMPAIGN_ROLLBACK_FAILED" and event["status"] == "ROLLBACK_FAILED" and event["detail"] == "1 revert job(s) failed" and event["reason"] == "ROLLBACK_FAILED"
    retry = client.post(f"/software-campaigns/{cid}/rollback", json={"requestedBy": "bob"})
    assert retry.json()["status"] == "ROLLING_BACK"
    _complete(client, [r for r in _reverts(client, cid) if r["status"] == "IN_PROGRESS"][0]["jobId"])
    assert _view(client, cid)["status"] == "ROLLED_BACK" and len(_sent(db_session_factory)) == 1


def test_without_a_subscription_a_halt_enqueues_nothing(client, db_session_factory, fleet):
    """With nobody subscribed a halted campaign adds no outbox row."""
    cid = _campaign(client, waveSize=2)
    _run_wave(client, cid, 1, fail={"ME-1"})
    assert _view(client, cid)["status"] == "HALTED"
    with db_session_factory() as db:
        assert db.scalars(select(NotificationOutbox)).all() == []


# ---- 15.7 a job that never reports

def test_a_campaign_made_without_a_timeout_waits_for_its_jobs_however_long(client, db_session_factory, fleet):
    """A campaign without jobTimeoutSeconds (default rollbackOrder "all") is never touched by the sweep, however old its wave, so existing campaigns keep their behaviour."""
    cid = _campaign(client, waveSize=2)
    assert _view(client, cid)["jobTimeoutSeconds"] is None and _view(client, cid)["rollbackOrder"] == "all"
    _age_wave(db_session_factory, cid, 10 * 86400)
    assert _sweep(client) == [] and _view(client, cid)["status"] == "RUNNING"


def test_a_job_that_never_reports_is_failed_by_the_sweep_and_the_gate_halts_the_campaign(client, db_session_factory, fleet):
    """Once the wave is older than jobTimeoutSeconds the sweep fails the job that never finished (marked timed out, in its phase) and the gate halts the campaign; a late report
    cannot revive the job and the next sweep has nothing to do."""
    _subscribe(client)
    cid = _campaign(client, waveSize=2, jobTimeoutSeconds=600)
    first, second = _wave_jobs(client, cid, 1)
    _complete(client, first["jobId"])
    _step(client, second["jobId"])                                              # reported once (DOWNLOAD), then silence
    _age_wave(db_session_factory, cid, 599)
    assert _sweep(client) == [] and _view(client, cid)["status"] == "RUNNING"          # not yet
    _age_wave(db_session_factory, cid, 601)
    advanced = _sweep(client)
    assert [(a["campaignId"], a["status"], a["haltedReason"]) for a in advanced] == [(cid, "HALTED", "GATE_FAILED")]
    jobs = {j["managedElementRef"]: j for j in _wave_jobs(client, cid, 1)}
    assert jobs["ME-2"]["status"] == "FAILED" and jobs["ME-2"]["phase"] == "INSTALL" and jobs["ME-2"]["timedOut"] is True and "timedOut" not in jobs["ME-1"]
    assert jobs["ME-1"]["status"] == "COMPLETED"
    assert "JOB_TIMED_OUT" in _events(client, cid)
    assert [a["problem"] for a in _report(client, cid)["attention"]] == ["software job timed out in phase INSTALL: the element did not report"]
    assert [e["eventType"] for e in _sent(db_session_factory)] == ["CAMPAIGN_HALTED"]
    late = client.post(f"/software-management-jobs/{second['jobId']}/advance", params={"succeeded": True})
    assert late.status_code == 409 and late.json()["detail"]["title"] == "LIFECYCLE_ILLEGAL_TRANSITION"      # the late report cannot revive it
    assert _sweep(client) == []                                                  # and the sweep has nothing more to do


def test_a_timeout_with_the_rollback_policy_undoes_the_completed_jobs(client, db_session_factory, fleet):
    """A timed-out job under onGateFailure "rollback" starts the rollback of the jobs that completed, and only of those."""
    cid = _campaign(client, waveSize=2, jobTimeoutSeconds=60, onGateFailure="rollback")
    first, _second = _wave_jobs(client, cid, 1)
    _complete(client, first["jobId"])
    _age_wave(db_session_factory, cid, 61)
    assert [a["status"] for a in _sweep(client)] == ["ROLLING_BACK"]
    assert [r["managedElementRef"] for r in _reverts(client, cid)] == ["ME-1"]


def test_a_revert_job_that_never_reports_fails_the_rollback(client, db_session_factory, fleet):
    """The timeout also covers a rollback: revert jobs still running after jobTimeoutSeconds are failed, the campaign ends ROLLBACK_FAILED and that is announced."""
    _subscribe(client, events=["CAMPAIGN_ROLLBACK_FAILED"])
    cid = _campaign(client, waveSize=2, jobTimeoutSeconds=60)
    _run_wave(client, cid, 1)
    _run_wave(client, cid, 2)
    client.post(f"/software-campaigns/{cid}/rollback", json={"requestedBy": "bob"})
    assert len(_reverts(client, cid)) == 4
    _age_wave(db_session_factory, cid, 30)
    assert _sweep(client) == []
    _age_wave(db_session_factory, cid, 61)
    assert [a["status"] for a in _sweep(client)] == ["ROLLBACK_FAILED"]
    assert {r["status"] for r in _reverts(client, cid)} == {"FAILED"}
    assert [e["eventType"] for e in _sent(db_session_factory)] == ["CAMPAIGN_ROLLBACK_FAILED"]


def test_a_sweep_that_loses_a_race_with_a_job_report_leaves_it_to_the_next_sweep(client, db_session_factory, fleet, monkeypatch):
    """When a job report changes the campaign while the sweep expires its jobs (a stale-data conflict), the sweep skips that campaign without an error and the next sweep handles it."""
    cid = _campaign(client, ["ME-1"], jobTimeoutSeconds=60)
    _age_wave(db_session_factory, cid, 120)
    from sqlalchemy.orm.exc import StaleDataError
    from app import lifecycle
    real = lifecycle.expire_jobs
    calls = []

    def conflicted(db, c):
        calls.append(c.campaign_id)
        if len(calls) == 1:
            raise StaleDataError("another request moved it")
        return real(db, c)

    monkeypatch.setattr(lifecycle, "expire_jobs", conflicted)
    assert _sweep(client) == [] and _view(client, cid)["status"] == "RUNNING"
    assert [a["status"] for a in _sweep(client)] == ["HALTED"]


# Table of bodies that must be refused: a timeout of 0, a negative one, one over seven days, and a rollback order that is neither "all" nor "reverse".
@pytest.mark.parametrize("body", [{"jobTimeoutSeconds": 0}, {"jobTimeoutSeconds": -5}, {"jobTimeoutSeconds": 8 * 86400}, {"rollbackOrder": "sideways"}])
def test_a_timeout_or_rollback_order_that_makes_no_sense_is_refused(client, fleet, body):
    """A zero, negative or over-a-week timeout or an unknown rollback order is refused with 422 and no campaign is made."""
    resp = client.post("/software-campaigns", json={"requestedBy": "a", "name": "n", "managedElementRefs": ["ME-1"], **body})
    assert resp.status_code == 422
    assert client.get("/software-campaigns").json()["items"] == []


# ---- 15.7 rollback in reverse wave order

def _finish_all(client, cid, waves):
    for wave in range(1, waves + 1):
        _run_wave(client, cid, wave)
    assert _view(client, cid)["status"] == "COMPLETED"


def _revert(client, cid, ref):
    return next(r for r in _reverts(client, cid) if r["managedElementRef"] == ref and r["status"] == "IN_PROGRESS")


def _in_progress(client, cid):
    return sorted(r["managedElementRef"] for r in _reverts(client, cid) if r["status"] == "IN_PROGRESS")


def test_a_reverse_rollback_undoes_the_last_wave_first_and_the_next_when_it_has_ended(client, fleet):
    """With rollbackOrder "reverse" the revert jobs of the last wave start first, an earlier wave starts only when every revert of the one after it has ended, and the
    event log shows that order."""
    cid = _campaign(client, waveSize=2, rollbackOrder="reverse")
    assert _view(client, cid)["rollbackOrder"] == "reverse"
    _finish_all(client, cid, 2)
    assert client.post(f"/software-campaigns/{cid}/rollback", json={"requestedBy": "bob"}).json()["status"] == "ROLLING_BACK"
    assert _in_progress(client, cid) == ["ME-3", "ME-4"]                          # wave 2 only
    _complete(client, _revert(client, cid, "ME-3")["jobId"])
    assert _in_progress(client, cid) == ["ME-4"] and len(_reverts(client, cid)) == 2        # wave 1 waits for the whole of wave 2
    _complete(client, _revert(client, cid, "ME-4")["jobId"])
    assert _view(client, cid)["status"] == "ROLLING_BACK" and _in_progress(client, cid) == ["ME-1", "ME-2"]
    for r in [r for r in _reverts(client, cid) if r["status"] == "IN_PROGRESS"]:
        _complete(client, r["jobId"])
    done = _report(client, cid)
    assert done["status"] == "ROLLED_BACK" and done["summary"]["reverted"] == 4 and done["attention"] == []
    log = [(e["event"], e["wave"]) for e in done["events"] if e["event"].startswith("ROLLBACK")]
    assert log == [("ROLLBACK_STARTED", 2), ("ROLLBACK_WAVE_STARTED", 2), ("ROLLBACK_WAVE_STARTED", 1), ("ROLLBACK_DONE", 2)]


def test_a_failed_revert_stops_a_reverse_rollback_and_a_retry_goes_on_from_there(client, fleet):
    """A failed revert stops a reverse rollback (ROLLBACK_FAILED) without touching the earlier waves, and rolling back again resumes at the failed element and goes on down the waves."""
    cid = _campaign(client, waveSize=1, rollbackOrder="reverse")
    for wave in range(1, 5):
        _run_wave(client, cid, wave)
    client.post(f"/software-campaigns/{cid}/rollback", json={"requestedBy": "bob"})
    assert _in_progress(client, cid) == ["ME-4"]
    _complete(client, _revert(client, cid, "ME-4")["jobId"])
    assert _in_progress(client, cid) == ["ME-3"]
    _step(client, _revert(client, cid, "ME-3")["jobId"], ok=False)
    stopped = _view(client, cid)
    assert stopped["status"] == "ROLLBACK_FAILED" and len(_reverts(client, cid)) == 2        # waves 2 and 1 were never touched
    retry = client.post(f"/software-campaigns/{cid}/rollback", json={"requestedBy": "bob"})
    assert retry.json()["status"] == "ROLLING_BACK" and _in_progress(client, cid) == ["ME-3"]
    _complete(client, _revert(client, cid, "ME-3")["jobId"])
    assert _in_progress(client, cid) == ["ME-2"]
    _complete(client, _revert(client, cid, "ME-2")["jobId"])
    _complete(client, _revert(client, cid, "ME-1")["jobId"])
    assert _view(client, cid)["status"] == "ROLLED_BACK" and _report(client, cid)["summary"]["reverted"] == 4


def test_a_gate_failure_can_roll_back_in_reverse_and_waves_with_nothing_completed_are_skipped(client, fleet):
    """A gate failure under the "rollback" policy can roll back in reverse, and a wave in which nothing completed (the failed one) has no revert job and is skipped."""
    cid = _campaign(client, waveSize=1, rollbackOrder="reverse", onGateFailure="rollback")
    _run_wave(client, cid, 1)
    _run_wave(client, cid, 2)
    _run_wave(client, cid, 3, fail={"ME-3"})
    assert _view(client, cid)["status"] == "ROLLING_BACK" and _in_progress(client, cid) == ["ME-2"]        # wave 3 failed: nothing of it to undo
    _complete(client, _revert(client, cid, "ME-2")["jobId"])
    assert _in_progress(client, cid) == ["ME-1"]
    _complete(client, _revert(client, cid, "ME-1")["jobId"])
    assert _view(client, cid)["status"] == "ROLLED_BACK"


def test_the_default_rollback_still_starts_every_revert_job_at_once(client, fleet):
    """Without rollbackOrder every revert job starts at once, as before, and no per-wave rollback event is logged."""
    cid = _campaign(client, waveSize=2)
    _finish_all(client, cid, 2)
    client.post(f"/software-campaigns/{cid}/rollback", json={"requestedBy": "bob"})
    assert _in_progress(client, cid) == ELEMENTS
    assert "ROLLBACK_WAVE_STARTED" not in _events(client, cid)
