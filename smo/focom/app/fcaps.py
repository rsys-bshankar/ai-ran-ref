"""O2-IMS fault and performance routes: AlarmEventRecord and AlarmSubscription, PerformanceMeasurementRecord, Job and Subscription (SA-FOCOM-6).

What it is: the routes `/alarms`, `/alarm-subscriptions`, `/performance`, `/performance-jobs` and `/performance-subscriptions`, mounted by `main.py`
through `router`. Design record: `focom/README.md` (1.2, 2.4, 2.8). The domain is infrastructure (O-Cloud host, node, cluster health); RAN-function
alarms and PM belong to RAN NF OAM.

`/alarms`, `/alarms/ingest` and `/performance` keep their old routes and fields (`resourceRef`, `severity`, `metricName`, `value`: the GUI reads them)
and return the O2-IMS fields beside them.

Realised from the spec: AlarmEventRecord with the X.733 `eventType`, the `PerceivedSeverity` enum and acknowledge / clear / change times;
AlarmSubscription with its NEW / CHANGE / CLEAR / ACKNOWLEDGE filter and `AlarmEvent` notifications; records, jobs and NOTIFICATION subscriptions
for performance, with a `PerformanceMeasurementReport` sent for job-linked records.

Not realised: FILE and STREAM performance reporting (a subscription for either is refused); `reportInterval`, `suppressRedundant` and
`heartbeatInterval` are stored but a report goes out as soon as a matching record arrives; a record with no job is stored and queryable but
never reported (the report format requires a job id); nothing collects measurements, they are ingested.

Notifications: every notification is an outbox row (`smo_shared.outbox.enqueue`) written in the same transaction as the change that caused it and
sent after the commit (PR-MSG-1.8). So a notification exists exactly when its change does, and an unreachable subscriber never fails a request.
Each route that notifies calls `_notify` / `_report` before `db.commit()`; moving a commit before them would separate the two.

Errors: bad input answers 422 `SCHEMA_VALIDATION_FAILED` (`_invalid`); an unknown alarm, job or subscription answers 404 `NRM_OBJECT_NOT_FOUND`.
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
    """Returns the current time as a timezone-aware UTC datetime."""
    return datetime.datetime.now(datetime.UTC)


def _iso(t: datetime.datetime | None) -> str | None:
    """Returns `t` as an ISO 8601 string, or None for None. A naive datetime (SQLite hands them back without a zone) is taken to be UTC."""
    return (t if t.tzinfo else t.replace(tzinfo=datetime.UTC)).isoformat() if t else None


def _invalid(detail: str):
    """Returns the 422 `SCHEMA_VALIDATION_FAILED` problem with `detail` (the caller raises it)."""
    return framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED, detail=detail)


def _severity(value: str) -> str:
    """Returns the stored (lowercase) form of a `PerceivedSeverity` given in any case; raises 422 when it is not one of `PERCEIVED_SEVERITIES`."""
    if value.upper() not in PERCEIVED_SEVERITIES:
        raise _invalid(f"severity {value!r} is not a PerceivedSeverity ({', '.join(PERCEIVED_SEVERITIES)})")
    return value.lower()


def _resource_type_of(db: Session, resource_ref: str) -> str | None:
    """Returns the `resource_type_id` of the inventory resource whose UUID is `resource_ref`, or None when it is not a UUID or names no resource.

    Alarms and performance records may carry any string as the resource (a host name, for example), so a miss is normal and not an error.
    """
    try:
        resource = db.get(Resource, uuid.UUID(resource_ref))
    except ValueError:
        return None
    return resource.resource_type_id if resource is not None else None


# ---------------------------------------------------------------- alarms

def _alarm_view(a: OCloudAlarm) -> dict:
    """Returns the wire form of an alarm: the O2-IMS `AlarmEventRecord` fields and, beside them, the older `alarmId`, `resourceRef` and `severity` the GUI reads.

    `severity` is the stored lowercase value and `perceivedSeverity` its upper-case view. `alarmAcknowledged` is the stored flag; the four time fields are
    ISO strings or null.
    """
    return {**ioc("AlarmEventRecord", "alarms", str(a.alarm_id)),
            "alarmId": str(a.alarm_id), "alarmEventRecordId": str(a.alarm_id),
            "resourceRef": a.resource_ref, "resourceId": a.resource_ref, "resourceTypeId": a.resource_type_id,
            "alarmDefinitionId": a.alarm_definition_id, "probableCauseId": a.probable_cause_id,
            "eventType": a.event_type, "severity": a.severity, "perceivedSeverity": a.severity.upper(),
            "alarmRaisedTime": _iso(a.raised_at), "alarmChangedTime": _iso(a.changed_at),
            "alarmClearedTime": _iso(a.cleared_at), "alarmAcknowledgeTime": _iso(a.acknowledged_at),
            "alarmAcknowledged": a.acknowledged, "extensions": a.extensions or []}


def _notify(db: Session, kind: str, alarm: OCloudAlarm) -> None:
    """Enqueues an `AlarmEvent` for every alarm subscription whose filter is unset or equals `kind` (NEW, CHANGE, CLEAR or ACKNOWLEDGE); one outbox row each.

    The rows are added to the caller's transaction and sent after the caller commits (PR-MSG-1.8); this function does not commit. A callback the SSRF guard
    refuses is dropped inside `enqueue` with a warning, so the subscription stays and simply never receives anything.
    """
    for sub in db.scalars(select(AlarmSubscription)).all():
        if sub.filter in (None, kind):
            enqueue(db, sub.callback, {"globalCloudId": GLOBAL_CLOUD_ID, "consumerSubscriptionId": sub.consumer_subscription_id,
                                       "alarmNotificationType": kind, "objectRef": f"/focom/alarms/{alarm.alarm_id}",
                                       "alarmEventRecord": _alarm_view(alarm)})


def _get_alarm(db: Session, alarm_id: uuid.UUID) -> OCloudAlarm:
    """Returns the alarm row, or raises 404 `NRM_OBJECT_NOT_FOUND`."""
    alarm = db.get(OCloudAlarm, alarm_id)
    if alarm is None:
        raise framework_error(FrameworkError.NRM_OBJECT_NOT_FOUND, detail=f"no such alarm {alarm_id}")
    return alarm


@router.get("/alarms")
def query_ocloud_alarms(severity: str | None = None, event_type: str | None = None, resource_ref: str | None = None,
                        limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    # Route notes (kept out of the docstring because FastAPI publishes it): `severity` is validated (422) and matched case-insensitively against the stored
    # lowercase value. `event_type` and `resource_ref` are exact matches and are not validated, so an unknown `event_type` returns an empty page.
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
    # Route notes: the alarm arrives as query parameters, not a body. 422 for a `severity` that is not a PerceivedSeverity (any case) and for an `event_type`
    # that is not one of `EVENT_TYPES` (exact case). `resource_type_id` defaults to the type of the inventory resource when `resource_ref` is its UUID.
    # Answers 200 (not 201) with `{alarmId}`. The `flush` gives the alarm its id before `_notify` writes it into the NEW notification; both commit together.
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
    # Route notes: 404 `NRM_OBJECT_NOT_FOUND` for an unknown id.
    return _alarm_view(_get_alarm(db, alarm_id))


@router.patch("/alarms/{alarm_id}/ack")
def acknowledge_alarm(alarm_id: uuid.UUID, db: Session = Depends(get_session)):
    # Route notes: sets the acknowledged flag and both the acknowledge and the changed time, notifies ACKNOWLEDGE and commits. Not idempotent: a second call
    # refreshes the times and notifies again. The severity is not touched. 404 for an unknown alarm.
    alarm = _get_alarm(db, alarm_id)
    alarm.acknowledged, alarm.acknowledged_at, alarm.changed_at = True, _now(), _now()
    _notify(db, "ACKNOWLEDGE", alarm)
    db.commit()
    return _alarm_view(alarm)


@router.patch("/alarms/{alarm_id}/clear")
def clear_alarm(alarm_id: uuid.UUID, db: Session = Depends(get_session)):
    # Route notes: rewrites the stored severity to `cleared` (so `perceivedSeverity` becomes CLEARED and the original severity is no longer known), sets the
    # cleared and changed time, notifies CLEAR and commits. Not idempotent. 404 for an unknown alarm.
    alarm = _get_alarm(db, alarm_id)
    alarm.severity, alarm.cleared_at, alarm.changed_at = "cleared", _now(), _now()
    _notify(db, "CLEAR", alarm)
    db.commit()
    return _alarm_view(alarm)


@router.patch("/alarms/{alarm_id}/severity")
def change_alarm_severity(alarm_id: uuid.UUID, severity: str, db: Session = Depends(get_session)):
    # Route notes: 422 for a severity that is not a PerceivedSeverity; sets the changed time, notifies CHANGE and commits. Changing to CLEARED sets no cleared
    # time (use `/clear`). 404 for an unknown alarm.
    alarm = _get_alarm(db, alarm_id)
    alarm.severity, alarm.changed_at = _severity(severity), _now()
    _notify(db, "CHANGE", alarm)
    db.commit()
    return _alarm_view(alarm)


# Body of POST /alarm-subscriptions. `filter` is one of NEW, CHANGE, CLEAR, ACKNOWLEDGE; unset subscribes to every kind. Unknown fields are refused (422).
class AlarmSubscriptionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    callback: str
    consumerSubscriptionId: str | None = None
    filter: Literal["NEW", "CHANGE", "CLEAR", "ACKNOWLEDGE"] | None = None


def _alarm_sub_view(s: AlarmSubscription) -> dict:
    """Returns the wire form of an alarm subscription (`ioc` pair, id, `consumerSubscriptionId`, `filter`, `callback`)."""
    return {**ioc("AlarmSubscription", "alarm-subscriptions", str(s.subscription_id)), "alarmSubscriptionId": str(s.subscription_id),
            "consumerSubscriptionId": s.consumer_subscription_id, "filter": s.filter, "callback": s.callback}


@router.post("/alarm-subscriptions", status_code=201)
def create_alarm_subscription(body: AlarmSubscriptionBody, db: Session = Depends(get_session)):
    # Route notes: 201. The callback URL is not checked here; the SSRF guard runs when a notification is enqueued, and a refused destination is dropped there
    # with a log warning (the subscription is kept).
    sub = AlarmSubscription(callback=body.callback, consumer_subscription_id=body.consumerSubscriptionId, filter=body.filter)
    db.add(sub)
    db.commit()
    return _alarm_sub_view(sub)


@router.get("/alarm-subscriptions")
def list_alarm_subscriptions(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    # Route notes: paginated.
    page = paginate(db, select(AlarmSubscription), limit, offset)
    return {**page, "items": [_alarm_sub_view(s) for s in page["items"]]}


@router.get("/alarm-subscriptions/{subscription_id}")
def get_alarm_subscription(subscription_id: uuid.UUID, db: Session = Depends(get_session)):
    # Route notes: 404 `NRM_OBJECT_NOT_FOUND` for an unknown id.
    sub = db.get(AlarmSubscription, subscription_id)
    if sub is None:
        raise framework_error(FrameworkError.NRM_OBJECT_NOT_FOUND, detail=f"no such alarm subscription {subscription_id}")
    return _alarm_sub_view(sub)


@router.delete("/alarm-subscriptions/{subscription_id}", status_code=204)
def delete_alarm_subscription(subscription_id: uuid.UUID, db: Session = Depends(get_session)):
    # Route notes: idempotent, 204 for an unknown id.
    sub = db.get(AlarmSubscription, subscription_id)
    if sub is not None:
        db.delete(sub)
        db.commit()


# ---------------------------------------------------------------- performance

def _metric_view(m: OCloudPerformanceMetric) -> dict:
    """Returns the wire form of a performance record: the older `resourceRef`, `metricName` and `value` beside the O2-IMS names.

    `measurementValue` is the object form when one was stored and otherwise the scalar `value`; `timeStamp` is the collection time.
    """
    return {"resourceRef": m.resource_ref, "metricName": m.metric_name, "value": m.value,
            "resourceId": m.resource_ref, "performanceMeasurementDefinitionId": m.metric_name,
            "performanceMeasurementJobId": m.job_id, "timeStamp": _iso(m.collected_at),
            "measurementValue": m.measurement_value if m.measurement_value is not None else m.value, "isSuspect": m.is_suspect}


# Body of POST /performance/ingest, a PerformanceMeasurementRecord. `measurementValue` is a number or an object; `timeStamp` defaults to now; the job id,
# when given, must be a UUID. Unknown fields are refused (422).
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
    # Route notes: three optional exact-match filters; `performance_measurement_job_id` is compared with the job id stored as a string. Paginated.
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
    # Route notes: 201. With a job id: 422 when the job does not exist or is not ACTIVE; otherwise the job's status becomes RUNNING. A number is stored in
    # `value`, an object in `measurement_value`. `_report` enqueues the report notifications in this transaction, before the commit. The response is the
    # record plus `notified`, the number of reports enqueued (always 0 for a record with no job).
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
    """Returns True when `record` satisfies one PerformanceSubscriptionCriteria entry.

    Each of the four criteria lists that is non-empty must contain a pair whose value (a string, or a list of strings) includes the record's job id, resource
    type, resource id or measurement id respectively; an empty or missing list does not restrict. `resource_type_id` is the one `_report` resolved for the record.
    """
    actual = {"jobCriteria": record.job_id, "resourceTypeCriteria": resource_type_id,
              "resourceCriteria": record.resource_ref, "measurementCriteria": record.metric_name}
    for field, value in actual.items():
        for pair in entry.get(field) or []:
            wanted = pair["value"] if isinstance(pair["value"], list) else [pair["value"]]
            if value not in wanted:
                return False
    return True


def _report(db: Session, record: OCloudPerformanceMetric, job: PerformanceJob | None) -> int:
    """Enqueues a `PerformanceMeasurementReport` for each subscription whose criteria admit `record`, and returns how many rows were enqueued.

    Returns 0 and sends nothing for a record with no job (`job` is None): the report format needs a job id. A subscription with no criteria receives every
    job-linked record; otherwise one matching criteria entry is enough. The rows join the caller's transaction and are sent after its commit. The count is of
    rows the code asked for, so it still counts a destination that `enqueue` then drops because the SSRF guard refuses it.
    """
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


# Body of POST /performance-jobs. `collectionInterval` must be above 0 (seconds; stored, nothing collects on it); `state` defaults to ACTIVE. Unknown fields are refused.
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
    # The complete record set of the job, in no defined order.
    """Returns the wire form of a performance job, with `measuredResources` and `collectedMeasurements` derived from the records stored for it.

    The two lists are computed on every call from all the job's records (one query plus a resource-type lookup per record), and each entry's `timeAdded` is the
    collection time of the first record the query returned, since the query has no order. `isCurrentlyMeasured` is true while the job is ACTIVE.
    """
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
    """Returns the job row, or raises 404 `NRM_OBJECT_NOT_FOUND`."""
    job = db.get(PerformanceJob, job_id)
    if job is None:
        raise framework_error(FrameworkError.NRM_OBJECT_NOT_FOUND, detail=f"no such performance measurement job {job_id}")
    return job


@router.post("/performance-jobs", status_code=201)
def create_performance_job(body: PerformanceJobBody, db: Session = Depends(get_session)):
    # Route notes: 201; the job starts IDLE (its stored default) and becomes RUNNING on the first ingest that names it. A `collectionInterval` of 0 or less is 422.
    job = PerformanceJob(consumer_job_id=body.consumerPerformanceJobId, state=body.state, collection_interval=body.collectionInterval,
                         resource_scope_criteria=[c.model_dump() for c in body.resourceScopeCriteria],
                         measurement_selection_criteria=[c.model_dump() for c in body.measurementSelectionCriteria],
                         qualified_resource_types=body.qualifiedResourceTypes, extensions=[e.model_dump() for e in body.extensions])
    db.add(job)
    db.commit()
    return _job_view(db, job)


@router.get("/performance-jobs")
def list_performance_jobs(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    # Route notes: paginated; every job in the page is rebuilt from its records (`_job_view`), so a page of busy jobs is expensive.
    page = paginate(db, select(PerformanceJob), limit, offset)
    return {**page, "items": [_job_view(db, j) for j in page["items"]]}


@router.get("/performance-jobs/{job_id}")
def get_performance_job(job_id: uuid.UUID, db: Session = Depends(get_session)):
    # Route notes: 404 `NRM_OBJECT_NOT_FOUND` for an unknown id.
    return _job_view(db, _get_job(db, job_id))


@router.patch("/performance-jobs/{job_id}")
def set_performance_job_state(job_id: uuid.UUID, state: Literal["ACTIVE", "SUSPENDED", "DEPRECATED"], db: Session = Depends(get_session)):
    # Route notes: `state` is a query parameter (422 for any value but ACTIVE, SUSPENDED, DEPRECATED). Any state other than ACTIVE sets the status to IDLE;
    # ACTIVE leaves the status as it was. A suspended job refuses ingest (422). No notification is sent. 404 for an unknown job.
    job = _get_job(db, job_id)
    job.state = state
    job.status = "IDLE" if state != "ACTIVE" else job.status
    db.commit()
    return _job_view(db, job)


@router.delete("/performance-jobs/{job_id}", status_code=204)
def delete_performance_job(job_id: uuid.UUID, db: Session = Depends(get_session)):
    # Route notes: idempotent, 204 for an unknown id. The job's records are kept; they keep the deleted job's id in `job_id`.
    job = db.get(PerformanceJob, job_id)
    if job is not None:
        db.delete(job)
        db.commit()


# One MeasurementReportingFrequency of a performance subscription: `subscriptionMode` is required; the intervals are stored and echoed, not scheduled.
class ReportingFrequency(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subscriptionCriteria: list[dict] = []
    subscriptionMode: Literal["TARGET_DEFINED", "ON_CHANGE", "SAMPLE"]
    reportInterval: int | None = None
    suppressRedundant: bool | None = None
    heartbeatInterval: int | None = None


# Body of POST /performance-subscriptions. Only `reportFormat` NOTIFICATION is accepted (422 for FILE or STREAM); `remoteFileLocation` is accepted and
# ignored. `globalSubscriptionCriteria` entries are checked by the route (known fields, key/value pairs), not by this model.
class PerformanceSubscriptionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consumerPerformanceSubscriptionId: str | None = None
    globalSubscriptionCriteria: list[dict] = []
    reportFormat: Literal["NOTIFICATION", "FILE", "STREAM"] = "NOTIFICATION"
    remoteFileLocation: str | None = None
    callback: str
    measurementReportingFrequencies: list[ReportingFrequency] = []


def _perf_sub_view(s: PerformanceSubscription) -> dict:
    """Returns the wire form of a performance subscription (`ioc` pair, ids, criteria, `reportFormat`, `callback`, reporting frequencies)."""
    return {**ioc("PerformanceSubscription", "performance-subscriptions", str(s.subscription_id)),
            "performanceSubscriptionId": str(s.subscription_id), "consumerPerformanceSubscriptionId": s.consumer_subscription_id,
            "globalSubscriptionCriteria": s.global_subscription_criteria, "reportFormat": s.report_format, "callback": s.callback,
            "measurementReportingFrequencies": s.measurement_reporting_frequencies}


@router.post("/performance-subscriptions", status_code=201)
def create_performance_subscription(body: PerformanceSubscriptionBody, db: Session = Depends(get_session)):
    # Route notes: 201. 422 for a `reportFormat` other than NOTIFICATION, for a criteria field not in `CRITERIA_KEYS`, and for a criteria pair whose key is not
    # the one that field supports (or that has no `value`). The callback URL is not checked here (see `create_alarm_subscription`).
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
    # Route notes: paginated.
    page = paginate(db, select(PerformanceSubscription), limit, offset)
    return {**page, "items": [_perf_sub_view(s) for s in page["items"]]}


@router.get("/performance-subscriptions/{subscription_id}")
def get_performance_subscription(subscription_id: uuid.UUID, db: Session = Depends(get_session)):
    # Route notes: 404 `NRM_OBJECT_NOT_FOUND` for an unknown id.
    sub = db.get(PerformanceSubscription, subscription_id)
    if sub is None:
        raise framework_error(FrameworkError.NRM_OBJECT_NOT_FOUND, detail=f"no such performance subscription {subscription_id}")
    return _perf_sub_view(sub)


@router.delete("/performance-subscriptions/{subscription_id}", status_code=204)
def delete_performance_subscription(subscription_id: uuid.UUID, db: Session = Depends(get_session)):
    # Route notes: idempotent, 204 for an unknown id.
    sub = db.get(PerformanceSubscription, subscription_id)
    if sub is not None:
        db.delete(sub)
        db.commit()
