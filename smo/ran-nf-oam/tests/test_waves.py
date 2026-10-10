"""PR-MGT-5.1..5.5: a CM job in waves, the health gate between them, halt / continue / abort, and the automatic revert."""

import datetime
import uuid

import pytest

from test_main import client, db_session_factory  # noqa: F401  (pytest fixtures)

from app.models import Alarm, ManagedEntity, O1AdaptorEndpoint, WriteConfigJob
from app.netconf_client import EditResult

ELEMENTS = ["ME-1", "ME-2", "ME-3", "ME-4"]


@pytest.fixture
def fleet(db_session_factory, monkeypatch):
    """Four registered elements and a fake NF for each: `values[element]` is its txPower, `fail` the elements whose edit is refused, and
    `on_edit[element]` a callback run when that element is written (to raise an alarm, or change another element's value behind the job's back)."""
    db = db_session_factory()
    for ref in ELEMENTS:
        endpoint = O1AdaptorEndpoint(managed_element_ref=ref, adaptor_uri="http://adaptor:9000/netconf", protocol_support=["NETCONF"], health_status="ACTIVE")
        db.add(endpoint)
        db.flush()
        db.add(ManagedEntity(managed_element_ref=ref, entity_type="O-DU", o1_protocol="NETCONF", o1_adaptor_endpoint_id=endpoint.endpoint_id))
    db.commit()
    db.close()
    state = {"values": {ref: "10" for ref in ELEMENTS}, "fail": set(), "on_edit": {}, "edits": []}

    def read(adaptor_uri, target_ref, message_id, managed_function_ref=None):
        return {"txPower": state["values"][target_ref]}

    def edit(adaptor_uri, target_ref, attribute_changes, message_id, operation="merge", managed_function_ref=None):
        state["edits"].append(target_ref)
        if target_ref in state["fail"]:
            return EditResult(False, "NETCONF_RPC_FAILED", "refused")
        state["values"][target_ref] = str(attribute_changes["txPower"])
        if target_ref in state["on_edit"]:
            state["on_edit"][target_ref]()
        return EditResult(True)

    monkeypatch.setattr("app.main.send_get_config", read)
    monkeypatch.setattr("app.main.send_edit_config", edit)
    state["db"] = db_session_factory
    return state


def _write(client, refs=ELEMENTS, value=20, **extra):
    changes = [{"managedElementRef": ref, "attributeChanges": {"txPower": value}} for ref in refs]
    resp = client.post("/config-jobs", json={"requestedBy": "operator", "scope": "cell", "changes": changes, **extra})
    assert resp.status_code == 202, resp.text
    return resp.json()


def _job(client, job_id):
    return client.get(f"/config-jobs/{job_id}").json()


def _act(client, job_id, action, **body):
    return client.post(f"/config-jobs/{job_id}/{action}", json={"requestedBy": "alice", **body})


def _alarm(fleet, ref, severity="major", hours=1):
    """An alarm raised `hours` from now: positive is after the wave starts (new to the gate), negative was there before it. Put in place before the
    job runs, because the test database is one connection and the request holds it."""
    db = fleet["db"]()
    db.add(Alarm(source_alarm_id=f"a-{uuid.uuid4()}", managed_element_ref=ref, severity=severity,
                 raised_at=datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=hours)))
    db.commit()
    db.close()


def test_a_job_without_wave_settings_is_one_wave(client, fleet):
    """A job with no wave settings is one wave and behaves as a job always did."""
    job = _write(client)
    view = _job(client, job["jobId"])
    assert view["status"] == "COMPLETED" and view["waveCount"] == 1 and view["currentWave"] == 1 and view["waveSize"] is None
    assert {s["wave"] for s in view["subChanges"]} == {1}


def test_the_elements_are_split_into_waves_in_request_order_and_run_wave_by_wave(client, fleet):
    """Elements are cut into waves of `waveSize` in request order, and each wave is dispatched in turn."""
    job = _write(client, waveSize=2)
    assert job["status"] == "COMPLETED" and job["waveCount"] == 2
    view = _job(client, job["jobId"])
    assert [(s["managedElementRef"], s["wave"]) for s in view["subChanges"]] == [("ME-1", 1), ("ME-2", 1), ("ME-3", 2), ("ME-4", 2)]
    assert fleet["edits"] == ELEMENTS and all(v == "20" for v in fleet["values"].values())


def test_every_change_of_one_element_stays_in_its_wave(client, fleet):
    """All changes to one element go in the wave of its first appearance, even when other elements come between them."""
    changes = [{"managedElementRef": "ME-1", "attributeChanges": {"txPower": 20}}, {"managedElementRef": "ME-2", "attributeChanges": {"txPower": 20}},
               {"managedElementRef": "ME-1", "attributeChanges": {"txPower": 21}}]
    resp = client.post("/config-jobs", json={"requestedBy": "operator", "scope": "cell", "changes": changes, "waveSize": 1})
    waves = [(s["managedElementRef"], s["wave"]) for s in _job(client, resp.json()["jobId"])["subChanges"]]
    assert waves == [("ME-1", 1), ("ME-2", 2), ("ME-1", 1)]


def test_a_dry_run_shows_the_waves(client, fleet):
    """A dry run lists the waves it would run and sends nothing."""
    resp = client.post("/config-jobs", json={"requestedBy": "operator", "scope": "cell", "dryRun": True, "waveSize": 3,
                                              "changes": [{"managedElementRef": r, "attributeChanges": {"txPower": 20}} for r in ELEMENTS]})
    assert resp.status_code == 200 and resp.json()["waves"] == [["ME-1", "ME-2", "ME-3"], ["ME-4"]] and fleet["edits"] == []


@pytest.mark.parametrize("extra", [{"waveSize": 0}, {"wavePauseSeconds": -1}, {"gateMaxNewAlarms": -1}, {"onGateFailure": "explode"}])
def test_wave_settings_are_validated(client, fleet, extra):
    """A wave size of zero, a negative pause or gate limit, or an unknown gate-failure action is 422."""
    resp = client.post("/config-jobs", json={"requestedBy": "operator", "scope": "cell", "changes": [
        {"managedElementRef": "ME-1", "attributeChanges": {"txPower": 20}}], **extra})
    assert resp.status_code == 422


def test_a_rejected_sub_change_fails_the_gate_and_halts_before_the_next_wave(client, fleet):
    """A rejected sub-change fails the gate: the job halts with GATE_FAILED after wave 1, wave 2 is never sent and its sub-changes stay PENDING."""
    fleet["fail"].add("ME-2")
    job = _write(client, waveSize=2)
    assert job["status"] == "HALTED" and job["haltedReason"] == "GATE_FAILED" and job["wave"] == 1
    assert fleet["edits"] == ["ME-1", "ME-2"]                                           # wave 2 was not sent
    view = _job(client, job["jobId"])
    assert [s["status"] for s in view["subChanges"]] == ["APPLIED", "REJECTED", "PENDING", "PENDING"]
    assert "1 sub-change(s) of wave 1 were rejected" in view["haltedDetail"]


def test_continue_runs_the_next_wave_and_the_job_ends_with_what_it_did(client, fleet):
    """Continuing a halted job runs the remaining wave, and the job ends PARTIAL_SUCCESS because of the earlier rejection."""
    fleet["fail"].add("ME-2")
    job = _write(client, waveSize=2)
    fleet["fail"].clear()
    resp = _act(client, job["jobId"], "continue")
    assert resp.status_code == 202 and resp.json()["status"] == "PARTIAL_SUCCESS"
    assert fleet["edits"] == ["ME-1", "ME-2", "ME-3", "ME-4"] and _job(client, job["jobId"])["haltedReason"] is None


def test_new_critical_or_major_alarms_on_the_wave_fail_the_gate_unless_within_the_limit(client, fleet):
    """New critical or major alarms on the wave's elements fail the gate beyond `gateMaxNewAlarms`; a minor alarm does not."""
    _alarm(fleet, "ME-2", "minor")
    assert _write(client, waveSize=2)["status"] == "COMPLETED"                                 # a minor alarm is not a gate failure
    _alarm(fleet, "ME-2")
    failed = _write(client, waveSize=2)
    assert failed["haltedReason"] == "GATE_FAILED" and "new critical or major alarm" in _job(client, failed["jobId"])["haltedDetail"]
    assert _write(client, waveSize=2, gateMaxNewAlarms=1)["status"] == "COMPLETED"             # one is within the limit


def test_an_alarm_on_an_element_outside_the_wave_or_from_before_it_is_not_counted(client, fleet):
    """The gate counts only alarms raised since the wave started on that wave's own elements."""
    _alarm(fleet, "ME-1", hours=-1)                                                           # there before the wave started
    _alarm(fleet, "ME-3")                                                                     # new, but ME-3 is in wave 2
    assert _write(client, waveSize=2)["status"] == "COMPLETED"


def test_abort_ends_a_halted_job_with_the_waves_that_have_not_run_rejected(client, fleet):
    """Aborting a halted job rejects the waves that did not run as WAVE_NOT_RUN, ends it from what it did, and an ended job cannot be continued
    (409).
    """
    fleet["fail"].add("ME-2")
    job = _write(client, waveSize=2)
    resp = _act(client, job["jobId"], "abort")
    assert resp.status_code == 200 and resp.json()["status"] == "PARTIAL_SUCCESS"
    view = _job(client, job["jobId"])
    assert [(s["status"], s["rejectionReason"]) for s in view["subChanges"]][2:] == [("REJECTED", "WAVE_NOT_RUN")] * 2
    assert "aborted by alice" in view["haltedDetail"]
    assert _act(client, job["jobId"], "continue").status_code == 409                          # an ended job does not go on


def test_the_pause_holds_the_job_until_it_has_elapsed(client, fleet):
    """A job with a wave pause halts as WAVE_PAUSE with a next-wave time; continuing early is 409 WAVE_PAUSE_NOT_ELAPSED unless forced."""
    job = _write(client, waveSize=2, wavePauseSeconds=3600)
    assert job["status"] == "HALTED" and job["haltedReason"] == "WAVE_PAUSE" and fleet["edits"] == ["ME-1", "ME-2"]
    assert _job(client, job["jobId"])["nextWaveAt"] is not None
    early = _act(client, job["jobId"], "continue")
    assert early.status_code == 409 and "WAVE_PAUSE_NOT_ELAPSED" in early.text
    forced = _act(client, job["jobId"], "continue", force=True)
    assert forced.status_code == 202 and forced.json()["status"] == "COMPLETED"


def _make_due(fleet, job_id):
    """Sets a job's next-wave time to a second ago so the sweep treats its pause as elapsed."""
    db = fleet["db"]()
    row = db.get(WriteConfigJob, uuid.UUID(job_id))
    row.next_wave_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=1)
    db.commit()
    db.close()


def test_advance_due_runs_the_jobs_whose_pause_has_elapsed_and_only_those(client, fleet):
    """The advance-due sweep runs only the jobs whose pause has elapsed."""
    due = _write(client, waveSize=2, wavePauseSeconds=3600)["jobId"]
    waiting = _write(client, waveSize=2, wavePauseSeconds=3600)["jobId"]
    gated = None
    _make_due(fleet, due)
    advanced = client.post("/config-jobs/advance-due").json()["advanced"]
    assert [a["jobId"] for a in advanced] == [due] and advanced[0]["status"] == "COMPLETED"
    assert _job(client, waiting)["status"] == "HALTED" and gated is None


def test_an_operator_halt_stops_a_pause_from_going_on_by_itself(client, fleet):
    """An operator halt turns a pause into OPERATOR_HALT so the sweep leaves it, while an explicit continue still goes on."""
    job = _write(client, waveSize=2, wavePauseSeconds=3600)["jobId"]
    assert _act(client, job, "halt").json()["haltedReason"] == "OPERATOR_HALT"
    _make_due(fleet, job)
    assert client.post("/config-jobs/advance-due").json()["advanced"] == []
    assert _act(client, job, "continue").json()["status"] == "COMPLETED"                      # the operator's own decision to go on


def test_halt_continue_and_abort_need_a_halted_job(client, fleet):
    """Halt, continue and abort are 409 for a job that is not halted and 404 for an unknown job."""
    done = _write(client)["jobId"]
    for action in ("continue", "halt", "abort"):
        assert _act(client, done, action).status_code == 409
        assert _act(client, uuid.uuid4(), action).status_code == 404


def test_a_failed_gate_with_revert_undoes_the_applied_waves_through_a_rollback_job(client, fleet):
    """With `onGateFailure: revert` a failed gate undoes the applied waves through a rollback job: the job ends FAILED, its applied sub-changes are
    REVERTED and the later wave is never sent.
    """
    fleet["fail"].add("ME-2")
    job = _write(client, waveSize=2, onGateFailure="revert")
    assert job["status"] == "FAILED" and fleet["values"]["ME-1"] == "10"                       # ME-1 was written (20) and put back (10)
    assert fleet["edits"] == ["ME-1", "ME-2", "ME-1"]                                          # wave 2 was never sent
    view = _job(client, job["jobId"])
    assert [(s["status"], s["rejectionReason"]) for s in view["subChanges"]] == [
        ("REVERTED", None), ("REJECTED", "NETCONF_RPC_FAILED"), ("REJECTED", "WAVE_NOT_RUN"), ("REJECTED", "WAVE_NOT_RUN")]
    assert view["haltedReason"] == "GATE_FAILED" and "reverted by job" in view["haltedDetail"]
    undo_id = view["haltedDetail"].rsplit(" ", 1)[1]
    undo = _job(client, undo_id)
    assert undo["rollbackOf"] == job["jobId"] and undo["status"] == "COMPLETED" and undo["requestedBy"] == "operator"


def test_a_revert_that_would_overwrite_someone_elses_change_is_refused_and_the_job_halts(client, fleet, monkeypatch):
    """If a value changed behind the job's back, the automatic revert is refused (REVERT_REFUSED) and the job halts, leaving that value alone."""
    fleet["fail"].add("ME-2")
    import app.main as main
    real = main.send_edit_config

    def edit(adaptor_uri, target_ref, *args, **kwargs):
        result = real(adaptor_uri, target_ref, *args, **kwargs)
        if target_ref == "ME-2":
            fleet["values"]["ME-1"] = "99"                    # ME-1 is changed behind the job's back, after wave 1 wrote it
        return result

    monkeypatch.setattr("app.main.send_edit_config", edit)
    job = _write(client, waveSize=2, onGateFailure="revert")
    assert job["status"] == "HALTED" and job["haltedReason"] == "REVERT_REFUSED"
    assert fleet["values"]["ME-1"] == "99"                                                        # nothing was overwritten
    assert "changed since" in _job(client, job["jobId"])["haltedDetail"]
