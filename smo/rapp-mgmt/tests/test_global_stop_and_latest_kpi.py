"""GUI-9.6 and GUI-9.8 in rApp Management: the global stop (`PUT|DELETE|GET /kill-all`) over every live instance's kill switch at RAN NF OAM, and the
latest headline KPI of an instance (`GET /instances/{id}/performance/latest`) and of many (`GET /instances/performance/latest?ids=`).

RAN NF OAM is faked by patching `app.main.R1Client` (`_wire_global`): it holds a set of stopped invoker ids, answers the paged list read, and records
every PUT and DELETE; instances are created through the module's own route with `_create` from `test_rapp_limits.py`. Fixtures `client` and
`db_session_factory` come from `test_main.py` (SQLite). Run: `PYTHONPATH=.:../shared python -m pytest tests/test_global_stop_and_latest_kpi.py -q`.
"""

import datetime
import uuid

import httpx
import pytest

from test_main import FakeR1Response, client, db_session_factory  # noqa: F401  (pytest fixtures)
from test_rapp_limits import _create, _wire_invokers

from app.models import RAppPerformanceReport


def _wire_global(monkeypatch, *, stopped=()):
    """Fakes RAN NF OAM's kill switch and returns the dict a test steers it with and reads it from: `stopped` (the stopped invoker ids, initially
    `stopped`), `list_outcome` (the status of `GET /rapp-kill`, or an exception to raise), `put_fail`/`delete_fail` (invoker ids whose PUT/DELETE
    answer 500), and the recordings `put`, `delete` and `listed`."""
    seen = _wire_invokers(monkeypatch)
    seen.update(put=[], stopped=set(stopped), listed=[], list_outcome=200, put_fail=set(), delete_fail=set())
    creation_get = __import__("app.main", fromlist=["R1Client"]).R1Client.get

    def get(self, path, **kw):
        # the list read is paged: answer the slice the caller asked for
        if path == "/ran-nf-oam/rapp-kill":
            seen["listed"].append(kw.get("params"))
            if isinstance(seen["list_outcome"], Exception):
                raise seen["list_outcome"]
            params = kw.get("params") or {}
            ids = sorted(seen["stopped"])[params.get("offset", 0):params.get("offset", 0) + params.get("limit", 100)]
            return FakeR1Response(seen["list_outcome"], {"items": [{"invokerId": i} for i in ids]})
        return creation_get(self, path, **kw)

    def put(self, path, json=None, **kw):
        # the per-instance kill call: record it, and stop the invoker unless told to fail
        invoker = path.rsplit("/", 1)[1]
        seen["put"].append((path, json))
        if invoker in seen["put_fail"]:
            return FakeR1Response(500, {})
        seen["stopped"].add(invoker)
        return FakeR1Response(200, {"invokerId": invoker, "killedBy": json["requestedBy"], "reason": json["reason"], "killedAt": "2026-10-10T00:00:00Z"})

    def delete(self, path, **kw):
        # the per-instance lift: 404 when the invoker was not stopped, as RAN NF OAM answers
        invoker = path.rsplit("/", 1)[1]
        seen["delete"].append(path)
        if invoker in seen["delete_fail"]:
            return FakeR1Response(500, {})
        if invoker not in seen["stopped"]:
            return FakeR1Response(404, {})
        seen["stopped"].discard(invoker)
        return FakeR1Response(204, {})

    monkeypatch.setattr("app.main.R1Client.get", get)
    monkeypatch.setattr("app.main.R1Client.put", put)
    monkeypatch.setattr("app.main.R1Client.delete", delete)
    return seen


def test_the_global_stop_stops_every_live_instance_through_the_per_instance_call(client, monkeypatch):
    """Every instance that is not UNDEPLOYED gets the same PUT the per-instance kill sends, with the operator's name and reason."""
    seen = _wire_global(monkeypatch)
    a, b = _create(client), _create(client)
    resp = client.put("/kill-all", json={"requestedBy": "alice", "reason": "storm"})
    assert resp.status_code == 200 and resp.json() == {"stopped": 2, "alreadyStopped": 0, "failed": []}
    assert sorted(seen["put"]) == sorted([(f"/ran-nf-oam/rapp-kill/{c['oauthClientId']}", {"requestedBy": "alice", "reason": "storm"}) for c in (a, b)])


def test_an_instance_already_stopped_keeps_its_first_stop(client, monkeypatch):
    """A stopped instance is counted as already stopped and not stopped again, so its original author and reason are not overwritten."""
    seen = _wire_global(monkeypatch)
    a, b = _create(client), _create(client)
    seen["stopped"].add(a["oauthClientId"])
    resp = client.put("/kill-all", json={"requestedBy": "alice"}).json()
    assert resp == {"stopped": 1, "alreadyStopped": 1, "failed": []}
    assert [p for p, _ in seen["put"]] == [f"/ran-nf-oam/rapp-kill/{b['oauthClientId']}"]


def test_a_terminated_instance_is_not_part_of_the_global_stop(client, monkeypatch):
    """An UNDEPLOYED instance has no credential and nothing to stop; it is neither stopped nor counted."""
    seen = _wire_global(monkeypatch)
    gone = _create(client)
    client.post(f"/instances/{gone['instanceId']}/bootstrap-complete")
    client.post(f"/instances/{gone['instanceId']}/terminate")
    live = _create(client)
    assert client.put("/kill-all", json={"requestedBy": "alice"}).json() == {"stopped": 1, "alreadyStopped": 0, "failed": []}
    assert [p for p, _ in seen["put"]] == [f"/ran-nf-oam/rapp-kill/{live['oauthClientId']}"]


def test_an_instance_ran_nf_oam_refused_is_listed_and_the_others_stay_stopped(client, monkeypatch):
    """One refusal does not undo or stop the rest: the failed instance is named with the reason, the others are stopped."""
    seen = _wire_global(monkeypatch)
    a, b = _create(client), _create(client)
    seen["put_fail"].add(a["oauthClientId"])
    resp = client.put("/kill-all", json={"requestedBy": "alice"}).json()
    assert resp["stopped"] == 1 and resp["alreadyStopped"] == 0
    assert resp["failed"] == [{"instanceId": a["instanceId"], "error": "RAN NF OAM did not accept the kill switch change; nothing was changed"}]
    assert b["oauthClientId"] in seen["stopped"]


@pytest.mark.parametrize("outcome", [500, httpx.ConnectError("down")])
def test_an_unreadable_stop_list_changes_nothing(client, monkeypatch, outcome):
    """Without the list of stopped rApps the global stop, resume and count answer 503 and send no change."""
    seen = _wire_global(monkeypatch)
    _create(client)
    seen["list_outcome"] = outcome
    for method in ("put", "delete", "get"):
        kwargs = {"json": {"requestedBy": "alice"}} if method == "put" else {}
        resp = getattr(client, method)("/kill-all", **kwargs)
        assert resp.status_code == 503 and "could not be read" in resp.json()["detail"]["detail"]
    assert seen["put"] == []


def test_the_stop_list_is_read_page_by_page(client, monkeypatch):
    """More stopped invokers than one page: every page is read, so an instance on the second page counts as stopped."""
    seen = _wire_global(monkeypatch, stopped={f"other-{i:03}" for i in range(500)})
    created = _create(client)
    seen["stopped"].add(created["oauthClientId"])           # sorts after "other-...": on the second page
    assert client.get("/kill-all").json() == {"stopped": 1, "instances": 1}
    assert [p["offset"] for p in seen["listed"]] == [0, 500]


def test_the_global_resume_lifts_only_this_modules_stopped_instances(client, monkeypatch):
    """Resume sends the per-instance lift for each stopped instance and leaves alone an invoker that is not an instance of this module."""
    seen = _wire_global(monkeypatch, stopped={"not-an-rapp-instance"})
    a, b = _create(client), _create(client)
    seen["stopped"].add(a["oauthClientId"])
    seen["delete"].clear()
    resp = client.delete("/kill-all")
    assert resp.status_code == 200 and resp.json() == {"resumed": 1, "failed": []}
    assert seen["delete"] == [f"/ran-nf-oam/rapp-kill/{a['oauthClientId']}"]
    assert seen["stopped"] == {"not-an-rapp-instance"} and b["oauthClientId"] not in seen["stopped"]


def test_a_resume_ran_nf_oam_refused_is_listed(client, monkeypatch):
    """A lift RAN NF OAM refused is reported per instance; the instance stays stopped."""
    seen = _wire_global(monkeypatch)
    a = _create(client)
    seen["stopped"].add(a["oauthClientId"])
    seen["delete_fail"].add(a["oauthClientId"])
    resp = client.delete("/kill-all").json()
    assert resp["resumed"] == 0 and resp["failed"][0]["instanceId"] == a["instanceId"]
    assert a["oauthClientId"] in seen["stopped"]


def test_the_count_is_of_live_instances_stopped_now(client, monkeypatch):
    """GET /kill-all counts the live instances whose invoker is stopped, out of all live instances; it changes nothing."""
    seen = _wire_global(monkeypatch)
    a, _ = _create(client), _create(client)
    assert client.get("/kill-all").json() == {"stopped": 0, "instances": 2}
    seen["stopped"].add(a["oauthClientId"])
    assert client.get("/kill-all").json() == {"stopped": 1, "instances": 2}
    assert seen["put"] == []


# ---- GUI-9.8: the latest headline KPI

def _report(db_session_factory, instance_id, metrics, minutes_ago):
    """Writes one performance report for `instance_id`, `minutes_ago` minutes in the past, straight into the table."""
    with db_session_factory() as db:
        db.add(RAppPerformanceReport(instance_id=uuid.UUID(instance_id), metrics=metrics,
                                     reported_at=datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=minutes_ago)))
        db.commit()


def test_the_latest_kpi_is_the_newest_reports_numbers(client, monkeypatch, db_session_factory):
    """Only the newest report counts, and only its numeric values (a string, a bool and an object are left out)."""
    _wire_invokers(monkeypatch)
    created = _create(client)
    _report(db_session_factory, created["instanceId"], {"energySavedPercent": 3.0}, minutes_ago=10)
    _report(db_session_factory, created["instanceId"], {"energySavedPercent": 7.5, "cells": 12, "mode": "eco", "ok": True, "detail": {"a": 1}}, minutes_ago=1)
    view = client.get(f"/instances/{created['instanceId']}/performance/latest").json()
    assert view["instanceId"] == created["instanceId"] and view["metrics"] == {"energySavedPercent": 7.5, "cells": 12}
    assert view["at"].endswith("+00:00")


def test_no_report_and_no_instance_are_both_an_empty_answer_never_404(client, monkeypatch):
    """The GUI asks for every row it shows: an instance with no report, or an id with no instance, answers 200 with empty metrics."""
    _wire_invokers(monkeypatch)
    created = _create(client)
    for instance_id in (created["instanceId"], str(uuid.uuid4())):
        resp = client.get(f"/instances/{instance_id}/performance/latest")
        assert resp.status_code == 200 and resp.json() == {"instanceId": instance_id, "at": None, "metrics": {}}


def test_the_batched_read_answers_each_id_in_order(client, monkeypatch, db_session_factory):
    """One call answers every id given, in the given order and once each, with the newest report of each instance."""
    _wire_invokers(monkeypatch)
    a, b = _create(client), _create(client)
    _report(db_session_factory, a["instanceId"], {"kpi": 1}, minutes_ago=5)
    _report(db_session_factory, a["instanceId"], {"kpi": 2}, minutes_ago=2)
    _report(db_session_factory, b["instanceId"], {"kpi": 9}, minutes_ago=3)
    unknown = str(uuid.uuid4())
    items = client.get("/instances/performance/latest", params={"ids": f"{b['instanceId']},{unknown}, {a['instanceId']},{b['instanceId']}"}).json()["items"]
    assert [i["instanceId"] for i in items] == [b["instanceId"], unknown, a["instanceId"]]
    assert [i["metrics"] for i in items] == [{"kpi": 9}, {}, {"kpi": 2}]


@pytest.mark.parametrize("ids", [",".join(str(uuid.uuid4()) for _ in range(51)), "not-a-uuid"])
def test_the_batched_read_refuses_too_many_or_malformed_ids(client, ids):
    """More than 50 ids, or one that is not a UUID, is a 422 rather than an unbounded or partial answer."""
    assert client.get("/instances/performance/latest", params={"ids": ids}).status_code == 422
