"""TRAINING mode (MLTF). It fits EnergyModel on historical
PRB_UTILIZATION (W10-07): first the hour-of-day profile, then the
persistence-anchored regression of the next-hour change. It returns the
trained model and the metrics AIMgF records on the training job."""

from .EnergyModel import EnergyModel, rmse, solve_least_squares
from .series import by_cell, hourly_pairs, hourly_profile


def train(records: list[dict], version: str = "1.0.0") -> tuple[EnergyModel, dict]:
    cells = by_cell(records)
    rows = [row for series in cells.values() for row in hourly_pairs(series)]
    if len(rows) < 3:
        raise ValueError(f"not enough hourly PRB history to train on ({len(rows)} rows)")
    model = EnergyModel(profile=hourly_profile(cells), trained_samples=len(rows), version=version)
    features = [[1.0, now - before, model.seasonal(hour)] for hour, now, before, _ in rows]
    model.weights = [round(w, 6) for w in solve_least_squares(features, [after - now for _, now, _, after in rows])]
    errors = [model.predict(now, before, hour) - after for hour, now, before, after in rows]
    model.rmse = round(rmse(errors), 4)
    metrics = {"algorithm": "THRESHOLD_LINEAR_REGRESSION", "trainingRows": len(rows), "rmse": model.rmse,
               "mae": round(sum(abs(e) for e in errors) / len(errors), 4),
               "weights": {"drift": model.weights[0], "trendGain": model.weights[1], "seasonalGain": model.weights[2]},
               "confidence": model.confidence()}
    return model, metrics
