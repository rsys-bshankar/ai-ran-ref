"""TRAINING mode (MLTF). Fits the next-hour change of the mobility
problem rate on historical HO_PERFORMANCE windows (W10.2-02)."""

from .MobilityModel import MobilityModel, rmse, solve_least_squares
from .series import by_relation, hourly_pairs


def train(records: list[dict], version: str = "1.0.0") -> tuple[MobilityModel, dict]:
    rows = [row for series in by_relation(records).values() for row in hourly_pairs(series)]
    if len(rows) < 3:
        raise ValueError(f"not enough hourly handover history to train on ({len(rows)} rows)")
    model = MobilityModel(trained_samples=len(rows), version=version)
    model.weights = [round(w, 6) for w in solve_least_squares([[1.0, now - before] for now, before, _ in rows],
                                                             [after - now for now, _, after in rows])]
    errors = [model.predict(now, before) - after for now, before, after in rows]
    model.rmse = round(rmse(errors), 4)
    return model, {"algorithm": "MRO_CLASSIFICATION_LINEAR_REGRESSION", "trainingRows": len(rows), "rmse": model.rmse,
                   "weights": {"drift": model.weights[0], "trendGain": model.weights[1]},
                   "confidence": model.confidence()}
