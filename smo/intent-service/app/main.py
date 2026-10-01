"""Intent Service (formerly "Policy Management & Info SMOS" — renamed in
Wave 1 of the AI Platform Service Decomposition: this module's own
surface was already entirely Intent-shaped, with no policy/rule/
constraint code to leave behind under the old name. See
docs/ownership/INTENT_SERVICE_OWNERSHIP.md.

SMO Design v1.3 section 3.12, extended by Policy Mgmt LLD sections 1-3:
UpdateIntentAdminState and QueryIntent close operations v1.3 never had
(intentAdminState existed with nothing to change it), and
DeregisterIntentHandlingFunction restores register/deregister symmetry.

Wave 3 (docs/ownership/INTENT_SERVICE_OWNERSHIP.md's "Open item carried
into Wave 3"): CreateIntent now uses consumer-side RMIH selection — the
caller addresses a specific, already-registered IntentHandlingFunction
by `rmihId` — matching TS28312_IntentNrm.yaml's own NRM containment
(IntentHandlingFunction *contains* Intent), replacing the former
producer-side push that matched and notified every capability-matching
function after the fact. A real consequence of that containment model:
DeregisterIntentHandlingFunction now really does end every Intent still
addressed to it (ON DELETE CASCADE), not just leave a dangling reference.
"""

import uuid
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.identity import is_framework_internal_identity
from smo_shared.pagination import PageLimit, PageOffset, paginate
from smo_shared.openapi_security import apply_r1_gateway_security
from smo_shared.correlation import apply_correlation_id
from smo_shared.r1_client import R1Client

from .models import AutonomyDispatch, Intent, IntentHandlingFunction, IntentReport

app = FastAPI(title="Intent Service")
apply_r1_gateway_security(app)
apply_correlation_id(app)

# OPEN_ITEMS.md section 6.3: cross-module read of a RAppInstance's own
# autonomyMode/regionScope (rApp Mgmt) — this module's first cross-module
# call; every other route here is purely local.
_r1 = R1Client()


@app.get("/health")
def health_check():
    """Liveness probe. The GUI BFF's GET /modules/status fans out to
    /<module>/health through R1 Termination for every module in parallel,
    so every module answers one — previously only ran-nf-oam/a1-related
    did (as their own DME producer-health callback URL).
    """
    return {"status": "healthy"}


class CreateIntentRequest(BaseModel):
    expectations: list[dict]
    priority: int = 1
    rmioId: str = ""
    # Wave 3 (docs/ownership/INTENT_SERVICE_OWNERSHIP.md's "Open item
    # carried into Wave 3"): consumer-side RMIH selection — the caller
    # names which already-registered IntentHandlingFunction this Intent
    # is addressed to, matching TS28312_IntentNrm.yaml's own NRM
    # containment (IntentHandlingFunction *contains* Intent) instead of
    # this build's former producer-side push-after-creation matching
    # across every registered function. Required: an Intent with no
    # target RMIH has nowhere to be contained, per that same model.
    rmihId: str
    # SPEC_AUDIT.md items 2-3: TS28312_IntentNrm.yaml's real
    # matching-relevant field is each expectation's own
    # `expectationObject.objectType` (a closed 5-value enum), not a
    # top-level Intent field at all — this build previously invented a
    # free-form top-level `intentType` string with no shared vocabulary
    # with the spec, AND stored it into `intent_mgmt_purpose`, even
    # though the spec's real `intentMgmtPurpose` is an unrelated
    # workflow-procedure enum (FEASIBILITYCHECK/.../
    # FULFILMENT_WITH_NEGOTIATION). Both fixed: matching now reads
    # `expectations[].expectationObject.objectType` (see
    # `_requested_expectation_object_types`), and this field now
    # carries the spec's own real semantics, with its own real default.
    intentMgmtPurpose: Literal[
        "FEASIBILITYCHECK", "FEASIBILITYCHECK_WITH_RECOMMENDATIONS", "FULFILMENT_WITHOUT_NEGOTIATION",
        "EXPLORATION", "FULFILMENT_WITH_NEGOTIATION",
    ] = "FULFILMENT_WITHOUT_NEGOTIATION"
    # SPEC_AUDIT.md item 5 (formerly 1): TS28312_IntentNrm.yaml's
    # IntentHandlingScope is a closed 2-value enum (RAN/CN) — not persisted
    # on Intent itself in the real spec either (it's IntentHandlingFunction's
    # own declared coverage). Wave 3: now a create-time validation against
    # the one named target RMIH's own declared scope, not a pre-filter
    # across many candidates.
    intentHandlingScope: Literal["RAN", "CN"] | None = None


class AdminStateRequest(BaseModel):
    newState: str
    requesterId: str


class IntentReportRequest(BaseModel):
    intentId: uuid.UUID
    fulfilmentReport: dict
    conflictReports: list | None = None


class RegisterRmihRequest(BaseModel):
    rmihId: str
    smeServiceId: str
    # Each capability dict's matching-relevant key is
    # `supportedExpectationObjectType` (SPEC_AUDIT.md items 2-3;
    # TS28312_IntentNrm.yaml's real `IntentHandlingCapability` field,
    # a closed 4-value enum: RAN_SUBNETWORK/EDGE_SERVICE_SUPPORT/
    # 5GC_SUBNETWORK/RADIO_SERVICE) — kept as a plain dict, not a full
    # typed sub-schema, matching this build's own pre-existing looseness
    # here; only the field `_matching_rmihs` actually reads was renamed.
    capabilities: list[dict]
    # Wave 3 (cross-cutting standardization, Subscriptions): renamed from
    # notificationCallbackUri — not a real TS28312_IntentNrm.yaml field
    # name (grepped: the real spec never names this callback at all),
    # unified with every other subscription-shaped resource's own
    # callback field (DME/A1-Related's own notificationDestination).
    notificationDestination: str
    # SPEC_AUDIT.md item 5: TS28312_IntentNrm.yaml's IntentHandlingScope is
    # a closed 2-value enum (RAN/CN) — was untyped JSON, never set by any
    # caller. None means "no declared scope restriction" (matches anything).
    intentHandlingScope: list[Literal["RAN", "CN"]] | None = None


class CreateAutonomyDispatchRequest(BaseModel):
    instanceId: uuid.UUID
    modelId: uuid.UUID | None = None
    expectations: list[dict]
    priority: int = 1
    rmihId: str
    intentMgmtPurpose: Literal[
        "FEASIBILITYCHECK", "FEASIBILITYCHECK_WITH_RECOMMENDATIONS", "FULFILMENT_WITHOUT_NEGOTIATION",
        "EXPLORATION", "FULFILMENT_WITH_NEGOTIATION",
    ] = "FULFILMENT_WITHOUT_NEGOTIATION"
    intentHandlingScope: Literal["RAN", "CN"] | None = None
    # All three modes always notify the operator — optional only in the
    # same sense every other subscription-shaped resource's own
    # notification_destination already is (a purely poll-based consumer
    # may still omit it).
    notificationDestination: str | None = None


class ResolveAutonomyDispatchRequest(BaseModel):
    regionScope: dict


@app.post("/intents", status_code=201)
def create_intent(body: CreateIntentRequest, db: Session = Depends(get_session)):
    """CreateIntent — Wave 3 (docs/ownership/INTENT_SERVICE_OWNERSHIP.md's
    "Open item carried into Wave 3"): the caller now addresses a specific,
    already-registered RMIH by `rmihId` (consumer-side selection,
    TS28312_IntentNrm.yaml's own NRM containment — IntentHandlingFunction
    *contains* Intent), replacing the former producer-side push that
    matched and notified every capability-matching RMIH after the fact.

    404 if `rmihId` names no registered function at all. If the Intent
    declares expectation object types and/or a handling scope, the named
    RMIH must actually cover them (`RMIH_CAPABILITY_MISMATCH`, 422) —
    addressing an Intent to a handler that can't fulfil it is rejected at
    creation, not silently accepted. Dispatch to that one RMIH is
    best-effort: a callback failure never blocks CreateIntent succeeding.
    """
    intent = _create_intent_row(db, rmih_id=body.rmihId, expectations=body.expectations, priority=body.priority,
                                 rmio_id=body.rmioId, intent_mgmt_purpose=body.intentMgmtPurpose,
                                 intent_handling_scope=body.intentHandlingScope)
    return {"intentId": str(intent.intent_id)}


def _create_intent_row(db: Session, rmih_id: str, expectations: list[dict], priority: int, rmio_id: str,
                        intent_mgmt_purpose: str | None, intent_handling_scope: str | None) -> Intent:
    """The real work CreateIntent does — factored out so OPEN_ITEMS.md
    section 6.3's own AUTONOMOUS/resolve-ASSIST paths (request_autonomy_dispatch/
    resolve_autonomy_dispatch, below) can create a real Intent the exact
    same validated way, rather than duplicating this logic. 404/422 and
    the best-effort RMIH notification are all identical to CreateIntent's
    own direct-caller path — an autonomy-driven Intent is a real Intent
    in every respect, not a second, lesser kind.
    """
    fn = db.get(IntentHandlingFunction, rmih_id)
    if fn is None:
        raise framework_error(FrameworkError.INTENT_HANDLING_FUNCTION_NOT_FOUND, detail="no such intent handling function")

    expectation_object_types = _requested_expectation_object_types(expectations)
    _validate_rmih_can_handle(fn, expectation_object_types, intent_handling_scope)

    intent = Intent(intent_expectations=expectations, intent_priority=priority, rmio_id=rmio_id,
                     intent_mgmt_purpose=intent_mgmt_purpose, rmih_id=rmih_id)
    db.add(intent)
    db.commit()

    try:
        httpx.post(fn.notification_destination, json={
            "intentId": str(intent.intent_id), "expectationObjectTypes": sorted(expectation_object_types),
            "priority": intent.intent_priority, "rmioId": intent.rmio_id,
        }, timeout=5.0)
    except httpx.HTTPError:
        pass
    return intent


def _requested_expectation_object_types(expectations: list[dict]) -> set[str]:
    """TS28312_IntentNrm.yaml's `IntentExpectation.expectationObject.objectType`
    — the real field an Intent uses to say what kind of thing each
    expectation targets (RAN_SUBNETWORK/EDGE_SERVICE_SUPPORT/
    5GC_SUBNETWORK/RADIO_SERVICE/SUBNETWORK). Reads it straight out of
    the already-accepted, already-opaque `expectations` list — no
    deeper expectation grammar needed for this one field.
    """
    types: set[str] = set()
    for expectation in expectations:
        object_type = (expectation.get("expectationObject") or {}).get("objectType")
        if object_type:
            types.add(object_type)
    return types


def _validate_rmih_can_handle(fn: IntentHandlingFunction, expectation_object_types: set[str], scope: str | None) -> None:
    """Wave 3: replaces the former _matching_rmihs (which scanned every
    registered function) now that the caller names one target directly —
    this validates that one function actually covers what the Intent is
    asking for, rather than trusting the caller's addressing blindly.

    Checks TS28312_IntentNrm.yaml's real `IntentHandlingCapability` field
    (`supportedExpectationObjectType`) against the Intent's own requested
    expectation object types, plus SPEC_AUDIT.md item 5's intentHandlingScope.
    An RMIH with no declared scope (None, the pre-existing default —
    matches anything) is unaffected, and a request with no requested scope
    skips that half of the check entirely — same permissive shape the
    former multi-candidate filter already used. An Intent with no
    expectation object types at all skips the capability check too (there
    is nothing to validate against).
    """
    if scope is not None and fn.intent_handling_scope and scope not in fn.intent_handling_scope:
        raise framework_error(FrameworkError.RMIH_CAPABILITY_MISMATCH,
                               detail=f"{fn.rmih_id} does not cover handling scope {scope!r}")
    if expectation_object_types:
        supported = {cap.get("supportedExpectationObjectType") for cap in fn.intent_handling_capability_list}
        if not expectation_object_types & supported:
            raise framework_error(FrameworkError.RMIH_CAPABILITY_MISMATCH,
                                   detail=f"{fn.rmih_id} does not support any of {sorted(expectation_object_types)}")


@app.get("/intents/{intent_id}")
def query_intent(intent_id: uuid.UUID, db: Session = Depends(get_session)):
    """NEW — v1.3 had CreateIntent and subscribe/publish, but no read
    operation at all (Policy Mgmt LLD section 1).

    Wave 3: a real 404 here (rather than crashing) matters more now that
    `Intent.rmih_id`'s ON DELETE CASCADE means an Intent can legitimately
    vanish out from under a caller mid-flight, when its addressed RMIH is
    deregistered.
    """
    intent = db.get(Intent, intent_id)
    if intent is None:
        raise framework_error(FrameworkError.INTENT_NOT_FOUND, detail="no such intent")
    return _intent_view(intent)


@app.get("/intents")
def query_intents(admin_state: str | None = None, limit: int = PageLimit, offset: int = PageOffset,
                   db: Session = Depends(get_session)):
    stmt = select(Intent)
    if admin_state:
        stmt = stmt.where(Intent.intent_admin_state == admin_state)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [_intent_view(i) for i in page["items"]]}


@app.patch("/intents/{intent_id}/admin-state")
def update_intent_admin_state(intent_id: uuid.UUID, body: AdminStateRequest, db: Session = Depends(get_session)):
    """NEW — closes v1.3's gap: intentAdminState had two enum values and no
    operation ever transitioning it. RMIO-only, checked against rmioId.
    """
    intent = db.get(Intent, intent_id)
    if intent.rmio_id != body.requesterId:
        raise framework_error(FrameworkError.SERVICE_NAME_CONFLICT, detail="only the intent's creator (RMIO) may change its admin state")
    intent.intent_admin_state = body.newState
    db.commit()
    return _intent_view(intent)


@app.delete("/intents/{intent_id}", status_code=204)
def delete_intent(intent_id: uuid.UUID, db: Session = Depends(get_session)):
    """SPEC_AUDIT.md item 5: no DELETE /intents/{id} existed at all —
    an RMIO had no way to ever retract an Intent it created, only
    deactivate it (UpdateIntentAdminState). Symmetric with
    deregister_intent_handling_function's own idempotent shape below.
    """
    intent = db.get(Intent, intent_id)
    if intent is not None:
        db.delete(intent)
        db.commit()


@app.post("/intent-reports", status_code=201)
def publish_intent_report(body: IntentReportRequest, db: Session = Depends(get_session)):
    report = IntentReport(intent_id=body.intentId, intent_fulfilment_report=body.fulfilmentReport, intent_conflict_reports=body.conflictReports)
    db.add(report)
    db.commit()
    return {"reportId": str(report.id)}


@app.post("/intent-handling-functions", status_code=201)
def register_intent_handling_function(body: RegisterRmihRequest, db: Session = Depends(get_session)):
    """RegisterIntentHandlingFunction — rejected if caller is external
    (D-SEC-POLICY-1, unchanged). identity.py's helper distinguishes an
    rApp caller (always rejected here) from a framework-internal SMO
    module (SO SMOS / SA SMOS, the only legitimate callers).
    """
    if not is_framework_internal_identity(body.rmihId):
        raise framework_error(FrameworkError.SERVICE_NAME_CONFLICT, detail="external callers may never hold an rmihId (D-SEC-POLICY-1)")
    fn = IntentHandlingFunction(rmih_id=body.rmihId, sme_service_id=body.smeServiceId, intent_handling_capability_list=body.capabilities,
                                 notification_destination=body.notificationDestination, intent_handling_scope=body.intentHandlingScope)
    db.add(fn)
    db.commit()
    return {"rmihId": fn.rmih_id, "intentHandlingScope": fn.intent_handling_scope}


@app.delete("/intent-handling-functions/{rmih_id}", status_code=204)
def deregister_intent_handling_function(rmih_id: str, db: Session = Depends(get_session)):
    """NEW — symmetric with Register, closing v1.3's gap (Policy Mgmt LLD section 1)."""
    fn = db.get(IntentHandlingFunction, rmih_id)
    if fn is not None:
        db.delete(fn)
        db.commit()


def _intent_view(i: Intent) -> dict:
    return {"intentId": str(i.intent_id), "intentAdminState": i.intent_admin_state,
            "intentPriority": i.intent_priority, "rmioId": i.rmio_id, "intentMgmtPurpose": i.intent_mgmt_purpose,
            "rmihId": i.rmih_id}


@app.get("/intent-handling-functions")
def list_intent_handling_functions(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    """List read over registered RMIHs (GUI pass) — which handlers an
    Intent can actually be dispatched to was otherwise invisible."""
    page = paginate(db, select(IntentHandlingFunction), limit, offset)
    return {**page, "items": [{"rmihId": fn.rmih_id, "smeServiceId": fn.sme_service_id, "capabilities": fn.intent_handling_capability_list,
             "notificationDestination": fn.notification_destination, "intentHandlingScope": fn.intent_handling_scope}
            for fn in page["items"]]}


@app.get("/intent-reports")
def list_intent_reports(intent_id: uuid.UUID | None = None, limit: int = PageLimit, offset: int = PageOffset,
                         db: Session = Depends(get_session)):
    """Read side of publish_intent_report — fulfilment/conflict reports
    were write-only."""
    stmt = select(IntentReport)
    if intent_id:
        stmt = stmt.where(IntentReport.intent_id == intent_id)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [{"reportId": str(r.id), "intentId": str(r.intent_id), "fulfilmentReport": r.intent_fulfilment_report,
             "conflictReports": r.intent_conflict_reports, "lastUpdatedTime": r.last_updated_time.isoformat()} for r in page["items"]]}


# ---------------------------------------------------------------- OPEN_ITEMS.md section 6.3: rApp Autonomy Modes

def _notify_autonomy_operator(notification_destination: str | None, dispatch: AutonomyDispatch) -> None:
    """All three modes always notify the operator of the AI/ML inference
    outcome — not mode-gated; only enforcement (AUTONOMOUS/ASSIST apply
    it, SHADOW doesn't) and scoping vary by mode. Same best-effort push
    pattern as every other notification in this build.
    """
    if not notification_destination:
        return
    try:
        httpx.post(notification_destination, json={
            "dispatchId": str(dispatch.dispatch_id), "instanceId": str(dispatch.instance_id),
            "modelId": str(dispatch.model_id) if dispatch.model_id else None,
            "autonomyMode": dispatch.autonomy_mode, "status": dispatch.status,
            "expectations": dispatch.expectations, "priority": dispatch.priority,
            "intentId": str(dispatch.intent_id) if dispatch.intent_id else None,
        }, timeout=5.0)
    except httpx.HTTPError:
        pass


@app.post("/autonomy-dispatches", status_code=201)
def request_autonomy_dispatch(body: CreateAutonomyDispatchRequest, db: Session = Depends(get_session)):
    """RequestAutonomyDispatch — the real, spec-shaped hand-off call flow
    02/03 lacked: an rApp that just pulled an inference result via DME
    calls this instead of a raw CM write (call flow 03 Path A/B) or an
    unaddressed CreateIntent, and its own onboarding-time `autonomyMode`
    (rApp Mgmt's own RAppInstance) decides what happens next —
    AUTONOMOUS enacts the outcome immediately, as part of a real Intent,
    scoped to the instance's own pre-configured regionScope; ASSIST holds
    it for an operator to scope before anything is dispatched; SHADOW
    computes nothing beyond this record — observe-only, never dispatched.
    All three always notify the operator.
    """
    inst_resp = _r1.get(f"/rapp-mgmt/instances/{body.instanceId}")
    if inst_resp.status_code != 200:
        raise framework_error(FrameworkError.RAPP_INSTANCE_NOT_FOUND, detail="no such RAppInstance")
    instance = inst_resp.json()
    autonomy_mode = instance["autonomyMode"]

    # Validated the same way for all three modes, up front — even SHADOW
    # "computes" the Intent it would have produced (design: "the Intent
    # (or its equivalent) is computed and surfaced"), so a SHADOW dispatch
    # addressed to a non-existent or incapable RMIH is rejected here too,
    # not silently accepted because nothing real ends up dispatched.
    fn = db.get(IntentHandlingFunction, body.rmihId)
    if fn is None:
        raise framework_error(FrameworkError.INTENT_HANDLING_FUNCTION_NOT_FOUND, detail="no such intent handling function")
    _validate_rmih_can_handle(fn, _requested_expectation_object_types(body.expectations), body.intentHandlingScope)

    dispatch = AutonomyDispatch(instance_id=body.instanceId, model_id=body.modelId, autonomy_mode=autonomy_mode,
                                 expectations=body.expectations, priority=body.priority, rmih_id=body.rmihId,
                                 intent_mgmt_purpose=body.intentMgmtPurpose, intent_handling_scope=body.intentHandlingScope,
                                 notification_destination=body.notificationDestination, status="SHADOWED")
    if autonomy_mode == "AUTONOMOUS":
        intent = _create_intent_row(db, rmih_id=body.rmihId, expectations=body.expectations, priority=body.priority,
                                     rmio_id=str(body.instanceId), intent_mgmt_purpose=body.intentMgmtPurpose,
                                     intent_handling_scope=body.intentHandlingScope)
        dispatch.status = "DISPATCHED"
        dispatch.intent_id = intent.intent_id
        dispatch.region_scope = instance.get("regionScope")
    elif autonomy_mode == "ASSIST":
        dispatch.status = "AWAITING_SCOPE"
    # SHADOW: dispatch.status stays "SHADOWED" — no Intent, ever, for this record.

    db.add(dispatch)
    db.commit()
    _notify_autonomy_operator(body.notificationDestination, dispatch)
    return _autonomy_dispatch_view(dispatch)


@app.post("/autonomy-dispatches/{dispatch_id}/resolve")
def resolve_autonomy_dispatch(dispatch_id: uuid.UUID, body: ResolveAutonomyDispatchRequest, db: Session = Depends(get_session)):
    """ASSIST's own human-in-the-loop scoping step: the operator supplies
    (or narrows) which RAN nodes/cells/slices the dispatch applies to,
    only now creating the real Intent — distinct from AUTONOMOUS, whose
    scope was already fixed at onboarding.
    """
    dispatch = db.get(AutonomyDispatch, dispatch_id)
    if dispatch is None:
        raise framework_error(FrameworkError.AUTONOMY_DISPATCH_NOT_FOUND, detail="no such autonomy dispatch")
    if dispatch.status != "AWAITING_SCOPE":
        raise framework_error(FrameworkError.AUTONOMY_DISPATCH_NOT_AWAITING_SCOPE,
                               detail=f"cannot resolve a dispatch in status {dispatch.status}")

    intent = _create_intent_row(db, rmih_id=dispatch.rmih_id, expectations=dispatch.expectations, priority=dispatch.priority,
                                 rmio_id=str(dispatch.instance_id), intent_mgmt_purpose=dispatch.intent_mgmt_purpose,
                                 intent_handling_scope=dispatch.intent_handling_scope)
    dispatch.status = "DISPATCHED"
    dispatch.intent_id = intent.intent_id
    dispatch.region_scope = body.regionScope
    db.commit()
    _notify_autonomy_operator(dispatch.notification_destination, dispatch)
    return _autonomy_dispatch_view(dispatch)


@app.get("/autonomy-dispatches/{dispatch_id}")
def query_autonomy_dispatch(dispatch_id: uuid.UUID, db: Session = Depends(get_session)):
    dispatch = db.get(AutonomyDispatch, dispatch_id)
    if dispatch is None:
        raise framework_error(FrameworkError.AUTONOMY_DISPATCH_NOT_FOUND, detail="no such autonomy dispatch")
    return _autonomy_dispatch_view(dispatch)


@app.get("/autonomy-dispatches")
def list_autonomy_dispatches(instance_id: uuid.UUID | None = None, status: str | None = None, limit: int = PageLimit,
                              offset: int = PageOffset, db: Session = Depends(get_session)):
    stmt = select(AutonomyDispatch)
    if instance_id:
        stmt = stmt.where(AutonomyDispatch.instance_id == instance_id)
    if status:
        stmt = stmt.where(AutonomyDispatch.status == status)
    page = paginate(db, stmt.order_by(AutonomyDispatch.created_at.desc()), limit, offset)
    return {**page, "items": [_autonomy_dispatch_view(d) for d in page["items"]]}


def _autonomy_dispatch_view(d: AutonomyDispatch) -> dict:
    return {
        "dispatchId": str(d.dispatch_id), "instanceId": str(d.instance_id),
        "modelId": str(d.model_id) if d.model_id else None, "autonomyMode": d.autonomy_mode,
        "expectations": d.expectations, "priority": d.priority, "rmihId": d.rmih_id,
        "intentMgmtPurpose": d.intent_mgmt_purpose, "intentHandlingScope": d.intent_handling_scope,
        "regionScope": d.region_scope, "status": d.status,
        "intentId": str(d.intent_id) if d.intent_id else None,
        "createdAt": d.created_at.isoformat(),
    }
