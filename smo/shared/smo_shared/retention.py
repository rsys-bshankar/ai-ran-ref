"""Retention (PR-DB-3): how long a high-volume table keeps its rows, and the purge that enforces it.

A table's retention is one number of days in one variable, `SMO_RETENTION_<NAME>_DAYS`; `0` (or unset, or not a number) keeps the rows
forever, so an upgrade deletes nothing until an operator asks. The names and defaults are in `docs/RETENTION.md`.

  retention_days(variable)  the days configured in environment variable `variable` (0 = keep)
  purge(db, model, column, older_than_days, where=())
                            delete the rows of `model` whose `column` is older than that many days (and match `where`), in batches of
                            `BATCH`, one commit per batch; returns how many. Rows younger than the cutoff, and rows `where` excludes,
                            are never selected. It may run again after a crash: it finds what is due from the database.
  report_retention_off(db, table, model, days)
                            for a table whose retention is `0`: estimate its rows (Postgres: `pg_class.reltuples`, no scan; other databases:
                            `count(*)`), set the gauge `smo_retention_off_rows{table}`, and log one WARNING per table per UTC day when the
                            estimate exceeds `SMO_RETENTION_WARN_ROWS` (default 1000000; `0` never warns). With retention on it drops the series.
                            It never raises: a failed estimate must not fail the purge task.
"""

import datetime
import logging
import os
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

BATCH = 1000
DEFAULT_WARN_ROWS = 1_000_000

log = logging.getLogger(__name__)
_warned: dict[str, datetime.date] = {}


def retention_days(variable: str) -> int:
    try:
        return max(0, int(os.environ.get(variable, "0") or 0))
    except ValueError:
        return 0


def purge(db: Session, model: Any, column: Any, older_than_days: int, where: Iterable[Any] = (),
          now: datetime.datetime | None = None) -> int:
    """Delete rows of `model` with `column` before now - days. `older_than_days` must be positive: a purge never runs with no age."""
    if older_than_days <= 0:
        raise ValueError("older_than_days must be positive")
    cutoff = (now or datetime.datetime.now(datetime.UTC)) - datetime.timedelta(days=older_than_days)
    key = list(model.__table__.primary_key.columns)
    conditions: Sequence[Any] = [column < cutoff, *where]
    total = 0
    while True:
        ids = db.execute(select(*key).where(*conditions).limit(BATCH)).all()
        if not ids:
            return total
        predicate = key[0].in_([r[0] for r in ids]) if len(key) == 1 else None
        if predicate is None:
            raise ValueError(f"{model.__name__}: purge needs a single-column primary key")
        db.execute(delete(model).where(predicate))
        db.commit()
        total += len(ids)


def warn_rows() -> int:
    """`SMO_RETENTION_WARN_ROWS`: the row count above which a table with retention off is warned about (0 = never)."""
    try:
        return max(0, int(os.environ.get("SMO_RETENTION_WARN_ROWS", str(DEFAULT_WARN_ROWS))))
    except ValueError:
        return DEFAULT_WARN_ROWS


def estimate_rows(db: Session, model: Any) -> int:
    """Rows in the table of `model`: `pg_class.reltuples` on Postgres (an estimate kept by autovacuum, -1 when never analysed: read as 0), else `count(*)`."""
    table = model.__table__
    if db.get_bind().dialect.name == "postgresql":
        qualified = f'"{table.schema}"."{table.name}"' if table.schema else f'"{table.name}"'
        value = db.execute(text("SELECT reltuples FROM pg_class WHERE oid = to_regclass(:name)"), {"name": qualified}).scalar()
        return max(0, int(value or 0))
    return int(db.execute(select(func.count()).select_from(table)).scalar() or 0)


def report_retention_off(db: Session, table: str, model: Any, days: int, today: datetime.date | None = None) -> None:
    """Set `smo_retention_off_rows{table}` for a table whose retention is off, and warn (once per table per UTC day) when it is large."""
    from .metrics import record_retention_off_rows
    if days > 0:
        record_retention_off_rows(table, None)
        return
    try:
        rows = estimate_rows(db, model)
    except Exception:                                   # noqa: BLE001 (an estimate must never fail the purge task)
        log.debug("retention: row estimate for %s failed", table, exc_info=True)
        return
    record_retention_off_rows(table, rows)
    limit = warn_rows()
    day = today or datetime.datetime.now(datetime.UTC).date()
    if limit > 0 and rows > limit and _warned.get(table) != day:
        _warned[table] = day
        log.warning("retention is off for %s and the table has about %d rows (more than SMO_RETENTION_WARN_ROWS=%d): set its retention "
                    "(docs/RETENTION.md) or raise the limit", table, rows, limit, extra={"table": table, "rows": rows})
