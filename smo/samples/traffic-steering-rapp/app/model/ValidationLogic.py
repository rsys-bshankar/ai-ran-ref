"""VALIDATION mode (MLVF). Scores the forecast on held-out history (the last
quarter of each cell's windows, unsteered hours only): the share of windows
whose CONGESTED / HOLD / NORMAL band it predicts right, and the RMSE in
score points. Both must pass."""

from .SteeringModel import SteeringModel, rmse
from .TrainingLogic import transitions
from .series import by_cell

PASS_THRESHOLD = 0.85
MAX_RMSE = 3.0


def validate(model: SteeringModel, records: list[dict], layers: dict[str, str],
             holdout_fraction: float = 0.25) -> tuple[bool, dict]:
    held = []
    for series in by_cell(records).values():
        cut = int(len(series) * (1 - holdout_fraction))
        held.extend({"payload": p} for _, p in series[max(0, cut - 1):])
    rows = [r for r in transitions(held, layers) if not r["xc"] and not r["xi"]]
    if not rows:
        return False, {"score": 0.0, "reason": "no held-out rows"}
    preds = [model.forecast(r["score"], r["score"] - r["trend"], r["hour"]) for r in rows]
    hits = sum(model.band(p) == model.band(r["next"]) for p, r in zip(preds, rows))
    score = round(hits / len(rows), 4)
    error = round(rmse([p - r["next"] for p, r in zip(preds, rows)]), 4)
    return score >= PASS_THRESHOLD and error <= MAX_RMSE, {
        "score": score, "threshold": PASS_THRESHOLD, "heldOutRows": len(rows), "rmse": error, "maxRmse": MAX_RMSE}
