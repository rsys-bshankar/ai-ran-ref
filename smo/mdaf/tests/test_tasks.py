"""Tests of the MDAF worker task (`app/tasks.py`): the report retention purge.

Run: `cd smo/mdaf && PYTHONPATH=.:../shared python -m pytest tests/test_tasks.py -q`. Uses the SQLite `db_session_factory` of
`test_main.py`; `tasks.SessionLocal` is patched to it, so the task never touches a real database.
"""

import datetime

from sqlalchemy import select

from test_main import db_session_factory  # noqa: F401  (pytest fixture)

from app import tasks
from app.models import MDAFReport


def _reports(factory):
    """Adds two reports: one generated 100 days ago, one a day ago."""
    now = datetime.datetime.now(datetime.UTC)
    with factory() as db:
        for name, age in (("old", 100), ("new", 1)):
            db.add(MDAFReport(analytics_type=name, input_sources=[], output={}, generated_at=now - datetime.timedelta(days=age)))
        db.commit()


def _left(factory):
    """The analytics types of the reports still stored, sorted."""
    with factory() as db:
        return sorted(r.analytics_type for r in db.scalars(select(MDAFReport)))


def test_reports_are_kept_unless_a_retention_is_configured(db_session_factory, monkeypatch):
    """With no retention configured the purge deletes nothing."""
    _reports(db_session_factory)
    monkeypatch.setattr(tasks, "SessionLocal", db_session_factory)
    tasks.purge_reports()
    assert _left(db_session_factory) == ["new", "old"]


def test_only_reports_older_than_the_retention_go(db_session_factory, monkeypatch):
    """With a 30 day retention only the report older than that is deleted."""
    _reports(db_session_factory)
    monkeypatch.setattr(tasks, "SessionLocal", db_session_factory)
    monkeypatch.setenv("SMO_RETENTION_MDAF_REPORTS_DAYS", "30")
    tasks.purge_reports()
    assert _left(db_session_factory) == ["new"]


def test_the_task_list_is_what_the_docs_say():
    """The worker runs exactly one task, `purge-reports`, hourly, as the README states."""
    assert {t.name: t.interval_seconds for t in tasks.TASKS} == {"purge-reports": 3600}


def test_reports_with_retention_off_are_counted_for_the_gauge(db_session_factory, monkeypatch):
    """With retention off the number of reports kept is exported in the `smo_retention_off_rows` gauge."""
    from prometheus_client import REGISTRY
    _reports(db_session_factory)
    monkeypatch.setattr(tasks, "SessionLocal", db_session_factory)
    tasks.purge_reports()
    assert REGISTRY.get_sample_value("smo_retention_off_rows", {"table": "mdaf_report"}) == 2
