"""PR-GUI-9 (OPEN_ITEMS.md GUI-9.2, 9.4, 9.5, 9.8): the reads the console redesign asks of RAN NF OAM.

Covered: the new alarm filters and the keyset cursor of `GET /alarms`; the ack and clear times; `GET /alarms/counts` (every grouping, the hourly
buckets); `GET /alarms/stats` (mean time to acknowledge); `GET /alarms/{id}/correlated`; the keyset cursor and the CSV export of decision records;
the `search` and `site_cluster` filters and `PUT /managed-entities/{me}/site-cluster`; the health map and the worst-element ranking; the paging,
the `reciprocal` filter and the counts of `GET /topology/links`. Every aggregate is checked against the caller's scope claim as well.

Fixtures: `client` and `db_session_factory` from `test_main.py` (a fresh in-memory SQLite per test, the app's session overridden); `fleet` below
registers four elements and writes alarms straight into the table with chosen times. Run with:
`cd smo/ran-nf-oam && PYTHONPATH=.:../shared python -m pytest tests/test_console_reads.py -q`.
"""

import csv
import datetime
import io
import json
import uuid

import pytest

from smo_shared.scope import SCOPE_HEADER

from app import alarm_query
from app.models import Alarm, ManagedEntity, RAppDecisionRecord

from test_main import client, db_session_factory  # noqa: F401  (pytest fixtures)

NOW = datetime.datetime.now(datetime.UTC)
EU = {"X-R1-Invoker-Id": "op", "X-R1-Role": "internal", SCOPE_HEADER: json.dumps({"regions": ["eu"]})}

# ME-1 eu/metro-a, ME-2 eu/metro-b, ME-3 us/no cluster, ME-4 no region
ELEMENTS = {"ME-1": ("eu", "metro-a"), "ME-2": ("eu", "metro-b"), "ME-3": ("us", None), "ME-4": (None, None)}


def _alarm(db, ref, severity, minutes_ago, *, ack=False, cause=None, ack_after=None, fn=None):
    """One alarm on `ref` raised `minutes_ago`; `ack_after` (seconds) also acknowledges it that long after it was raised."""
    raised = NOW - datetime.timedelta(minutes=minutes_ago)
    a = Alarm(alarm_id=uuid.uuid4(), source_alarm_id=f"s-{uuid.uuid4().hex[:6]}", managed_element_ref=ref, severity=severity, raised_at=raised,
              probable_cause=cause, managed_function_ref=fn, ack_state="ACKNOWLEDGED" if ack or ack_after is not None else "UNACKNOWLEDGED",
              acknowledged_at=raised + datetime.timedelta(seconds=ack_after) if ack_after is not None else None)
    db.add(a)
    return a


@pytest.fixture
def fleet(db_session_factory):
    """Four elements (ELEMENTS) and nine alarms: ME-1 two critical (one acked after 60 s) and a major; ME-2 one major, one minor acked after 120 s and a
    cleared one; ME-3 one warning and one critical raised 3 hours ago; ME-4 one indeterminate. Returns the alarm ids by name."""
    with db_session_factory() as db:
        for ref, (region, cluster) in ELEMENTS.items():
            db.add(ManagedEntity(managed_element_ref=ref, entity_type="O-DU", o1_protocol="NETCONF", region=region, site_cluster=cluster,
                                 managed_function_ref=f"GNBDU-{ref.lower()}"))
        ids = {
            "c1": _alarm(db, "ME-1", "critical", 5, cause="LINK_DOWN", ack_after=60),
            "c2": _alarm(db, "ME-1", "critical", 4, cause="LINK_DOWN"),
            "m1": _alarm(db, "ME-1", "major", 4.5, cause="HIGH_TEMP"),
            "m2": _alarm(db, "ME-2", "major", 30, cause="LINK_DOWN"),
            "n2": _alarm(db, "ME-2", "minor", 31, ack_after=120),
            "x2": _alarm(db, "ME-2", "cleared", 32),
            "w3": _alarm(db, "ME-3", "warning", 1),
            "c3": _alarm(db, "ME-3", "critical", 180),
            "i4": _alarm(db, "ME-4", "indeterminate", 2),
        }
        db.commit()
        return {name: str(a.alarm_id) for name, a in ids.items()}


def _ids(items):
    return [i["alarmId"] for i in items]


# ---------------------------------------------------------------- GET /alarms: filters and the keyset cursor

def test_the_new_alarm_filters_narrow_the_list(client, fleet):
    """ack_state, open_only, probable_cause, since/until and region each narrow `GET /alarms` and combine."""
    get = lambda **p: set(_ids(client.get("/alarms", params=p).json()["items"]))  # noqa: E731
    assert get(ack_state="ACKNOWLEDGED") == {fleet["c1"], fleet["n2"]}
    assert fleet["x2"] not in get(open_only=True) and len(get(open_only=True)) == 8
    assert get(probable_cause="LINK_DOWN") == {fleet["c1"], fleet["c2"], fleet["m2"]}
    assert get(region="us") == {fleet["w3"], fleet["c3"]}
    assert get(since=(NOW - datetime.timedelta(minutes=10)).isoformat(), until=(NOW - datetime.timedelta(minutes=3)).isoformat()) == {
        fleet["c1"], fleet["c2"], fleet["m1"]}
    assert get(region="eu", ack_state="UNACKNOWLEDGED", open_only=True) == {fleet["c2"], fleet["m1"], fleet["m2"]}


def test_the_keyset_cursor_walks_the_list_worst_first_without_gaps(client, fleet):
    """`after` pages in the console order (severity, newest first, id); following `nextCursor` visits every alarm exactly once."""
    seen, cursor, pages = [], "", 0
    while True:
        body = client.get("/alarms", params={"after": cursor, "limit": 2}).json()
        assert set(body) == {"items", "limit", "nextCursor", "hasMore"} and body["limit"] == 2
        seen += body["items"]
        pages += 1
        if not body["hasMore"]:
            assert body["nextCursor"] is None
            break
        cursor = body["nextCursor"]
    assert pages == 5 and len(seen) == 9 and len(set(_ids(seen))) == 9
    assert _ids(seen)[:3] == [fleet["c2"], fleet["c1"], fleet["c3"]]                  # critical, newest first
    assert [a["severity"] for a in seen][-3:] == ["warning", "indeterminate", "cleared"]


def test_the_keyset_cursor_keeps_the_filters_and_the_scope(client, fleet):
    """A cursor page is filtered like the list: an EU-scoped caller following cursors sees only ME-1 and ME-2's alarms."""
    body = client.get("/alarms", params={"after": "", "limit": 3}, headers=EU).json()
    rest = client.get("/alarms", params={"after": body["nextCursor"], "limit": 50}, headers=EU).json()
    refs = {a["managedElementRef"] for a in body["items"] + rest["items"]}
    assert refs == {"ME-1", "ME-2"} and len(body["items"] + rest["items"]) == 6


@pytest.mark.parametrize("cursor", ["not-a-cursor", alarm_query.encode_cursor("d", NOW, uuid.uuid4())])
# a garbage string, and a well-formed cursor of the decision-record list
def test_a_bad_or_foreign_cursor_is_a_422(client, fleet, cursor):
    """A cursor that is not one of this list is refused, never guessed at."""
    assert client.get("/alarms", params={"after": cursor}).status_code == 422


def test_without_after_the_alarm_list_is_unchanged(client, fleet):
    """No `after`: the offset envelope with `total`, as before PR-GUI-9."""
    body = client.get("/alarms", params={"limit": 4}).json()
    assert body["total"] == 9 and body["offset"] == 0 and "nextCursor" not in body


# ---------------------------------------------------------------- ack and clear times

def test_ack_and_clear_set_their_times(client, fleet):
    """Acknowledging sets ackTime, a repeated ack keeps the first, un-acking drops it; clearing sets clearTime (equal to clearedAt)."""
    first = client.patch(f"/alarms/{fleet['c2']}/ack", params={"new_state": "ACKNOWLEDGED"}).json()
    assert first["ackTime"] is not None and first["clearTime"] is None
    again = client.patch(f"/alarms/{fleet['c2']}/ack", params={"new_state": "ACKNOWLEDGED"}).json()
    assert again["ackTime"] == first["ackTime"]
    assert client.patch(f"/alarms/{fleet['c2']}/ack", params={"new_state": "UNACKNOWLEDGED"}).json()["ackTime"] is None
    cleared = client.patch(f"/alarms/{fleet['c2']}/clear").json()
    assert cleared["clearTime"] is not None and cleared["clearTime"][:19] == cleared["clearedAt"][:19]


# ---------------------------------------------------------------- GET /alarms/counts

def _groups(client, group_by, headers=None, **params):
    body = client.get("/alarms/counts", params={"group_by": group_by, **params}, headers=headers or {}).json()
    assert body["groupBy"] == group_by
    return {g["key"]: g["count"] for g in body["groups"]}


def test_counts_by_severity_ack_state_cause_element_and_region(client, fleet):
    """Each grouping counts in SQL what the list holds; a NULL cause or region is the key null."""
    assert _groups(client, "severity") == {"critical": 3, "major": 2, "minor": 1, "warning": 1, "indeterminate": 1, "cleared": 1}
    assert _groups(client, "ack_state") == {"ACKNOWLEDGED": 2, "UNACKNOWLEDGED": 7}
    assert _groups(client, "probable_cause") == {"LINK_DOWN": 3, "HIGH_TEMP": 1, None: 5}
    assert _groups(client, "managed_element_ref") == {"ME-1": 3, "ME-2": 3, "ME-3": 2, "ME-4": 1}
    assert _groups(client, "region") == {"eu": 6, "us": 2, None: 1}


def test_counts_take_the_list_filters_and_the_scope(client, fleet):
    """The filters of the list apply, and an EU-scoped caller counts only what it may see."""
    assert _groups(client, "severity", open_only=True, region="eu") == {"critical": 2, "major": 2, "minor": 1}
    assert _groups(client, "managed_element_ref", headers=EU) == {"ME-1": 3, "ME-2": 3}


def test_counts_by_hour_are_the_last_24_buckets_with_severities(client, fleet):
    """`hour`: 24 UTC hourly buckets oldest first, zeros included, with bySeverity for the four graded severities."""
    groups = client.get("/alarms/counts", params={"group_by": "hour"}).json()["groups"]
    after = datetime.datetime.now(datetime.UTC)                                 # the fixture's NOW and the request may straddle an hour
    assert len(groups) == 24 and groups[-1]["key"] in {NOW.strftime("%Y-%m-%dT%H:00:00Z"), after.strftime("%Y-%m-%dT%H:00:00Z")}
    assert sum(g["count"] for g in groups) == 9
    assert sum(g["bySeverity"]["critical"] for g in groups) == 3
    three_hours_ago = (NOW - datetime.timedelta(minutes=180)).strftime("%Y-%m-%dT%H:00:00Z")
    assert next(g for g in groups if g["key"] == three_hours_ago)["bySeverity"]["critical"] >= 1


def test_an_unknown_grouping_is_a_422(client, fleet):
    """`group_by` is a closed set."""
    assert client.get("/alarms/counts", params={"group_by": "colour"}).status_code == 422


# ---------------------------------------------------------------- GET /alarms/stats and /alarms/{id}/correlated

def test_stats_give_the_mean_time_to_acknowledge(client, fleet):
    """mttaSeconds is the mean of ackTime - raisedAt over the alarms acked in the window (60 s and 120 s here), open counts what is not cleared."""
    body = client.get("/alarms/stats").json()
    assert body == {"windowHours": 24, "mttaSeconds": 90.0, "acked": 2, "open": 8}
    assert client.get("/alarms/stats", params={"region": "us"}).json() == {"windowHours": 24, "mttaSeconds": None, "acked": 0, "open": 2}


def test_correlated_alarms_are_the_same_element_within_the_window(client, fleet):
    """The rule is stated; the other alarms of the element within ±window are returned, the alarm itself and other elements' are not."""
    body = client.get(f"/alarms/{fleet['c1']}/correlated", params={"window_seconds": 120}).json()
    assert body["rule"] == "same-element-within-window" and body["windowSeconds"] == 120 and body["truncated"] is False
    assert set(_ids(body["items"])) == {fleet["c2"], fleet["m1"]}
    narrow = client.get(f"/alarms/{fleet['c1']}/correlated", params={"window_seconds": 10}).json()
    assert narrow["items"] == []


def test_correlation_of_an_alarm_outside_the_scope_is_a_404(client, fleet):
    """An alarm of an element the caller may not see is not found, like the ack route."""
    assert client.get(f"/alarms/{fleet['c3']}/correlated", headers=EU).status_code == 404
    assert client.get(f"/alarms/{uuid.uuid4()}/correlated").status_code == 404


# ---------------------------------------------------------------- decision records: keyset and CSV

def _record(db, minutes_ago, invoker="es-rapp", disposition="DIRECT", rationale=None):
    db.add(RAppDecisionRecord(decision_id=uuid.uuid4(), occurred_at=NOW - datetime.timedelta(minutes=minutes_ago), invoker_id=invoker,
                              requested_by=invoker, disposition=disposition, managed_elements=["ME-1", "ME-2"], change_count=2,
                              content_hash="0" * 64, rationale=rationale))


@pytest.fixture
def decisions(db_session_factory):
    """Five decision records one minute apart (the oldest REJECTED by ts-rapp, with a rationale that looks like a spreadsheet formula)."""
    with db_session_factory() as db:
        for m in range(1, 5):
            _record(db, m)
        _record(db, 5, invoker="ts-rapp", disposition="REJECTED", rationale="=HYPERLINK(\"http://x\")")
        db.commit()


def test_decision_records_page_by_keyset(client, decisions):
    """`after` walks the records newest first, two at a time, with no repeat; without it the list is unchanged."""
    first = client.get("/decision-records", params={"after": "", "limit": 2}).json()
    second = client.get("/decision-records", params={"after": first["nextCursor"], "limit": 2}).json()
    third = client.get("/decision-records", params={"after": second["nextCursor"], "limit": 2}).json()
    ids = [r["decisionId"] for r in first["items"] + second["items"] + third["items"]]
    assert len(set(ids)) == 5 and third["hasMore"] is False and third["nextCursor"] is None
    times = [r["occurredAt"] for r in first["items"] + second["items"] + third["items"]]
    assert times == sorted(times, reverse=True)
    assert client.get("/decision-records").json()["total"] == 5


def test_decision_records_export_as_csv(client, decisions):
    """The export streams a header and one row per record oldest first, filtered, with a formula-looking cell neutralised."""
    resp = client.get("/decision-records/export.csv", params={"since": (NOW - datetime.timedelta(days=1)).isoformat()})
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/csv")
    assert resp.headers["content-disposition"].startswith("attachment; filename=\"decision-records-")
    rows = list(csv.reader(io.StringIO(resp.text)))
    assert rows[0][:3] == ["decisionId", "occurredAt", "invokerId"] and len(rows) == 6
    assert rows[1][2] == "ts-rapp" and rows[1][rows[0].index("rationale")].startswith("'=")
    assert rows[1][rows[0].index("managedElements")] == "ME-1;ME-2"
    only = client.get("/decision-records/export.csv", params={"since": (NOW - datetime.timedelta(days=1)).isoformat(), "disposition": "REJECTED"})
    assert len(list(csv.reader(io.StringIO(only.text)))) == 2


def test_the_export_is_bounded(client, decisions):
    """`since` is required, the span is at most 31 days, and `until` must follow `since`."""
    assert client.get("/decision-records/export.csv").status_code == 422
    assert client.get("/decision-records/export.csv", params={"since": (NOW - datetime.timedelta(days=40)).isoformat()}).status_code == 422
    assert client.get("/decision-records/export.csv", params={"since": NOW.isoformat(),
                                                               "until": (NOW - datetime.timedelta(days=1)).isoformat()}).status_code == 422


def test_the_export_streams_in_batches(client, db_session_factory, monkeypatch):
    """More records than one batch are all exported (the keyset between batches neither repeats nor skips a row)."""
    monkeypatch.setattr("app.main.DECISION_EXPORT_BATCH", 2)
    with db_session_factory() as db:
        for m in range(1, 8):
            _record(db, m)
        db.commit()
    rows = list(csv.reader(io.StringIO(client.get("/decision-records/export.csv", params={"since": (NOW - datetime.timedelta(hours=1)).isoformat()}).text)))
    assert len(rows) == 8 and len({r[0] for r in rows[1:]}) == 7


# ---------------------------------------------------------------- managed entities: search, site cluster, health, worst

def _refs(client, **params):
    return [e["managedElementRef"] for e in client.get("/managed-entities", params=params).json()["items"]]


def test_managed_entities_can_be_searched_and_filtered_by_site_cluster(client, fleet):
    """`search` is a case-insensitive substring of the element or function ref (a `%` is literal); `site_cluster` filters; the view has siteCluster."""
    assert _refs(client, search="me-") == ["ME-1", "ME-2", "ME-3", "ME-4"]
    assert _refs(client, search="gnbdu-me-2") == ["ME-2"]
    assert _refs(client, search="%") == []
    assert _refs(client, site_cluster="metro-a") == ["ME-1"]
    assert client.get("/managed-entities/ME-1").json()["siteCluster"] == "metro-a"


def test_the_site_cluster_is_set_and_cleared(client, fleet):
    """PUT site-cluster sets or (null) clears it; an unknown element is a 404 and a bad value a 422."""
    assert client.put("/managed-entities/ME-3/site-cluster", json={"siteCluster": "metro-z"}).json() == {"managedElementRef": "ME-3", "siteCluster": "metro-z"}
    assert _refs(client, site_cluster="metro-z") == ["ME-3"]
    assert client.put("/managed-entities/ME-3/site-cluster", json={"siteCluster": None}).json()["siteCluster"] is None
    assert client.put("/managed-entities/ME-9/site-cluster", json={"siteCluster": "x"}).status_code == 404
    assert client.put("/managed-entities/ME-3/site-cluster", json={"siteCluster": "bad value!"}).status_code == 422


def test_the_health_map_by_region(client, fleet):
    """Per region: elements, unhealthy (an open critical or major alarm), the worst open severity; the score over all of them."""
    body = client.get("/managed-entities/health").json()
    groups = {g["key"]: g for g in body["groups"]}
    assert groups["eu"] == {"key": "eu", "elements": 2, "unhealthy": 2, "worstSeverity": "critical"}
    assert groups["us"] == {"key": "us", "elements": 1, "unhealthy": 1, "worstSeverity": "critical"}
    assert groups[None] == {"key": None, "elements": 1, "unhealthy": 0, "worstSeverity": None}      # indeterminate is not graded
    assert body["healthScore"] == 25.0


def test_the_health_map_by_site_cluster_narrowed_and_scoped(client, fleet):
    """`site_cluster` groups; `region` narrows; a scoped caller's map holds only its elements; no element at all gives a null score."""
    body = client.get("/managed-entities/health", params={"group_by": "site_cluster", "region": "eu"}).json()
    assert {g["key"] for g in body["groups"]} == {"metro-a", "metro-b"}
    assert {g["key"] for g in client.get("/managed-entities/health", headers=EU).json()["groups"]} == {"eu"}
    assert client.get("/managed-entities/health", params={"region": "nowhere"}).json() == {"groupBy": "region", "groups": [], "healthScore": None}


def test_the_worst_elements_are_ranked_in_sql(client, fleet):
    """Ranked by open critical, then open major, then open alarms; an element with no open alarm is not listed (ME-4 has one: indeterminate)."""
    worst = client.get("/managed-entities/worst").json()
    assert [w["managedElementRef"] for w in worst] == ["ME-1", "ME-3", "ME-2", "ME-4"]
    assert worst[0] == {"managedElementRef": "ME-1", "region": "eu", "siteCluster": "metro-a", "critical": 2, "major": 1, "openAlarms": 3}
    assert worst[2]["openAlarms"] == 2                                             # the cleared alarm of ME-2 is not open
    assert [w["managedElementRef"] for w in client.get("/managed-entities/worst", params={"limit": 1, "region": "eu"}).json()] == ["ME-1"]
    assert {w["managedElementRef"] for w in client.get("/managed-entities/worst", headers=EU).json()} == {"ME-1", "ME-2"}
