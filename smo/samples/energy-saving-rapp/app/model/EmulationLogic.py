"""EMULATION mode (MLEF) — replays the model against the Digital Twin's
synthetic PRB_UTILIZATION_SIM trend (decision D-7; W10-05, TC05). It
reports two things:

  * how many cell-hours the model would put to sleep (the energy saving);
  * how often a sleeping cell would have met real load in the next hour
    (the coverage impact).

Midnight hours below 5 % must come out LOCKED.
"""

import datetime

from .EnergyModel import EnergyModel
from .series import by_cell, value_at

CELL_POWER_KW = 0.8          # radio power saved per sleeping cell-hour (sample figure)
MAX_COVERAGE_IMPACT = 0.05   # at most 5 % of sleep recommendations may meet load above the wake threshold
HOUR = datetime.timedelta(hours=1)


def emulate(model: EnergyModel, records: list[dict]) -> tuple[bool, dict]:
    """Replays the model over every sample of the Digital Twin trend and returns (passed, metrics).

    Counts the cell-hours the model would put to sleep, how many of those met a load above the wake threshold in the next hour (the coverage
    impact), and whether every night-time (00:00 to 04:00) sample below the sleep threshold is recommended LOCKED. Passes when the night hours
    are all LOCKED and the coverage impact is at most MAX_COVERAGE_IMPACT.
    """
    sleeps = misses = midnight_low = midnight_locked = 0
    for series in by_cell(records).values():
        for t, now in series:
            recommended = model.recommend(now, model.predict(now, value_at(series, t - HOUR), t.hour))
            if recommended == "LOCKED":
                sleeps += 1
                after = value_at(series, t + HOUR)
                if after is not None and after > model.wake_threshold:
                    misses += 1
            if 0 <= t.hour < 4 and now < model.sleep_threshold:
                midnight_low += 1
                midnight_locked += recommended == "LOCKED"
    coverage_impact = round(misses / sleeps, 4) if sleeps else 0.0
    midnight_ok = midnight_low > 0 and midnight_locked == midnight_low
    metrics = {"sleepCellHours": sleeps, "energySavingsKwh": round(sleeps * CELL_POWER_KW, 2),
               "coverageImpact": coverage_impact, "midnightLowHours": midnight_low,
               "midnightRecommendation": "LOCKED" if midnight_ok else "MIXED"}
    return midnight_ok and coverage_impact <= MAX_COVERAGE_IMPACT, metrics
