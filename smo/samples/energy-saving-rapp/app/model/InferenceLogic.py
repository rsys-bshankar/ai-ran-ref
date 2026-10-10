"""INFERENCE mode (MLIF) — one prediction per cell from its live PRB series
(W10-09, TC08): {cellId, prbUtilization, futurePrb, recommendedState,
confidence, observedAt}."""

import datetime

from .EnergyModel import EnergyModel
from .series import Series, value_at


def infer(model: EnergyModel, cell: str, series: Series) -> dict | None:
    """One prediction for a cell from its live series, using the sample an hour before the latest as the trend baseline; None when the series is
    empty.
    """
    if not series:
        return None
    now_t, now = series[-1]
    before = value_at(series, now_t - datetime.timedelta(minutes=model.horizon_minutes))
    return {"cellId": cell, "prbUtilization": now, "observedAt": now_t.isoformat(), **model.infer(now, before, now_t.hour)}
