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
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

DEFAULT_LIMIT = 100
MAX_LIMIT = 500
MAX_OFFSET = 2**31 - 1  # a larger one overflows the database's integer (a 500 before the contract test found it)

PageLimit = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT, description="Max rows to return (1-500).")
PageOffset = Query(0, ge=0, le=MAX_OFFSET, description="Rows to skip before the first one returned.")


def _in_a_stable_order(stmt):
    """`stmt` ordered by its entity's primary key when it is not ordered at all: without an ORDER BY a database may return the rows of two pages in
    different orders, so a row could be on both pages or on neither (found by the volume lane, PR-V-6). A statement that orders itself is left as it is."""
    if stmt._order_by_clauses:                                  # noqa: SLF001 — the only way to ask a Select whether it is ordered
        return stmt
    entity = stmt.column_descriptions[0].get("entity") if stmt.column_descriptions else None
    if entity is None:
        return stmt
    return stmt.order_by(*inspect(entity).primary_key)


def paginate(db: Session, stmt, limit: int, offset: int) -> dict[str, Any]:
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(_in_a_stable_order(stmt).limit(limit).offset(offset)).all()
    return {"items": rows, "total": total, "limit": limit, "offset": offset}
