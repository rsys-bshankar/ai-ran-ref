"""AI-10.2: a per-rApp cap on CM write jobs, set from the rApp's manifest and counted per invoker id (the one R1 Termination vouches for)."""

import pytest

from test_main import client, db_session_factory  # noqa: F401  (pytest fixtures)

from app.models import ManagedEntity, O1AdaptorEndpoint
from app.netconf_client import EditResult

ES = {"X-R1-Invoker-Id": "es-client"}
OTHER = {"X-R1-Invoker-Id": "ts-client"}


@pytest.fixture
def element(db_session_factory, monkeypatch):
    """Fixture: registers element ME-1 with an adaptor endpoint, and patches the NETCONF edit and read so a write changes an in-memory `txPower` value. Tests may assume one ACTIVE NETCONF element and a working write.
    """
    db = db_session_factory()
    endpoint = O1AdaptorEndpoint(managed_element_ref="ME-1", adaptor_uri="http://adaptor:9000/netconf", protocol_support=["NETCONF"], health_status="ACTIVE")
    db.add(endpoint)
    db.flush()
    db.add(ManagedEntity(managed_element_ref="ME-1", entity_type="O-DU", o1_protocol="NETCONF", o1_adaptor_endpoint_id=endpoint.endpoint_id))
    db.commit()
    db.close()
    values = {"txPower": "10"}

    def edit(adaptor_uri, target_ref, attribute_changes, message_id, operation="merge", managed_function_ref=None):
        values["txPower"] = str(attribute_changes["txPower"])
        return EditResult(True)

    monkeypatch.setattr("app.main.send_get_config", lambda *a, **k: dict(values))
    monkeypatch.setattr("app.main.send_edit_config", edit)


def _write(client, headers, power=20):
    return client.post("/config-jobs", headers=headers, json={"requestedBy": "r", "scope": "cell", "changes": [
        {"managedElementRef": "ME-1", "attributeChanges": {"txPower": power}}]})


def _limit(client, invoker="es-client", per_hour=2, headers=None):
    return client.put(f"/rapp-limits/{invoker}", json={"maxConfigJobsPerHour": per_hour}, headers=headers or {})


def test_a_caller_over_its_limit_gets_429_and_a_retry_hint(client, element):
    """Once a caller has started as many jobs in the hour as its limit, the next is 429 RAPP_RATE_LIMITED with a Retry-After header."""
    assert _limit(client).status_code == 200
    assert [_write(client, ES, p).status_code for p in (20, 21)] == [202, 202]
    refused = _write(client, ES, 22)
    assert refused.status_code == 429
    assert refused.json()["detail"]["title"] == "RAPP_RATE_LIMITED"
    assert refused.headers["retry-after"] == "60"


def test_the_limit_is_per_caller_and_does_not_touch_others(client, element):
    """The limit counts only the limited rApp's own jobs; another rApp, and a call R1 did not identify, are not limited."""
    _limit(client, per_hour=1)
    assert _write(client, ES).status_code == 202
    assert _write(client, ES, 21).status_code == 429
    assert _write(client, OTHER).status_code == 202            # no limit set for it
    assert _write(client, {}).status_code == 202               # a call R1 did not identify is not counted


def test_a_refused_call_creates_no_job(client, element):
    """A refused write does not add to the count of the caller's jobs."""
    _limit(client, per_hour=1)
    _write(client, ES)
    _write(client, ES, 21)
    assert client.get("/rapp-limits/es-client").json()["configJobsLastHour"] == 1


def test_a_rollback_is_not_counted_against_the_limit(client, element):
    """A rollback is neither refused by the rate limit nor counted in it, so a caller at its limit can still undo."""
    _limit(client, per_hour=1)
    job = _write(client, ES).json()["jobId"]
    assert client.post(f"/config-jobs/{job}/rollback", json={"requestedBy": "ops"}, headers=ES).status_code == 202
    assert _write(client, ES, 21).status_code == 429          # the rollback did not use the one a caller has, nor free it


def test_raising_the_limit_lets_the_caller_go_on(client, element):
    """Replacing the limit with a higher one takes effect on the next write."""
    _limit(client, per_hour=1)
    _write(client, ES)
    assert _write(client, ES, 21).status_code == 429
    _limit(client, per_hour=5)
    assert _write(client, ES, 21).status_code == 202


def test_delete_removes_the_limit(client, element):
    """Deleting the limit lifts it, and reading or deleting it again is 404."""
    _limit(client, per_hour=1)
    _write(client, ES)
    assert client.delete("/rapp-limits/es-client").status_code == 204
    assert _write(client, ES, 21).status_code == 202
    assert client.get("/rapp-limits/es-client").status_code == 404
    assert client.delete("/rapp-limits/es-client").status_code == 404


def test_a_caller_cannot_change_its_own_limit(client, element):
    """An rApp cannot set or delete its own limit (403), while another caller can."""
    _limit(client, per_hour=1)
    assert _limit(client, per_hour=1000, headers=ES).status_code == 403
    assert client.delete("/rapp-limits/es-client", headers=ES).status_code == 403
    assert client.get("/rapp-limits/es-client").json()["maxConfigJobsPerHour"] == 1
    assert _limit(client, per_hour=3, headers=OTHER).status_code == 200      # someone else (rApp Management) may


@pytest.mark.parametrize("value", [0, -1, 100_001, "many"])
def test_a_limit_must_be_a_positive_number(client, value):
    """A limit of zero, a negative or too large number, or a non-number is a 422."""
    assert client.put("/rapp-limits/x", json={"maxConfigJobsPerHour": value}).status_code == 422
