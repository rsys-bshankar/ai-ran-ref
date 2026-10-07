"""The periodic work of MDAF, run by `python -m smo_shared.worker` (compose service `mdaf-worker`), never by a request process.

  purge-reports   every hour   reports generated more than `SMO_RETENTION_MDAF_REPORTS_DAYS` ago (default 0: keep them)
"""

from smo_shared.db import SessionLocal
from smo_shared.retention import purge, retention_days
from smo_shared.worker import Task

from .models import MDAFReport


def purge_reports() -> None:
    days = retention_days("SMO_RETENTION_MDAF_REPORTS_DAYS")
    if days > 0:
        with SessionLocal() as db:
            purge(db, MDAFReport, MDAFReport.generated_at, days)


TASKS = [Task("purge-reports", 3600, purge_reports)]
