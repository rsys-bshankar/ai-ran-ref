"""The safeguards of one instance in one read, for the operator GUI: stopped or not, its limits, and how much of its hourly allowance it used."""

import uuid

import httpx
import pytest

from test_main import FakeR1Response, client, db_session_factory  # noqa: F401  (pytest fixtures)
from test_rapp_limits import _create, _wire_invokers

KILL = {"invokerId": "x", "killedBy": "alice", "reason": "oscillating", "killedAt": "2026-10-04T00:00:00Z"}
LIMIT = {"invokerId": "x", "maxConfigJobsPerHour": 20, "maxElementsPerJob": 5, "maxChangePercent": 10.0, "updatedAt": "2026-10-04T00:00:00Z", "configJobsLastHour": 3}
NOTHING = (404, {})


def _wire(monkeypatch):
    """R1 as instance creation needs it, plus `answers`: what RAN NF OAM replies to the two reads (a (status, body) pair, or an exception to raise)."""
    seen = _wire_invokers(monkeypatch)
    seen["asked"] = []
    seen["answers"] = {"rapp-kill": NOTHING, "rapp-limits": NOTHING}
    creation_get = __import__("app.main", fromlist=["R1Client"]).R1Client.get

    def get(self, path, **kw):
        for name, outcome in seen["answers"].items():
            if path.startswith(f"/ran-nf-oam/{name}/"):
                seen["asked"].append(path)
                if isinstance(outcome, Exception):
                    raise outcome
                return FakeR1Response(*outcome)
        return creation_get(self, path, **kw)

    monkeypatch.setattr("app.main.R1Client.get", get)
    return seen


def test_a_stopped_instance_with_limits_is_described_in_one_read(client, monkeypatch):
    seen = _wire(monkeypatch)
    created = _create(client)
    seen["answers"].update({"rapp-kill": (200, KILL), "rapp-limits": (200, LIMIT)})
    view = client.get(f"/instances/{created['instanceId']}/safeguards").json()
    assert view["invokerId"] == created["oauthClientId"] and view["killed"] is True
    assert view["kill"]["killedBy"] == "alice" and view["limits"]["configJobsLastHour"] == 3
    assert seen["asked"] == [f"/ran-nf-oam/rapp-kill/{created['oauthClientId']}", f"/ran-nf-oam/rapp-limits/{created['oauthClientId']}"]


def test_an_instance_with_neither_is_not_stopped_and_has_no_limits(client, monkeypatch):
    _wire(monkeypatch)
    created = _create(client)
    view = client.get(f"/instances/{created['instanceId']}/safeguards").json()
    assert view["killed"] is False and view["kill"] is None and view["limits"] is None and view["invokerId"]


@pytest.mark.parametrize("which", ["rapp-kill", "rapp-limits"])
@pytest.mark.parametrize("outcome", [(500, {}), httpx.ConnectError("down")])
def test_a_read_that_failed_is_an_error_never_a_not_stopped(client, monkeypatch, which, outcome):
    seen = _wire(monkeypatch)
    created = _create(client)
    seen["answers"][which] = outcome
    resp = client.get(f"/instances/{created['instanceId']}/safeguards")
    assert resp.status_code == 503 and "could not be read" in resp.json()["detail"]["detail"]


def test_a_terminated_instance_has_no_invoker_and_nothing_is_asked(client, monkeypatch):
    seen = _wire(monkeypatch)
    created = _create(client)
    client.post(f"/instances/{created['instanceId']}/bootstrap-complete")
    client.post(f"/instances/{created['instanceId']}/terminate")
    view = client.get(f"/instances/{created['instanceId']}/safeguards").json()
    assert view == {"instanceId": created["instanceId"], "invokerId": None, "killed": False, "kill": None, "limits": None}
    assert seen["asked"] == []


def test_an_unknown_instance_is_404(client, monkeypatch):
    _wire(monkeypatch)
    assert client.get(f"/instances/{uuid.uuid4()}/safeguards").status_code == 404
