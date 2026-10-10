"""The Coverage Optimization rApp's decision engine (Wave 10.3,
W10.3-05/07, decisions D10.3-2 / D10.3-4). Pure functions, no I/O.

One pass over the cluster, in order:

  1. KPI-verified revert. While a change set is OBSERVING, nothing else
     changes. Once 60 minutes of post-change PM exist, the cluster objective
     (the sum of every problem share's excess over 5 %) is compared with its
     value before the change. If it is worse by more than 0.5, every cell in
     the set is REVERTED. Otherwise the set is CONFIRMED.
  2. Guards, per cell. Any one of these keeps a cell from moving, whatever
     the model's confidence:
       - an EMERGENCY or incident-zone cell, or an active critical alarm on
         the cell, on one of its neighbours, or on the managed element as a whole;
       - the cell is asleep (O1 locked / energy saving, or the EnergySaving
         rApp has it in SLEEP or PRE_SLEEP);
       - a neighbour is asleep;
       - the cell or a neighbour woke less than 30 minutes ago;
       - the Mobility rApp has one of the cell's relations OBSERVING;
       - fewer than 100 measurement reports in the window;
       - the cell changed less than 60 minutes ago (pacing).
     A guarded cell still counts in the objective: its neighbours' moves
     still affect it.
  3. Bounds. Tilt stays within baseline ± 4° and moves 1° at a time; power
     stays within baseline ± 3 dB and moves 1 dB at a time.
  4. Joint plan. The allowed moves go to the model's optimiser (at most 2
     cells per pass).
"""

import datetime
from dataclasses import dataclass, field

from .model.CoverageModel import MOVES, excess

TILT_STEP, MAX_TILT_DEVIATION = 10, 40      # 0.1° units: 1° steps, ± 4°
POWER_STEP, MAX_POWER_DEVIATION = 1, 3      # dB
PACING = datetime.timedelta(minutes=60)
OBSERVATION = datetime.timedelta(minutes=60)
AFTER_WAKE = datetime.timedelta(minutes=30)
MIN_REPORTS = 100
DEGRADATION = 0.5

STEADY, OBSERVING = "STEADY", "OBSERVING"
NO_CHANGE, REVERT = "NO_CHANGE", "REVERT"


@dataclass
class CellInput:
    """One cell's inputs to a pass: its PM total, live tilt and power, the baselines the bounds are measured from, its neighbours, and the facts
    the guards read (class and incident zone, critical alarm, sleep state, last wake, relations the Mobility rApp is observing).
    """
    cell: str
    total: float
    tilt: int
    power: int
    baseline_tilt: int
    baseline_power: int
    neighbours: list[str] = field(default_factory=list)
    last_changed_at: datetime.datetime | None = None
    guard: dict = field(default_factory=dict)
    critical_alarm: bool = False
    asleep: bool = False                               # O1 state or EnergySaving SLEEP / PRE_SLEEP
    asleep_neighbours: list[str] = field(default_factory=list)
    last_woken: datetime.datetime | None = None         # the latest wake of the cell or a neighbour
    mro_observing: list[str] = field(default_factory=list)  # the cell's relations the Mobility rApp is observing


def evaluate_guards(c: CellInput, now: datetime.datetime) -> dict:
    """Runs every guard on one cell and returns {"passed": bool, "blocks": [{guard, level, detail?}]}.

    Each guard that fires adds one block, so the audit trail lists every reason a cell did not move, not only the first. `now` is the time of
    the newest PM window, not the wall clock, so a replay of old data gives the same answer. No I/O.
    """
    blocks: list[dict] = []
    if c.guard.get("cellClass") == "EMERGENCY" or c.guard.get("incidentZone"):
        blocks.append({"guard": "PROTECTED_CELL", "level": "HARD",
                       "detail": "EMERGENCY" if c.guard.get("cellClass") == "EMERGENCY" else f"incident zone {c.guard['incidentZone']}"})
    if c.critical_alarm:
        blocks.append({"guard": "CRITICAL_ALARM", "level": "HARD"})
    if c.asleep:
        blocks.append({"guard": "CELL_ASLEEP", "level": "HARD"})
    if c.asleep_neighbours:
        blocks.append({"guard": "NEIGHBOUR_ASLEEP", "level": "HARD", "detail": c.asleep_neighbours})
    if c.last_woken and now - c.last_woken < AFTER_WAKE:
        blocks.append({"guard": "RECENTLY_WOKEN", "level": "MEDIUM", "detail": c.last_woken.isoformat()})
    if c.mro_observing:
        blocks.append({"guard": "MRO_OBSERVING", "level": "MEDIUM", "detail": c.mro_observing})
    if c.total < MIN_REPORTS:
        blocks.append({"guard": "INSUFFICIENT_SAMPLES", "level": "SOFT", "detail": f"{c.total:.0f} reports"})
    if c.last_changed_at and now - c.last_changed_at < PACING:
        blocks.append({"guard": "PACING", "level": "SOFT", "detail": f"changed at {c.last_changed_at.isoformat()}"})
    return {"passed": not blocks, "blocks": blocks}


def apply(move: str, tilt: int, power: int) -> tuple[int, int]:
    """Returns the (tilt, power) a cell would have after `move`: tilt in 0.1 degree units (1 degree per step), power in dB (1 dB per step)."""
    d_tilt, d_power = MOVES[move]
    return tilt + d_tilt * TILT_STEP, power + d_power * POWER_STEP


def allowed_moves(c: CellInput) -> list[str]:
    """The non-trivial moves that keep the cell inside its bounds."""
    out = []
    for move in ("DOWNTILT", "UPTILT", "POWER_UP", "POWER_DOWN"):
        tilt, power = apply(move, c.tilt, c.power)
        if abs(tilt - c.baseline_tilt) <= MAX_TILT_DEVIATION and abs(power - c.baseline_power) <= MAX_POWER_DEVIATION:
            out.append(move)
    return out


def kpi_check(observing: dict, state: dict, now: datetime.datetime) -> dict | None:
    """None while the change set is still being observed; else the verdict."""
    changed_at = observing["at"]
    if now <= changed_at or now - changed_at < OBSERVATION:
        return None
    post = round(sum(excess(s["shares"]) for s in state.values()), 3)
    verdict = "DEGRADED" if post > observing["preObjective"] + DEGRADATION else "IMPROVED_OR_EQUAL"
    return {"preObjective": observing["preObjective"], "postObjective": post,
            "predictedObjective": observing.get("predictedObjective"), "verdict": verdict}


@dataclass
class CellDecision:
    """The engine's verdict for one cell: the move (or NO_CHANGE), the reason code the audit trail shows, the settings before and after
    (`to_setting` is None when nothing changes), the guard evaluation, and the moves the guards and bounds left open.
    """
    cell: str
    decision: str
    reason: str
    from_setting: dict
    to_setting: dict | None
    safety: dict
    allowed: list[str]


def plan_cells(cells: list[CellInput], state: dict, inference: dict, now: datetime.datetime,
               safety: dict[str, dict]) -> dict[str, CellDecision]:
    """Per-cell decisions from the optimiser's joint plan."""
    plan = inference["plan"]
    out = {}
    for c in cells:
        frm = {"digitalTilt": c.tilt, "configuredMaxTxPower": c.power}
        allowed = allowed_moves(c) if safety[c.cell]["passed"] else []
        if c.cell in plan:
            tilt, power = apply(plan[c.cell], c.tilt, c.power)
            out[c.cell] = CellDecision(c.cell, plan[c.cell], inference["drivers"][c.cell], frm,
                                       {"digitalTilt": tilt, "configuredMaxTxPower": power}, safety[c.cell], allowed)
            continue
        if not safety[c.cell]["passed"]:
            reason = "SAFETY_BLOCKED:" + ",".join(b["guard"] for b in safety[c.cell]["blocks"])
        elif c.cell not in state or excess(state[c.cell]["shares"]) == 0:
            reason = "HEALTHY"
        elif plan and excess(inference["predicted"][c.cell]) < excess(state[c.cell]["shares"]):
            reason = "HELPED_BY:" + ",".join(sorted(plan))
        else:
            reason = "NO_BENEFICIAL_MOVE"
        out[c.cell] = CellDecision(c.cell, NO_CHANGE, reason, frm, None, safety[c.cell], allowed)
    return out
