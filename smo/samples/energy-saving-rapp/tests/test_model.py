"""Unit tests for the EnergySaving model and its execution-mode logics
(HISTORY.md W10-02, decision D-6).
Run with: cd smo/samples/energy-saving-rapp && PYTHONPATH=.:../../shared:../../sdk pytest tests -q
"""

import datetime

import pytest

from app.model import EmulationLogic, InferenceLogic, TrainingLogic, ValidationLogic
from app.model.EnergyModel import EnergyModel, solve_least_squares
from app.model.series import by_cell
from app.producer import diurnal_prb, samples

START = datetime.datetime(2026, 9, 1, tzinfo=datetime.UTC)


def _records(cells, hours, me="gnb-du-01", start=START, step=60, value=None):
    return [{"payload": {"managedElementRef": me, **s}}
            for series in samples(cells, start, hours, step, value).values() for s in series]


@pytest.fixture(scope="module")
def trained():
    return TrainingLogic.train(_records(["101", "102", "103", "104"], 24 * 7))


def test_least_squares_recovers_known_weights():
    rows = [[1.0, x, x * x] for x in range(10)]
    assert [round(w, 3) for w in solve_least_squares(rows, [2 + 3 * x - 0.5 * x * x for x in range(10)])] == [2.0, 3.0, -0.5]


def test_training_learns_the_daily_profile_and_a_tight_fit(trained):
    model, metrics = trained
    assert metrics["trainingRows"] > 600 and metrics["rmse"] < 2.0 and metrics["confidence"] > 0.8
    assert len(model.profile) == 24 and model.profile[2] < 5 < 40 < model.profile[18]


def test_recommendations_respect_thresholds_and_the_morning_ramp(trained):
    model, _ = trained
    assert model.infer(2.0, 2.2, hour=1)["recommendedState"] == "LOCKED"        # TC08: PRB 2 % at night
    assert model.infer(10.0, 10.0, hour=1)["recommendedState"] == "NO_CHANGE"    # hysteresis zone
    assert model.infer(20.0, 12.0, hour=1)["recommendedState"] == "UNLOCKED"     # wake threshold
    assert model.infer(2.0, 2.0, hour=5)["recommendedState"] == "NO_CHANGE"      # the ramp is coming: don't sleep now


def test_artifact_round_trip(trained):
    model, _ = trained
    restored = EnergyModel.from_artifact(model.to_artifact())
    assert restored == model and restored.infer(3.0, 3.0, 2) == model.infer(3.0, 3.0, 2)


def test_validation_and_emulation_pass_on_realistic_data(trained):
    model, _ = trained
    passed, metrics = ValidationLogic.validate(model, _records(["101", "102", "103", "104"], 24 * 7))
    assert passed and metrics["score"] >= ValidationLogic.PASS_THRESHOLD
    passed, metrics = EmulationLogic.emulate(model, _records(["201", "202"], 48, me="dt"))
    assert passed and metrics["midnightRecommendation"] == "LOCKED" and metrics["energySavingsKwh"] > 0
    assert metrics["coverageImpact"] <= EmulationLogic.MAX_COVERAGE_IMPACT


def test_an_untrained_model_fails_validation_and_tiny_history_fails_training():
    passed, metrics = ValidationLogic.validate(EnergyModel(weights=[-30.0, 0.0, 0.0]), _records(["101"], 48))
    assert not passed
    with pytest.raises(ValueError):
        TrainingLogic.train(_records(["101"], 1))


def test_inference_uses_the_hour_ago_sample(trained):
    model, _ = trained
    live = by_cell(_records(["101"], 1.5, start=START, step=5, value=lambda c, t: 2.0))
    out = InferenceLogic.infer(model, "101", live["gnb-du-01/101"])
    assert out["recommendedState"] == "LOCKED" and out["prbUtilization"] == 2.0
    assert out["observedAt"].startswith("2026-09-01T01:25")


def test_the_synthetic_profile_is_idle_at_night_and_busy_by_day():
    assert diurnal_prb("101", START.replace(hour=2)) < 5 < 40 < diurnal_prb("101", START.replace(hour=18))
