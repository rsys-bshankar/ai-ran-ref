"""Wave 10.3 — the Coverage Optimization rApp's integration tests
(docs/ROADMAP.md Wave 10.3, W10.3-10): CCO-01..CCO-20, run against
the in-process mesh with the real onboarding, AIMgF / MLMR / MLLF / NFO,
DME, Intent Service, SA SMOS, RAN NF OAM and mock O1 adaptor.

The live PM comes from the cells' current O1 tilt and power (read back from
the mock adaptor) through the sample's propagation model, so every change
the rApp makes is measured in the next hour's PM."""

import energy_saving_env as es
import mobility_env as mro
import pytest
from coverage_env import (CSAR, ME, OPERATOR, RAPP, SMO_ROOT, Clock, cell_state, decision, evaluate, fault, ok, scenario,
                          setting)

CELLS = ["301", "302", "303", "304"]
OVERSHOOT_301 = {"301": "OVERSHOOT"}


def _decisions(mesh, iid, **params):
    return ok(mesh[RAPP].get(f"/instances/{iid}/decisions", params=params))["items"]


# ---------------------------------------------------------------- CCO-01..13, CCO-20

def test_cco01_to_cco13_lifecycle_joint_plan_verified_tilt_and_kpi_confirmation(mesh, loaded_apps, monkeypatch):
    out = scenario(mesh, loaded_apps, monkeypatch, {"rapp": "AUTONOMOUS"})["rapp"]
    iid = out["instanceId"]
    # CCO-01 — onboarded AVAILABLE with execution modes, autonomy modes and runtime profiles
    status = ok(mesh["onboarding"].get(f"/packages/{ok(mesh[RAPP].get(f'/instances/{iid}'))['packageId']}/onboarding-status"))
    assert status["state"] == "AVAILABLE" and len(status["aiCapabilities"]["executionModes"]) == 4
    # CCO-02 — COVERAGE_PERFORMANCE (O1 PM → RAN NF OAM → DME) and the Digital Twin's COVERAGE_PERFORMANCE_SIM
    inst = ok(mesh[RAPP].get(f"/instances/{iid}"))
    assert {s: d["dataset"] for s, d in inst["datasets"].items()} == {
        "TRAINING": "COVERAGE_PERFORMANCE", "INFERENCE": "COVERAGE_PERFORMANCE", "EMULATION": "COVERAGE_PERFORMANCE_SIM"}
    assert inst["datasets"]["EMULATION"]["sourceDomain"] == "DIGITAL_TWIN"
    # CCO-03..07 — sensitivities learned, validated, every injected fault answered correctly, promoted, ACTIVE
    sens = out["train"]["metrics"]["sensitivities"]
    assert sens["OVERSHOOT"][0] > 1.5 and sens["WEAK_COVERAGE"][1] < -1.2 and sens["PILOT_POLLUTION"][2] > 0.9
    assert out["validate"]["passed"] and out["emulate"]["passed"]
    assert out["emulate"]["metrics"]["moveAccuracy"] == 1.0 and out["emulate"]["metrics"]["falseActions"] == 0
    assert out["deploy"]["lifecycle"]["runtimeLifecycleState"] == "ACTIVE"
    assert out["deploy"]["lifecycle"]["modelLifecycleState"] == "PROMOTED"

    # CCO-08 — 301 overshoots into 302/303: the joint plan downtilts 301, through Intent → O1-CM handler → DME
    clock = Clock(mesh, loaded_apps).hour(OVERSHOOT_301)
    result = evaluate(mesh, iid, correlation_id="cco-exec-1")
    d301 = decision(result, "301")
    assert (d301["decision"], d301["toSetting"]) == ("DOWNTILT", {"digitalTilt": 70, "configuredMaxTxPower": 43})
    assert set(result["plan"]["moves"]) <= {"301", "304"} and "301" in result["plan"]["moves"]
    assert result["plan"]["objectiveAfter"] < result["plan"]["objectiveBefore"]
    assert d301["intent"]["status"] == "DISPATCHED" and d301["outcome"] == "EXECUTED"
    action = ok(mesh["dme"].get(f"/actions/{d301['action']['actionId']}"))
    assert action["changes"][0]["managedFunctionRef"] == "CommonBeamformingFunction=301" and action["status"] == "COMPLETED"
    # CCO-09 — read back over NETCONF get-config; the polluted neighbours are helped, not moved
    assert setting(mesh, "301") == (70, 43) and d301["verification"]["result"] == "VERIFIED"
    assert decision(result, "302")["reason"].startswith("HELPED_BY:") and setting(mesh, "302") == (60, 43)
    assert cell_state(mesh, iid, "301")["state"] == "OBSERVING"

    # CCO-10 — no new PM yet: the change set is still being observed; nothing moves
    assert {d["reason"] for d in evaluate(mesh, iid)["decisions"]} == {"OBSERVING"}
    # CCO-11 — an hour later the measured cluster objective has dropped: the change set is CONFIRMED
    clock.hour(OVERSHOOT_301)
    result = evaluate(mesh, iid)
    assert result["kpi"]["verdict"] == "IMPROVED_OR_EQUAL" and result["kpi"]["postObjective"] < result["kpi"]["preObjective"]
    assert (decision(result, "301")["reason"], decision(result, "301")["outcome"]) == ("CHANGE_CONFIRMED", "CONFIRMED")
    assert decision(result, "303")["reason"] in ("CHANGE_SET_REVIEW", "CHANGE_CONFIRMED")
    assert cell_state(mesh, iid, "301")["state"] == "STEADY"

    # CCO-12/13 — 1° steps, each confirmed before the next, until the overshoot is gone — never past baseline + 4°
    tilts, objective = [70], result["kpi"]["postObjective"]
    for _ in range(6):
        clock.hour(OVERSHOOT_301)
        result = evaluate(mesh, iid)
        if not result["plan"]["moves"]:
            break
        assert all(abs(setting(mesh, c)[0] - 60) <= 40 for c in CELLS)
        tilts.append(setting(mesh, "301")[0])
        clock.hour(OVERSHOOT_301)
        result = evaluate(mesh, iid)
        assert result["kpi"]["verdict"] == "IMPROVED_OR_EQUAL"
        objective = result["kpi"]["postObjective"]
    assert tilts == sorted(tilts) and tilts[-1] > 70 and objective < 2.0
    assert decision(result, "301")["reason"] in ("HEALTHY", "NO_BENEFICIAL_MOVE", "CHANGE_CONFIRMED")

    # CCO-20 — the audit trail and the dashboard
    audit = _decisions(mesh, iid, execution_id="cco-exec-1", cell_id="301")[0]
    for stage in ("shares", "prediction", "safety", "decision", "intent", "action", "verification", "finalState"):
        assert audit[stage], stage
    assert audit["prediction"]["plan"]["moves"]["301"] == "DOWNTILT" and audit["safety"]["passed"]
    dash = ok(mesh[RAPP].get(f"/instances/{iid}/dashboard"))
    row = next(c for c in dash["cells"] if c["cellId"] == "301")
    assert row["digitalTilt"] == tilts[-1] and row["shareTrend"] and row["excessTrend"] and row["latestDecision"]


# ---------------------------------------------------------------- CCO-14, CCO-15

def test_cco14_weak_coverage_raises_power_and_cco15_pollution_pulls_in_the_neighbours(mesh, loaded_apps, monkeypatch):
    iid = scenario(mesh, loaded_apps, monkeypatch, {"rapp": "AUTONOMOUS"})["rapp"]["instanceId"]
    clock = Clock(mesh, loaded_apps).hour({"302": "WEAK_COVERAGE"})
    # CCO-14 — weak coverage in 302: more power on 302, nothing else
    result = evaluate(mesh, iid)
    assert result["plan"]["moves"] == {"302": "POWER_UP"} and decision(result, "302")["reason"] == "WEAK_COVERAGE@302"
    assert setting(mesh, "302") == (60, 44)
    action = ok(mesh["dme"].get(f"/actions/{decision(result, '302')['action']['actionId']}"))
    assert action["changes"][0]["managedFunctionRef"] == "NRSectorCarrier=302"
    clock.hour({"302": "WEAK_COVERAGE"})
    assert decision(evaluate(mesh, iid), "302")["outcome"] == "CONFIRMED"

    # CCO-15 — pilot pollution in 303 (no overshooter): its neighbours are downtilted; 303 itself isn't pushed
    clock.hour({"303": "PILOT_POLLUTION"})
    result = evaluate(mesh, iid)
    moves = result["plan"]["moves"]
    assert moves and set(moves) <= {"301", "302", "304"} and set(moves.values()) == {"DOWNTILT"}
    assert all(decision(result, c)["reason"] == "PILOT_POLLUTION@303" for c in moves)
    assert setting(mesh, "303") == (60, 43)


# ---------------------------------------------------------------- CCO-16

def test_cco16_a_change_set_that_made_the_cluster_worse_is_reverted(mesh, loaded_apps, monkeypatch):
    iid = scenario(mesh, loaded_apps, monkeypatch, {"rapp": "AUTONOMOUS"})["rapp"]["instanceId"]
    clock = Clock(mesh, loaded_apps).hour(OVERSHOOT_301)
    moved = evaluate(mesh, iid)["plan"]["moves"]
    assert "301" in moved
    # the next hour, coverage holes open in 302 and 303: the cluster is worse than before the change
    clock.hour({**OVERSHOOT_301, "302": "WEAK_COVERAGE", "303": "WEAK_COVERAGE"})
    result = evaluate(mesh, iid, correlation_id="cco-revert")
    assert result["kpi"]["verdict"] == "DEGRADED" and result["kpi"]["postObjective"] > result["kpi"]["preObjective"]
    for cell in moved:
        d = decision(result, cell)
        assert (d["decision"], d["reason"], d["outcome"]) == ("REVERT", "KPI_DEGRADED", "REVERTED")
        assert d["toSetting"] == {"digitalTilt": 60, "configuredMaxTxPower": 43} and d["verification"]["result"] == "VERIFIED"
        assert setting(mesh, cell) == (60, 43)
        action = ok(mesh["dme"].get(f"/actions/{d['action']['actionId']}"))
        assert action["sourceContext"]["reason"] == "REVERT:KPI_DEGRADED" and action["correlationId"] == "cco-revert"
        assert cell_state(mesh, iid, cell)["state"] == "STEADY"


# ---------------------------------------------------------------- CCO-17

def test_cco17_protected_cells_thin_samples_and_critical_alarms_block(mesh, loaded_apps, monkeypatch):
    iid = scenario(mesh, loaded_apps, monkeypatch, {"rapp": "AUTONOMOUS"},
                   guards={"301": {"cellClass": "EMERGENCY"}, "302": {"incidentZone": "flood-7"}})["rapp"]["instanceId"]
    thin = {"MR.Total": 60, "MR.WeakRsrp": 1, "MR.Overshoot": 1, "MR.PilotPollution": 1, "MR.Overlap.301": 6,
            "CM.DigitalTilt": 60, "CM.ConfiguredMaxTxPower": 43}
    clock = Clock(mesh, loaded_apps).hour(OVERSHOOT_301, overrides={"303": thin})
    result = evaluate(mesh, iid)
    assert decision(result, "301")["reason"] == "SAFETY_BLOCKED:PROTECTED_CELL"      # emergency: never moved
    assert decision(result, "302")["reason"] == "SAFETY_BLOCKED:PROTECTED_CELL"      # incident zone
    assert decision(result, "303")["reason"] == "SAFETY_BLOCKED:INSUFFICIENT_SAMPLES"
    # with the overshooting cell itself held, no move left is worth its cost
    assert result["plan"]["moves"] == {} and setting(mesh, "301") == (60, 43)

    # an active critical alarm on the gNB holds every cell
    ok(mesh["ran-nf-oam"].post("/alarms/ingest", params={"source_alarm_id": "pa-1", "managed_element_ref": ME,
                                                         "severity": "critical", "probable_cause": "powerProblem"}))
    clock.hour(OVERSHOOT_301)
    result = evaluate(mesh, iid)
    assert not result["plan"]["moves"]
    assert all("CRITICAL_ALARM" in decision(result, c)["reason"] for c in CELLS)


# ---------------------------------------------------------------- CCO-18

def test_cco18_coordination_with_the_energy_saving_rapp(mesh, loaded_apps, monkeypatch):
    es_iid = es.ready(mesh, loaded_apps, monkeypatch)
    cells = ["101", "102", "103", "104"]   # the sample cluster mapped onto the EnergySaving rApp's cells
    iid = scenario(mesh, loaded_apps, monkeypatch, {"rapp": "AUTONOMOUS"}, me=es.ME, reset=False, cells=cells,
                   energySavingInstanceId=es_iid)["rapp"]["instanceId"]
    # EnergySaving sleeps 101 (101's neighbours: 102, 103)
    es_clock = es.Clock(mesh)
    es_clock.feed(65, c101=2, c102=40, c103=40, c104=40)
    assert es.decision(es.evaluate(mesh, es_iid), "101")["outcome"] == "EXECUTED"

    clock = Clock(mesh, loaded_apps, me=es.ME, cells=cells).hour({"102": "WEAK_COVERAGE"})
    result = evaluate(mesh, iid)
    assert "CELL_ASLEEP" in decision(result, "101")["reason"]
    assert "NEIGHBOUR_ASLEEP" in decision(result, "102")["reason"]   # a neighbour asleep distorts its coverage picture
    assert "NEIGHBOUR_ASLEEP" in decision(result, "103")["reason"]
    assert "102" not in result["plan"]["moves"] and setting(mesh, "102", es.ME) == (60, 43)

    # EnergySaving wakes 101: the cell and its neighbours wait out the after-wake window
    ok(mesh["energy-saving-rapp"].post(f"/instances/{es_iid}/cells/101/override", json={"operator": OPERATOR}))
    clock.hour({"102": "WEAK_COVERAGE"})
    result = evaluate(mesh, iid)
    assert "RECENTLY_WOKEN" in decision(result, "101")["reason"] and "RECENTLY_WOKEN" in decision(result, "102")["reason"]
    assert "NEIGHBOUR_ASLEEP" not in decision(result, "102")["reason"]


def test_cco18_coordination_with_the_mobility_rapp(mesh, loaded_apps, monkeypatch):
    relations = [{"relation": "301-302", "source": "301", "target": "302"}]
    m_iid = mro.scenario(mesh, loaded_apps, monkeypatch, {"rapp": "AUTONOMOUS"}, me=ME, relations=relations,
                         history={"301-302": "TOO_LATE"})["rapp"]["instanceId"]
    iid = scenario(mesh, loaded_apps, monkeypatch, {"rapp": "AUTONOMOUS"}, reset=False,
                   mobilityInstanceId=m_iid)["rapp"]["instanceId"]
    # the Mobility rApp raises 301→302's CIO: that relation is now OBSERVING
    mro.Clock(mesh, loaded_apps, me=ME).hour(hours=2, r301_302="TOO_LATE")
    assert mro.decision(mro.evaluate(mesh, m_iid), "301-302")["decision"] == "RAISE_CIO"
    Clock(mesh, loaded_apps).hour(OVERSHOOT_301)
    result = evaluate(mesh, iid)
    for cell in ("301", "302"):
        d = decision(result, cell)
        assert "MRO_OBSERVING" in d["reason"] and d["safety"]["blocks"][0]["detail"] == ["301-302"]
    assert "301" not in result["plan"]["moves"] and setting(mesh, "301") == (60, 43)


# ---------------------------------------------------------------- CCO-19

def test_cco19_shadow_recommends_and_assist_needs_approval(mesh, loaded_apps, monkeypatch):
    ids = {k: v["instanceId"] for k, v in scenario(mesh, loaded_apps, monkeypatch,
                                                   {"shadow": "SHADOW", "assist": "ASSIST"}).items()}
    clock = Clock(mesh, loaded_apps).hour(OVERSHOOT_301)
    d = decision(evaluate(mesh, ids["shadow"]), "301")
    assert (d["decision"], d["outcome"], d["intent"]["status"]) == ("DOWNTILT", "SHADOWED", "SHADOWED")
    assert setting(mesh, "301") == (60, 43)

    result = evaluate(mesh, ids["assist"])
    moves = result["plan"]["moves"]
    assert all(decision(result, c)["outcome"] == "AWAITING_APPROVAL" for c in moves) and setting(mesh, "301") == (60, 43)
    dispatch_id = decision(result, "301")["intent"]["dispatchId"]
    clock.hour(OVERSHOOT_301)
    assert {d["reason"] for d in evaluate(mesh, ids["assist"])["decisions"]} == {"AWAITING_APPROVAL"}
    ok(mesh["intent-service"].post(f"/autonomy-dispatches/{dispatch_id}/resolve",
                                   json={"regionScope": {"objectInstance": ME, "cells": CELLS}}))
    settled = ok(mesh[RAPP].post(f"/instances/{ids['assist']}/reconcile"))["settled"]
    assert settled[0]["outcomes"] == {c: "EXECUTED" for c in moves} and setting(mesh, "301") == (70, 43)


def test_cco19_a_failed_or_unverified_write_is_rolled_back(mesh, loaded_apps, monkeypatch):
    iid = scenario(mesh, loaded_apps, monkeypatch, {"rapp": "AUTONOMOUS"})["rapp"]["instanceId"]
    fault(mesh, "CommonBeamformingFunction=301", "RPC_ERROR")
    fault(mesh, "CommonBeamformingFunction=304", "IGNORE_WRITE")
    Clock(mesh, loaded_apps).hour(OVERSHOOT_301)
    result = evaluate(mesh, iid)
    assert result["plan"]["moves"] == {"301": "DOWNTILT", "304": "DOWNTILT"}
    failed, unverified = decision(result, "301"), decision(result, "304")
    assert (failed["action"]["status"], failed["outcome"]) == ("FAILED", "ACTION_FAILED_ROLLED_BACK")
    assert unverified["verification"]["result"] == "VERIFY_FAILED" and unverified["outcome"] == "VERIFY_FAILED_ROLLED_BACK"
    for d in (failed, unverified):
        assert d["finalState"] == {"state": "STEADY", "digitalTilt": 60, "configuredMaxTxPower": 43}
    assert (setting(mesh, "301"), setting(mesh, "304")) == ((60, 43), (60, 43))
    assert ok(mesh[RAPP].get(f"/instances/{iid}"))["observing"] is None


def test_the_committed_csar_is_built_from_the_sample_sources():
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_csar", SMO_ROOT / "samples" / "build_csar.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    assert builder.build_bytes("coverage-optimization-rapp") == CSAR.read_bytes(), \
        "rebuild: python3 smo/samples/build_csar.py coverage-optimization-rapp"


@pytest.fixture(autouse=True)
def _no_backoff(loaded_apps, monkeypatch):
    monkeypatch.setattr(loaded_apps["ran-nf-oam"], "_sleep", lambda s: None)
