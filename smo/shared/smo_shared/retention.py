"""Retention (PR-DB-3): how long a high-volume table keeps its rows, and the purge that enforces it.

A table's retention is one number of days in one variable, `SMO_RETENTION_<NAME>_DAYS`; `0` (or unset, or not a number) keeps the rows
forever, so an upgrade deletes nothing until an operator asks. The names and defaults are in `docs/RETENTION.md`.

  retention_days(variable)  the days configured in environment variable `variable` (0 = keep)
  purge(db, model, column, older_than_days, where=())
                            delete the rows of `model` whose `column` is older than that many days (and match `where`), in batches of
                            `BATCH`, one commit per batch; returns how many. Rows younger than the cutoff, and rows `where` excludes,
                            are never selected. It may run again after a crash: it finds what is due from the database.
"""

import datetime
import os
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

BATCH = 1000


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
