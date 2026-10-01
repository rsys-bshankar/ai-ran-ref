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

import datetime
import uuid

import httpx
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.identity import is_framework_internal_identity
from smo_shared.pagination import PageLimit, PageOffset, paginate
from smo_shared.openapi_security import apply_r1_gateway_security
from smo_shared.correlation import apply_correlation_id
from smo_shared.r1_client import R1Client
from smo_shared.webhook import post_webhook

from . import ts28312
from .models import AutonomyDispatch, Intent, IntentHandlingFunction, IntentReport, IntentUtilityFormula

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
    """Wave 6 — TS 28.312 Intent, validated strictly (agreed: every caller
    migrated): the spec's own attribute names, required fields and enums,
    and every expectation checked against its expectation family
    (`app/ts28312.py`). `rmihId`/`rmioId`/`intentHandlingScope` are this
    build's own: the consumer-side choice of handling function (TS 28.312
    containment: IntentHandlingFunction *contains* Intent), the creating
    consumer's identity, and the handling-scope check against that
    function's declared coverage (SPEC_AUDIT.md items 1-3).
    """
    model_config = ConfigDict(extra="forbid")

    userLabel: str
    intentExpectations: list[ts28312.IntentExpectation] = Field(min_length=1)
    intentMgmtPurpose: ts28312.IntentMgmtPurpose = "FULFILMENT_WITHOUT_NEGOTIATION"
    contextSelectivity: ts28312.Selectivity | None = None
    consumerSatisfactionIndexThreshold: int | None = None
    expectationSelectivity: ts28312.Selectivity | None = None
    intentContexts: list[ts28312.Context] | None = None
    intentAdminState: ts28312.IntentAdminState = "ACTIVATED"
    intentPriority: int = Field(default=1, ge=1, le=100)
    intentPreemptionCapability: bool = False
    intentReportControl: list[ts28312.IntentReportControl] = Field(min_length=1)
    implicitIntentIndex: bool = False
    guaranteePeriods: list[ts28312.Context] | None = None
    intentHandlingInfo: ts28312.IntentHandlingInfo | None = None
    intentInterpretationAssistanceInfo: ts28312.IntentInterpretationAssistanceInfo | None = None
    intentUtilityFormulaRef: uuid.UUID | None = None
    rmihId: str
    rmioId: str = ""
    intentHandlingScope: ts28312.IntentHandlingScope | None = None


class AdminStateRequest(BaseModel):
    newState: ts28312.IntentAdminState
    requesterId: str


class IntentReportRequest(BaseModel):
    """Wave 6 — TS 28.312 IntentReport, published by the handling function
    (or SO/SA-SMOS on its behalf). Every report kind of the spec."""
    model_config = ConfigDict(extra="forbid")

    intentReference: uuid.UUID
    intentFulfilmentReport: ts28312.IntentFulfilmentReport | None = None
    intentConflictReports: list[ts28312.IntentConflictReport] | None = None
    intentFeasibilityCheckReport: ts28312.IntentFeasibilityCheckReport | None = None
    intentExplorationReport: ts28312.IntentExplorationReport | None = None
    intentUtilityReports: list[ts28312.IntentUtilityReport] | None = None
    intentFulfilmentNegotiationReport: ts28312.IntentFulfilmentNegotiationReport | None = None
    intentDecompositionReport: ts28312.IntentDecompositionReport | None = None


class RegisterRmihRequest(BaseModel):
    """Wave 6 — TS 28.312 IntentHandlingFunction with its spec attributes
    (intentHandlingCapabilityList, supportedNegotiationFunctionalities,
    supportedUtilityList). `smeServiceId`/`notificationDestination` are this
    build's own: the SME-published API it serves and where new Intents are
    pushed (same callback convention as DME/A1-Related)."""
    model_config = ConfigDict(extra="forbid")

    rmihId: str
    smeServiceId: str
    intentHandlingCapabilityList: list[ts28312.IntentHandlingCapability] = Field(min_length=1)
    supportedNegotiationFunctionalities: list[ts28312.NegotiationFunctionality] | None = Field(default=None, min_length=1)
    supportedUtilityList: list[ts28312.UtilityDefinition] | None = None
    notificationDestination: str
    intentHandlingScope: list[ts28312.IntentHandlingScope] | None = None


class IntentUtilityFormulaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    utilityFunctionId: str
    utilityParameterList: list[ts28312.UtilityParameter]
    utilityScale: float = 1
    utilityOffset: float = 0


class NegotiationFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    referredIntentOutcomeId: int
    consumerSatisfactionIndex: int | None = None


class CreateAutonomyDispatchRequest(BaseModel):
    instanceId: uuid.UUID
    modelId: uuid.UUID | None = None
    # Wave 6: the Intent an AUTONOMOUS/ASSIST dispatch creates is a strict
    # TS 28.312 Intent, so its expectations are validated as such up front.
    expectations: list[ts28312.IntentExpectation] = Field(min_length=1)
    priority: int = Field(default=1, ge=1, le=100)
    rmihId: str
    intentMgmtPurpose: ts28312.IntentMgmtPurpose = "FULFILMENT_WITHOUT_NEGOTIATION"
    intentHandlingScope: ts28312.IntentHandlingScope | None = None
    # All three modes always notify the operator — optional only in the
    # same sense every other subscription-shaped resource's own
    # notification_destination already is (a purely poll-based consumer
    # may still omit it).
    notificationDestination: str | None = None
    userLabel: str | None = None


class ResolveAutonomyDispatchRequest(BaseModel):
    regionScope: dict


@app.post("/intents", status_code=201)
def create_intent(body: CreateIntentRequest, db: Session = Depends(get_session)):
    """CreateIntent — the caller addresses a specific, already-registered
    RMIH by `rmihId` (consumer-side selection, TS 28.312 containment).
    404 if no such function; 422 RMIH_CAPABILITY_MISMATCH if the function
    cannot handle the intent's object types, scope or management purpose,
    or — for a fulfilment purpose — any of its targets (see
    `_create_intent_row`). Dispatch to the RMIH is best-effort.
    """
    intent = _create_intent_row(db, body)
    return {"intentId": str(intent.intent_id), "intentReportReference": _s(intent.intent_report_reference)}


def _s(value) -> str | None:
    return str(value) if value is not None else None


def _supported_targets(fn: IntentHandlingFunction, object_type: str | None) -> list[dict] | None:
    """The RMIH's supportedExpectationTargetInfoList for one object type
    (None: the function declares no capability for it)."""
    for cap in fn.intent_handling_capability_list or []:
        if cap.get("supportedExpectationObjectType") == object_type:
            return cap.get("supportedExpectationTargetInfoList") or []
    return None


def _feasibility(fn: IntentHandlingFunction, expectations: list[dict]) -> dict:
    """TS 28.312 IntentFeasibilityCheckReport, computed against the
    handling function's own declared IntentHandlingCapability: a target is
    feasible when the capability for its expectation's object type names
    it (and, where declared, with the same condition)."""
    infeasible = []
    for exp in expectations:
        supported = _supported_targets(fn, exp["expectationObject"].get("objectType"))
        if supported is None:
            continue  # object type not covered at all — rejected by the capability check
        by_name = {t["supportedTargetName"]: t for t in supported}
        bad = []
        for target in exp["expectationTargets"]:
            known = by_name.get(target["targetName"])
            if known is None or (known.get("supportedTargetCondition")
                                 and known["supportedTargetCondition"] != target["targetCondition"]):
                bad.append({"targetName": target["targetName"]})
        if bad:
            infeasible.append({"expectationId": exp["expectationId"], "inFeasibleTargets": bad})
    report = {"feasibilityCheckResult": "INFEASIBLE" if infeasible else "FEASIBLE",
              "infeasibilityReasons": ["INVALID_INTENT_EXPRESSION"] if infeasible else []}
    if infeasible:
        report["inFeasibleExpectationInfos"] = infeasible
    return report


def _conflicts(db: Session, expectations: list[dict]) -> list[dict]:
    """TS 28.312 IntentConflictReport (TARGET_CONFLICT): another ACTIVATED
    intent already sets the same target on the same object instance with
    a different condition or value."""
    wanted = {}
    for exp in expectations:
        instance = exp["expectationObject"].get("objectInstance")
        if not instance:
            continue
        for target in exp["expectationTargets"]:
            wanted[(instance, target["targetName"])] = (exp["expectationId"], target)
    if not wanted:
        return []
    conflicts = []
    for other in db.scalars(select(Intent).where(Intent.intent_admin_state == "ACTIVATED")).all():
        for exp in other.intent_expectations or []:
            instance = (exp.get("expectationObject") or {}).get("objectInstance")
            for target in exp.get("expectationTargets") or []:
                mine = wanted.get((instance, target.get("targetName")))
                if mine and (mine[1]["targetCondition"], mine[1]["targetValueRange"]) != (
                        target.get("targetCondition"), target.get("targetValueRange")):
                    conflicts.append({"conflictId": str(uuid.uuid4()), "conflictType": "TARGET_CONFLICT",
                                      "conflictingIntent": str(other.intent_id),
                                      "conflictingExpectation": exp.get("expectationId"),
                                      "conflictingTarget": target.get("targetName"), "recommendedSolutions": "MODIFY"})
    return conflicts


def _received_fulfilment(expectations: list[dict], state: str = "RECEIVED") -> dict:
    """The initial (or suspended) IntentFulfilmentReport: nothing is
    fulfilled yet, per expectation and per target."""
    info = {"fulfilmentStatus": "NOT_FULFILLED", "notFullfilledState": state}
    return {"intentFulfilmentInfo": info, "expectationFulfilmentResult": [
        {"expectaitonId": e["expectationId"], "expectationFulfilmentInfo": info,
         "targetFulfilmentResults": [{"targetName": t["targetName"], "targetFulfilmentInfo": info}
                                     for t in e["expectationTargets"]]}
        for e in expectations]}


def _create_intent_row(db: Session, body: CreateIntentRequest) -> Intent:
    """The real work CreateIntent does — shared with OPEN_ITEMS.md §6.3's
    AUTONOMOUS/resolve-ASSIST paths, so an autonomy-driven Intent is a real
    Intent in every respect.

    Wave 6 (TS 28.312): every expectation object type must be one the
    function declares a capability for; a purpose needing negotiation
    (feasibility check / exploration / negotiated fulfilment) needs the
    function to declare that functionality (when it declares any). The
    feasibility of every target is checked against the capability's
    supportedExpectationTargetInfoList: a FEASIBILITYCHECK* intent is
    accepted and carries the INFEASIBLE report; a fulfilment intent with an
    infeasible target is rejected (it could never be fulfilled). Target
    conflicts with other ACTIVATED intents are reported, not rejected. The
    initial IntentReport (fulfilment RECEIVED, plus conflict/feasibility
    reports) becomes the intent's intentReportReference and is delivered
    per intentReportControl.
    """
    fn = db.get(IntentHandlingFunction, body.rmihId)
    if fn is None:
        raise framework_error(FrameworkError.INTENT_HANDLING_FUNCTION_NOT_FOUND, detail="no such intent handling function")
    expectations = ts28312.dump(body.intentExpectations)
    _validate_rmih_can_handle(fn, _requested_expectation_object_types(expectations), body.intentHandlingScope)
    needed = ts28312.PURPOSE_NEEDS.get(body.intentMgmtPurpose)
    if needed and fn.supported_negotiation_functionalities and needed not in fn.supported_negotiation_functionalities:
        raise framework_error(FrameworkError.RMIH_CAPABILITY_MISMATCH,
                               detail=f"{fn.rmih_id} does not support {needed} (intentMgmtPurpose {body.intentMgmtPurpose})")
    if body.intentUtilityFormulaRef is not None and db.get(IntentUtilityFormula, body.intentUtilityFormulaRef) is None:
        raise framework_error(FrameworkError.NRM_OBJECT_NOT_FOUND, detail="no such IntentUtilityFormula")

    feasibility = _feasibility(fn, expectations)
    is_check = body.intentMgmtPurpose.startswith("FEASIBILITYCHECK")
    if feasibility["feasibilityCheckResult"] == "INFEASIBLE" and not is_check:
        raise framework_error(FrameworkError.RMIH_CAPABILITY_MISMATCH,
                               detail=f"{fn.rmih_id} cannot fulfil: {feasibility['inFeasibleExpectationInfos']}")
    conflicts = _conflicts(db, expectations)

    intent = Intent(user_label=body.userLabel, intent_expectations=expectations, intent_priority=body.intentPriority,
                    rmio_id=body.rmioId, intent_mgmt_purpose=body.intentMgmtPurpose, rmih_id=body.rmihId,
                    intent_admin_state=body.intentAdminState, intent_preemption_capability=body.intentPreemptionCapability,
                    context_selectivity=body.contextSelectivity, expectation_selectivity=body.expectationSelectivity,
                    consumer_satisfaction_index_threshold=body.consumerSatisfactionIndexThreshold,
                    intent_contexts=ts28312.dump(body.intentContexts),
                    intent_report_control=ts28312.dump(body.intentReportControl),
                    implicit_intent_index=body.implicitIntentIndex, guarantee_periods=ts28312.dump(body.guaranteePeriods),
                    intent_handling_info=ts28312.dump(body.intentHandlingInfo),
                    intent_interpretation_assistance_info=ts28312.dump(body.intentInterpretationAssistanceInfo),
                    intent_utility_formula_id=body.intentUtilityFormulaRef)
    db.add(intent)
    db.flush()
    report = IntentReport(intent_id=intent.intent_id, intent_fulfilment_report=_received_fulfilment(expectations),
                          intent_conflict_reports=conflicts or None,
                          intent_feasibility_check_report=feasibility if is_check else None)
    db.add(report)
    db.flush()
    intent.intent_report_reference = report.id
    db.commit()

    post_webhook(fn.notification_destination, json={
        "intentId": str(intent.intent_id), "expectationObjectTypes": sorted(_requested_expectation_object_types(expectations)),
        "intentPriority": intent.intent_priority, "rmioId": intent.rmio_id, "intentMgmtPurpose": intent.intent_mgmt_purpose,
    }, timeout=5.0)
    _deliver_report(intent, report)
    return intent


def _requested_expectation_object_types(expectations: list[dict]) -> set[str]:
    """TS28312_IntentNrm.yaml's `IntentExpectation.expectationObject.objectType`
    — what kind of thing each expectation targets."""
    types: set[str] = set()
    for expectation in expectations:
        object_type = (expectation.get("expectationObject") or {}).get("objectType")
        if object_type:
            types.add(object_type)
    return types


def _validate_rmih_can_handle(fn: IntentHandlingFunction, expectation_object_types: set[str], scope: str | None) -> None:
    """Validates that the one addressed function covers what the Intent
    asks for: its declared intentHandlingScope (SPEC_AUDIT.md item 1) and —
    Wave 6, strict — a capability for *every* expectation object type the
    intent names (previously any one sufficed)."""
    if scope is not None and fn.intent_handling_scope and scope not in fn.intent_handling_scope:
        raise framework_error(FrameworkError.RMIH_CAPABILITY_MISMATCH,
                               detail=f"{fn.rmih_id} does not cover handling scope {scope!r}")
    supported = {cap.get("supportedExpectationObjectType") for cap in fn.intent_handling_capability_list or []}
    missing = expectation_object_types - supported
    if missing:
        raise framework_error(FrameworkError.RMIH_CAPABILITY_MISMATCH,
                               detail=f"{fn.rmih_id} does not support {sorted(missing)}")


_REPORT_COLUMNS = {
    "intentFulfilmentReport": "intent_fulfilment_report", "intentConflictReports": "intent_conflict_reports",
    "intentFeasibilityCheckReport": "intent_feasibility_check_report", "intentExplorationReport": "intent_exploration_report",
    "intentUtilityReports": "intent_utility_reports",
    "intentFulfilmentNegotiationReport": "intent_fulfilment_negotiation_report",
    "intentDecompositionReport": "intent_decomposition_report",
}


def _report_view(r: IntentReport) -> dict:
    attrs = {name: getattr(r, column) for name, column in _REPORT_COLUMNS.items()}
    attrs = {k: v for k, v in attrs.items() if v is not None}
    return {"id": str(r.id), "reportId": str(r.id), "intentId": str(r.intent_id),
            "attributes": {**attrs, "lastUpdatedTime": r.last_updated_time.isoformat(), "intentReference": str(r.intent_id)}}


def _deliver_report(intent: Intent, report: IntentReport) -> None:
    """IntentReportControl: each control with a reportRecipientAddress gets
    the report when it carries one of its expectedReportTypes (all types
    when none are listed). Best-effort, like every notification here."""
    view = _report_view(report)
    present = {ts28312.REPORT_TYPE_OF[k] for k in view["attributes"] if k in ts28312.REPORT_TYPE_OF}
    for control in intent.intent_report_control or []:
        wanted = set(control.get("expectedReportTypes") or []) or present
        if control.get("reportRecipientAddress") and present & wanted:
            post_webhook(control["reportRecipientAddress"], json={"notificationType": "notifyIntentReport", **view}, timeout=2.0)


@app.get("/intents/{intent_id}")
def query_intent(intent_id: uuid.UUID, db: Session = Depends(get_session)):
    """QueryIntent — a real 404 matters since `Intent.rmih_id`'s ON DELETE
    CASCADE means an Intent can vanish when its RMIH is deregistered."""
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
    """UpdateIntentAdminState — RMIO-only. Wave 6: a DEACTIVATED intent's
    report moves to NOT_FULFILLED / SUSPENDED (TS 28.312 NotFulfilledState)
    and is delivered per intentReportControl; reactivating it reports
    RECEIVED again until the handler reports otherwise."""
    intent = db.get(Intent, intent_id)
    if intent is None:
        raise framework_error(FrameworkError.INTENT_NOT_FOUND, detail="no such intent")
    if intent.rmio_id != body.requesterId:
        raise framework_error(FrameworkError.SERVICE_NAME_CONFLICT, detail="only the intent's creator (RMIO) may change its admin state")
    if intent.intent_admin_state != body.newState:
        intent.intent_admin_state = body.newState
        state = "SUSPENDED" if body.newState == "DEACTIVATED" else "RECEIVED"
        report = IntentReport(intent_id=intent.intent_id,
                              intent_fulfilment_report=_received_fulfilment(intent.intent_expectations, state))
        db.add(report)
        db.flush()
        intent.intent_report_reference = report.id
        db.commit()
        _deliver_report(intent, report)
    return _intent_view(intent)


@app.delete("/intents/{intent_id}", status_code=204)
def delete_intent(intent_id: uuid.UUID, db: Session = Depends(get_session)):
    """SPEC_AUDIT.md item 5: an RMIO retracts an Intent it created.
    Idempotent."""
    intent = db.get(Intent, intent_id)
    if intent is not None:
        db.delete(intent)
        db.commit()


@app.post("/intents/{intent_id}/negotiation-feedback")
def submit_negotiation_feedback(intent_id: uuid.UUID, body: NegotiationFeedbackRequest, db: Session = Depends(get_session)):
    """TS 28.312 IntentFulfilmentNegotiationFeedback: the consumer picks one
    of the possibleIntentOutcomeList entries of the intent's latest
    negotiation report."""
    intent = db.get(Intent, intent_id)
    if intent is None:
        raise framework_error(FrameworkError.INTENT_NOT_FOUND, detail="no such intent")
    report = db.scalars(select(IntentReport).where(IntentReport.intent_id == intent_id,
                                                   IntentReport.intent_fulfilment_negotiation_report.is_not(None))
                        .order_by(IntentReport.last_updated_time.desc())).first()
    if report is None:
        raise framework_error(FrameworkError.NRM_OBJECT_NOT_FOUND, detail="intent has no negotiation report to answer")
    negotiation = dict(report.intent_fulfilment_negotiation_report)
    outcome_ids = {o["possibleIntentOutcomeId"] for o in negotiation.get("possibleIntentOutcomeList") or []}
    if body.referredIntentOutcomeId not in outcome_ids:
        raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED,
                               detail=f"no possibleIntentOutcomeId {body.referredIntentOutcomeId}")
    negotiation["intentFulfilmentNegotiationConsumerFeedback"] = body.model_dump(exclude_none=True)
    report.intent_fulfilment_negotiation_report = negotiation
    report.last_updated_time = datetime.datetime.now(datetime.UTC)
    db.commit()
    return _report_view(report)


@app.post("/intent-reports", status_code=201)
def publish_intent_report(body: IntentReportRequest, db: Session = Depends(get_session)):
    """PublishIntentReport — any of the spec's report kinds, at least one.
    The new report becomes the intent's intentReportReference and is
    delivered per its intentReportControl."""
    intent = db.get(Intent, body.intentReference)
    if intent is None:
        raise framework_error(FrameworkError.INTENT_NOT_FOUND, detail="no such intent")
    values = {column: ts28312.dump(getattr(body, name)) for name, column in _REPORT_COLUMNS.items()}
    if all(v is None for v in values.values()):
        raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED, detail="an IntentReport needs at least one report")
    report = IntentReport(intent_id=intent.intent_id, **values)
    db.add(report)
    db.flush()
    intent.intent_report_reference = report.id
    db.commit()
    _deliver_report(intent, report)
    return {"reportId": str(report.id), **_report_view(report)}


@app.post("/intent-handling-functions", status_code=201)
def register_intent_handling_function(body: RegisterRmihRequest, db: Session = Depends(get_session)):
    """RegisterIntentHandlingFunction — rejected if caller is external
    (D-SEC-POLICY-1, unchanged). identity.py's helper distinguishes an
    rApp caller (always rejected here) from a framework-internal SMO
    module (SO SMOS / SA SMOS, the only legitimate callers).
    """
    if not is_framework_internal_identity(body.rmihId):
        raise framework_error(FrameworkError.SERVICE_NAME_CONFLICT, detail="external callers may never hold an rmihId (D-SEC-POLICY-1)")
    fn = IntentHandlingFunction(rmih_id=body.rmihId, sme_service_id=body.smeServiceId,
                                intent_handling_capability_list=ts28312.dump(body.intentHandlingCapabilityList),
                                supported_negotiation_functionalities=body.supportedNegotiationFunctionalities,
                                supported_utility_list=ts28312.dump(body.supportedUtilityList),
                                notification_destination=body.notificationDestination, intent_handling_scope=body.intentHandlingScope)
    db.add(fn)
    db.commit()
    return _rmih_view(fn)


@app.delete("/intent-handling-functions/{rmih_id}", status_code=204)
def deregister_intent_handling_function(rmih_id: str, db: Session = Depends(get_session)):
    """NEW — symmetric with Register, closing v1.3's gap (Policy Mgmt LLD section 1)."""
    fn = db.get(IntentHandlingFunction, rmih_id)
    if fn is not None:
        db.delete(fn)
        db.commit()


def _intent_view(i: Intent) -> dict:
    """This build's own summary keys (kept for existing readers) plus the
    full TS 28.312 Intent under `attributes`."""
    return {"id": str(i.intent_id), "intentId": str(i.intent_id), "intentAdminState": i.intent_admin_state,
            "intentPriority": i.intent_priority, "rmioId": i.rmio_id, "intentMgmtPurpose": i.intent_mgmt_purpose,
            "rmihId": i.rmih_id, "userLabel": i.user_label,
            "attributes": {k: v for k, v in {
                "userLabel": i.user_label, "intentExpectations": i.intent_expectations,
                "intentMgmtPurpose": i.intent_mgmt_purpose, "contextSelectivity": i.context_selectivity,
                "consumerSatisfactionIndexThreshold": i.consumer_satisfaction_index_threshold,
                "expectationSelectivity": i.expectation_selectivity, "intentContexts": i.intent_contexts,
                "intentAdminState": i.intent_admin_state, "intentPriority": i.intent_priority,
                "intentPreemptionCapability": i.intent_preemption_capability,
                "intentReportControl": i.intent_report_control, "implicitIntentIndex": i.implicit_intent_index,
                "guaranteePeriods": i.guarantee_periods, "intentHandlingInfo": i.intent_handling_info,
                "intentInterpretationAssistanceInfo": i.intent_interpretation_assistance_info,
                "intentReportReference": _s(i.intent_report_reference),
                "intentUtilityFormulaRef": _s(i.intent_utility_formula_id)}.items() if v is not None}}


def _rmih_view(fn: IntentHandlingFunction) -> dict:
    return {"id": fn.rmih_id, "rmihId": fn.rmih_id, "smeServiceId": fn.sme_service_id,
            "notificationDestination": fn.notification_destination, "intentHandlingScope": fn.intent_handling_scope,
            "attributes": {"intentHandlingScope": fn.intent_handling_scope,
                           "intentHandlingCapabilityList": fn.intent_handling_capability_list,
                           "supportedNegotiationFunctionalities": fn.supported_negotiation_functionalities,
                           "supportedUtilityList": fn.supported_utility_list}}


@app.get("/intent-handling-functions")
def list_intent_handling_functions(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    """List read over registered RMIHs (GUI pass)."""
    page = paginate(db, select(IntentHandlingFunction), limit, offset)
    return {**page, "items": [_rmih_view(fn) for fn in page["items"]]}


@app.get("/intent-reports")
def list_intent_reports(intent_id: uuid.UUID | None = None, limit: int = PageLimit, offset: int = PageOffset,
                         db: Session = Depends(get_session)):
    """Read side of publish_intent_report, newest first."""
    stmt = select(IntentReport)
    if intent_id:
        stmt = stmt.where(IntentReport.intent_id == intent_id)
    page = paginate(db, stmt.order_by(IntentReport.last_updated_time.desc()), limit, offset)
    return {**page, "items": [_report_view(r) for r in page["items"]]}


@app.get("/intent-reports/{report_id}")
def get_intent_report(report_id: uuid.UUID, db: Session = Depends(get_session)):
    report = db.get(IntentReport, report_id)
    if report is None:
        raise framework_error(FrameworkError.NRM_OBJECT_NOT_FOUND, detail="no such IntentReport")
    return _report_view(report)


# ---------------------------------------------------------------- Wave 6: IntentUtilityFormula (TS 28.312 IOC)

def _formula_view(f: IntentUtilityFormula) -> dict:
    return {"id": str(f.intent_utility_formula_id), "attributes": {
        "utilityFunctionId": f.utility_function_id, "utilityParameterList": f.utility_parameter_list,
        "utilityScale": f.utility_scale, "utilityOffset": f.utility_offset}}


@app.post("/intent-utility-formulas", status_code=201)
def create_intent_utility_formula(body: IntentUtilityFormulaRequest, db: Session = Depends(get_session)):
    f = IntentUtilityFormula(utility_function_id=body.utilityFunctionId,
                             utility_parameter_list=ts28312.dump(body.utilityParameterList),
                             utility_scale=body.utilityScale, utility_offset=body.utilityOffset)
    db.add(f)
    db.commit()
    return _formula_view(f)


@app.get("/intent-utility-formulas")
def list_intent_utility_formulas(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    page = paginate(db, select(IntentUtilityFormula), limit, offset)
    return {**page, "items": [_formula_view(f) for f in page["items"]]}


@app.get("/intent-utility-formulas/{formula_id}")
def get_intent_utility_formula(formula_id: uuid.UUID, db: Session = Depends(get_session)):
    f = db.get(IntentUtilityFormula, formula_id)
    if f is None:
        raise framework_error(FrameworkError.NRM_OBJECT_NOT_FOUND, detail="no such IntentUtilityFormula")
    return _formula_view(f)


@app.delete("/intent-utility-formulas/{formula_id}", status_code=204)
def delete_intent_utility_formula(formula_id: uuid.UUID, db: Session = Depends(get_session)):
    f = db.get(IntentUtilityFormula, formula_id)
    if f is not None:
        db.delete(f)
        db.commit()


# ---------------------------------------------------------------- OPEN_ITEMS.md section 6.3: rApp Autonomy Modes

def _notify_autonomy_operator(notification_destination: str | None, dispatch: AutonomyDispatch) -> None:
    """All three modes always notify the operator of the AI/ML inference
    outcome — not mode-gated; only enforcement (AUTONOMOUS/ASSIST apply
    it, SHADOW doesn't) and scoping vary by mode. Same best-effort push
    pattern as every other notification in this build, routed through
    smo_shared.webhook's SSRF guard like every other caller-chosen
    callback destination (see that module's docstring for why).
    """
    post_webhook(notification_destination, json={
        "dispatchId": str(dispatch.dispatch_id), "instanceId": str(dispatch.instance_id),
        "modelId": str(dispatch.model_id) if dispatch.model_id else None,
        "autonomyMode": dispatch.autonomy_mode, "status": dispatch.status,
        "expectations": dispatch.expectations, "priority": dispatch.priority,
        "intentId": str(dispatch.intent_id) if dispatch.intent_id else None,
    }, timeout=5.0)


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
    expectations = ts28312.dump(body.expectations)
    _validate_rmih_can_handle(fn, _requested_expectation_object_types(expectations), body.intentHandlingScope)

    dispatch = AutonomyDispatch(instance_id=body.instanceId, model_id=body.modelId, autonomy_mode=autonomy_mode,
                                 expectations=expectations, priority=body.priority, rmih_id=body.rmihId,
                                 intent_mgmt_purpose=body.intentMgmtPurpose, intent_handling_scope=body.intentHandlingScope,
                                 notification_destination=body.notificationDestination, status="SHADOWED")
    db.add(dispatch)
    db.flush()
    if autonomy_mode == "AUTONOMOUS":
        intent = _create_intent_row(db, _dispatch_intent_request(dispatch, body.userLabel))
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

    intent = _create_intent_row(db, _dispatch_intent_request(dispatch, None))
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


def _dispatch_intent_request(dispatch: AutonomyDispatch, user_label: str | None) -> CreateIntentRequest:
    """The strict TS 28.312 Intent an AUTONOMOUS/resolved-ASSIST dispatch
    creates: its expectations as dispatched, reports delivered to the
    dispatch's own operator notification destination (when it has one)."""
    control = {"observationPeriod": 60}
    if dispatch.notification_destination:
        control["reportRecipientAddress"] = dispatch.notification_destination
    return CreateIntentRequest(
        userLabel=user_label or f"autonomy-dispatch {dispatch.dispatch_id}", intentExpectations=dispatch.expectations,
        intentPriority=dispatch.priority, intentMgmtPurpose=dispatch.intent_mgmt_purpose or "FULFILMENT_WITHOUT_NEGOTIATION",
        intentReportControl=[control], rmihId=dispatch.rmih_id, rmioId=str(dispatch.instance_id),
        intentHandlingScope=dispatch.intent_handling_scope)


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
