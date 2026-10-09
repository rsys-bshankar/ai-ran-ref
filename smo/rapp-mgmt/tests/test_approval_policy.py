"""AI-11.4: an ASSIST instance created with an approval policy has its config jobs held at RAN NF OAM for a human; the policy is put in force before the
instance runs (fail closed), removed on teardown and carried through an upgrade and a rollback. An instance created without one is not touched."""

import uuid

import httpx
import pytest

from test_main import FakeR1Response, client, db_session_factory  # noqa: F401  (pytest fixtures)
from test_rapp_limits import _wire

POLICY = {"timeoutSeconds": 600, "onTimeout": "REJECT"}


def _create(client, mode="ASSIST", policy=POLICY):
    body = {"packageId": str(uuid.uuid4()), "config": {}, "autonomyMode": mode}
    if policy is not None:
        body["approvalPolicy"] = policy
    return client.post("/instances", json=body)


def _policy_calls(calls):
    return [(p, j) for p, j in calls["put"] if "rapp-approval-policy" in p]


def test_an_assist_instance_with_a_policy_has_it_pushed_under_its_client_id_at_bootstrap(client, monkeypatch):
    calls = _wire(monkeypatch, None)
    created = _create(client).json()
    assert _policy_calls(calls) == []                                                   # nothing yet: the instance does not run
    assert client.post(f"/instances/{created['instanceId']}/bootstrap-complete").json()["state"] == "RUNNING"
    assert _policy_calls(calls) == [(f"/ran-nf-oam/rapp-approval-policy/{created['oauthClientId']}",
                                     {"requestedBy": "rapp-mgmt", "timeoutSeconds": 600, "onTimeout": "REJECT"})]
    assert client.get(f"/instances/{created['instanceId']}").json()["approvalPolicy"] == POLICY


def test_the_defaults_are_the_conservative_ones(client, monkeypatch):
    calls = _wire(monkeypatch, None)
    created = _create(client, policy={}).json()
    client.post(f"/instances/{created['instanceId']}/bootstrap-complete")
    assert _policy_calls(calls)[0][1] == {"requestedBy": "rapp-mgmt", "timeoutSeconds": 3600, "onTimeout": "EXPIRE"}


@pytest.mark.parametrize("mode", ["AUTONOMOUS", "SHADOW"])
def test_a_policy_is_refused_for_a_mode_in_which_no_one_decides(client, monkeypatch, mode):
    _wire(monkeypatch, None)
    resp = _create(client, mode=mode)
    assert resp.status_code == 422 and resp.json()["detail"]["title"] == "APPROVAL_POLICY_NEEDS_ASSIST"


@pytest.mark.parametrize("policy", [{"timeoutSeconds": 59}, {"timeoutSeconds": 604_801}, {"onTimeout": "APPROVE"}, {"onTimeout": "expire"}])
def test_a_policy_outside_the_bounds_is_422(client, monkeypatch, policy):
    _wire(monkeypatch, None)
    assert _create(client, policy=policy).status_code == 422


@pytest.mark.parametrize("mode", ["AUTONOMOUS", "ASSIST", "SHADOW"])
def test_an_instance_without_a_policy_behaves_as_before_in_every_mode(client, monkeypatch, mode):
    calls = _wire(monkeypatch, None)
    created = _create(client, mode=mode, policy=None).json()
    assert client.post(f"/instances/{created['instanceId']}/bootstrap-complete").json()["state"] == "RUNNING"
    assert _policy_calls(calls) == [] and client.get(f"/instances/{created['instanceId']}").json()["approvalPolicy"] is None
    client.post(f"/instances/{created['instanceId']}/terminate")
    assert [p for p in calls["delete"] if "rapp-approval-policy" in p] == []


@pytest.mark.parametrize("outcome", [500, 404, httpx.ConnectError("down")])
def test_a_policy_that_cannot_be_put_in_force_keeps_the_instance_from_running(client, monkeypatch, outcome):
    _wire(monkeypatch, None, put_outcome=outcome)
    created = _create(client).json()
    resp = client.post(f"/instances/{created['instanceId']}/bootstrap-complete")
    assert resp.status_code == 503 and "approval policy" in resp.json()["detail"]["detail"]
    assert client.get(f"/instances/{created['instanceId']}").json()["state"] == "DEPLOYING"          # never RUNNING and writing at once


def test_terminate_removes_the_policy(client, monkeypatch):
    calls = _wire(monkeypatch, None)
    created = _create(client).json()
    client.post(f"/instances/{created['instanceId']}/bootstrap-complete")
    assert client.post(f"/instances/{created['instanceId']}/terminate").status_code == 200
    assert calls["delete"].count(f"/ran-nf-oam/rapp-approval-policy/{created['oauthClientId']}") == 1


def test_an_upgrade_keeps_the_policy_and_a_rollback_restores_it(client, db_session_factory, monkeypatch):
    calls = _wire(monkeypatch, None)
    created = _create(client).json()
    client.post(f"/instances/{created['instanceId']}/bootstrap-complete")
    upgraded = client.post(f"/instances/{created['instanceId']}/upgrade", json={"newPackageId": str(uuid.uuid4())})
    assert upgraded.status_code == 200, upgraded.text
    new_id = upgraded.json()["newInstanceId"]
    assert client.get(f"/instances/{new_id}").json()["approvalPolicy"] == POLICY
    assert client.post(f"/instances/{created['instanceId']}/upgrade/resolve", params={"succeeded": True}).status_code == 200
    assert len(_policy_calls(calls)) == 2                                                               # pushed again under the replacement's own client id
    assert _policy_calls(calls)[1][0] != _policy_calls(calls)[0][0]
    from app.models import RAppInstanceVersion
    with db_session_factory() as db:
        [version] = db.query(RAppInstanceVersion).all()
        assert version.previous_approval_policy == POLICY
