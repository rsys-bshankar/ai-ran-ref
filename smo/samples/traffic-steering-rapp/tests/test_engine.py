"""Unit tests for the traffic steering engine (W10.4-05/07, D10.4-4)."""

import datetime

import pytest

from app import engine
from app.engine import Neighbour, SourceInput, kpi_check, options, source_guards

T0 = datetime.datetime(2026, 9, 4, 12, tzinfo=datetime.UTC)


def _src(**kw):
    nbrs = kw.pop("neighbours", None) or [Neighbour(cell="402", layer="F3500"), Neighbour(cell="411", layer="F2100")]
    return SourceInput(**{"cell": "401", "layer": "F3500", "samples": 12, "baseline_cio": 0, "baseline_priority": 5,
                          "neighbours": nbrs, "priorities": {"F2100": 5}, **kw})


# One row per source guard: the input that trips it and the guard that must be the only block.
@pytest.mark.parametrize("kw,guard", [
    ({"guard": {"cellClass": "EMERGENCY"}}, "PROTECTED_CELL"),
    ({"guard": {"incidentZone": "flood-7"}}, "PROTECTED_CELL"),
    ({"critical_alarm": True}, "CRITICAL_ALARM"),
    ({"asleep": True}, "CELL_ASLEEP"),
    ({"coverage_observing": True}, "COVERAGE_OBSERVING"),
    ({"samples": 4}, "INSUFFICIENT_SAMPLES"),
    ({"last_changed_at": T0 - datetime.timedelta(minutes=30)}, "PACING"),
])
def test_source_guards(kw, guard):
    """Each source guard fires on its own input and on nothing else, so the audit trail names the right reason."""
    result = source_guards(_src(**kw), T0)
    assert not result["passed"] and [b["guard"] for b in result["blocks"]] == [guard]


def test_idle_first_towards_another_layer_and_connected_within_the_layer():
    """A neighbour on another layer is steered in idle mode (priority + 1) and one on the same layer in connected mode (CIO + 2 dB), with the
    managed references the writes need.
    """
    cands, releases, excluded = options(_src(), T0)
    assert [(c["knob"], c.get("layer") or c.get("target"), c["from"], c["to"]) for c in cands] == [
        ("IDLE", "F2100", 5, 6), ("CONNECTED", "402", 0, 2)]
    assert cands[0]["ref"] == "NRFreqRelation=401-F2100" and cands[1]["ref"] == "NRCellRelation=401-402"
    assert releases == [] and excluded == []


def test_idle_at_bound_falls_back_to_connected_and_the_cio_envelope_holds():
    """An idle priority at its bound is excluded as IDLE_AT_BOUND and connected steering takes over, but not past the shared CIO envelope
    (CIO_AT_BOUND).
    """
    cands, _, excluded = options(_src(priorities={"F2100": 7}), T0)
    assert {"layer": "F2100", "reason": "IDLE_AT_BOUND"} in excluded
    assert [c.get("target") for c in cands] == ["402", "411"]
    nbrs = [Neighbour(cell="402", layer="F3500", cio=6), Neighbour(cell="411", layer="F2100", cio=0)]
    cands, _, excluded = options(_src(neighbours=nbrs, priorities={"F2100": 7}), T0)
    assert {"target": "402", "knob": "CONNECTED", "reason": "CIO_AT_BOUND"} in excluded


# One row per neighbour condition and the exclusion reason it must give.
@pytest.mark.parametrize("nbr,reason", [
    ({"protected": True}, "TARGET_PROTECTED"),
    ({"critical_alarm": True}, "TARGET_CRITICAL_ALARM"),   # W10-alarm-cellref
    ({"asleep": True}, "TARGET_ASLEEP"),
    ({"last_woken": T0 - datetime.timedelta(minutes=10)}, "TARGET_RECENTLY_WOKEN"),
    ({"coverage_observing": True}, "TARGET_COVERAGE_OBSERVING"),
])
def test_target_exclusions(nbr, reason):
    """Each condition rules a neighbour out as a target, with its own reason."""
    _, _, excluded = options(_src(neighbours=[Neighbour(cell="402", layer="F3500", **nbr)]), T0)
    assert excluded == [{"target": "402", "reason": reason}]


# One row per relation setting that forbids CIO steering and the reason it gives.
@pytest.mark.parametrize("rel,reason", [({"mlb_allowed": False}, "MLB_NOT_ALLOWED"), ({"ho_allowed": False}, "MLB_NOT_ALLOWED"),
                                        ({"mro_observing": True}, "MRO_OBSERVING")])
def test_connected_steering_respects_the_relation_and_the_mobility_rapp(rel, reason):
    """No CIO step on a relation that does not allow load balancing or handover, or that the Mobility rApp is observing, since the two rApps share
    the CIO.
    """
    cands, _, excluded = options(_src(neighbours=[Neighbour(cell="402", layer="F3500", **rel)]), T0)
    assert cands == [] and excluded == [{"target": "402", "knob": "CONNECTED", "reason": reason}]


def test_anti_oscillation():
    """A neighbour that steered load to this cell less than 6 hours ago is not a target; after 6 hours it is."""
    s = _src(steered_to_me={"402": T0 - datetime.timedelta(hours=2)})
    assert {"target": "402", "reason": "ANTI_OSCILLATION"} in options(s, T0)[2]
    s = _src(steered_to_me={"402": T0 - datetime.timedelta(hours=7)})
    assert not options(s, T0)[2]


def test_releases_step_back_this_rapps_own_steering():
    """A release steps back only the steering this rApp has in force, one step per knob and biggest CIO bias first."""
    nbrs = [Neighbour(cell="402", layer="F3500", cio=4), Neighbour(cell="411", layer="F2100")]
    _, releases, _ = options(_src(neighbours=nbrs, priorities={"F2100": 6}, steering={"cio": {"402": 2}, "prio": {"F2100": 1}}), T0)
    assert [(r["knob"], r["from"], r["to"]) for r in releases] == [("CONNECTED", 4, 2), ("IDLE", 6, 5)]


def test_kpi_check():
    """The KPI check waits for an hour, confirms a calm change, and names each way a change degrades (target congested, source worse, handover
    failures); handover failures count only for a CIO change.
    """
    change = {"at": T0, "source": "401", "knob": "CONNECTED", "targets": ["402"], "preScore": 80, "preForecast": 80.5,
              "preTargets": {"402": 44}, "preHoFail": 1.0}
    assert kpi_check(change, {"401": 75, "402": 49}, 1.0, T0) is None
    later = T0 + datetime.timedelta(hours=1)
    assert kpi_check(change, {"401": 75, "402": 49}, 1.2, later)["verdict"] == "IMPROVED_OR_EQUAL"
    assert kpi_check(change, {"401": 75, "402": 72}, 1.2, later)["causes"] == ["TARGET_CONGESTED"]
    assert kpi_check(change, {"401": 84, "402": 49}, 1.2, later)["causes"] == ["SOURCE_WORSE"]
    assert kpi_check(change, {"401": 75, "402": 49}, 4.5, later)["causes"] == ["HO_FAILURES"]
    assert kpi_check({**change, "knob": "IDLE"}, {"401": 75, "402": 49}, 4.5, later)["verdict"] == "IMPROVED_OR_EQUAL"
