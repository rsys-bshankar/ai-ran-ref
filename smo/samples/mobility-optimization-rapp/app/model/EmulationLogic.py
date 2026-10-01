"""EMULATION mode (MLEF). The model is replayed against the Digital Twin's
HO_PERFORMANCE_SIM relations, each with a known injected fault (`scenario`:
TOO_LATE / TOO_EARLY / PING_PONG / WRONG_CELL / HEALTHY).

Pass criteria:
  * the controller acts in the injected fault's direction on at least 90 %
    of faulty windows;
  * it never acts on a healthy relation.
"""

import datetime

from .MobilityModel import STEP_DB, MobilityModel
from .series import attempts, by_relation, counters, dominant_cause, mro_rate, value_at

MIN_ATTEMPTS = 50
HOUR = datetime.timedelta(hours=1)
PASS_RATE = 0.9


def emulate(model: MobilityModel, records: list[dict]) -> tuple[bool, dict]:
    faulty = correct = healthy = false_actions = 0
    reduction = 0.0
    for series in by_relation(records).values():
        for t, p in series:
            c = counters(p)
            if attempts(c) < MIN_ATTEMPTS:
                continue
            before = value_at(series, t - HOUR)
            out = model.infer(mro_rate(c), mro_rate(counters(before)) if before else None, dominant_cause(c))
            acted = out["recommendation"] in ("RAISE_CIO", "LOWER_CIO")
            scenario = p.get("scenario", "HEALTHY")
            if scenario == "HEALTHY":
                healthy += 1
                false_actions += acted
            else:
                faulty += 1
                if acted and (out["recommendation"] == "RAISE_CIO") == (STEP_DB[scenario] > 0):
                    correct += 1
                    reduction += max(0.0, mro_rate(c) - model.healthy_threshold)
    accuracy = round(correct / faulty, 4) if faulty else 0.0
    metrics = {"faultyWindows": faulty, "correctDirection": correct, "directionAccuracy": accuracy,
               "healthyWindows": healthy, "falseActions": false_actions,
               "expectedFailureReductionPp": round(reduction, 2)}
    return faulty > 0 and accuracy >= PASS_RATE and false_actions == 0, metrics
