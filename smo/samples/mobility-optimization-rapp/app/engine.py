"""The Mobility Optimization rApp's decision engine (Wave 10.2, W10.2-05/07,
decisions D10.2-2 / D10.2-4). Pure functions, no I/O.

Per neighbour relation, in order:

  1. KPI-verified revert. A relation that was changed is OBSERVING until
     60 minutes of post-change PM exist. If its mobility problem rate is
     then worse than before the change (by more than 0.5 points), the change
     is REVERTED to the previous CIO; otherwise it is CONFIRMED.
  2. Guards. Any one of these blocks a change, whatever the model's
     confidence:
       - handover over the relation is not allowed (isHOAllowed=false);
       - source or target is an EMERGENCY cell or in an incident zone;
       - the target cell is asleep (O1 LOCKED / energy saving) or the
         EnergySaving rApp has it in SLEEP or PRE_SLEEP;
       - the target woke less than 30 minutes ago;
       - fewer than 50 handover attempts in the window;
       - the relation changed less than 60 minutes ago (pacing).
  3. Prediction. Act only when the predicted next-hour rate is at least
     5 %; 2–5 % is a hold zone.
  4. Bounded step. The dominant failure class sets the direction:
     TOO_LATE +2 dB; TOO_EARLY and PING_PONG −2 dB; WRONG_CELL −1 dB.
     The result is clamped to baseline ± 6 dB.
"""

import datetime
from dataclasses import dataclass, field

from .model.MobilityModel import STEP_DB
from .model.series import Series, attempts, counters, merged, mro_rate

MAX_DEVIATION_DB = 6
PACING = datetime.timedelta(minutes=60)
OBSERVATION = datetime.timedelta(minutes=60)
AFTER_WAKE = datetime.timedelta(minutes=30)
MIN_ATTEMPTS = 50
DEGRADATION_PP = 0.5

STEADY, OBSERVING = "STEADY", "OBSERVING"
RAISE, LOWER, REVERT, NO_CHANGE = "RAISE_CIO", "LOWER_CIO", "REVERT_CIO", "NO_CHANGE"


@dataclass
class RelationInput:
    relation: str
    source: str
    target: str
    series: Series
    current_cio: int
    baseline_cio: int = 0
    state: str = STEADY
    last_change: dict | None = None        # {at, from, to, preRate}
    last_changed_at: datetime.datetime | None = None
    prediction: dict | None = None         # {futureRate, cause, recommendation}
    ho_allowed: bool = True
    source_guard: dict = field(default_factory=dict)
    target_guard: dict = field(default_factory=dict)
    target_o1_asleep: bool = False
    target_es_state: str | None = None     # the EnergySaving rApp's SERVING / PRE_SLEEP / SLEEP
    target_last_woken: datetime.datetime | None = None


@dataclass
class Decision:
    decision: str
    reason: str
    new_cio: int | None
    next_state: str
    safety: dict
    kpi: dict | None
    observed_at: datetime.datetime | None
    rate: float | None
    attempts: float | None


def _protected(guard: dict) -> bool:
    return guard.get("cellClass") == "EMERGENCY" or bool(guard.get("incidentZone"))


def evaluate_guards(r: RelationInput, now: datetime.datetime, window: dict) -> dict:
    blocks = []
    if not r.ho_allowed:
        blocks.append({"guard": "HO_NOT_ALLOWED", "level": "HARD"})
    for side, g in (("source", r.source_guard), ("target", r.target_guard)):
        if _protected(g):
            blocks.append({"guard": "PROTECTED_CELL", "level": "HARD", "detail": f"{side} cell is "
                           + ("EMERGENCY" if g.get("cellClass") == "EMERGENCY" else f"in incident zone {g['incidentZone']}")})
    if r.target_o1_asleep or r.target_es_state in ("SLEEP", "PRE_SLEEP"):
        blocks.append({"guard": "TARGET_ASLEEP", "level": "HARD",
                       "detail": r.target_es_state or "O1 locked / energy saving"})
    if r.target_last_woken and now - r.target_last_woken < AFTER_WAKE:
        blocks.append({"guard": "TARGET_RECENTLY_WOKEN", "level": "MEDIUM", "detail": r.target_last_woken.isoformat()})
    if attempts(window) < MIN_ATTEMPTS:
        blocks.append({"guard": "INSUFFICIENT_SAMPLES", "level": "SOFT", "detail": f"{attempts(window):.0f} attempts"})
    if r.last_changed_at and now - r.last_changed_at < PACING:
        blocks.append({"guard": "PACING", "level": "SOFT", "detail": f"changed at {r.last_changed_at.isoformat()}"})
    return {"passed": not blocks, "blocks": blocks}


def decide(r: RelationInput) -> Decision:
    if not r.series:
        return Decision(NO_CHANGE, "NO_DATA", None, r.state, {"passed": False, "blocks": []}, None, None, None, None)
    now, payload = r.series[-1]
    window = counters(payload)
    rate = mro_rate(window)

    def out(decision, reason, new_cio=None, next_state=None, safety=None, kpi=None):
        return Decision(decision, reason, new_cio, next_state or r.state, safety or {"passed": True, "blocks": []}, kpi,
                        now, rate, attempts(window))

    # 1 — KPI-verified revert of the last change
    if r.state == OBSERVING and r.last_change:
        changed_at = r.last_change["at"]
        post = [w for w in r.series if w[0] > changed_at]
        if not post or post[-1][0] - changed_at < OBSERVATION:
            return out(NO_CHANGE, "OBSERVING")
        post_rate = mro_rate(merged(post))
        kpi = {"preRate": r.last_change["preRate"], "postRate": post_rate, "windows": len(post),
               "verdict": "DEGRADED" if post_rate > r.last_change["preRate"] + DEGRADATION_PP else "IMPROVED_OR_EQUAL"}
        if kpi["verdict"] == "DEGRADED":
            return out(REVERT, "KPI_DEGRADED", r.last_change["from"], STEADY, kpi=kpi)
        return out(NO_CHANGE, "CHANGE_CONFIRMED", next_state=STEADY, kpi=kpi)

    # 2 — guards
    safety = evaluate_guards(r, now, window)
    if not safety["passed"]:
        return out(NO_CHANGE, "SAFETY_BLOCKED:" + ",".join(b["guard"] for b in safety["blocks"]), safety=safety)

    # 3 — prediction
    p = r.prediction or {}
    if p.get("recommendation") not in ("RAISE_CIO", "LOWER_CIO"):
        return out(NO_CHANGE, "HEALTHY" if p.get("recommendation") == "HEALTHY" else "HOLD_ZONE", safety=safety)

    # 4 — bounded step in the dominant failure class's direction
    step = STEP_DB[p["cause"]]
    low, high = r.baseline_cio - MAX_DEVIATION_DB, r.baseline_cio + MAX_DEVIATION_DB
    new = max(low, min(high, r.current_cio + step))
    if new == r.current_cio:
        return out(NO_CHANGE, f"AT_BOUND:{p['cause']}", safety=safety)
    return out(RAISE if new > r.current_cio else LOWER, f"{p['cause']}_FAILURES", new, OBSERVING, safety)
