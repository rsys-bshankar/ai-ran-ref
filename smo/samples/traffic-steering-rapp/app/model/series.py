"""Load performance as the DME datasets deliver it: one record per cell and
hourly PM window. The payload is
{managedElementRef, cellId, values: {counter: value}, timestamp}.

    RRU.PrbTotDl        DL PRB utilisation, %            (TS 28.552)
    RRC.ConnMean        mean RRC-connected UEs           (TS 28.552)
    DRB.UEThpDl         mean DL UE throughput, Mbit/s    (TS 28.552)
    PM.Samples          granularity samples in the window
    HO.Att.<cell>       handover attempts towards a neighbour
    HO.Fail.<cell>      handover failures towards a neighbour
    CM.Cio.<cell>       the CM snapshot: CIO (dB) on the relation to <cell>
    CM.Prio.<layer>     the CM snapshot: reselection priority towards a layer

A cell's neighbours are the cells it has handover counters for."""

import datetime
from collections import defaultdict

PRB = "RRU.PrbTotDl"
UES = "RRC.ConnMean"
THP = "DRB.UEThpDl"
SAMPLES = "PM.Samples"
HO_ATT, HO_FAIL = "HO.Att.", "HO.Fail."
CIO, PRIO = "CM.Cio.", "CM.Prio."
UE_CAPACITY = 200.0
THP_REFERENCE = 50.0

Window = tuple[datetime.datetime, dict]
Series = list[Window]


def parse_time(value: str) -> datetime.datetime:
    t = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=datetime.UTC)


def by_cell(records: list[dict]) -> dict[str, Series]:
    """{cellId: [(time, payload), ...]}, sorted by time."""
    out: dict[str, Series] = defaultdict(list)
    for record in records:
        p = record.get("payload", record)
        if p.get("cellId") and isinstance(p.get("values"), dict) and PRB in p["values"]:
            out[p["cellId"]].append((parse_time(p["timestamp"]), p))
    return {k: sorted(v, key=lambda w: w[0]) for k, v in out.items()}


def counters(payload: dict) -> dict:
    return payload.get("values", {})


def score(c: dict) -> float:
    """Congestion score, 0–100: 0.5·PRB % + 0.3·UE load % + 0.2·throughput deficit %."""
    prb = min(100.0, float(c.get(PRB, 0)))
    ue = min(100.0, 100.0 * float(c.get(UES, 0)) / UE_CAPACITY)
    deficit = max(0.0, min(100.0, 100.0 - 100.0 * float(c.get(THP, THP_REFERENCE)) / THP_REFERENCE))
    return round(0.5 * prb + 0.3 * ue + 0.2 * deficit, 3)


def neighbours(c: dict) -> list[str]:
    return sorted(k[len(HO_ATT):] for k in c if k.startswith(HO_ATT))


def ho_fail_rate(c: dict, target: str) -> float | None:
    att = float(c.get(f"{HO_ATT}{target}", 0))
    return None if att <= 0 else round(100.0 * float(c.get(f"{HO_FAIL}{target}", 0)) / att, 3)


def biases(c: dict) -> tuple[dict[str, float], dict[str, float]]:
    """({target: CIO dB}, {layer: priority}) from the window's CM snapshot."""
    return ({k[len(CIO):]: float(v) for k, v in c.items() if k.startswith(CIO)},
            {k[len(PRIO):]: float(v) for k, v in c.items() if k.startswith(PRIO)})


def at(series: Series, when: datetime.datetime) -> dict | None:
    for t, p in series:
        if t == when:
            return p
    return None


def latest(series: Series) -> Window | None:
    return series[-1] if series else None
