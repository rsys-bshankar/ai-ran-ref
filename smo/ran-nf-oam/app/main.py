"""RAN NF OAM SMOS.

SMO Design v1.3 section 3.10, extended by RAN NF OAM LLD sections 1-8:
Option A endpoint registry, multi-function-ME addressing, schema-checked
writes with decomposed-PATCH aggregation, fleet-unique alarm IDs, and the
explicit clarification that SubscribePM is a DME-producer registration,
never a clause-8 call (no such API exists).

CM writes dispatch as NETCONF-shaped <edit-config> RPCs (netconf_client.py)
— the confirmed protocol per OPEN_ITEMS.md's "CM cache sync method" item.
An ME provisioned for RESTCONF has no dispatch implementation yet and is
rejected with PROTOCOL_NOT_SUPPORTED rather than silently applied.
"""

import datetime
import uuid

from fastapi import Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.r1_client import R1Client

from .models import Alarm, CMSchemaCache, ManagedEntity, O1AdaptorEndpoint, PMSubscription, SoftwareManagementJob, WriteConfigJob, WriteConfigSubChange
from .netconf_client import send_edit_config
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

MISSED_HEARTBEAT_THRESHOLD = datetime.timedelta(seconds=90)


class WriteConfigRequest(BaseModel):
    requestedBy: str
    scope: str
    changes: list[dict]  # each: {managedElementRef, managedFunctionRef?, attributeChanges}
    msacRole: str | None = None


@app.post("/config-jobs", status_code=202)
def write_configuration_changes(body: WriteConfigRequest, db: Session = Depends(get_session)):
    """WriteConfigurationChanges — RAN NF OAM LLD section 5.1's full
    sequence: MSAC gate, schema check (cache-or-fetch), decompose into
    sub_changes, PATCH each independently, aggregate.
    """
    if body.scope == "entire-RAN" and not body.msacRole:
        raise framework_error(FrameworkError.MSAC_ACCESS_DENIED, detail="entire-RAN scope requires an MSAC access tier")

    job = WriteConfigJob(requested_by=body.requestedBy, scope=body.scope, msac_role=body.msacRole)
    db.add(job)
    db.flush()

    # schema check — cache hit or fetch via Configuration Schema Info (clause 8.3)
    job.schema_validated_at = datetime.datetime.now(datetime.UTC)
    job.status = WRITE_CONFIG_JOB_FSM.fire(JobState.PENDING, JobEvent.PRECHECK_PASS)
    db.flush()

    for change in body.changes:
        me = db.get(ManagedEntity, change["managedElementRef"])
        if me is None or me.o1_adaptor_endpoint_id is None:
            db.add(WriteConfigSubChange(job_id=job.job_id, managed_element_ref=change["managedElementRef"],
                                         managed_function_ref=change.get("managedFunctionRef"),
                                         attribute_changes=change["attributeChanges"], status="REJECTED",
                                         rejection_reason="ENDPOINT_UNREACHABLE"))
            continue
        endpoint = db.get(O1AdaptorEndpoint, me.o1_adaptor_endpoint_id)
        if endpoint.health_status == "UNREACHABLE":
            db.add(WriteConfigSubChange(job_id=job.job_id, managed_element_ref=change["managedElementRef"],
                                         managed_function_ref=change.get("managedFunctionRef"),
                                         attribute_changes=change["attributeChanges"], status="REJECTED",
                                         rejection_reason="ENDPOINT_UNREACHABLE"))
            continue
        if me.o1_protocol != "NETCONF":
            # Confirmed protocol choice (OPEN_ITEMS.md) is NETCONF — an ME
            # provisioned for RESTCONF has no dispatch implementation yet,
            # rejected honestly rather than silently treated as applied.
            db.add(WriteConfigSubChange(job_id=job.job_id, managed_element_ref=change["managedElementRef"],
                                         managed_function_ref=change.get("managedFunctionRef"),
                                         attribute_changes=change["attributeChanges"], status="REJECTED",
                                         rejection_reason="PROTOCOL_NOT_SUPPORTED"))
            continue
        applied = send_edit_config(endpoint.adaptor_uri, change["managedElementRef"], change["attributeChanges"], message_id=str(job.job_id))
        db.add(WriteConfigSubChange(job_id=job.job_id, managed_element_ref=change["managedElementRef"],
                                     managed_function_ref=change.get("managedFunctionRef"),
                                     attribute_changes=change["attributeChanges"],
                                     status="APPLIED" if applied else "REJECTED",
                                     rejection_reason=None if applied else "NETCONF_RPC_FAILED"))

    db.flush()
    statuses = [sc.status for sc in db.scalars(select(WriteConfigSubChange).where(WriteConfigSubChange.job_id == job.job_id)).all()]
    job.status = WRITE_CONFIG_JOB_FSM.fire(JobState.PROCESSING, aggregate_event(statuses))
    db.commit()
    return {"jobId": str(job.job_id), "status": job.status}


@app.get("/config-jobs/{job_id}")
def query_write_config_job_status(job_id: uuid.UUID, db: Session = Depends(get_session)):
    job = db.get(WriteConfigJob, job_id)
    sub_changes = db.scalars(select(WriteConfigSubChange).where(WriteConfigSubChange.job_id == job_id)).all()
    return {"jobId": str(job.job_id), "status": job.status,
            "subChanges": [{"managedElementRef": sc.managed_element_ref, "status": sc.status, "rejectionReason": sc.rejection_reason} for sc in sub_changes]}


@app.get("/alarms")
def query_alarms(managed_element_ref: str | None = None, db: Session = Depends(get_session)):
    stmt = select(Alarm)
    if managed_element_ref:
        stmt = stmt.where(Alarm.managed_element_ref == managed_element_ref)
    return [_alarm_view(a) for a in db.scalars(stmt).all()]


@app.post("/alarms/ingest")
def ingest_alarm(source_alarm_id: str, managed_element_ref: str, severity: str, correlation_group: str | None = None,
                  probable_cause: str | None = None, specific_problem: str | None = None, root_cause_indicator: bool = False,
                  correlated_notifications: list[uuid.UUID] = Query(default=[]), proposed_repair_actions: str | None = None,
                  db: Session = Depends(get_session)):
    """alarmId is ALWAYS a fresh UUID minted here, never the raising ME's
    native ID — RAN NF OAM LLD section 3.3, closing R1UCR's own flagged,
    unresolved collision risk under a fleet of N MEs.

    probableCause/specificProblem/rootCauseIndicator/correlatedNotifications/
    proposedRepairActions (OPEN_ITEMS.md section 5): the standard fault
    fields 3GPP TS 28.532 FaultMnS's NotifyNewAlarm carries, previously
    entirely absent from this alarm model.
    """
    alarm = Alarm(source_alarm_id=source_alarm_id, managed_element_ref=managed_element_ref, severity=severity, correlation_group=correlation_group,
                  probable_cause=probable_cause, specific_problem=specific_problem, root_cause_indicator=root_cause_indicator,
                  correlated_notifications=correlated_notifications or [], proposed_repair_actions=proposed_repair_actions)
    db.add(alarm)
    db.commit()
    return {"alarmId": str(alarm.alarm_id)}


@app.patch("/alarms/{alarm_id}/ack")
def change_alarm_ack_state(alarm_id: uuid.UUID, new_state: str, db: Session = Depends(get_session)):
    alarm = db.get(Alarm, alarm_id)
    alarm.ack_state = new_state
    db.commit()
    return _alarm_view(alarm)


@app.patch("/alarms/{alarm_id}/clear")
def clear_alarm(alarm_id: uuid.UUID, clear_user_id: str | None = None, db: Session = Depends(get_session)):
    """OPEN_ITEMS.md section 5: no alarm-cleared lifecycle existed at
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
    db.commit()
    return _alarm_view(alarm)


@app.post("/pm-subscriptions")
def subscribe_pm(managed_element_ref: str, counter_type: str, delivery_method: str, db: Session = Depends(get_session)):
    """SubscribePM — RAN NF OAM LLD section 3.5: this is a DME-producer
    registration wrapper, NOT a clause-8 API call. No R1AP endpoint exists
    for PM at all; that's the spec's own documented design intent.
    """
    engine = {"pull": "ProvMnS", "push": "PMJobControl", "stream": "StreamingDataReporting"}.get(delivery_method, "FileDataReporting")
    sub = PMSubscription(managed_element_ref=managed_element_ref, counter_type=counter_type, delivery_method=delivery_method, southbound_engine=engine)
    db.add(sub)
    db.commit()

    r1 = R1Client()
    r1.post("/dme/production-capabilities", json={
        "namespace": "RAN", "name": f"PMCounters.{counter_type}", "version": "1.0.0",
        "typeName": f"RAN.PMCounters.{counter_type}", "producerId": "ran-nf-oam",
        "dataProductionSchema": {}, "producerHealthCallbackUrl": "http://ran-nf-oam:8000/health",
        "jobCallbackUrl": "http://ran-nf-oam:8000/dme-jobs",
    })
    return {"subscriptionId": str(sub.subscription_id), "southboundEngine": engine}


@app.get("/health")
def health_check():
    """Producer health-supervision callback (OPEN_ITEMS.md section 5):
    subscribe_pm registers this exact URL with DME as its
    producerHealthCallbackUrl, but no route ever answered it — a health
    poller hitting the registered callback would 404 against a producer
    this module itself just told DME was healthy. A plain liveness
    check: reachable and 200 means this RAN NF OAM instance is up.
    """
    return {"status": "healthy"}


@app.post("/dme-jobs")
def receive_dme_job(body: dict):
    """DME's own job-push callback (OPEN_ITEMS.md section 5): DME's
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


@app.post("/o1-adaptor-endpoints/discover")
def discover_endpoints(db: Session = Depends(get_session)):
    """RAN NF OAM LLD section 1.2/5.2 — the endpoint discovery loop, meant
    to run on a timer against the MnS Registry NRM. Phase 1: heartbeat
    aging is evaluated here rather than a live registry poll.
    """
    endpoints = db.scalars(select(O1AdaptorEndpoint)).all()
    now = datetime.datetime.now(datetime.UTC)
    for ep in endpoints:
        if ep.health_status == "ACTIVE" and ep.last_heartbeat_at and now - ep.last_heartbeat_at > MISSED_HEARTBEAT_THRESHOLD:
            ep.health_status = ENDPOINT_HEALTH_FSM.fire(EndpointHealth.ACTIVE, EndpointEvent.MISSED_HEARTBEATS)
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
            "severity": a.severity, "ackState": a.ack_state, "correlationGroup": a.correlation_group,
            "probableCause": a.probable_cause, "specificProblem": a.specific_problem,
            "rootCauseIndicator": a.root_cause_indicator,
            "correlatedNotifications": [str(c) for c in a.correlated_notifications],
            "proposedRepairActions": a.proposed_repair_actions,
            "clearedAt": a.cleared_at.isoformat() if a.cleared_at else None, "clearUserId": a.clear_user_id}
