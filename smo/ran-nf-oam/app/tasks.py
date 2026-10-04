"""The periodic work of RAN NF OAM (PR-MSG-4), run by `python -m smo_shared.worker` (compose service `ran-nf-oam-worker`), never by a request
process. Each task finds what is due from the database, does it in committed steps, and may run again after a crash.

  advance-waves             every 15 s   the next wave of every staged CM job whose pause has elapsed (`POST /config-jobs/advance-due`)
  publish-kpis              every 30 s   the KPI schedules whose interval has passed (`PUT /kpi-schedules/{id}`)
  run-kpi-guards            every minute the KPI guards of finished CM jobs whose observation window has passed (`kpiGuard` on `POST /config-jobs`)
  purge-safeguard-refusals  every hour   refusal records older than `SAFEGUARD_REFUSAL_RETENTION_DAYS` (default 0: keep them)
"""

from smo_shared.db import SessionLocal
from smo_shared.worker import Task


def advance_waves() -> None:
    from . import main
    with SessionLocal() as db:
        main.advance_due(db)


def publish_kpis() -> None:
    from . import main
    with SessionLocal() as db:
        main.run_due_kpi_schedules(db)


def run_kpi_guards() -> None:
    from . import main
    with SessionLocal() as db:
        main.run_due_kpi_guards(db)


def purge_safeguard_refusals() -> None:
    from . import main
    if main.SAFEGUARD_REFUSAL_RETENTION_DAYS > 0:
        with SessionLocal() as db:
            main.purge_safeguard_refusals(db, main.SAFEGUARD_REFUSAL_RETENTION_DAYS)


TASKS = [Task("advance-waves", 15, advance_waves), Task("publish-kpis", 30, publish_kpis), Task("run-kpi-guards", 60, run_kpi_guards),
         Task("purge-safeguard-refusals", 3600, purge_safeguard_refusals)]
