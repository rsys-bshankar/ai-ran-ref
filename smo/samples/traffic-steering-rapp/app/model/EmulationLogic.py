"""EMULATION mode (MLEF). The planner is replayed against the Digital Twin's
LOAD_PERFORMANCE_SIM clusters, each either HEALTHY or with a known HOTSPOT
cell. Every move is open to it, except that idle steering needs a target on
another layer.

A window counts when the hotspot cell is forecast congested. It is correct
when the planner steers out of the hotspot cell and keeps every target at or
below its capacity limit.

Pass criteria: at least 90 % of counted windows correct, and no steering at
all in a healthy cluster.
"""

from collections import defaultdict

from .SteeringModel import SteeringModel
from .series import by_cell, counters, neighbours
from .InferenceLogic import infer

PASS_RATE = 0.9


def candidates_for(cell: str, nbrs: list[str], layers: dict[str, str]) -> list[dict]:
    """The steering moves open to a cell in emulation: a connected-mode move for every neighbour, and an idle-mode move for every layer other than
    its own that has a neighbour.
    """
    out: list[dict] = [{"knob": "CONNECTED", "target": t} for t in nbrs]
    for layer in sorted({layers[t] for t in nbrs if layers.get(t) and layers[t] != layers.get(cell)}):
        out.append({"knob": "IDLE", "layer": layer, "targets": [t for t in nbrs if layers.get(t) == layer]})
    return out


def emulate(model: SteeringModel, records: list[dict]) -> tuple[bool, dict]:
    """Replays the planner over every window of every Digital Twin cluster and returns (passed, metrics).

    Windows in which not every cell has a reading are skipped. A window with a forecast-congested hotspot counts and is correct when the hotspot
    steers and no target is predicted above its capacity limit; a healthy window the planner steers in is a false action. Passes when there is
    at least one counted window, the accuracy is at least PASS_RATE and there is no false action.
    """
    clusters: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        clusters[r.get("payload", r).get("cluster", "default")].append(r)
    counted = correct = healthy = false_actions = 0
    for _, recs in sorted(clusters.items()):
        first = recs[0].get("payload", recs[0])
        scenario, hot = first.get("scenario", "HEALTHY"), first.get("hotCell")
        layers = {r.get("payload", r)["cellId"]: r.get("payload", r).get("layer") for r in recs}
        cells = by_cell(recs)
        times = sorted({t for s in cells.values() for t, _ in s})
        for when in times[1:]:
            state, nbrs = {}, {}
            for cell, series in cells.items():
                upto = [w for w in series if w[0] <= when]
                out = infer(model, cell, upto)
                if out and upto[-1][0] == when:
                    state[cell] = out
                    nbrs[cell] = neighbours(counters(upto[-1][1]))
            if len(state) < len(cells):
                continue
            plan = model.plan(state, {c: candidates_for(c, nbrs[c], layers) for c in state}, {})
            steered = [c for c, d in plan.items() if d["decision"].startswith("STEER")]
            if scenario == "HEALTHY":
                healthy += 1
                false_actions += bool(steered)
            elif state[hot]["forecast"] >= model.act:
                counted += 1
                ok = hot in steered and all(d.get("targetForecastAfter", 0) <= model.target_max for d in plan.values())
                correct += ok
    accuracy = round(correct / counted, 4) if counted else 0.0
    metrics = {"congestedWindows": counted, "correctSteering": correct, "steeringAccuracy": accuracy,
               "healthyWindows": healthy, "falseActions": false_actions}
    return counted > 0 and accuracy >= PASS_RATE and false_actions == 0, metrics
