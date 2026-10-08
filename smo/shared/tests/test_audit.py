"""PR-SEC-11: the audit chain detects an edited, deleted, inserted or truncated row, and numbering holds under concurrent writers."""

import datetime
import io
import json
import threading

import pytest
from sqlalchemy import delete, text, update
from sqlalchemy.orm import Session

from smo_shared import audit
from smo_shared.db import Base
from smo_shared.testing import make_test_engine

T0 = datetime.datetime(2026, 10, 4, 12, 0, tzinfo=datetime.timezone.utc)


@pytest.fixture
def db():
    engine = make_test_engine()
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _fill(db, n=5):
    for i in range(n):
        audit.record(db, actor=f"inv-{i % 2}", action="post", target=f"/dme/data-jobs/{i}", result="201", role="rapp", correlation_id=f"c{i}",
                     now=T0 + datetime.timedelta(seconds=i))
        db.commit()


def test_an_empty_chain_is_intact(db):
    assert audit.verify(db) is None


def test_rows_are_numbered_and_linked(db):
    _fill(db, 3)
    rows = db.query(audit.AuditEntry).order_by(audit.AuditEntry.seq).all()
    assert [r.seq for r in rows] == [1, 2, 3]
    assert rows[0].prev_hash == audit.GENESIS and rows[1].prev_hash == rows[0].hash and rows[2].prev_hash == rows[1].hash
    assert rows[0].action == "POST"
    assert audit.verify(db) is None


def test_an_edited_row_is_found_at_that_row(db):
    _fill(db)
    db.execute(update(audit.AuditEntry).where(audit.AuditEntry.seq == 3).values(actor="someone-else"))
    db.commit()
    assert audit.verify(db) == {"seq": 3, "reason": "the row was changed: its hash does not match its content"}


def test_a_deleted_row_is_found(db):
    _fill(db)
    db.execute(delete(audit.AuditEntry).where(audit.AuditEntry.seq == 2))
    db.commit()
    problem = audit.verify(db)
    assert problem["seq"] == 2 and "missing" in problem["reason"]


def test_a_removed_tail_is_found_through_the_head(db):
    _fill(db)
    db.execute(delete(audit.AuditEntry).where(audit.AuditEntry.seq.in_([4, 5])))
    db.commit()
    problem = audit.verify(db)
    assert problem["seq"] == 3 and "head" in problem["reason"]


def test_a_row_added_while_the_chain_is_read_is_not_a_break(db):
    """A writer commits after the rows were read and before the head is read again (found by the upgrade lane's load, V-10)."""
    _fill(db, 3)
    real, state = db.execute, {"rows": False}

    def execute(statement, *a, **kw):
        text_ = str(statement)
        if "audit_head" not in text_:
            state["rows"] = True
        elif state["rows"]:                          # the read of the head after the rows: a writer got in before it
            state["rows"] = False
            audit.record(db, actor="late", action="post", target="/x", result="201", role="rapp", correlation_id="late", now=T0)
            db.commit()
        return real(statement, *a, **kw)

    db.execute = execute
    assert audit.verify(db) is None
    del db.execute


def test_rows_past_the_head_are_found(db):
    _fill(db)
    db.execute(update(audit.AuditHead).where(audit.AuditHead.head_id == 1).values(last_seq=3, last_hash=db.get(audit.AuditEntry, 3).hash))
    db.commit()
    assert audit.verify(db)["seq"] == 5


def test_a_row_with_a_forged_link_is_found(db):
    _fill(db)
    row = db.get(audit.AuditEntry, 3)
    row.prev_hash = "f" * 64
    row.hash = audit.compute_hash(row)               # the forger recomputes the row's own hash, but the link no longer matches the row before
    db.commit()
    problem = audit.verify(db)
    assert problem["seq"] == 3 and "prev_hash" in problem["reason"]


def test_a_failed_transaction_leaves_no_gap(db):
    _fill(db, 2)
    audit.record(db, actor="a", action="DELETE", target="/x", result="204")
    db.rollback()
    audit.record(db, actor="a", action="DELETE", target="/y", result="204")
    db.commit()
    assert [r.seq for r in db.query(audit.AuditEntry).order_by(audit.AuditEntry.seq)] == [1, 2, 3]
    assert audit.verify(db) is None


def test_the_timestamp_survives_a_round_trip_without_its_zone(db):
    audit.record(db, actor="a", action="PUT", target="/x", result="200", now=datetime.datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=datetime.timezone.utc))
    db.commit()
    db.expire_all()
    assert audit.verify(db) is None


def test_the_export_ends_with_the_head_and_syslog_wraps_each_row(db):
    _fill(db, 3)
    out = io.StringIO()
    audit.export(db, out=out)
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [line["seq"] for line in lines[:-1]] == [1, 2, 3]
    assert lines[-1] == {"head": {"seq": 3, "hash": lines[2]["hash"]}}
    out = io.StringIO()
    audit.export(db, since=2, fmt="syslog", out=out)
    first = out.getvalue().splitlines()[0]
    assert first.startswith("<110>1 2026-10-04T12:00:02.000000Z smo smo-audit - 3 - {") and len(out.getvalue().splitlines()) == 2


def test_write_audit_never_raises_and_counts(monkeypatch):
    from prometheus_client import REGISTRY
    monkeypatch.setattr("smo_shared.db.SessionLocal", lambda: (_ for _ in ()).throw(RuntimeError("database down")))
    before = REGISTRY.get_sample_value("smo_audit_writes_total", {"outcome": "failed"}) or 0
    assert audit.write_audit(actor="a", action="POST", target="/x", result="200") is False
    assert REGISTRY.get_sample_value("smo_audit_writes_total", {"outcome": "failed"}) == before + 1


def test_the_switch():
    assert audit.audit_enabled({}) and audit.audit_enabled({"R1_AUDIT": "on"})
    assert not audit.audit_enabled({"R1_AUDIT": "off"}) and not audit.audit_enabled({"R1_AUDIT": "0"})


@pytest.fixture(params=["sqlite", "postgres"])
def shared_engine(request, tmp_path):
    import os
    from sqlalchemy import create_engine
    if request.param == "postgres":
        if not os.environ.get("SMO_TEST_POSTGRES_URL"):
            pytest.skip("SMO_TEST_POSTGRES_URL not set")
        engine = create_engine(os.environ["SMO_TEST_POSTGRES_URL"], future=True)
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'audit.db'}", future=True, connect_args={"timeout": 30})
    tables = [audit.AuditEntry.__table__, audit.AuditHead.__table__]
    Base.metadata.drop_all(engine, tables=tables)
    Base.metadata.create_all(engine, tables=tables)
    with engine.begin() as conn:
        conn.execute(audit.AuditHead.__table__.insert().values(head_id=1, last_seq=0, last_hash=audit.GENESIS))
    yield engine
    Base.metadata.drop_all(engine, tables=tables)
    engine.dispose()


def test_concurrent_writers_never_fork_the_chain(shared_engine, monkeypatch):
    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr("smo_shared.db.SessionLocal", sessionmaker(bind=shared_engine, autoflush=False, future=True))
    # Postgres locks the head row, so writers queue; SQLite does not, so a lost race is a duplicate number that the primary key refuses
    # and `write_audit` takes again: either way the chain has no fork and no gap.
    results = []

    def writer(n):
        for i in range(15):
            results.append(audit.write_audit(actor=f"w{n}", action="POST", target=f"/x/{i}", result="200"))

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    with Session(shared_engine) as db:
        written = db.query(audit.AuditEntry).count()
        assert written == results.count(True)
        assert [r.seq for r in db.query(audit.AuditEntry).order_by(audit.AuditEntry.seq)] == list(range(1, written + 1))
        assert audit.verify(db) is None
    if shared_engine.dialect.name == "postgresql":
        assert results.count(True) == 60
