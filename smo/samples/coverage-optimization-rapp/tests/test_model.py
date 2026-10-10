"""Unit tests for the CoverageSensitivityModel, its four logics and the
sample propagation model (W10.3-02/03)."""

import datetime

import pytest

from app import producer as P
from app.model import EmulationLogic, InferenceLogic, TrainingLogic, ValidationLogic
from app.model.CoverageModel import CoverageModel, excess
from app.model.series import OVERLAP, TOTAL, by_cell, overlaps, shares, snapshot

T0 = datetime.datetime(2026, 9, 1, tzinfo=datetime.UTC)
ALL = ["DOWNTILT", "UPTILT", "POWER_UP", "POWER_DOWN"]


def _records(measurements):
    return [{"payload": {"managedElementRef": "gnb", **m}} for m in measurements]


@pytest.fixture(scope="module")
def trained():
    """A model trained once on 72 hours of explored history, shared by the tests of this file."""
    model, metrics = TrainingLogic.train(_records(P.history(P.NEIGHBOURS, T0, 72)))
    return model, metrics


def test_counters_shares_and_overlaps():
    """A cell's PM window carries the report total, problem shares and an overlap counter for each neighbour, and a neighbour's overshoot shows as
    pollution and overlap.
    """
    radio = P.propagation(P.NEIGHBOURS, {}, {"301": "OVERSHOOT"})
    c = P.cell_counters("302", T0.replace(hour=12), radio["302"], (60, 43))
    assert c[TOTAL] > 1000 and set(overlaps(c)) == {"301", "303", "304"}
    assert shares(c)["PILOT_POLLUTION"] > 8 and overlaps(c)["301"] > 18   # 301 reaches into 302
    assert {k for k in c if k.startswith(OVERLAP)} == {f"{OVERLAP}{n}" for n in P.NEIGHBOURS["302"]}


def test_the_propagation_model_responds_to_tilt_and_power():
    """Downtilting an overshooting cell lowers its overshoot and its neighbour's pollution, and raising power lowers weak coverage: the sample
    radio model closes the loop.
    """
    base = P.propagation(P.NEIGHBOURS, {}, {"301": "OVERSHOOT"})
    down = P.propagation(P.NEIGHBOURS, {"301": (70, 43)}, {"301": "OVERSHOOT"})
    assert down["301"]["shares"]["OVERSHOOT"] < base["301"]["shares"]["OVERSHOOT"]
    assert down["302"]["shares"]["PILOT_POLLUTION"] < base["302"]["shares"]["PILOT_POLLUTION"]
    up = P.propagation(P.NEIGHBOURS, {"302": (60, 44)}, {"302": "WEAK_COVERAGE"})
    assert up["302"]["shares"]["WEAK_COVERAGE"] < P.propagation(P.NEIGHBOURS, {}, {"302": "WEAK_COVERAGE"})["302"]["shares"]["WEAK_COVERAGE"]


def test_training_recovers_the_sensitivities(trained):
    """Training recovers the signs and rough size of the sensitivities the propagation model was built with, and refuses history in which no
    setting changed.
    """
    model, metrics = trained
    s = model.sensitivities
    assert metrics["rmse"] < 0.5 and metrics["rowsWithChanges"] >= 8
    assert s["OVERSHOOT"][0] > 1.5                       # uptilt → more overshoot
    assert s["WEAK_COVERAGE"][1] < -1.2                  # more power → less weak coverage
    assert s["PILOT_POLLUTION"][2] > 0.9                 # neighbours reaching in → more pollution
    with pytest.raises(ValueError):
        TrainingLogic.train(_records(P.measurements(P.NEIGHBOURS, {}, {}, T0)))


def test_validation_and_artifact_round_trip(trained):
    """The trained model passes validation on its own history and an artifact round trip gives back an equal model."""
    model, _ = trained
    passed, metrics = ValidationLogic.validate(model, _records(P.history(P.NEIGHBOURS, T0, 72)))
    assert passed and metrics["rmse"] <= ValidationLogic.MAX_RMSE
    assert CoverageModel.from_artifact(model.to_artifact()) == model


# One row per injected fault: the check on the plan the optimiser must find.
@pytest.mark.parametrize("faults,expected", [
    ({"301": "OVERSHOOT"}, lambda plan: plan.get("301") == "DOWNTILT"),
    ({"302": "WEAK_COVERAGE"}, lambda plan: plan == {"302": "POWER_UP"}),
    # pollution in 303 is cured by pulling in its neighbours, not by pushing 303
    ({"303": "PILOT_POLLUTION"}, lambda plan: set(plan) <= {"301", "302", "304"} and set(plan.values()) == {"DOWNTILT"}),
    ({}, lambda plan: plan == {}),
])
def test_the_joint_optimiser_picks_the_corrective_moves(trained, faults, expected):
    """For each fault the optimiser picks the corrective move (including curing pollution through the neighbours), plans at most two cells, only
    for a real gain, and none on a healthy cluster.
    """
    model, _ = trained
    state = P.propagation(P.NEIGHBOURS, {}, faults)
    out = model.optimise(state, {c: ALL for c in state})
    assert expected(out["plan"]), out["plan"]
    assert len(out["plan"]) <= 2
    if out["plan"]:
        assert out["objectiveAfter"] < out["objectiveBefore"] and out["gain"] >= 0.75


def test_the_optimiser_respects_allowed_moves_and_worsening(trained):
    """A cell the guards closed is not moved, no allowed moves means no plan, and no cell's excess is predicted to grow by more than the tolerance."""
    model, _ = trained
    state = P.propagation(P.NEIGHBOURS, {}, {"301": "OVERSHOOT"})
    out = model.optimise(state, {"302": ALL, "303": ALL})           # 301 may not move
    assert "301" not in out["plan"]
    assert model.optimise(state, {})["plan"] == {}
    for c, pred in out["predicted"].items():
        assert excess(pred) <= excess(state[c]["shares"]) + 0.5


def test_emulation_and_inference(trained):
    """Emulation passes on faulty and healthy twin clusters with at least 90 % correct moves and no false action, and inference plans the downtilt
    for an overshooting cell.
    """
    model, _ = trained
    sim = []
    for cl, spec in {"dt1": ("OVERSHOOT", "a"), "dt2": ("WEAK_COVERAGE", "b"), "dt3": ("PILOT_POLLUTION", "c"),
                     "dt4": ("HEALTHY", "a")}.items():
        topo, fault = P.sim_cluster(cl), f"{cl}-{spec[1]}"
        for h in range(6):
            sim += _records(P.measurements(topo, {}, {} if spec[0] == "HEALTHY" else {fault: spec[0]},
                                           T0 + datetime.timedelta(hours=h),
                                           extra={"cluster": cl, "scenario": spec[0], "faultCell": fault}))
    passed, metrics = EmulationLogic.emulate(model, sim)
    assert passed and metrics["moveAccuracy"] >= 0.9 and metrics["falseActions"] == 0
    state = snapshot(by_cell(_records(P.measurements(P.NEIGHBOURS, {}, {"301": "OVERSHOOT"}, T0.replace(hour=12)))))
    out = InferenceLogic.infer(model, state, {c: ALL for c in state})
    assert out["cells"]["301"]["problem"] == "OVERSHOOT" and out["plan"]["301"] == "DOWNTILT"
