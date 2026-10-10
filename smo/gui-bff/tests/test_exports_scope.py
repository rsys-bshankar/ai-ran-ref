"""The scope filters of the summary counts (GUI-9.3), the "Needs your attention" aggregate (GUI-9.8b), their event-stream topics, and the
asynchronous export jobs (GUI-9.5b): app/summary.py, app/events.py, app/exports.py.

R1 Termination and SME are test_preferences_summary.py's CountingSmo (itself test_main.py's FakeSmo), extended here with the attention lists (a page
with `items` and `total`) and RAN NF OAM's keyset-paged `GET /decision-records?after=` over `decisions` records. An export job is an asyncio task,
so the export tests sign in on a TestClient used as a context manager (`client` fixture): its event loop lives for the whole test, and the task runs
while the test polls the job. Run: `PYTHONPATH=.:../shared python -m pytest tests/test_exports_scope.py -q`.
"""

import csv
import datetime
import io
import time
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app import events, exports, summary
from app.config import Settings
from app.db import AuditEntry, Database, ExportChunk, ExportJob
from app.main import create_app, seed_users
from app.smo_client import R1Gateway
from test_events_search import parse
from test_main import PASSWORDS, R1, login
from test_preferences_summary import CountingSmo

ATTENTION_ROWS = {
    "/ran-nf-oam/alarms": [{"alarmId": f"a{i}", "managedElementRef": f"gnb-{i}", "severity": "critical", "ackState": "UNACKNOWLEDGED",
                            "specificProblem": "LINK_DOWN", "raisedAt": "2026-10-10T10:00:00Z", "correlatedNotifications": ["x"],
                            "proposedRepairActions": "reboot"} for i in range(5)],
    "/ran-nf-oam/rapp-approvals": [{"approvalId": "p1", "invokerId": "rapp-1", "status": "PENDING", "changeCount": 2, "managedElements": ["gnb-1"],
                                    "createdAt": "2026-10-10T10:00:00Z", "expiresAt": "2026-10-10T11:00:00Z", "request": {"big": "body"}}],
    "/aimgf/mlmf/reports": [{"reportId": "r1", "subscriptionId": "s1", "breachedFloor": True, "reportedAt": "2026-10-10T10:00:00Z",
                             "metrics": {"secret": 1}}],
    "/sa-smos/remedial-actions": [],
}
ATTENTION_TOTALS = {"/ran-nf-oam/alarms": 1234, "/ran-nf-oam/rapp-approvals": 18, "/aimgf/mlmf/reports": 4, "/sa-smos/remedial-actions": 0}


def decision(i: int) -> dict:
    """The i-th fake decision record (newest first in `ExportSmo.decisions`); record 1 carries a formula in its rationale."""
    return {"decisionId": f"d-{i}", "occurredAt": f"2026-10-0{1 + i % 9}T00:00:00Z", "invokerId": "rapp-1", "requestedBy": "rapp-1",
            "disposition": "DIRECT", "jobId": None, "approvalId": None, "actionId": "act", "inputsRef": None, "modelVersion": "1.0",
            "rationale": "=HYPERLINK(\"http://x\")" if i == 1 else f"because {i}", "decidedBy": None, "decidedAt": None,
            "managedElements": ["gnb-1", "gnb-2"], "changeCount": 2, "correlationId": None, "contentHash": "h", "auditSeq": i}


class ExportSmo(CountingSmo):
    """CountingSmo plus the attention lists (`ATTENTION_ROWS` / `ATTENTION_TOTALS`, a module in `down_modules` fails) and RAN NF OAM's keyset
    pages of `decisions` (the cursor is the index of the next record). Every list call is recorded in `lists`; `decision_status` forces a status."""

    def __init__(self):
        super().__init__()
        self.lists: list[httpx.Request] = []
        self.decisions = [decision(i) for i in range(7)]
        self.decision_status: int | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, request.url.params
        module = path.split("/")[1]
        if request.url.host == "r1-termination" and request.method == "GET" and path == "/ran-nf-oam/decision-records" and "after" in params:
            self.lists.append(request)
            if self.decision_status is not None:
                return httpx.Response(self.decision_status, json={"title": "BOOM"})
            start, limit = int(params["after"] or 0), int(params["limit"])
            page = self.decisions[start:start + limit]
            more = start + limit < len(self.decisions)
            return httpx.Response(200, json={"items": page, "limit": limit, "nextCursor": str(start + limit) if more else None, "hasMore": more})
        if request.url.host == "r1-termination" and request.method == "GET" and path in ATTENTION_ROWS and params.get("limit") not in (None, "1"):
            if module in self.down_modules:
                raise httpx.ConnectError("down")
            self.lists.append(request)
            return httpx.Response(200, json={"items": ATTENTION_ROWS[path][:int(params["limit"])], "total": ATTENTION_TOTALS[path],
                                             "limit": int(params["limit"]), "offset": 0})
        return super().handler(request)


@pytest.fixture
def smo():
    """A fresh ExportSmo per test."""
    return ExportSmo()


@pytest.fixture
def app(smo, tmp_path):
    """The BFF on a SQLite file with the three seeded users, talking to `smo` through the real gateway client. A file, not `sqlite://`: the
    in-memory database is one connection shared by every thread, and an export task writing a chunk while a request thread closes its session
    on that same connection can lose the write (a pooled database, as in production, has a connection per session)."""
    cfg = Settings(r1_url=R1, jwt_secret="test-secret", cookie_secure=False, admin_password=PASSWORDS["admin"],
                   operator_password=PASSWORDS["operator"], viewer_password=PASSWORDS["viewer"])
    db = Database(f"sqlite:///{tmp_path / 'gui-bff.db'}")
    seed_users(db, cfg)
    return create_app(cfg, db=db, gateway=R1Gateway(R1, db, transport=httpx.MockTransport(smo.handler)))


@pytest.fixture
def client(app):
    """A TestClient whose event loop lives for the whole test (so an export task runs to its end), signed in as the operator."""
    with TestClient(app) as c:
        sign_in(c, "operator")
        yield c


def sign_in(c: TestClient, username: str) -> TestClient:
    """Sign `c` in as `username` (replacing its session cookie) and set the CSRF header."""
    resp = c.post("/api/login", json={"username": username, "password": PASSWORDS[username]})
    assert resp.status_code == 200, resp.text
    c.headers["X-CSRF-Token"] = resp.json()["csrfToken"]
    return c


def wait(c: TestClient, job_id: str, seconds: float = 5.0) -> dict:
    """Poll the job until it is no longer QUEUED or RUNNING; its last view."""
    deadline = time.monotonic() + seconds
    while True:
        view = c.get(f"/api/exports/{job_id}").json()
        if view["state"] not in exports.ACTIVE or time.monotonic() > deadline:
            return view
        time.sleep(0.02)


def add_job(db, username="operator", **kw) -> uuid.UUID:
    """Insert an export job row directly (a job of another instance, an old one); returns its id."""
    now = datetime.datetime.now(datetime.UTC)
    values = {"id": uuid.uuid4(), "username": username, "kind": "decisions", "params": {"since": "2026-10-01T00:00:00+00:00"}, "state": "RUNNING",
              "rows": 0, "bytes": 0, "created_at": now, "expires_at": now + exports.TTL, "runner_id": "other-instance", "heartbeat_at": now, **kw}
    with db.session() as s:
        s.add(ExportJob(**values))
        s.commit()
    return values["id"]


SINCE = "2026-09-01T00:00:00Z"


# ------------------------------------------------------------------ scoped summary counts (GUI-9.3)

def test_a_scoped_summary_narrows_the_counts_whose_list_accepts_the_scope(app, smo):
    """`region` and `site_cluster` reach every count of `SCOPED_PATHS` and no other; the answer echoes the scope and names the network-wide counts."""
    body = login(app, "viewer").get("/api/summary/dashboard", params={"region": "north", "site_cluster": "c1"}).json()
    assert body["scope"] == {"region": "north", "siteCluster": "c1"}
    for call in smo.count_calls:
        params, path = call.url.params, call.url.path
        accepted = summary.SCOPED_PATHS.get(path, ())
        assert params.get("region") == ("north" if "region" in accepted else None), path
        assert params.get("site_cluster") == ("c1" if "site_cluster" in accepted else None), path
    assert "ocloudAlarms.total" in body["unscoped"] and "packages.total" in body["unscoped"] and "alarms.critical" not in body["unscoped"]
    # rApp Management's instances take a region but no site cluster: with a cluster asked for they are not narrowed by all of the scope
    assert "instances.RUNNING" in body["unscoped"]


def test_a_region_alone_narrows_the_rapp_instances_too(app, smo):
    """With a region only, rApp Management's instance counts are narrowed (they accept `region`) and so not named in `unscoped`."""
    body = login(app, "viewer").get("/api/summary/rapps", params={"region": "north"}).json()
    sent = [c for c in smo.count_calls if c.url.path == "/rapp-mgmt/instances"]
    assert sent and all(c.url.params["region"] == "north" and "site_cluster" not in c.url.params for c in sent)
    assert "instances.RUNNING" not in body["unscoped"] and "packages.total" in body["unscoped"]


def test_an_unscoped_summary_keeps_its_shape(app):
    """Without a scope, `scope` is null and `unscoped` empty: the answer the console read before GUI-9.3, plus two members."""
    body = login(app, "viewer").get("/api/summary/nav").json()
    assert body["scope"] is None and body["unscoped"] == [] and body["counts"]["alarms.critical"] == 1234


def test_each_scope_is_its_own_cache_entry(app, smo):
    """The scope is part of the cache key: the network-wide page does not answer a scoped ask, and a second scoped ask is a cache hit."""
    viewer = login(app, "viewer")
    viewer.get("/api/summary/nav")
    calls = len(smo.count_calls)
    viewer.get("/api/summary/nav", params={"region": "north"})
    assert len(smo.count_calls) > calls
    calls = len(smo.count_calls)
    viewer.get("/api/summary/nav", params={"region": "north"})
    assert len(smo.count_calls) == calls


def test_the_summary_cache_is_bounded(app, monkeypatch):
    """A caller cannot grow the cache without bound by asking new scopes: past `MAX_CACHE_ENTRIES` the oldest entries are dropped."""
    monkeypatch.setattr(summary, "MAX_CACHE_ENTRIES", 2)
    viewer = login(app, "viewer")
    for region in ("r1", "r2", "r3", "r4"):
        assert viewer.get("/api/summary/approvals", params={"region": region}).status_code == 200
    assert len(app.state.summary_page.cache) <= 2


@pytest.mark.parametrize("params", [{"region": "north east"}, {"region": "x" * 65}, {"site_cluster": "c1;drop"}, {"region": ""}])
# A space, a name longer than 64, a punctuation character and an empty name are each refused before any module is asked.
def test_a_malformed_scope_is_a_400(app, smo, params):
    """A scope value outside `SCOPE_RE` never reaches a module or the cache: 400 `INVALID_SCOPE`."""
    resp = login(app, "viewer").get("/api/summary/nav", params=params)
    assert resp.status_code == 400 and resp.json()["title"] == "INVALID_SCOPE" and smo.count_calls == []


# ------------------------------------------------------------------ "Needs your attention" (GUI-9.8b)

def test_attention_answers_the_four_groups_in_one_call_with_trimmed_rows(app, smo):
    """Each group has the module's true total and its newest `limit` rows, trimmed to the fields the console shows (no request bodies, no metrics)."""
    body = login(app, "viewer").get("/api/summary/attention").json()
    groups = {g["type"]: g for g in body["groups"]}
    assert [g["type"] for g in body["groups"]] == ["critical-alarms", "approvals", "mlmf-breaches", "escalations"]
    assert groups["critical-alarms"]["total"] == 1234 and len(groups["critical-alarms"]["items"]) == 3
    assert set(groups["critical-alarms"]["items"][0]) == set(summary.ATTENTION[0].fields)
    assert "request" not in groups["approvals"]["items"][0] and "metrics" not in groups["mlmf-breaches"]["items"][0]
    assert groups["escalations"] == {"type": "escalations", "total": 0, "items": []} and body["partial"] == []
    sent = {c.url.path: c.url.params for c in smo.lists}
    assert sent["/ran-nf-oam/alarms"]["severity"] == "critical" and sent["/ran-nf-oam/alarms"]["limit"] == "3"
    assert sent["/ran-nf-oam/rapp-approvals"]["status"] == "PENDING" and sent["/sa-smos/remedial-actions"]["outcome"] == "ESCALATED"


def test_attention_is_scoped_where_the_lists_accept_it_and_cached(app, smo):
    """The scope reaches the alarm and approval lists only; the model and assurance groups are named in `unscoped`; a repeat ask is a cache hit."""
    viewer = login(app, "viewer")
    body = viewer.get("/api/summary/attention", params={"region": "north", "limit": 5}).json()
    sent = {c.url.path: c.url.params for c in smo.lists}
    assert sent["/ran-nf-oam/alarms"]["region"] == "north" and sent["/ran-nf-oam/alarms"]["limit"] == "5"
    assert "region" not in sent["/aimgf/mlmf/reports"] and "region" not in sent["/sa-smos/remedial-actions"]
    assert body["unscoped"] == ["mlmf-breaches", "escalations"] and body["scope"] == {"region": "north", "siteCluster": None}
    calls = len(smo.lists)
    login(app, "operator").get("/api/summary/attention", params={"region": "north", "limit": 5})
    assert len(smo.lists) == calls


def test_a_down_module_leaves_its_attention_group_empty_and_named(app, smo):
    """A module that does not answer gives its group `total` null and no rows, and is named in `partial`; the other groups still come back."""
    smo.down_modules.add("aimgf")
    body = login(app, "viewer").get("/api/summary/attention").json()
    groups = {g["type"]: g for g in body["groups"]}
    assert groups["mlmf-breaches"] == {"type": "mlmf-breaches", "total": None, "items": []} and body["partial"] == ["aimgf"]
    assert groups["critical-alarms"]["total"] == 1234


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 11}, {"region": "a/b"}])
# A group size outside 1-10, and a malformed scope, are refused.
def test_bad_attention_parameters_are_refused(app, params):
    """The group size is bounded (422) and the scope checked (400) before any module is asked."""
    assert login(app, "viewer").get("/api/summary/attention", params=params).status_code in (400, 422)


# ------------------------------------------------------------------ event-stream topics

@pytest.fixture
def quick(monkeypatch):
    """A stream that lives 0.6 s, polls every 0.05 s and pings every 0.2 s, with no summary cache."""
    monkeypatch.setattr(events, "STREAM_MAX_SECONDS", 0.6)
    monkeypatch.setattr(events, "POLL_SECONDS", 0.05)
    monkeypatch.setattr(events, "PING_SECONDS", 0.2)
    monkeypatch.setattr(summary, "CACHE_SECONDS", 0.0)


def test_a_scoped_topic_streams_the_scoped_counts(app, smo, quick):
    """`summary:nav@north/c1` streams the counts of that scope, and its event names the topic and the scope."""
    got = parse(login(app, "viewer").get("/api/events", params={"topics": "summary:nav@north/c1,summary:nav"}).text)
    first = [d for e, d in got if e == "summary"][:2]
    assert first[0]["topic"] == "summary:nav@north/c1" and first[0]["scope"] == {"region": "north", "siteCluster": "c1"}
    assert "topic" not in first[1] and first[1]["page"] == "nav"
    assert any(c.url.params.get("site_cluster") == "c1" for c in smo.count_calls)


def test_the_attention_topic_streams_the_groups(app, quick):
    """`summary:attention` sends `event: attention` with the groups, every group type named as changed the first time."""
    got = parse(login(app, "viewer").get("/api/events", params={"topics": "summary:attention"}).text)
    attention = [d for e, d in got if e == "attention"]
    assert attention and attention[0]["page"] == "attention" and len(attention[0]["groups"]) == 4
    assert set(attention[0]["changed"]) == {"critical-alarms", "approvals", "mlmf-breaches", "escalations"}


@pytest.mark.parametrize("topic", ["summary:nav@", "summary:nav@north/", "summary:nav@no rth", "summary:attention@a/b/c", "summary:nope@north"])
# An empty region, an empty cluster, a space, a cluster with a slash and an unknown page are each refused.
def test_a_malformed_scoped_topic_is_a_400(app, topic):
    """Only `summary:<page>[@<region>[/<site_cluster>]]` with names `SCOPE_RE` accepts opens a stream."""
    resp = login(app, "viewer").get("/api/events", params={"topics": topic})
    assert resp.status_code == 400 and resp.json()["title"] == "UNKNOWN_TOPIC"


def test_parse_topic_reads_the_page_and_the_scope():
    """The parser splits a topic into its key, page, region and site cluster."""
    assert events.parse_topic("summary:alarms@north/c1") == events.Topic("alarms@north/c1", "alarms", "north", "c1")
    assert events.parse_topic("summary:attention") == events.Topic("attention", "attention")
    assert events.parse_topic("alarms") is None


# ------------------------------------------------------------------ export jobs (GUI-9.5b)

def test_a_decision_export_runs_to_done_and_its_file_is_the_records_as_safe_csv(client, smo, app):
    """The job pages RAN NF OAM by keyset with the filters, ends DONE with every record, and the file neutralises formulas and joins lists."""
    resp = client.post("/api/exports", json={"kind": "decisions", "since": SINCE, "until": "2026-10-10T00:00:00Z", "invokerId": "rapp-1",
                                             "disposition": "DIRECT", "region": "north", "siteCluster": "c1"})
    assert resp.status_code == 202 and resp.json()["state"] == "QUEUED"
    view = wait(client, resp.json()["id"])
    assert view["state"] == "DONE" and view["rows"] == 7 and view["fileUrl"] == f"/api/exports/{view['id']}/file" and view["error"] is None
    first = smo.lists[0].url.params
    assert (first["after"], first["invoker_id"], first["disposition"], first["region"], first["site_cluster"]) == ("", "rapp-1", "DIRECT", "north", "c1")
    assert first["since"].startswith("2026-09-01T00:00:00") and first["until"].startswith("2026-10-10T00:00:00")
    file = client.get(view["fileUrl"])
    assert file.status_code == 200 and file.headers["content-type"].startswith("text/csv") and "attachment" in file.headers["content-disposition"]
    assert int(file.headers["content-length"]) == view["bytes"] == len(file.content)
    rows = list(csv.reader(io.StringIO(file.text)))
    assert tuple(rows[0]) == exports.DECISION_COLUMNS and [r[0] for r in rows[1:]] == [f"d-{i}" for i in range(7)]
    assert rows[2][10].startswith("'=") and rows[1][13] == "gnb-1;gnb-2"


def test_pages_follow_the_cursor(client, smo, monkeypatch):
    """With a page of 3, seven records take three calls, each sending the previous page's `nextCursor` as `after`."""
    monkeypatch.setattr(exports, "DECISION_PAGE", 3)
    view = wait(client, client.post("/api/exports", json={"kind": "decisions", "since": SINCE}).json()["id"])
    assert view["rows"] == 7 and [c.url.params["after"] for c in smo.lists] == ["", "3", "6"]


def test_the_file_is_stored_in_chunks_of_at_most_the_chunk_size(client, app, monkeypatch):
    """The CSV is cut into pieces of at most `CHUNK_BYTES`, and the download is their concatenation, byte for byte."""
    monkeypatch.setattr(exports, "CHUNK_BYTES", 100)
    view = wait(client, client.post("/api/exports", json={"kind": "decisions", "since": SINCE}).json()["id"])
    with app.state.db.session() as s:
        chunks = s.query(ExportChunk).filter(ExportChunk.job_id == uuid.UUID(view["id"])).order_by(ExportChunk.seq).all()
    assert len(chunks) > 3 and all(len(c.data) <= 100 for c in chunks)
    assert client.get(view["fileUrl"]).content == b"".join(c.data for c in chunks)


def test_an_audit_export_is_admin_only_and_reads_the_bffs_own_log(client, app):
    """An operator may not export the audit log; an admin's job holds the log's rows (filtered by action), newest first."""
    assert client.post("/api/exports", json={"kind": "audit", "since": SINCE}).status_code == 403
    sign_in(client, "admin")
    view = wait(client, client.post("/api/exports", json={"kind": "audit", "since": "2000-01-01T00:00:00Z", "action": "LOGIN"}).json()["id"])
    rows = list(csv.reader(io.StringIO(client.get(view["fileUrl"]).text)))
    assert rows[0][:3] == ["id", "at", "username"] and len(rows) == 1 + view["rows"] and view["rows"] >= 2
    assert all(r[4] == "LOGIN" for r in rows[1:]) and int(rows[1][0]) > int(rows[2][0])


def test_a_viewer_may_not_export_decisions(app):
    """Decision exports need role operator."""
    resp = login(app, "viewer").post("/api/exports", json={"kind": "decisions", "since": SINCE})
    assert resp.status_code == 403 and resp.json()["title"] == "FORBIDDEN"


@pytest.mark.parametrize("body", [{"kind": "decisions", "since": SINCE, "username": "x"}, {"kind": "audit", "since": SINCE, "region": "north"},
                                  {"kind": "decisions", "since": SINCE, "until": SINCE}, {"kind": "decisions", "since": SINCE, "region": "a b"},
                                  {"kind": "decisions", "since": SINCE, "extra": 1}, {"kind": "alarms", "since": SINCE},
                                  {"kind": "decisions"}])
# A filter of the other kind, an empty span, a malformed region, an unknown field, an unknown kind and a missing `since` are refused.
def test_malformed_export_requests_are_a_422(app, body):
    """Nothing is queued unless the request is a well-formed export the caller's role may make (the admin, so the role is never the reason)."""
    resp = login(app, "admin").post("/api/exports", json=body)
    assert resp.status_code == 422
    with app.state.db.session() as s:
        assert s.query(ExportJob).count() == 0


def test_creating_an_export_needs_the_csrf_token(app):
    """The POST is a state change: without the double-submit token it is refused."""
    c = login(app, "operator")
    del c.headers["X-CSRF-Token"]
    assert c.post("/api/exports", json={"kind": "decisions", "since": SINCE}).status_code == 403


def test_requests_and_downloads_are_audited(client, app):
    """`EXPORT_REQUESTED` names the job, the kind and the filters; `EXPORT_DOWNLOADED` the job."""
    view = wait(client, client.post("/api/exports", json={"kind": "decisions", "since": SINCE, "invokerId": "rapp-9"}).json()["id"])
    client.get(view["fileUrl"])
    with app.state.db.session() as s:
        actions = {e.action: e.detail for e in s.query(AuditEntry).all()}
    assert view["id"] in actions["EXPORT_REQUESTED"] and "invokerId=rapp-9" in actions["EXPORT_REQUESTED"]
    assert view["id"] in actions["EXPORT_DOWNLOADED"]


def test_a_user_sees_their_own_jobs_and_an_admin_sees_all(client, app):
    """Another user's job is a 404 to an operator and absent from their list; an admin lists and reads it."""
    other = add_job(app.state.db, username="viewer", state="DONE")
    mine = wait(client, client.post("/api/exports", json={"kind": "decisions", "since": SINCE}).json()["id"])
    assert [j["id"] for j in client.get("/api/exports").json()["items"]] == [mine["id"]]
    assert client.get(f"/api/exports/{other}").status_code == 404 and client.get(f"/api/exports/{other}/file").status_code == 404
    sign_in(client, "admin")
    assert {j["id"] for j in client.get("/api/exports").json()["items"]} == {mine["id"], str(other)}
    assert [j["id"] for j in client.get("/api/exports", params={"username": "viewer"}).json()["items"]] == [str(other)]
    assert client.get(f"/api/exports/{other}").status_code == 200


def test_at_most_three_jobs_of_one_user_run_at_once(client, app):
    """A fourth QUEUED or RUNNING job of one user is a 429; another user's jobs do not count."""
    for _ in range(exports.MAX_ACTIVE_PER_USER):
        add_job(app.state.db)
    add_job(app.state.db, username="admin")
    resp = client.post("/api/exports", json={"kind": "decisions", "since": SINCE})
    assert resp.status_code == 429 and resp.json()["title"] == "TOO_MANY_EXPORTS"


def test_a_job_whose_instance_stopped_is_failed_interrupted_when_read(client, app):
    """A RUNNING job with a stale heartbeat (its instance restarted) reads FAILED "interrupted" and no longer counts against the user's limit."""
    old = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=exports.STALE_SECONDS + 5)
    stale = add_job(app.state.db, heartbeat_at=old)
    fresh = add_job(app.state.db)
    view = client.get(f"/api/exports/{stale}").json()
    assert view["state"] == "FAILED" and view["error"].startswith("interrupted") and view["finishedAt"] is not None
    assert client.get(f"/api/exports/{fresh}").json()["state"] == "RUNNING"


def test_a_job_this_instance_lost_is_failed_at_once(client, app):
    """A RUNNING job that names this process as its runner but has no task here (the task died) is an orphan without waiting for the heartbeat."""
    lost = add_job(app.state.db, runner_id=app.state.exports.runner_id)
    listed = client.get("/api/exports").json()["items"]
    assert [(j["id"], j["state"]) for j in listed] == [(str(lost), "FAILED")]


def test_stopping_the_instance_leaves_its_running_jobs_interrupted(app, monkeypatch):
    """The lifespan's end cancels this instance's export tasks and each job is left FAILED "interrupted", not RUNNING for ever."""
    import asyncio

    async def endless(self, params):
        # a source that never ends: the job is still running when the instance stops
        while True:
            await asyncio.sleep(0.01)
            yield []

    monkeypatch.setattr(exports.ExportRunner, "_decisions", endless)
    with TestClient(app) as c:
        sign_in(c, "operator")
        job_id = c.post("/api/exports", json={"kind": "decisions", "since": SINCE}).json()["id"]
        time.sleep(0.1)
        assert c.get(f"/api/exports/{job_id}").json()["state"] == "RUNNING"
    with app.state.db.session() as s:
        job = s.get(ExportJob, uuid.UUID(job_id))
    assert job.state == "FAILED" and job.error.startswith("interrupted")


def test_an_expired_job_reads_expired_and_is_purged_by_the_next_create(client, app):
    """A finished job past its expiry is EXPIRED, its file a 410; the next create deletes it and its chunks."""
    past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=1)
    old = add_job(app.state.db, state="DONE", expires_at=past)
    with app.state.db.session() as s:
        s.add(ExportChunk(job_id=old, seq=0, data=b"x"))
        s.commit()
    assert client.get(f"/api/exports/{old}").json()["state"] == "EXPIRED"
    assert client.get(f"/api/exports/{old}/file").status_code == 410
    wait(client, client.post("/api/exports", json={"kind": "decisions", "since": SINCE}).json()["id"])
    with app.state.db.session() as s:
        assert s.get(ExportJob, old) is None and s.query(ExportChunk).filter(ExportChunk.job_id == old).count() == 0


def test_a_file_that_is_not_ready_is_a_409(client, app):
    """A running or failed job has no file yet: 409 `EXPORT_NOT_READY`."""
    running = add_job(app.state.db)
    resp = client.get(f"/api/exports/{running}/file")
    assert resp.status_code == 409 and resp.json()["title"] == "EXPORT_NOT_READY"


def test_deleting_a_job_removes_it_and_its_file(client, app):
    """DELETE removes the row and every chunk, is audited, and the job is a 404 afterwards."""
    view = wait(client, client.post("/api/exports", json={"kind": "decisions", "since": SINCE}).json()["id"])
    assert client.delete(f"/api/exports/{view['id']}").status_code == 204
    assert client.get(f"/api/exports/{view['id']}").status_code == 404
    with app.state.db.session() as s:
        assert s.query(ExportChunk).count() == 0 and s.query(AuditEntry).filter(AuditEntry.action == "EXPORT_DELETED").count() == 1


def test_a_source_that_fails_ends_the_job_failed_with_the_reason(client, smo, monkeypatch):
    """A 5xx after every retry, or a refusal, ends the job FAILED with a reason the console can show, never RUNNING for ever."""
    monkeypatch.setattr(exports, "FETCH_ATTEMPTS", 1)
    smo.decision_status = 503
    view = wait(client, client.post("/api/exports", json={"kind": "decisions", "since": SINCE}).json()["id"])
    assert view["state"] == "FAILED" and "did not answer" in view["error"] and view["fileUrl"] is None
    smo.decision_status = 403
    view = wait(client, client.post("/api/exports", json={"kind": "decisions", "since": SINCE}).json()["id"])
    assert view["state"] == "FAILED" and "refused" in view["error"]


def test_a_job_stops_at_the_row_limit_and_says_so(client, monkeypatch):
    """At `MAX_ROWS` the file stops; the job is DONE with that many rows and an `error` saying the file is cut there."""
    monkeypatch.setattr(exports, "MAX_ROWS", 3)
    view = wait(client, client.post("/api/exports", json={"kind": "decisions", "since": SINCE}).json()["id"])
    assert view["state"] == "DONE" and view["rows"] == 3 and "limit" in view["error"]
    assert len(client.get(view["fileUrl"]).text.strip().splitlines()) == 1 + 3
