"""RAN NF OAM SMOS.

SMO Design v1.3 section 3.10, extended by RAN NF OAM LLD sections 1-8:
Option A endpoint registry, multi-function-ME addressing, schema-checked
writes with decomposed-PATCH aggregation, fleet-unique alarm IDs, and the
explicit clarification that SubscribePM is a DME-producer registration,
never a clause-8 call (no such API exists).

CM writes dispatch as NETCONF-shaped <edit-config> RPCs (netconf_client.py)
— the confirmed protocol per HISTORY.md's "CM cache sync method" item.
An ME provisioned for RESTCONF has no dispatch implementation yet and is
rejected with PROTOCOL_NOT_SUPPORTED rather than silently applied.
"""

import datetime
import os
import time
import uuid

from fastapi import Depends, FastAPI, Query
from pydantic import BaseModel, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.r1_client import R1Client
from smo_shared.timeutil import as_utc
from smo_shared.openapi_security import apply_r1_gateway_security
from smo_shared.correlation import apply_correlation_id
from smo_shared.pagination import PageLimit, PageOffset, paginate

from .models import Alarm, CMSchemaCache, VendorCapability, FMSubscription, ManagedEntity, O1AdaptorEndpoint, PMSubscription, SoftwareManagementJob, WriteConfigJob, WriteConfigSubChange
from .netconf_client import send_edit_config, send_get_config
from .vendors import MnsService, check_vendor_mode, require_service, router as vendors_router, schema_problems
from .statemachine import (
    ENDPOINT_HEALTH_FSM,
    SOFTWARE_MANAGEMENT_FSM,
    WRITE_CONFIG_JOB_FSM,
    EndpointEvent,
    EndpointHealth,
    JobEvent,
    JobState,
    PHASE_ORDER,
    SwmEvent,
    SwmPhase,
    SwmState,
    aggregate_event,
)

app = FastAPI(title="RAN NF OAM SMOS")
apply_r1_gateway_security(app)
apply_correlation_id(app)

MISSED_HEARTBEAT_THRESHOLD = datetime.timedelta(seconds=90)

# Wave 10.1 (W10-19): a transient NETCONF failure (timeout / unreachable
# agent) is retried — attempt 1 immediately, then after +5, +10 and +20 s
# (the delays before each attempt; "max 3 retries"). An <rpc-error> is a
# definite answer and never retried. Exhausting the retries raises an
# alarm on the ME. Overridable for demos and tests.
NETCONF_RETRY_DELAYS = [float(d) for d in os.environ.get("RAN_NF_OAM_NETCONF_RETRY_DELAYS", "0,5,10,20").split(",")]
_sleep = time.sleep


def _dispatch_with_retries(adaptor_uri: str, change: dict, attribute_changes: dict, message_id: str,
                           operation: str) -> tuple[bool, str | None, int]:
    """(applied, rejection reason, attempts) for one sub-change."""
    reason, attempts = None, 0
    for delay in NETCONF_RETRY_DELAYS:
        if delay:
            _sleep(delay)
        attempts += 1
        result = send_edit_config(adaptor_uri, change["managedElementRef"], attribute_changes, message_id=message_id,
                                  operation=operation, managed_function_ref=change.get("managedFunctionRef"))
        if result:
            return True, None, attempts
        reason = getattr(result, "reason", None) or "NETCONF_RPC_FAILED"
        if not getattr(result, "retryable", False):
            break
    return False, reason, attempts


def _raise_dispatch_alarm(db: Session, job_id: uuid.UUID, change: dict, reason: str, attempts: int) -> None:
    target = change.get("managedFunctionRef") or change["managedElementRef"]
    db.add(Alarm(source_alarm_id=f"o1-config:{job_id}:{target}", managed_element_ref=change["managedElementRef"],
                 managed_function_ref=change.get("managedFunctionRef"), severity="major",
                 alarm_type="COMMUNICATIONS_ALARM", probable_cause=reason,
                 specific_problem=f"edit-config to {target} failed after {attempts} attempts"))


class WriteConfigRequest(BaseModel):
    requestedBy: str
    scope: str
    changes: list[dict]  # each: {managedElementRef, managedFunctionRef?, attributeChanges?, operation?}
    msacRole: str | None = None


class RegisterO1AdaptorEndpointRequest(BaseModel):
    managedElementRef: str
    adaptorUri: str
    protocolSupport: list[str]
    o1Protocol: str
    entityType: str
    managedFunctionRef: str | None = None
    vendorName: str | None = None
    # Wave 9 (W9-01): the MnS services this adaptor implements; omitted =
    # its vendor's declared capability (vendors.py)
    supportedServices: list[MnsService] | None = None


@app.post("/o1-adaptor-endpoints", status_code=201)
def register_o1_adaptor_endpoint(body: RegisterO1AdaptorEndpointRequest, db: Session = Depends(get_session)):
    """RAN NF OAM LLD section 1's own design intent (Option A,
    `docs/call-flows/03-config-write-with-schema-check.md`: "per ME's O1
    Adaptor registers itself into the MnS Registry NRM") had no concrete
    self-registration route anywhere in this build — the entire
    `O1AdaptorEndpoint`/`ManagedEntity` registry could previously only
    ever be populated by a test fixture reaching directly into the DB,
    never by any real caller; even `endpoint_heartbeat` below implicitly
    assumed the row it pings already existed. `docker-compose.yml`'s own
    comment on `mock-o1-adaptor` names this precisely: a real ME's
    `adaptor_uri` "would point at http://mock-o1-adaptor:8000/edit-config
    once one is ever registered against this service" — until now, none
    ever was.

    Real MnS Registry NRM polling stays out of scope (no such registry
    exists in this build, HISTORY.md's confirmed elision) — this is
    the same honest, lighter self-registration-POST substitute already
    used everywhere else in this build (DME's producer registration,
    SME's provider/invoker registration): the O1 Adaptor itself POSTs
    its own existence here instead of a registry polling it.
    `health_status` starts at `DISCOVERED`, the FSM's own real starting
    state (`statemachine.py`'s `ENDPOINT_HEALTH_FSM`) — not the model's
    column default `ACTIVE` (chosen for other callers' test
    convenience) — since a fresh registration hasn't heartbeated yet.
    """
    # Wave 9 (W9-04): a registered vendor's endpoint must use a transport
    # (vendor mode) the vendor declared, and can't claim services it lacks.
    check_vendor_mode(db, body.vendorName, body.o1Protocol)
    cap = db.get(VendorCapability, body.vendorName) if body.vendorName else None
    if cap is not None and body.supportedServices is not None and not set(body.supportedServices) <= set(cap.supported_services):
        raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED,
                              detail=f"supportedServices {body.supportedServices} exceed vendor {body.vendorName!r}'s {cap.supported_services}")
    endpoint = O1AdaptorEndpoint(managed_element_ref=body.managedElementRef, adaptor_uri=body.adaptorUri,
                                  protocol_support=body.protocolSupport, health_status=EndpointHealth.DISCOVERED.value,
                                  supported_services=body.supportedServices)
    db.add(endpoint)
    db.flush()
    me = ManagedEntity(managed_element_ref=body.managedElementRef, managed_function_ref=body.managedFunctionRef,
                        entity_type=body.entityType, vendor_name=body.vendorName, o1_protocol=body.o1Protocol,
                        o1_adaptor_endpoint_id=endpoint.endpoint_id)
    db.add(me)
    db.commit()
    return {"endpointId": str(endpoint.endpoint_id), "managedElementRef": me.managed_element_ref, "healthStatus": endpoint.health_status}


@app.post("/config-jobs", status_code=202)
def write_configuration_changes(body: WriteConfigRequest, db: Session = Depends(get_session)):
    """WriteConfigurationChanges — RAN NF OAM LLD section 5.1's full
    sequence: MSAC gate, schema check (cache-or-fetch), decompose into
    sub_changes, PATCH each independently, aggregate.
    """
    if body.scope == "entire-RAN" and not body.msacRole:
        raise framework_error(FrameworkError.MSAC_ACCESS_DENIED, detail="entire-RAN scope requires an MSAC access tier")
    # Wave 9 (W9-02): the pre-check is real now — every change's ME must
    # implement Provisioning, and its class/attributes/values must exist in
    # the data model its vendor's conformance mode selects. Nothing is
    # dispatched (or recorded) if any change fails.
    problems = []
    for change in body.changes:
        require_service(db, change["managedElementRef"], "PROV")
        problems.extend(schema_problems(db, change))
    if problems:
        raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED, detail="; ".join(problems))

    job = WriteConfigJob(requested_by=body.requestedBy, scope=body.scope, msac_role=body.msacRole)
    db.add(job)
    db.flush()

    # schema check — cache hit or fetch via Configuration Schema Info (clause 8.3)
    job.schema_validated_at = datetime.datetime.now(datetime.UTC)
    job.status = WRITE_CONFIG_JOB_FSM.fire(JobState.PENDING, JobEvent.PRECHECK_PASS)
    db.flush()

    for change in body.changes:
        # HISTORY.md §7 item 3: `operation` is RFC 6241 section 7.2's real
        # edit-config attribute — a delete/remove legitimately carries no
        # attributeChanges at all, so this no longer assumes the key is
        # always present the way a merge-only model could.
        attribute_changes = change.get("attributeChanges", {})
        operation = change.get("operation", "merge")
        me = db.get(ManagedEntity, change["managedElementRef"])
        if me is None or me.o1_adaptor_endpoint_id is None:
            db.add(WriteConfigSubChange(job_id=job.job_id, managed_element_ref=change["managedElementRef"],
                                         managed_function_ref=change.get("managedFunctionRef"),
                                         attribute_changes=attribute_changes, operation=operation, status="REJECTED",
                                         rejection_reason="ENDPOINT_UNREACHABLE"))
            continue
        endpoint = db.get(O1AdaptorEndpoint, me.o1_adaptor_endpoint_id)
        # Live-computed staleness at the point health is actually consulted —
        # the same "no scheduler exists anywhere in this build" pattern as
        # DME's producer health and A1 Related's service supervision sweep —
        # rather than depending on something having already called
        # POST /o1-adaptor-endpoints/discover first.
        _age_endpoint_health(endpoint, datetime.datetime.now(datetime.UTC))
        if endpoint.health_status in ("UNREACHABLE", "DEGRADED"):
            db.add(WriteConfigSubChange(job_id=job.job_id, managed_element_ref=change["managedElementRef"],
                                         managed_function_ref=change.get("managedFunctionRef"),
                                         attribute_changes=attribute_changes, operation=operation, status="REJECTED",
                                         rejection_reason="ENDPOINT_UNREACHABLE"))
            continue
        if me.o1_protocol != "NETCONF":
            # Confirmed protocol choice (HISTORY.md) is NETCONF — an ME
            # provisioned for RESTCONF has no dispatch implementation yet,
            # rejected honestly rather than silently treated as applied.
            db.add(WriteConfigSubChange(job_id=job.job_id, managed_element_ref=change["managedElementRef"],
                                         managed_function_ref=change.get("managedFunctionRef"),
                                         attribute_changes=attribute_changes, operation=operation, status="REJECTED",
                                         rejection_reason="PROTOCOL_NOT_SUPPORTED"))
            continue
        applied, reason, attempts = _dispatch_with_retries(endpoint.adaptor_uri, change, attribute_changes,
                                                           str(job.job_id), operation)
        if not applied and attempts > 1:
            _raise_dispatch_alarm(db, job.job_id, change, reason, attempts)
        db.add(WriteConfigSubChange(job_id=job.job_id, managed_element_ref=change["managedElementRef"],
                                     managed_function_ref=change.get("managedFunctionRef"),
                                     attribute_changes=attribute_changes, operation=operation,
                                     status="APPLIED" if applied else "REJECTED",
                                     rejection_reason=reason, attempts=attempts))

    db.flush()
    statuses = [sc.status for sc in db.scalars(select(WriteConfigSubChange).where(WriteConfigSubChange.job_id == job.job_id)).all()]
    job.status = WRITE_CONFIG_JOB_FSM.fire(JobState.PROCESSING, aggregate_event(statuses))
    db.commit()
    return {"jobId": str(job.job_id), "status": job.status}


@app.get("/managed-entities/{managed_element_ref}/config")
def read_configuration(managed_element_ref: str, managed_function_ref: str | None = None, db: Session = Depends(get_session)):
    """Wave 10.1 (W10-20): read-after-write. Reads the managed object's
    running configuration from its O1 adaptor (NETCONF <get-config>) — the
    live value on the NF, not what this module last asked for — so a
    caller can verify that a write actually took effect."""
    require_service(db, managed_element_ref, "PROV")
    me = db.get(ManagedEntity, managed_element_ref)
    endpoint = db.get(O1AdaptorEndpoint, me.o1_adaptor_endpoint_id) if me and me.o1_adaptor_endpoint_id else None
    if endpoint is None:
        raise framework_error(FrameworkError.ENDPOINT_UNREACHABLE, detail=f"{managed_element_ref} has no registered O1 adaptor")
    attributes = send_get_config(endpoint.adaptor_uri, managed_element_ref, message_id=str(uuid.uuid4()),
                                 managed_function_ref=managed_function_ref)
    if attributes is None:
        raise framework_error(FrameworkError.ENDPOINT_UNREACHABLE, detail=f"get-config on {managed_element_ref} failed")
    return {"managedElementRef": managed_element_ref, "managedFunctionRef": managed_function_ref, "attributes": attributes}


@app.get("/config-jobs/{job_id}")
def query_write_config_job_status(job_id: uuid.UUID, db: Session = Depends(get_session)):
    job = db.get(WriteConfigJob, job_id)
    sub_changes = db.scalars(select(WriteConfigSubChange).where(WriteConfigSubChange.job_id == job_id)).all()
    return {"jobId": str(job.job_id), "status": job.status,
            "subChanges": [{"managedElementRef": sc.managed_element_ref, "managedFunctionRef": sc.managed_function_ref,
                            "operation": sc.operation, "status": sc.status, "rejectionReason": sc.rejection_reason,
                            "attempts": sc.attempts} for sc in sub_changes]}


@app.get("/alarms")
def query_alarms(managed_element_ref: str | None = None, severity: str | None = None, limit: int = PageLimit,
                  offset: int = PageOffset, db: Session = Depends(get_session)):
    """`severity` filter (GUI pass) — the alarm console filters by ME and
    by perceivedSeverity; `severity=cleared` isolates the cleared history.
    """
    stmt = select(Alarm)
    if managed_element_ref:
        stmt = stmt.where(Alarm.managed_element_ref == managed_element_ref)
    if severity:
        stmt = stmt.where(Alarm.severity == severity)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [_alarm_view(a) for a in page["items"]]}


@app.post("/alarms/ingest")
def ingest_alarm(source_alarm_id: str, managed_element_ref: str, severity: str, correlation_group: str | None = None,
                  probable_cause: str | None = None, specific_problem: str | None = None, root_cause_indicator: bool = False,
                  correlated_notifications: list[uuid.UUID] = Query(default=[]), proposed_repair_actions: str | None = None,
                  alarm_type: str | None = None, db: Session = Depends(get_session)):
    """alarmId is ALWAYS a fresh UUID minted here, never the raising ME's
    native ID — RAN NF OAM LLD section 3.3, closing R1UCR's own flagged,
    unresolved collision risk under a fleet of N MEs.

    probableCause/specificProblem/rootCauseIndicator/correlatedNotifications/
    proposedRepairActions (HISTORY.md §5): the standard fault
    fields 3GPP TS 28.532 FaultMnS's NotifyNewAlarm carries, previously
    entirely absent from this alarm model. alarmType (HISTORY.md §7,
    TS28111_FaultNrm.yaml's AlarmRecord) was the one of these fields
    still missing after that pass.
    """
    require_service(db, managed_element_ref, "FM")  # Wave 9 (W9-01)
    alarm = Alarm(source_alarm_id=source_alarm_id, managed_element_ref=managed_element_ref, severity=severity, correlation_group=correlation_group,
                  probable_cause=probable_cause, specific_problem=specific_problem, root_cause_indicator=root_cause_indicator,
                  correlated_notifications=correlated_notifications or [], proposed_repair_actions=proposed_repair_actions,
                  alarm_type=alarm_type)
    db.add(alarm)
    db.commit()
    return {"alarmId": str(alarm.alarm_id)}


@app.patch("/alarms/{alarm_id}/ack")
def change_alarm_ack_state(alarm_id: uuid.UUID, new_state: str, ack_user_id: str | None = None, db: Session = Depends(get_session)):
    """ackUserId (HISTORY.md §7, TS28111_FaultNrm.yaml's AlarmRecord) —
    who acknowledged it, never recorded before. alarmChangedTime (the
    spec's own "last mutated" timestamp, distinct from raised_at/
    cleared_at) updates here and in clear_alarm below, the two places
    this build actually mutates an existing alarm.
    """
    alarm = db.get(Alarm, alarm_id)
    alarm.ack_state = new_state
    alarm.ack_user_id = ack_user_id
    alarm.changed_at = datetime.datetime.now(datetime.UTC)
    db.commit()
    return _alarm_view(alarm)


@app.patch("/alarms/{alarm_id}/clear")
def clear_alarm(alarm_id: uuid.UUID, clear_user_id: str | None = None, db: Session = Depends(get_session)):
    """HISTORY.md §5: no alarm-cleared lifecycle existed at
    all — `/alarms/{id}/ack` only ever toggled ack_state, so an alarm
    that stopped recurring on the NF had no way to ever be marked
    resolved. Matches the reference's own NotifyClearedAlarm shape:
    setting severity to 'cleared' (already a valid value in this
    build's own CHECK constraint) rather than a separate state field.
    """
    alarm = db.get(Alarm, alarm_id)
    alarm.severity = "cleared"
    alarm.cleared_at = datetime.datetime.now(datetime.UTC)
    alarm.clear_user_id = clear_user_id
    alarm.changed_at = alarm.cleared_at
    db.commit()
    return _alarm_view(alarm)


@app.post("/pm-subscriptions")
def subscribe_pm(managed_element_ref: str, counter_type: str, delivery_method: str, granularity_period: int | None = None,
                  db: Session = Depends(get_session)):
    """SubscribePM — RAN NF OAM LLD section 3.5: this is a DME-producer
    registration wrapper, NOT a clause-8 API call. No R1AP endpoint exists
    for PM at all; that's the spec's own documented design intent.

    granularityPeriod (HISTORY.md §7 item 4, TS28550_PerfMeasJobCtrlMnS.yaml's
    measJobCreation-RequestType) — the one real job-control field worth
    carrying despite the wrapper scope cut; everything else on that
    schema (schedule/priority/multi-instance/reportingPeriod) stays out.
    """
    require_service(db, managed_element_ref, "PM")  # Wave 9 (W9-01)
    engine = {"pull": "ProvMnS", "push": "PMJobControl", "stream": "StreamingDataReporting"}.get(delivery_method, "FileDataReporting")
    sub = PMSubscription(managed_element_ref=managed_element_ref, counter_type=counter_type, delivery_method=delivery_method,
                          southbound_engine=engine, granularity_period=granularity_period)
    db.add(sub)
    db.commit()

    r1 = R1Client()
    r1.post("/dme/production-capabilities", json={
        "namespace": "RAN", "name": f"PMCounters.{counter_type}", "version": "1.0.0",
        "typeName": f"RAN.PMCounters.{counter_type}", "producerId": "ran-nf-oam",
        "dataProductionSchema": {}, "producerHealthCallbackUrl": "http://ran-nf-oam:8000/health",
        "jobCallbackUrl": "http://ran-nf-oam:8000/dme-jobs",
    })
    return {"subscriptionId": str(sub.subscription_id), "southboundEngine": engine, "granularityPeriod": sub.granularity_period}


class PmMeasurement(BaseModel):
    cellId: str
    timestamp: datetime.datetime
    value: float | None = None
    # Wave 10.2 (W10.2-03): a measurement can carry several counters of one
    # family at once (a PM file's measInfo with several measTypes, e.g. the
    # handover counters MM.HoExeAtt / MM.HoFailTooLate / ...), and can be
    # per neighbour relation rather than per cell.
    values: dict[str, float] | None = None
    relation: str | None = None  # the neighbour relation (e.g. "201-202") the counters are measured on

    @model_validator(mode="after")
    def _has_a_value(self):
        if self.value is None and not self.values:
            raise ValueError("a measurement needs value or values")
        return self


class PmReportRequest(BaseModel):
    managedElementRef: str
    counterType: str
    measurements: list[PmMeasurement]


@app.post("/pm-reports", status_code=201)
def receive_pm_report(body: PmReportRequest, db: Session = Depends(get_session)):
    """Wave 10.1 (W10-04): the PM data path O1 PM → RAN NF OAM → DME. An
    NF's PM report (here a simplified JSON shape of a measurement file or
    stream) for a counter RAN NF OAM has a PM subscription on is delivered
    as DME records — one per cell and sample — to every data job open on
    that counter's DME type (`RAN.PMCounters.<counterType>`, registered by
    SubscribePM). Consumers (e.g. the EnergySaving rApp reading
    PRB_UTILIZATION) only ever see DME, never this module."""
    require_service(db, body.managedElementRef, "PM")
    subscribed = db.scalars(select(PMSubscription).where(PMSubscription.managed_element_ref == body.managedElementRef,
                                                         PMSubscription.counter_type == body.counterType)).first()
    if subscribed is None:
        raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED,
                              detail=f"no PM subscription for {body.counterType} on {body.managedElementRef}")
    db.commit()  # end the read transaction before fanning out to DME (nothing of ours is written)
    r1 = R1Client()
    type_name = f"RAN.PMCounters.{body.counterType}"
    dme_type = next((t for t in r1.get("/dme/dme-types", params={"data_category": "RAN"}).json()
                     if t["typeName"] == type_name), None)
    jobs = r1.get("/dme/data-jobs", params={"dme_type_id": dme_type["dmeTypeId"], "limit": 500}).json()["items"] if dme_type else []
    delivered = 0
    for m in body.measurements:
        payload = {"managedElementRef": body.managedElementRef, "cellId": m.cellId, "counter": body.counterType,
                   "value": m.value, "timestamp": m.timestamp.isoformat()}
        if m.values is not None:
            payload["values"] = m.values
        if m.relation is not None:
            payload["relation"] = m.relation
        for job in jobs:
            r1.post(f"/dme/data-jobs/{job['dataJobId']}/records", json={"payload": payload})
            delivered += 1
    return {"managedElementRef": body.managedElementRef, "counterType": body.counterType,
            "measurements": len(body.measurements), "dataJobs": len(jobs), "recordsDelivered": delivered}


@app.get("/health")
def health_check():
    """Producer health-supervision callback (HISTORY.md §5):
    subscribe_pm registers this exact URL with DME as its
    producerHealthCallbackUrl, but no route ever answered it — a health
    poller hitting the registered callback would 404 against a producer
    this module itself just told DME was healthy. A plain liveness
    check: reachable and 200 means this RAN NF OAM instance is up.
    """
    return {"status": "healthy"}


@app.post("/dme-jobs")
def receive_dme_job(body: dict):
    """DME's own job-push callback (HISTORY.md §5): DME's
    create_data_job now actually POSTs the job to jobCallbackUrl on
    create — subscribe_pm registers this exact URL, so this closes the
    same class of dangling-callback bug the /health route closed for
    the health-supervision URL. Phase 1: acks only, no real per-job
    state tracked producer-side.
    """
    return {"status": "accepted"}


@app.delete("/dme-jobs/{data_job_id}", status_code=204)
def stop_dme_job(data_job_id: str):
    pass


@app.post("/software-management-jobs", status_code=202)
def software_update(managed_element_ref: str, ru_instance_id: str | None = None, db: Session = Depends(get_session)):
    require_service(db, managed_element_ref, "SWM")  # Wave 9 (W9-01)
    job = SoftwareManagementJob(managed_element_ref=managed_element_ref, ru_instance_id=ru_instance_id, status="PENDING", phase="DOWNLOAD")
    db.add(job)
    db.flush()
    job.status = SOFTWARE_MANAGEMENT_FSM.fire(SwmState.PENDING, SwmEvent.START)
    db.commit()
    return {"jobId": str(job.job_id), "status": job.status, "phase": job.phase}


@app.post("/software-management-jobs/{job_id}/advance")
def advance_software_job(job_id: uuid.UUID, succeeded: bool, db: Session = Depends(get_session)):
    job = db.get(SoftwareManagementJob, job_id)
    event = {"DOWNLOAD": SwmEvent.DOWNLOAD_OK, "INSTALL": SwmEvent.INSTALL_OK, "ACTIVATE": SwmEvent.ACTIVATE_OK}[job.phase]
    if not succeeded:
        job.status = SOFTWARE_MANAGEMENT_FSM.fire(SwmState(job.status), SwmEvent.PHASE_FAILED)
    else:
        job.status = SOFTWARE_MANAGEMENT_FSM.fire(SwmState(job.status), event)
        if event in PHASE_ORDER:
            job.phase = PHASE_ORDER[event]
    db.commit()
    return {"jobId": str(job.job_id), "status": job.status, "phase": job.phase}


def _age_endpoint_health(ep: O1AdaptorEndpoint, now: datetime.datetime) -> None:
    """Ages a single endpoint's health status in place if it has missed its
    heartbeat window. Shared by the bulk `/discover` sweep below and by
    write_configuration_changes's own gate, so staleness is caught the
    moment it's actually consulted, not only when something has separately
    polled `/discover` first — no scheduler exists anywhere in this build
    (same elision as DME's producer health / A1 Related's service
    supervision), so a live-computed check at the point of use is this
    build's substitute for a periodic sweep.
    """
    if ep.health_status == "ACTIVE" and ep.last_heartbeat_at and now - as_utc(ep.last_heartbeat_at) > MISSED_HEARTBEAT_THRESHOLD:
        ep.health_status = ENDPOINT_HEALTH_FSM.fire(EndpointHealth.ACTIVE, EndpointEvent.MISSED_HEARTBEATS)


@app.post("/o1-adaptor-endpoints/discover")
def discover_endpoints(db: Session = Depends(get_session)):
    """RAN NF OAM LLD section 1.2/5.2 — the endpoint discovery loop, meant
    to run on a timer against the MnS Registry NRM. Phase 1: still a
    heartbeat-aging stub rather than real registry polling — there is no
    real MnS Registry NRM in this build to poll — but staleness is no
    longer only visible through this route: write_configuration_changes's
    own gate now ages an endpoint live at dispatch time too (see
    `_age_endpoint_health`), so a stale endpoint can't silently pass a
    write attempt just because nothing called this route first. This route
    stays as the bulk equivalent of a registry poll — check every endpoint
    at once, e.g. from an operator dashboard or an external timer.
    """
    endpoints = db.scalars(select(O1AdaptorEndpoint)).all()
    now = datetime.datetime.now(datetime.UTC)
    for ep in endpoints:
        _age_endpoint_health(ep, now)
    db.commit()
    return {"checked": len(endpoints)}


@app.post("/o1-adaptor-endpoints/{endpoint_id}/heartbeat")
def endpoint_heartbeat(endpoint_id: uuid.UUID, db: Session = Depends(get_session)):
    ep = db.get(O1AdaptorEndpoint, endpoint_id)
    ep.last_heartbeat_at = datetime.datetime.now(datetime.UTC)
    current = EndpointHealth(ep.health_status) if ep.health_status in EndpointHealth.__members__.values() else EndpointHealth.DISCOVERED
    if current in (EndpointHealth.DISCOVERED, EndpointHealth.DEGRADED):
        ep.health_status = ENDPOINT_HEALTH_FSM.fire(current, EndpointEvent.HEARTBEAT)
    db.commit()
    return {"endpointId": str(ep.endpoint_id), "healthStatus": ep.health_status}


def _alarm_view(a: Alarm) -> dict:
    return {"alarmId": str(a.alarm_id), "sourceAlarmId": a.source_alarm_id, "managedElementRef": a.managed_element_ref,
            "severity": a.severity, "ackState": a.ack_state,
            "raisedAt": a.raised_at.isoformat() if a.raised_at else None, "correlationGroup": a.correlation_group,
            "probableCause": a.probable_cause, "specificProblem": a.specific_problem,
            "rootCauseIndicator": a.root_cause_indicator,
            "correlatedNotifications": [str(c) for c in a.correlated_notifications],
            "proposedRepairActions": a.proposed_repair_actions, "alarmType": a.alarm_type,
            "ackUserId": a.ack_user_id, "changedAt": a.changed_at.isoformat() if a.changed_at else None,
            "clearedAt": a.cleared_at.isoformat() if a.cleared_at else None, "clearUserId": a.clear_user_id}


# ---------------------------------------------------------------- list reads (GUI pass)
# PM subscriptions, O1 adaptor endpoints, CM write jobs and software jobs were
# all write-only (or read-by-id only): an operator had no way to see what was
# registered without already holding every id.

@app.get("/pm-subscriptions")
def list_pm_subscriptions(managed_element_ref: str | None = None, limit: int = PageLimit, offset: int = PageOffset,
                           db: Session = Depends(get_session)):
    stmt = select(PMSubscription)
    if managed_element_ref:
        stmt = stmt.where(PMSubscription.managed_element_ref == managed_element_ref)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [{"subscriptionId": str(s.subscription_id), "managedElementRef": s.managed_element_ref,
             "counterType": s.counter_type, "deliveryMethod": s.delivery_method,
             "southboundEngine": s.southbound_engine, "granularityPeriod": s.granularity_period}
            for s in page["items"]]}


@app.delete("/pm-subscriptions/{subscription_id}", status_code=204)
def unsubscribe_pm(subscription_id: uuid.UUID, db: Session = Depends(get_session)):
    """`docs/call-flows/20-alarm-pm-subscription-lifecycle.md`'s own
    gap, closed: every other subscription-shaped resource in this build
    (DME's type subscriptions, MDAF's, A1 Related's EI jobs, Intent
    Service's RMIH registration, MLMF's) has a real unsubscribe route —
    `PMSubscription` could previously only be created and listed, never
    torn down through this build's own API. Idempotent, matching all of
    those.
    """
    sub = db.get(PMSubscription, subscription_id)
    if sub is not None:
        db.delete(sub)
        db.commit()


@app.post("/fm-subscriptions")
def subscribe_fm(managed_element_ref: str, delivery_method: str, db: Session = Depends(get_session)):
    """SubscribeFM — HISTORY.md OI-6.7, closed: mirrors
    subscribe_pm's own DME-producer registration wrapper shape exactly
    (RAN NF OAM LLD section 3.5's SubscribePM pattern), for alarms
    instead of PM counters. Unlike PM, there is no per-counter-type
    identity — every ME's fault records register under one shared
    `RAN.FaultRecords` DME type, joined many-to-many across every
    managed element that subscribes (the same DMEType join behavior
    call flow 11 walks for any other multi-producer type). Gives an
    rApp/AI-ML model DME-mediated visibility into outstanding-active/
    historical alarms — it does NOT give DME or a consuming rApp any way
    to clear an alarm; that stays RAN NF OAM's own
    PATCH /alarms/{alarm_id}/clear, called by the source NF or an
    operator, unaffected by whether FM is DME-registered.
    """
    require_service(db, managed_element_ref, "FM")  # Wave 9 (W9-01)
    engine = {"pull": "FaultMnS", "push": "FaultMnS", "stream": "StreamingDataReporting"}.get(delivery_method, "FaultMnS")
    sub = FMSubscription(managed_element_ref=managed_element_ref, delivery_method=delivery_method, southbound_engine=engine)
    db.add(sub)
    db.commit()

    r1 = R1Client()
    r1.post("/dme/production-capabilities", json={
        "namespace": "RAN", "name": "FaultRecords", "version": "1.0.0",
        "typeName": "RAN.FaultRecords", "producerId": "ran-nf-oam",
        "dataProductionSchema": {}, "producerHealthCallbackUrl": "http://ran-nf-oam:8000/health",
        "jobCallbackUrl": "http://ran-nf-oam:8000/dme-jobs",
    })
    return {"subscriptionId": str(sub.subscription_id), "southboundEngine": engine}


@app.get("/fm-subscriptions")
def list_fm_subscriptions(managed_element_ref: str | None = None, limit: int = PageLimit, offset: int = PageOffset,
                           db: Session = Depends(get_session)):
    stmt = select(FMSubscription)
    if managed_element_ref:
        stmt = stmt.where(FMSubscription.managed_element_ref == managed_element_ref)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [{"subscriptionId": str(s.subscription_id), "managedElementRef": s.managed_element_ref,
             "deliveryMethod": s.delivery_method, "southboundEngine": s.southbound_engine}
            for s in page["items"]]}


@app.delete("/fm-subscriptions/{subscription_id}", status_code=204)
def unsubscribe_fm(subscription_id: uuid.UUID, db: Session = Depends(get_session)):
    """Idempotent, matching pm-subscriptions' own unsubscribe route and
    every other subscription-shaped resource in this build.
    """
    sub = db.get(FMSubscription, subscription_id)
    if sub is not None:
        db.delete(sub)
        db.commit()


@app.get("/o1-adaptor-endpoints")
def list_o1_adaptor_endpoints(health_status: str | None = None, limit: int = PageLimit, offset: int = PageOffset,
                               db: Session = Depends(get_session)):
    stmt = select(O1AdaptorEndpoint)
    if health_status:
        stmt = stmt.where(O1AdaptorEndpoint.health_status == health_status)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [{"endpointId": str(ep.endpoint_id), "managedElementRef": ep.managed_element_ref, "adaptorUri": ep.adaptor_uri,
             "protocolSupport": ep.protocol_support, "registeredVia": ep.registered_via, "healthStatus": ep.health_status,
             "lastHeartbeatAt": ep.last_heartbeat_at.isoformat() if ep.last_heartbeat_at else None,
             "supportedServices": ep.supported_services}
            for ep in page["items"]]}


@app.get("/config-jobs")
def list_write_config_jobs(status: str | None = None, limit: int = PageLimit, offset: int = PageOffset,
                            db: Session = Depends(get_session)):
    stmt = select(WriteConfigJob)
    if status:
        stmt = stmt.where(WriteConfigJob.status == status)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [{"jobId": str(j.job_id), "requestedBy": j.requested_by, "scope": j.scope, "status": j.status,
             "msacRole": j.msac_role} for j in page["items"]]}


@app.get("/software-management-jobs")
def list_software_management_jobs(managed_element_ref: str | None = None, limit: int = PageLimit, offset: int = PageOffset,
                                   db: Session = Depends(get_session)):
    stmt = select(SoftwareManagementJob)
    if managed_element_ref:
        stmt = stmt.where(SoftwareManagementJob.managed_element_ref == managed_element_ref)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [{"jobId": str(j.job_id), "managedElementRef": j.managed_element_ref, "ruInstanceId": j.ru_instance_id,
             "phase": j.phase, "status": j.status} for j in page["items"]]}


# Wave 9 — multi-vendor capability registry, CM schemas, cell guards (vendors.py)
app.include_router(vendors_router)
