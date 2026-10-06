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

`PageLimit`/`PageOffset` are the shared parameter declarations every
route's own signature uses, so every service's generated OpenAPI spec
documents the same bounds (1-500, default 100) for `limit` and describes
`offset` the same way.

`?total=false` (opt-out of the count). The `COUNT(*)` of the whole result
is linear in the result (55 ms at a million alarms); a client that pages
with "is there a next page" does not need it. `PageLimit` is a dependency
that reads `limit` AND the optional boolean query parameter `total`
(default true: the response is exactly what it always was) and hands the
route a `PageSize`, an `int` that remembers `total`; so the 100 list routes
gain the parameter by this one declaration and `paginate()` finds the
choice on the `limit` it is already given. With `total=false` no count
query is issued, `limit + 1` rows are fetched, and the envelope is
`{"items", "limit", "offset", "hasMore"}`: the `total` key is left OUT
(the list envelopes are not declared in the specs, so nothing a client was
promised goes missing; a client that reads `total` must not ask for
`total=false`), and `hasMore` says whether a row follows the page (it is
only present in this mode).
"""

from collections.abc import Sequence
from typing import Any

from fastapi import Depends, Query
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

DEFAULT_LIMIT = 100
MAX_LIMIT = 500
MAX_OFFSET = 2**31 - 1  # a larger one overflows the database's integer (a 500 before the contract test found it)



class PageSize(int):
    """The `limit` of a page: an int that also carries whether the caller wants the page's `total` counted (`?total=false` says no)."""

    with_total: bool = True

    def __new__(cls, limit: int, with_total: bool = True):
        self = super().__new__(cls, limit)
        self.with_total = with_total
        return self


def _page_limit(limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT, description="Max rows to return (1-500)."),
                total: bool = Query(True, description="`false` skips the count of the whole result (linear in its size): the response then has "
                                    "no `total` and a `hasMore` flag instead. Default `true`.")) -> PageSize:
    return PageSize(limit, total)


PageLimit: Any = Depends(_page_limit)
PageOffset = Query(0, ge=0, le=MAX_OFFSET, description="Rows to skip before the first one returned.")


def _in_a_stable_order(stmt):
    """`stmt` ordered by its entity's primary key when it is not ordered at all: without an ORDER BY a database may return the rows of two pages in
    different orders, so a row could be on both pages or on neither (found by the volume lane, PR-V-6). A statement that orders itself is left as it is."""
    if stmt._order_by_clauses:                                  # noqa: SLF001 — the only way to ask a Select whether it is ordered
        return stmt
    entity = stmt.column_descriptions[0].get("entity")
    if entity is None:
        return stmt
    return stmt.order_by(*inspect(entity).primary_key)


def _envelope(items: Sequence, limit: int, offset: int, total: int | None, has_more: bool | None) -> dict[str, Any]:
    out: dict[str, Any] = {"items": items, "limit": int(limit), "offset": offset}
    if total is not None:
        out["total"] = total
    if has_more is not None:
        out["hasMore"] = has_more
    return out


def paginate(db: Session, stmt, limit: int, offset: int) -> dict[str, Any]:
    n = int(limit)
    if getattr(limit, "with_total", True):
        total = db.scalar(select(func.count()).select_from(stmt.subquery()))
        rows = db.scalars(_in_a_stable_order(stmt).limit(n).offset(offset)).all()
        return {"items": rows, "total": total, "limit": n, "offset": offset}
    rows = db.scalars(_in_a_stable_order(stmt).limit(n + 1).offset(offset)).all()    # one more row than asked: is there a next page
    return _envelope(rows[:n], n, offset, None, len(rows) > n)


def paginate_list(rows: list, limit: int, offset: int) -> dict[str, Any]:
    """The same envelope for a result that is already a Python list (a route that filters in Python): a slice, and `total` counted unless `total=false`."""
    n = int(limit)
    page = rows[offset:offset + n]
    if getattr(limit, "with_total", True):
        return _envelope(page, n, offset, len(rows), None)
    return _envelope(page, n, offset, None, offset + n < len(rows))
