"""PRB time series as the DME datasets deliver them: one record per cell and
sample, payload {managedElementRef, cellId, value | prbUtilization,
timestamp}. A cell is keyed "<managedElementRef>/<cellId>"."""

import datetime
from collections import defaultdict

Series = list[tuple[datetime.datetime, float]]


def parse_time(value: str) -> datetime.datetime:
    """Parses an ISO 8601 time (a trailing Z allowed) and returns it timezone-aware, assuming UTC when it has no offset."""
    t = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=datetime.UTC)


def cell_key(managed_element_ref: str, cell_id: str) -> str:
    return f"{managed_element_ref}/{cell_id}"


def by_cell(records: list[dict]) -> dict[str, Series]:
    """{cellKey: [(time, prb), ...]} sorted by time, from DME records (or bare payloads)."""
    out: dict[str, Series] = defaultdict(list)
    for record in records:
        p = record.get("payload", record)
        value = p.get("prbUtilization", p.get("value"))
        if value is None or "cellId" not in p:
            continue
        out[cell_key(p.get("managedElementRef", ""), str(p["cellId"]))].append((parse_time(p["timestamp"]), float(value)))
    return {k: sorted(v) for k, v in out.items()}


def value_at(series: Series, when: datetime.datetime) -> float | None:
    """The latest sample at or before `when`."""
    found = None
    for t, v in series:
        if t > when:
            break
        found = v
    return found


def hourly_pairs(series: Series, horizon: datetime.timedelta = datetime.timedelta(hours=1)) -> list[tuple[int, float, float, float]]:
    """(hourOfDay, prbNow, prbHourAgo, prbNextHour) for every sample that has
    both an hour-earlier and an hour-later sample — the regression's rows."""
    index = dict(series)
    rows = []
    for t, v in series:
        before, after = index.get(t - horizon), index.get(t + horizon)
        if before is not None and after is not None:
            rows.append((t.hour, v, before, after))
    return rows


def hourly_profile(series_by_cell: dict[str, Series]) -> list[float]:
    """Mean PRB per hour of day across every cell's history (24 values)."""
    sums, counts = [0.0] * 24, [0] * 24
    for series in series_by_cell.values():
        for t, v in series:
            sums[t.hour] += v
            counts[t.hour] += 1
    overall = sum(sums) / max(1, sum(counts))
    return [round(sums[h] / counts[h], 4) if counts[h] else round(overall, 4) for h in range(24)]
