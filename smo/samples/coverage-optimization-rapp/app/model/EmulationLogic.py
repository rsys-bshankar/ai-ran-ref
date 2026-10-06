"""EMULATION mode (MLEF). The joint optimiser is replayed against the
Digital Twin's COVERAGE_PERFORMANCE_SIM clusters, each with one known
injected fault (`scenario` on a `faultCell`).

A window counts as correct when:
  * WEAK_COVERAGE: the fault cell's reach goes up (POWER_UP or UPTILT);
  * OVERSHOOT: the fault cell's reach goes down. Its polluted neighbours
    are not the ones moved instead;
  * PILOT_POLLUTION: a neighbour of the fault cell has its reach reduced,
    and the fault cell itself is not reached further.

Pass criteria: at least 90 % of faulty windows correct, and no move at all
on a healthy cluster.
"""

from collections import defaultdict

from .CoverageModel import MOVES, CoverageModel
from .series import by_cell, snapshot

MIN_REPORTS = 100
PASS_RATE = 0.9
UP, DOWN = {"POWER_UP", "UPTILT"}, {"POWER_DOWN", "DOWNTILT"}


def _correct(scenario: str, fault: str, plan: dict[str, str], neighbours: list[str]) -> bool:
    if scenario == "WEAK_COVERAGE":
        return plan.get(fault) in UP
    if scenario == "OVERSHOOT":
        return plan.get(fault) in DOWN
    if scenario == "PILOT_POLLUTION":
        return any(plan.get(n) in DOWN for n in neighbours) and plan.get(fault) not in UP
    return not plan


def emulate(model: CoverageModel, records: list[dict]) -> tuple[bool, dict]:
    clusters: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        p = r.get("payload", r)
        clusters[p.get("cluster", "default")].append(r)
    faulty = correct = healthy = false_actions = 0
    gain = 0.0
    every_move = {m for m in MOVES if m != "NONE"}
    for _cluster, recs in sorted(clusters.items()):
        cells = by_cell(recs)
        first = recs[0].get("payload", recs[0])
        scenario, fault = first.get("scenario", "HEALTHY"), first.get("faultCell")
        for when in sorted({t for series in cells.values() for t, _ in series}):
            state = snapshot(cells, when=when)
            if not state or any(s["total"] < MIN_REPORTS for s in state.values()):
                continue
            out = model.optimise(state, {c: sorted(every_move) for c in state})
            if scenario == "HEALTHY":
                healthy += 1
                false_actions += bool(out["plan"])
            else:
                faulty += 1
                ok = _correct(scenario, fault, out["plan"], list(state.get(fault, {}).get("overlaps", {})))
                correct += ok
                gain += out["gain"] if ok else 0.0
    accuracy = round(correct / faulty, 4) if faulty else 0.0
    metrics = {"faultyWindows": faulty, "correctMoves": correct, "moveAccuracy": accuracy, "healthyWindows": healthy,
               "falseActions": false_actions, "expectedObjectiveReduction": round(gain, 2)}
    return faulty > 0 and accuracy >= PASS_RATE and false_actions == 0, metrics

