"""TRAINING mode (MLTF). From historical LOAD_PERFORMANCE windows in which
the biases were stepped (W10.4-02), it learns:
  * the hour-of-day profile: the mean change of the score from each hour to
    the next, over windows with no bias change;
  * the forecast weights and the transfer per CIO dB and per priority step.
    These come from one regression of the next-hour score change on drift,
    trend, profile, and the source's outgoing / incoming bias changes
    weighted by the source's score."""

import datetime
from collections import defaultdict

from .SteeringModel import SteeringModel, rmse, solve_least_squares
from .series import Series, at, biases, by_cell, counters, neighbours, score

HOUR = datetime.timedelta(hours=1)


def _deltas(series: Series, t: datetime.datetime) -> tuple[dict, dict] | None:
    before, after = at(series, t), at(series, t + HOUR)
    if before is None or after is None:
        return None
    (c0, p0), (c1, p1) = biases(counters(before)), biases(counters(after))
    return ({k: c1.get(k, 0.0) - v for k, v in c0.items()}, {k: p1.get(k, 0.0) - v for k, v in p0.items()})


def transitions(records: list[dict], layers: dict[str, str]) -> list[dict]:
    """One row per cell and pair of consecutive hourly windows."""
    cells = by_cell(records)
    rows = []
    for cell, series in cells.items():
        for t, p in series:
            after = at(series, t + HOUR)
            own = _deltas(series, t)
            if after is None or own is None:
                continue
            s0, s1 = score(counters(p)), score(counters(after))
            prev = at(series, t - HOUR)
            d_cio, d_prio = own
            incoming_c = incoming_i = 0.0
            for j, j_series in cells.items():
                jp = at(j_series, t)
                jd = _deltas(j_series, t)
                if j == cell or jp is None or jd is None or cell not in neighbours(counters(jp)):
                    continue
                sj = score(counters(jp))
                incoming_c += sj * jd[0].get(cell, 0.0)
                layer = layers.get(cell)
                if layer and layer in jd[1]:
                    share = sum(1 for n in neighbours(counters(jp)) if layers.get(n) == layer) or 1
                    incoming_i += sj * jd[1][layer] / share
            rows.append({"hour": t.hour, "score": s0, "next": s1,
                         "trend": s0 - score(counters(prev)) if prev is not None else 0.0,
                         "xc": -(s0 * sum(d_cio.values()) - incoming_c), "xi": -(s0 * sum(d_prio.values()) - incoming_i)})
    return rows


def profile(rows: list[dict]) -> list[float]:
    by_hour = defaultdict(list)
    for r in rows:
        if r["xc"] == 0 and r["xi"] == 0:
            by_hour[r["hour"]].append(r["next"] - r["score"])
    return [round(sum(v) / len(v), 4) if (v := by_hour.get(h)) else 0.0 for h in range(24)]


def train(records: list[dict], layers: dict[str, str], version: str = "1.0.0") -> tuple[SteeringModel, dict]:
    rows = transitions(records, layers)
    steered = [r for r in rows if r["xc"] or r["xi"]]
    if len(rows) < 24 or len(steered) < 6:
        raise ValueError(f"not enough history with steering changes to train on ({len(rows)} rows, {len(steered)} steered)")
    prof = profile(rows)
    w = solve_least_squares([[1.0, r["trend"], prof[r["hour"]], r["xc"], r["xi"]] for r in rows],
                            [r["next"] - r["score"] for r in rows])
    model = SteeringModel(weights=[round(x, 5) for x in w[:3]], profile=prof,
                          transfer={"CONNECTED": round(w[3], 5), "IDLE": round(w[4], 5)}, trained_samples=len(rows),
                          version=version)
    errors = [r["score"] + w[0] + w[1] * r["trend"] + w[2] * prof[r["hour"]] + w[3] * r["xc"] + w[4] * r["xi"] - r["next"]
              for r in rows]
    model.rmse = round(rmse(errors), 4)
    return model, {"algorithm": "CONGESTION_SCORE_REGRESSION_PAIRWISE", "trainingRows": len(rows),
                   "rowsWithSteering": len(steered), "rmse": model.rmse,
                   "weights": {"drift": model.weights[0], "trendGain": model.weights[1], "profileGain": model.weights[2]},
                   "transfer": model.transfer, "confidence": model.confidence()}
