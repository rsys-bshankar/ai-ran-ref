"""VALIDATION mode (MLVF). It scores the trained model on held-out
history (W10-07, TC04). Two scores are computed:
  * the share of hours whose LOCK / NO_CHANGE / UNLOCK class the model gets
    right, against what actually happened an hour later;
  * the RMSE of the PRB prediction, which must stay within half of the
    10-point hysteresis band.
Both must pass."""

from .EnergyModel import EnergyModel, rmse
from .series import by_cell, hourly_pairs

PASS_THRESHOLD = 0.8
MAX_RMSE = 5.0


def validate(model: EnergyModel, records: list[dict], holdout_fraction: float = 0.25) -> tuple[bool, dict]:
    rows = []
    for series in by_cell(records).values():
        cut = int(len(series) * (1 - holdout_fraction))
        rows.extend(hourly_pairs(series[max(0, cut - 1):]))  # the latest quarter of each cell's history
    if not rows:
        return False, {"score": 0.0, "reason": "no held-out rows"}
    # the class the model chose vs the class the next hour actually called for
    hits = sum(model.recommend(now, model.predict(now, before, hour)) == model.recommend(now, after)
               for hour, now, before, after in rows)
    score = round(hits / len(rows), 4)
    errors = [model.predict(now, before, hour) - after for hour, now, before, after in rows]
    error = round(rmse(errors), 4)
    return score >= PASS_THRESHOLD and error <= MAX_RMSE, {
        "score": score, "threshold": PASS_THRESHOLD, "heldOutRows": len(rows), "rmse": error, "maxRmse": MAX_RMSE}
