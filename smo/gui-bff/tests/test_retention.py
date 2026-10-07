"""PR-DB-3.6: the audit-log purge deletes only the rows older than the cutoff, and exports them first when asked."""

import datetime
import json

import pytest

from app.db import AuditEntry, Database
from app.retention import purge_audit

NOW = datetime.datetime(2026, 10, 1, tzinfo=datetime.UTC)


@pytest.fixture
def db(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'gui.db'}")
    with database.session() as s:
        for days_old in (400, 100, 91, 89, 1):
            s.add(AuditEntry(at=NOW - datetime.timedelta(days=days_old), username="u", action=f"A{days_old}"))
        s.commit()
    return database


def actions(db):
    with db.session() as s:
        return sorted(r.action for r in s.query(AuditEntry))


def test_only_rows_older_than_the_cutoff_go(db):
    assert purge_audit(db, 90, now=NOW) == 3
    assert actions(db) == ["A1", "A89"]


def test_a_purge_with_no_age_is_refused(db):
    for days in (0, -1):
        with pytest.raises(ValueError):
            purge_audit(db, days, now=NOW)
    assert len(actions(db)) == 5


def test_the_rows_are_exported_before_they_are_deleted(db, tmp_path):
    out = tmp_path / "export"
    assert purge_audit(db, 90, export_dir=out, now=NOW) == 3
    lines = [json.loads(line) for f in out.iterdir() for line in f.read_text().splitlines()]
    assert sorted(line["action"] for line in lines) == ["A100", "A400", "A91"]
    assert actions(db) == ["A1", "A89"]


def test_a_failed_export_deletes_nothing(db, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(OSError):
        purge_audit(db, 90, export_dir=blocker / "sub", now=NOW)
    assert len(actions(db)) == 5


def test_more_rows_than_a_batch_all_go(db, monkeypatch):
    monkeypatch.setattr("app.retention.BATCH", 2)
    assert purge_audit(db, 90, now=NOW) == 3
