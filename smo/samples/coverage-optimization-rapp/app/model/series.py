"""Coverage performance as the DME datasets deliver it: one record per cell
and PM window. The payload is
{managedElementRef, cellId, values: {counter: value}, timestamp}.

The counters are measurement-report (MR) statistics of the TS 28.541 CCO
problem classes, plus the cell's CM snapshot that the data pipeline joins
into each window:

    MR.Total            measurement reports in the window
    MR.WeakRsrp         reports with serving RSRP below the weak-coverage threshold
    MR.Overshoot        reports from beyond the cell's planned range
    MR.PilotPollution   reports with several cells within 6 dB and none dominant
    MR.Overlap.<cell>   reports in which neighbour <cell> is within 6 dB
    CM.DigitalTilt      CommonBeamformingFunction.digitalTilt (0.1°)
    CM.ConfiguredMaxTxPower  NRSectorCarrier.configuredMaxTxPower (dBm)

A cell's neighbours are the cells it has overlap counters for."""

import datetime
from collections import defaultdict

TOTAL = "MR.Total"
WEAK = "MR.WeakRsrp"
OVERSHOOT = "MR.Overshoot"
POLLUTION = "MR.PilotPollution"
OVERLAP = "MR.Overlap."
TILT = "CM.DigitalTilt"
POWER = "CM.ConfiguredMaxTxPower"
# the three CCO problem classes, in the order the model uses them
PROBLEMS = {"WEAK_COVERAGE": WEAK, "OVERSHOOT": OVERSHOOT, "PILOT_POLLUTION": POLLUTION}

Window = tuple[datetime.datetime, dict]
Series = list[Window]


def parse_time(value: str) -> datetime.datetime:
    """Parses an ISO 8601 time (a trailing Z allowed) and returns it timezone-aware, assuming UTC when it has no offset."""
    t = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=datetime.UTC)


def by_cell(records: list[dict]) -> dict[str, Series]:
    """{cellId: [(time, payload), ...]}, sorted by time."""
    out: dict[str, Series] = defaultdict(list)
    for record in records:
        p = record.get("payload", record)
        if p.get("cellId") and isinstance(p.get("values"), dict) and TOTAL in p["values"]:
            out[p["cellId"]].append((parse_time(p["timestamp"]), p))
    return {k: sorted(v, key=lambda w: w[0]) for k, v in out.items()}


def counters(payload: dict) -> dict:
    return payload.get("values", {})


def total(c: dict) -> float:
    return float(c.get(TOTAL, 0))


def share(c: dict, counter: str) -> float:
    """One counter as a percentage of the window's report total, rounded to 3 places; 0 when the total is 0."""
    t = total(c)
    return 0.0 if t <= 0 else round(100.0 * float(c.get(counter, 0)) / t, 3)


def shares(c: dict) -> dict[str, float]:
    """{WEAK_COVERAGE, OVERSHOOT, PILOT_POLLUTION: % of reports}."""
    return {k: share(c, counter) for k, counter in PROBLEMS.items()}


def overlaps(c: dict) -> dict[str, float]:
    """{neighbour: % of reports in which it is within 6 dB}."""
    return {k[len(OVERLAP):]: share(c, k) for k in c if k.startswith(OVERLAP)}


def config(c: dict) -> tuple[float | None, float | None]:
    """(digitalTilt, configuredMaxTxPower) from the window's CM snapshot."""
    tilt, power = c.get(TILT), c.get(POWER)
    return (float(tilt) if tilt is not None else None, float(power) if power is not None else None)


def latest(series: Series) -> Window | None:
    return series[-1] if series else None


def at(series: Series, when: datetime.datetime) -> dict | None:
    """The payload of the window that starts exactly at `when`, or None."""
    for t, p in series:
        if t == when:
            return p
    return None


def snapshot(series_by_cell: dict[str, Series], cells: list[str] | None = None,
             when: datetime.datetime | None = None) -> dict[str, dict]:
    """The cluster's state from each cell's latest window (or the window at
    `when`): {cell: {shares, overlaps, total, observedAt, tilt, power}}."""
    out = {}
    for cell in cells if cells is not None else sorted(series_by_cell):
        series = series_by_cell.get(cell) or []
        window = (when, at(series, when)) if when is not None else latest(series)
        if not window or window[1] is None:
            continue
        c = counters(window[1])
        tilt, power = config(c)
        out[cell] = {"shares": shares(c), "overlaps": overlaps(c), "total": total(c),
                     "observedAt": window[0], "tilt": tilt, "power": power}
    return out
