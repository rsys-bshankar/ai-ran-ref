"""Wave 10.4 — the Traffic Steering rApp's integration tests
(docs/ROADMAP.md Wave 10.4, W10.4-10): TS-01..TS-20, run against the
in-process mesh with the real onboarding, AIMgF / MLMR / MLLF / NFO, DME,
Intent Service, SA SMOS, RAN NF OAM and mock O1 adaptor.

The live PM comes from the cells' current O1 steering (each relation's CIO
and each cell's reselection priority towards the other layer, read back from
the mock adaptor) through the sample's load model. So every step the rApp
takes is measured in the next hour's PM."""

import energy_saving_env as es
import mobility_env as mro
import pytest
from traffic_env import (CSAR, ME, OPERATOR, RAPP, SMO_ROOT, Clock, cell_state, cio, decision, evaluate, fault, ok, priority,
                         scenario)

HOT_401 = {"401": "HOTSPOT"}
CELLS = ["401", "402", "411", "412"]


def _decisions(mesh, iid, **params):
    return ok(mesh[RAPP].get(f"/instances/{iid}/decisions", params=params))["items"]


def _relations(mesh, iid):
    return {r["relation"]: r for r in ok(mesh[RAPP].get(f"/instances/{iid}/relations"))["items"]}


# ---------------------------------------------------------------- TS-01..14, TS-20

def test_ts01_to_ts14_lifecycle_connected_and_idle_steering_kpi_revert_and_release(mesh, loaded_apps, monkeypatch):
    out = scenario(mesh, loaded_apps, monkeypatch, {"rapp": "AUTONOMOUS"})["rapp"]
    iid = out["instanceId"]
    # TS-01 — onboarded AVAILABLE with execution modes, autonomy modes and runtime profiles
    status = ok(mesh["onboarding"].get(f"/packages/{ok(mesh[RAPP].get(f'/instances/{iid}'))['packageId']}/onboarding-status"))
    assert status["state"] == "AVAILABLE" and len(status["aiCapabilities"]["executionModes"]) == 4
    # TS-02 — LOAD_PERFORMANCE (O1 PM → RAN NF OAM → DME) and the Digital Twin's LOAD_PERFORMANCE_SIM
    inst = ok(mesh[RAPP].get(f"/instances/{iid}"))
    assert {s: d["dataset"] for s, d in inst["datasets"].items()} == {
        "TRAINING": "LOAD_PERFORMANCE", "INFERENCE": "LOAD_PERFORMANCE", "EMULATION": "LOAD_PERFORMANCE_SIM"}
    # TS-03..07 — transfer per step learned, forecast validated, every hotspot steered in emulation, promoted, ACTIVE
    transfer = out["train"]["metrics"]["transfer"]
    assert abs(transfer["CONNECTED"] - 0.03) < 0.006 and abs(transfer["IDLE"] - 0.06) < 0.012
    assert out["validate"]["passed"] and out["emulate"]["passed"]
    assert out["emulate"]["metrics"]["steeringAccuracy"] == 1.0 and out["emulate"]["metrics"]["falseActions"] == 0
    assert out["deploy"]["lifecycle"]["runtimeLifecycleState"] == "ACTIVE"

    # TS-08 — 401 is a hotspot: one connected step towards its least-loaded neighbour, 402 (same layer),
    # through Intent → O1-CM handler → DME, read back
    clock = Clock(mesh, loaded_apps).hour(HOT_401)
    result = evaluate(mesh, iid, correlation_id="ts-exec-1")
    d = decision(result, "401")
    assert (d["decision"], d["managedRef"], d["fromValue"], d["toValue"]) == ("STEER_CONNECTED", "NRCellRelation=401-402", 0, 2)
    assert d["reason"].startswith("CONGESTED:") and d["prediction"]["plan"]["targetForecastAfter"] <= 55
    assert d["intent"]["status"] == "DISPATCHED" and d["outcome"] == "EXECUTED" and d["verification"]["result"] == "VERIFIED"
    assert ok(mesh["dme"].get(f"/actions/{d['action']['actionId']}"))["changes"][0]["managedFunctionRef"] == "NRCellRelation=401-402"
    assert cio(mesh, "401", "402") == 2
    assert all(decision(result, c)["reason"] == "NOT_CONGESTED" for c in ("402", "411", "412"))
    # the shared-CIO arbitration: the relation is published as OBSERVING for the Mobility rApp
    assert _relations(mesh, iid)["401-402"]["state"] == "OBSERVING"

    # TS-09 — no new PM: still observing
    assert decision(evaluate(mesh, iid), "401")["reason"] == "OBSERVING"
    # TS-10 — an hour later 401's measured score is below its forecast and 402 is fine: CONFIRMED
    clock.hour(HOT_401)
    d = decision(evaluate(mesh, iid), "401")
    assert (d["reason"], d["outcome"], d["kpi"]["verdict"]) == ("CHANGE_CONFIRMED", "CONFIRMED", "IMPROVED_OR_EQUAL")
    assert d["kpi"]["postSource"] < d["kpi"]["preForecast"] and cell_state(mesh, iid, "401")["state"] == "STEADY"
    assert _relations(mesh, iid)["401-402"] == {"relation": "401-402", "source": "401", "target": "402", "cioBias": 2,
                                                "state": "STEADY"}

    # TS-11 — still congested: now 411 (the other layer) is the least loaded, so idle UEs are steered by priority
    clock.hour(HOT_401)
    d = decision(evaluate(mesh, iid), "401")
    assert (d["decision"], d["managedRef"], d["fromValue"], d["toValue"]) == ("STEER_IDLE", "NRFreqRelation=401-F2100", 5, 6)
    assert priority(mesh, "401", "F2100") == 6 and d["targets"] == ["411"]
    clock.hour(HOT_401)
    assert decision(evaluate(mesh, iid), "401")["outcome"] == "CONFIRMED"

    # TS-12 — both neighbours are now close to their limit: no target may end above 55
    clock.hour(HOT_401)
    d = decision(evaluate(mesh, iid), "401")
    assert d["reason"] == "NO_ELIGIBLE_TARGET"
    assert {r["reason"] for r in d["prediction"]["plan"]["rejected"]} == {"TARGET_CAPACITY"}

    # TS-13 — later, a second CIO step (4 dB) causes too-early handovers: reverted straight through DME
    clock.hour(HOT_401)
    evaluate(mesh, iid)
    clock.hour(HOT_401)
    d = decision(evaluate(mesh, iid, correlation_id="ts-exec-2"), "401")
    assert (d["decision"], d["toValue"]) == ("STEER_CONNECTED", 4)
    clock.hour(HOT_401)
    d = decision(evaluate(mesh, iid, correlation_id="ts-revert"), "401")
    assert (d["decision"], d["reason"], d["outcome"]) == ("REVERT", "KPI_DEGRADED:HO_FAILURES", "REVERTED")
    assert d["kpi"]["postHoFail"] > d["kpi"]["preHoFail"] + 2 and cio(mesh, "401", "402") == 2
    action = ok(mesh["dme"].get(f"/actions/{d['action']['actionId']}"))
    assert action["sourceContext"]["reason"] == "REVERT:KPI_DEGRADED" and action["correlationId"] == "ts-revert"

    # TS-14 — the hotspot is gone and the evening load falls: the steering is released step by step
    released = []
    for _ in range(3):
        clock.hour()
        d = decision(evaluate(mesh, iid), "401")
        if d["decision"].startswith("RELEASE_"):
            assert d["reason"] == "LOAD_RELIEVED" and d["outcome"] == "EXECUTED"
            released.append(d["decision"])
    assert released == ["RELEASE_CONNECTED", "RELEASE_IDLE"]
    assert (cio(mesh, "401", "402"), priority(mesh, "401", "F2100")) == (0, 5)
    assert cell_state(mesh, iid, "401")["steering"] == {"cio": {}, "prio": {}}

    # TS-20 — the audit trail and the dashboard
    audit = _decisions(mesh, iid, execution_id="ts-exec-1", cell_id="401")[0]
    for stage in ("score", "forecast", "prediction", "safety", "decision", "intent", "action", "verification", "finalState"):
        assert audit[stage] is not None, stage
    assert audit["prediction"]["model"]["band"] == "CONGESTED" and audit["safety"]["passed"]
    dash = ok(mesh[RAPP].get(f"/instances/{iid}/dashboard"))
    row = next(c for c in dash["cells"] if c["cellId"] == "401")
    assert row["layer"] == "F3500" and row["scoreTrend"] and row["latestDecision"]


# ---------------------------------------------------------------- TS-15

def test_ts15_a_step_that_congests_its_target_is_reverted(mesh, loaded_apps, monkeypatch):
    iid = scenario(mesh, loaded_apps, monkeypatch, {"rapp": "AUTONOMOUS"})["rapp"]["instanceId"]
    clock = Clock(mesh, loaded_apps).hour(HOT_401)
    assert decision(evaluate(mesh, iid), "401")["managedRef"] == "NRCellRelation=401-402"
    clock.hour({**HOT_401, "402": "HOTSPOT"})             # 402 picks up its own hotspot on top of 401's load
    d = decision(evaluate(mesh, iid, correlation_id="ts-revert-target"), "401")
    assert (d["decision"], d["outcome"]) == ("REVERT", "REVERTED") and "TARGET_CONGESTED" in d["kpi"]["causes"]
    assert d["kpi"]["postTargets"]["402"] >= 70 and cio(mesh, "401", "402") == 0
    assert cell_state(mesh, iid, "401")["steering"]["cio"] == {}


# ---------------------------------------------------------------- TS-16, TS-17

def test_ts16_protected_targets_thin_samples_and_critical_alarms(mesh, loaded_apps, monkeypatch):
    iid = scenario(mesh, loaded_apps, monkeypatch, {"rapp": "AUTONOMOUS"},
                   guards={"402": {"incidentZone": "flood-7"}, "412": {"cellClass": "EMERGENCY"}})["rapp"]["instanceId"]
    clock = Clock(mesh, loaded_apps).hour(HOT_401)
    result = evaluate(mesh, iid)
    d = decision(result, "401")
    # 402 is in an incident zone: not a target; the load goes to the other layer in idle mode
    assert {"target": "402", "reason": "TARGET_PROTECTED"} in d["safety"]["excluded"]
    assert (d["decision"], d["managedRef"]) == ("STEER_IDLE", "NRFreqRelation=401-F2100")
    assert decision(result, "402")["reason"] == "SAFETY_BLOCKED:PROTECTED_CELL"
    assert decision(result, "412")["reason"] == "SAFETY_BLOCKED:PROTECTED_CELL"

    thin = {"RRU.PrbTotDl": 90, "RRC.ConnMean": 180, "DRB.UEThpDl": 5, "PM.Samples": 4, "HO.Att.401": 10}
    ok(mesh["ran-nf-oam"].post("/alarms/ingest", params={"source_alarm_id": "pa-1", "managed_element_ref": ME,
                                                         "severity": "critical", "probable_cause": "powerProblem"}))
    clock.hour(HOT_401, overrides={"411": thin}, hours=2)
    result = evaluate(mesh, iid)
    assert decision(result, "401")["reason"] in ("CHANGE_CONFIRMED", "KPI_DEGRADED:TARGET_CONGESTED")
    clock.hour(HOT_401, overrides={"411": thin})
    result = evaluate(mesh, iid)
    assert "CRITICAL_ALARM" in decision(result, "401")["reason"]
    assert decision(result, "411")["reason"] == "SAFETY_BLOCKED:CRITICAL_ALARM,INSUFFICIENT_SAMPLES"


def test_a_cell_alarm_excludes_that_cell_as_a_target_only(mesh, loaded_apps, monkeypatch):
    """W10-alarm-cellref: a critical alarm raised on 402 holds 402 and makes
    it no target, while 401 still steers — to the other layer, as when 402
    is protected — instead of the whole gNB being held."""
    iid = scenario(mesh, loaded_apps, monkeypatch, {"rapp": "AUTONOMOUS"})["rapp"]["instanceId"]
    alarm = ok(mesh["ran-nf-oam"].post("/alarms/ingest", params={
        "source_alarm_id": "rru-402", "managed_element_ref": ME, "severity": "critical",
        "probable_cause": "equipmentMalfunction", "managed_function_ref": "NRCellDU=402"}))
    Clock(mesh, loaded_apps).hour(HOT_401)
    result = evaluate(mesh, iid)

    d = decision(result, "401")
    assert {"target": "402", "reason": "TARGET_CRITICAL_ALARM"} in d["safety"]["excluded"]
    assert (d["decision"], d["managedRef"]) == ("STEER_IDLE", "NRFreqRelation=401-F2100")
    assert d["safety"]["criticalAlarmIds"] == []
    d402 = decision(result, "402")
    assert "CRITICAL_ALARM" in d402["reason"] and d402["safety"]["criticalAlarmIds"] == [alarm["alarmId"]]


def test_ts17_bounds_mlb_disallowed_relations_and_the_target_capacity_limit(mesh, loaded_apps, monkeypatch):
    iid = scenario(mesh, loaded_apps, monkeypatch, {"rapp": "AUTONOMOUS"})["rapp"]["instanceId"]
    # the operator has already pushed 401's idle UEs to the other layer (priority 7) and barred load balancing on 401→402
    ok(mesh["ran-nf-oam"].post("/config-jobs", json={"requestedBy": "noc", "scope": "cell", "changes": [
        {"managedElementRef": ME, "className": "NRFreqRelation", "managedFunctionRef": "NRFreqRelation=401-F2100",
         "attributeChanges": {"cellReselectionPriority": "7"}},
        {"managedElementRef": ME, "className": "NRCellRelation", "managedFunctionRef": "NRCellRelation=401-402",
         "attributeChanges": {"isMLBAllowed": "false"}}]}))
    Clock(mesh, loaded_apps).hour(HOT_401)
    d = decision(evaluate(mesh, iid), "401")
    excluded = d["safety"]["excluded"]
    assert {"layer": "F2100", "reason": "IDLE_AT_BOUND"} in excluded
    assert {"target": "402", "knob": "CONNECTED", "reason": "MLB_NOT_ALLOWED"} in excluded
    # what is left is connected steering towards 411, but 411 already carries 401's idle load: it would end above 55
    assert [c["ref"] for c in d["safety"]["candidates"]] == ["NRCellRelation=401-411"]
    assert d["reason"] == "NO_ELIGIBLE_TARGET"
    assert [(r["target"], r["reason"]) for r in d["prediction"]["plan"]["rejected"]] == [("411", "TARGET_CAPACITY")]
    assert (cio(mesh, "401", "402"), cio(mesh, "401", "411"), priority(mesh, "401", "F2100")) == (0, 0, 7)


# ---------------------------------------------------------------- TS-18

def test_ts18_coordination_with_the_energy_saving_rapp(mesh, loaded_apps, monkeypatch):
    es_iid = es.ready(mesh, loaded_apps, monkeypatch)
    cells = ["101", "102", "103", "104"]   # the sample cluster mapped onto the EnergySaving rApp's cells
    iid = scenario(mesh, loaded_apps, monkeypatch, {"rapp": "AUTONOMOUS"}, me=es.ME, reset=False, cells=cells,
                   energySavingInstanceId=es_iid)["rapp"]["instanceId"]
    es_clock = es.Clock(mesh)
    es_clock.feed(65, c101=2, c102=40, c103=40, c104=40)
    es.executed(mesh, es.evaluate(mesh, es_iid), "101")      # 101 asleep

    # 102 (neighbours 101, same layer, and 104, the other layer) is a hotspot
    Clock(mesh, loaded_apps, me=es.ME, cells=cells).hour({"102": "HOTSPOT"})
    result = evaluate(mesh, iid)
    d = decision(result, "102")
    assert {"target": "101", "reason": "TARGET_ASLEEP"} in d["safety"]["excluded"]
    assert (d["decision"], d["managedRef"]) == ("STEER_IDLE", "NRFreqRelation=102-F2100")
    assert "CELL_ASLEEP" in decision(result, "101")["reason"]


def test_ts18_two_way_cio_arbitration_with_the_mobility_rapp(mesh, loaded_apps, monkeypatch):
    relations = [{"relation": "401-402", "source": "401", "target": "402"}]
    # (a) the Mobility rApp is observing a CIO change on 401→402: Traffic Steering leaves that relation alone
    m_iid = mro.scenario(mesh, loaded_apps, monkeypatch, {"rapp": "AUTONOMOUS"}, me=ME, relations=relations,
                         history={"401-402": "TOO_LATE"})["rapp"]["instanceId"]
    iid = scenario(mesh, loaded_apps, monkeypatch, {"rapp": "AUTONOMOUS"}, reset=False, mobilityInstanceId=m_iid)["rapp"]["instanceId"]
    m_clock = mro.Clock(mesh, loaded_apps, me=ME).hour(hours=2, r401_402="TOO_LATE")
    assert mro.decision(mro.evaluate(mesh, m_iid), "401-402")["decision"] == "RAISE_CIO"
    clock = Clock(mesh, loaded_apps).hour(HOT_401)
    d = decision(evaluate(mesh, iid), "401")
    assert {"target": "402", "knob": "CONNECTED", "reason": "MRO_OBSERVING"} in d["safety"]["excluded"]
    assert d["decision"] == "STEER_IDLE" and cio(mesh, "401", "402") == 2          # the Mobility rApp's +2, untouched

    # (b) the other way: once the Mobility change is confirmed, Traffic Steering may step the relation's CIO.
    # A Mobility instance that coordinates with it then holds that relation while it is observed.
    m_clock.hour(r401_402="HEALTHY")
    assert mro.decision(mro.evaluate(mesh, m_iid), "401-402")["outcome"] == "CONFIRMED"
    clock.hour(HOT_401)
    assert decision(evaluate(mesh, iid), "401")["outcome"] == "CONFIRMED"
    clock.hour(HOT_401)
    d = decision(evaluate(mesh, iid), "401")
    assert (d["decision"], d["managedRef"], d["fromValue"], d["toValue"]) == ("STEER_CONNECTED", "NRCellRelation=401-402", 2, 4)
    assert _relations(mesh, iid)["401-402"]["state"] == "OBSERVING"
    package_id = ok(mesh["mobility-optimization-rapp"].get(f"/instances/{m_iid}"))["packageId"]
    m2 = mro.create_instance(mesh, package_id, "AUTONOMOUS", ME, relations, trafficSteeringInstanceId=iid)
    ok(mesh["mobility-optimization-rapp"].post(f"/instances/{m2}/start"))
    mro.report(mesh, loaded_apps, {"401-402": "TOO_LATE"}, mro.HISTORY_START, 72, ME)
    mro.lifecycle(mesh, m2)
    m_clock.hour(hours=2, r401_402="TOO_LATE")
    assert mro.decision(mro.evaluate(mesh, m2), "401-402")["reason"] == "SAFETY_BLOCKED:MLB_OBSERVING"
    assert cio(mesh, "401", "402") == 4


# ---------------------------------------------------------------- TS-19

def test_ts19_shadow_recommends_and_assist_needs_approval(mesh, loaded_apps, monkeypatch):
    ids = {k: v["instanceId"] for k, v in scenario(mesh, loaded_apps, monkeypatch, {"shadow": "SHADOW", "assist": "ASSIST"}).items()}
    clock = Clock(mesh, loaded_apps).hour(HOT_401)
    d = decision(evaluate(mesh, ids["shadow"]), "401")
    assert (d["decision"], d["outcome"], d["intent"]["status"]) == ("STEER_CONNECTED", "SHADOWED", "SHADOWED")
    assert cio(mesh, "401", "402") == 0

    d = decision(evaluate(mesh, ids["assist"]), "401")
    assert d["outcome"] == "AWAITING_APPROVAL" and cio(mesh, "401", "402") == 0
    clock.hour(HOT_401)
    assert {x["reason"] for x in evaluate(mesh, ids["assist"])["decisions"]} == {"AWAITING_APPROVAL"}
    ok(mesh["intent-service"].post(f"/autonomy-dispatches/{d['intent']['dispatchId']}/resolve",
                                   json={"regionScope": {"objectInstance": ME, "cells": ["401-402"]}}))
    settled = ok(mesh[RAPP].post(f"/instances/{ids['assist']}/reconcile"))["settled"]
    assert settled[0]["outcomes"] == {"401": "EXECUTED"} and cio(mesh, "401", "402") == 2


def test_ts19_a_failed_or_unverified_write_is_rolled_back(mesh, loaded_apps, monkeypatch):
    ids = {k: v["instanceId"] for k, v in scenario(mesh, loaded_apps, monkeypatch,
                                                   {"first": "AUTONOMOUS", "second": "AUTONOMOUS"}).items()}
    iid = ids["first"]
    fault(mesh, "NRCellRelation=401-402", "RPC_ERROR")
    Clock(mesh, loaded_apps).hour(HOT_401)
    d = decision(evaluate(mesh, iid), "401")
    assert (d["action"]["status"], d["outcome"]) == ("FAILED", "ACTION_FAILED_ROLLED_BACK")
    assert cio(mesh, "401", "402") == 0 and cell_state(mesh, iid, "401")["state"] == "STEADY"
    assert cell_state(mesh, iid, "401")["steering"] == {"cio": {}, "prio": {}}

    # a second instance on the same cluster: the write is accepted but never takes
    fault(mesh, "NRCellRelation=401-402", "IGNORE_WRITE")
    d = decision(evaluate(mesh, ids["second"]), "401")
    assert d["verification"]["result"] == "VERIFY_FAILED" and d["outcome"] == "VERIFY_FAILED_ROLLED_BACK"
    assert cio(mesh, "401", "402") == 0


def test_the_committed_csar_is_built_from_the_sample_sources():
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_csar", SMO_ROOT / "samples" / "build_csar.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    assert builder.build_bytes("traffic-steering-rapp") == CSAR.read_bytes(), \
        "rebuild: python3 smo/samples/build_csar.py traffic-steering-rapp"


@pytest.fixture(autouse=True)
def _no_backoff(loaded_apps, monkeypatch):
    monkeypatch.setattr(loaded_apps["ran-nf-oam"], "_sleep", lambda s: None)
