import copy
import datetime
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import engine  # noqa: E402

NOW = datetime.datetime(2026, 9, 30, 9, 30, tzinfo=datetime.UTC)
CFG = json.loads((HERE / "thresholds.json").read_text())


def snap(prb=18.4, ue=4, mru="SYNCHRONIZED", state="MUTING_OFF", enabled=True, alarms=(), age_s=60):
    ts = (NOW - datetime.timedelta(seconds=age_s)).isoformat()
    return {"dlPrbUtilization": {"value": prb, "timestamp": ts}, "rrcConnectedUeCount": {"value": ue, "timestamp": ts},
            "mruSynchronizationState": {"value": mru, "timestamp": ts}, "txMutingActivation": {"value": state},
            "txMutingFeatureEnable": {"value": enabled}, "blockingAlarms": list(alarms)}


def decide(**kw):
    return engine.evaluate(snap(**kw), CFG, NOW)


def test_low_load_mutes():
    r = decide()
    assert r["decision"] == "REDUCED_TX"
    assert r["changes"] == {"txMutingFeatureEnable": "true", "txPathOffPattern": "HORIZONTAL_PLANE",
                            "txMutingActivation": "MUTING_ON"}


@pytest.mark.parametrize("kw,reason", [
    ({"prb": 41.0, "ue": 8}, "PRB_NOT_LOW"),
    ({"ue": 10}, "UE_COUNT_NOT_LOW"),
    ({"enabled": False}, "FEATURE_DISABLED"),
    ({"mru": "NOT_SYNCHRONIZED"}, "MRU_NOT_SYNCHRONIZED"),
    ({"alarms": [13325]}, "BLOCKING_ALARM"),
    ({"age_s": 901}, "MEASUREMENTS_NOT_VALID"),
])
def test_activation_blocked(kw, reason):
    r = decide(**kw)
    assert r["decision"] == "NO_CHANGE"
    assert reason in r["reason"]
    assert r["changes"] == {}


def test_trigger_alarm_does_not_block():
    assert decide(alarms=[13321])["decision"] == "REDUCED_TX"


def test_hysteresis_band_keeps_muting():
    r = decide(prb=41.0, ue=11, state="MUTING_ON")
    assert (r["decision"], r["reason"]) == ("NO_CHANGE", "LOAD_WITHIN_HYSTERESIS")


@pytest.mark.parametrize("kw,reason", [
    ({"prb": 42.0}, "PRB_HIGH"),
    ({"ue": 12}, "UE_COUNT_HIGH"),
    ({"mru": "NOT_SYNCHRONIZED"}, "MRU_NOT_SYNCHRONIZED"),
    ({"alarms": [13325]}, "BLOCKING_ALARM"),
    ({"age_s": 901}, "MEASUREMENT_STALE"),
])
def test_restore_full_tx(kw, reason):
    r = decide(state="MUTING_ON", **kw)
    assert r["decision"] == "FULL_TX"
    assert reason in r["reason"]
    assert r["changes"] == {"txMutingActivation": "MUTING_OFF"}


def test_missing_measurement_while_muted_restores():
    s = snap(state="MUTING_ON")
    del s["dlPrbUtilization"]
    assert engine.evaluate(s, CFG, NOW)["decision"] == "FULL_TX"


def test_stale_policy_no_action_keeps_muting():
    cfg = copy.deepcopy(CFG)
    cfg["measurementPolicy"]["staleMeasurementAction"] = "NO_ACTION"
    r = engine.evaluate(snap(state="MUTING_ON", age_s=901), cfg, NOW)
    assert (r["decision"], r["reason"]) == ("NO_CHANGE", "MEASUREMENTS_NOT_VALID")


def test_unknown_state_is_no_change():
    assert decide(state=None)["reason"] == "CURRENT_STATE_UNKNOWN"


def test_thresholds_without_hysteresis_are_rejected():
    cfg = copy.deepcopy(CFG)
    cfg["deactivation"]["prbUtilizationPercent"] = 40.0
    with pytest.raises(engine.ThresholdError):
        engine.validate_thresholds(cfg)
