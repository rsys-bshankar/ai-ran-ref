"""PR-DB-3.5: the MDAF worker purges reports older than the retention, and only those."""

import datetime

from sqlalchemy import select

from test_main import db_session_factory  # noqa: F401  (pytest fixture)

from app import tasks
from app.models import MDAFReport


def _reports(factory):
    now = datetime.datetime.now(datetime.UTC)
    with factory() as db:
        for name, age in (("old", 100), ("new", 1)):
            db.add(MDAFReport(analytics_type=name, input_sources=[], output={}, generated_at=now - datetime.timedelta(days=age)))
        db.commit()


def _left(factory):
    with factory() as db:
        return sorted(r.analytics_type for r in db.scalars(select(MDAFReport)))


def test_reports_are_kept_unless_a_retention_is_configured(db_session_factory, monkeypatch):
    _reports(db_session_factory)
    monkeypatch.setattr(tasks, "SessionLocal", db_session_factory)
    tasks.purge_reports()
    assert _left(db_session_factory) == ["new", "old"]


def test_only_reports_older_than_the_retention_go(db_session_factory, monkeypatch):
    _reports(db_session_factory)
    monkeypatch.setattr(tasks, "SessionLocal", db_session_factory)
    monkeypatch.setenv("SMO_RETENTION_MDAF_REPORTS_DAYS", "30")
    tasks.purge_reports()
    assert _left(db_session_factory) == ["new"]


def test_the_task_list_is_what_the_docs_say():
    assert {t.name: t.interval_seconds for t in tasks.TASKS} == {"purge-reports": 3600}
