"""TS 28.104 MDA NRM at REST level — Wave 5 (HISTORY.md
W5-01..W5-04, decision D-9): MDAFunction, MDARequest and MDAReport with the
spec's own attribute names and enums, as `{"id", "attributes"}` resources.

The original producer-push surface (`POST /reports`, `/subscriptions`) is
unchanged. This adds the spec's consumer-driven side on top of it: an
MDARequest declares the outputs it wants (`requestedMDAOutputs`, with
per-IE filters and thresholds), for which scope and time window, and how
they are delivered (`reportingMethod`). Every report — spec-shaped from
`POST /mda-reports` or legacy from `POST /reports` — is matched against the
open requests and delivered to each one that it satisfies:

  NOTIFICATION  POST of the report to `reportingTarget` (best-effort)
  FILE          the report is a downloadable file (`GET /mda-reports/{id}/file`)
                and a file-ready notification goes to `reportingTarget`
  STREAMING     recorded and retrievable; this build has no TS 28.532
                streaming transport (the same gap OPEN_ITEMS.md §3 records
                for RAN NF OAM), so this is the one reporting-method deviation

A DRIFT report (W5-04) naming a model (`mLModelRef` output IE) is forwarded
to that model's AIMgF MLMF subscriptions as a performance report, so the
existing guard-KPI floor decides whether retraining fires.
"""

import datetime
import json
import uuid

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.pagination import PageLimit, PageOffset, paginate, paginate_list
from smo_shared.outbox import enqueue

from . import ts28104
from .main import _notify_report_subscribers, _r1, _validate_input_sources_are_real_dme_artifacts
from .models import MDAFReport, MDAFunction, MDAReportDelivery, MDARequest

router = APIRouter()


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _s(value) -> str | None:
    return str(value) if value is not None else None


def _iso(value: datetime.datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _aware(value: datetime.datetime | None) -> datetime.datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=datetime.UTC)
    return value


def _get(db: Session, cls, object_id: uuid.UUID, name: str):
    obj = db.get(cls, object_id)
    if obj is None:
        raise framework_error(FrameworkError.NRM_OBJECT_NOT_FOUND, detail=f"no such {name} {object_id}")
    return obj


# ================================================================ MDAFunction

class MDAFunctionBody(_Body):
    userLabel: str | None = None
    supportedMDACapabilities: list[ts28104.MDAType] = []
    supportedMDADomain: ts28104.MDADomain | None = None
    mLModelRefList: list[str] = []
    aIMLInferenceFunctionRefList: list[str] = []


def _function_view(f: MDAFunction) -> dict:
    return {"id": str(f.mda_function_id), "attributes": {
        "userLabel": f.user_label, "supportedMDACapabilities": list(f.supported_mda_capabilities or []),
        "supportedMDADomain": f.supported_mda_domain, "mLModelRefList": list(f.ml_model_refs or []),
        "aIMLInferenceFunctionRefList": list(f.aiml_inference_function_refs or [])}}


def _apply_function(f: MDAFunction, body: MDAFunctionBody) -> None:
    f.user_label = body.userLabel
    f.supported_mda_capabilities = sorted(set(body.supportedMDACapabilities))
    f.supported_mda_domain = body.supportedMDADomain
    f.ml_model_refs = body.mLModelRefList
    f.aiml_inference_function_refs = body.aIMLInferenceFunctionRefList


@router.post("/mda-functions", status_code=201)
def create_mda_function(body: MDAFunctionBody, db: Session = Depends(get_session)):
    f = MDAFunction()
    _apply_function(f, body)
    db.add(f)
    db.commit()
    return _function_view(f)


@router.get("/mda-functions")
def list_mda_functions(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    page = paginate(db, select(MDAFunction), limit, offset)
    return {**page, "items": [_function_view(f) for f in page["items"]]}


@router.get("/mda-functions/{function_id}")
def get_mda_function(function_id: uuid.UUID, db: Session = Depends(get_session)):
    return _function_view(_get(db, MDAFunction, function_id, "MDAFunction"))


@router.put("/mda-functions/{function_id}")
def replace_mda_function(function_id: uuid.UUID, body: MDAFunctionBody, db: Session = Depends(get_session)):
    f = _get(db, MDAFunction, function_id, "MDAFunction")
    _apply_function(f, body)
    db.commit()
    return _function_view(f)


@router.delete("/mda-functions/{function_id}", status_code=204)
def delete_mda_function(function_id: uuid.UUID, db: Session = Depends(get_session)):
    f = db.get(MDAFunction, function_id)
    if f is not None:
        db.delete(f)
        db.commit()


# ================================================================ MDARequest

class MDARequestBody(_Body):
    mDAFunctionRef: uuid.UUID | None = None
    requestedMDAOutputs: list[ts28104.MDAOutputPerMDAType]
    reportingMethod: ts28104.ReportingMethod
    reportingTarget: str | None = None
    analyticsScope: ts28104.AnalyticsScopeType | None = None
    startTime: datetime.datetime | None = None
    stopTime: datetime.datetime | None = None
    recommendationFilter: ts28104.AnalyticsScopeType | None = None
    performanceThresholdInfo: list[ts28104.ThresholdInfo] | None = None
    analysisRequirements: ts28104.AnalysisRequirement | None = None
    thresholdMonitorRefList: list[str] | None = None
    # Not a spec attribute — who asked, as on /subscriptions.
    requestedBy: str | None = None


def _request_view(r: MDARequest) -> dict:
    return {"id": str(r.mda_request_id), "attributes": {
        "mDAFunctionRef": _s(r.mda_function_id), "requestedMDAOutputs": r.requested_mda_outputs,
        "reportingMethod": r.reporting_method, "reportingTarget": r.reporting_target,
        "analyticsScope": r.analytics_scope, "startTime": _iso(r.start_time), "stopTime": _iso(r.stop_time),
        "recommendationFilter": r.recommendation_filter, "performanceThresholdInfo": r.performance_threshold_info,
        "analysisRequirements": r.analysis_requirements, "thresholdMonitorRefList": r.threshold_monitor_refs,
        "requestedBy": r.requested_by, "active": _is_active(r, datetime.datetime.now(datetime.UTC))}}


def _is_active(r: MDARequest, now: datetime.datetime) -> bool:
    start, stop = _aware(r.start_time), _aware(r.stop_time)
    return (start is None or start <= now) and (stop is None or now < stop)


@router.post("/mda-requests", status_code=201)
def create_mda_request(body: MDARequestBody, db: Session = Depends(get_session)):
    """A request names the MDA outputs it wants. When it is addressed to an
    MDAFunction, every requested mDAType must be one of that function's
    supportedMDACapabilities. NOTIFICATION and FILE delivery need a
    reportingTarget."""
    if not body.requestedMDAOutputs:
        raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED, detail="requestedMDAOutputs must not be empty")
    if body.reportingMethod in ("NOTIFICATION", "FILE") and not body.reportingTarget:
        raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED,
                               detail=f"reportingMethod {body.reportingMethod} needs a reportingTarget")
    start, stop = _aware(body.startTime), _aware(body.stopTime)
    if start and stop and stop <= start:
        raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED, detail="stopTime must be after startTime")
    if body.mDAFunctionRef is not None:
        function = _get(db, MDAFunction, body.mDAFunctionRef, "MDAFunction")
        unsupported = sorted({o.mDAType for o in body.requestedMDAOutputs} - set(function.supported_mda_capabilities or []))
        if unsupported:
            raise framework_error(FrameworkError.MDA_CAPABILITY_NOT_SUPPORTED,
                                   detail=f"MDAFunction {function.mda_function_id} does not support {unsupported}")
    r = MDARequest(mda_function_id=body.mDAFunctionRef, requested_by=body.requestedBy,
                   requested_mda_outputs=ts28104.dump(body.requestedMDAOutputs), reporting_method=body.reportingMethod,
                   reporting_target=body.reportingTarget, analytics_scope=ts28104.dump(body.analyticsScope),
                   start_time=_aware(body.startTime), stop_time=_aware(body.stopTime),
                   recommendation_filter=ts28104.dump(body.recommendationFilter),
                   performance_threshold_info=ts28104.dump(body.performanceThresholdInfo),
                   analysis_requirements=ts28104.dump(body.analysisRequirements),
                   threshold_monitor_refs=body.thresholdMonitorRefList)
    db.add(r)
    db.commit()
    return _request_view(r)


@router.get("/mda-requests")
def list_mda_requests(requested_by: str | None = None, limit: int = PageLimit, offset: int = PageOffset,
                      db: Session = Depends(get_session)):
    stmt = select(MDARequest)
    if requested_by:
        stmt = stmt.where(MDARequest.requested_by == requested_by)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [_request_view(r) for r in page["items"]]}


@router.get("/mda-requests/{request_id}")
def get_mda_request(request_id: uuid.UUID, db: Session = Depends(get_session)):
    return _request_view(_get(db, MDARequest, request_id, "MDARequest"))


@router.delete("/mda-requests/{request_id}", status_code=204)
def delete_mda_request(request_id: uuid.UUID, db: Session = Depends(get_session)):
    """Withdraws the request: no further reports are matched to it."""
    r = db.get(MDARequest, request_id)
    if r is not None:
        db.delete(r)
        db.commit()


# ================================================================ MDAReport

class MDAReportBody(_Body):
    mDAOutputs: list[ts28104.MDAOutputs]
    # Set when a producer answers one specific request; omitted, the report
    # is matched against every open request.
    mDARequestRef: uuid.UUID | None = None
    mDAFunctionRef: uuid.UUID | None = None
    # Not spec attributes: the managed entities the analysis covers (for
    # scope matching), the DME data jobs it was computed from (the Wave 3
    # "MDAF sources from DME only" rule) and this build's report kind.
    managedEntitiesScope: list[str] | None = None
    inputSources: list[uuid.UUID] = []
    reportKind: ts28104.ReportKind | None = None


def _outputs_of(report: MDAFReport) -> list[dict]:
    """A legacy `POST /reports` report has no typed mDAOutputs; its
    free-form output is shown as MDAOutputEntry pairs under its own
    analytics_type (or the mDAType a producer declared for it)."""
    if report.mda_outputs is not None:
        return report.mda_outputs
    return [{"mDAType": report.mda_type or report.analytics_type,
             "mDAOutputList": [{"mDAOutputIEName": k, "mDAOutputIEValue": v} for k, v in (report.output or {}).items()]}]


def _report_view_for(db: Session):
    def view(report: MDAFReport) -> dict:
        deliveries = db.scalars(select(MDAReportDelivery).where(MDAReportDelivery.report_id == report.report_id)).all()
        return {"id": str(report.report_id), "attributes": {
            "mDAReportID": str(report.report_id), "mDAOutputs": _outputs_of(report),
            "mDARequestRef": _s(report.mda_request_id), "mDAFunctionRef": _s(report.mda_function_id),
            "reportKind": report.report_kind, "scope": report.scope,
            "deliveredToRequestRefList": [str(d.mda_request_id) for d in deliveries],
            "generatedAt": _iso(report.generated_at)}}
    return view


def _flat_entries(report: MDAFReport) -> dict[str, dict]:
    """mDAType -> flattened IE name/value map of the report."""
    out: dict[str, dict] = {}
    for item in _outputs_of(report):
        flat = dict(item["mDAOutputList"]) if isinstance(item["mDAOutputList"], dict) else {
            e["mDAOutputIEName"]: e.get("mDAOutputIEValue") for e in item["mDAOutputList"]}
        for prediction in flat.get("pmPredictions") or []:
            flat[prediction["pmName"]] = prediction["pmPredictedValue"]
        out.setdefault(item["mDAType"], {}).update(flat)
    return out


def _threshold_crossed(state: dict, entry: dict, value) -> tuple[bool, dict]:
    """Same edge-triggered crossing with hysteresis as MDASubscription's
    ThresholdInfo (main.py `_threshold_crossed`), per (request, IE)."""
    ie, direction, threshold, hysteresis = (entry["monitoredMDAOutputIE"], entry["thresholdDirection"],
                                            entry["thresholdValue"], entry.get("hysteresis", 0))
    previous = state.get(ie)
    if direction in ("UP", "UP_AND_DOWN") and value >= threshold and previous != "ABOVE":
        return True, {**state, ie: "ABOVE"}
    if direction in ("DOWN", "UP_AND_DOWN") and value <= threshold and previous != "BELOW":
        return True, {**state, ie: "BELOW"}
    if value < threshold - hysteresis:
        return False, {**state, ie: "BELOW"}
    if value > threshold + hysteresis:
        return False, {**state, ie: "ABOVE"}
    return False, state


def _request_matches(request: MDARequest, report: MDAFReport, entries: dict[str, dict], now: datetime.datetime) -> bool:
    if not _is_active(request, now):
        return False
    scope = (request.analytics_scope or {}).get("managedEntitiesScope")
    report_scope = (report.scope or {}).get("managedEntitiesScope")
    if scope and report_scope is not None and not set(scope) & set(report_scope):
        return False
    state = dict(request.threshold_state or {})
    matched = False
    for wanted in request.requested_mda_outputs:
        values = entries.get(wanted["mDAType"])
        if values is None:
            continue
        filters = wanted.get("mDAOutputIEFilters") or []
        ok, gated = True, False
        for f in filters:
            if f.get("timeOut") and datetime.datetime.fromisoformat(f["timeOut"].replace("Z", "+00:00")) <= now:
                ok = False
                break
            value = values.get(f["mDAOutputIEName"])
            if f.get("filterValue") is not None and str(value) != f["filterValue"]:
                ok = False
                break
            for threshold in f.get("threshold") or []:
                gated = True
                if isinstance(value, (int, float)):
                    crossed, state = _threshold_crossed(state, threshold, value)
                    if crossed:
                        gated = False
        if ok and not gated:
            matched = True
    request.threshold_state = state
    return matched


def _deliver(db: Session, report: MDAFReport, view: dict) -> None:
    """Matches the report against open requests and delivers it per each
    request's reportingMethod. Best-effort, like every notification here."""
    now = datetime.datetime.now(datetime.UTC)
    entries = _flat_entries(report)
    candidates: list[MDARequest | None]
    if report.mda_request_id is not None:
        candidates = [db.get(MDARequest, report.mda_request_id)]
    else:
        candidates = list(db.scalars(select(MDARequest)).all())
    for request in candidates:
        if request is None:
            continue
        if report.mda_request_id is None and not _request_matches(request, report, entries, now):
            continue
        delivery = MDAReportDelivery(report_id=report.report_id, mda_request_id=request.mda_request_id,
                                     reporting_method=request.reporting_method)
        db.add(delivery)
        if request.reporting_method == "NOTIFICATION":
            enqueue(db, request.reporting_target, {
                "notificationType": "notifyMDAReport", "mDARequestRef": str(request.mda_request_id), **view})
            delivery.notified = True  # enqueued, to be sent once this transaction commits
        elif request.reporting_method == "FILE":
            enqueue(db, request.reporting_target, {
                "notificationType": "notifyFileReady", "mDARequestRef": str(request.mda_request_id),
                "fileInfoList": [{"fileLocation": f"/mdaf/mda-reports/{report.report_id}/file",
                                  "fileContent": "MDAReport", "fileDataType": "Analytics"}]})
            delivery.notified = True
    db.commit()


def _forward_drift_to_mlmf(report: MDAFReport) -> list[str]:
    """W5-04: a DRIFT report naming `mLModelRef` is forwarded, as metrics,
    to every AIMgF MLMF subscription on that model — AIMgF's existing
    guard-KPI floor then decides whether a retrain fires. Best-effort."""
    forwarded = []
    for values in _flat_entries(report).values():
        model_ref = values.get("mLModelRef")
        if not model_ref:
            continue
        metrics = {k: v for k, v in values.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
        try:
            subs = _r1.get("/aimgf/mlmf/subscriptions", params={"model_id": str(model_ref)})
            if subs.status_code != 200:
                continue
            for sub in subs.json().get("items", []):
                _r1.post(f"/aimgf/mlmf/subscriptions/{sub['subscriptionId']}/reports", json=metrics)
                forwarded.append(sub["subscriptionId"])
        except Exception:  # noqa: BLE001, S112 — drift forwarding never fails the publish
            continue
    return forwarded


def _infer_kind(outputs: list[dict]) -> str:
    return "PREDICTION" if any(o["mDAType"] in ts28104.PREDICTION_MDA_TYPES for o in outputs) else "ANALYTICS"


@router.post("/mda-reports", status_code=201)
def publish_mda_report(body: MDAReportBody, db: Session = Depends(get_session)):
    if not body.mDAOutputs:
        raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED, detail="mDAOutputs must not be empty")
    if body.mDARequestRef is not None:
        _get(db, MDARequest, body.mDARequestRef, "MDARequest")
    if body.mDAFunctionRef is not None:
        _get(db, MDAFunction, body.mDAFunctionRef, "MDAFunction")
    _validate_input_sources_are_real_dme_artifacts(body.inputSources)
    outputs = ts28104.dump(body.mDAOutputs)
    flat = {}
    for o in body.mDAOutputs:
        flat.update(o.entries())
    report = MDAFReport(analytics_type=outputs[0]["mDAType"], mda_type=outputs[0]["mDAType"], mda_outputs=outputs,
                        output=json.loads(json.dumps(flat, default=str)), input_sources=body.inputSources,
                        scope={"managedEntitiesScope": body.managedEntitiesScope} if body.managedEntitiesScope else None,
                        report_kind=body.reportKind or _infer_kind(outputs),
                        mda_request_id=body.mDARequestRef, mda_function_id=body.mDAFunctionRef)
    db.add(report)
    db.flush()
    # Existing analytics_type subscribers (keyed by the first mDAType) are
    # notified exactly as for a legacy report; then MDARequest delivery, whose
    # commit makes the report and every notification one transaction (PR-MSG-1.8).
    _notify_report_subscribers(db, report)
    view = _report_view_for(db)(report)
    _deliver(db, report, view)
    if report.report_kind == "DRIFT":
        _forward_drift_to_mlmf(report)
    return _report_view_for(db)(report)


def deliver_legacy_report(db: Session, report: MDAFReport) -> None:
    """Called by `POST /reports` so a producer-push report satisfies open
    MDARequests too."""
    _deliver(db, report, _report_view_for(db)(report))


@router.get("/mda-reports")
def list_mda_reports(mda_type: str | None = None, report_kind: str | None = None, mda_request_id: uuid.UUID | None = None,
                     managed_entity: str | None = None, limit: int = PageLimit, offset: int = PageOffset,
                     db: Session = Depends(get_session)):
    """Filters: mDAType, report kind (ANALYTICS/PREDICTION/DRIFT), the
    request it was delivered to, and a managed entity in its scope."""
    stmt = select(MDAFReport)
    if mda_type:
        stmt = stmt.where((MDAFReport.mda_type == mda_type) | (MDAFReport.analytics_type == mda_type))
    if report_kind:
        stmt = stmt.where(MDAFReport.report_kind == report_kind)
    if mda_request_id:
        stmt = stmt.where(MDAFReport.report_id.in_(
            select(MDAReportDelivery.report_id).where(MDAReportDelivery.mda_request_id == mda_request_id)))
    rows = db.scalars(stmt.order_by(MDAFReport.generated_at.desc())).all()
    if managed_entity:
        rows = [r for r in rows if managed_entity in ((r.scope or {}).get("managedEntitiesScope") or [])]
    view = _report_view_for(db)
    page = paginate_list(rows, limit, offset)
    return {**page, "items": [view(r) for r in page["items"]]}


@router.get("/mda-reports/{report_id}")
def get_mda_report(report_id: uuid.UUID, db: Session = Depends(get_session)):
    return _report_view_for(db)(_get(db, MDAFReport, report_id, "MDAReport"))


@router.get("/mda-reports/{report_id}/file")
def download_mda_report_file(report_id: uuid.UUID, db: Session = Depends(get_session)):
    """FILE reportingMethod: the report as a downloadable JSON file."""
    report = _get(db, MDAFReport, report_id, "MDAReport")
    body = json.dumps(_report_view_for(db)(report), indent=2).encode()
    return Response(content=body, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="mda-report-{report_id}.json"'})
