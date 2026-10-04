"""AI-10.6: every refusal of an rApp by a safeguard is recorded and announced to the subscribers (through the outbox), and a repeat is announced once."""

import pytest
from sqlalchemy import select

from smo_shared.outbox import NotificationOutbox

from test_main import client, db_session_factory  # noqa: F401  (pytest fixtures)
from test_waves import ELEMENTS, fleet  # noqa: F401

ES = {"X-R1-Invoker-Id": "es-client"}
WATCHER = "http://watcher.example:9000/events"


@pytest.fixture(autouse=True)
def no_inline_sending(monkeypatch):
    monkeypatch.setenv("SMO_OUTBOX_INLINE_DRAIN", "false")          # the rows are what is under test, not the network


def _write(client, refs=ELEMENTS[:1], value=12, headers=ES, **extra):
    changes = [{"managedElementRef": ref, "attributeChanges": {"txPower": value}} for ref in refs]
    return client.post("/config-jobs", headers=headers, json={"requestedBy": "es-rapp", "scope": "cell", "changes": changes, **extra})


def _events(fleet, destination=WATCHER):
    with fleet["db"]() as db:
        return [row.payload for row in db.scalars(select(NotificationOutbox).where(NotificationOutbox.destination == destination)
                                                  .order_by(NotificationOutbox.created_at)).all()]


def _subscribe(client, **body):
    resp = client.post("/safeguard-subscriptions", json={"callbackUri": WATCHER, **body})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_a_kill_switch_refusal_is_announced_with_who_what_and_why(client, fleet):
    _subscribe(client)
    client.put("/rapp-kill/es-client", json={"requestedBy": "alice", "reason": "oscillating"})
    assert _write(client).status_code == 403
    [event] = _events(fleet)
    assert event["eventType"] == "RAPP_SAFEGUARD_REFUSAL" and event["refusal"] == "RAPP_KILLED"
    assert event["invokerId"] == "es-client" and event["requestedBy"] == "es-rapp" and "oscillating" in event["detail"] and event["occurredAt"]
    assert event["href"] == "/ran-nf-oam/safeguard-subscriptions"


@pytest.mark.parametrize("limits, call, code", [
    ({"maxConfigJobsPerHour": 1}, lambda c: (_write(c), _write(c, value=11))[1], "RAPP_RATE_LIMITED"),
    ({"maxElementsPerJob": 1}, lambda c: _write(c, ELEMENTS[:2]), "RAPP_BLAST_RADIUS_EXCEEDED"),
    ({"maxChangePercent": 5}, lambda c: _write(c, value=50), "RAPP_MAGNITUDE_EXCEEDED"),
])
def test_each_kind_of_refusal_is_announced(client, fleet, limits, call, code):
    _subscribe(client)
    assert client.put("/rapp-limits/es-client", json=limits).status_code == 200
    refused = call(client)
    assert refused.status_code in (403, 429)
    assert [e["refusal"] for e in _events(fleet)] == [code]


def test_a_refusal_in_the_middle_of_a_job_is_announced_too(client, fleet):
    _subscribe(client)
    job = _write(client, ELEMENTS[:2], waveSize=1, wavePauseSeconds=3600).json()["jobId"]
    client.put("/rapp-kill/es-client", json={"requestedBy": "alice"})
    resp = client.post(f"/config-jobs/{job}/continue", json={"requestedBy": "alice", "force": True})
    assert resp.status_code == 403
    assert [(e["refusal"], e["requestedBy"]) for e in _events(fleet)] == [("RAPP_KILLED", "alice")]


def test_a_refusal_is_recorded_when_nobody_is_subscribed_and_can_be_queried(client, fleet):
    client.put("/rapp-limits/es-client", json={"maxElementsPerJob": 1})
    _write(client, ELEMENTS[:3])
    items = client.get("/safeguard-refusals").json()["items"]
    assert len(items) == 1 and items[0]["refusal"] == "RAPP_BLAST_RADIUS_EXCEEDED" and items[0]["invokerId"] == "es-client" and items[0]["announced"] is True
    assert client.get("/safeguard-refusals", params={"invoker_id": "someone-else"}).json()["items"] == []
    assert client.get("/safeguard-refusals", params={"code": "RAPP_KILLED"}).json()["items"] == []
    assert len(client.get("/safeguard-refusals", params={"code": "RAPP_BLAST_RADIUS_EXCEEDED", "since": "2020-01-01T00:00:00Z"}).json()["items"]) == 1
    assert _events(fleet) == []


def test_a_repeat_is_recorded_each_time_but_announced_once_per_interval(client, fleet, monkeypatch):
    _subscribe(client)
    client.put("/rapp-kill/es-client", json={"requestedBy": "alice"})
    for _ in range(4):
        assert _write(client).status_code == 403
    items = client.get("/safeguard-refusals").json()["items"]
    assert len(items) == 4 and sorted(i["announced"] for i in items) == [False, False, False, True]
    assert len(_events(fleet)) == 1
    other = _write(client, ELEMENTS[:1], headers={"X-R1-Invoker-Id": "ts-client"})          # another rApp is another story
    assert other.status_code == 202
    client.put("/rapp-kill/ts-client", json={"requestedBy": "alice"})
    _write(client, headers={"X-R1-Invoker-Id": "ts-client"})
    assert len(_events(fleet)) == 2


def test_with_the_interval_at_zero_every_refusal_is_announced(client, fleet, monkeypatch):
    import datetime
    monkeypatch.setattr("app.main.SAFEGUARD_EVENT_MIN_INTERVAL", datetime.timedelta(0))
    _subscribe(client)
    client.put("/rapp-kill/es-client", json={"requestedBy": "alice"})
    for _ in range(3):
        _write(client)
    assert len(_events(fleet)) == 3


def test_a_subscriber_may_ask_for_some_refusals_only(client, fleet):
    _subscribe(client, refusals=["RAPP_MAGNITUDE_EXCEEDED"])
    client.put("/rapp-kill/es-client", json={"requestedBy": "alice"})
    _write(client)
    assert _events(fleet) == []
    client.delete("/rapp-kill/es-client")
    client.put("/rapp-limits/es-client", json={"maxChangePercent": 5})
    _write(client, value=50)
    assert [e["refusal"] for e in _events(fleet)] == ["RAPP_MAGNITUDE_EXCEEDED"]


def test_every_subscriber_is_told(client, fleet):
    _subscribe(client)
    second = client.post("/safeguard-subscriptions", json={"callbackUri": "http://other.example/hook"})
    assert second.status_code == 201
    client.put("/rapp-kill/es-client", json={"requestedBy": "alice"})
    _write(client)
    assert len(_events(fleet)) == 1 and len(_events(fleet, "http://other.example/hook")) == 1


def test_a_callers_that_is_not_refused_causes_no_event(client, fleet):
    _subscribe(client)
    assert _write(client).status_code == 202 and _events(fleet) == []


def test_subscriptions_can_be_listed_and_removed_and_a_bad_destination_is_refused(client, fleet):
    sub = _subscribe(client, refusals=["RAPP_KILLED", "RAPP_KILLED"])
    assert sub["refusals"] == ["RAPP_KILLED"]
    assert [s["subscriptionId"] for s in client.get("/safeguard-subscriptions").json()["items"]] == [sub["subscriptionId"]]
    assert client.delete(f"/safeguard-subscriptions/{sub['subscriptionId']}").status_code == 204
    assert client.delete(f"/safeguard-subscriptions/{sub['subscriptionId']}").status_code == 404
    for body in ({"callbackUri": "http://localhost/hook"}, {"callbackUri": "ftp://x/y"}, {"callbackUri": ""}, {"callbackUri": WATCHER, "refusals": ["NOPE"]}):
        assert client.post("/safeguard-subscriptions", json=body).status_code in (422,)
