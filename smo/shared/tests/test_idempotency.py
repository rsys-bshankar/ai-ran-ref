"""smo_shared.idempotency — `Idempotency-Key` on command routes (PR-ST-3).

Runs on a file SQLite database and, when `SMO_TEST_POSTGRES_URL` is set (CI's
`migration-postgres` job), on real Postgres too, including a concurrent race.

Run with: cd smo/shared && PYTHONPATH=. python -m pytest tests/test_idempotency.py -q
"""

import datetime
import os
import threading
import time

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import String, create_engine, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from smo_shared.db import Base
from smo_shared.idempotency import (COMPLETED, IN_PROGRESS, REPLAY_HEADER, IdempotencyKey, idempotent,
                                    request_hash)
from smo_shared.invoker import INVOKER_ID_HEADER

KEY = "Idempotency-Key"


class _Rows(DeclarativeBase):
    pass


class Thing(_Rows):
    __tablename__ = "st3_thing"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String)


class NewThing(BaseModel):
    name: str


@pytest.fixture(params=["sqlite", "postgres"])
def engine(request, tmp_path):
    """A database with the idempotency table and the test's own `st3_thing` table, on file SQLite and, when SMO_TEST_POSTGRES_URL is set, on Postgres
    too; dropped afterwards.
    """
    if request.param == "postgres":
        if not os.environ.get("SMO_TEST_POSTGRES_URL"):
            pytest.skip("SMO_TEST_POSTGRES_URL not set")
        engine = create_engine(os.environ["SMO_TEST_POSTGRES_URL"], future=True)
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'idem.db'}", future=True)
    for metadata, tables in ((Base.metadata, [IdempotencyKey.__table__]), (_Rows.metadata, None)):
        metadata.drop_all(engine, tables=tables)
        metadata.create_all(engine, tables=tables)
    yield engine
    for metadata, tables in ((Base.metadata, [IdempotencyKey.__table__]), (_Rows.metadata, None)):
        metadata.drop_all(engine, tables=tables)
    engine.dispose()


class Harness:
    """Builds a small service around an idempotent create route (`/things`), a second idempotent route (`/other`), a switch that makes the create fail,
    and a counter of real executions.
    """

    def __init__(self, engine, slow: float = 0.0):
        self.engine, self.factory = engine, sessionmaker(bind=engine)
        self.executions = 0
        self.fail = False
        app = FastAPI()

        def get_db():
            session = self.factory()
            try:
                yield session
            finally:
                session.close()

        @app.post("/things", status_code=201)
        @idempotent("demo", status_code=201)
        def create(body: NewThing, request: Request, db: Session = Depends(get_db)):
            # Test route behind @idempotent; counts executions, can be made to fail, and inserts one row. Not part of any published API.
            self.executions += 1
            if self.fail:
                raise HTTPException(status_code=409, detail={"title": "SOME_CONFLICT", "status": 409})
            if slow:
                time.sleep(slow)
            thing = Thing(name=body.name)
            db.add(thing)
            db.commit()
            return {"thingId": thing.id, "name": body.name}

        @app.post("/other", status_code=201)
        @idempotent("demo", status_code=201)
        def other(body: NewThing, request: Request, db: Session = Depends(get_db)):
            # Second idempotent test route, to prove a key cannot be reused across paths. Not part of any published API.
            return {"other": body.name}

        self.client = TestClient(app)

    def post(self, key=None, name="a", path="/things", invoker=None):
        """Posts a body with an optional Idempotency-Key and invoker id to `path`."""
        headers = {}
        if key is not None:
            headers[KEY] = key
        if invoker:
            headers[INVOKER_ID_HEADER] = invoker
        return self.client.post(path, json={"name": name}, headers=headers)

    def things(self) -> int:
        """The number of rows the create route has really inserted."""
        with self.factory() as s:
            return len(s.scalars(select(Thing)).all())

    def record(self, key="k", scope="anonymous"):
        """The stored idempotency record for (key, scope), or None."""
        with self.factory() as s:
            return s.get(IdempotencyKey, ("demo", scope, key))


def test_without_the_header_the_route_runs_every_time(engine):
    """Without an Idempotency-Key the route behaves as it always did: every request runs."""
    h = Harness(engine)
    assert h.post().status_code == 201 and h.post().status_code == 201
    assert h.executions == 2 and h.things() == 2


def test_a_repeat_with_the_same_key_returns_the_first_answer_and_does_not_run_again(engine):
    """A repeat with the same key and request gets the first answer with Idempotent-Replayed: true and the command is not run again."""
    h = Harness(engine)
    first = h.post("k")
    again = h.post("k")
    assert first.status_code == again.status_code == 201
    assert again.json() == first.json()
    assert REPLAY_HEADER not in first.headers and again.headers[REPLAY_HEADER] == "true"
    assert h.executions == 1 and h.things() == 1
    row = h.record()
    assert (row.state, row.response_status) == (COMPLETED, 201)


def test_the_same_key_for_a_different_payload_is_refused(engine):
    """Reusing a key with a different body is 422 IDEMPOTENCY_KEY_REUSED and nothing runs."""
    h = Harness(engine)
    h.post("k", name="a")
    resp = h.post("k", name="b")
    assert resp.status_code == 422 and resp.json()["detail"]["title"] == "IDEMPOTENCY_KEY_REUSED"
    assert h.executions == 1


def test_the_same_key_for_a_different_path_is_refused(engine):
    """Reusing a key on a different route is 422 IDEMPOTENCY_KEY_REUSED."""
    h = Harness(engine)
    h.post("k", path="/things")
    resp = h.post("k", path="/other")
    assert resp.status_code == 422 and resp.json()["detail"]["title"] == "IDEMPOTENCY_KEY_REUSED"


def test_keys_are_scoped_to_the_caller(engine):
    """The same key from two invokers is two independent keys, so one rApp can neither replay nor block another's."""
    h = Harness(engine)
    a1, b1 = h.post("k", invoker="rapp-a"), h.post("k", invoker="rapp-b")
    a2 = h.post("k", invoker="rapp-a")
    assert h.executions == 2 and a2.json() == a1.json() and b1.json() != a1.json()
    assert REPLAY_HEADER in a2.headers and REPLAY_HEADER not in b1.headers


def test_a_failed_attempt_is_not_stored_so_the_repeat_runs_again(engine):
    """A command that fails leaves no record, so the client's repeat runs it again; only the later success is stored and replayed."""
    h = Harness(engine)
    h.fail = True
    failed = h.post("k")
    assert failed.status_code == 409 and h.record() is None
    h.fail = False
    ok = h.post("k")
    assert ok.status_code == 201 and h.executions == 2 and h.things() == 1
    assert h.post("k").headers[REPLAY_HEADER] == "true"


def test_a_repeat_while_the_first_is_still_running_is_409_and_does_not_run(engine):
    """A repeat while the first attempt is still in progress is 409 IDEMPOTENCY_KEY_IN_PROGRESS and does not run."""
    h = Harness(engine)
    with h.factory() as s:
        s.add(IdempotencyKey(module="demo", scope="anonymous", key="k", state=IN_PROGRESS,
                             request_hash=request_hash("POST", "/things", {"body": {"name": "a"}})))
        s.commit()
    resp = h.post("k")
    assert resp.status_code == 409 and resp.json()["detail"]["title"] == "IDEMPOTENCY_KEY_IN_PROGRESS"
    assert h.executions == 0


def test_a_reservation_abandoned_by_a_crashed_replica_is_taken_over(engine, monkeypatch):
    """A reservation older than the in-progress limit is treated as abandoned by a crashed replica: the repeat runs and completes it."""
    monkeypatch.setenv("IDEMPOTENCY_IN_PROGRESS_SECONDS", "60")
    h = Harness(engine)
    with h.factory() as s:
        s.add(IdempotencyKey(module="demo", scope="anonymous", key="k", state=IN_PROGRESS,
                             request_hash=request_hash("POST", "/things", {"body": {"name": "a"}}),
                             created_at=datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=120)))
        s.commit()
    resp = h.post("k")
    assert resp.status_code == 201 and h.executions == 1
    assert h.record().state == COMPLETED


def test_expired_records_are_purged_when_a_new_key_is_reserved(engine, monkeypatch):
    """Records older than the TTL are removed when a new key is reserved, and an expired key can be used again as a new one."""
    monkeypatch.setenv("IDEMPOTENCY_KEY_TTL_SECONDS", "60")
    h = Harness(engine)
    h.post("old")
    with h.factory() as s:
        s.execute(update(IdempotencyKey).where(IdempotencyKey.key == "old")
                  .values(created_at=datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=120)))
        s.commit()
    h.post("new")
    assert h.record("old") is None and h.record("new") is not None
    assert h.post("old").status_code == 201 and h.executions == 3  # an expired key is just a new key


# Table: an empty key, a key one character over the 255 limit, and one with a control character; each is 422 IDEMPOTENCY_KEY_INVALID and the route
# does not run.
@pytest.mark.parametrize("key", ["", "x" * 256, "bad\nkey"])
def test_an_invalid_key_is_422(engine, key):
    h = Harness(engine)
    resp = h.client.post("/things", json={"name": "a"}, headers={KEY: key})
    assert resp.status_code == 422 and resp.json()["detail"]["title"] == "IDEMPOTENCY_KEY_INVALID"
    assert h.executions == 0


def test_the_decorator_needs_request_and_db_parameters():
    """Decorating a route without `request` and `db` parameters fails at definition time, not at the first request."""
    with pytest.raises(TypeError, match="request"):
        @idempotent("demo")
        def route(body: NewThing):
            return {}


def test_concurrent_requests_with_one_key_run_the_command_once(engine):
    """Six simultaneous requests with one key run the command exactly once; the rest get the identical stored answer or 409 in progress."""
    h = Harness(engine, slow=0.3)
    workers = 6
    barrier = threading.Barrier(workers)
    results: list[tuple[int, dict]] = []
    lock = threading.Lock()

    def attempt():
        barrier.wait()
        resp = h.post("race")
        with lock:
            results.append((resp.status_code, resp.json()))

    threads = [threading.Thread(target=attempt) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert len(results) == workers
    assert h.executions == 1 and h.things() == 1
    winners = [body for status, body in results if status == 201]
    others = [body for status, body in results if status == 409]
    assert winners and len(winners) + len(others) == workers
    assert all(w == winners[0] for w in winners)           # a replay is the first answer, byte for byte
    assert all(o["detail"]["title"] == "IDEMPOTENCY_KEY_IN_PROGRESS" for o in others)
