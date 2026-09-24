"""Read-time normalization for `DateTime(timezone=True)` columns.

SQLite round-trips such a column as a naive `datetime` (no `tzinfo`) even
when every value was written from `datetime.now(datetime.UTC)`; Postgres
returns one already tz-aware. Subtracting `datetime.now(datetime.UTC)`
from a naive value raises `TypeError: can't subtract offset-naive and
offset-aware datetimes` — first hit by A1 Related's service supervision
(OPEN_ITEMS.md section 5, the first elapsed-time computation this build
ever did), needed again by SME's issued-token expiry check.
"""

import datetime


def as_utc(dt: datetime.datetime) -> datetime.datetime:
    """Treat a naive value as UTC — that's what's always written — rather
    than the local system timezone Python would otherwise assume.
    """
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=datetime.UTC)
