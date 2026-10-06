import copy
import json
from pathlib import Path

import pytest

from app import engine

HERE = Path(__file__).resolve().parents[1]
CFG = json.loads((HERE / "app" / "thresholds.json").read_text())


def snap(prb=18.4, ue=4, state="MUTING_OFF", enabled=True):
    s = {"txMutingActivation": {"value": state}, "txMutingFeatureEnable": {"value": enabled}}
    if prb is not None:
        s["dlPrbUtilization"] = {"value": prb}
    if ue is not None:
        s["rrcConnectedUeCount"] = {"value": ue}
    return s


def decide(**kw):
    return engine.evaluate(snap(**kw), CFG)


def test_low_load_mutes():
    r = decide()
    assert (r["decision"], r["reason"]) == ("REDUCED_TX", "INSTANTANEOUS_LOW_LOAD")
    assert r["changes"] == {"txMutingFeatureEnable": "true", "txPathOffPattern": "HORIZONTAL_PLANE",
                            "txMutingActivation": "MUTING_ON"}


@pytest.mark.parametrize("kw,reason", [
    ({"prb": 41.0, "ue": 8}, "PRB_NOT_LOW"),
    ({"prb": 40.0}, "PRB_NOT_LOW"),
    ({"ue": 10}, "UE_COUNT_NOT_LOW"),
    ({"enabled": False}, "FEATURE_DISABLED"),
    ({"prb": 50.0, "ue": 20, "enabled": False}, "PRB_NOT_LOW,UE_COUNT_NOT_LOW,FEATURE_DISABLED"),
])
def test_mute_blocked(kw, reason):
    r = decide(**kw)
    assert (r["decision"], r["reason"]) == ("NO_CHANGE", reason)
    assert r["changes"] == {}


def test_hysteresis_band_keeps_muting():
    r = decide(prb=41.0, ue=11, state="MUTING_ON")
    assert (r["decision"], r["reason"]) == ("NO_CHANGE", "LOAD_WITHIN_HYSTERESIS")


@pytest.mark.parametrize("kw,reason", [
    ({"prb": 42.0}, "PRB_HIGH"),
    ({"ue": 12}, "UE_COUNT_HIGH"),
    ({"prb": 45.0, "ue": 15}, "PRB_HIGH,UE_COUNT_HIGH"),
])
def test_restore_full_tx(kw, reason):
    r = decide(state="MUTING_ON", **kw)
    assert (r["decision"], r["reason"]) == ("FULL_TX", reason)
    assert r["changes"] == {"txMutingActivation": "MUTING_OFF"}


def test_disabled_feature_does_not_stop_a_restore():
    assert decide(state="MUTING_ON", prb=50.0, enabled=False)["decision"] == "FULL_TX"


@pytest.mark.parametrize("state", ["MUTING_OFF", "MUTING_ON"])
@pytest.mark.parametrize("missing", [{"prb": None}, {"ue": None}])
def test_missing_measurement_is_no_change(state, missing):
    r = decide(state=state, **missing)
    assert (r["decision"], r["reason"], r["changes"]) == ("NO_CHANGE", "MEASUREMENT_MISSING", {})


def test_unknown_state_is_no_change():
    assert decide(state=None)["reason"] == "CURRENT_STATE_UNKNOWN"
    assert decide(state="HALF")["reason"] == "CURRENT_STATE_UNKNOWN"


def test_feature_requirement_is_configurable():
    cfg = copy.deepcopy(CFG)
    cfg["requestedConfiguration"]["requireFeatureEnabled"] = False
    assert engine.evaluate(snap(enabled=False), cfg)["decision"] == "REDUCED_TX"


@pytest.mark.parametrize("key,field", [("activation", "prbUtilizationPercent"), ("activation", "rrcConnectedUeCount")])
def test_thresholds_need_a_hysteresis_band(key, field):
    cfg = copy.deepcopy(CFG)
    cfg["activation"][field] = cfg["deactivation"][field]
    with pytest.raises(engine.ThresholdError):
        engine.validate_thresholds(cfg)
    with pytest.raises(engine.ThresholdError):
        engine.evaluate(snap(), cfg)
