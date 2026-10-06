"""gNB O1 adaptor simulator: a stand-in for a vendor's O1 adaptor that RAN NF OAM can register, configure and read, and
that makes up the data a real network function would send.

South of RAN NF OAM it behaves like an adaptor:
  * consumes configuration: POST /edit-config (NETCONF-shaped `edit-config` / `get-config` over HTTP, the contract
    of mock-o1-adaptor), GET /capabilities;
  * generates data, only when told to (CLI, control API or the generator): self-registration, heartbeat, PM counter
    reports, alarms.

Everything it does or receives is an event on one log (GET /events, long poll) so a CLI can print asynchronous
happenings as they occur. `python -m app.gnb_cli` is that CLI; it only calls the /control routes below.
"""

import datetime
import os
import random
import threading
from xml.sax.saxutils import escape, quoteattr

import defusedxml.ElementTree as ET
from defusedxml.common import DefusedXmlException
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field

from smo_shared.health import install_health
from smo_shared.logconfig import install_logging
from smo_shared.metrics import install_metrics

from . import oam
from .state import FAULT_MODES, EventLog, Store

NETCONF_NS = "urn:ietf:params:xml:ns:netconf:base:1.0"
ME = os.environ.get("ADAPTOR_ME", "tx-muting-me-001")
CELL = os.environ.get("ADAPTOR_CELL", "101")
VENDOR = os.environ.get("ADAPTOR_VENDOR", "demo-vendor")
PUBLIC_URI = os.environ.get("ADAPTOR_PUBLIC_URI", "http://gnb-o1-adaptor-sim:8000/edit-config")
OPERATOR = "gnb-o1-adaptor-sim"

app = FastAPI(title="gNB O1 adaptor simulator")
install_logging(app)
install_metrics(app)
install_health(app)

events = EventLog()
store = Store()
northbound = oam.OamClient(os.environ.get("RAN_NF_OAM_URL", "http://ran-nf-oam:8000"))
_registration: dict = {}


def _function_ref(cell: str) -> str:
    return f"NRCellDU={cell}"


def _oam(fn, *args, **kw):
    """Run a RAN NF OAM call; a failure is an event and a 502, never a crash."""
    try:
        return fn(*args, **kw)
    except oam.OamError as exc:
        events.emit("northbound.error", error=str(exc))
        raise HTTPException(502, str(exc))


# ---------------------------------------------------------------- consumed: NETCONF-shaped configuration

def _rpc_reply(message_id: str, error_tag: str | None = None) -> Response:
    if error_tag is None:
        body = f'<rpc-reply message-id={quoteattr(message_id)} xmlns="{NETCONF_NS}"><ok/></rpc-reply>'
    else:
        body = (f'<rpc-reply message-id={quoteattr(message_id)} xmlns="{NETCONF_NS}"><rpc-error><error-type>application'
                f"</error-type><error-tag>{error_tag}</error-tag><error-severity>error</error-severity></rpc-error></rpc-reply>")
    return Response(content=body, media_type="application/xml")


def _data_reply(message_id: str, ref: str, function_ref: str | None, attributes: dict) -> Response:
    function = f" function-ref={quoteattr(function_ref)}" if function_ref else ""
    body = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in attributes.items())
    return Response(content=(f'<rpc-reply message-id={quoteattr(message_id)} xmlns="{NETCONF_NS}"><data>'
                             f"<managed-object ref={quoteattr(ref)}{function}>{body}</managed-object></data></rpc-reply>"),
                    media_type="application/xml")


@app.post("/edit-config")
async def edit_config(request: Request) -> Response:
    """`<edit-config>` (merge / delete) and `<get-config>` on a `<managed-object ref= function-ref=>` payload."""
    try:
        root = ET.fromstring(await request.body())
    except (ET.ParseError, DefusedXmlException):
        events.emit("config.rejected", reason="malformed-message")
        return _rpc_reply("0", "malformed-message")
    message_id = root.attrib.get("message-id", "0")
    mo = root.find(f".//{{{NETCONF_NS}}}managed-object")
    ref = mo.attrib.get("ref") if mo is not None else None
    function_ref = mo.attrib.get("function-ref") if mo is not None else None
    if root.find(f"{{{NETCONF_NS}}}get-config") is not None:
        if not ref:
            return _rpc_reply(message_id, "invalid-value")
        current = store.current(ref, function_ref)
        events.emit("config.read", ref=ref, functionRef=function_ref, attributes=current)
        return _data_reply(message_id, ref, function_ref, current)
    operation = mo.attrib.get("operation", "merge") if mo is not None else "merge"
    changes = {c.tag.rsplit("}", 1)[-1]: c.text for c in mo} if mo is not None else {}
    if not ref or (not changes and operation not in ("delete", "remove")):
        events.emit("config.rejected", ref=ref, reason="invalid-value")
        return _rpc_reply(message_id, "invalid-value")
    fault = store.take_fault()
    if fault == "TIMEOUT":
        events.emit("config.fault", ref=ref, mode=fault)
        return Response(status_code=504, content="agent did not answer in time")
    if fault == "RPC_ERROR":
        events.emit("config.fault", ref=ref, mode=fault)
        return _rpc_reply(message_id, "operation-failed")
    if fault == "IGNORE_WRITE":  # acknowledged but never applied: a lying agent
        events.emit("config.fault", ref=ref, mode=fault, ignored=changes)
        return _rpc_reply(message_id)
    if operation in ("delete", "remove"):
        store.config.pop((ref, function_ref), None)
        events.emit("config.deleted", ref=ref, functionRef=function_ref)
    else:
        events.emit("config.received", ref=ref, functionRef=function_ref, operation=operation, changes=changes,
                    running=store.merge(ref, function_ref, changes))
    return _rpc_reply(message_id)


@app.get("/capabilities")
def capabilities():
    return {"vendorName": VENDOR, "supportedServices": ["PROV", "FM", "PM", "HEARTBEAT"],
            "supportedVendorModes": ["O1_NETCONF"]}


@app.get("/objects/{managed_object_ref}")
def query_object(managed_object_ref: str, function_ref: str | None = None):
    """Introspection of one managed function's running configuration."""
    return {"managedObjectRef": managed_object_ref, "functionRef": function_ref,
            "attributes": store.current(managed_object_ref, function_ref)}


# ---------------------------------------------------------------- generated: control API (the CLI's surface)

class CounterRequest(BaseModel):
    counters: dict[str, float] = Field(..., description="PM counter type -> value, e.g. {'DL_PRB_UTILIZATION': 18.4}")
    cellId: str = CELL


class AlarmRequest(BaseModel):
    sourceAlarmId: str
    severity: str = "critical"
    probableCause: str = "SIMULATED_FAULT"
    specificProblem: str | None = None
    cellId: str = CELL


class ConfigRequest(BaseModel):
    functionRef: str | None = None
    attributes: dict[str, str]


class FaultRequest(BaseModel):
    mode: str
    count: int = 1


class GeneratorRequest(BaseModel):
    action: str = Field(..., pattern="^(start|stop)$")
    intervalSeconds: float = 10.0
    cellId: str = CELL


@app.get("/control/status")
def status():
    return {"managedElementRef": ME, "cellId": CELL, "vendor": VENDOR, "adaptorUri": PUBLIC_URI,
            "registration": _registration or None, "alarms": store.alarms, "faults": store.faults,
            "generator": _generator.status(), "lastEventSeq": events.last_seq,
            "config": store.current(ME, _function_ref(CELL))}


@app.post("/control/register")
def register():
    """Self-register with RAN NF OAM, send the first heartbeat, subscribe the PM counters."""
    result = _oam(northbound.register, ME, PUBLIC_URI, VENDOR)
    _registration.update(result)
    events.emit("endpoint.registered", managedElementRef=ME, **result)
    return result


@app.post("/control/heartbeat")
def heartbeat():
    if "endpointId" not in _registration:
        raise HTTPException(409, "not registered")
    _oam(northbound.heartbeat, _registration["endpointId"])
    events.emit("endpoint.heartbeat", endpointId=_registration["endpointId"])
    return {"endpointId": _registration["endpointId"]}


@app.post("/control/counters")
def counters(body: CounterRequest):
    """Report PM counter values for one cell, time-stamped now."""
    _oam(northbound.report_counters, ME, body.cellId, body.counters)
    events.emit("pm.reported", cellId=body.cellId, counters=body.counters)
    return {"reported": body.counters}


@app.post("/control/alarms", status_code=201)
def raise_alarm(body: AlarmRequest):
    raised = _oam(northbound.raise_alarm, ME, _function_ref(body.cellId), body.sourceAlarmId, body.severity,
                  body.probableCause, body.specificProblem or body.sourceAlarmId)
    store.alarms[raised["alarmId"]] = {"sourceAlarmId": body.sourceAlarmId, "cellId": body.cellId,
                                       "severity": body.severity}
    events.emit("alarm.raised", alarmId=raised["alarmId"], sourceAlarmId=body.sourceAlarmId, severity=body.severity,
                cellId=body.cellId)
    return raised


@app.post("/control/alarms/{alarm_id}/clear")
def clear_alarm(alarm_id: str):
    """`alarm_id` is the RAN NF OAM alarmId, or the source alarm id of one this adaptor raised."""
    match = next((a for a, v in store.alarms.items() if a == alarm_id or v["sourceAlarmId"] == alarm_id), None)
    if match is None:
        raise HTTPException(404, f"no alarm {alarm_id} raised by this adaptor")
    _oam(northbound.clear_alarm, match, OPERATOR)
    events.emit("alarm.cleared", alarmId=match, sourceAlarmId=store.alarms.pop(match)["sourceAlarmId"])
    return {"cleared": match}


@app.post("/control/config")
def set_config(body: ConfigRequest):
    """Change the running configuration locally, as if the node changed it itself (no RAN NF OAM involved)."""
    function_ref = body.functionRef or _function_ref(CELL)
    running = store.merge(ME, function_ref, body.attributes)
    events.emit("config.local", functionRef=function_ref, changes=body.attributes, running=running)
    return {"functionRef": function_ref, "attributes": running}


@app.post("/control/faults", status_code=201)
def inject_fault(body: FaultRequest):
    """Make the next `count` edit-configs misbehave: TIMEOUT (HTTP 504), RPC_ERROR, IGNORE_WRITE."""
    if body.mode not in FAULT_MODES:
        raise HTTPException(422, f"mode must be one of {FAULT_MODES}")
    store.faults.append({"mode": body.mode, "count": body.count})
    events.emit("fault.injected", mode=body.mode, count=body.count)
    return {"faults": store.faults}


@app.delete("/control/state", status_code=204)
def reset_state():
    """Forget configuration, alarms and faults (the registration with RAN NF OAM is kept)."""
    store.reset()
    events.emit("state.reset")


@app.get("/events")
def get_events(since: int = 0, wait: float = 0.0, limit: int = 200):
    """Events after sequence `since`; with `wait` > 0 the call blocks until one arrives (max 30 s)."""
    return {"items": events.since(since, min(wait, 30.0), limit), "lastSeq": events.last_seq}


# ---------------------------------------------------------------- generated: periodic counters

class Generator:
    """A random walk of PRB utilisation and connected UEs, reported every interval. Not a model of a real cell."""

    def __init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.interval = 0.0
        self.cell = CELL

    def status(self) -> dict:
        return {"running": bool(self._thread and self._thread.is_alive()), "intervalSeconds": self.interval, "cellId": self.cell}

    def start(self, interval: float, cell: str) -> None:
        self.stop()
        self._stop = threading.Event()
        self.interval, self.cell = interval, cell
        self._thread = threading.Thread(target=self._run, args=(self._stop,), name="counter-generator", daemon=True)
        self._thread.start()
        events.emit("generator.started", intervalSeconds=interval, cellId=cell)

    def stop(self) -> None:
        if self._thread and self._thread.is_alive():
            self._stop.set()
            self._thread.join(2)
            events.emit("generator.stopped")
        self._thread = None

    def _run(self, stop: threading.Event) -> None:
        prb, ue = 30.0, 6
        while not stop.wait(self.interval):
            prb = min(max(prb + random.uniform(-6, 6), 2.0), 95.0)
            ue = min(max(ue + random.randint(-2, 2), 0), 60)
            values = {"DL_PRB_UTILIZATION": round(prb, 1), "RRC_CONNECTED_UE": ue}
            try:
                northbound.report_counters(ME, self.cell, values)
                events.emit("pm.reported", cellId=self.cell, counters=values, generated=True)
            except oam.OamError as exc:
                events.emit("northbound.error", error=str(exc))


_generator = Generator()


@app.post("/control/generator")
def generator(body: GeneratorRequest):
    if body.action == "start":
        _generator.start(body.intervalSeconds, body.cellId)
    else:
        _generator.stop()
    return _generator.status()
