"""OI-3-nfo-abnormal: an asynchronous Terminate the deployment manager (O2
DMS) completes, and the DELETING / ABNORMAL states it reaches.
Run with: pytest smo/nfo/tests -q
"""

import pytest

from test_main import _create_descriptor, _instantiate, client, db_session_factory  # noqa: F401  (pytest fixtures)


def _deploy(client, monkeypatch, name="nf-1"):
    return _instantiate(client, monkeypatch, name=name).json()["nfDeploymentId"]


def _notify(client, dep_id, event, detail=None):
    return client.post(f"/deployments/{dep_id}/dms-notifications", json={"event": event, "detail": detail})


def _state(client, dep_id):
    return client.get(f"/deployments/{dep_id}").json()["state"]


def _operations(client, dep_id):
    return [(o["operationType"], o["status"]) for o in client.get(f"/deployments/{dep_id}/operations").json()["items"]]


def test_an_async_terminate_waits_for_the_dms_and_passes_through_deleting(client, monkeypatch):
    dep = _deploy(client, monkeypatch)

    accepted = client.delete(f"/deployments/{dep}", params={"async_uninstall": True})

    assert accepted.status_code == 202 and accepted.json()["state"] == "TERMINATING"
    assert ("TERMINATE", "IN_PROGRESS") in _operations(client, dep)
    assert _notify(client, dep, "UNINSTALL_COMPLETE").json()["state"] == "DELETING"
    assert _state(client, dep) == "DELETING"
    assert _notify(client, dep, "DELETE_COMPLETE").json() == {"nfDeploymentId": dep, "state": "DELETED"}
    assert client.get(f"/deployments/{dep}").status_code == 404


def test_the_descriptor_is_held_until_the_deployment_is_really_gone(client, monkeypatch):
    descriptor = _create_descriptor(client)
    dep = _instantiate(client, monkeypatch, descriptor_id=descriptor).json()["nfDeploymentId"]
    client.delete(f"/deployments/{dep}", params={"async_uninstall": True})
    assert _instantiate(client, monkeypatch, name="nf-2", descriptor_id=descriptor).status_code == 409
    _notify(client, dep, "UNINSTALL_COMPLETE")
    _notify(client, dep, "DELETE_COMPLETE")
    assert _instantiate(client, monkeypatch, name="nf-2", descriptor_id=descriptor).status_code == 202


@pytest.mark.parametrize("fail_at", ["UNINSTALL_FAILED", "DELETE_FAILED"])
def test_a_failed_uninstall_or_delete_is_abnormal_and_terminate_retries(client, monkeypatch, fail_at):
    dep = _deploy(client, monkeypatch)
    client.delete(f"/deployments/{dep}", params={"async_uninstall": True})
    if fail_at == "DELETE_FAILED":
        _notify(client, dep, "UNINSTALL_COMPLETE")

    failed = _notify(client, dep, fail_at, "helm timed out")

    assert failed.json()["state"] == "ABNORMAL" and failed.json()["abnormalReason"] == f"{fail_at}: helm timed out"
    assert ("TERMINATE", "FAILED") in _operations(client, dep)
    assert client.get("/deployments", params={"state": "ABNORMAL"}).json()["items"][0]["abnormalReason"].startswith(fail_at)
    # Terminate retries from ABNORMAL: nothing left to uninstall, straight to DELETING and gone
    assert client.delete(f"/deployments/{dep}").status_code == 204
    assert client.get(f"/deployments/{dep}").status_code == 404


def test_a_runtime_failure_is_abnormal_and_heal_recovers_it(client, monkeypatch):
    dep = _deploy(client, monkeypatch)
    assert _notify(client, dep, "RUNTIME_FAILURE", "pod CrashLoopBackOff").json()["state"] == "ABNORMAL"
    healed = client.post(f"/deployments/{dep}/heal")
    assert healed.json()["state"] == "RUNNING"
    assert client.get(f"/deployments/{dep}").json()["abnormalReason"] is None


def test_terminating_again_while_the_uninstall_runs_is_a_no_op(client, monkeypatch):
    dep = _deploy(client, monkeypatch)
    client.delete(f"/deployments/{dep}", params={"async_uninstall": True})
    assert client.delete(f"/deployments/{dep}", params={"async_uninstall": True}).json()["state"] == "TERMINATING"
    assert client.delete(f"/deployments/{dep}").status_code == 204
    assert _state(client, dep) == "TERMINATING"


@pytest.mark.parametrize("event", ["UNINSTALL_COMPLETE", "DELETE_COMPLETE", "DELETE_FAILED"])
def test_an_event_the_state_cannot_take_is_refused(client, monkeypatch, event):
    dep = _deploy(client, monkeypatch)   # RUNNING
    resp = _notify(client, dep, event)
    assert resp.status_code == 409 and resp.json()["detail"]["title"] == "NFDEPLOYMENT_ILLEGAL_OPERATION"
    assert _state(client, dep) == "RUNNING"


def test_notifications_validate_the_event_and_the_deployment(client, monkeypatch):
    dep = _deploy(client, monkeypatch)
    assert _notify(client, dep, "EXPLODED").status_code == 422
    assert _notify(client, "00000000-0000-0000-0000-000000000000", "RUNTIME_FAILURE").status_code == 404


def test_the_default_terminate_is_still_synchronous(client, monkeypatch):
    dep = _deploy(client, monkeypatch)
    assert client.delete(f"/deployments/{dep}").status_code == 204
    assert client.get(f"/deployments/{dep}").status_code == 404
