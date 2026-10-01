"""Unit tests for the MRO decision engine (W10.2-05/07, D10.2-4)."""

import datetime

import pytest

from app import engine
from app.engine import RelationInput, decide
from app.model.series import ATTEMPTS, PING_PONG, TOO_EARLY, TOO_LATE, WRONG_CELL

T0 = datetime.datetime(2026, 9, 4, 12, tzinfo=datetime.UTC)
LATE = {ATTEMPTS: 200, TOO_LATE: 14, TOO_EARLY: 1, WRONG_CELL: 1, PING_PONG: 1}
OK = {ATTEMPTS: 200, TOO_LATE: 1, TOO_EARLY: 1, WRONG_CELL: 0, PING_PONG: 0}


def _series(*windows, start=T0):
    return [(start + datetime.timedelta(hours=i), {"values": w}) for i, w in enumerate(windows)]


def _rel(**kw):
    defaults = {"relation": "201-202", "source": "201", "target": "202", "series": _series(LATE, LATE), "current_cio": 0,
                "prediction": {"futureRate": 8.5, "cause": "TOO_LATE", "recommendation": "RAISE_CIO"}}
    return RelationInput(**{**defaults, **kw})


@pytest.mark.parametrize("cause,rec,step", [("TOO_LATE", "RAISE_CIO", 2), ("TOO_EARLY", "LOWER_CIO", -2),
                                            ("PING_PONG", "LOWER_CIO", -2), ("WRONG_CELL", "LOWER_CIO", -1)])
def test_the_failure_class_sets_the_step(cause, rec, step):
    d = decide(_rel(prediction={"futureRate": 8.0, "cause": cause, "recommendation": rec}))
    assert (d.decision, d.new_cio, d.next_state) == (rec, step, engine.OBSERVING)


def test_bounds_hold_and_healthy():
    assert decide(_rel(current_cio=6)).reason == "AT_BOUND:TOO_LATE"
    assert decide(_rel(current_cio=5)).new_cio == 6
    assert decide(_rel(current_cio=1, baseline_cio=-4)).new_cio == 2
    assert decide(_rel(prediction={"futureRate": 3.0, "cause": "TOO_LATE", "recommendation": "HOLD"})).reason == "HOLD_ZONE"
    assert decide(_rel(prediction={"futureRate": 1.0, "cause": None, "recommendation": "HEALTHY"})).reason == "HEALTHY"


@pytest.mark.parametrize("kw,guard", [
    ({"ho_allowed": False}, "HO_NOT_ALLOWED"),
    ({"target_guard": {"cellClass": "EMERGENCY"}}, "PROTECTED_CELL"),
    ({"source_guard": {"incidentZone": "flood-7"}}, "PROTECTED_CELL"),
    ({"target_o1_asleep": True}, "TARGET_ASLEEP"),
    ({"target_es_state": "PRE_SLEEP"}, "TARGET_ASLEEP"),
    ({"target_last_woken": T0 + datetime.timedelta(minutes=45)}, "TARGET_RECENTLY_WOKEN"),
    ({"series": _series(LATE, {**LATE, ATTEMPTS: 40})}, "INSUFFICIENT_SAMPLES"),
    ({"last_changed_at": T0 + datetime.timedelta(minutes=30)}, "PACING"),
    ({"mlb_observing": True}, "MLB_OBSERVING"),
])
def test_guards_block_whatever_the_confidence(kw, guard):
    d = decide(_rel(**kw))
    assert d.decision == engine.NO_CHANGE and guard in d.reason


def test_kpi_verified_revert_and_confirmation():
    change = {"at": T0, "from": 0, "to": 2, "preRate": 8.5}
    observing = dict(state=engine.OBSERVING, last_change=change, current_cio=2)
    assert decide(_rel(series=_series(LATE, LATE, start=T0 - datetime.timedelta(hours=1)), **observing)).reason == "OBSERVING"
    worse = {ATTEMPTS: 200, TOO_LATE: 20, TOO_EARLY: 2}
    d = decide(_rel(series=_series(LATE, worse, worse), **observing))
    assert (d.decision, d.new_cio, d.reason, d.kpi["verdict"]) == (engine.REVERT, 0, "KPI_DEGRADED", "DEGRADED")
    d = decide(_rel(series=_series(LATE, OK, OK), **observing))
    assert (d.decision, d.reason, d.next_state) == (engine.NO_CHANGE, "CHANGE_CONFIRMED", engine.STEADY)
