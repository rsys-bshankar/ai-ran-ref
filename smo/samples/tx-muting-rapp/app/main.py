"""TX-muting rApp: a Non-RT RIC energy-saving rApp that mutes half of a cell's TX paths at low load and restores full
TX when load returns, through O1. R1 only: no A1, Near-RT RIC, xApp or E2.

The loop (POST /evaluate):

    PM from DME + config from RAN NF OAM -> decision (engine.py)
        -> DME /actions -> RAN NF OAM config job -> O1 edit-config -> read-back -> rollback -> audit

Every write carries the decision id as X-Correlation-ID, is read back with an O1 get-config through RAN NF OAM and,
for REDUCED_TX only, is rolled back to full TX if the read-back disagrees.

Every call to DME and RAN NF OAM goes through R1 Termination (R1_GATEWAY_URL) as an rApp: the client registers at SME as
an API invoker, takes a client_credentials token (scope smo-rapp) and sends it as a Bearer token, so R1 applies the rApp
role policy to each call. Nothing talks to a backend service directly.

State is in memory, and every change to it is an event: logged as one structured line ("state change ...") and kept in a
numbered log served by GET /events (long poll), so what changed, when and why is always visible (see AppState).
"""

import collections
import contextvars
import datetime
import json
import logging
import os
import threading
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from smo_shared.correlation import HEADER_NAME as CORRELATION_HEADER, apply_correlation_id
from smo_shared.health import install_health
from smo_shared.logconfig import install_logging
from smo_shared.metrics import install_metrics
from smo_shared.r1_client import R1Client

from . import engine

app = FastAPI(title="TX-muting rApp")
install_logging(app)  # structured JSON logs and one access-log line per request (PR-OBS-1)
install_metrics(app)  # /metrics and request count/latency series (PR-OBS-2)
install_health(app)  # /live, /ready and /health
apply_correlation_id(app)
log = logging.getLogger("tx_muting_rapp")

RAPP_ID = "tx-muting-rapp"
THRESHOLDS_FILE = Path(os.environ.get("TX_MUTING_THRESHOLDS", Path(__file__).with_name("thresholds.json")))

# A decision's id is the correlation id of every call it makes: the DME action, the config job it becomes and the read-backs
# all share it, instead of the id of the /evaluate request that triggered them.
_correlation_override: contextvars.ContextVar[str | None] = contextvars.ContextVar("correlation_override", default=None)


class _RappR1Client(R1Client):
    def _headers(self, refresh: bool = False) -> dict:
        headers = super()._headers(refresh)
        if _correlation_override.get():
            headers[CORRELATION_HEADER] = _correlation_override.get()
        return headers


_r1 = _RappR1Client()  # reads R1_GATEWAY_URL; identity: SMO_IDENTITY_KIND=rapp (compose)

# rApp measurement name -> RAN NF OAM PM counter type (DME type RAN.PMCounters.<counter>)
COUNTERS = {"dlPrbUtilization": "DL_PRB_UTILIZATION", "rrcConnectedUeCount": "RRC_CONNECTED_UE"}
MAX_SAMPLE_AGE_SECONDS = 900  # how old a PM sample DME may hand back for the data job; the decision does not check age
TX_LEAVES = ("txMutingFeatureEnable", "txPathOffPattern", "txMutingActivation")

class AppState:
    """The rApp's in-memory state. Nothing changes it except the methods below, and each one emits an event: logged
    ("state change <kind> {...}") and appended to a numbered log that GET /events serves, with a long-poll option."""

    def __init__(self):
        self._events = collections.deque(maxlen=1000)
        self._seq = 0
        self._cond = threading.Condition()
        self.started = False
        self.target: dict = {}
        self.data_jobs: dict = {}
        self.decisions: list[dict] = []
        self.counter = 0
        self.tx_state: str | None = None  # txMutingActivation as last read or written

    def emit(self, kind: str, **data) -> dict:
        with self._cond:
            self._seq += 1
            event = {"seq": self._seq, "time": datetime.datetime.now(datetime.UTC).isoformat(timespec="milliseconds"),
                     "kind": kind, "data": data}
            self._events.append(event)
            self._cond.notify_all()
        log.info("state change %s %s", kind, json.dumps(data, default=str))
        return event

    def events_since(self, seq: int = 0, wait: float = 0.0, limit: int = 200) -> list[dict]:
        with self._cond:
            if wait and not any(e["seq"] > seq for e in self._events):
                self._cond.wait(wait)
            return [e for e in self._events if e["seq"] > seq][:limit]

    @property
    def last_seq(self) -> int:
        return self._seq

    def start(self, managed_element: str, cell: str, jobs: dict) -> None:
        self.started, self.data_jobs = True, jobs
        self.target = {"managedElementRef": managed_element, "cellId": cell}
        self.emit("started", managedElementRef=managed_element, cellId=cell, dataJobs=jobs)

    def next_decision_id(self) -> str:
        self.counter += 1
        return f"TXM-{self.counter:04d}"

    def observe_tx_state(self, value: str | None, decision_id: str) -> None:
        """The cell's txMutingActivation as read for a pass; an event only when it differs from what we last knew
        (a change nobody here made, or the first read)."""
        if value != self.tx_state:
            self.emit("tx-state.observed", decisionId=decision_id, previous=self.tx_state, current=value)
            self.tx_state = value

    def record(self, record: dict) -> None:
        self.decisions.append(record)
        self.emit("decision", decisionId=record["decisionId"], decision=record["decision"], reason=record["reason"],
                  stateBefore=record["currentState"], changes=record["changes"],
                  verification=(record.get("verification") or {}).get("result"), attempts=record.get("attempts"),
                  rolledBack="rollback" in record)
        verified = (record.get("verification") or {}).get("result") == "VERIFIED"
        if record["changes"] and "txMutingActivation" in record["changes"]:
            new = record["changes"]["txMutingActivation"]
            rb = record.get("rollback")
            if rb:
                new = (rb["verification"]["expected"] or {}).get("txMutingActivation", new)
                verified = rb["verification"]["result"] == "VERIFIED"
            if verified and new != self.tx_state:
                self.emit("tx-state.changed", decisionId=record["decisionId"], previous=self.tx_state, current=new)
                self.tx_state = new

    def reset(self) -> None:
        before = {"started": self.started, "decisions": len(self.decisions)}
        self.started, self.target, self.data_jobs, self.decisions, self.counter, self.tx_state = False, {}, {}, [], 0, None
        self.emit("reset", discarded=before)


_lock = threading.Lock()
_state = AppState()


class StartRequest(BaseModel):
    managedElementRef: str = "tx-muting-me-001"
    cellId: str = "101"


def _call(service: str, verb: str, path: str, expect=(200, 201, 202, 204), **kw):
    """One call to `service` ("dme", "ran-nf-oam") through R1 Termination, with this rApp's SME token."""
    try:
        resp = getattr(_r1, verb)(f"/{service}{path}", **kw)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"{verb.upper()} /{service}{path}: {exc!r}")
    if resp.status_code not in expect:
        raise HTTPException(502, f"{verb.upper()} /{service}{path} via R1 -> {resp.status_code}: {resp.text}")
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
    if not _state.started:
        raise HTTPException(409, "not started: POST /start first")
    return _state.target["managedElementRef"], _state.target["cellId"], f"NRCellDU={_state.target['cellId']}"


# ---------------------------------------------------------------- reads

def _latest(job_id: str, cell: str) -> dict | None:
    items = _call("dme", "get", f"/data-jobs/{job_id}/records", params={"limit": 50})["items"]
    return next((r["payload"] for r in items if r["payload"].get("cellId") == cell), None)


def read_tx_config() -> dict:
    me, _, mfr = _target()
    attrs = _call("ran-nf-oam", "get", f"/managed-entities/{me}/config", params={"managed_function_ref": mfr})["attributes"]
    return {k: attrs.get(k) for k in TX_LEAVES}


def snapshot() -> dict:
    _, cell, _ = _target()
    snap = {}
    for name, counter in COUNTERS.items():
        rec = _latest(_state.data_jobs[counter], cell)
        if rec is None:
            continue
        value = rec["value"]
        if name == "rrcConnectedUeCount":
            value = int(value)
        snap[name] = {"value": value, "timestamp": rec["timestamp"]}
    cfg = read_tx_config()
    enabled = cfg["txMutingFeatureEnable"]
    snap["txMutingFeatureEnable"] = {"value": None if enabled is None else str(enabled).lower() == "true"}
    snap["txMutingActivation"] = {"value": cfg["txMutingActivation"]}
    snap["txPathOffPattern"] = {"value": cfg["txPathOffPattern"]}
    return snap


# ---------------------------------------------------------------- write path

def _action(decision_id: str, attribute_changes: dict, reason: str) -> dict:
    me, _, mfr = _target()
    return _call("dme", "post", "/actions", headers={"X-Correlation-ID": decision_id}, json={
        "actionId": str(uuid.uuid4()), "requestedBy": RAPP_ID, "scope": "single-ME",
        "changes": [{"managedElementRef": me, "className": "NRCellDU", "managedFunctionRef": mfr,
                     "attributeChanges": attribute_changes}],
        "sourceContext": {"rApp": RAPP_ID, "decisionId": decision_id, "reason": reason,
                          "dataJobIds": list(_state.data_jobs.values())}})


def _verify(expected: dict) -> dict:
    observed = read_tx_config()
    ok = all(str(observed.get(k)).lower() == str(v).lower() for k, v in expected.items())
    return {"result": "VERIFIED" if ok else "VERIFY_FAILED", "expected": expected, "observed": observed}


def _evaluate_and_act() -> dict:
    me, _, mfr = _target()
    cfg = thresholds()
    decision_id = _state.next_decision_id()
    token = _correlation_override.set(decision_id)  # every call of this pass carries the decision id
    try:
        snap = snapshot()
        _state.observe_tx_state(snap["txMutingActivation"]["value"], decision_id)
        result = engine.evaluate(snap, cfg)
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
    finally:
        _correlation_override.reset(token)
    _state.record(record)
    return record


# ---------------------------------------------------------------- routes

@app.post("/start")
def start(body: StartRequest | None = None):
    """Check the thresholds and open one ONE_TIME pull data job per PM counter in DME. Run the O1 adaptor's
    registration first (it subscribes the counters, which registers the DME types)."""
    body = body or StartRequest()
    cfg = thresholds()
    types = {t["typeName"]: t["dmeTypeId"] for t in _call("dme", "get", "/dme-types", params={"data_category": "RAN"})}
    jobs = {}
    for counter in COUNTERS.values():
        type_id = types.get(f"RAN.PMCounters.{counter}")
        if type_id is None:
            raise HTTPException(409, f"DME type RAN.PMCounters.{counter} is not registered: register the O1 adaptor first")
        jobs[counter] = _call("dme", "post", "/data-jobs", json={
            "dataDeliveryMode": "ONE_TIME", "dmeTypeId": type_id, "dataDeliveryMethod": "PULL_HTTP",
            "consumerId": RAPP_ID, "lifecycleStage": "INFERENCE",
            "productionJobDefinition": {
                "target": {"ranNodeId": body.managedElementRef, "managedElementId": body.managedElementRef,
                           "gnbDuFunctionId": "DU-1", "cellId": body.cellId},
                "measurementNames": list(COUNTERS), "sampleSelection": "LATEST_AVAILABLE",
                "maximumSampleAgeSeconds": MAX_SAMPLE_AGE_SECONDS}})["dataJobId"]
    with _lock:
        _state.start(body.managedElementRef, body.cellId, jobs)
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
    if not _state.started:
        return {"started": False}
    return {"started": True, **_state.target, "dataJobs": _state.data_jobs, "config": read_tx_config(),
            "lastKnownTxState": _state.tx_state, "decisions": len(_state.decisions), "lastEventSeq": _state.last_seq}


@app.get("/decisions")
def decisions(limit: int = 100):
    return {"items": _state.decisions[-limit:]}


@app.get("/events")
def events(since: int = 0, wait: float = 0.0, limit: int = 200):
    """Every change to this service's state, numbered: started, decision, tx-state.observed / tx-state.changed, reset.
    With `wait` > 0 the call blocks until an event after `since` arrives (max 30 s): follow it with scripts/watch.sh."""
    return {"items": _state.events_since(since, min(wait, 30.0), limit), "lastSeq": _state.last_seq}


@app.get("/thresholds")
def get_thresholds():
    return thresholds()


@app.get("/actions")
def actions():
    """The DME actions this rApp requested (audit)."""
    return _call("dme", "get", "/actions", params={"requested_by": RAPP_ID, "limit": 100})


@app.delete("/state", status_code=204)
def reset():
    """Forget the target and the decision log (data jobs are ended by `DELETE /data-jobs?consumer_id=` on DME). The event log is kept."""
    with _lock:
        _state.reset()
