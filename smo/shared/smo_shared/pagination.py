"""Wave 3 (cross-cutting standardization) — Pagination.

Every list-returning GET route across this build used to return either a
bare, unbounded array, or (a handful of routes) a fixed `limit` cap with no
way to page past it — confirmed by a full-codebase audit before this was
built, not assumed. Real breaking change, asked rather than guessed:
confirmed, build it. Every such route now returns
`{"items": [...], "total": N, "limit": L, "offset": O}` instead.

`paginate()` does the actual work at the SQL level (a real `LIMIT`/`OFFSET`
on the query, plus a real `COUNT(*)` for `total` — never a Python-level
slice of an already-fetched full result set) and returns raw ORM rows for
the caller to map through its own per-resource view function:

    page = paginate(db, select(TrainingJob).where(...), limit, offset)
    return {**page, "items": [_training_job_view(j) for j in page["items"]]}

`PageQuery`/`PageOffset` are the shared `Query(...)` parameter
declarations every route's own signature uses, so every service's
generated OpenAPI spec documents the same bounds (1-500, default 100) for
`limit` and describes `offset` the same way.
"""

from typing import Any

from fastapi import Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

DEFAULT_LIMIT = 100
MAX_LIMIT = 500

PageLimit = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT, description="Max rows to return (1-500).")
PageOffset = Query(0, ge=0, description="Rows to skip before the first one returned.")


def paginate(db: Session, stmt, limit: int, offset: int) -> dict[str, Any]:
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.limit(limit).offset(offset)).all()
    return {"items": rows, "total": total, "limit": limit, "offset": offset}
