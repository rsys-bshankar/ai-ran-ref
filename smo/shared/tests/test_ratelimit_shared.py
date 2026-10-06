"""smo_shared.ratelimit.SharedTokenBuckets — the limiter state in the database, so replicas share one budget (PR-SEC-8.5).

Runs on SQLite (the unit-test engine): the same upsert Postgres runs, with min/max for LEAST/GREATEST. The Postgres statement itself is exercised by
tests_integration/test_rate_limit_postgres.py (skipped without SMO_TEST_POSTGRES_URL).
"""

import logging
import threading

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from smo_shared import metrics, ratelimit
from smo_shared.db import Base
from smo_shared.ratelimit import FAIL_BACKOFF_SECONDS, PURGE_INTERVAL_SECONDS, RateBucket, SharedTokenBuckets, TokenBuckets, store_from_environment
from smo_shared.testing import make_test_engine


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def factory():
    engine = make_test_engine()
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, future=True)


def shared(factory, rate=2.0, burst=4.0):
    clock, ticks = Clock(), Clock()
    return SharedTokenBuckets(lambda: rate, lambda: burst, clock, factory, ticks), clock, ticks


def _callers(factory):
    with factory() as db:
        return sorted(db.execute(select(RateBucket.caller)).scalars().all())


def test_the_shared_bucket_is_the_same_token_bucket_as_the_in_process_one(factory):
    b, clock, _ = shared(factory, rate=2, burst=4)
    assert [b.take("a") for _ in range(4)] == [None] * 4
    assert b.take("a") == 1                                    # empty: half a second to the next token, rounded up
    clock.now += 0.5
    assert b.take("a") is None and b.take("a") == 1
    clock.now += 10                                            # idle: refilled, not past its size
    assert [b.take("a") for _ in range(4)] == [None] * 4 and b.take("a") is not None


def test_a_refused_request_takes_nothing_and_retry_after_is_whole_seconds_to_the_next_token(factory):
    b, clock, _ = shared(factory, rate=0.25, burst=1)
    assert b.take("a") is None
    assert [b.take("a") for _ in range(3)] == [4, 4, 4]        # hammering while empty does not dig the hole deeper
    clock.now += 3
    assert b.take("a") == 1
    clock.now += 1
    assert b.take("a") is None


def test_callers_do_not_share_a_bucket(factory):
    b, _, _ = shared(factory, rate=1, burst=1)
    assert b.take("noisy") is None and b.take("noisy") is not None
    assert b.take("quiet") is None


def test_two_limiters_over_one_database_share_one_budget(factory):
    clock, ticks = Clock(), Clock()
    first = SharedTokenBuckets(lambda: 0.001, lambda: 5.0, clock, factory, ticks)
    second = SharedTokenBuckets(lambda: 0.001, lambda: 5.0, clock, factory, ticks)       # another replica
    outcomes = [limiter.take("a") for limiter in (first, second) * 3]                   # six calls, alternating replicas
    assert outcomes[:5] == [None] * 5 and outcomes[5] is not None   # 5 in all, not 5 each
    assert second.take("other") is None                         # another caller's bucket is untouched
    memory = [TokenBuckets(lambda: 0.001, lambda: 5.0), TokenBuckets(lambda: 0.001, lambda: 5.0)]
    assert [m.take("a") for m in memory * 3] == [None] * 6      # what the in-process limiter gives two replicas: 5 each


def test_a_rate_of_zero_turns_the_shared_limiter_off_and_touches_nothing(factory):
    b, _, _ = shared(factory, rate=0, burst=1)
    assert all(b.take("a") is None for _ in range(20)) and len(b) == 0


def test_a_burst_below_one_never_lets_a_request_through_as_in_memory(factory):
    b, _, _ = shared(factory, rate=1, burst=0.5)
    assert b.take("a") is not None and b.take("a") is not None
    assert TokenBuckets(lambda: 1.0, lambda: 0.5, Clock()).take("a") is not None


def test_the_settings_are_read_on_every_call_and_a_clock_stepping_back_refills_nothing(factory):
    state = {"rate": 1.0}
    clock, ticks = Clock(), Clock()
    b = SharedTokenBuckets(lambda: state["rate"], lambda: 1.0, clock, factory, ticks)
    assert b.take("a") is None and b.take("a") is not None
    clock.now -= 500                                           # another replica's clock is behind: no negative refill
    assert b.take("a") is not None
    state["rate"] = 0
    assert b.take("a") is None


def test_the_row_holds_the_bucket_state(factory):
    b, clock, _ = shared(factory, rate=1, burst=3)
    b.take("a")
    b.take("a")
    with factory() as db:
        row = db.execute(select(RateBucket)).scalar_one()
    assert (row.caller, row.tokens, row.refilled_at, row.last_allowed) == ("a", 1.0, clock.now, True)


def test_idle_buckets_are_purged_once_full_again(factory):
    b, clock, _ = shared(factory, rate=1, burst=2)
    b.take("busy")
    b.take("gone")
    clock.now += 1.5                                            # busy: 1 + 1.5 refilled, capped at 2
    b.take("busy")
    b.take("busy")
    clock.now += 1.0
    assert b.purge() == 1                                       # "gone" is full again (1 + 2.5 >= 2); "busy" (0 + 1 < 2) is still in use
    assert _callers(factory) == ["busy"]


def test_take_purges_by_itself_at_most_once_per_interval(factory):
    b, clock, ticks = shared(factory, rate=1, burst=2)
    b.take("a")
    clock.now += 100                                            # a is full again
    ticks.now += PURGE_INTERVAL_SECONDS - 1
    b.take("b")
    assert _callers(factory) == ["a", "b"]                      # not yet an interval since the limiter was made
    ticks.now += 2
    b.take("c")
    assert _callers(factory) == ["b", "c"]                      # a was purged; b (just used) and c are not full


def test_a_purge_that_fails_is_ignored(factory):
    b, _, ticks = shared(factory)
    b.take("a")
    ticks.now += PURGE_INTERVAL_SECONDS + 1
    state = {"n": 0}

    def second_session_fails():
        state["n"] += 1
        if state["n"] > 1:
            raise RuntimeError("database down")
        return factory()
    b._session_factory = second_session_fails                   # the take's session works, the purge's does not
    assert b.take("a") is None


def broken_factory():
    raise ConnectionError("database down")


def test_when_the_store_fails_the_limiter_fails_open_to_the_replicas_own_bucket(caplog):
    clock, ticks = Clock(), Clock()
    b = SharedTokenBuckets(lambda: 0.001, lambda: 2.0, clock, broken_factory, ticks)
    errors, fallbacks = metrics.RATE_STORE_ERRORS._value.get(), metrics.RATE_STORE_FALLBACKS._value.get()
    with caplog.at_level(logging.WARNING, logger="smo_shared.ratelimit"):
        outcomes = [b.take("a") for _ in range(4)]
    assert outcomes[:2] == [None, None] and outcomes[2] is not None      # not refused because of the outage, but still bounded: 2 here, not unlimited
    assert metrics.RATE_STORE_ERRORS._value.get() == errors + 1          # one statement failed; the rest were not even tried (back-off)
    assert metrics.RATE_STORE_FALLBACKS._value.get() == fallbacks + 4
    assert sum("rate limiter store unavailable" in r.getMessage() for r in caplog.records) == 1


def test_the_store_is_tried_again_after_the_back_off(factory):
    clock, ticks = Clock(), Clock()
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("blip")
        return factory()
    b = SharedTokenBuckets(lambda: 0.001, lambda: 2.0, clock, flaky, ticks)
    assert b.take("a") is None and calls["n"] == 1               # failed, answered by the local bucket
    assert b.take("a") is None and calls["n"] == 1               # inside the back-off: the store is not tried
    ticks.now += FAIL_BACKOFF_SECONDS + 0.1
    assert b.take("a") is None and calls["n"] == 2               # tried again, and now it is the shared bucket
    assert len(b) == 1


def test_the_store_setting_is_memory_unless_postgres_is_asked_for():
    assert store_from_environment({}) == "memory" and store_from_environment({"R1_RATE_STORE": ""}) == "memory"
    assert store_from_environment({"R1_RATE_STORE": " Postgres "}) == "postgres"
    assert store_from_environment({"R1_RATE_STORE": "  "}) == "memory" and store_from_environment({"R1_RATE_STORE": "MEMORY"}) == "memory"
    with pytest.raises(RuntimeError) as refused:
        store_from_environment({"R1_RATE_STORE": "redis"})
    assert str(refused.value) == "R1_RATE_STORE must be 'memory' or 'postgres', not 'redis'"


def test_clear_empties_the_store_and_the_fallback(factory):
    b, _, _ = shared(factory, rate=1, burst=1)
    b.take("a")
    b.clear()
    assert len(b) == 0 and b.take("a") is None


def test_the_default_session_factory_is_the_shared_database(monkeypatch, factory):
    monkeypatch.setattr("smo_shared.db.SessionLocal", factory)
    b = SharedTokenBuckets(lambda: 1.0, lambda: 1.0, Clock(), None, Clock())
    assert b.take("a") is None and b.take("a") is not None and _callers(factory) == ["a"]


def test_the_statements_name_each_dialects_functions():
    take_pg, purge_pg = ratelimit._statements("postgresql")
    take_lite, purge_lite = ratelimit._statements("sqlite")
    assert "LEAST(" in str(take_pg) and "GREATEST(" in str(take_pg) and "GREATEST(" in str(purge_pg)
    assert "min(" in str(take_lite) and "LEAST" not in str(take_lite) and "max(" in str(purge_lite)
    assert "ON CONFLICT (caller) DO UPDATE" in str(take_pg) and "RETURNING tokens, last_allowed" in str(take_pg)


def test_concurrent_callers_never_get_more_than_the_burst_from_the_shared_store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'limiter.db'}", connect_args={"timeout": 30})      # a file: real connections, one per thread
    Base.metadata.create_all(engine)
    b, _, _ = shared(sessionmaker(bind=engine, autoflush=False, future=True), rate=0.000001, burst=30)
    errors = metrics.RATE_STORE_ERRORS._value.get()
    allowed, lock = [], threading.Lock()

    def worker():
        for _ in range(10):
            if b.take("a") is None:
                with lock:
                    allowed.append(1)
    threads = [threading.Thread(target=worker) for _ in range(5)]
    [t.start() for t in threads]
    [t.join(timeout=60) for t in threads]
    assert metrics.RATE_STORE_ERRORS._value.get() == errors      # no statement failed (a failure would have fallen back and hidden the count)
    assert len(allowed) == 30


# --- found by the mutation pilot (V-2c): boundaries, exact counts, the fallback's clock, the log lines ---

def flaky_once(factory, calls):
    def make():
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("blip")
        return factory()
    return make


def test_the_purge_runs_exactly_when_an_interval_has_passed_and_the_interval_restarts_from_it(factory):
    b, clock, ticks = shared(factory, rate=1, burst=2)         # made at tick 1000
    b.take("a")
    clock.now += 100                                           # a would be full again, and so would anything idle from here on
    ticks.now = 1000 + PURGE_INTERVAL_SECONDS - 0.001
    b.take("b")
    assert _callers(factory) == ["a", "b"]                     # a hair under the interval: no purge
    ticks.now = 1000 + PURGE_INTERVAL_SECONDS
    b.take("c")
    assert _callers(factory) == ["b", "c"]                     # exactly the interval: purge
    clock.now += 100                                           # b and c idle now
    ticks.now = 1000 + 2 * PURGE_INTERVAL_SECONDS - 0.001
    b.take("d")
    assert _callers(factory) == ["b", "c", "d"]                # the next interval counts from the purge, not from the start
    ticks.now = 1000 + 2 * PURGE_INTERVAL_SECONDS
    b.take("e")
    assert _callers(factory) == ["d", "e"]


def test_purge_deletes_what_would_be_full_again_by_the_current_settings_and_counts_it(factory):
    state = {"burst": 3.0}
    clock, ticks = Clock(), Clock()
    b = SharedTokenBuckets(lambda: 1, lambda: state["burst"], clock, factory, ticks)
    for caller in ("a", "b", "c"):
        b.take(caller)                                         # each holds 2 of 3
    assert len(b) == 3 and b.purge() == 0                      # none full yet
    clock.now += 0.999
    assert b.purge() == 0                                      # 2.999 < 3
    state["burst"] = 2.0                                       # the new settings are read: every bucket is now at or above its size
    assert b.purge() == 3 and len(b) == 0 and _callers(factory) == []


def test_clear_counts_and_empties_the_store(factory):
    b, _, _ = shared(factory, rate=1, burst=3)
    for caller in ("a", "b", "c"):
        b.take(caller)
    assert len(b) == 3
    b.clear()
    assert len(b) == 0 and _callers(factory) == []


def test_a_failed_purge_is_logged_at_debug_with_its_traceback(factory, caplog):
    b, _, ticks = shared(factory)
    b.take("a")
    ticks.now += PURGE_INTERVAL_SECONDS
    calls = []

    def second_session_fails():
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError("database down")
        return factory()
    b._session_factory = second_session_fails
    with caplog.at_level(logging.DEBUG, logger="smo_shared.ratelimit"):
        assert b.take("a") is None
    [record] = [r for r in caplog.records if r.name == "smo_shared.ratelimit"]
    assert (record.levelno, record.getMessage()) == (logging.DEBUG, "rate limiter purge failed")
    assert record.exc_info is not None and record.exc_info[0] is RuntimeError


def test_the_statements_are_built_for_the_connections_dialect(factory, monkeypatch):
    seen = []
    real = ratelimit._statements
    monkeypatch.setattr(ratelimit, "_statements", lambda dialect: (seen.append(dialect), real(dialect))[1])
    b, _, _ = shared(factory)
    b.take("a")
    b.purge()
    assert seen == ["sqlite", "sqlite"]


def test_the_upsert_is_an_insert_the_purge_a_delete():
    for dialect in ("postgresql", "sqlite"):
        take, purge = ratelimit._statements(dialect)
        assert str(take).startswith("INSERT INTO rate_bucket (caller, tokens, refilled_at, last_allowed) VALUES (:caller, :first_tokens, :now, :first_allowed) ON CONFLICT")
        assert str(purge).startswith("DELETE FROM rate_bucket WHERE ")


def test_a_new_caller_with_a_burst_below_one_starts_with_the_whole_burst_and_is_refused(factory):
    b, _, _ = shared(factory, rate=1, burst=0.5)
    assert b.take("a") == 1                                    # (1 - 0.5) / 1 rounded up
    with factory() as db:
        row = db.execute(select(RateBucket)).scalar_one()
    assert (row.tokens, row.last_allowed) == (0.5, False)      # not 0.5 - 1: a refused request takes nothing


def test_the_fallback_bucket_refills_on_the_replicas_own_monotonic_clock():
    clock, ticks = Clock(), Clock()
    b = SharedTokenBuckets(lambda: 1.0, lambda: 1.0, clock, broken_factory, ticks)
    assert b.take("a") is None and b.take("a") is not None     # the local bucket, empty
    ticks.now += 2 * FAIL_BACKOFF_SECONDS                      # out of the back-off: the store fails again, the local bucket has refilled by the injected clock
    assert b.take("a") is None


def test_a_limiter_starts_with_the_store_available_whatever_the_monotonic_clock_reads(factory):
    clock, ticks = Clock(), Clock(0.5)                         # a monotonic clock is not epoch time: it may be small
    b = SharedTokenBuckets(lambda: 1.0, lambda: 1.0, clock, factory, ticks)
    assert b.take("a") is None
    assert _callers(factory) == ["a"]


def test_clear_ends_a_back_off(factory):
    calls = []
    clock, ticks = Clock(), Clock(0.5)
    b = SharedTokenBuckets(lambda: 1.0, lambda: 1.0, clock, flaky_once(factory, calls), ticks)
    assert b.take("a") is None and calls == [1]                # failed: backing off until tick 5.5
    b.clear()
    assert b.take("a") is None
    assert _callers(factory) == ["a"]                          # the store was used again at once, not after the back-off


def test_the_back_off_lasts_exactly_FAIL_BACKOFF_SECONDS(factory):
    calls = []
    clock, ticks = Clock(), Clock()
    b = SharedTokenBuckets(lambda: 1.0, lambda: 5.0, clock, flaky_once(factory, calls), ticks)
    b.take("a")
    assert len(calls) == 1
    ticks.now = 1000 + FAIL_BACKOFF_SECONDS - 0.001
    b.take("a")
    assert len(calls) == 1                                     # still backing off
    ticks.now = 1000 + FAIL_BACKOFF_SECONDS
    b.take("a")
    assert len(calls) == 2                                     # at the boundary the store is tried again


def test_the_outage_is_logged_once_per_interval_with_the_error_named(caplog):
    clock, ticks = Clock(), Clock()
    b = SharedTokenBuckets(lambda: 1.0, lambda: 5.0, clock, broken_factory, ticks)
    with caplog.at_level(logging.WARNING, logger="smo_shared.ratelimit"):
        for at in (0, 10, 20, 30):                   # each is past the back-off, so each is a failed statement
            ticks.now = 1000 + at
            b.take("a")
    lines = [r.getMessage() for r in caplog.records if r.name == "smo_shared.ratelimit"]
    expected = "rate limiter store unavailable, failing open to the per-replica bucket for 5 s: ConnectionError: database down"
    assert lines == [expected, expected]                       # at 0 and at exactly 30 s later
