"""Unit tests for the CongestionSteeringModel, its four logics and the sample
load model (W10.4-02/03)."""

import datetime

import pytest

from app import producer as P
from app.model import EmulationLogic, InferenceLogic, TrainingLogic, ValidationLogic
from app.model.SteeringModel import SteeringModel
from app.model.series import by_cell, ho_fail_rate, neighbours, score

T0 = datetime.datetime(2026, 9, 1, tzinfo=datetime.UTC)
NOON = T0 + datetime.timedelta(days=3, hours=12)


def _records(measurements):
    return [{"payload": {"managedElementRef": "gnb", **m}} for m in measurements]


@pytest.fixture(scope="module")
def trained():
    return TrainingLogic.train(_records(P.history(P.NEIGHBOURS, P.LAYERS, T0, 72)), P.LAYERS)


def _state(model, faults, settings=None):
    ms = [m for t in (NOON - datetime.timedelta(hours=1), NOON)
          for m in P.measurements(P.NEIGHBOURS, P.LAYERS, settings, faults, t)]
    return {c: InferenceLogic.infer(model, c, s) for c, s in by_cell(_records(ms)).items()}


def test_the_load_model_scores_and_moves_load():
    base = P.measurements(P.NEIGHBOURS, P.LAYERS, None, {"401": "HOTSPOT"}, NOON)
    c401 = next(m["values"] for m in base if m["cellId"] == "401")
    assert score(c401) > 75 and neighbours(c401) == ["402", "411"] and ho_fail_rate(c401, "402") < 2
    settings = P.baseline_settings(P.NEIGHBOURS, P.LAYERS)
    settings["401"]["prio"]["F2100"] = 6
    settings["401"]["cio"]["402"] = 6
    moved = {m["cellId"]: m["values"] for m in P.measurements(P.NEIGHBOURS, P.LAYERS, settings, {"401": "HOTSPOT"}, NOON)}
    assert score(moved["401"]) < score(c401) - 15
    assert score(moved["411"]) > score(next(m["values"] for m in base if m["cellId"] == "411"))
    assert ho_fail_rate(moved["401"], "402") > 5                      # 6 dB CIO: too-early handovers


def test_training_recovers_the_transfer(trained):
    model, metrics = trained
    assert abs(model.transfer["CONNECTED"] - P.CIO_TRANSFER) < 0.006
    assert abs(model.transfer["IDLE"] - P.IDLE_TRANSFER) < 0.012
    assert metrics["rmse"] < 2.0 and metrics["rowsWithSteering"] >= 6
    assert max(model.profile[6:9]) > 3                                # the morning ramp is learned
    with pytest.raises(ValueError):
        TrainingLogic.train(_records(P.measurements(P.NEIGHBOURS, P.LAYERS, None, {}, T0)), P.LAYERS)


def test_validation_and_artifact_round_trip(trained):
    model, _ = trained
    passed, metrics = ValidationLogic.validate(model, _records(P.history(P.NEIGHBOURS, P.LAYERS, T0, 72)), P.LAYERS)
    assert passed and metrics["rmse"] <= ValidationLogic.MAX_RMSE
    assert SteeringModel.from_artifact(model.to_artifact()) == model


def test_the_plan_offloads_a_hotspot_to_the_least_loaded_neighbour(trained):
    model, _ = trained
    state = _state(model, {"401": "HOTSPOT"})
    cands = {c: EmulationLogic.candidates_for(c, P.NEIGHBOURS[c], P.LAYERS) for c in state}
    plan = model.plan(state, cands, {})
    assert plan["401"]["decision"].startswith("STEER_") and plan["401"]["targetForecastAfter"] <= 55
    assert all(plan[c]["decision"] == "NO_CHANGE" for c in ("402", "411", "412"))
    # a hot target is never chosen
    state["402"]["forecast"] = state["411"]["forecast"] = 54.0
    plan = model.plan(state, cands, {})
    assert plan["401"]["reason"] == "NO_ELIGIBLE_TARGET"
    assert {r["reason"] for r in plan["401"]["rejected"]} == {"TARGET_CAPACITY"}


def test_hysteresis_and_release(trained):
    model, _ = trained
    state = {"401": {"score": 60, "forecast": 60}, "402": {"score": 30, "forecast": 30}}
    release = {"401": [{"knob": "IDLE", "layer": "F2100", "ref": "NRFreqRelation=401-F2100", "from": 6, "to": 5}]}
    assert model.plan(state, {"401": []}, release)["401"]["reason"] == "HOLD_ZONE"
    state["401"] = {"score": 40, "forecast": 40}
    out = model.plan(state, {"401": []}, release)["401"]
    assert (out["decision"], out["reason"]) == ("RELEASE_IDLE", "LOAD_RELIEVED")
    assert model.plan(state, {"401": []}, {})["401"]["reason"] == "NOT_CONGESTED"


def test_emulation(trained):
    model, _ = trained
    sim = []
    for cl, spec in {"dt1": ("HOTSPOT", "a"), "dt2": ("HOTSPOT", "c"), "dt3": ("HEALTHY", "a")}.items():
        topo, layers = P.sim_cluster(cl)
        hot = f"{cl}-{spec[1]}"
        for h in range(10, 20):
            sim += _records(P.measurements(topo, layers, None, {hot: "HOTSPOT"} if spec[0] == "HOTSPOT" else {},
                                           T0 + datetime.timedelta(hours=h), extra=lambda c, cl=cl, spec=spec, hot=hot, layers=layers: {
                                               "cluster": cl, "scenario": spec[0], "hotCell": hot, "layer": layers[c]}))
    passed, metrics = EmulationLogic.emulate(model, sim)
    assert passed and metrics["steeringAccuracy"] >= 0.9 and metrics["falseActions"] == 0
