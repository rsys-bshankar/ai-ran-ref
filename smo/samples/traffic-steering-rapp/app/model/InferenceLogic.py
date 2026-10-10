"""INFERENCE mode (MLIF). Each cell's congestion score and next-hour forecast
from its latest PM window and the window an hour earlier."""

import datetime

from .SteeringModel import SteeringModel
from .series import Series, at, counters, score


def infer(model: SteeringModel, cell: str, series: Series) -> dict | None:
    """One cell's congestion score, next-hour forecast, band and the model's confidence from its latest window and the one an hour earlier; None
    when the series is empty.
    """
    if not series:
        return None
    t, p = series[-1]
    s = score(counters(p))
    before = at(series, t - datetime.timedelta(hours=1))
    f = model.forecast(s, score(counters(before)) if before is not None else None, t.hour)
    return {"cellId": cell, "observedAt": t.isoformat(), "score": s, "forecast": f, "band": model.band(f),
            "confidence": model.confidence()}
