"""Unit tests for the decision engine (W10-10..W10-15)."""

import datetime

import pytest

from app import engine
from app.engine import CellInput, decide

T0 = datetime.datetime(2026, 9, 2, 1, 0, tzinfo=datetime.UTC)
LOW = {"futurePrb": 2.0, "recommendedState": "LOCKED", "confidence": 0.9}


def _series(values, step=5, start=T0):
    return [(start + datetime.timedelta(minutes=i * step), v) for i, v in enumerate(values)]


def _low(minutes, value=2.0):
    return _series([value] * (minutes // 5 + 1))


def _cell(**kw):
    defaults = {"cell": "gnb/101", "series": _low(60), "prediction": LOW, "sector_peers_awake": None}
    return CellInput(**{**defaults, **kw})


def test_sustained_low_load_locks_and_shorter_is_pre_sleep():
    """An hour of low PRB locks, a shorter run only moves the cell to PRE_SLEEP, and one spike in the window restarts the count."""
    assert decide(_cell()).decision == engine.LOCK                               # TC08
    d = decide(_cell(series=_low(30)))
    assert (d.decision, d.next_state, d.reason) == (engine.NO_CHANGE, engine.PRE_SLEEP, "SUSTAINING_LOW_LOAD")
    assert decide(_cell(series=_series([2] * 6 + [6] + [2] * 6))).next_state == engine.PRE_SLEEP  # a spike resets the window


def test_hysteresis_zone_never_changes_state():                                  # TC23
    """PRB between 5 % and 15 % changes nothing for a serving or a sleeping cell."""
    for state in (engine.SERVING, engine.SLEEP):
        d = decide(_cell(series=_series([10.0] * 13), state=state,
                         prediction={"futurePrb": 10.0, "recommendedState": "NO_CHANGE"}))
        assert (d.decision, d.next_state) == (engine.NO_CHANGE, state)


# One row per wake condition and the reason it must report.
@pytest.mark.parametrize("kw,reason", [
    ({"prediction": {"futurePrb": 19.0, "recommendedState": "UNLOCKED"}}, "PREDICTED_LOAD"),   # TC22
    ({"mdaf_future_prb": 30.0}, "PREDICTED_LOAD"),
    ({"neighbour_prb": {"gnb/102": 85.0}}, "NEIGHBOUR_CONGESTION"),                           # TC19
    ({"coverage_alarm": True, "critical_alarm": True}, "COVERAGE_ALARM"),                      # TC20
    ({"override": True}, "OPERATOR_OVERRIDE"),                                                  # TC21
])
def test_wake_conditions_unlock_a_sleeping_cell(kw, reason):
    """Each wake condition unlocks a sleeping cell with its own reason; a coverage alarm outranks a critical alarm."""
    d = decide(_cell(state=engine.SLEEP, **kw))
    assert (d.decision, d.reason, d.next_state) == (engine.UNLOCK, reason, engine.SERVING)


# One row per guard: the input that trips it, its name and its level.
@pytest.mark.parametrize("kw,guard,level", [
    ({"guards": {"cellClass": "EMERGENCY"}}, "EMERGENCY_CELL", "HARD"),                       # TC26
    ({"guards": {"cellClass": "COVERAGE_CRITICAL"}}, "COVERAGE_CRITICAL_CELL", "HARD"),
    ({"guards": {"sectorGroup": "S1"}, "sector_peers_awake": 0}, "LAST_SECTOR", "HARD"),       # TC25
    ({"guards": {"incidentZone": "flood-7"}}, "INCIDENT_ZONE", "HARD"),
    ({"neighbour_prb": {"gnb/102": 90.0}}, "NEIGHBOUR_CONGESTION", "MEDIUM"),
    ({"critical_alarm": True}, "ACTIVE_CRITICAL_ALARM", "MEDIUM"),                            # TC24
    ({"last_unlocked_at": T0 + datetime.timedelta(minutes=45)}, "RECENTLY_UNLOCKED", "SOFT"),
])
def test_guards_block_a_lock_whatever_the_confidence(kw, guard, level):
    """Every guard blocks a LOCK even at confidence 1.0 and is reported with its level, so the model cannot talk its way past a safety rule."""
    d = decide(_cell(prediction={**LOW, "confidence": 1.0}, **kw))
    assert d.decision == engine.NO_CHANGE and d.reason.startswith("SAFETY_BLOCKED")
    assert {"guard": guard, "level": level} in [{k: b[k] for k in ("guard", "level")} for b in d.safety["blocks"]]


def test_a_sector_with_an_awake_peer_and_an_old_unlock_do_not_block():
    """A sector group with another cell awake and an old unlock (90 minutes before the latest sample) do not block a LOCK."""
    d = decide(_cell(guards={"sectorGroup": "S1"}, sector_peers_awake=1,
                     last_unlocked_at=T0 - datetime.timedelta(minutes=30)))
    assert d.decision == engine.LOCK and d.safety["passed"]


def test_no_lock_without_a_low_prediction_and_override_suppresses():
    """No LOCK without a low prediction from the model and from MDAF; an override and a missing series each give their own reason."""
    assert decide(_cell(prediction={"futurePrb": 8.0, "recommendedState": "NO_CHANGE"})).reason == "PREDICTED_NOT_LOW"
    assert decide(_cell(mdaf_future_prb=7.0)).reason == "PREDICTED_NOT_LOW"
    assert decide(_cell(override=True)).reason == "OPERATOR_OVERRIDE"
    assert decide(_cell(series=[])).reason == "NO_DATA"
