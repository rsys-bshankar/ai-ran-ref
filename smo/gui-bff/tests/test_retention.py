"""PR-DB-3.6: the audit-log purge deletes only the rows older than the cutoff, and exports them first when asked."""

import datetime
import json

import pytest

from app.db import AuditEntry, Database
from app.retention import purge_audit

NOW = datetime.datetime(2026, 10, 1, tzinfo=datetime.UTC)


@pytest.fixture
def db(tmp_path):
    """A file-backed audit database holding five rows aged 400, 100, 91, 89 and 1 days at `NOW`."""
    database = Database(f"sqlite:///{tmp_path / 'gui.db'}")
    with database.session() as s:
        for days_old in (400, 100, 91, 89, 1):
            s.add(AuditEntry(at=NOW - datetime.timedelta(days=days_old), username="u", action=f"A{days_old}"))
        s.commit()
    return database


def actions(db):
    """The sorted `action` names of the rows left in the audit log."""
    with db.session() as s:
        return sorted(r.action for r in s.query(AuditEntry))


def test_only_rows_older_than_the_cutoff_go(db):
    """A 90-day purge deletes exactly the three rows older than the cutoff (400, 100 and 91 days) and keeps the others."""
    assert purge_audit(db, 90, now=NOW) == 3
    assert actions(db) == ["A1", "A89"]


def test_a_purge_with_no_age_is_refused(db):
    """A zero or negative age raises ValueError and deletes nothing, so a misconfiguration cannot wipe the log."""
    for days in (0, -1):
        with pytest.raises(ValueError):
            purge_audit(db, days, now=NOW)
    assert len(actions(db)) == 5


def test_the_rows_are_exported_before_they_are_deleted(db, tmp_path):
    """With an export directory, the deleted rows are written there as JSON lines before they go."""
    out = tmp_path / "export"
    assert purge_audit(db, 90, export_dir=out, now=NOW) == 3
    lines = [json.loads(line) for f in out.iterdir() for line in f.read_text().splitlines()]
    assert sorted(line["action"] for line in lines) == ["A100", "A400", "A91"]
    assert actions(db) == ["A1", "A89"]


def test_a_failed_export_deletes_nothing(db, tmp_path):
    """If the export directory cannot be created, the purge raises and every row stays."""
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(OSError):
        purge_audit(db, 90, export_dir=blocker / "sub", now=NOW)
    assert len(actions(db)) == 5


def test_more_rows_than_a_batch_all_go(db, monkeypatch):
    """When there are more rows than one batch, the loop repeats until all the old rows are gone."""
    monkeypatch.setattr("app.retention.BATCH", 2)
    assert purge_audit(db, 90, now=NOW) == 3


def test_a_large_audit_log_with_retention_off_is_warned_about(db, monkeypatch, caplog):
    """With retention off, a table above `SMO_RETENTION_WARN_ROWS` logs a warning, and a limit of 0 never warns; the row count is returned either way.
    """
    from app.retention import warn_if_large
    monkeypatch.setenv("SMO_RETENTION_WARN_ROWS", "4")
    with caplog.at_level("WARNING", logger="app.retention"):
        assert warn_if_large(db) == 5
    assert "gui_audit_log" in caplog.text
    caplog.clear()
    monkeypatch.setenv("SMO_RETENTION_WARN_ROWS", "0")
    with caplog.at_level("WARNING", logger="app.retention"):
        assert warn_if_large(db) == 5
    assert not caplog.records


def test_main_with_retention_off_deletes_nothing_and_still_counts(db, monkeypatch, capsys, tmp_path):
    """The command with `GUI_AUDIT_RETENTION_DAYS` unset deletes nothing, warns on stderr when the table is large and exits 0."""
    from app import retention
    monkeypatch.delenv("GUI_AUDIT_RETENTION_DAYS", raising=False)
    monkeypatch.setenv("SMO_RETENTION_WARN_ROWS", "1")
    monkeypatch.setenv("GUI_DATABASE_URL", f"sqlite:///{tmp_path / 'gui.db'}")
    monkeypatch.setattr(retention, "Database", lambda _url: db)
    assert retention.main() == 0
    assert "WARNING" in capsys.readouterr().err
    assert len(actions(db)) == 5
