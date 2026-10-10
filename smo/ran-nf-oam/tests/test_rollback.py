"""PR-MGT-1.5..1.8: the diff of two snapshots, rollback as a new write job, the changed-since guard, and snapshot retention."""

import datetime
import uuid

import pytest

from test_main import _make_me, client, db_session_factory  # noqa: F401  (pytest fixtures)
from test_cm_history import _history, _write, nf  # noqa: F401

from app.models import CMSnapshot, WriteConfigJob

CELL = {"managedElementRef": "ME-1", "managedFunctionRef": "NRCellDU=1"}


def _cell(attributes, **extra):
    return {**CELL, "attributeChanges": attributes, **extra}


def _rollback(client, job_id, **body):
    return client.post(f"/config-jobs/{job_id}/rollback", json={"requestedBy": "alice", **body})


def _job(client, job_id):
    return client.get(f"/config-jobs/{job_id}").json()


def test_a_rollback_restores_the_recorded_values_as_a_new_job_that_names_its_actor(client, db_session_factory, nf):
    """A rollback writes the recorded before values as a new job that names the original (`rollbackOf`) and the actor, and leaves the original untouched.
    """
    _make_me(db_session_factory)
    original = _write(client, [_cell({"administrativeState": "LOCKED"})]).json()["jobId"]
    assert nf["objects"][("ME-1", "NRCellDU=1")]["administrativeState"] == "LOCKED"
    resp = _rollback(client, original)
    assert resp.status_code == 202 and resp.json()["rollbackOf"] == original and resp.json()["forced"] is False
    assert nf["objects"][("ME-1", "NRCellDU=1")]["administrativeState"] == "UNLOCKED"
    undo = _job(client, resp.json()["jobId"])
    assert undo["status"] == "COMPLETED" and undo["requestedBy"] == "alice" and undo["rollbackOf"] == original and undo["rollbackForced"] is False
    assert _job(client, original)["rollbackOf"] is None


def test_two_writes_of_one_job_are_undone_in_reverse_order(client, db_session_factory, nf):
    """A job that wrote the same attribute twice is undone in reverse order, so the value ends as it was before the first write."""
    _make_me(db_session_factory)
    original = _write(client, [_cell({"administrativeState": "LOCKED"}), _cell({"administrativeState": "SHUTTING_DOWN"})]).json()["jobId"]
    assert _rollback(client, original).status_code == 202
    assert nf["objects"][("ME-1", "NRCellDU=1")]["administrativeState"] == "UNLOCKED"          # not LOCKED: the first write's own before image


def test_a_changed_value_refuses_the_rollback_unless_forced(client, db_session_factory, nf):
    """If the value changed after the job, the rollback is 409 CONFIG_CHANGED_SINCE; a dry run lists the difference; `force` restores anyway and marks the job forced.
    """
    _make_me(db_session_factory)
    original = _write(client, [_cell({"administrativeState": "LOCKED"})]).json()["jobId"]
    nf["objects"][("ME-1", "NRCellDU=1")]["administrativeState"] = "SHUTTING_DOWN"              # somebody else changed it since
    refused = _rollback(client, original)
    assert refused.status_code == 409 and "CONFIG_CHANGED_SINCE" in refused.text and "SHUTTING_DOWN" in refused.text
    assert nf["objects"][("ME-1", "NRCellDU=1")]["administrativeState"] == "SHUTTING_DOWN"
    plan = _rollback(client, original, dryRun=True).json()
    assert plan["status"] == "CHANGED_SINCE" and plan["changedSince"] == [
        {"managedElementRef": "ME-1", "managedFunctionRef": "NRCellDU=1", "attribute": "administrativeState", "expected": "LOCKED",
         "actual": "SHUTTING_DOWN"}]
    forced = _rollback(client, original, force=True)
    assert forced.status_code == 202 and forced.json()["forced"] is True
    assert nf["objects"][("ME-1", "NRCellDU=1")]["administrativeState"] == "UNLOCKED"
    assert _job(client, forced.json()["jobId"])["rollbackForced"] is True


def test_a_dry_run_changes_nothing(client, db_session_factory, nf):
    """A rollback dry run returns the plan (VALIDATED) and writes nothing."""
    _make_me(db_session_factory)
    original = _write(client, [_cell({"administrativeState": "LOCKED"})]).json()["jobId"]
    plan = _rollback(client, original, dryRun=True)
    assert plan.status_code == 200 and plan.json()["status"] == "VALIDATED" and plan.json()["changes"][0]["attributeChanges"] == {"administrativeState": "UNLOCKED"}
    assert nf["objects"][("ME-1", "NRCellDU=1")]["administrativeState"] == "LOCKED"


def test_a_deleted_object_is_created_again_and_a_created_one_is_deleted(client, db_session_factory, nf):
    """Undoing a delete creates the object again from its before image, and undoing a create deletes it."""
    _make_me(db_session_factory)
    deleted = _write(client, [{**CELL, "operation": "delete"}]).json()["jobId"]
    assert ("ME-1", "NRCellDU=1") not in nf["objects"]
    assert _rollback(client, deleted).status_code == 202
    assert nf["objects"][("ME-1", "NRCellDU=1")] == {"administrativeState": "UNLOCKED", "cellLocalId": "7"}
    created = _write(client, [{"managedElementRef": "ME-1", "managedFunctionRef": "NRCellDU=2", "operation": "create", "attributeChanges": {"cellLocalId": "8"}}]).json()["jobId"]
    assert ("ME-1", "NRCellDU=2") in nf["objects"]
    assert _rollback(client, created).status_code == 202
    assert ("ME-1", "NRCellDU=2") not in nf["objects"]


def test_what_cannot_be_restored_is_refused_with_the_reason(client, db_session_factory, nf):
    """An attribute that had no value before the write, or a write whose before image could not be read, makes the rollback 422 ROLLBACK_NOT_POSSIBLE with the reason.
    """
    _make_me(db_session_factory)
    new_attribute = _write(client, [_cell({"brandNew": "x"})]).json()["jobId"]
    resp = _rollback(client, new_attribute)
    assert resp.status_code == 422 and "ROLLBACK_NOT_POSSIBLE" in resp.text and "brandNew had no value before" in resp.text
    nf["read_fails"] = True
    no_image = _write(client, [_cell({"cellLocalId": "9"})]).json()["jobId"]
    nf["read_fails"] = False
    resp = _rollback(client, no_image)
    assert resp.status_code == 422 and "no before image" in resp.text


def test_an_unknown_job_and_a_job_that_applied_nothing(client, db_session_factory, nf):
    """An unknown job is 404, and a job that applied nothing cannot be rolled back (422)."""
    _make_me(db_session_factory)
    assert _rollback(client, uuid.uuid4()).status_code == 404
    from app.netconf_client import EditResult
    nf["edit_result"] = EditResult(False, "NETCONF_RPC_FAILED")
    refused = _write(client, [_cell({"cellLocalId": "9"})]).json()["jobId"]
    resp = _rollback(client, refused)
    assert resp.status_code == 422 and "applied nothing" in resp.text


def test_a_rollback_goes_through_the_same_access_gate_as_any_write(client, db_session_factory, nf):
    """A rollback is held to the access gate of any write: entire-RAN scope without an MSAC role is 403 and nothing is written."""
    _make_me(db_session_factory)
    original = _write(client, [_cell({"administrativeState": "LOCKED"})]).json()["jobId"]
    resp = _rollback(client, original, accessScope="entire-RAN")                      # entire-RAN needs a named msacRole
    assert resp.status_code == 403 and "MSAC_ACCESS_DENIED" in resp.text
    assert nf["objects"][("ME-1", "NRCellDU=1")]["administrativeState"] == "LOCKED"


# ---------------------------------------------------------------- MGT-1.5: the diff of two snapshots


def test_the_diff_of_two_snapshots_lists_changed_and_one_sided_attributes(client, db_session_factory, nf):
    """The snapshot diff lists attributes whose value changed and those present in only one of the two snapshots."""
    _make_me(db_session_factory)
    _write(client, [_cell({"administrativeState": "LOCKED"})])
    _write(client, [_cell({"administrativeState": "SHUTTING_DOWN", "cellLocalId": "9"})])
    older, newer = sorted(_history(client)["items"], key=lambda i: i["createdAt"])
    diff = client.get("/managed-entities/ME-1/config-history/diff", params={"from_snapshot": older["snapshotId"], "to_snapshot": newer["snapshotId"]}).json()
    assert diff["changed"] == [{"attribute": "administrativeState", "from": "LOCKED", "to": "SHUTTING_DOWN"}]
    assert diff["onlyInFrom"] == {} and diff["onlyInTo"] == {"cellLocalId": "9"}


def test_a_diff_needs_two_snapshots_of_the_same_object(client, db_session_factory, nf):
    """Snapshots of different managed functions cannot be diffed (422), and an unknown element or snapshot is 404."""
    _make_me(db_session_factory)
    nf["objects"][("ME-1", "NRCellDU=2")] = {"administrativeState": "UNLOCKED"}
    _write(client, [_cell({"administrativeState": "LOCKED"})])
    _write(client, [{"managedElementRef": "ME-1", "managedFunctionRef": "NRCellDU=2", "attributeChanges": {"administrativeState": "LOCKED"}}])
    first, second = _history(client)["items"]
    params = {"from_snapshot": first["snapshotId"], "to_snapshot": second["snapshotId"]}
    assert client.get("/managed-entities/ME-1/config-history/diff", params=params).status_code == 422
    assert client.get("/managed-entities/ME-9/config-history/diff", params=params).status_code == 404
    assert client.get("/managed-entities/ME-1/config-history/diff", params={**params, "to_snapshot": str(uuid.uuid4())}).status_code == 404


# ---------------------------------------------------------------- MGT-1.8: retention


def _age(db_session_factory, snapshot_id, days):
    """Sets a snapshot's creation time `days` days in the past, so the purge has something old to delete."""
    db = db_session_factory()
    row = db.get(CMSnapshot, uuid.UUID(snapshot_id))
    row.created_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)
    db.commit()
    db.close()


def test_purge_deletes_only_snapshots_older_than_the_age_and_never_without_one(client, db_session_factory, nf, monkeypatch):
    """The purge needs an age (422 without one), deletes only snapshots older than it, keeps the jobs, and a job whose snapshots were purged can no longer be rolled back.
    """
    _make_me(db_session_factory)
    old_job = _write(client, [_cell({"administrativeState": "LOCKED"})]).json()["jobId"]
    _write(client, [_cell({"cellLocalId": "9"})])
    old = [i for i in _history(client)["items"] if i["jobId"] == old_job][0]
    _age(db_session_factory, old["snapshotId"], 40)
    assert client.post("/config-history/purge").status_code == 422                          # no age given, none configured
    assert client.post("/config-history/purge", params={"older_than_days": 0}).status_code == 422
    assert client.post("/config-history/purge", params={"older_than_days": 90}).json()["deleted"] == 0
    assert client.post("/config-history/purge", params={"older_than_days": 30}).json()["deleted"] == 1
    assert _history(client)["total"] == 1 and _history(client)["items"][0]["jobId"] != old_job
    resp = _rollback(client, old_job)                                                      # its history is gone: it can no longer be undone
    assert resp.status_code == 422 and "purged" in resp.text
    assert _job(client, old_job)["status"] == "COMPLETED"                                  # the job itself stays


def test_the_configured_retention_is_the_default_age(client, db_session_factory, nf, monkeypatch):
    """Without `older_than_days` the purge uses `RAN_NF_OAM_CM_SNAPSHOT_RETENTION_DAYS`, and the sub-change of the job stays APPLIED."""
    _make_me(db_session_factory)
    job = _write(client, [_cell({"administrativeState": "LOCKED"})]).json()["jobId"]
    _age(db_session_factory, _history(client)["items"][0]["snapshotId"], 10)
    monkeypatch.setattr("app.main.CM_SNAPSHOT_RETENTION_DAYS", 7)
    assert client.post("/config-history/purge").json()["deleted"] == 1
    assert _job(client, job)["subChanges"][0]["status"] == "APPLIED"
