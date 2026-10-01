"""Mock O1 Adaptor — the isolated NETCONF-shaped test double RAN NF OAM's
own CM write path needs to prove a real HTTP round trip, the same role
mock-near-rt-ric already plays for A1 Related.

RAN NF OAM LLD section 5.1's PATCH step (ran-nf-oam/app/netconf_client.py)
dispatches a real RFC 6241 <edit-config> RPC — as XML over plain HTTP, not
a real SSH/NETCONF transport, matching this build's all-HTTP-JSON
pragmatism everywhere else (e.g. R1Client) — to a ManagedElement's
registered adaptor_uri. Before this module existed, nothing in this
build's own docker-compose topology ever answered that URL for real: the
real O-RAN-SC reference (sim-o1-interface's ntsim-ng) is a full
YANG-model-validated NETCONF/SSH network simulator, out of proportion
with this build's single-Python/FastAPI-stack consolidation (the same
"ADOPT repos stay pattern references only" boundary already documented
elsewhere, HISTORY.md §2). This is the honest, minimal
substitute: just enough real NETCONF-shaped XML parsing to close the loop
RAN NF OAM's own dispatch client was already built to reach, mirroring
mock-near-rt-ric's own "give the real caller something real to call, not
a full protocol implementation" scope.
"""

import os
from xml.sax.saxutils import escape, quoteattr

import defusedxml.ElementTree as ET
from defusedxml.common import DefusedXmlException
from fastapi import FastAPI, Request, Response
from pydantic import BaseModel

app = FastAPI(title="Mock O1 Adaptor (NETCONF test double)")

NETCONF_BASE_NS = "urn:ietf:params:xml:ns:netconf:base:1.0"

_applied_changes: dict[str, dict] = {}  # managed-object ref -> last applied attribute_changes, for real test assertions

# Wave 10.1 (W10-17): the running configuration of each managed function
# addressed with `function-ref` (e.g. NRCellDU=101 inside gnb-du-01) —
# merged writes, read back by <get-config>. A function never written reads
# as its IOC's defaults: a cell is UNLOCKED and not energy saving.
_object_state: dict[tuple[str, str | None], dict] = {}
IOC_DEFAULTS = {
    "NRCellDU": {"administrativeState": "UNLOCKED", "operationalState": "ENABLED"},
    "CESManagementFunction": {"energySavingControl": "TO_BE_NOT_ENERGY_SAVING", "energySavingState": "IS_NOT_ENERGY_SAVING"},
    # Wave 10.2 (W10.2-04): a neighbour relation's CIO — TS 28.541's six
    # QOffsetRange entries (dB) — and whether handover over it is allowed
    "NRCellRelation": {"cellIndividualOffset": "[0, 0, 0, 0, 0, 0]", "isHOAllowed": "true", "isMLBAllowed": "true"},
    # the gNB's own distributed MRO (TS 28.541 DMROFunction) and its bounds
    "DMROFunction": {"dmroControl": "true", "maximumDeviationHoTriggerLow": "-12", "maximumDeviationHoTriggerHigh": "12",
                     "minimumTimeBetweenHoTriggerChange": "10", "tstoreUEcntxt": "100"},
    # Wave 10.3 (W10.3-04): a cell's coverage knobs — its beam's digital tilt
    # (TS 28.541, tenths of a degree, positive = downtilt) and its sector
    # carrier's maximum transmit power (dBm in this build)
    "CommonBeamformingFunction": {"digitalTilt": "60", "digitalAzimuth": "0", "coverageShape": "0"},
    "NRSectorCarrier": {"configuredMaxTxPower": "43", "txDirection": "DL_AND_UL"},
    # Wave 10.4 (W10.4-04): a cell's idle-mode relation to a frequency layer
    # (NRFreqRelation=<cell>-<layer>) — the reselection priority it broadcasts
    "NRFreqRelation": {"cellReselectionPriority": "5", "qOffsetFreq": "0"},
}
# TS 28.541 CESManagementFunction: energySavingState follows energySavingControl
ENERGY_SAVING_STATE = {"TO_BE_ENERGY_SAVING": "IS_ENERGY_SAVING", "TO_BE_NOT_ENERGY_SAVING": "IS_NOT_ENERGY_SAVING"}

# Test-only fault injection (see POST /faults): each entry is consumed by
# the next matching edit-config.
_faults: list[dict] = []


def _defaults(function_ref: str | None) -> dict:
    return dict(IOC_DEFAULTS.get((function_ref or "").split("=", 1)[0], {}))


def _current(ref: str, function_ref: str | None) -> dict:
    return {**_defaults(function_ref), **_object_state.get((ref, function_ref), {})}


def _take_fault(ref: str, function_ref: str | None) -> str | None:
    for fault in _faults:
        target = fault.get("managedObjectRef")
        if target in (None, ref, f"{ref}/{function_ref}"):
            fault["count"] -= 1
            if fault["count"] <= 0:
                _faults.remove(fault)
            return fault["mode"]
    return None


@app.post("/edit-config")
async def edit_config(request: Request) -> Response:
    """Mirrors netconf_client.py's own build_edit_config_rpc/send_edit_config
    exactly: parses the real <rpc><edit-config>...</edit-config></rpc>
    request, replies <rpc-reply><ok/></rpc-reply> (RFC 6241 section 4.2) on
    success. REJECTED (a real <rpc-error>, same as a real NETCONF agent
    refusing a request) when the managed-object ref is missing, or its
    config body is empty for any operation other than delete/remove — a
    delete legitimately carries no attribute_changes at all (RFC 6241
    section 7.2's `operation` attribute), so rejecting it for emptiness
    would be wrong. Non-delete/remove emptiness rejection is the same
    "empty payload is a real, testable rejection trigger" pattern
    mock-near-rt-ric's own create_policy already established for an
    empty policyObject.
    """
    body = await request.body()
    try:
        # defusedxml (not stdlib ET) — this body is attacker-reachable over
        # HTTP, so entity expansion / external-entity XML needs to be
        # rejected the same way malformed XML is, not parsed.
        root = ET.fromstring(body)
    except (ET.ParseError, DefusedXmlException):
        return _reply(message_id="0", ok=False, error_tag="malformed-message")

    message_id = root.attrib.get("message-id", "0")
    managed_object = root.find(f".//{{{NETCONF_BASE_NS}}}managed-object")
    ref = managed_object.attrib.get("ref") if managed_object is not None else None
    function_ref = managed_object.attrib.get("function-ref") if managed_object is not None else None
    if root.find(f"{{{NETCONF_BASE_NS}}}get-config") is not None:
        # Wave 10.1 (W10-20): read-after-write
        if not ref:
            return _reply(message_id, ok=False, error_tag="invalid-value")
        return _data_reply(message_id, ref, function_ref, _current(ref, function_ref))
    # RFC 6241 section 7.2's edit-config `operation` attribute — defaults
    # to "merge" per the RFC when absent, matching netconf_client.py's own
    # build_edit_config_rpc default.
    operation = managed_object.attrib.get("operation", "merge") if managed_object is not None else "merge"
    # child.tag carries the inherited default namespace (build_edit_config_rpc
    # declares xmlns once, on the <rpc> root — every descendant, including
    # each attribute-change element, inherits it) — strip it back to the
    # plain attribute name, the same rsplit("}", 1)[-1] send_edit_config's
    # own reply-parsing already uses for the same reason.
    attribute_changes = {
        child.tag.rsplit("}", 1)[-1]: child.text for child in managed_object
    } if managed_object is not None else {}

    if not ref or (not attribute_changes and operation not in ("delete", "remove")):
        return _reply(message_id, ok=False, error_tag="invalid-value")

    fault = _take_fault(ref, function_ref)
    if fault == "TIMEOUT":
        return Response(status_code=504, content="agent did not answer in time")
    if fault == "RPC_ERROR":
        return _reply(message_id, ok=False, error_tag="operation-failed")

    if operation in ("delete", "remove"):
        _applied_changes.pop(ref, None)
        _object_state.pop((ref, function_ref), None)
    elif fault != "IGNORE_WRITE":  # IGNORE_WRITE: acknowledged but never applied (a lying agent)
        _applied_changes[ref] = attribute_changes
        state = {**_object_state.get((ref, function_ref), {}), **attribute_changes}
        if "energySavingControl" in attribute_changes:
            state["energySavingState"] = ENERGY_SAVING_STATE.get(attribute_changes["energySavingControl"], "IS_NOT_ENERGY_SAVING")
        _object_state[(ref, function_ref)] = state
    return _reply(message_id, ok=True)


@app.get("/capabilities")
def declare_capabilities():
    """The vendor capability declaration RAN NF OAM's vendor onboarding
    flow discovers (Wave 9 W9-03, `POST /ran-nf-oam/vendor-onboarding`
    with `discoverFrom`, read at this fixed path on the adaptor's registered
    origin): which vendor this adaptor fronts, which MnS
    services it implements and which O1 transports it speaks. Configurable
    per deployment so one image can stand in for several vendors.
    """
    return {
        "vendorName": os.environ.get("MOCK_O1_VENDOR_NAME", "mock-vendor"),
        "supportedServices": os.environ.get("MOCK_O1_SUPPORTED_SERVICES",
                                            "PROV,FM,PM,FILE,STREAM,SWM,SUBSCRIPTION,HEARTBEAT").split(","),
        "supportedVendorModes": os.environ.get("MOCK_O1_VENDOR_MODES", "O1_NETCONF").split(","),
    }


@app.get("/edit-config/{managed_object_ref}")
def query_last_applied(managed_object_ref: str):
    """Not part of the real NETCONF RPC surface — a test-only introspection
    route (matching this build's own `_policies`-dict-backed test doubles
    elsewhere) so a real integration test can assert what was actually
    applied, not just that the call returned 200.
    """
    return {"managedObjectRef": managed_object_ref, "attributeChanges": _applied_changes.get(managed_object_ref)}


class FaultBody(BaseModel):
    mode: str  # TIMEOUT (HTTP 504) | RPC_ERROR (<rpc-error>) | IGNORE_WRITE (<ok/> but not applied)
    count: int = 1
    managedObjectRef: str | None = None  # "<ref>" or "<ref>/<function-ref>"; omitted = any


@app.post("/faults", status_code=201)
def inject_fault(body: FaultBody):
    """Test-only (like GET /edit-config/{ref}): make the next `count`
    matching edit-configs misbehave, so the SMO's retry, verification and
    rollback paths can be exercised against a real round trip."""
    if body.mode not in ("TIMEOUT", "RPC_ERROR", "IGNORE_WRITE"):
        return Response(status_code=422, content=f"unknown fault mode {body.mode}")
    _faults.append(body.model_dump())
    return {"faults": _faults}


@app.delete("/state", status_code=204)
def reset_state():
    """Test-only: forget every applied change and pending fault."""
    _applied_changes.clear()
    _object_state.clear()
    _faults.clear()


@app.get("/objects/{managed_object_ref}")
def query_object(managed_object_ref: str, function_ref: str | None = None):
    """Test-only introspection of one managed function's running config."""
    return {"managedObjectRef": managed_object_ref, "functionRef": function_ref,
            "attributes": _current(managed_object_ref, function_ref)}


def _data_reply(message_id: str, ref: str, function_ref: str | None, attributes: dict) -> Response:
    # every value echoed back is escaped — it came from the request
    function = f" function-ref={quoteattr(function_ref)}" if function_ref else ""
    body = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in attributes.items())
    return Response(content=(f"<rpc-reply message-id={quoteattr(message_id)} xmlns=\"{NETCONF_BASE_NS}\"><data>"
                             f"<managed-object ref={quoteattr(ref)}{function}>{body}</managed-object></data></rpc-reply>"),
                    media_type="application/xml")


def _reply(message_id: str, *, ok: bool, error_tag: str | None = None) -> Response:
    if ok:
        body = f'<rpc-reply message-id="{message_id}" xmlns="{NETCONF_BASE_NS}"><ok/></rpc-reply>'
    else:
        body = (
            f'<rpc-reply message-id="{message_id}" xmlns="{NETCONF_BASE_NS}">'
            f"<rpc-error><error-type>application</error-type><error-tag>{error_tag}</error-tag>"
            f"<error-severity>error</error-severity></rpc-error></rpc-reply>"
        )
    return Response(content=body, media_type="application/xml")
