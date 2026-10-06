"""VALIDATION mode (MLVF). Scores the sensitivities on held-out history
(the last quarter of each cell's windows): the RMSE of the predicted share
changes, and how often the predicted direction is right where the share
moved by at least a point. Both must pass."""

import datetime
from typing import cast

from .CoverageModel import PROBLEM_KEYS, CoverageModel, rmse
from .TrainingLogic import transitions
from .series import by_cell

MAX_RMSE = 1.0
MIN_DIRECTION = 0.85


def validate(model: CoverageModel, records: list[dict], holdout_fraction: float = 0.25) -> tuple[bool, dict]:
    cut: dict[str, datetime.datetime | None] = {}
    for cell, series in by_cell(records).items():
        cut[cell] = series[int(len(series) * (1 - holdout_fraction))][0] if series else None
    held: list = []
    for cell, series in by_cell(records).items():
        held.extend(r for r in series if cut[cell] is not None and r[0] >= cast(datetime.datetime, cut[cell]))      # not None, checked just before
    rows = transitions([{"payload": p} for _, p in held])
    if not rows:
        return False, {"reason": "no held-out rows"}
    errors, hits, moved = [], 0, 0
    for f, d in rows:
        for k in PROBLEM_KEYS:
            pred = sum(w * x for w, x in zip(model.sensitivities[k], f))
            errors.append(pred - d[k])
            if abs(d[k]) >= 1.0:
                moved += 1
                hits += (pred > 0) == (d[k] > 0)
    error = round(rmse(errors), 4)
    direction = round(hits / moved, 4) if moved else 1.0
    return error <= MAX_RMSE and direction >= MIN_DIRECTION, {
        "heldOutRows": len(rows), "rmse": error, "maxRmse": MAX_RMSE, "directionAccuracy": direction,
        "minDirectionAccuracy": MIN_DIRECTION, "movedShares": moved}
