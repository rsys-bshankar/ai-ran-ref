"""The data the console redesign asks of rApp Management, AIMgF, the Intent Service and DME (PR-GUI-9.8): training progress, intent fulfilment and
conflict, data-job delivery health, and an index for the latest rApp KPI.

  rapp_mgmt.ix_rapp_performance_report_instance_reported    (instance_id, reported_at): the newest report per instance
                                                            (`GET /rapp-mgmt/instances/{id}/performance/latest` and its batched form)
  aimgf.training_job.epoch, total_epochs, progress_updated_at
                                                            the epoch a run reached out of how many, and when it said so; the ETA is derived
  intent_service.intent.fulfilment_percent, fulfilled, in_conflict
                                                            what the newest fulfilment and conflict reports say, on the row so `GET /intents` can
                                                            filter on it in SQL (the reports are JSON)
  dme.data_job.expected_interval_seconds, last_delivery_at, late_after
                                                            how often the consumer expects data, when a producer last delivered a record, and when
                                                            the job turns LATE (two intervals later)

Expand only: one index and nine nullable columns that the previous release neither reads nor writes. During a rolling upgrade a report or record
written by the previous release does not update the new columns, so they can lag until the next one written by this release.

Data: the intent summary columns are filled for the intents that exist, from their stored reports (in Python, with the rule of
`intent-service/app/main.py`'s `fulfilment_percent` repeated below: a migration must not import application code); `in_conflict` is false for an
intent whose reports never named a conflict. `data_job.last_delivery_at` is filled with the time of the job's newest `data_record`. No job declares an
interval yet, so `late_after` stays NULL. Training progress has no history to fill. The downgrade drops the index and the nine columns.

Revision ID: 0035
Revises: 0034
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("aimgf.training_job", "epoch", "INTEGER"),
    ("aimgf.training_job", "total_epochs", "INTEGER"),
    ("aimgf.training_job", "progress_updated_at", "TIMESTAMP WITH TIME ZONE"),
    ("intent_service.intent", "fulfilment_percent", "DOUBLE PRECISION"),
    ("intent_service.intent", "fulfilled", "BOOLEAN"),
    ("intent_service.intent", "in_conflict", "BOOLEAN"),
    ("dme.data_job", "expected_interval_seconds", "INTEGER"),
    ("dme.data_job", "last_delivery_at", "TIMESTAMP WITH TIME ZONE"),
    ("dme.data_job", "late_after", "TIMESTAMP WITH TIME ZONE"),
)


def _json(value):
    """A JSON column's value as Python data: the driver usually decodes it already; a string is decoded here."""
    return json.loads(value) if isinstance(value, str) else value


def _fulfilment_percent(report):
    """The share (0-100, one decimal) of a fulfilment report that is FULFILLED, counted over its targets, else its expectations, else the intent's own
    verdict; None for nothing to count. The same rule as `fulfilment_percent` in `intent-service/app/main.py` (keep the two equal)."""
    if not isinstance(report, dict):
        return None
    results = [e for e in report.get("expectationFulfilmentResult") or [] if isinstance(e, dict)]
    infos = [t.get("targetFulfilmentInfo") for e in results for t in e.get("targetFulfilmentResults") or [] if isinstance(t, dict)]
    if not infos:
        infos = [e.get("expectationFulfilmentInfo") for e in results]
    if not infos:
        infos = [report.get("intentFulfilmentInfo")]
    infos = [i for i in infos if isinstance(i, dict)]
    if not infos:
        return None
    return round(100 * sum(i.get("fulfilmentStatus") == "FULFILLED" for i in infos) / len(infos), 1)


def _fill_intent_summaries() -> None:
    """Sets `fulfilment_percent`, `fulfilled` and `in_conflict` of every existing intent from its reports, oldest first so the newest of each kind wins."""
    bind = op.get_bind()
    newest: dict = {}
    rows = bind.execute(sa.text("SELECT intent_id, intent_fulfilment_report, intent_conflict_reports FROM intent_service.intent_report "
                                "ORDER BY last_updated_time, id"))
    for intent_id, fulfilment, conflicts in rows:
        summary = newest.setdefault(intent_id, {"percent": None, "fulfilled": None, "in_conflict": False})
        fulfilment, conflicts = _json(fulfilment), _json(conflicts)
        if fulfilment is not None:
            summary["percent"] = _fulfilment_percent(fulfilment)
            summary["fulfilled"] = ((fulfilment.get("intentFulfilmentInfo") or {}).get("fulfilmentStatus") == "FULFILLED") if isinstance(fulfilment, dict) else None
        if conflicts is not None:
            summary["in_conflict"] = bool(conflicts)
    update = sa.text("UPDATE intent_service.intent SET fulfilment_percent = :percent, fulfilled = :fulfilled, in_conflict = :in_conflict "
                     "WHERE intent_id = :intent_id")
    for intent_id, summary in newest.items():
        bind.execute(update, {"intent_id": intent_id, **summary})
    # an intent with no report at all is not in conflict either
    bind.execute(sa.text("UPDATE intent_service.intent SET in_conflict = false WHERE in_conflict IS NULL"))


def upgrade() -> None:
    """Create the latest-KPI index, add the nine nullable columns, then fill the intent summaries and the data jobs' last delivery from existing rows;
    every statement tolerates a rerun."""
    op.execute("CREATE INDEX IF NOT EXISTS ix_rapp_performance_report_instance_reported "
               "ON rapp_mgmt.rapp_performance_report (instance_id, reported_at)")
    for table, column, sql_type in _COLUMNS:
        op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {sql_type}")
    _fill_intent_summaries()
    # the newest record of each job is its last delivery; a job with no record keeps NULL
    op.execute("UPDATE dme.data_job AS j SET last_delivery_at = r.newest "
               "FROM (SELECT data_job_id, max(produced_at) AS newest FROM dme.data_record GROUP BY data_job_id) AS r "
               "WHERE r.data_job_id = j.data_job_id AND j.last_delivery_at IS NULL")


def downgrade() -> None:
    """Drop the nine columns and the index, in reverse order; recorded training progress, intent summaries and delivery times are lost."""
    for table, column, _ in reversed(_COLUMNS):
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {column}")
    op.execute("DROP INDEX IF EXISTS rapp_mgmt.ix_rapp_performance_report_instance_reported")
