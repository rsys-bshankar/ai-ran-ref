"""Generic O1-CM intent handler — Wave 8 (HISTORY.md W8-07,
decision D-1).

SA SMOS registers itself with Intent Service as an IntentHandlingFunction
(RMIH `sa-smos`) for RAN_SUBNETWORK expectations whose targets are O1 CM
attributes, named `<IOC>.<attribute>` (TS 28.541 class and attribute), e.g.
`NRCellDU.administrativeState` or `CESManagementFunction.energySavingControl`.
Those are generic TS 28.312 ExpectationTargets (the families allow any
target name beside their specialised ones), so any rApp can express
"set this CM attribute on these cells" as an Intent, and an AUTONOMOUS or
operator-resolved ASSIST AutonomyDispatch becomes a real O1 change.

When Intent Service pushes a new Intent here, the handler:

  1. reads the Intent back (Intent Service is the source of truth);
  2. for each expectation, takes `expectationObject.objectInstance` as the
     managed element and its `Cell` object context (if any) as the cells,
     and turns every `IS_EQUAL_TO` CM target into an attribute change per
     cell (`managedFunctionRef` = `<IOC>=<cell>`);
  3. writes them through DME's O1 action mediation (`POST /dme/actions`,
     which forwards to RAN NF OAM's config jobs — the same audited path a
     manual CM write takes), one action per expectation, carrying the
     intent/expectation ids as its source context;
  4. publishes the Intent's IntentReport: FULFILLED when every change was
     applied, otherwise NOT_FULFILLED / DEGRADED, with the DME action and
     RAN NF OAM job references in `additionalFulfilmentInfo` (W6-04).

Each enactment is recorded (`GET /o1-cm-handler/enactments`).
"""

import json
import os
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.pagination import PageLimit, PageOffset, paginate
from smo_shared.r1_client import R1Client

from .models import O1CmEnactment

router = APIRouter()
_r1 = R1Client()

RMIH_ID = "sa-smos"
REQUESTED_BY = "sa-smos:o1-cm-intent-handler"
# Where Intent Service pushes new Intents for this handler (in-cluster).
HANDLER_URL = os.environ.get("SA_SMOS_O1_CM_HANDLER_URL", "http://sa-smos:8000/o1-cm-handler/intents")

# The CM targets this handler enacts by default, with their allowed values
# (TS 28.541 / TS 28.623 enums). A registration may declare others.
DEFAULT_CM_TARGETS = {
    "NRCellDU.administrativeState": ["LOCKED", "UNLOCKED"],
    "CESManagementFunction.energySavingControl": ["TO_BE_ENERGY_SAVING", "TO_BE_NOT_ENERGY_SAVING"],
    # Wave 10.2 (W10.2-06): a neighbour relation's CIO. The value (six
    # QOffsetRange dB entries) isn't enumerable here — RAN NF OAM's schema
    # pre-check validates the write against the vendor's data model.
    "NRCellRelation.cellIndividualOffset": [],
    # Wave 10.3 (W10.3-06): a cell's coverage knobs (integers, schema-checked
    # by RAN NF OAM like the CIO)
    "CommonBeamformingFunction.digitalTilt": [],
    "NRSectorCarrier.configuredMaxTxPower": [],
    # Wave 10.4 (W10.4-06): a cell's idle-mode reselection priority towards a
    # frequency layer (Cell-context values name NRFreqRelation=<cell>-<layer>)
    "NRFreqRelation.cellReselectionPriority": [],
}
SUCCESS_STATUSES = {"COMPLETED"}
ACTION_ID_NAMESPACE = uuid.UUID("6f2b8c1e-3d4a-5b6c-8d9e-0a1b2c3d4e5f")


class RegistrationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cmTargets: dict[str, list[str]] | None = None  # "<IOC>.<attribute>" -> allowed values
    intentHandlingScope: list[str] = ["RAN"]


class IntentNotification(BaseModel):
    """What Intent Service pushes to a handling function on CreateIntent."""
    intentId: uuid.UUID


def _cm_targets_from_registration() -> dict[str, list[str]]:
    """The CM targets as currently registered with Intent Service (its
    capability list is the source of truth); defaults if unregistered."""
    try:
        resp = _r1.get("/intent-service/intent-handling-functions")
        if resp.status_code == 200:
            for fn in resp.json().get("items", []):
                if fn["rmihId"] == RMIH_ID:
                    targets = {}
                    for cap in fn["attributes"]["intentHandlingCapabilityList"]:
                        for t in cap.get("supportedExpectationTargetInfoList") or []:
                            targets[t["supportedTargetName"]] = t.get("supportedTargetValueRange") or []
                    return targets
    except Exception:  # noqa: BLE001, S110 — fall back to the defaults
        pass
    return DEFAULT_CM_TARGETS


@router.post("/o1-cm-handler/registration", status_code=201)
def register_o1_cm_handler(body: RegistrationBody = RegistrationBody()):
    """Registers SA SMOS with Intent Service as the O1-CM intent handling
    function. Idempotent: an existing registration is replaced."""
    targets = body.cmTargets or DEFAULT_CM_TARGETS
    for name in targets:
        if "." not in name:
            raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED, detail=f"CM target {name!r} must be <IOC>.<attribute>")
    _r1.delete(f"/intent-service/intent-handling-functions/{RMIH_ID}")
    resp = _r1.post("/intent-service/intent-handling-functions", json={
        "rmihId": RMIH_ID, "smeServiceId": "sa-smos-o1-cm-intent-handler", "notificationDestination": HANDLER_URL,
        "intentHandlingScope": body.intentHandlingScope,
        "intentHandlingCapabilityList": [{
            "intentHandlingCapabilityId": "o1-cm", "supportedExpectationObjectType": "RAN_SUBNETWORK",
            "supportedExpectationTargetInfoList": [
                {"supportedTargetName": name, "supportedTargetCondition": "IS_EQUAL_TO", "supportedTargetValueRange": values}
                for name, values in targets.items()],
        }],
    })
    if resp.status_code != 201:
        raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED, detail=f"Intent Service refused registration: {resp.json()}")
    return {"rmihId": RMIH_ID, "cmTargets": targets, "notificationDestination": HANDLER_URL}


@router.delete("/o1-cm-handler/registration", status_code=204)
def deregister_o1_cm_handler():
    _r1.delete(f"/intent-service/intent-handling-functions/{RMIH_ID}")


def _cells(expectation: dict) -> list[str]:
    cells = []
    for ctx in (expectation["expectationObject"].get("objectContexts") or []):
        if ctx.get("contextAttribute") != "Cell":
            continue
        values = ctx.get("contextValueRange")
        for v in values if isinstance(values, list) else [values]:
            if isinstance(v, dict):
                v = v.get("cellLocalId", v.get("nCI", v.get("id")))
            if v is not None:
                cells.append(str(v))
    return cells


def _changes_for(expectation: dict, cm_targets: dict[str, list[str]]) -> tuple[list[dict], list[dict]]:
    """CM attribute changes for one expectation, and the targets it can't
    enact (with why)."""
    element = expectation["expectationObject"].get("objectInstance")
    changes, unsupported = [], []
    for target in expectation["expectationTargets"]:
        name, value = target["targetName"], target["targetValueRange"]
        allowed = cm_targets.get(name)
        reason = None
        if allowed is None:
            reason = "not a CM target of this handler"
        elif target["targetCondition"] != "IS_EQUAL_TO":
            reason = "CM targets are set with IS_EQUAL_TO"
        elif allowed and value not in allowed:
            reason = f"value must be one of {allowed}"
        elif not element:
            reason = "expectationObject.objectInstance (the managed element) is required"
        if reason:
            unsupported.append({"expectationId": expectation["expectationId"], "targetName": name, "reason": reason})
            continue
        ioc, attribute = name.split(".", 1)
        for cell in _cells(expectation) or [None]:
            change = {"managedElementRef": element, "className": ioc, "attributeChanges": {attribute: value}}
            if cell is not None:
                change["managedFunctionRef"] = f"{ioc}={cell}"
            changes.append(change)
    return changes, unsupported


@router.post("/o1-cm-handler/intents")
def enact_intent(body: IntentNotification, db: Session = Depends(get_session)):
    """Intent Service's push for a new Intent addressed to this handler."""
    resp = _r1.get(f"/intent-service/intents/{body.intentId}")
    if resp.status_code != 200:
        raise framework_error(FrameworkError.INTENT_NOT_FOUND, detail=f"no such intent {body.intentId}")
    intent = resp.json()
    if intent["rmihId"] != RMIH_ID:
        raise framework_error(FrameworkError.RMIH_CAPABILITY_MISMATCH, detail="intent is addressed to another handler")
    if intent["intentAdminState"] != "ACTIVATED":
        return {"intentId": str(body.intentId), "status": "SKIPPED", "reason": "intent is DEACTIVATED"}

    cm_targets = _cm_targets_from_registration()
    actions, unsupported, expectation_results = [], [], []
    for expectation in intent["attributes"]["intentExpectations"]:
        changes, bad = _changes_for(expectation, cm_targets)
        unsupported.extend(bad)
        applied = None
        if changes:
            # Wave 10.1 (W10-18): the action id is derived from the intent and
            # expectation, so a re-pushed Intent replays the same action —
            # which DME ignores — instead of writing the change twice.
            action = _r1.post("/dme/actions", json={
                "requestedBy": REQUESTED_BY, "changes": changes,
                "actionId": str(uuid.uuid5(ACTION_ID_NAMESPACE, f"{body.intentId}:{expectation['expectationId']}")),
                "sourceContext": {"intentId": str(body.intentId), "expectationId": expectation["expectationId"],
                                  "rmioId": intent["rmioId"]},
            })
            payload = action.json() if action.status_code in (200, 202) else {"status": f"HTTP_{action.status_code}"}
            status = payload.get("originalStatus") if payload.get("status") == "IGNORED" else payload.get("status")
            actions.append({"expectationId": expectation["expectationId"], "actionId": payload.get("actionId"),
                            "forwardedJobId": payload.get("forwardedJobId"), "status": status,
                            "replayed": payload.get("status") == "IGNORED"})
            applied = status in SUCCESS_STATUSES
        bad_names = {b["targetName"] for b in bad}
        ok = applied is True and not bad_names
        info = {"fulfilmentStatus": "FULFILLED"} if ok else {"fulfilmentStatus": "NOT_FULFILLED", "notFullfilledState": "DEGRADED"}
        expectation_results.append({
            "expectaitonId": expectation["expectationId"], "expectationFulfilmentInfo": info,
            "targetFulfilmentResults": [
                {"targetName": t["targetName"],
                 "targetFulfilmentInfo": {"fulfilmentStatus": "FULFILLED"} if (applied and t["targetName"] not in bad_names)
                 else {"fulfilmentStatus": "NOT_FULFILLED", "notFullfilledState": "DEGRADED"},
                 **({"targetAchievedValue": t["targetValueRange"]} if (applied and t["targetName"] not in bad_names) else {})}
                for t in expectation["expectationTargets"]],
        })

    fulfilled = all(r["expectationFulfilmentInfo"]["fulfilmentStatus"] == "FULFILLED" for r in expectation_results)
    status = "FULFILLED" if fulfilled else "NOT_FULFILLED"
    report = {"intentReference": str(body.intentId), "intentFulfilmentReport": {
        "intentFulfilmentInfo": {"fulfilmentStatus": "FULFILLED"} if fulfilled else {
            "fulfilmentStatus": "NOT_FULFILLED", "notFullfilledState": "DEGRADED",
            "notFulfilledReasons": [f"{u['targetName']}: {u['reason']}" for u in unsupported]
                                   + [f"action {a['actionId']} {a['status']}" for a in actions if a["status"] not in SUCCESS_STATUSES]},
        "expectationFulfilmentResult": expectation_results,
        "additionalFulfilmentInfo": json.dumps({"actions": actions}),
    }}
    published = _r1.post("/intent-service/intent-reports", json=report)
    report_id = published.json().get("reportId") if published.status_code == 201 else None

    enactment = O1CmEnactment(intent_id=body.intentId, status=status, actions=actions, unsupported_targets=unsupported,
                              intent_report_id=uuid.UUID(report_id) if report_id else None)
    db.add(enactment)
    db.commit()
    return _enactment_view(enactment)


def _enactment_view(e: O1CmEnactment) -> dict:
    return {"enactmentId": str(e.enactment_id), "intentId": str(e.intent_id), "status": e.status, "actions": e.actions,
            "unsupportedTargets": e.unsupported_targets,
            "intentReportId": str(e.intent_report_id) if e.intent_report_id else None, "createdAt": e.created_at.isoformat()}


@router.get("/o1-cm-handler/enactments")
def list_enactments(intent_id: uuid.UUID | None = None, limit: int = PageLimit, offset: int = PageOffset,
                    db: Session = Depends(get_session)):
    stmt = select(O1CmEnactment)
    if intent_id:
        stmt = stmt.where(O1CmEnactment.intent_id == intent_id)
    page = paginate(db, stmt.order_by(O1CmEnactment.created_at.desc()), limit, offset)
    return {**page, "items": [_enactment_view(e) for e in page["items"]]}
