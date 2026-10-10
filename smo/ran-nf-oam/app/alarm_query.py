"""PR-GUI-9.4, 9.5, 9.8: the SQL pieces behind the alarm console's reads: filters, keyset cursors, severity order, hourly buckets and elapsed time.

`main.py` calls these from `GET /alarms`, `GET /alarms/counts`, `GET /alarms/stats` and `GET /decision-records` (the cursor codec); `fleet.py` uses the
severity order for the health map and the worst-element ranking. Nothing here commits or writes: every function builds or runs a read.

Two SQL dialects are served: Postgres in production and SQLite in the unit tests. The only places that differ (truncating a time to the hour, the
seconds between two times) branch on `db.get_bind().dialect.name`; everything else is portable SQLAlchemy.

A keyset cursor is opaque to the client: base64url of a JSON list whose first element names the list it belongs to (`"a"` alarms, `"d"` decision
records), so a cursor of one list is refused by the other. It carries the sort key of the last row of the page, never a row the caller could not see:
the scope filter is applied again on every page.
"""

import base64
import binascii
import datetime
import json
import uuid

from sqlalchemy import and_, case, extract, func, literal_column, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement, Select

from smo_shared.errors import FrameworkError, framework_error
from smo_shared.timeutil import as_utc

from .models import Alarm, ManagedEntity

# The order of the console: the worst first. `indeterminate` is a real severity that says nothing about how bad it is, so it sorts after the four
# graded ones; a cleared alarm (severity `cleared`, HISTORY.md §5) is last.
SEVERITY_RANK = {"critical": 0, "major": 1, "minor": 2, "warning": 3, "indeterminate": 4, "cleared": 5}
GRADED_SEVERITIES = ("critical", "major", "minor", "warning")
ACK_STATES = ("ACKNOWLEDGED", "UNACKNOWLEDGED")


def severity_rank(column=Alarm.severity) -> ColumnElement:
    """The rank of `column` (`SEVERITY_RANK`) as a SQL expression; an unknown value (none exists: the column has a CHECK) ranks after `cleared`."""
    return case(SEVERITY_RANK, value=column, else_=len(SEVERITY_RANK))


def filtered_alarms(stmt: Select, *, managed_element_ref: str | None = None, managed_function_ref: str | None = None, severity: str | None = None,
                    ack_state: str | None = None, open_only: bool = False, probable_cause: str | None = None,
                    since: datetime.datetime | None = None, until: datetime.datetime | None = None, region: str | None = None) -> Select:
    """`stmt` (a select over `alarm`) narrowed by the console's filters; `None` (or `open_only=False`) leaves a filter out.

    `severity` must already be the stored lower-case form. `managed_function_ref` matches a flat ref, a full DN, or an RDN that ends a stored DN.
    `open_only` drops cleared alarms. `since` is inclusive and `until` exclusive, both on `raised_at`. `region` keeps the alarms of the elements
    registered in that region (ADR 0005's column); it narrows, it does not authorize: the caller's scope is applied by the route.
    """
    if managed_element_ref:
        stmt = stmt.where(Alarm.managed_element_ref == managed_element_ref)
    if managed_function_ref:
        stmt = stmt.where((Alarm.managed_function_ref == managed_function_ref)
                          | Alarm.managed_function_ref.endswith("," + managed_function_ref, autoescape=True))
    if severity:
        stmt = stmt.where(Alarm.severity == severity)
    if ack_state:
        stmt = stmt.where(Alarm.ack_state == ack_state)
    if open_only:
        stmt = stmt.where(Alarm.severity != "cleared")
    if probable_cause:
        stmt = stmt.where(Alarm.probable_cause == probable_cause)
    if since:
        stmt = stmt.where(Alarm.raised_at >= as_utc(since))
    if until:
        stmt = stmt.where(Alarm.raised_at < as_utc(until))
    if region:
        stmt = stmt.where(Alarm.managed_element_ref.in_(select(ManagedEntity.managed_element_ref).where(ManagedEntity.region == region)))
    return stmt


# ---------------------------------------------------------------- keyset cursors

def encode_cursor(kind: str, *values) -> str:
    """An opaque cursor for list `kind` carrying `values` (datetimes as ISO 8601 UTC, UUIDs as strings)."""
    plain = [as_utc(v).isoformat() if isinstance(v, datetime.datetime) else str(v) if isinstance(v, uuid.UUID) else v for v in values]
    return base64.urlsafe_b64encode(json.dumps([kind, *plain], separators=(",", ":")).encode()).decode().rstrip("=")


def decode_cursor(kind: str, text: str, arity: int) -> list:
    """The values of a cursor made by `encode_cursor(kind, ...)` with `arity` values; 422 `SCHEMA_VALIDATION_FAILED` for anything else.

    The cursor is caller-supplied: a malformed one, or one of another list, is refused instead of guessed at."""
    try:
        data = json.loads(base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)))
    except (binascii.Error, ValueError) as exc:
        raise _bad_cursor() from exc
    if not isinstance(data, list) or len(data) != arity + 1 or data[0] != kind:
        raise _bad_cursor()
    return data[1:]


def _bad_cursor():
    return framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED, detail="`after` is not a cursor of this list: pass the `nextCursor` of the previous page")


def parse_time(value) -> datetime.datetime:
    """A cursor's time back as an aware UTC datetime; 422 when it is not one."""
    try:
        return as_utc(datetime.datetime.fromisoformat(value))
    except (TypeError, ValueError) as exc:
        raise _bad_cursor() from exc


def parse_uuid(value) -> uuid.UUID:
    """A cursor's id back as a UUID; 422 when it is not one."""
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise _bad_cursor() from exc


def alarm_keyset_page(db: Session, stmt: Select, after: str, limit: int) -> tuple[list[Alarm], str | None]:
    """One page of `stmt` (a select of `Alarm`) in the console order (severity rank, raisedAt newest first, alarmId) after the cursor `after`
    (empty: the first page). Returns the rows and the cursor of the next page (`None` when this page is the last).

    Reads `limit + 1` rows so that "is there more" costs no count. The tie-breaker `alarm_id` makes the order total, so no row is on two pages or none."""
    rank = severity_rank()
    if after:
        r, t, i = decode_cursor("a", after, 3)
        if not isinstance(r, int):
            raise _bad_cursor()
        t, i = parse_time(t), parse_uuid(i)
        stmt = stmt.where(or_(rank > r, and_(rank == r, Alarm.raised_at < t), and_(rank == r, Alarm.raised_at == t, Alarm.alarm_id > i)))
    rows = db.scalars(stmt.order_by(rank, Alarm.raised_at.desc(), Alarm.alarm_id).limit(limit + 1)).all()
    page = list(rows[:limit])
    if len(rows) <= limit or not page:
        return page, None
    last = page[-1]
    return page, encode_cursor("a", SEVERITY_RANK.get(last.severity, len(SEVERITY_RANK)), last.raised_at, last.alarm_id)


# ---------------------------------------------------------------- dialect-specific expressions

def _dialect(db: Session) -> str:
    return db.get_bind().dialect.name


def hour_bucket(db: Session, column) -> ColumnElement:
    """`column` (a timestamp) truncated to its UTC hour, as text `YYYY-MM-DDTHH:00:00Z`, computed in SQL so a grouping returns one row per hour."""
    # The constant arguments are SQL literals, not bound parameters: the same expression appears in the SELECT and the GROUP BY, and Postgres
    # matches the two only when they are identical text (two parameters $1 and $2 are not, and the query fails "must appear in the GROUP BY").
    if _dialect(db) == "postgresql":
        # timezone('UTC', ...) first: date_trunc of a timestamptz truncates in the session's time zone, which need not be UTC
        return func.to_char(func.date_trunc(literal_column("'hour'"), func.timezone(literal_column("'UTC'"), column)),
                            literal_column("""'YYYY-MM-DD"T"HH24":00:00Z"'"""))
    return func.strftime(literal_column("'%Y-%m-%dT%H:00:00Z'"), column)


def seconds_between(db: Session, later, earlier) -> ColumnElement:
    """The seconds from `earlier` to `later` (two timestamp columns) as a SQL number; NULL when either is NULL."""
    if _dialect(db) == "postgresql":
        return extract("epoch", later - earlier)
    return (func.julianday(later) - func.julianday(earlier)) * 86400.0

