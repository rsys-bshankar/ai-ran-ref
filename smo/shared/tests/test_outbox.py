"""smo_shared.outbox — enqueue in the caller's transaction, send after commit, recover after a crash (PR-MSG-1.3, 1.4).
Runs on file SQLite and, with `SMO_TEST_POSTGRES_URL`, on real Postgres."""

import datetime
import os
import threading

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from smo_shared import outbox, webhook
from smo_shared.db import Base
from smo_shared.outbox import DEAD, PENDING, SENT, NotificationOutbox, drain, enqueue

T0 = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=datetime.UTC)


@pytest.fixture(params=["sqlite", "postgres"])
def engine(request, tmp_path):
    if request.param == "postgres":
        if not os.environ.get("SMO_TEST_POSTGRES_URL"):
            pytest.skip("SMO_TEST_POSTGRES_URL not set")
        engine = create_engine(os.environ["SMO_TEST_POSTGRES_URL"], future=True)
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'outbox.db'}", future=True)
    Base.metadata.drop_all(engine, tables=[NotificationOutbox.__table__])
    Base.metadata.create_all(engine, tables=[NotificationOutbox.__table__])
    yield engine
    Base.metadata.drop_all(engine, tables=[NotificationOutbox.__table__])
    engine.dispose()


@pytest.fixture
def network(monkeypatch):
    """Stands in for the destinations: records every POST and answers with `status` (None: unreachable)."""
    class Sent(list):
        behaviour = {"status": 200}

    sent = Sent()
    behaviour = sent.behaviour

    def post(destination, json, timeout=5.0):
        sent.append((destination, json))
        status = behaviour["status"]
        return None if status is None else httpx.Response(status)

    monkeypatch.setattr(webhook, "post_webhook", post)
    return sent


def rows(engine):
    with Session(engine) as db:
        return db.scalars(select(NotificationOutbox).order_by(NotificationOutbox.created_at)).all()


def test_enqueue_inserts_in_the_callers_transaction_and_a_rollback_removes_it(engine, network):
    with Session(engine) as db:
        enqueue(db, "http://consumer/cb", {"n": 1})
        db.flush()
        assert db.scalar(select(func.count()).select_from(NotificationOutbox)) == 1
        db.rollback()
    assert rows(engine) == [] and network == []                      # nothing stored, nothing sent


def test_nothing_is_sent_before_the_commit(engine, network):
    with Session(engine) as db:
        enqueue(db, "http://consumer/cb", {"n": 1})
        db.flush()
        assert network == []
        db.commit()
    assert network == [("http://consumer/cb", {"n": 1})]


def test_a_commit_sends_what_it_enqueued_and_marks_it_sent(engine, network):
    with Session(engine) as db:
        enqueue(db, "http://a/cb", {"n": 1}, module="dme")
        enqueue(db, "http://b/cb", {"n": 2}, module="dme")
        db.commit()
    assert [d for d, _ in network] == ["http://a/cb", "http://b/cb"]
    assert [(r.status, r.attempts, r.module) for r in rows(engine)] == [(SENT, 1, "dme")] * 2


def test_a_commit_that_enqueued_nothing_sends_nothing(engine, network):
    with Session(engine) as db:
        db.execute(select(1))
        db.commit()
    assert network == []


def test_a_destination_the_ssrf_guard_refuses_is_dropped_at_enqueue(engine, network):
    with Session(engine) as db:
        assert enqueue(db, "http://169.254.169.254/latest", {"n": 1}) is None
        assert enqueue(db, None, {"n": 1}) is None
        assert enqueue(db, "file:///etc/passwd", {"n": 1}) is None
        db.commit()
    assert rows(engine) == [] and network == []


def test_the_module_defaults_to_the_containers_module(engine, network, monkeypatch):
    monkeypatch.setenv("MODULE", "sme")
    with Session(engine) as db:
        assert enqueue(db, "http://a/cb", {}).module == "sme"


def test_a_crash_between_commit_and_send_leaves_a_pending_row_that_a_later_drain_sends(engine, network, monkeypatch):
    monkeypatch.setenv("SMO_OUTBOX_INLINE_DRAIN", "false")           # the process "dies" before any inline send
    with Session(engine) as db:
        enqueue(db, "http://consumer/cb", {"n": 1})
        db.commit()
    assert network == [] and [(r.status, r.attempts) for r in rows(engine)] == [(PENDING, 0)]

    monkeypatch.delenv("SMO_OUTBOX_INLINE_DRAIN")                    # the restarted process sweeps
    assert drain(engine) == {"sent": 1, "retry": 0, "dead": 0}
    assert network == [("http://consumer/cb", {"n": 1})] and rows(engine)[0].status == SENT


def test_a_crash_during_the_send_is_retried_once_the_lease_runs_out(engine, network, monkeypatch):
    def die(destination, payload):
        raise SystemExit("process killed mid-send")

    real_send = outbox._send
    with Session(engine) as db:
        enqueue(db, "http://consumer/cb", {"n": 1})
        monkeypatch.setattr(outbox, "_send", die)
        with pytest.raises(SystemExit):
            db.commit()                                               # committed, claimed, then the process dies
    monkeypatch.setattr(outbox, "_send", real_send)                   # the restarted process
    assert [(r.status, r.attempts) for r in rows(engine)] == [(PENDING, 1)]
    assert drain(engine) == {"sent": 0, "retry": 0, "dead": 0}      # still leased: another replica may be sending it
    later = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=outbox.LEASE_SECONDS + 5)
    assert drain(engine, now=later)["sent"] == 1


def test_an_unreachable_destination_is_retried_with_backoff_and_then_dead(engine, network):
    network.behaviour["status"] = None
    with Session(engine) as db:
        enqueue(db, "http://down/cb", {"n": 1})
        db.commit()                                                   # the inline attempt fails
    row = rows(engine)[0]
    assert (row.status, row.attempts, row.last_error) == (PENDING, 1, "no answer from the destination")
    assert drain(engine) == {"sent": 0, "retry": 0, "dead": 0}      # not due yet: a dead destination does not slow later work

    clock = datetime.datetime.now(datetime.UTC)
    for attempt in range(2, outbox.MAX_ATTEMPTS + 1):
        clock += datetime.timedelta(hours=1)
        result = drain(engine, now=clock)
        assert result == ({"sent": 0, "retry": 1, "dead": 0} if attempt < outbox.MAX_ATTEMPTS else {"sent": 0, "retry": 0, "dead": 1})
    row = rows(engine)[0]
    assert (row.status, row.attempts) == (DEAD, outbox.MAX_ATTEMPTS)
    assert drain(engine, now=clock + datetime.timedelta(days=1)) == {"sent": 0, "retry": 0, "dead": 0}


def test_a_5xx_is_retried_and_a_4xx_is_not(engine, network):
    network.behaviour["status"] = 503
    with Session(engine) as db:
        enqueue(db, "http://flaky/cb", {})
        db.commit()
    assert (rows(engine)[0].status, rows(engine)[0].last_error) == (PENDING, "destination answered 503")

    network.behaviour["status"] = 404
    with Session(engine) as db:
        enqueue(db, "http://refuses/cb", {})
        db.commit()
    assert [r.status for r in rows(engine)] == [PENDING, SENT]       # a 4xx is the destination's answer: delivered, not retried


def test_two_drains_never_send_the_same_row_twice(engine, network, monkeypatch):
    monkeypatch.setenv("SMO_OUTBOX_INLINE_DRAIN", "false")
    with Session(engine) as db:
        for n in range(20):
            enqueue(db, f"http://consumer/{n}", {"n": n})
        db.commit()
    threads = [threading.Thread(target=drain, args=(engine,)) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(d for d, _ in network) == sorted(f"http://consumer/{n}" for n in range(20))
    assert {r.status for r in rows(engine)} == {SENT}


def test_the_inline_drain_never_fails_the_callers_commit(engine, network, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("the outbox is broken")

    monkeypatch.setattr(outbox, "drain", broken)
    with Session(engine) as db:
        enqueue(db, "http://consumer/cb", {})
        db.commit()                                                   # does not raise
    assert rows(engine)[0].status == PENDING


def test_a_full_drain_removes_old_sent_rows_only(engine, network, monkeypatch):
    monkeypatch.setenv("SMO_OUTBOX_SENT_RETENTION_SECONDS", "3600")
    with Session(engine) as db:
        enqueue(db, "http://consumer/cb", {})
        db.commit()
    assert len(rows(engine)) == 1
    drain(engine)
    assert len(rows(engine)) == 1                                    # recent: kept for the delivery log
    drain(engine, now=datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=2))
    assert rows(engine) == []


def test_the_pending_ids_do_not_leak_across_a_rollback(engine, network):
    with Session(engine) as db:
        enqueue(db, "http://gone/cb", {})
        db.rollback()
        enqueue(db, "http://kept/cb", {})
        db.commit()
    assert network == [("http://kept/cb", {})]
