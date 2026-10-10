"""AI-10.4: the per-rApp kill switch: an operator stops an rApp (by invoker id) and its config jobs are refused until it is lifted; undoing is not."""

import pytest

from test_main import client, db_session_factory  # noqa: F401  (pytest fixtures)
from test_waves import ELEMENTS, fleet  # noqa: F401

from app.models import WriteConfigJob

ES = {"X-R1-Invoker-Id": "es-client"}
OTHER = {"X-R1-Invoker-Id": "ts-client"}


def _kill(client, invoker="es-client", reason="oscillating tx power", by="alice"):
    return client.put(f"/rapp-kill/{invoker}", json={"requestedBy": by, "reason": reason})


def _write(client, headers, power=20, **extra):
    return client.post("/config-jobs", headers=headers, json={"requestedBy": "r", "scope": "cell", "changes": [
        {"managedElementRef": ref, "attributeChanges": {"txPower": power}} for ref in ELEMENTS[:2]], **extra})


def test_a_stopped_rapp_is_refused_with_403_and_the_reason(client, fleet):
    """After the kill switch is thrown, the rApp's next write is 403 RAPP_KILLED with who stopped it and why, and nothing reaches the element."""
    assert _write(client, ES).status_code == 202
    assert _kill(client).status_code == 200
    refused = _write(client, ES, 21)
    assert refused.status_code == 403 and refused.json()["detail"]["title"] == "RAPP_KILLED"
    assert "alice" in refused.json()["detail"]["detail"] and "oscillating tx power" in refused.json()["detail"]["detail"]
    assert fleet["values"]["ME-1"] == "20"                                # nothing was written after the stop


def test_only_that_rapp_is_stopped_and_an_unidentified_caller_is_not(client, fleet):
    """The switch is per rApp: another rApp and a caller R1 did not identify keep writing."""
    _kill(client)
    assert _write(client, OTHER).status_code == 202
    assert _write(client, {}, 22).status_code == 202


def test_a_refused_job_is_not_created_and_a_dry_run_is_refused_too(client, fleet):
    """A stopped rApp gets no job row, and not even a dry run (the refusal comes before any check)."""
    _kill(client)
    assert _write(client, ES, dryRun=True).status_code == 403
    assert _write(client, ES).status_code == 403
    with fleet["db"]() as db:
        assert db.query(WriteConfigJob).count() == 0


def test_lifting_the_switch_lets_it_write_again(client, fleet):
    """Deleting the kill record lets the rApp write again, and reading it then is 404."""
    _kill(client)
    assert client.delete("/rapp-kill/es-client").status_code == 204
    assert _write(client, ES).status_code == 202
    assert client.get("/rapp-kill/es-client").status_code == 404


def test_stopping_twice_keeps_the_first_time_and_updates_the_reason(client, fleet):
    """Stopping an already stopped rApp updates the reason and the stopper but keeps the time of the first stop, and does not add a second record."""
    first = _kill(client, reason="first").json()
    second = _kill(client, reason="second", by="bob").json()
    assert second["killedAt"] == first["killedAt"] and second["reason"] == "second" and second["killedBy"] == "bob"
    assert len(client.get("/rapp-kill").json()["items"]) == 1


def test_undoing_is_not_refused_to_a_stopped_rapp(client, fleet):
    """A rollback of the rApp's job still works after the stop: undoing a change is never blocked by the switch."""
    job = _write(client, ES).json()["jobId"]
    _kill(client)
    resp = client.post(f"/config-jobs/{job}/rollback", json={"requestedBy": "ops"}, headers=ES)
    assert resp.status_code == 202 and fleet["values"]["ME-1"] == "10"


def test_a_job_of_a_stopped_rapp_does_not_go_on_to_its_next_wave_but_can_be_halted_and_aborted(client, fleet):
    """A staged job paused between waves cannot be continued once its rApp is stopped (403), but it can still be aborted."""
    job = _write(client, ES, waveSize=1, wavePauseSeconds=3600).json()
    assert job["status"] in ("HALTED", "PROCESSING") and fleet["values"]["ME-2"] == "10"              # wave 1 only, then the pause
    _kill(client)
    resumed = client.post(f"/config-jobs/{job['jobId']}/continue", json={"requestedBy": "alice", "force": True})
    assert resumed.status_code == 403 and resumed.json()["detail"]["title"] == "RAPP_KILLED"
    assert fleet["values"]["ME-2"] == "10"
    assert client.post(f"/config-jobs/{job['jobId']}/abort", json={"requestedBy": "alice"}).status_code in (200, 202)


def test_reading_the_switch(client, fleet):
    """The kill record is readable by id and in the list, and reading or deleting one that does not exist is 404."""
    assert client.get("/rapp-kill/es-client").status_code == 404
    _kill(client)
    view = client.get("/rapp-kill/es-client").json()
    assert view["invokerId"] == "es-client" and view["killedBy"] == "alice" and view["reason"] == "oscillating tx power"
    assert [i["invokerId"] for i in client.get("/rapp-kill").json()["items"]] == ["es-client"]
    assert client.delete("/rapp-kill/nobody").status_code == 404


@pytest.mark.parametrize("body", [{}, {"requestedBy": ""}, {"requestedBy": "a", "reason": "x" * 501}])
def test_the_request_is_validated(client, body):
    """A kill request needs a non-empty `requestedBy` and a reason of at most 500 characters (422)."""
    assert client.put("/rapp-kill/x", json=body).status_code == 422
