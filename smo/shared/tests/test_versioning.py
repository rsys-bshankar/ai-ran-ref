"""smo_shared.versioning — optimistic concurrency on lifecycle rows (PR-ST-2).

Runs against a file SQLite database (real, separate connections) and, when
`SMO_TEST_POSTGRES_URL` is set, against real Postgres as well. CI sets it in the
`migration-postgres` job, so the two-session race is proven on the database the
platform actually uses.
"""

import os
import threading
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import String, Uuid, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.orm.exc import StaleDataError

from smo_shared.statemachine import IllegalTransition, StateMachine
from smo_shared.versioning import CONCURRENT_MODIFICATION_TITLE, Versioned, install_concurrency_handler


class _Base(DeclarativeBase):
    pass


class VersionedJob(Versioned, _Base):
    __tablename__ = "st2_versioned_job"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    state: Mapped[str] = mapped_column(String, nullable=False, default="NEW")
    note: Mapped[str | None] = mapped_column(String)


FSM = StateMachine().add("NEW", "START", "RUNNING").add("RUNNING", "FINISH", "DONE")


def _urls(tmp_path):
    urls = {"sqlite": f"sqlite:///{tmp_path / 'versioned.db'}"}
    if os.environ.get("SMO_TEST_POSTGRES_URL"):
        urls["postgres"] = os.environ["SMO_TEST_POSTGRES_URL"]
    return urls


@pytest.fixture(params=["sqlite", "postgres"])
def engine(request, tmp_path):
    urls = _urls(tmp_path)
    if request.param not in urls:
        pytest.skip("SMO_TEST_POSTGRES_URL not set")
    engine = create_engine(urls[request.param], future=True)
    _Base.metadata.drop_all(engine)
    _Base.metadata.create_all(engine)
    yield engine
    _Base.metadata.drop_all(engine)
    engine.dispose()


def _new_job(engine) -> uuid.UUID:
    with Session(engine) as s:
        job = VersionedJob()
        s.add(job)
        s.commit()
        return job.id


def _fire(job: VersionedJob, event: str) -> None:
    job.state = FSM.fire(job.state, event)


def test_a_new_row_starts_at_version_1_and_every_update_bumps_it(engine):
    job_id = _new_job(engine)
    with Session(engine) as s:
        job = s.get(VersionedJob, job_id)
        assert job.row_version == 1
        _fire(job, "START")
        s.commit()
        assert job.row_version == 2
        job.note = "any other column counts too"
        s.commit()
        assert job.row_version == 3


def test_two_sessions_firing_the_same_transition_have_exactly_one_winner(engine):
    job_id = _new_job(engine)
    first, second = Session(engine), Session(engine)
    try:
        a, b = first.get(VersionedJob, job_id), second.get(VersionedJob, job_id)
        _fire(a, "START")
        _fire(b, "START")          # both saw NEW, both think START is legal
        first.commit()
        with pytest.raises(StaleDataError):
            second.commit()
    finally:
        first.close()
        second.close()
    with Session(engine) as s:
        job = s.get(VersionedJob, job_id)
        assert (job.state, job.row_version) == ("RUNNING", 2)


def test_repeating_after_a_conflict_is_refused_as_an_illegal_transition(engine):
    """The retry reloads the row, sees the other writer's state, and the FSM refuses START in RUNNING."""
    job_id = _new_job(engine)
    loser, winner = Session(engine), Session(engine)
    try:
        stale = loser.get(VersionedJob, job_id)
        won = winner.get(VersionedJob, job_id)
        _fire(won, "START")
        winner.commit()
        _fire(stale, "START")
        with pytest.raises(StaleDataError):
            loser.commit()
        loser.rollback()
        with pytest.raises(IllegalTransition):
            _fire(loser.get(VersionedJob, job_id), "START")
    finally:
        loser.close()
        winner.close()


def test_a_write_to_a_different_column_also_conflicts(engine):
    job_id = _new_job(engine)
    first, second = Session(engine), Session(engine)
    try:
        a, b = first.get(VersionedJob, job_id), second.get(VersionedJob, job_id)
        a.note = "x"
        b.state = "RUNNING"
        first.commit()
        with pytest.raises(StaleDataError):
            second.commit()
    finally:
        first.close()
        second.close()


def test_many_threads_racing_one_transition_produce_one_winner(engine):
    job_id = _new_job(engine)
    workers = 8
    barrier = threading.Barrier(workers)
    results: list[str] = []
    lock = threading.Lock()

    def attempt():
        with Session(engine) as s:
            job = s.get(VersionedJob, job_id)
            barrier.wait()            # every thread has loaded NEW before any writes
            try:
                _fire(job, "START")
                s.commit()
                outcome = "won"
            except StaleDataError:
                s.rollback()
                outcome = "stale"
            with lock:
                results.append(outcome)

    threads = [threading.Thread(target=attempt) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert sorted(results) == ["stale"] * (workers - 1) + ["won"]
    with Session(engine) as s:
        job = s.get(VersionedJob, job_id)
        assert (job.state, job.row_version) == ("RUNNING", 2)


def test_a_stale_write_is_answered_with_409_problem_details():
    app = FastAPI()
    install_concurrency_handler(app)

    @app.post("/boom")
    def boom():
        raise StaleDataError("UPDATE statement on table 'x' expected to update 1 row(s); 0 were matched.")

    resp = TestClient(app).post("/boom")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["title"] == CONCURRENT_MODIFICATION_TITLE == "CONCURRENT_MODIFICATION"
    assert detail["status"] == 409
    assert "repeat" in detail["detail"]
