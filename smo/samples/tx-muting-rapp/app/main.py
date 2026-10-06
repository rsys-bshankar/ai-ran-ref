"""TX-muting rApp: a Non-RT RIC energy-saving rApp that mutes half of a cell's TX paths at low load and restores full
TX when load returns, through O1. R1 only: no A1, Near-RT RIC, xApp or E2.

The loop (POST /evaluate):

    PM from DME + config and alarms from RAN NF OAM -> decision (engine.py)
        -> DME /actions -> RAN NF OAM config job -> O1 edit-config -> read-back -> rollback -> audit

Every write carries the decision id as X-Correlation-ID, is read back with an O1 get-config through RAN NF OAM and,
for REDUCED_TX only, is rolled back to full TX if the read-back disagrees. The service keeps its state in memory.
"""

import datetime
import json
import os
import threading
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from smo_shared.health import install_health
from smo_shared.logconfig import install_logging
from smo_shared.metrics import install_metrics

from . import engine

app = FastAPI(title="TX-muting rApp")
install_logging(app)  # structured JSON logs and one access-log line per request (PR-OBS-1)
install_metrics(app)  # /metrics and request count/latency series (PR-OBS-2)
install_health(app)  # /live, /ready and /health

RAPP_ID = "tx-muting-rapp"
DME_URL = os.environ.get("DME_URL", "http://dme:8000")
RAN_NF_OAM_URL = os.environ.get("RAN_NF_OAM_URL", "http://ran-nf-oam:8000")
THRESHOLDS_FILE = Path(os.environ.get("TX_MUTING_THRESHOLDS", Path(__file__).with_name("thresholds.json")))
VENDOR_TIMEOUT = 60.0

# rApp measurement name -> RAN NF OAM PM counter type (DME type RAN.PMCounters.<counter>)
COUNTERS = {"dlPrbUtilization": "DL_PRB_UTILIZATION", "rrcConnectedUeCount": "RRC_CONNECTED_UE",
            "radioSynchronizationState": "RADIO_SYNC_STATE"}
TX_LEAVES = ("txMutingFeatureEnable", "txPathOffPattern", "txMutingActivation")

_lock = threading.Lock()
_state: dict = {"started": False, "decisions": [], "seq": 0}


class StartRequest(BaseModel):
    managedElementRef: str = "tx-muting-me-001"
    cellId: str = "101"


def _call(base: str, verb: str, path: str, expect=(200, 201, 202, 204), **kw):
    resp = getattr(httpx, verb)(f"{base}{path}", timeout=VENDOR_TIMEOUT, **kw)
    if resp.status_code not in expect:
        raise HTTPException(502, f"{verb.upper()} {base}{path} -> {resp.status_code}: {resp.text}")
    return resp.json() if resp.content else None


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def thresholds() -> dict:
    cfg = json.loads(THRESHOLDS_FILE.read_text())
    try:
        engine.validate_thresholds(cfg)
    except engine.ThresholdError as exc:
        raise HTTPException(422, str(exc))
    return cfg


def _target() -> tuple[str, str, str]:
    if not _state["started"]:
        raise HTTPException(409, "not started: POST /start first")
    return _state["managedElementRef"], _state["cellId"], f"NRCellDU={_state['cellId']}"


# ---------------------------------------------------------------- reads

def _latest(job_id: str, cell: str) -> dict | None:
    items = _call(DME_URL, "get", f"/data-jobs/{job_id}/records", params={"limit": 50})["items"]
    return next((r["payload"] for r in items if r["payload"].get("cellId") == cell), None)


def read_tx_config() -> dict:
    me, _, mfr = _target()
    attrs = _call(RAN_NF_OAM_URL, "get", f"/managed-entities/{me}/config", params={"managed_function_ref": mfr})["attributes"]
    return {k: attrs.get(k) for k in TX_LEAVES}


def active_alarms() -> list[dict]:
    me, _, mfr = _target()
    items = _call(RAN_NF_OAM_URL, "get", "/alarms", params={"managed_element_ref": me, "limit": 500})["items"]
    return [a for a in items if a["severity"] != "cleared" and a.get("managedFunctionRef") in (None, mfr)]


def snapshot() -> dict:
    _, cell, _ = _target()
    snap = {}
    for name, counter in COUNTERS.items():
        rec = _latest(_state["dataJobs"][counter], cell)
        if rec is None:
            continue
        value = rec["value"]
        if name == "rrcConnectedUeCount":
            value = int(value)
        elif name == "radioSynchronizationState":
            value = "SYNCHRONIZED" if value >= 1 else "NOT_SYNCHRONIZED"
        snap[name] = {"value": value, "timestamp": rec["timestamp"]}
    cfg = read_tx_config()
    enabled = cfg["txMutingFeatureEnable"]
    snap["txMutingFeatureEnable"] = {"value": None if enabled is None else str(enabled).lower() == "true"}
    snap["txMutingActivation"] = {"value": cfg["txMutingActivation"]}
    snap["txPathOffPattern"] = {"value": cfg["txPathOffPattern"]}
    snap["blockingAlarms"] = [int(a["sourceAlarmId"]) for a in active_alarms() if str(a["sourceAlarmId"]).isdigit()]
    return snap


# ---------------------------------------------------------------- write path

def _action(decision_id: str, attribute_changes: dict, reason: str) -> dict:
    me, _, mfr = _target()
    return _call(DME_URL, "post", "/actions", headers={"X-Correlation-ID": decision_id}, json={
        "actionId": str(uuid.uuid4()), "requestedBy": RAPP_ID, "scope": "single-ME",
        "changes": [{"managedElementRef": me, "className": "NRCellDU", "managedFunctionRef": mfr,
                     "attributeChanges": attribute_changes}],
        "sourceContext": {"rApp": RAPP_ID, "decisionId": decision_id, "reason": reason,
                          "dataJobIds": list(_state["dataJobs"].values())}})


def _verify(expected: dict) -> dict:
    observed = read_tx_config()
    ok = all(str(observed.get(k)).lower() == str(v).lower() for k, v in expected.items())
    return {"result": "VERIFIED" if ok else "VERIFY_FAILED", "expected": expected, "observed": observed}


def _evaluate_and_act() -> dict:
    me, _, mfr = _target()
    cfg = thresholds()
    _state["seq"] += 1
    decision_id = f"TXM-{_state['seq']:04d}"
    snap = snapshot()
    result = engine.evaluate(snap, cfg, _now())
    record = {"decisionId": decision_id, "decisionTime": _now().isoformat(),
              "target": {"managedElementRef": me, "managedFunctionRef": mfr},
              "instantaneousValues": {k: v.get("value") if isinstance(v, dict) else v for k, v in snap.items()}, **result}
    if result["changes"]:
        retries = cfg["executionPolicy"]["maximumRetries"]
        for attempt in range(retries + 1):
            action = _action(decision_id, result["changes"], result["reason"])
            verification = _verify(result["changes"])
            if verification["result"] == "VERIFIED":
                break
        record.update(action=action, verification=verification, attempts=attempt + 1)
        if (verification["result"] != "VERIFIED" and result["decision"] == "REDUCED_TX"
                and cfg["executionPolicy"]["rollbackOnVerificationFailure"]):
            restore = {"txMutingActivation": cfg["requestedConfiguration"]["fullTxYangValue"]}
            record["rollback"] = {"action": _action(decision_id, restore, "ROLLBACK_VERIFY_FAILED"),
                                  "verification": _verify(restore)}
    _state["decisions"].append(record)
    return record


# ---------------------------------------------------------------- routes

@app.post("/start")
def start(body: StartRequest | None = None):
    """Check the thresholds and open one ONE_TIME pull data job per PM counter in DME. Run the O1 adaptor's
    registration first (it subscribes the counters, which registers the DME types)."""
    body = body or StartRequest()
    cfg = thresholds()
    types = {t["typeName"]: t["dmeTypeId"] for t in _call(DME_URL, "get", "/dme-types", params={"data_category": "RAN"})}
    jobs = {}
    for counter in COUNTERS.values():
        type_id = types.get(f"RAN.PMCounters.{counter}")
        if type_id is None:
            raise HTTPException(409, f"DME type RAN.PMCounters.{counter} is not registered: register the O1 adaptor first")
        jobs[counter] = _call(DME_URL, "post", "/data-jobs", json={
            "dataDeliveryMode": "ONE_TIME", "dmeTypeId": type_id, "dataDeliveryMethod": "PULL_HTTP",
            "consumerId": RAPP_ID, "lifecycleStage": "INFERENCE",
            "productionJobDefinition": {
                "target": {"ranNodeId": body.managedElementRef, "managedElementId": body.managedElementRef,
                           "gnbDuFunctionId": "DU-1", "cellId": body.cellId},
                "measurementNames": list(COUNTERS), "sampleSelection": "LATEST_AVAILABLE",
                "maximumSampleAgeSeconds": cfg["measurementPolicy"]["maximumSampleAgeSeconds"]}})["dataJobId"]
    with _lock:
        _state.update(started=True, managedElementRef=body.managedElementRef, cellId=body.cellId, dataJobs=jobs)
    return {"managedElementRef": body.managedElementRef, "cellId": body.cellId, "dataJobs": jobs,
            "thresholds": {"activation": cfg["activation"], "deactivation": cfg["deactivation"]}}


@app.post("/evaluate")
def evaluate():
    """One closed-loop pass. X-Correlation-ID of the DME action is the returned decisionId."""
    with _lock:
        return _evaluate_and_act()


@app.get("/state")
def state():
    """Target, data jobs, and the live O1 configuration read through RAN NF OAM."""
    if not _state["started"]:
        return {"started": False}
    return {"started": True, "managedElementRef": _state["managedElementRef"], "cellId": _state["cellId"],
            "dataJobs": _state["dataJobs"], "config": read_tx_config(), "decisions": len(_state["decisions"])}


@app.get("/decisions")
def decisions(limit: int = 100):
    return {"items": _state["decisions"][-limit:]}


@app.get("/thresholds")
def get_thresholds():
    return thresholds()


@app.get("/actions")
def actions():
    """The DME actions this rApp requested (audit)."""
    return _call(DME_URL, "get", "/actions", params={"requested_by": RAPP_ID, "limit": 100})


@app.delete("/state", status_code=204)
def reset():
    """Forget the target and the decision log (data jobs are ended by `DELETE /data-jobs?consumer_id=` on DME)."""
    with _lock:
        _state.clear()
        _state.update(started=False, decisions=[], seq=0)
