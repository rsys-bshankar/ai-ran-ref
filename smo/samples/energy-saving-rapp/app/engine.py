"""The EnergySaving rApp's decision engine (Wave 10.1, W10-10..W10-15). Pure
functions, no I/O: the service (main.py) gathers inputs and acts on outputs.

    Input → Prediction → Safety evaluation → Decision {LOCK, UNLOCK, NO_CHANGE}

Cell states are rApp-internal (decision D-3):

  * SERVING   — the cell is in service.
  * PRE_SLEEP — PRB has been below 5 % but not yet for the full 60 minutes.
  * SLEEP     — the cell is locked or energy saving on O1.

Only PRE_SLEEP → SLEEP (a LOCK) and SLEEP → SERVING (an UNLOCK) write to O1.

Sleep (W10-11): every condition below must hold, and all guards must pass.
  * PRB < 5 % for at least 60 minutes, measured on the samples' own
    timestamps, so the result never depends on how often the rApp runs.
  * The model recommends LOCKED.
  * The MDAF prediction, when there is one, is also below 5 %.

Wake (W10-12): any one of these triggers an UNLOCK.
  * Predicted PRB > 15 %, from the model or from MDAF.
  * A neighbour's PRB > 80 %.
  * A critical coverage alarm.
  * An operator override.

Hysteresis (W10-13): 5–15 % is a NO_CHANGE zone in both directions.

Guards (W10-14) block a LOCK whatever the model's confidence:
  * HARD:
    - an EMERGENCY or COVERAGE_CRITICAL cell;
    - the last awake cell of its sector group;
    - a cell in an active incident zone.
  * MEDIUM:
    - neighbour congestion;
    - an active critical alarm on the managed element.
  * SOFT: a cell unlocked less than 30 minutes ago.
"""

import datetime
from dataclasses import dataclass, field

SLEEP_THRESHOLD = 5.0
WAKE_THRESHOLD = 15.0
NEIGHBOUR_CONGESTION = 80.0
SUSTAIN = datetime.timedelta(minutes=60)
RECENTLY_UNLOCKED = datetime.timedelta(minutes=30)

SERVING, PRE_SLEEP, SLEEP = "SERVING", "PRE_SLEEP", "SLEEP"
LOCK, UNLOCK, NO_CHANGE = "LOCK", "UNLOCK", "NO_CHANGE"


@dataclass
class CellInput:
    cell: str                                   # "<managedElementRef>/<cellId>"
    series: list[tuple[datetime.datetime, float]]
    state: str = SERVING
    prediction: dict | None = None              # {futurePrb, recommendedState, confidence}
    mdaf_future_prb: float | None = None
    guards: dict = field(default_factory=dict)  # RAN NF OAM cell guards: cellClass, sectorGroup, incidentZone, neighbourRefs
    sector_peers_awake: int | None = None       # other cells of the sector group not asleep; None = no sector group
    neighbour_prb: dict[str, float] = field(default_factory=dict)
    critical_alarm: bool = False
    coverage_alarm: bool = False
    last_unlocked_at: datetime.datetime | None = None
    override: bool = False


@dataclass
class Decision:
    decision: str
    reason: str
    next_state: str
    safety: dict
    observed_at: datetime.datetime | None
    prb: float | None
    low_for_minutes: float


def low_run_minutes(series) -> float:
    """How long PRB has been continuously below the sleep threshold, ending
    at the latest sample."""
    if not series or series[-1][1] >= SLEEP_THRESHOLD:
        return 0.0
    start = series[-1][0]
    for t, v in reversed(series):
        if v >= SLEEP_THRESHOLD:
            break
        start = t
    return (series[-1][0] - start).total_seconds() / 60


def evaluate_guards(c: CellInput, now: datetime.datetime) -> dict:
    blocks = []
    cell_class = c.guards.get("cellClass", "NORMAL")
    if cell_class == "EMERGENCY":
        blocks.append({"guard": "EMERGENCY_CELL", "level": "HARD"})
    if cell_class == "COVERAGE_CRITICAL":
        blocks.append({"guard": "COVERAGE_CRITICAL_CELL", "level": "HARD"})
    if c.guards.get("sectorGroup") and not c.sector_peers_awake:
        blocks.append({"guard": "LAST_SECTOR", "level": "HARD", "detail": f"sector group {c.guards['sectorGroup']}"})
    if c.guards.get("incidentZone"):
        blocks.append({"guard": "INCIDENT_ZONE", "level": "HARD", "detail": c.guards["incidentZone"]})
    congested = {n: v for n, v in c.neighbour_prb.items() if v > NEIGHBOUR_CONGESTION}
    if congested:
        blocks.append({"guard": "NEIGHBOUR_CONGESTION", "level": "MEDIUM", "detail": congested})
    if c.critical_alarm:
        blocks.append({"guard": "ACTIVE_CRITICAL_ALARM", "level": "MEDIUM"})
    if c.last_unlocked_at and now - c.last_unlocked_at < RECENTLY_UNLOCKED:
        blocks.append({"guard": "RECENTLY_UNLOCKED", "level": "SOFT",
                       "detail": f"unlocked at {c.last_unlocked_at.isoformat()}"})
    return {"passed": not blocks, "blocks": blocks}


def decide(c: CellInput) -> Decision:
    if not c.series:
        return Decision(NO_CHANGE, "NO_DATA", c.state, {"passed": False, "blocks": []}, None, None, 0.0)
    now, prb = c.series[-1]
    low_for = low_run_minutes(c.series)
    future = (c.prediction or {}).get("futurePrb")
    predicted_high = [p for p in (future, c.mdaf_future_prb) if p is not None and p > WAKE_THRESHOLD]
    congested = {n: v for n, v in c.neighbour_prb.items() if v > NEIGHBOUR_CONGESTION}

    def out(decision, reason, next_state, safety=None):
        return Decision(decision, reason, next_state, safety or {"passed": True, "blocks": []}, now, prb, low_for)

    if c.state == SLEEP:
        if c.override:
            return out(UNLOCK, "OPERATOR_OVERRIDE", SERVING)
        if c.coverage_alarm:
            return out(UNLOCK, "COVERAGE_ALARM", SERVING)
        if congested:
            return out(UNLOCK, "NEIGHBOUR_CONGESTION", SERVING)
        if predicted_high:
            return out(UNLOCK, "PREDICTED_LOAD", SERVING)
        return out(NO_CHANGE, "HYSTERESIS_ZONE" if prb >= SLEEP_THRESHOLD else "SLEEPING", SLEEP)

    if c.override:
        return out(NO_CHANGE, "OPERATOR_OVERRIDE", SERVING)
    if prb >= SLEEP_THRESHOLD:
        return out(NO_CHANGE, "HYSTERESIS_ZONE" if prb <= WAKE_THRESHOLD else "SERVING_LOAD", SERVING)
    safety = evaluate_guards(c, now)
    if not safety["passed"]:
        return out(NO_CHANGE, "SAFETY_BLOCKED:" + ",".join(b["guard"] for b in safety["blocks"]), SERVING, safety)
    if (c.prediction or {}).get("recommendedState") != "LOCKED" or predicted_high \
            or (c.mdaf_future_prb is not None and c.mdaf_future_prb >= SLEEP_THRESHOLD):
        return out(NO_CHANGE, "PREDICTED_NOT_LOW", SERVING, safety)
    if low_for < SUSTAIN.total_seconds() / 60:
        return out(NO_CHANGE, "SUSTAINING_LOW_LOAD", PRE_SLEEP, safety)
    return out(LOCK, "PREDICTED_LOW_UTILIZATION", SLEEP, safety)
