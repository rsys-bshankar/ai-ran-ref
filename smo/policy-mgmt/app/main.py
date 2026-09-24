"""Policy Management & Info SMOS (Intent common mechanism).

SMO Design v1.3 section 3.12, extended by Policy Mgmt LLD sections 1-3:
UpdateIntentAdminState and QueryIntent close operations v1.3 never had
(intentAdminState existed with nothing to change it), and
DeregisterIntentHandlingFunction restores register/deregister symmetry.
"""

import uuid
from typing import Literal

import httpx
from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.identity import is_framework_internal_identity

from .models import Intent, IntentHandlingFunction, IntentReport

app = FastAPI(title="Policy Management & Info SMOS")


class CreateIntentRequest(BaseModel):
    expectations: list[dict]
    priority: int = 1
    rmioId: str = ""
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
    # own declared coverage), used here purely as an optional match-time
    # pre-filter alongside the expectation-object-type match.
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
    notificationCallbackUri: str
    # SPEC_AUDIT.md item 5: TS28312_IntentNrm.yaml's IntentHandlingScope is
    # a closed 2-value enum (RAN/CN) — was untyped JSON, never set by any
    # caller. None means "no declared scope restriction" (matches anything).
    intentHandlingScope: list[Literal["RAN", "CN"]] | None = None


@app.post("/intents", status_code=201)
def create_intent(body: CreateIntentRequest, db: Session = Depends(get_session)):
    """CreateIntent — closes the matching/dispatch gap between this and
    RegisterIntentHandlingFunction: previously an RMIH was never notified
    of a new Intent it could fulfil at all.

    SPEC_AUDIT.md items 2-3: matching is now grounded in the real spec's
    own matching-relevant field — each expectation's
    `expectationObject.objectType` — read straight out of the
    already-accepted, already-opaque `expectations` list (no deeper
    TS 28.312 expectation grammar needed for this one field), rather than
    an invented top-level `intentType` string with no shared vocabulary
    with the spec, or `intentMgmtPurpose` (a different, workflow-procedure
    field). Dispatch is best-effort: a callback failure never blocks
    CreateIntent itself succeeding.
    """
    intent = Intent(intent_expectations=body.expectations, intent_priority=body.priority, rmio_id=body.rmioId,
                     intent_mgmt_purpose=body.intentMgmtPurpose)
    db.add(intent)
    db.commit()

    expectation_object_types = _requested_expectation_object_types(body.expectations)
    if expectation_object_types:
        for fn in _matching_rmihs(db, expectation_object_types, body.intentHandlingScope):
            try:
                httpx.post(fn.notification_callback_uri, json={
                    "intentId": str(intent.intent_id), "expectationObjectTypes": sorted(expectation_object_types),
                    "priority": intent.intent_priority, "rmioId": intent.rmio_id,
                }, timeout=5.0)
            except httpx.HTTPError:
                pass
    return {"intentId": str(intent.intent_id)}


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


def _matching_rmihs(db: Session, expectation_object_types: set[str], scope: str | None = None) -> list[IntentHandlingFunction]:
    """Matches each registered RMIH's declared
    `supportedExpectationObjectType` capabilities (TS28312_IntentNrm.yaml's
    real `IntentHandlingCapability` field) against the Intent's own
    requested expectation object types, plus SPEC_AUDIT.md item 5's
    intentHandlingScope pre-filter: an RMIH with a declared scope that
    doesn't cover the intent's requested scope is skipped before the
    capability check even runs. An RMIH with no declared scope (None,
    the pre-existing default — matches anything) is unaffected, and a
    request with no requested scope skips the filter entirely, so every
    caller predating that field keeps its exact prior behavior.
    """
    candidates = db.scalars(select(IntentHandlingFunction)).all()
    if scope is not None:
        candidates = [fn for fn in candidates if not fn.intent_handling_scope or scope in fn.intent_handling_scope]
    return [fn for fn in candidates
            if any(cap.get("supportedExpectationObjectType") in expectation_object_types for cap in fn.intent_handling_capability_list)]


@app.get("/intents/{intent_id}")
def query_intent(intent_id: uuid.UUID, db: Session = Depends(get_session)):
    """NEW — v1.3 had CreateIntent and subscribe/publish, but no read
    operation at all (Policy Mgmt LLD section 1).
    """
    intent = db.get(Intent, intent_id)
    return _intent_view(intent)


@app.get("/intents")
def query_intents(admin_state: str | None = None, db: Session = Depends(get_session)):
    stmt = select(Intent)
    if admin_state:
        stmt = stmt.where(Intent.intent_admin_state == admin_state)
    return [_intent_view(i) for i in db.scalars(stmt).all()]


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
                                 notification_callback_uri=body.notificationCallbackUri, intent_handling_scope=body.intentHandlingScope)
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
            "intentPriority": i.intent_priority, "rmioId": i.rmio_id, "intentMgmtPurpose": i.intent_mgmt_purpose}
