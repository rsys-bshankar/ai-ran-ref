"""smo_shared.worker — the periodic-work loop (PR-MSG-4). The claim itself is tested in test_single_runner.py; here: what a tick does with a
list of tasks, and the loop's own handling of failures, the heartbeat and the stop signal."""

import datetime
import threading

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from smo_shared import worker
from smo_shared.db import Base
from smo_shared.single_runner import PeriodicRun
from smo_shared.worker import Task, load_tasks, tick

T0 = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=datetime.UTC)


def at(seconds: float) -> datetime.datetime:
    return T0 + datetime.timedelta(seconds=seconds)


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'worker.db'}", future=True)
    Base.metadata.create_all(engine, tables=[PeriodicRun.__table__])
    yield engine, sessionmaker(bind=engine)
    engine.dispose()


def run_tick(db, tasks, now, **kw):
    engine, factory = db
    return tick(tasks, module="m", session_factory=factory, engine=engine, now=now, **kw)


def test_a_task_runs_once_per_interval_however_often_it_is_offered(db):
    calls = []
    tasks = [Task("t", 60, lambda: calls.append(1))]
    assert run_tick(db, tasks, at(0)) == {"t": "ran"}
    assert run_tick(db, tasks, at(5)) == {"t": "skipped"}
    assert run_tick(db, tasks, at(59)) == {"t": "skipped"}
    assert run_tick(db, tasks, at(61)) == {"t": "ran"}
    assert len(calls) == 2


def test_two_workers_offering_the_same_task_run_it_once(db):
    calls = []
    tasks = [Task("t", 60, lambda: calls.append(1))]
    first = run_tick(db, tasks, at(0))
    second = run_tick(db, tasks, at(0))                        # a second worker, same instant
    assert (first, second) == ({"t": "ran"}, {"t": "skipped"}) and len(calls) == 1


def test_a_failing_task_does_not_stop_the_others_and_gives_its_claim_back(db):
    seen = []

    def boom():
        raise RuntimeError("down")

    tasks = [Task("bad", 60, boom), Task("good", 60, lambda: seen.append(1))]
    assert run_tick(db, tasks, at(0)) == {"bad": "failed", "good": "ran"}
    assert run_tick(db, tasks, at(1)) == {"bad": "failed", "good": "skipped"}     # the failed one is offered again at once: the claim was returned


def test_a_task_in_skip_is_not_offered(db):
    calls = []
    tasks = [Task("t", 60, lambda: calls.append(1))]
    assert run_tick(db, tasks, at(0), skip=["t"]) == {"t": "skipped"} and calls == []


def test_tasks_of_two_modules_with_the_same_name_do_not_share_a_claim(db):
    engine, factory = db
    calls = []
    tasks = [Task("purge", 60, lambda: calls.append(1))]
    assert tick(tasks, module="a", session_factory=factory, engine=engine, now=at(0)) == {"purge": "ran"}
    assert tick(tasks, module="b", session_factory=factory, engine=engine, now=at(0)) == {"purge": "ran"}
    assert len(calls) == 2


def test_load_tasks_refuses_duplicate_names(monkeypatch):
    class Fake:
        TASKS = [Task("x", 1, lambda: None), Task("x", 2, lambda: None)]

    monkeypatch.setattr(worker.importlib, "import_module", lambda path: Fake)
    with pytest.raises(ValueError, match="duplicate task names"):
        load_tasks()


def test_the_loop_touches_the_heartbeat_backs_off_a_failure_and_stops_on_the_event(db, monkeypatch, tmp_path):
    engine, factory = db
    offered = []
    stop = threading.Event()

    def fake_tick(tasks, *, module, skip, **kw):
        offered.append(sorted(skip))
        if len(offered) == 3:
            stop.set()
        return {"bad": "failed"}

    monkeypatch.setattr(worker, "tick", fake_tick)
    monkeypatch.setenv("SMO_WORKER_HEARTBEAT_FILE", str(tmp_path / "beat"))
    monkeypatch.setenv("SMO_WORKER_TICK_SECONDS", "0.1")
    monkeypatch.setenv("SMO_WORKER_FAILURE_BACKOFF_SECONDS", "3600")
    monkeypatch.setattr(worker.signal, "signal", lambda *a: None)
    assert worker.main([Task("bad", 1, lambda: None)], module="m", stop=stop) == 0
    assert (tmp_path / "beat").exists()
    assert offered == [[], ["bad"], ["bad"]]                   # offered once, then skipped for the back-off
