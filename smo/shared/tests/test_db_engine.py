"""smo_shared.db.engine_options / build_engine and smo_shared.timeouts (PR-ST-6).

The option tests are pure. The two session-limit tests need a real Postgres (`SMO_TEST_POSTGRES_URL`, CI's
`migration-postgres` job): SQLite has no server-side statement timeout to test.
"""

import os
import time

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, OperationalError

from smo_shared import timeouts
from smo_shared.db import build_engine, engine_options

PG = "postgresql+psycopg://smo:smo@db:5432/smo"


def test_postgres_defaults_are_a_bounded_pool_with_recycling_and_session_limits():
    options = engine_options(PG, environ={})
    assert options["pool_size"] == 5 and options["max_overflow"] == 10
    assert options["pool_timeout"] == 30 and options["pool_recycle"] == 1800
    assert options["pool_pre_ping"] is True
    assert options["connect_args"]["options"] == "-c statement_timeout=30000 -c idle_in_transaction_session_timeout=300000"


def test_every_setting_can_be_changed_from_the_environment():
    options = engine_options(PG, environ={
        "SMO_DB_POOL_SIZE": "20", "SMO_DB_MAX_OVERFLOW": "0", "SMO_DB_POOL_TIMEOUT_SECONDS": "5",
        "SMO_DB_POOL_RECYCLE_SECONDS": "600", "SMO_DB_STATEMENT_TIMEOUT_MS": "1500",
        "SMO_DB_IDLE_IN_TRANSACTION_TIMEOUT_MS": "9000"})
    assert (options["pool_size"], options["max_overflow"], options["pool_timeout"], options["pool_recycle"]) == (20, 0, 5, 600)
    assert options["connect_args"]["options"] == "-c statement_timeout=1500 -c idle_in_transaction_session_timeout=9000"


def test_zero_turns_a_limit_off():
    options = engine_options(PG, environ={"SMO_DB_STATEMENT_TIMEOUT_MS": "0", "SMO_DB_POOL_RECYCLE_SECONDS": "0"})
    assert options["pool_recycle"] == -1
    assert options["connect_args"]["options"] == "-c idle_in_transaction_session_timeout=300000"
    off = engine_options(PG, environ={"SMO_DB_STATEMENT_TIMEOUT_MS": "0", "SMO_DB_IDLE_IN_TRANSACTION_TIMEOUT_MS": "0"})
    assert "connect_args" not in off


def test_sqlite_gets_no_pool_or_session_settings():
    assert engine_options("sqlite://", environ={"SMO_DB_POOL_SIZE": "99"}) == {"pool_pre_ping": True, "future": True}
    engine = build_engine("sqlite://")                     # and an engine really builds from it
    with engine.connect() as connection:
        assert connection.execute(text("select 1")).scalar() == 1


def test_the_pool_settings_reach_the_engine():
    engine = build_engine(PG, environ={"SMO_DB_POOL_SIZE": "7", "SMO_DB_MAX_OVERFLOW": "3"})   # lazy: nothing connects
    assert engine.pool.size() == 7 and engine.pool._max_overflow == 3


needs_postgres = pytest.mark.skipif(not os.environ.get("SMO_TEST_POSTGRES_URL"), reason="SMO_TEST_POSTGRES_URL not set")


@needs_postgres
def test_a_statement_that_runs_too_long_is_cancelled_by_the_server():
    engine = build_engine(os.environ["SMO_TEST_POSTGRES_URL"], environ={"SMO_DB_STATEMENT_TIMEOUT_MS": "300"})
    started = time.monotonic()
    with pytest.raises(OperationalError, match="statement timeout"):
        with engine.connect() as connection:
            connection.execute(text("select pg_sleep(10)"))
    assert time.monotonic() - started < 5
    with engine.connect() as connection:                   # the pool is still usable afterwards
        assert connection.execute(text("select 1")).scalar() == 1
    engine.dispose()


@needs_postgres
def test_a_session_left_idle_inside_a_transaction_is_ended_by_the_server():
    engine = build_engine(os.environ["SMO_TEST_POSTGRES_URL"], environ={"SMO_DB_IDLE_IN_TRANSACTION_TIMEOUT_MS": "300"})
    with pytest.raises(DBAPIError, match="idle-in-transaction"):
        with engine.connect() as connection:
            connection.execute(text("select 1"))           # opens a transaction ...
            time.sleep(1.5)                                # ... and abandons it
            connection.execute(text("select 1"))
    engine.dispose()


@needs_postgres
def test_without_the_limits_the_same_statement_runs_to_completion():
    """The control: the timeout comes from the setting, not from anything else in the setup."""
    engine = build_engine(os.environ["SMO_TEST_POSTGRES_URL"], environ={"SMO_DB_STATEMENT_TIMEOUT_MS": "0"})
    with engine.connect() as connection:
        connection.execute(text("select pg_sleep(0.6)"))
    engine.dispose()


# ---------------------------------------------------------------- a connection pooler (PR-DB-5)

PG = "postgresql+psycopg://u@h/db"


def test_direct_to_postgres_sends_the_session_limits_and_keeps_prepared_statements_on():
    connect_args = engine_options(PG, {})["connect_args"]
    assert "statement_timeout" in connect_args["options"] and "prepare_threshold" not in connect_args


def test_behind_a_transaction_pooler_no_startup_options_and_no_prepared_statements():
    # a pooler refuses `options`, and hands the next transaction another server connection, which has not prepared the statement
    options = engine_options(PG, {"SMO_DB_POOLER": "transaction"})
    assert options["connect_args"] == {"prepare_threshold": None}


def test_a_pooler_that_tracks_prepared_statements_can_have_them_back():
    assert engine_options(PG, {"SMO_DB_POOLER": "transaction", "SMO_DB_PREPARE_THRESHOLD": "5"})["connect_args"] == {"prepare_threshold": 5}
    assert engine_options(PG, {"SMO_DB_PREPARE_THRESHOLD": "off"})["connect_args"]["prepare_threshold"] is None


def test_a_session_pooler_keeps_prepared_statements_but_sends_no_options():
    assert "connect_args" not in engine_options(PG, {"SMO_DB_POOLER": "session"})


def test_an_unknown_pooler_mode_is_refused_not_ignored():
    with pytest.raises(ValueError, match="SMO_DB_POOLER"):
        engine_options(PG, {"SMO_DB_POOLER": "statement"})


# ---------------------------------------------------------------- timeouts.py

def test_http_timeout_defaults_nest_so_an_outer_caller_outlasts_the_call_it_waits_on(monkeypatch):
    for name in ("SMO_HTTP_TIMEOUT_SECONDS", "R1_UPSTREAM_TIMEOUT_SECONDS", "R1_INTROSPECT_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    assert (timeouts.call_timeout(), timeouts.upstream_timeout(), timeouts.introspect_timeout()) == (30.0, 60.0, 5.0)
    assert timeouts.upstream_timeout() > timeouts.call_timeout() > timeouts.introspect_timeout()


def test_http_timeouts_are_read_when_asked(monkeypatch):
    monkeypatch.setenv("SMO_HTTP_TIMEOUT_SECONDS", "12.5")
    assert timeouts.call_timeout() == 12.5
