"""The periodic work of MDAF, run by `python -m smo_shared.worker` (compose service `mdaf-worker`), never by a request process.

  purge-reports   every hour   reports generated more than `SMO_RETENTION_MDAF_REPORTS_DAYS` ago (default 0: keep them)
"""

from smo_shared.db import SessionLocal
from smo_shared.retention import purge, report_retention_off, retention_days
from smo_shared.worker import Task

from .models import MDAFReport


def purge_reports() -> None:
    """Deletes reports older than the retention, then reports on the ones kept.

    Reads `SMO_RETENTION_MDAF_REPORTS_DAYS` on every run. With a positive value, `generated_at` older than that many days is
    purged; with 0 (the default) nothing is deleted and `report_retention_off` sets the row-count gauge and the daily warning for the
    table that is kept forever. Opens its own session, as the worker calls it outside any request.
    """
    days = retention_days("SMO_RETENTION_MDAF_REPORTS_DAYS")
    with SessionLocal() as db:
        if days > 0:
            purge(db, MDAFReport, MDAFReport.generated_at, days)
        report_retention_off(db, "mdaf_report", MDAFReport, days)       # retention off: the row estimate, the gauge and the daily warning


# The worker (`python -m smo_shared.worker`) runs each task every `interval_seconds`; the tests pin this list, and `README.md` documents it.
TASKS = [Task("purge-reports", 3600, purge_reports)]
