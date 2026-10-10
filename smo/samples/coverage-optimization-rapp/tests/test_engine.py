"""Unit tests for the coverage decision engine (W10.3-05/07, D10.3-4)."""

import datetime

import pytest

from app import engine
from app.engine import CellInput, allowed_moves, evaluate_guards, kpi_check, plan_cells

T0 = datetime.datetime(2026, 9, 4, 12, tzinfo=datetime.UTC)


def _cell(**kw):
    return CellInput(**{"cell": "301", "total": 1500, "tilt": 60, "power": 43, "baseline_tilt": 60, "baseline_power": 43,
                        "neighbours": ["302", "303"], **kw})


# One row per guard: the input that trips it and the guard that must be the only block.
@pytest.mark.parametrize("kw,guard", [
    ({"guard": {"cellClass": "EMERGENCY"}}, "PROTECTED_CELL"),
    ({"guard": {"incidentZone": "flood-7"}}, "PROTECTED_CELL"),
    ({"critical_alarm": True}, "CRITICAL_ALARM"),
    ({"asleep": True}, "CELL_ASLEEP"),
    ({"asleep_neighbours": ["302"]}, "NEIGHBOUR_ASLEEP"),
    ({"last_woken": T0 - datetime.timedelta(minutes=10)}, "RECENTLY_WOKEN"),
    ({"mro_observing": ["301-302"]}, "MRO_OBSERVING"),
    ({"total": 60}, "INSUFFICIENT_SAMPLES"),
    ({"last_changed_at": T0 - datetime.timedelta(minutes=30)}, "PACING"),
])
def test_each_guard_blocks(kw, guard):
    """Each guard fires on its own input and on nothing else, so the audit trail names the right reason."""
    result = evaluate_guards(_cell(**kw), T0)
    assert not result["passed"] and [b["guard"] for b in result["blocks"]] == [guard]


def test_nothing_blocks_a_healthy_quiet_cell():
    """A cell that woke 45 minutes ago and was last changed exactly 60 minutes ago is free to move: the pacing window is closed at 60 minutes."""
    assert evaluate_guards(_cell(last_woken=T0 - datetime.timedelta(minutes=45),
                                 last_changed_at=T0 - datetime.timedelta(minutes=60)), T0)["passed"]


def test_bounds_close_moves_at_the_limits():
    """A move that would leave the baseline +- 4 degrees or +- 3 dB band is not offered, and `apply` steps 1 degree or 1 dB."""
    assert allowed_moves(_cell()) == ["DOWNTILT", "UPTILT", "POWER_UP", "POWER_DOWN"]
    assert allowed_moves(_cell(tilt=100)) == ["UPTILT", "POWER_UP", "POWER_DOWN"]      # baseline + 4°
    assert allowed_moves(_cell(tilt=20, power=46)) == ["DOWNTILT", "POWER_DOWN"]       # − 4°, + 3 dB
    assert engine.apply("DOWNTILT", 60, 43) == (70, 43) and engine.apply("POWER_DOWN", 60, 43) == (60, 42)


def test_kpi_check_waits_then_confirms_or_degrades():
    """The KPI check says nothing until an hour of post-change PM exists, then IMPROVED_OR_EQUAL, or DEGRADED when the objective grew by more than
    0.5.
    """
    obs = {"at": T0, "preObjective": 10.0}
    state = {"301": {"shares": {"WEAK_COVERAGE": 2, "OVERSHOOT": 8, "PILOT_POLLUTION": 2}}}
    assert kpi_check(obs, state, T0) is None
    assert kpi_check(obs, state, T0 + datetime.timedelta(hours=1))["verdict"] == "IMPROVED_OR_EQUAL"
    worse = {"301": {"shares": {"WEAK_COVERAGE": 9, "OVERSHOOT": 12, "PILOT_POLLUTION": 2}}}
    assert kpi_check(obs, worse, T0 + datetime.timedelta(hours=1))["verdict"] == "DEGRADED"


def test_plan_cells_reasons():
    """Every no-move cell gets the right reason code (safety block, helped by another cell's move, no beneficial move) and a moved cell carries its
    new setting.
    """
    cells = [_cell(), _cell(cell="302", guard={"cellClass": "EMERGENCY"}), _cell(cell="303"), _cell(cell="304", tilt=100, power=46)]
    ok = {"WEAK_COVERAGE": 2, "OVERSHOOT": 2, "PILOT_POLLUTION": 2}
    state = {"301": {"shares": {**ok, "OVERSHOOT": 12}}, "302": {"shares": {**ok, "PILOT_POLLUTION": 9}},
             "303": {"shares": {**ok, "PILOT_POLLUTION": 9}}, "304": {"shares": {**ok, "WEAK_COVERAGE": 8}}}
    inference = {"plan": {"301": "DOWNTILT"}, "drivers": {"301": "OVERSHOOT@301"},
                 "predicted": {"301": {**ok, "OVERSHOOT": 10}, "302": {**ok, "PILOT_POLLUTION": 7},
                               "303": {**ok, "PILOT_POLLUTION": 7}, "304": {**ok, "WEAK_COVERAGE": 8}}}
    safety = {c.cell: evaluate_guards(c, T0) for c in cells}
    out = plan_cells(cells, state, inference, T0, safety)
    assert (out["301"].decision, out["301"].to_setting) == ("DOWNTILT", {"digitalTilt": 70, "configuredMaxTxPower": 43})
    assert out["302"].reason == "SAFETY_BLOCKED:PROTECTED_CELL"
    assert out["303"].reason == "HELPED_BY:301"
    assert out["304"].reason == "NO_BENEFICIAL_MOVE"
    out = plan_cells(cells, state, {**inference, "plan": {}, "drivers": {}}, T0, safety)
    assert out["303"].reason == "NO_BENEFICIAL_MOVE"
