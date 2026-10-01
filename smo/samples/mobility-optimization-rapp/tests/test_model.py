"""Unit tests for the Mobility model and its execution-mode logics (W10.2-02).
Run with: cd smo/samples/mobility-optimization-rapp && PYTHONPATH=.:../../shared:../../sdk pytest tests -q
"""

import datetime

import pytest

from app.model import EmulationLogic, InferenceLogic, TrainingLogic, ValidationLogic
from app.model.MobilityModel import MobilityModel
from app.model.series import ATTEMPTS, PING_PONG, TOO_EARLY, TOO_LATE, WRONG_CELL, by_relation, dominant_cause, mro_rate
from app.producer import relation_counters, windows

START = datetime.datetime(2026, 9, 1, tzinfo=datetime.UTC)
RELATIONS = {"201-202": "HEALTHY", "201-203": "TOO_LATE", "202-203": "TOO_EARLY", "203-204": "HEALTHY", "204-201": "PING_PONG"}


def _records(relations, hours, me="gnb", sim=False):
    return [{"payload": {"managedElementRef": me, "cellId": r.split("-")[0], "relation": r, "values": v,
                         "timestamp": t.isoformat(), **({"scenario": s} if sim else {})}}
            for r, s in relations.items() for t, v in windows(r, START, hours, s)]


@pytest.fixture(scope="module")
def trained():
    return TrainingLogic.train(_records(RELATIONS, 72))


def test_rates_and_failure_classes():
    c = {ATTEMPTS: 200, TOO_LATE: 14, TOO_EARLY: 1, WRONG_CELL: 1, PING_PONG: 0}
    assert mro_rate(c) == 8.0 and dominant_cause(c) == "TOO_LATE"
    assert dominant_cause({ATTEMPTS: 100, TOO_EARLY: 4, PING_PONG: 3}) == "TOO_EARLY"
    assert dominant_cause({ATTEMPTS: 100, TOO_EARLY: 1, PING_PONG: 6}) == "PING_PONG"
    assert dominant_cause({ATTEMPTS: 100, WRONG_CELL: 6}) == "WRONG_CELL"
    assert dominant_cause({ATTEMPTS: 100}) is None and mro_rate({ATTEMPTS: 0}) == 0.0


def test_training_and_recommendations(trained):
    model, metrics = trained
    assert metrics["rmse"] < 1.0 and metrics["confidence"] > 0.6
    assert model.infer(8.0, 8.0, "TOO_LATE")["recommendation"] == "RAISE_CIO"
    assert model.infer(8.0, 8.0, "PING_PONG")["recommendation"] == "LOWER_CIO"
    assert model.infer(3.0, 3.0, "TOO_LATE")["recommendation"] == "HOLD"
    assert model.infer(1.0, 1.0, "TOO_LATE")["recommendation"] == "HEALTHY"
    assert MobilityModel.from_artifact(model.to_artifact()) == model


def test_validation_and_emulation(trained):
    model, _ = trained
    passed, metrics = ValidationLogic.validate(model, _records(RELATIONS, 72))
    assert passed and metrics["score"] >= ValidationLogic.PASS_THRESHOLD
    passed, metrics = EmulationLogic.emulate(model, _records({"a-b": "TOO_LATE", "b-c": "TOO_EARLY", "c-d": "HEALTHY",
                                                              "d-e": "WRONG_CELL", "e-f": "PING_PONG"}, 48, sim=True))
    assert passed and metrics["directionAccuracy"] == 1.0 and metrics["falseActions"] == 0
    assert not ValidationLogic.validate(MobilityModel(weights=[10.0, 0.0]), _records(RELATIONS, 48))[0]


def test_inference_per_relation(trained):
    model, _ = trained
    series = by_relation(_records({"201-203": "TOO_LATE"}, 3))["201-203"]
    out = InferenceLogic.infer(model, "201-203", series)
    assert out["cause"] == "TOO_LATE" and out["recommendation"] == "RAISE_CIO" and out["attempts"] >= 50


def test_synthetic_counters_follow_the_daily_load():
    night, peak = relation_counters("r", START.replace(hour=2)), relation_counters("r", START.replace(hour=18))
    assert 50 <= night[ATTEMPTS] < peak[ATTEMPTS]
