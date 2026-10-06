"""O2IMS fault and performance: AlarmEventRecord / AlarmSubscription and
PerformanceMeasurementRecord / Job / Subscription (SA-FOCOM-6).

`/alarms`, `/alarms/ingest` and `/performance` keep their old routes and
fields (`resourceRef`, `severity`, `metricName`, `value`: the GUI reads them)
and gain the O2IMS fields beside them.

Realised from the spec: AlarmEventRecord with the X.733 `eventType`, the
`PerceivedSeverity` enum, acknowledge / clear / change times; AlarmSubscription
with its NEW / CHANGE / CLEAR / ACKNOWLEDGE filter and `AlarmEvent`
notifications; records, jobs and NOTIFICATION subscriptions for performance,
with `PerformanceMeasurementReport` pushed for job-linked records.

Not realised: FILE and STREAM performance reporting (a subscription for either
is refused); `reportInterval`, `suppressRedundant` and `heartbeatInterval` are
stored but a report goes out as soon as a matching record arrives; a record
with no job is stored and queryable but never reported (the report format
requires a job id); nothing collects measurements, they are ingested.
"""

import datetime
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.pagination import PageLimit, PageOffset, paginate
from smo_shared.outbox import enqueue

from .common import PHASE1_CLUSTER_ID, AttributeValuePair, global_cloud_id, ioc
from .models import AlarmSubscription, OCloudAlarm, OCloudPerformanceMetric, PerformanceJob, PerformanceSubscription, Resource

router = APIRouter()

PERCEIVED_SEVERITIES = ("INDETERMINATE", "CRITICAL", "MAJOR", "MINOR", "WARNING", "CLEARED")
EVENT_TYPES = ("COMMUNICATIONS_ALARM", "PROCESSING_ERROR_ALARM", "ENVIRONMENTAL_ALARM", "QOS_ALARM", "EQUIPMENT_ALARM",
               "INTEGRITY_VIOLATION", "OPERATIONAL_VIOLATION", "PHYSICAL_VIOLATION",
               "SECURITY_SERVICE_OR_MECHANISM_VIOLATION", "TIME_DOMAIN_VIOLATION", "OTHER")
GLOBAL_CLOUD_ID = global_cloud_id(PHASE1_CLUSTER_ID)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _iso(t: datetime.datetime | None) -> str | None:
    return (t if t.tzinfo else t.replace(tzinfo=datetime.UTC)).isoformat() if t else None


def _invalid(detail: str):
    return framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED, detail=detail)


def _severity(value: str) -> str:
    if value.upper() not in PERCEIVED_SEVERITIES:
        raise _invalid(f"severity {value!r} is not a PerceivedSeverity ({', '.join(PERCEIVED_SEVERITIES)})")
    return value.lower()


def _resource_type_of(db: Session, resource_ref: str) -> str | None:
    try:
        resource = db.get(Resource, uuid.UUID(resource_ref))
    except ValueError:
        return None
    return resource.resource_type_id if resource is not None else None


# ---------------------------------------------------------------- alarms

def _alarm_view(a: OCloudAlarm) -> dict:
    return {**ioc("AlarmEventRecord", "alarms", str(a.alarm_id)),
            "alarmId": str(a.alarm_id), "alarmEventRecordId": str(a.alarm_id),
            "resourceRef": a.resource_ref, "resourceId": a.resource_ref, "resourceTypeId": a.resource_type_id,
            "alarmDefinitionId": a.alarm_definition_id, "probableCauseId": a.probable_cause_id,
            "eventType": a.event_type, "severity": a.severity, "perceivedSeverity": a.severity.upper(),
            "alarmRaisedTime": _iso(a.raised_at), "alarmChangedTime": _iso(a.changed_at),
            "alarmClearedTime": _iso(a.cleared_at), "alarmAcknowledgeTime": _iso(a.acknowledged_at),
            "alarmAcknowledged": a.acknowledged, "extensions": a.extensions or []}


def _notify(db: Session, kind: str, alarm: OCloudAlarm) -> None:
    """AlarmEvent to every subscription whose filter admits `kind`: one outbox row each, in the caller's transaction (PR-MSG-1.8),
    sent after the caller commits. The caller commits after this."""
    for sub in db.scalars(select(AlarmSubscription)).all():
        if sub.filter in (None, kind):
            enqueue(db, sub.callback, {"globalCloudId": GLOBAL_CLOUD_ID, "consumerSubscriptionId": sub.consumer_subscription_id,
                                       "alarmNotificationType": kind, "objectRef": f"/focom/alarms/{alarm.alarm_id}",
                                       "alarmEventRecord": _alarm_view(alarm)})


def _get_alarm(db: Session, alarm_id: uuid.UUID) -> OCloudAlarm:
    alarm = db.get(OCloudAlarm, alarm_id)
    if alarm is None:
        raise framework_error(FrameworkError.NRM_OBJECT_NOT_FOUND, detail=f"no such alarm {alarm_id}")
    return alarm


@router.get("/alarms")
def query_ocloud_alarms(severity: str | None = None, event_type: str | None = None, resource_ref: str | None = None,
                        limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    stmt = select(OCloudAlarm)
    if severity:
        stmt = stmt.where(OCloudAlarm.severity == _severity(severity))
    if event_type:
        stmt = stmt.where(OCloudAlarm.event_type == event_type)
    if resource_ref:
        stmt = stmt.where(OCloudAlarm.resource_ref == resource_ref)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [_alarm_view(a) for a in page["items"]]}


@router.post("/alarms/ingest")
def ingest_ocloud_alarm(resource_ref: str, severity: str, event_type: str = "OTHER", alarm_definition_id: str | None = None,
                        probable_cause_id: str | None = None, resource_type_id: str | None = None,
                        db: Session = Depends(get_session)):
    if event_type not in EVENT_TYPES:
        raise _invalid(f"eventType {event_type!r} is not one of {', '.join(EVENT_TYPES)}")
    alarm = OCloudAlarm(resource_ref=resource_ref, severity=_severity(severity), event_type=event_type,
                        alarm_definition_id=alarm_definition_id, probable_cause_id=probable_cause_id,
                        resource_type_id=resource_type_id or _resource_type_of(db, resource_ref))
    db.add(alarm)
    db.flush()  # the alarm's id, for the notification
    _notify(db, "NEW", alarm)
    db.commit()
    return {"alarmId": str(alarm.alarm_id)}


@router.get("/alarms/{alarm_id}")
def get_ocloud_alarm(alarm_id: uuid.UUID, db: Session = Depends(get_session)):
    return _alarm_view(_get_alarm(db, alarm_id))


@router.patch("/alarms/{alarm_id}/ack")
def acknowledge_alarm(alarm_id: uuid.UUID, db: Session = Depends(get_session)):
    alarm = _get_alarm(db, alarm_id)
    alarm.acknowledged, alarm.acknowledged_at, alarm.changed_at = True, _now(), _now()
    _notify(db, "ACKNOWLEDGE", alarm)
    db.commit()
    return _alarm_view(alarm)


@router.patch("/alarms/{alarm_id}/clear")
def clear_alarm(alarm_id: uuid.UUID, db: Session = Depends(get_session)):
    alarm = _get_alarm(db, alarm_id)
    alarm.severity, alarm.cleared_at, alarm.changed_at = "cleared", _now(), _now()
    _notify(db, "CLEAR", alarm)
    db.commit()
    return _alarm_view(alarm)


@router.patch("/alarms/{alarm_id}/severity")
def change_alarm_severity(alarm_id: uuid.UUID, severity: str, db: Session = Depends(get_session)):
    alarm = _get_alarm(db, alarm_id)
    alarm.severity, alarm.changed_at = _severity(severity), _now()
    _notify(db, "CHANGE", alarm)
    db.commit()
    return _alarm_view(alarm)


class AlarmSubscriptionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    callback: str
    consumerSubscriptionId: str | None = None
    filter: Literal["NEW", "CHANGE", "CLEAR", "ACKNOWLEDGE"] | None = None


def _alarm_sub_view(s: AlarmSubscription) -> dict:
    return {**ioc("AlarmSubscription", "alarm-subscriptions", str(s.subscription_id)), "alarmSubscriptionId": str(s.subscription_id),
            "consumerSubscriptionId": s.consumer_subscription_id, "filter": s.filter, "callback": s.callback}


@router.post("/alarm-subscriptions", status_code=201)
def create_alarm_subscription(body: AlarmSubscriptionBody, db: Session = Depends(get_session)):
    sub = AlarmSubscription(callback=body.callback, consumer_subscription_id=body.consumerSubscriptionId, filter=body.filter)
    db.add(sub)
    db.commit()
    return _alarm_sub_view(sub)


@router.get("/alarm-subscriptions")
def list_alarm_subscriptions(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    page = paginate(db, select(AlarmSubscription), limit, offset)
    return {**page, "items": [_alarm_sub_view(s) for s in page["items"]]}


@router.get("/alarm-subscriptions/{subscription_id}")
def get_alarm_subscription(subscription_id: uuid.UUID, db: Session = Depends(get_session)):
    sub = db.get(AlarmSubscription, subscription_id)
    if sub is None:
        raise framework_error(FrameworkError.NRM_OBJECT_NOT_FOUND, detail=f"no such alarm subscription {subscription_id}")
    return _alarm_sub_view(sub)


@router.delete("/alarm-subscriptions/{subscription_id}", status_code=204)
def delete_alarm_subscription(subscription_id: uuid.UUID, db: Session = Depends(get_session)):
    sub = db.get(AlarmSubscription, subscription_id)
    if sub is not None:
        db.delete(sub)
        db.commit()


# ---------------------------------------------------------------- performance

def _metric_view(m: OCloudPerformanceMetric) -> dict:
    return {"resourceRef": m.resource_ref, "metricName": m.metric_name, "value": m.value,
            "resourceId": m.resource_ref, "performanceMeasurementDefinitionId": m.metric_name,
            "performanceMeasurementJobId": m.job_id, "timeStamp": _iso(m.collected_at),
            "measurementValue": m.measurement_value if m.measurement_value is not None else m.value, "isSuspect": m.is_suspect}


class PerformanceIngestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resourceId: str
    performanceMeasurementDefinitionId: str
    measurementValue: float | dict[str, Any]
    performanceMeasurementJobId: uuid.UUID | None = None
    timeStamp: datetime.datetime | None = None
    isSuspect: bool = False


@router.get("/performance")
def query_ocloud_performance(resource_ref: str | None = None, performance_measurement_job_id: str | None = None,
                             performance_measurement_definition_id: str | None = None, limit: int = PageLimit,
                             offset: int = PageOffset, db: Session = Depends(get_session)):
    stmt = select(OCloudPerformanceMetric)
    if resource_ref:
        stmt = stmt.where(OCloudPerformanceMetric.resource_ref == resource_ref)
    if performance_measurement_job_id:
        stmt = stmt.where(OCloudPerformanceMetric.job_id == performance_measurement_job_id)
    if performance_measurement_definition_id:
        stmt = stmt.where(OCloudPerformanceMetric.metric_name == performance_measurement_definition_id)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [_metric_view(m) for m in page["items"]]}


@router.post("/performance/ingest", status_code=201)
def ingest_performance_record(body: PerformanceIngestBody, db: Session = Depends(get_session)):
    job = None
    if body.performanceMeasurementJobId is not None:
        job = db.get(PerformanceJob, body.performanceMeasurementJobId)
        if job is None:
            raise _invalid(f"unknown performance measurement job {body.performanceMeasurementJobId}")
        if job.state != "ACTIVE":
            raise _invalid(f"performance measurement job {job.job_id} is {job.state}")
        job.status = "RUNNING"
    scalar_value = body.measurementValue if isinstance(body.measurementValue, (int, float)) else None
    record = OCloudPerformanceMetric(resource_ref=body.resourceId, metric_name=body.performanceMeasurementDefinitionId,
                                     value=float(scalar_value) if scalar_value is not None else None,
                                     measurement_value=None if scalar_value is not None else body.measurementValue, is_suspect=body.isSuspect,
                                     job_id=str(job.job_id) if job else None,
                                     collected_at=body.timeStamp or _now())
    db.add(record)
    notified = _report(db, record, job)  # outbox rows for the matching subscriptions, committed with the record
    db.commit()
    return {**_metric_view(record), "notified": notified}


# PerformanceSubscriptionCriteria field -> the one AttributeValuePair key it supports
CRITERIA_KEYS = {"jobCriteria": "performanceMeasurementJobId", "resourceTypeCriteria": "resourceTypeId",
                 "resourceCriteria": "resourceId", "measurementCriteria": "performanceMeasurementDefinitionId"}


def _criteria_match(entry: dict, record: OCloudPerformanceMetric, resource_type_id: str | None) -> bool:
    """One PerformanceSubscriptionCriteria: every non-empty list of key/value pairs
    must match the record (a value may be a string or a list of strings)."""
    actual = {"jobCriteria": record.job_id, "resourceTypeCriteria": resource_type_id,
              "resourceCriteria": record.resource_ref, "measurementCriteria": record.metric_name}
    for field, value in actual.items():
        for pair in entry.get(field) or []:
            wanted = pair["value"] if isinstance(pair["value"], list) else [pair["value"]]
            if value not in wanted:
                return False
    return True


def _report(db: Session, record: OCloudPerformanceMetric, job: PerformanceJob | None) -> int:
    if job is None:
        return 0
    resource_type_id = _resource_type_of(db, record.resource_ref)
    sent = 0
    for sub in db.scalars(select(PerformanceSubscription)).all():
        criteria = sub.global_subscription_criteria
        if criteria and not any(_criteria_match(c, record, resource_type_id) for c in criteria):
            continue
        enqueue(db, sub.callback, {
            "globalCloudId": GLOBAL_CLOUD_ID, "notificationTime": _iso(_now()), "performanceSubscriptionId": str(sub.subscription_id),
            "consumerPerformanceSubscriptionId": sub.consumer_subscription_id,
            "jobReports": [{"performanceJobId": str(job.job_id), "consumerJobId": job.consumer_job_id, "measuredResources": [{
                "resourceId": record.resource_ref, "measurementValues": [{
                    "performanceMeasurementId": record.metric_name, "measurementCollectionTime": _iso(record.collected_at),
                    "measurementValue": record.measurement_value if record.measurement_value is not None else record.value,
                    "isSuspect": record.is_suspect}]}]}]})
        sent += 1
    return sent


class PerformanceJobBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consumerPerformanceJobId: str | None = None
    collectionInterval: int = Field(gt=0)
    resourceScopeCriteria: list[AttributeValuePair] = []
    measurementSelectionCriteria: list[AttributeValuePair] = []
    qualifiedResourceTypes: list[str] = []
    state: Literal["ACTIVE", "SUSPENDED", "DEPRECATED"] = "ACTIVE"
    extensions: list[AttributeValuePair] = []


def _job_view(db: Session, j: PerformanceJob) -> dict:
    records = db.scalars(select(OCloudPerformanceMetric).where(OCloudPerformanceMetric.job_id == str(j.job_id))).all()
    current = j.state == "ACTIVE"
    measured: dict[str, dict[str, Any]] = {}
    collected: dict[tuple[str | None, str], dict[str, Any]] = {}
    for r in records:
        rt = _resource_type_of(db, r.resource_ref)
        measured.setdefault(r.resource_ref, {"resourceTypeId": rt, "resourceId": r.resource_ref, "timeAdded": [_iso(r.collected_at)],
                                             "timeDeleted": [], "isCurrentlyMeasured": current})
        collected.setdefault((rt, r.metric_name), {
            "resourceTypeId": rt, "performanceMeasurementDefinitionId": r.metric_name,
            "performanceMeasurementDefnitionId": r.metric_name,  # the spec's `required` list spells it this way
            "timeAdded": [_iso(r.collected_at)], "timeDeleted": [], "isCurrentlyMeasured": current})
    return {**ioc("PerformanceMeasurementJob", "performance-jobs", str(j.job_id)), "performanceMeasurementJobId": str(j.job_id),
            "consumerPerformanceJobId": j.consumer_job_id, "state": j.state, "collectionInterval": j.collection_interval,
            "resourceScopeCriteria": j.resource_scope_criteria, "measurementSelectionCriteria": j.measurement_selection_criteria,
            "status": j.status, "preInstalledJob": j.pre_installed, "qualifiedResourceTypes": j.qualified_resource_types,
            "measuredResources": list(measured.values()), "collectedMeasurements": list(collected.values()),
            "extensions": j.extensions or []}


def _get_job(db: Session, job_id: uuid.UUID) -> PerformanceJob:
    job = db.get(PerformanceJob, job_id)
    if job is None:
        raise framework_error(FrameworkError.NRM_OBJECT_NOT_FOUND, detail=f"no such performance measurement job {job_id}")
    return job


@router.post("/performance-jobs", status_code=201)
def create_performance_job(body: PerformanceJobBody, db: Session = Depends(get_session)):
    job = PerformanceJob(consumer_job_id=body.consumerPerformanceJobId, state=body.state, collection_interval=body.collectionInterval,
                         resource_scope_criteria=[c.model_dump() for c in body.resourceScopeCriteria],
                         measurement_selection_criteria=[c.model_dump() for c in body.measurementSelectionCriteria],
                         qualified_resource_types=body.qualifiedResourceTypes, extensions=[e.model_dump() for e in body.extensions])
    db.add(job)
    db.commit()
    return _job_view(db, job)


@router.get("/performance-jobs")
def list_performance_jobs(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    page = paginate(db, select(PerformanceJob), limit, offset)
    return {**page, "items": [_job_view(db, j) for j in page["items"]]}


@router.get("/performance-jobs/{job_id}")
def get_performance_job(job_id: uuid.UUID, db: Session = Depends(get_session)):
    return _job_view(db, _get_job(db, job_id))


@router.patch("/performance-jobs/{job_id}")
def set_performance_job_state(job_id: uuid.UUID, state: Literal["ACTIVE", "SUSPENDED", "DEPRECATED"], db: Session = Depends(get_session)):
    job = _get_job(db, job_id)
    job.state = state
    job.status = "IDLE" if state != "ACTIVE" else job.status
    db.commit()
    return _job_view(db, job)


@router.delete("/performance-jobs/{job_id}", status_code=204)
def delete_performance_job(job_id: uuid.UUID, db: Session = Depends(get_session)):
    job = db.get(PerformanceJob, job_id)
    if job is not None:
        db.delete(job)
        db.commit()


class ReportingFrequency(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subscriptionCriteria: list[dict] = []
    subscriptionMode: Literal["TARGET_DEFINED", "ON_CHANGE", "SAMPLE"]
    reportInterval: int | None = None
    suppressRedundant: bool | None = None
    heartbeatInterval: int | None = None


class PerformanceSubscriptionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consumerPerformanceSubscriptionId: str | None = None
    globalSubscriptionCriteria: list[dict] = []
    reportFormat: Literal["NOTIFICATION", "FILE", "STREAM"] = "NOTIFICATION"
    remoteFileLocation: str | None = None
    callback: str
    measurementReportingFrequencies: list[ReportingFrequency] = []


def _perf_sub_view(s: PerformanceSubscription) -> dict:
    return {**ioc("PerformanceSubscription", "performance-subscriptions", str(s.subscription_id)),
            "performanceSubscriptionId": str(s.subscription_id), "consumerPerformanceSubscriptionId": s.consumer_subscription_id,
            "globalSubscriptionCriteria": s.global_subscription_criteria, "reportFormat": s.report_format, "callback": s.callback,
            "measurementReportingFrequencies": s.measurement_reporting_frequencies}


@router.post("/performance-subscriptions", status_code=201)
def create_performance_subscription(body: PerformanceSubscriptionBody, db: Session = Depends(get_session)):
    if body.reportFormat != "NOTIFICATION":
        raise _invalid(f"reportFormat {body.reportFormat} is not supported (NOTIFICATION only)")
    for entry in body.globalSubscriptionCriteria:
        for field in entry:
            if field not in CRITERIA_KEYS:
                raise _invalid(f"unknown PerformanceSubscriptionCriteria field {field!r}")
            for pair in entry[field]:
                if not isinstance(pair, dict) or pair.get("key") != CRITERIA_KEYS[field] or "value" not in pair:
                    raise _invalid(f"{field} entries are AttributeValuePairs with key {CRITERIA_KEYS[field]!r}")
    sub = PerformanceSubscription(consumer_subscription_id=body.consumerPerformanceSubscriptionId,
                                  global_subscription_criteria=body.globalSubscriptionCriteria, report_format=body.reportFormat,
                                  callback=body.callback,
                                  measurement_reporting_frequencies=[f.model_dump() for f in body.measurementReportingFrequencies])
    db.add(sub)
    db.commit()
    return _perf_sub_view(sub)


@router.get("/performance-subscriptions")
def list_performance_subscriptions(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    page = paginate(db, select(PerformanceSubscription), limit, offset)
    return {**page, "items": [_perf_sub_view(s) for s in page["items"]]}


@router.get("/performance-subscriptions/{subscription_id}")
def get_performance_subscription(subscription_id: uuid.UUID, db: Session = Depends(get_session)):
    sub = db.get(PerformanceSubscription, subscription_id)
    if sub is None:
        raise framework_error(FrameworkError.NRM_OBJECT_NOT_FOUND, detail=f"no such performance subscription {subscription_id}")
    return _perf_sub_view(sub)


@router.delete("/performance-subscriptions/{subscription_id}", status_code=204)
def delete_performance_subscription(subscription_id: uuid.UUID, db: Session = Depends(get_session)):
    sub = db.get(PerformanceSubscription, subscription_id)
    if sub is not None:
        db.delete(sub)
        db.commit()
