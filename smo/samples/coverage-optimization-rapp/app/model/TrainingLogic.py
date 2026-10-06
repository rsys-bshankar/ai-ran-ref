"""TRAINING mode (MLTF). Learns the 12 sensitivities from historical
COVERAGE_PERFORMANCE windows in which tilt and power varied (W10.3-02):
for every cell and pair of consecutive hourly windows, the change of each
problem share is regressed on the cell's own reach step and its
neighbours' overlap-weighted reach steps."""

import datetime

from .CoverageModel import OVERLAP_SCALE, PROBLEM_KEYS, CoverageModel, rmse, solve_least_squares
from .series import Series, at, by_cell, config, counters, overlaps, shares

HOUR = datetime.timedelta(hours=1)


def _reach_step(series: Series, t: datetime.datetime) -> tuple[float, float] | None:
    before, after = at(series, t), at(series, t + HOUR)
    if before is None or after is None:
        return None
    (t0, p0), (t1, p1) = config(counters(before)), config(counters(after))
    if None in (t0, p0, t1, p1):
        return None
    return -(t1 - t0) / 10.0, p1 - p0


def transitions(records: list[dict]) -> list[tuple[list[float], dict[str, float]]]:
    """[(features [t, p, Σc·t_j, Σc·p_j], {problem: Δshare})] for every cell
    with a window an hour later."""
    cells = by_cell(records)
    rows = []
    for _cell, series in cells.items():
        for t, p in series:
            own = _reach_step(series, t)
            after = at(series, t + HOUR)
            if own is None or after is None:
                continue
            nt = np_ = 0.0
            for nbr, ov in overlaps(counters(p)).items():
                step = _reach_step(cells.get(nbr, []), t)
                if step:
                    nt += ov / OVERLAP_SCALE * step[0]
                    np_ += ov / OVERLAP_SCALE * step[1]
            s0, s1 = shares(counters(p)), shares(counters(after))
            rows.append(([own[0], own[1], nt, np_], {k: s1[k] - s0[k] for k in PROBLEM_KEYS}))
    return rows


def fit(rows) -> tuple[dict, float]:
    sens = {k: [round(w, 4) for w in solve_least_squares([f for f, _ in rows], [d[k] for _, d in rows])]
            for k in PROBLEM_KEYS}
    errors = [sum(w * x for w, x in zip(sens[k], f)) - d[k] for f, d in rows for k in PROBLEM_KEYS]
    return sens, round(rmse(errors), 4)


def train(records: list[dict], version: str = "1.0.0") -> tuple[CoverageModel, dict]:
    rows = transitions(records)
    moved = [r for r in rows if any(r[0])]
    if len(moved) < 8:
        raise ValueError(f"not enough history with tilt/power changes to train on ({len(moved)} moved windows)")
    sens, error = fit(rows)
    model = CoverageModel(sensitivities=sens, rmse=error, trained_samples=len(rows), version=version)
    return model, {"algorithm": "JOINT_LINEAR_SENSITIVITY", "trainingRows": len(rows), "rowsWithChanges": len(moved),
                   "rmse": model.rmse, "sensitivities": sens, "confidence": model.confidence()}
