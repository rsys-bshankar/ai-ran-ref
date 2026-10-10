"""Handover performance as the DME datasets deliver it: one record per
neighbour relation and PM window. The payload is
{managedElementRef, cellId, relation, values: {MM.* counter: count}, timestamp}.
A relation is keyed by its id, e.g. "201-202" (source cell → target cell)."""

import datetime
from collections import defaultdict

# TS 28.552-style mobility counters, with the failure classes of
# TS 38.300 §15.5.2 (mobility robustness optimisation)
ATTEMPTS = "MM.HoExeAtt"
TOO_LATE = "MM.HoFailTooLate"
TOO_EARLY = "MM.HoFailTooEarly"
WRONG_CELL = "MM.HoFailWrongCell"
PING_PONG = "MM.HoPingPong"
FAILURE_COUNTERS = {TOO_LATE: "TOO_LATE", TOO_EARLY: "TOO_EARLY", WRONG_CELL: "WRONG_CELL", PING_PONG: "PING_PONG"}

Window = tuple[datetime.datetime, dict]
Series = list[Window]


def parse_time(value: str) -> datetime.datetime:
    """Parses an ISO 8601 time (a trailing Z allowed) and returns it timezone-aware, assuming UTC when it has no offset."""
    t = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=datetime.UTC)


def by_relation(records: list[dict]) -> dict[str, Series]:
    """{relation: [(time, counters), ...]}, sorted by time."""
    out: dict[str, Series] = defaultdict(list)
    for record in records:
        p = record.get("payload", record)
        if p.get("relation") and isinstance(p.get("values"), dict):
            out[p["relation"]].append((parse_time(p["timestamp"]), p))
    return {k: sorted(v, key=lambda w: w[0]) for k, v in out.items()}


def counters(window_payload: dict) -> dict:
    return window_payload.get("values", {})


def attempts(c: dict) -> float:
    return float(c.get(ATTEMPTS, 0))


def mro_rate(c: dict) -> float:
    """Mobility problem rate (%): failures of every class plus ping-pongs, per attempt."""
    att = attempts(c)
    return 0.0 if att <= 0 else round(100.0 * sum(float(c.get(k, 0)) for k in FAILURE_COUNTERS) / att, 3)


def dominant_cause(c: dict) -> str | None:
    """The failure class with the most events (too-early and ping-pong both
    say "hand over later", so they count together against too-late)."""
    late, early = float(c.get(TOO_LATE, 0)), float(c.get(TOO_EARLY, 0)) + float(c.get(PING_PONG, 0))
    wrong = float(c.get(WRONG_CELL, 0))
    if late + early + wrong == 0:
        return None
    if late >= early and late >= wrong:
        return "TOO_LATE"
    if early >= wrong:
        return "TOO_EARLY" if float(c.get(TOO_EARLY, 0)) >= float(c.get(PING_PONG, 0)) else "PING_PONG"
    return "WRONG_CELL"


def merged(windows: list[Window]) -> dict:
    """Sum the counters of several windows."""
    total: dict = defaultdict(float)
    for _, p in windows:
        for k, v in counters(p).items():
            total[k] += float(v)
    return dict(total)


def value_at(series: Series, when: datetime.datetime) -> dict | None:
    """The payload of the latest window at or before `when`, or None when there is none."""
    found = None
    for t, p in series:
        if t > when:
            break
        found = p
    return found


def hourly_pairs(series: Series) -> list[tuple[float, float, float]]:
    """(rateNow, rateHourAgo, rateNextHour) for windows with neighbours both sides."""
    index = {t: p for t, p in series}
    hour = datetime.timedelta(hours=1)
    rows = []
    for t, p in series:
        before, after = index.get(t - hour), index.get(t + hour)
        if before is not None and after is not None:
            rows.append((mro_rate(counters(p)), mro_rate(counters(before)), mro_rate(counters(after))))
    return rows
