# Retention

How long each high-volume table keeps its rows, which variable changes it, and what removes the rows (`PR-DB-3`). **Nothing is deleted by default**: a retention of `0` keeps the rows for ever, so an upgrade deletes nothing until an operator sets a number. Decided October 2026: the code defaults stay at `0`, and the values in the "Proposed value" column ship in a production sample values file and in `.env.example` (`DB-3.10`, 0.6.0), not as defaults. They are proposals for an operator to confirm, not legal advice; the retention a law or a contract requires is the operator's to decide (`docs/PRIVACY.md`).

## The table (`DB-3.1`)

| Table | Module | What a row is | Grows with | Setting (days; `0` keeps) | Proposed value (production sample) | Removed by | Notes |
|---|---|---|---|---|---|---|---|
| `alarm` | RAN NF OAM | An alarm | Every alarm ingested | `SMO_RETENTION_ALARMS_DAYS` | 90 | Worker task `purge-cleared-alarms` (hourly) | Only an alarm **cleared** more than N days ago. An alarm still raised is never removed, however old |
| `pm_file` | RAN NF OAM | A PM file: the measurements are the row's `content`, there is **no file on disk** | Every PM file reported | `SMO_RETENTION_PM_FILES_DAYS` | 14 | Worker task `purge-pm-files` (hourly) | By `file_ready_time`. KPIs (`/kpi-schedules`, `/kpis/...`) read these rows, so keep at least the longest KPI look-back |
| `safeguard_refusal` | RAN NF OAM | A recorded refusal by a safeguard | Every refusal | `SAFEGUARD_REFUSAL_RETENTION_DAYS` (older name, kept) | 90 | Worker task `purge-safeguard-refusals` (hourly), or `POST /safeguard-refusals/purge` | Subscriptions are not touched |
| `mdaf_report` | MDAF | A published analytics report | Every report | `SMO_RETENTION_MDAF_REPORTS_DAYS` | 30 | Worker task `purge-reports` (hourly), in the new `mdaf-worker` | By `generated_at` |
| `gui_audit_log` | GUI BFF | Who did what through the GUI | Every proxied call, login and denial | `GUI_AUDIT_RETENTION_DAYS` | 365 | `python -m app.retention`, run by cron or a CronJob (the BFF has no worker) | With `GUI_AUDIT_EXPORT_DIR` set, the rows are written there as JSON lines first, and only the rows written are deleted |
| `notification_outbox` | shared | A queued notification | Every notification | `SMO_OUTBOX_SENT_RETENTION_SECONDS` (existing, 86400) | 1 day | Every drain, for `SENT` rows | `DEAD` rows are **not** removed: they are what an operator looks at. A purge of `DEAD` rows is open |
| `idempotency_key` | shared | A stored answer for an idempotency key | Every command with a key | `IDEMPOTENCY_KEY_TTL_SECONDS` (existing, 86400) | 1 day | Purged when a key is written | |
| `audit_log` (platform chain) | R1 Termination | A hash-chained audit row | Every change through the gateway | none | keep | Nothing | Rows are never purged: removing the oldest breaks `verify`. Retention is by exporting (`python -m smo_shared.audit export`) and archiving. Pruning behind a signed checkpoint is open |
| `gui_login_failure` | GUI BFF | A failed-login counter | Names typed | none | – | Cleared by the name's next successful sign-in | Rows for names that never sign in accumulate; open |

## How it runs (`DB-3.7`)

The purges are tasks of the module's own worker (`python -m smo_shared.worker`, the single-runner of `PR-MSG-4`), so any number of workers runs a task at most once an hour across all of them, and a worker that dies frees the task for another. Each module's worker connects as that module's database role, so the purge can only touch that module's schema (`PR-DB-2`). A task is idempotent: it finds what is due from the database, deletes in batches of 1000 with a commit per batch, and may run again after a crash. A setting of `0` makes the task do nothing. The GUI audit purge is a command because the BFF is not on `smo_shared`:

```bash
GUI_AUDIT_RETENTION_DAYS=365 GUI_AUDIT_EXPORT_DIR=/var/lib/gui-bff/audit-export python -m app.retention
```

Compose passes each variable through from the environment (`.env`) to the worker; the Helm chart sets them under `modules.<worker>.env` (`ran-nf-oam-worker`, `mdaf-worker`); both default to `0`.

## What is tested

`shared/tests/test_retention.py` (the purge deletes only rows older than the cutoff, leaves a row with no date, applies extra conditions, goes through more than one batch, refuses no age), `ran-nf-oam/tests/test_tasks.py` (only cleared alarms older than N days, PM files older than N days, nothing without a setting), `mdaf/tests/test_tasks.py`, `gui-bff/tests/test_retention.py` (only the old rows, exported first, a failed export deletes nothing). Not tested: a purge against Postgres at volume (the unit tests run on SQLite), and the time a purge takes on a table of millions of rows.

## Not built

Time partitioning of the PM table, so an old partition drops in one statement (`DB-3.8`; it rewrites a table and is a schema step of its own), a purge of `DEAD` outbox rows, pruning the platform audit chain behind a checkpoint, and an expiry for `gui_login_failure`.
