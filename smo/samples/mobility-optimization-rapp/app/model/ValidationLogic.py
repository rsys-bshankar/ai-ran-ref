"""VALIDATION mode (MLVF). It scores the model on held-out handover
history: the share of windows whose ACT / HOLD / HEALTHY class it predicts
right, plus the prediction RMSE (in percentage points). Both must pass."""

from .MobilityModel import MobilityModel, rmse
from .series import by_relation, hourly_pairs

PASS_THRESHOLD = 0.8
MAX_RMSE = 1.5


def _band(model: MobilityModel, rate: float) -> str:
    return "ACT" if rate >= model.act_threshold else "HOLD" if rate >= model.healthy_threshold else "HEALTHY"


def validate(model: MobilityModel, records: list[dict], holdout_fraction: float = 0.25) -> tuple[bool, dict]:
    """Scores the model on the latest `holdout_fraction` of each relation's history and returns (passed, metrics).

    Passes when the share of windows whose predicted ACT / HOLD / HEALTHY band matches the band of the next hour is at least PASS_THRESHOLD and
    the RMSE is at most MAX_RMSE. Returns (False, {"reason": ...}) when no window has both neighbours.
    """
    rows = []
    for series in by_relation(records).values():
        cut = int(len(series) * (1 - holdout_fraction))
        rows.extend(hourly_pairs(series[max(0, cut - 1):]))
    if not rows:
        return False, {"score": 0.0, "reason": "no held-out rows"}
    hits = sum(_band(model, model.predict(now, before)) == _band(model, after) for now, before, after in rows)
    score = round(hits / len(rows), 4)
    error = round(rmse([model.predict(now, before) - after for now, before, after in rows]), 4)
    return score >= PASS_THRESHOLD and error <= MAX_RMSE, {
        "score": score, "threshold": PASS_THRESHOLD, "heldOutRows": len(rows), "rmse": error, "maxRmse": MAX_RMSE}
