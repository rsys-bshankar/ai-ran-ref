"""INFERENCE mode (MLIF). One prediction per relation from its latest PM
window and the window an hour earlier."""

import datetime

from .MobilityModel import MobilityModel
from .series import Series, attempts, counters, dominant_cause, mro_rate, value_at


def infer(model: MobilityModel, relation: str, series: Series) -> dict | None:
    """One prediction for a relation from its latest window and the window an hour earlier; None when the series is empty."""
    if not series:
        return None
    t, p = series[-1]
    c = counters(p)
    before = value_at(series, t - datetime.timedelta(hours=1))
    return {"relation": relation, "observedAt": t.isoformat(), "attempts": attempts(c), "rate": mro_rate(c),
            **model.infer(mro_rate(c), mro_rate(counters(before)) if before else None, dominant_cause(c))}
