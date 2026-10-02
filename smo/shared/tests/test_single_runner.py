"""smo_shared.single_runner — one firing per interval across replicas (PR-ST-8). Runs on file SQLite and, with
`SMO_TEST_POSTGRES_URL` (CI's `migration-postgres` job), on real Postgres, where the advisory lock is tested too."""

import datetime
import os
import threading

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from smo_shared.db import Base
from smo_shared.single_runner import PeriodicRun, advisory_lock, run_once_per_interval

T0 = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=datetime.UTC)


def at(seconds: float) -> datetime.datetime:
    return T0 + datetime.timedelta(seconds=seconds)


@pytest.fixture(params=["sqlite", "postgres"])
def engine(request, tmp_path):
    if request.param == "postgres":
        if not os.environ.get("SMO_TEST_POSTGRES_URL"):
            pytest.skip("SMO_TEST_POSTGRES_URL not set")
        engine = create_engine(os.environ["SMO_TEST_POSTGRES_URL"], future=True)
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'runner.db'}", future=True)
    Base.metadata.drop_all(engine, tables=[PeriodicRun.__table__])
    Base.metadata.create_all(engine, tables=[PeriodicRun.__table__])
    yield engine
    Base.metadata.drop_all(engine, tables=[PeriodicRun.__table__])
    engine.dispose()


@pytest.fixture
def run(engine):
    factory = sessionmaker(bind=engine)

    def go(name, interval, fn, now):
        return run_once_per_interval(name, interval, fn, session_factory=factory, engine=engine, now=now)
    return go


class Counter:
    def __init__(self):
        self.calls = 0
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            self.calls += 1


def test_the_first_call_runs_and_a_repeat_inside_the_interval_does_not(run):
    task = Counter()
    assert run("collect", 60, task, at(0)) is True
    assert run("collect", 60, task, at(30)) is False
    assert run("collect", 60, task, at(59.9)) is False
    assert task.calls == 1


def test_it_runs_again_once_the_interval_has_passed(run):
    task = Counter()
    run("collect", 60, task, at(0))
    assert run("collect", 60, task, at(60)) is True
    assert run("collect", 60, task, at(119)) is False
    assert run("collect", 60, task, at(120)) is True
    assert task.calls == 3


def test_tasks_are_independent_and_keep_their_own_interval(run):
    a, b = Counter(), Counter()
    run("a", 60, a, at(0))
    run("b", 10, b, at(0))
    assert run("a", 60, a, at(20)) is False and run("b", 10, b, at(20)) is True
    assert (a.calls, b.calls) == (1, 2)


def test_a_failed_run_gives_the_interval_back_so_the_next_tick_retries(run, engine):
    task = Counter()

    def boom():
        raise RuntimeError("collector down")

    with pytest.raises(RuntimeError, match="collector down"):
        run("collect", 60, boom, at(0))
    assert run("collect", 60, task, at(1)) is True          # not locked out for the rest of the interval
    assert task.calls == 1


def test_several_replicas_calling_at_once_run_the_task_once(run):
    task = Counter()
    replicas = 6
    barrier = threading.Barrier(replicas, timeout=30)
    results, lock = [], threading.Lock()

    def replica():
        barrier.wait()
        won = run("collect", 60, task, at(0))
        with lock:
            results.append(won)

    threads = [threading.Thread(target=replica) for _ in range(replicas)]
    [t.start() for t in threads]
    [t.join(timeout=60) for t in threads]
    assert sorted(results) == [False] * (replicas - 1) + [True]
    assert task.calls == 1


def test_the_state_is_one_row_per_task(run, engine):
    run("a", 60, Counter(), at(0))
    run("a", 60, Counter(), at(100))
    run("b", 60, Counter(), at(0))
    with sessionmaker(bind=engine)() as s:
        rows = {r.name: r.last_run_at for r in s.scalars(select(PeriodicRun))}
    assert set(rows) == {"a", "b"}


# ---------------------------------------------------------------- advisory lock (Postgres only)

@pytest.fixture
def postgres():
    if not os.environ.get("SMO_TEST_POSTGRES_URL"):
        pytest.skip("SMO_TEST_POSTGRES_URL not set")
    engine = create_engine(os.environ["SMO_TEST_POSTGRES_URL"], future=True)
    yield engine
    engine.dispose()


def test_two_sessions_cannot_hold_the_same_lock_and_it_is_free_afterwards(postgres):
    with advisory_lock("t8-lock", postgres) as first:
        with advisory_lock("t8-lock", postgres) as second:
            assert (first, second) == (True, False)
        with advisory_lock("t8-other", postgres) as other:
            assert other is True                             # a different name is a different lock
    with advisory_lock("t8-lock", postgres) as again:
        assert again is True


def test_a_holder_that_dies_frees_the_lock_without_releasing_it(postgres):
    connection = postgres.connect().execution_options(isolation_level="AUTOCOMMIT")
    from sqlalchemy import text

    from smo_shared.single_runner import _lock_key
    assert connection.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _lock_key("t8-crash")}).scalar()
    with advisory_lock("t8-crash", postgres) as blocked:
        assert blocked is False
    connection.invalidate()                                   # the process died: the server drops the session
    connection.close()
    with advisory_lock("t8-crash", postgres) as freed:
        assert freed is True


def test_a_run_longer_than_the_interval_is_not_started_again_elsewhere(run, engine):
    if engine.dialect.name != "postgresql":
        pytest.skip("the advisory lock is Postgres-only")
    started, release = threading.Event(), threading.Event()
    second_result = []

    def slow():
        started.set()
        release.wait(timeout=30)

    first = threading.Thread(target=lambda: run("t8-slow", 10, slow, at(0)))
    first.start()
    assert started.wait(timeout=30)
    second_result.append(run("t8-slow", 10, Counter(), at(20)))   # the interval has passed, the first run has not ended
    release.set()
    first.join(timeout=30)
    assert second_result == [False]
    assert run("t8-slow", 10, Counter(), at(30)) is True          # its claim was given back, the lock is free
