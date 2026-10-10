"""Wave 10.2 — the Mobility Optimization rApp end to end
(HISTORY.md Wave 10.2, W10.2-10): MRO-01..MRO-20, run against
the real services in the in-process mesh.

The chain under test:
  * the package is onboarded from samples/mobility-optimization-rapp.csar;
  * per-relation handover PM goes through RAN NF OAM into DME;
  * the model runs the governed lifecycle;
  * CIO changes are enacted per autonomy mode, read back, KPI-verified,
    and reverted or rolled back when needed, coordinated with the
    EnergySaving rApp.

Run with: PYTHONPATH=shared pytest tests_integration/test_mobility_optimization_rapp.py -q
"""

import pytest

import energy_saving_env as es
from energy_saving_env import ok
from mobility_env import (CSAR, ME, OPERATOR, RAPP, RELATIONS, SMO_ROOT, Clock, cio, decision, evaluate, fault, relation_state,
                          scenario)

ZERO, PLUS2, MINUS2 = "[0, 0, 0, 0, 0, 0]", "[2, 2, 2, 2, 2, 2]", "[-2, -2, -2, -2, -2, -2]"
ALL_LATE = {"r201_202": "HEALTHY", "r201_203": "TOO_LATE", "r202_203": "HEALTHY", "r203_204": "HEALTHY"}


def _decisions(mesh, iid, **params):
    return ok(mesh[RAPP].get(f"/instances/{iid}/decisions", params=params))["items"]


# ---------------------------------------------------------------- MRO-01..13, MRO-20

def test_mro01_to_mro13_lifecycle_dmro_bounded_cio_steps_and_kpi_confirmation(mesh, loaded_apps, monkeypatch):
    """MRO-01 to MRO-13 end to end: onboarding and datasets; training, validation, emulation, promotion; the DMRO bounds imposed on the gNB and
    read back; too-late failures raising and too-early lowering the CIO through Intent, the O1-CM handler and DME; and the KPI-verified
    confirmation.
    """
    out = scenario(mesh, loaded_apps, monkeypatch, {"rapp": "AUTONOMOUS"})["rapp"]
    iid, model_id = out["instanceId"], out["modelId"]
    inst = ok(mesh[RAPP].get(f"/instances/{iid}"))

    # MRO-01 — onboarded AVAILABLE with execution modes, autonomy modes and runtime profiles
    status = ok(mesh["onboarding"].get(f"/packages/{inst['packageId']}/onboarding-status"))
    assert status["state"] == "AVAILABLE" and status["aiCapabilities"]["autonomyModes"] == ["SHADOW", "ASSIST", "AUTONOMOUS"]
    assert status["aiCapabilities"]["runtimeProfiles"]["INFERENCE"] == {"cpu": 1, "memory": "2Gi", "gpu": 0}
    # MRO-02 — HO_PERFORMANCE (O1 PM → RAN NF OAM → DME) and the Digital Twin's HO_PERFORMANCE_SIM
    assert {s: d["dataset"] for s, d in inst["datasets"].items()} == {
        "TRAINING": "HO_PERFORMANCE", "INFERENCE": "HO_PERFORMANCE", "EMULATION": "HO_PERFORMANCE_SIM"}
    assert ok(mesh["dme"].get(f"/data-jobs/{inst['datasets']['TRAINING']['dataJobId']}/records"))["total"] == 72 * 4
    # MRO-03..07 — trained, validated, emulated (every injected fault steered the right way), promoted, ACTIVE
    assert out["train"]["status"] == "FINISHED" and out["validate"]["passed"]
    assert out["emulate"]["metrics"]["directionAccuracy"] == 1.0 and out["emulate"]["metrics"]["falseActions"] == 0
    lifecycle = ok(mesh["aimgf"].get(f"/models/{model_id}/lifecycle"))
    assert (lifecycle["modelLifecycleState"], lifecycle["runtimeLifecycleState"]) == ("PROMOTED", "ACTIVE")
    # MRO-08 — DMRO bounds imposed on the gNB and read back (D10.2-1)
    assert out["deploy"]["dmro"]["verification"] == "VERIFIED"
    dmro = ok(mesh["mock-o1-adaptor"].get(f"/objects/{ME}", params={"function_ref": f"DMROFunction={ME}"}))["attributes"]
    assert (dmro["maximumDeviationHoTriggerLow"], dmro["maximumDeviationHoTriggerHigh"]) == ("-6", "6")

    # MRO-09 — too-late failures raise CIO, too-early lower it, through Intent → O1-CM handler → DME
    clock = Clock(mesh, loaded_apps).hour(r201_202="HEALTHY", r201_203="TOO_LATE", r202_203="TOO_EARLY", r203_204="HEALTHY")
    result = evaluate(mesh, iid, correlation_id="mro-exec-1")
    late, early = decision(result, "201-203"), decision(result, "202-203")
    assert (late["decision"], late["reason"], late["fromCio"], late["toCio"]) == ("RAISE_CIO", "TOO_LATE_FAILURES", 0, 2)
    assert (early["decision"], early["toCio"]) == ("LOWER_CIO", -2)
    assert decision(result, "201-202")["reason"] == "HEALTHY"
    assert late["intent"]["status"] == "DISPATCHED"
    action = ok(mesh["dme"].get(f"/actions/{late['action']['actionId']}"))
    assert action["changes"][0]["managedFunctionRef"] == "NRCellRelation=201-203" and action["status"] == "COMPLETED"
    # MRO-10 — read back over NETCONF get-config: all six QOffsetRange entries
    assert (cio(mesh, "201-203"), cio(mesh, "202-203"), cio(mesh, "201-202")) == (PLUS2, MINUS2, ZERO)
    assert late["verification"]["result"] == "VERIFIED" and relation_state(mesh, iid, "201-203")["state"] == "OBSERVING"

    # MRO-11 — no new PM yet: the change is still being observed, nothing else happens
    assert decision(evaluate(mesh, iid), "201-203")["reason"] == "OBSERVING"
    # MRO-12 — an hour later the failure rate has dropped: the change is CONFIRMED
    clock.hour(**ALL_LATE | {"r201_203": "HEALTHY"})
    d = decision(evaluate(mesh, iid), "201-203")
    assert (d["reason"], d["outcome"], d["kpi"]["verdict"]) == ("CHANGE_CONFIRMED", "CONFIRMED", "IMPROVED_OR_EQUAL")
    assert relation_state(mesh, iid, "201-203")["state"] == "STEADY"

    # MRO-13 — 2 dB steps, each confirmed before the next, up to baseline + 6 dB and no further
    # (two hours of sustained failures each time: a one-hour spike straight after a healthy hour is
    # predicted to partly revert, and stays in the hold zone)
    for expected in (4, 6):
        clock.hour(hours=2, **ALL_LATE)
        assert decision(evaluate(mesh, iid), "201-203")["toCio"] == expected
        clock.hour(**ALL_LATE | {"r201_203": "HEALTHY"})
        assert decision(evaluate(mesh, iid), "201-203")["outcome"] == "CONFIRMED"
    clock.hour(hours=2, **ALL_LATE)
    assert decision(evaluate(mesh, iid), "201-203")["reason"] == "AT_BOUND:TOO_LATE"
    assert cio(mesh, "201-203") == "[6, 6, 6, 6, 6, 6]"

    # MRO-20 — the audit trail and the dashboard
    audit = _decisions(mesh, iid, execution_id="mro-exec-1", relation="201-203")[0]
    for stage in ("prediction", "safety", "decision", "intent", "action", "verification", "finalState"):
        assert audit[stage], stage
    assert audit["prediction"]["model"]["cause"] == "TOO_LATE"
    dash = ok(mesh[RAPP].get(f"/instances/{iid}/dashboard"))
    row = next(r for r in dash["relations"] if r["relation"] == "201-203")
    assert row["cio"] == 6 and row["rateTrend"] and row["latestDecision"]["reason"] == "AT_BOUND:TOO_LATE"


# ---------------------------------------------------------------- MRO-14, MRO-15

def test_mro14_ping_pong_and_wrong_cell_lower_and_mro15_kpi_degradation_reverts(mesh, loaded_apps, monkeypatch):
    """MRO-14: ping-pong lowers the CIO by 2 dB and wrong-cell failures by 1 dB. MRO-15: a relation that got worse after its change is reverted
    through DME, read back, while the other relation's fix is confirmed.
    """
    iid = scenario(mesh, loaded_apps, monkeypatch, {"rapp": "AUTONOMOUS"})["rapp"]["instanceId"]
    gen = loaded_apps[RAPP].producer
    clock = Clock(mesh, loaded_apps).hour(hours=2, r201_202="WRONG_CELL", r201_203="HEALTHY", r202_203="HEALTHY",
                                          r203_204="PING_PONG")
    result = evaluate(mesh, iid)
    # MRO-14 — ping-pong lowers by 2 dB, wrong-cell by 1 dB
    assert (decision(result, "203-204")["reason"], decision(result, "203-204")["toCio"]) == ("PING_PONG_FAILURES", -2)
    assert (decision(result, "201-202")["reason"], decision(result, "201-202")["toCio"]) == ("WRONG_CELL_FAILURES", -1)

    # MRO-15 — the ping-pong relation got worse after its change: reverted straight through DME, read back
    worse = {**gen.relation_counters("203-204", clock.now, "PING_PONG"), "MM.HoPingPong": 40}
    clock.hour(overrides={"203-204": worse}, r201_202="HEALTHY", r201_203="HEALTHY", r202_203="HEALTHY", r203_204="PING_PONG")
    result = evaluate(mesh, iid, correlation_id="mro-revert")
    d = decision(result, "203-204")
    assert (d["decision"], d["reason"], d["outcome"], d["toCio"]) == ("REVERT_CIO", "KPI_DEGRADED", "REVERTED", 0)
    assert d["kpi"]["verdict"] == "DEGRADED" and d["kpi"]["postRate"] > d["kpi"]["preRate"]
    assert cio(mesh, "203-204") == ZERO and d["verification"]["result"] == "VERIFIED"
    action = ok(mesh["dme"].get(f"/actions/{d['action']['actionId']}"))
    assert action["sourceContext"]["reason"] == "REVERT:KPI_DEGRADED" and action["correlationId"] == "mro-revert"
    assert decision(result, "201-202")["outcome"] == "CONFIRMED"  # the wrong-cell fix held


# ---------------------------------------------------------------- MRO-16

def test_mro16_guards_block_ho_disallowed_protected_cells_and_thin_samples(mesh, loaded_apps, monkeypatch):
    """MRO-16: handover not allowed, an emergency or incident-zone cell at either end and thin samples each block a relation, every blocking guard
    is recorded, and no CIO is written.
    """
    iid = scenario(mesh, loaded_apps, monkeypatch, {"rapp": "AUTONOMOUS"},
                   guards={"204": {"cellClass": "EMERGENCY"}, "202": {"incidentZone": "flood-7"}})["rapp"]["instanceId"]
    ok(mesh["ran-nf-oam"].post("/config-jobs", json={"requestedBy": "noc", "scope": "cell", "changes": [
        {"managedElementRef": ME, "className": "NRCellRelation", "managedFunctionRef": "NRCellRelation=201-202",
         "attributeChanges": {"isHOAllowed": "false"}}]}))
    thin = {"MM.HoExeAtt": 30, "MM.HoFailTooLate": 5}
    Clock(mesh, loaded_apps).hour(overrides={"201-203": thin}, r201_202="TOO_LATE", r201_203="TOO_LATE",
                                  r202_203="TOO_LATE", r203_204="TOO_LATE")
    result = evaluate(mesh, iid)
    # every guard that blocks is recorded: 201-202 is also aimed at 202, which is in an incident zone
    assert decision(result, "201-202")["reason"] == "SAFETY_BLOCKED:HO_NOT_ALLOWED,PROTECTED_CELL"
    assert decision(result, "201-203")["reason"] == "SAFETY_BLOCKED:INSUFFICIENT_SAMPLES"
    assert decision(result, "202-203")["reason"] == "SAFETY_BLOCKED:PROTECTED_CELL"     # source in an incident zone
    assert decision(result, "203-204")["reason"] == "SAFETY_BLOCKED:PROTECTED_CELL"     # target is an emergency cell
    assert all(cio(mesh, r["relation"]) == ZERO for r in RELATIONS)


# ---------------------------------------------------------------- MRO-17

def test_mro17_coordination_with_the_energy_saving_rapp(mesh, loaded_apps, monkeypatch):
    """MRO-17: a relation towards a cell the EnergySaving rApp has asleep or in PRE_SLEEP is held, one towards an awake cell is tuned, and after
    EnergySaving wakes the cell the relation waits out the after-wake window.
    """
    es_iid = es.ready(mesh, loaded_apps, monkeypatch)
    relations = [{"relation": "102-101", "source": "102", "target": "101"},
                 {"relation": "104-103", "source": "104", "target": "103"},
                 {"relation": "102-104", "source": "102", "target": "104"}]
    iid = scenario(mesh, loaded_apps, monkeypatch, {"rapp": "AUTONOMOUS"}, me=es.ME, relations=relations, reset=False,
                   history={r["relation"]: "TOO_LATE" for r in relations}, energySavingInstanceId=es_iid)["rapp"]["instanceId"]
    # EnergySaving sleeps 101 and has 103 in PRE_SLEEP
    es_clock = es.Clock(mesh)
    es_clock.feed(65, c101=2, c102=40, c103=40, c104=40)
    es.executed(mesh, es.evaluate(mesh, es_iid), "101")
    es_clock.feed(5, c101=2, c102=40, c103=2, c104=40)
    es.evaluate(mesh, es_iid)
    assert es.cell_state(mesh, es_iid, "103")["state"] == "PRE_SLEEP"

    clock = Clock(mesh, loaded_apps, me=es.ME).hour(r102_101="TOO_LATE", r104_103="TOO_LATE", r102_104="TOO_LATE")
    result = evaluate(mesh, iid)
    assert decision(result, "102-101")["reason"] == "SAFETY_BLOCKED:TARGET_ASLEEP"   # asleep (O1 LOCKED, ES SLEEP)
    assert decision(result, "104-103")["reason"] == "SAFETY_BLOCKED:TARGET_ASLEEP"   # ES PRE_SLEEP
    assert decision(result, "102-104")["decision"] == "RAISE_CIO"

    # EnergySaving wakes 101: tuning towards it waits out the after-wake window
    ok(mesh["energy-saving-rapp"].post(f"/instances/{es_iid}/cells/101/override", json={"operator": OPERATOR}))
    clock.hour(r102_101="TOO_LATE", r104_103="TOO_LATE", r102_104="HEALTHY")
    assert decision(evaluate(mesh, iid), "102-101")["reason"] == "SAFETY_BLOCKED:TARGET_RECENTLY_WOKEN"


# ---------------------------------------------------------------- MRO-18

def test_mro18_shadow_recommends_and_assist_needs_approval(mesh, loaded_apps, monkeypatch):
    """MRO-18: SHADOW recommends and writes nothing; ASSIST waits for approval and enacts after the operator resolves the dispatch, and a rejected
    dispatch is never enacted.
    """
    ids = {k: v["instanceId"] for k, v in scenario(mesh, loaded_apps, monkeypatch,
                                                   {"shadow": "SHADOW", "assist": "ASSIST"}).items()}
    clock = Clock(mesh, loaded_apps).hour(**ALL_LATE)
    d = decision(evaluate(mesh, ids["shadow"]), "201-203")
    assert (d["decision"], d["outcome"], d["intent"]["status"]) == ("RAISE_CIO", "SHADOWED", "SHADOWED")
    assert cio(mesh, "201-203") == ZERO

    d = decision(evaluate(mesh, ids["assist"]), "201-203")
    assert (d["outcome"], d["intent"]["status"]) == ("AWAITING_APPROVAL", "AWAITING_SCOPE") and cio(mesh, "201-203") == ZERO
    ok(mesh["intent-service"].post(f"/autonomy-dispatches/{d['intent']['dispatchId']}/resolve",
                                   json={"regionScope": {"objectInstance": ME, "cells": ["201-203"]}}))
    settled = ok(mesh[RAPP].post(f"/instances/{ids['assist']}/reconcile"))["settled"]
    assert settled[0]["outcomes"] == {"201-203": "EXECUTED"} and cio(mesh, "201-203") == PLUS2

    clock.hour(hours=2, **ALL_LATE | {"r201_203": "HEALTHY", "r202_203": "TOO_EARLY"})
    d = decision(evaluate(mesh, ids["assist"]), "202-203")
    assert d["outcome"] == "AWAITING_APPROVAL"
    ok(mesh["intent-service"].post(f"/autonomy-dispatches/{d['intent']['dispatchId']}/reject",
                                   json={"rejectedBy": OPERATOR, "reason": "drive test pending"}))
    settled = ok(mesh[RAPP].post(f"/instances/{ids['assist']}/reconcile"))["settled"]
    assert settled[0]["outcomes"] == {"202-203": "REJECTED"} and cio(mesh, "202-203") == ZERO


# ---------------------------------------------------------------- MRO-19

def test_mro19_a_failed_or_unverified_cio_write_is_rolled_back(mesh, loaded_apps, monkeypatch):
    """MRO-19: a CIO write that fails and one that is accepted but never takes are each rolled back to the old CIO."""
    iid = scenario(mesh, loaded_apps, monkeypatch, {"rapp": "AUTONOMOUS"})["rapp"]["instanceId"]
    fault(mesh, "201-203", "RPC_ERROR")
    fault(mesh, "202-203", "IGNORE_WRITE")
    Clock(mesh, loaded_apps).hour(r201_202="HEALTHY", r201_203="TOO_LATE", r202_203="TOO_EARLY", r203_204="HEALTHY")
    result = evaluate(mesh, iid)
    failed, unverified = decision(result, "201-203"), decision(result, "202-203")
    assert (failed["action"]["status"], failed["outcome"]) == ("FAILED", "ACTION_FAILED_ROLLED_BACK")
    assert unverified["verification"]["result"] == "VERIFY_FAILED" and unverified["outcome"] == "VERIFY_FAILED_ROLLED_BACK"
    for d in (failed, unverified):
        assert d["finalState"] == {"state": "STEADY", "cio": 0}
    assert (cio(mesh, "201-203"), cio(mesh, "202-203")) == (ZERO, ZERO)


def test_the_committed_csar_is_built_from_the_sample_sources():
    """The committed package is exactly what `samples/build_csar.py` builds from the sample's sources now (signed with the demo key), so a source
    change without a rebuilt package fails here with the command to run.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_csar", SMO_ROOT / "samples" / "build_csar.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    assert builder.build_bytes("mobility-optimization-rapp") == CSAR.read_bytes(), \
        "rebuild: python3 smo/samples/build_csar.py mobility-optimization-rapp"


@pytest.fixture(autouse=True)
def _no_backoff(loaded_apps, monkeypatch):
    monkeypatch.setattr(loaded_apps["ran-nf-oam"], "_sleep", lambda s: None)
