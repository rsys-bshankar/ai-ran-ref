"""AI-10.4, extended: a stopped rApp changes nothing through the gateway, and nothing else is affected.

Covers the kill-switch step of `_proxy`: a change by a stopped rApp (or by a module acting for one) is refused 403 `RAPP_KILLED` without calling a backend;
withdrawing, authenticating, undoing and reading stay open; another rApp is untouched; an unreadable table refuses changes (503) rather than letting them
through; the cache that spares the database. Fake SME and backend replace httpx; the kill table is in the `gateway_database` engine of `conftest.py`.
Run: `PYTHONPATH=.:../shared python -m pytest tests/test_killswitch.py -q`.
"""

import json

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY
from sqlalchemy.orm import Session

from smo_shared import audit, killswitch

from app.main import ROUTES, app

client = TestClient(app)
AUTH = {"Authorization": "Bearer t"}
INTROSPECT_URL = f"{ROUTES['/sme']}/oauth2/introspect"


class FakeResponse:
    def __init__(self, content=b"{}", status_code=200):
        self.content, self.status_code, self.headers = content, status_code, {}

    def json(self):
        return json.loads(self.content)


@pytest.fixture(autouse=True)
def fresh_rate_limiter():
    """Autouse fixture: every test starts with every caller's rate bucket full."""
    from app.main import _limiter
    _limiter.clear()


@pytest.fixture
def gateway(monkeypatch, gateway_database):
    """Fixture: a fake SME and backend behind the gateway; returns the mutable `state` dict.

    `state["sme_says"]` is the introspection answer (default: an active `rapp` caller `inv-1`), `state["forwarded"]` the (method, url) of every call that reached a
    backend, `state["engine"]` the database holding `rapp_kill`. The switch cache is off (`R1_KILL_CACHE_SECONDS=0`), so each call reads the table.
    """
    monkeypatch.setenv("R1_KILL_CACHE_SECONDS", "0")                # every call looks at the table: the cache has its own tests
    state = {"sme_says": {"active": True, "client_id": "inv-1", "role": "rapp"}, "forwarded": [], "engine": gateway_database}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, **kwargs):
            if url == INTROSPECT_URL:
                return FakeResponse(json.dumps(state["sme_says"]).encode())
            state["forwarded"].append((method, url))
            return FakeResponse(status_code=201)

    monkeypatch.setattr("app.main.httpx.AsyncClient", FakeAsyncClient)
    return state


def stop(gateway, invoker="inv-1"):
    """Stops `invoker` (default `inv-1`) by inserting its row into `rapp_kill`, as RAN NF OAM does."""
    with gateway["engine"].begin() as conn:
        conn.execute(killswitch.RAPP_KILL.insert().values(invoker_id=invoker, killed_by="op", reason="test"))


def lift(gateway, invoker="inv-1"):
    """Lifts the stop of `invoker` (default `inv-1`) by deleting its `rapp_kill` row."""
    with gateway["engine"].begin() as conn:
        conn.execute(killswitch.RAPP_KILL.delete().where(killswitch.RAPP_KILL.c.invoker_id == invoker))


CHANGES = [("POST", "/dme/data-jobs"), ("PUT", "/dme/data-jobs/j"), ("POST", "/aimgf/training-jobs"), ("PATCH", "/intent-service/intents/i/admin-state"),
           ("POST", "/dme/actions")]


@pytest.mark.parametrize("method, path", CHANGES)
def test_a_stopped_rapp_changes_nothing_and_no_backend_is_called(gateway, method, path):
    """Every kind of change by a stopped rApp is a 403 `RAPP_KILLED` and never reaches a backend (table of change routes above)."""
    stop(gateway)
    resp = client.request(method, path, headers=AUTH)
    assert resp.status_code == 403 and resp.json()["title"] == "RAPP_KILLED"
    assert gateway["forwarded"] == []


@pytest.mark.parametrize("method, path", [
    ("DELETE", "/dme/data-jobs/j"), ("DELETE", "/intent-service/intents/i"),                            # withdrawing what it made
    ("POST", "/sme/oauth2/token"), ("POST", "/ran-nf-oam/config-jobs/j/rollback"),                       # authenticating, undoing
    ("POST", "/ran-nf-oam/config-jobs"),                                                                 # refused, and recorded, by RAN NF OAM itself
    ("GET", "/dme/data-jobs"), ("GET", "/ran-nf-oam/config-jobs")])                                      # reading
def test_a_stopped_rapp_can_still_withdraw_authenticate_undo_and_read(gateway, method, path):
    """A stopped rApp can still take back what it made, get a token, undo a config job and read; the exempt routes and all reads stay open (table above)."""
    stop(gateway)
    assert client.request(method, path, headers=AUTH).status_code == 201


def test_lifting_the_switch_lets_it_change_again(gateway):
    """Lifting the stop restores the rApp's ability to change things, on the very next call."""
    stop(gateway)
    assert client.post("/dme/data-jobs", headers=AUTH).status_code == 403
    lift(gateway)
    assert client.post("/dme/data-jobs", headers=AUTH).status_code == 201


def test_another_rapp_is_not_affected(gateway):
    """Stopping one rApp does not stop another."""
    stop(gateway, "inv-other")
    assert client.post("/dme/data-jobs", headers=AUTH).status_code == 201


def test_a_module_acting_for_a_stopped_rapp_is_refused_and_for_another_is_not(gateway):
    """A module's change is refused when its `X-R1-On-Behalf-Of` names a stopped rApp, and not when it names another or none; withdrawals stay open."""
    gateway["sme_says"] = {"active": True, "client_id": "dme-module", "role": "internal"}
    stop(gateway, "api-invoker-7")
    assert client.post("/dme/data-jobs", headers={**AUTH, "X-R1-On-Behalf-Of": "api-invoker-7"}).status_code == 403
    assert client.post("/dme/data-jobs", headers={**AUTH, "X-R1-On-Behalf-Of": "api-invoker-8"}).status_code == 201
    assert client.post("/dme/data-jobs", headers=AUTH).status_code == 201                       # acting for no one
    assert client.delete("/dme/data-jobs/j", headers={**AUTH, "X-R1-On-Behalf-Of": "api-invoker-7"}).status_code == 201


def test_an_rapp_cannot_escape_by_naming_someone_else_in_the_header(gateway):
    """An rApp that sends `X-R1-On-Behalf-Of` for another rApp is still judged by its own id: the header is only believed from a module."""
    stop(gateway)
    assert client.post("/dme/data-jobs", headers={**AUTH, "X-R1-On-Behalf-Of": "api-invoker-8"}).status_code == 403


def test_the_refusal_is_audited_and_counted(gateway):
    """A kill-switch refusal increments the refusal metric (`action="killed"`) and leaves an audit row `REFUSED:RAPP_KILLED`."""
    stop(gateway)
    before = REGISTRY.get_sample_value("smo_role_refusals_total", {"module": "dme", "action": "killed"}) or 0
    client.post("/dme/data-jobs", headers=AUTH)
    assert REGISTRY.get_sample_value("smo_role_refusals_total", {"module": "dme", "action": "killed"}) == before + 1
    with Session(gateway["engine"]) as db:
        assert [(r.actor, r.result) for r in db.query(audit.AuditEntry)] == [("inv-1", "REFUSED:RAPP_KILLED")]


def test_the_switch_can_be_turned_off_at_the_gateway(gateway, monkeypatch):
    """`R1_KILL_SWITCH=off` makes the gateway ignore the table."""
    stop(gateway)
    monkeypatch.setenv("R1_KILL_SWITCH", "off")
    assert client.post("/dme/data-jobs", headers=AUTH).status_code == 201


def test_a_table_that_cannot_be_read_refuses_the_change_when_there_is_no_recent_answer(gateway, monkeypatch):
    """With the table unreadable and no recent answer, a change is a 503 `KILL_SWITCH_UNAVAILABLE` (fail closed) while a read is unaffected."""
    monkeypatch.setattr("smo_shared.db.SessionLocal", lambda: (_ for _ in ()).throw(RuntimeError("database down")))
    resp = client.post("/dme/data-jobs", headers=AUTH)
    assert resp.status_code == 503 and resp.json()["title"] == "KILL_SWITCH_UNAVAILABLE"
    assert client.get("/dme/data-jobs", headers=AUTH).status_code == 201                                  # a read needs no answer


def test_the_cache_holds_an_answer_for_its_time_and_a_stale_one_serves_a_database_outage(gateway, monkeypatch):
    """The switch cache serves an answer for `R1_KILL_CACHE_SECONDS`, then looks again; during a database outage the last answer is used for up to a minute, and after that the lookup raises."""
    clock = {"now": 1000.0}
    monkeypatch.setenv("R1_KILL_CACHE_SECONDS", "3")
    assert killswitch.is_killed("inv-1", now=lambda: clock["now"]) is False
    stop(gateway)
    clock["now"] += 2
    assert killswitch.is_killed("inv-1", now=lambda: clock["now"]) is False                              # still inside the 3 s
    clock["now"] += 2
    assert killswitch.is_killed("inv-1", now=lambda: clock["now"]) is True                               # looked again
    monkeypatch.setattr("smo_shared.db.SessionLocal", lambda: (_ for _ in ()).throw(RuntimeError("database down")))
    clock["now"] += 30
    assert killswitch.is_killed("inv-1", now=lambda: clock["now"]) is True                               # the last answer, for up to a minute
    clock["now"] += 61
    with pytest.raises(killswitch.KillSwitchUnavailable):
        killswitch.is_killed("inv-1", now=lambda: clock["now"])


def test_the_table_the_gateway_reads_is_the_one_ran_nf_oam_writes():
    """The `rapp_kill` table the gateway reads has the same name and columns as the model RAN NF OAM writes, so the two cannot drift apart unnoticed."""
    import importlib.util
    import pathlib
    source = (pathlib.Path(__file__).resolve().parents[2] / "ran-nf-oam" / "app" / "models.py").read_text()
    assert '__tablename__ = "rapp_kill"' in source and all(f"{c}: Mapped" in source for c in ("invoker_id", "reason", "killed_by"))
    assert {c.name for c in killswitch.RAPP_KILL.columns} == {"invoker_id", "reason", "killed_by"}
