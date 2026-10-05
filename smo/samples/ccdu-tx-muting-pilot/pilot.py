#!/usr/bin/env python3
"""CCDU TX-muting pilot rApp: demo steps 00-08 against a running stack.

    python3 pilot.py 00        # one step
    python3 pilot.py all       # every step in order

Design: smo/docs/design/CCDU_TX_MUTING_PILOT_HLD_LLD.md. Run it inside the compose network (from the r1-termination
container, as the DEMO_RUNBOOK.md demos do); helper scripts are in ./scripts. Ids are kept between steps in
$PILOT_STATE (default /tmp/ccdu-tx-muting-pilot.json).

The pilot is the rApp: it reads PM from DME, configuration and alarms from RAN NF OAM, decides with engine.py and
writes through DME /actions. RAN NF OAM dispatches the NETCONF edit-config to mock-o1-adaptor, which stands in for
the CCDU O1 Adaptor. Nothing below the O1 Adaptor (OID/UDP, OAMManager, MRU) is emulated.
"""

import datetime
import json
import os
import sys
import uuid

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import engine  # noqa: E402

RAPP_ID = "ccdu-tx-muting-pilot"
ME = os.environ.get("PILOT_ME", "ccdu-001")
CELL = os.environ.get("PILOT_CELL", "101")
MFR = f"NRCellDU={CELL}"
VENDOR = "radisys-ccdu"
ADAPTOR_URI = os.environ.get("PILOT_ADAPTOR_URI", "http://mock-o1-adaptor:8000/edit-config")
STATE_FILE = os.environ.get("PILOT_STATE", "/tmp/ccdu-tx-muting-pilot.json")
THRESHOLDS_FILE = os.environ.get("PILOT_THRESHOLDS", os.path.join(HERE, "thresholds.json"))

# rApp measurement name -> RAN NF OAM PM counter type (DME type RAN.PMCounters.<counter>)
COUNTERS = {"dlPrbUtilization": "DL_PRB_UTILIZATION", "rrcConnectedUeCount": "RRC_CONNECTED_UE",
            "mruSynchronizationState": "MRU_SYNC_STATE"}
TX_LEAVES = ("txMutingFeatureEnable", "txPathOffPattern", "txMutingActivation")


def call(verb: str, service: str, path: str, expect=(200, 201, 202, 204), **kw):
    resp = getattr(httpx, verb)(f"http://{service}:8000{path}", timeout=60.0, **kw)
    if resp.status_code not in expect:
        raise SystemExit(f"{verb.upper()} {service}{path} -> {resp.status_code}: {resp.text}")
    return resp.json() if resp.content else None


def show(label: str, value) -> None:
    print(f"  {label}: {value if isinstance(value, str) else json.dumps(value, default=str)}")


def now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def thresholds() -> dict:
    with open(THRESHOLDS_FILE) as f:
        cfg = json.load(f)
    engine.validate_thresholds(cfg)
    return cfg


def report_pm(prb: float, ue: int, mru_synchronized: bool = True) -> None:
    """What the CCDU's PM reporting would deliver: one sample per counter, time-stamped now."""
    ts = now().isoformat()
    for counter, value in ((COUNTERS["dlPrbUtilization"], prb), (COUNTERS["rrcConnectedUeCount"], ue),
                           (COUNTERS["mruSynchronizationState"], 1.0 if mru_synchronized else 0.0)):
        call("post", "ran-nf-oam", "/pm-reports", json={"managedElementRef": ME, "counterType": counter,
                                                        "measurements": [{"cellId": CELL, "value": value, "timestamp": ts}]})
    show("PM reported", {"dlPrbUtilization": prb, "rrcConnectedUeCount": ue, "mruSynchronized": mru_synchronized})


# ---------------------------------------------------------------- the rApp's reads

def _latest(job_id: str) -> dict | None:
    items = call("get", "dme", f"/data-jobs/{job_id}/records", params={"limit": 50})["items"]
    return next((r["payload"] for r in items if r["payload"].get("cellId") == CELL), None)


def read_tx_config() -> dict:
    attrs = call("get", "ran-nf-oam", f"/managed-entities/{ME}/config", params={"managed_function_ref": MFR})["attributes"]
    return {k: attrs.get(k) for k in TX_LEAVES}


def active_alarms() -> list[dict]:
    items = call("get", "ran-nf-oam", "/alarms", params={"managed_element_ref": ME, "limit": 500})["items"]
    return [a for a in items if a["severity"] != "cleared" and a.get("managedFunctionRef") in (None, MFR)]


def snapshot(state: dict) -> dict:
    snap = {}
    for name, counter in COUNTERS.items():
        rec = _latest(state["dataJobs"][counter])
        if rec is None:
            continue
        value = rec["value"]
        if name == "rrcConnectedUeCount":
            value = int(value)
        elif name == "mruSynchronizationState":
            value = "SYNCHRONIZED" if value >= 1 else "NOT_SYNCHRONIZED"
        snap[name] = {"value": value, "timestamp": rec["timestamp"]}
    cfg = read_tx_config()
    enabled = cfg["txMutingFeatureEnable"]
    snap["txMutingFeatureEnable"] = {"value": None if enabled is None else str(enabled).lower() == "true"}
    snap["txMutingActivation"] = {"value": cfg["txMutingActivation"]}
    snap["txPathOffPattern"] = {"value": cfg["txPathOffPattern"]}
    snap["blockingAlarms"] = [int(a["sourceAlarmId"]) for a in active_alarms() if str(a["sourceAlarmId"]).isdigit()]
    return snap


# ---------------------------------------------------------------- the rApp's write path

def _action(state: dict, decision_id: str, attribute_changes: dict, reason: str) -> dict:
    return call("post", "dme", "/actions", headers={"X-Correlation-ID": decision_id}, json={
        "actionId": str(uuid.uuid4()), "requestedBy": RAPP_ID, "scope": "single-ME",
        "changes": [{"managedElementRef": ME, "className": "NRCellDU", "managedFunctionRef": MFR,
                     "attributeChanges": attribute_changes}],
        "sourceContext": {"rApp": RAPP_ID, "decisionId": decision_id, "reason": reason,
                          "dataJobIds": list(state["dataJobs"].values())}})


def _verify(expected: dict) -> dict:
    observed = read_tx_config()
    ok = all(str(observed.get(k)).lower() == str(v).lower() for k, v in expected.items())
    return {"result": "VERIFIED" if ok else "VERIFY_FAILED", "expected": expected, "observed": observed}


def evaluate_and_act(state: dict) -> dict:
    """One closed-loop pass (Figures 5b-9): read, decide, write through DME, read back."""
    cfg = thresholds()
    state["seq"] = state.get("seq", 0) + 1
    decision_id = f"ES-PILOT-{state['seq']:04d}"
    snap = snapshot(state)
    result = engine.evaluate(snap, cfg, now())
    record = {"decisionId": decision_id, "decisionTime": now().isoformat(),
              "target": {"managedElementRef": ME, "managedFunctionRef": MFR},
              "instantaneousValues": {k: v.get("value") if isinstance(v, dict) else v for k, v in snap.items()}, **result}
    if result["changes"]:
        retries = cfg["executionPolicy"]["maximumRetries"]
        for attempt in range(retries + 1):
            action = _action(state, decision_id, result["changes"], result["reason"])
            verification = _verify(result["changes"])
            if verification["result"] == "VERIFIED":
                break
        record.update(action=action, verification=verification, attempts=attempt + 1)
        if (verification["result"] != "VERIFIED" and result["decision"] == "REDUCED_TX"
                and cfg["executionPolicy"]["rollbackOnVerificationFailure"]):
            restore = {"txMutingActivation": cfg["requestedConfiguration"]["fullTxYangValue"]}
            record["rollback"] = {"action": _action(state, decision_id, restore, "ROLLBACK_VERIFY_FAILED"),
                                  "verification": _verify(restore)}
    state.setdefault("decisions", []).append(record)
    show("decision", f"{decision_id} {result['decision']} ({result['reason']})")
    show("state before", result["currentState"])
    if "action" in record:
        show("DME action", {k: record["action"].get(k) for k in ("actionId", "status", "forwardedJobId")})
        show("read-back", record["verification"])
    return record


# ---------------------------------------------------------------- the steps

def step_00(state: dict) -> None:
    """Prepare the RAN (Figures 2-3): CCDU O1 Adaptor registered and ACTIVE, PM subscribed, existing TX-muting config."""
    me = call("get", "ran-nf-oam", f"/managed-entities/{ME}", expect=(200, 404))
    if me and me.get("o1AdaptorEndpointId"):
        endpoint_id = me["o1AdaptorEndpointId"]
    else:
        endpoint_id = call("post", "ran-nf-oam", "/o1-adaptor-endpoints", json={
            "managedElementRef": ME, "adaptorUri": ADAPTOR_URI, "protocolSupport": ["NETCONF"], "o1Protocol": "NETCONF",
            "entityType": "O-DU", "vendorName": VENDOR})["endpointId"]
    call("post", "ran-nf-oam", f"/o1-adaptor-endpoints/{endpoint_id}/heartbeat")
    for counter in COUNTERS.values():
        call("post", "ran-nf-oam", "/pm-subscriptions", params={"managed_element_ref": ME, "counter_type": counter,
                                                               "delivery_method": "pull", "granularity_period": 300})
    # Figure 2: the configuration the CCDU already runs (feature enabled, muting off), so the rApp reads a known state
    seeded = call("post", "ran-nf-oam", "/config-jobs", json={
        "requestedBy": "ccdu-initial-reconciliation", "accessScope": "single-ME",
        "changes": [{"managedElementRef": ME, "className": "NRCellDU", "managedFunctionRef": MFR,
                     "attributeChanges": {"txMutingFeatureEnable": "true", "txPathOffPattern": "HORIZONTAL_PLANE",
                                          "txMutingActivation": "MUTING_OFF"}}]})
    state["endpointId"] = endpoint_id
    show("managed element", f"{ME} ({VENDOR}) behind {ADAPTOR_URI}")
    show("PM counters", list(COUNTERS.values()))
    show("initial config job", seeded["status"])
    show(MFR, read_tx_config())


def step_01(state: dict) -> None:
    """rApp start (Figure 5b): load and check thresholds, open one ONE_TIME pull data job per counter in DME."""
    cfg = thresholds()
    types = {t["typeName"]: t["dmeTypeId"] for t in call("get", "dme", "/dme-types", params={"data_category": "RAN"})}
    jobs = {}
    for counter in COUNTERS.values():
        type_id = types.get(f"RAN.PMCounters.{counter}")
        if type_id is None:
            raise SystemExit(f"DME type RAN.PMCounters.{counter} is not registered: run step 00 first")
        jobs[counter] = call("post", "dme", "/data-jobs", json={
            "dataDeliveryMode": "ONE_TIME", "dmeTypeId": type_id, "dataDeliveryMethod": "PULL_HTTP",
            "consumerId": RAPP_ID, "lifecycleStage": "INFERENCE",
            "productionJobDefinition": {
                "target": {"ranNodeId": ME, "managedElementId": ME, "gnbDuFunctionId": "DU-1", "cellId": CELL},
                "measurementNames": list(COUNTERS), "sampleSelection": "LATEST_AVAILABLE",
                "maximumSampleAgeSeconds": cfg["measurementPolicy"]["maximumSampleAgeSeconds"]}})["dataJobId"]
    state["dataJobs"] = jobs
    show("thresholds", {"activation": cfg["activation"], "deactivation": cfg["deactivation"]})
    show("data jobs", jobs)


def step_02(state: dict) -> None:
    """Low load (Figures 6-9): PRB 18.4 %, 4 UEs, MRU synchronized -> REDUCED_TX, MUTING_ON read back."""
    report_pm(18.4, 4)
    state["lastAction"] = evaluate_and_act(state).get("action")


def step_03(state: dict) -> None:
    """Show the DME action, the RAN NF OAM job and what the O1 Adaptor applied."""
    action = state.get("lastAction")
    if not action:
        raise SystemExit("no action recorded: run step 02 first")
    record = call("get", "dme", f"/actions/{action['actionId']}")
    job = call("get", "ran-nf-oam", f"/config-jobs/{record['forwardedJobId']}")
    applied = call("get", "mock-o1-adaptor", f"/objects/{ME}", params={"function_ref": MFR})
    show("DME action", {k: record[k] for k in ("actionId", "requestedBy", "status", "correlationId", "sourceContext")})
    show("config job", {k: job.get(k) for k in ("jobId", "status")})
    show("O1 Adaptor running config", {k: applied["attributes"].get(k) for k in TX_LEAVES})


def step_04(state: dict) -> None:
    """Hysteresis (no change): PRB 41 %, 8 UEs while MUTING_ON -> NO_CHANGE, no O1 write."""
    report_pm(41.0, 8)
    evaluate_and_act(state)


def step_05(state: dict) -> None:
    """Restore full TX: PRB 45 % -> FULL_TX, MUTING_OFF read back."""
    report_pm(45.0, 8)
    evaluate_and_act(state)


def step_06(state: dict) -> None:
    """Safety gate: a blocking alarm (13325 L1_FH_COMM_DOWN_ERROR) on the cell stops MUTING_ON despite low load."""
    raised = call("post", "ran-nf-oam", "/alarms/ingest", params={
        "source_alarm_id": "13325", "managed_element_ref": ME, "severity": "critical", "managed_function_ref": MFR,
        "probable_cause": "L1_FH_COMM_DOWN_ERROR", "specific_problem": "13325"})
    report_pm(16.2, 3)
    evaluate_and_act(state)
    call("patch", "ran-nf-oam", f"/alarms/{raised['alarmId']}/clear", params={"clear_user_id": RAPP_ID})
    show("alarm", f"13325 raised as {raised['alarmId']}, cleared after the pass")


def step_07(state: dict) -> None:
    """MRU loses sync while muted: re-mute on low load, then MRU NOT_SYNCHRONIZED -> FULL_TX."""
    report_pm(18.0, 4)
    evaluate_and_act(state)
    report_pm(18.0, 4, mru_synchronized=False)
    evaluate_and_act(state)


def step_08(state: dict) -> None:
    """Audit: every decision of this run and every DME action the pilot requested."""
    for d in state.get("decisions", []):
        verified = (d.get("verification") or {}).get("result", "-")
        print(f"  {d['decisionId']}  {d['currentState'] or '-':10} -> {d['decision']:10} {verified:13} {d['reason']}")
    actions = call("get", "dme", "/actions", params={"requested_by": RAPP_ID, "limit": 100})["items"]
    show("DME actions by the pilot", len(actions))


STEPS = {f"{i:02d}": globals()[f"step_{i:02d}"] for i in range(9)}


def main() -> None:
    steps = list(STEPS) if sys.argv[1:] in ([], ["all"]) else sys.argv[1:]
    unknown = [s for s in steps if s not in STEPS]
    if unknown:
        raise SystemExit(f"unknown step(s) {unknown}; choose from {list(STEPS)} or 'all'")
    state = json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else {}
    for step in steps:
        fn = STEPS[step]
        print(f"Step {step} - {fn.__doc__.splitlines()[0]}")
        fn(state)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, default=str, indent=1)


if __name__ == "__main__":
    main()
