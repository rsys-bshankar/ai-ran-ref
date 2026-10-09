"""The periodic work of RAN NF OAM (PR-MSG-4), run by `python -m smo_shared.worker` (compose service `ran-nf-oam-worker`), never by a request
process. Each task finds what is due from the database, does it in committed steps, and may run again after a crash.

  advance-waves             every 15 s   the next wave of every staged CM job whose pause has elapsed (`POST /config-jobs/advance-due`), and of every software campaign
                                         (`POST /software-campaigns/advance-due`, which also fails the software jobs of a campaign with a `jobTimeoutSeconds` that have not reported in time, MGT-15.7)
  publish-kpis              every 30 s   the KPI schedules whose interval has passed (`PUT /kpi-schedules/{id}`)
  run-kpi-guards            every minute the KPI guards of finished CM jobs whose observation window has passed (`kpiGuard` on `POST /config-jobs`)
  expire-approvals          every minute the rApp action approvals nobody decided in time (EXPIRED or REJECTED by the policy they were parked under; AI-11.3)
  chain-decisions           every minute the decision records not yet written to the audit chain (AI-13.1; normally done right after the commit)
  purge-safeguard-refusals  every hour   refusal records older than `SAFEGUARD_REFUSAL_RETENTION_DAYS` (default 0: keep them)
  purge-cleared-alarms      every hour   alarms cleared more than `SMO_RETENTION_ALARMS_DAYS` ago (default 0: keep); an alarm still raised is never purged
  purge-pm-files            every hour   PM files ready more than `SMO_RETENTION_PM_FILES_DAYS` ago (default 0: keep); the content is the row, no file on disk

A purge task whose retention is `0` still estimates its table's rows (`smo_shared.retention.report_retention_off`): the gauge `smo_retention_off_rows{table}`
and one WARNING a day above `SMO_RETENTION_WARN_ROWS`.
"""

from smo_shared.db import SessionLocal
from smo_shared.retention import purge, report_retention_off, retention_days
from smo_shared.worker import Task

from .models import Alarm, PMFile, SafeguardRefusal


def advance_waves() -> None:
    from . import lifecycle, main
    with SessionLocal() as db:
        main.advance_due(db)
        lifecycle.advance_due(db)                  # MGT-15: the software campaigns whose pause has elapsed, and the running ones that have fallen behind


def publish_kpis() -> None:
    from . import main
    with SessionLocal() as db:
        main.run_due_kpi_schedules(db)


def run_kpi_guards() -> None:
    from . import main
    with SessionLocal() as db:
        main.run_due_kpi_guards(db)


def expire_approvals() -> None:
    from . import main
    with SessionLocal() as db:
        main.lapse_due_approvals(db)


def chain_decisions() -> None:
    from . import main
    with SessionLocal() as db:
        main.chain_decisions(db)


def purge_safeguard_refusals() -> None:
    from . import main
    with SessionLocal() as db:
        if main.SAFEGUARD_REFUSAL_RETENTION_DAYS > 0:
            main.purge_safeguard_refusals(db, main.SAFEGUARD_REFUSAL_RETENTION_DAYS)
        report_retention_off(db, "safeguard_refusal", SafeguardRefusal, main.SAFEGUARD_REFUSAL_RETENTION_DAYS)


def purge_cleared_alarms() -> None:
    days = retention_days("SMO_RETENTION_ALARMS_DAYS")
    with SessionLocal() as db:
        if days > 0:
            purge(db, Alarm, Alarm.cleared_at, days)       # a NULL cleared_at never compares older, so a raised alarm stays
        report_retention_off(db, "alarm", Alarm, days)


def purge_pm_files() -> None:
    days = retention_days("SMO_RETENTION_PM_FILES_DAYS")
    with SessionLocal() as db:
        if days > 0:
            purge(db, PMFile, PMFile.file_ready_time, days)
        report_retention_off(db, "pm_file", PMFile, days)


TASKS = [Task("advance-waves", 15, advance_waves), Task("publish-kpis", 30, publish_kpis), Task("run-kpi-guards", 60, run_kpi_guards),
         Task("expire-approvals", 60, expire_approvals), Task("chain-decisions", 60, chain_decisions),
         Task("purge-safeguard-refusals", 3600, purge_safeguard_refusals), Task("purge-cleared-alarms", 3600, purge_cleared_alarms),
         Task("purge-pm-files", 3600, purge_pm_files)]
