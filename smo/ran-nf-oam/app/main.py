"""RAN NF OAM SMOS.

SMO Design v1.3 section 3.10, extended by RAN NF OAM LLD sections 1-8:
Option A endpoint registry, multi-function-ME addressing, schema-checked
writes with decomposed-PATCH aggregation, fleet-unique alarm IDs, and the
explicit clarification that SubscribePM is a DME-producer registration,
never a clause-8 call (no such API exists).

CM writes dispatch over the ME's provisioned O1 protocol: NETCONF-shaped
<edit-config> RPCs (netconf_client.py, HISTORY.md's "CM cache sync method"
item) or RFC 8040 RESTCONF requests on the data resource
(restconf_client.py, OI-1-cm-sync-restconf). Any other protocol is
rejected with PROTOCOL_NOT_SUPPORTED rather than silently applied.
"""

import datetime
import json
import os
import time
import uuid
from typing import Literal

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.logconfig import install_logging
from smo_shared.metrics import install_metrics
from smo_shared.health import database_check, install_health, sme_token_check
from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.r1_client import R1Client
from smo_shared.timeutil import as_utc
from smo_shared.openapi_security import apply_r1_gateway_security
from smo_shared.correlation import apply_correlation_id
from smo_shared.pagination import PageLimit, PageOffset, paginate
from smo_shared.webhook import post_webhook
from smo_shared.versioning import install_concurrency_handler
from smo_shared.idempotency import idempotent

from .models import Alarm, CMSchemaCache, FileSubscription, VendorCapability, FMSubscription, ManagedEntity, O1AdaptorEndpoint, PMFile, PMSubscription, SoftwareManagementJob, WriteConfigJob, WriteConfigSubChange
from . import msac
from .ldn import check_ref, leaf_class, leaf_id
from . import restconf_client
from .netconf_client import NETCONF_TIMEOUT_SECONDS, send_edit_config, send_get_config
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
install_logging(app)  # structured JSON logs and one access-log line per request (PR-OBS-1)
install_metrics(app)  # /metrics and request count/latency series (PR-OBS-2)
install_concurrency_handler(app)  # a stale write (PR-ST-2) is a 409, not a 500
app.include_router(msac.router)
apply_r1_gateway_security(app)
apply_correlation_id(app)

MISSED_HEARTBEAT_THRESHOLD = datetime.timedelta(seconds=90)

# Wave 10.1 (W10-19): a transient NETCONF failure (timeout / unreachable
# agent) is retried — attempt 1 immediately, then after +5, +10 and +20 s
# (the delays before each attempt; "max 3 retries"). An <rpc-error> is a
# definite answer and never retried. Exhausting the retries raises an
# alarm on the ME. Overridable for demos and tests.
#
# The retries run inside the request that submitted the job (a request thread sleeps; moving them to a job
# runner is MSG-4 / ST-9.3, not built), so their total time is bounded, per sub-change, by a budget (PR-ST-9):
# a retry is not started when the time already spent plus its delay would pass DISPATCH_RETRY_BUDGET_SECONDS,
# and the first attempt is always made. The default, 35 s, is exactly the sum of the default delays, so a
# fast-failing adaptor (connection refused) gets the whole schedule; an unresponsive one (each attempt waits out
# the 30 s exchange timeout) gets two attempts. Worst case per sub-change: the budget plus one more attempt in
# flight (`worst_case_dispatch_seconds()`, 65 s by default). A job's sub-changes are dispatched one after the
# other, so a job of N changes can take N times that. R1 Termination answers 504 after
# R1_UPSTREAM_TIMEOUT_SECONDS (60), while this request goes on: set the budget to 25 or less for a single-change
# caller that must be answered inside that window.
NETCONF_RETRY_DELAYS = [float(d) for d in os.environ.get("RAN_NF_OAM_NETCONF_RETRY_DELAYS", "0,5,10,20").split(",")]
DISPATCH_RETRY_BUDGET_SECONDS = float(os.environ.get("RAN_NF_OAM_DISPATCH_RETRY_BUDGET_SECONDS", "35"))
_sleep = time.sleep
_monotonic = time.monotonic


def worst_case_dispatch_seconds() -> float:
    """The longest one sub-change's dispatch can take: the retry time allowed by the budget, plus the last attempt."""
    return min(sum(NETCONF_RETRY_DELAYS), DISPATCH_RETRY_BUDGET_SECONDS) + NETCONF_TIMEOUT_SECONDS


# OI-1-cm-sync-restconf: the O1 protocols this module dispatches CM over —
# o1_protocol -> (edit, read, reason for an unexplained failure). Resolved
# at call time so tests can patch either client function.
def _o1_client(protocol: str):
    if protocol == "NETCONF":
        return send_edit_config, send_get_config, "NETCONF_RPC_FAILED"
    if protocol == "RESTCONF":
        return restconf_client.send_edit, restconf_client.send_get, "RESTCONF_REQUEST_FAILED"
    return None


def _dispatch_with_retries(adaptor_uri: str, change: dict, attribute_changes: dict, message_id: str,
                           operation: str, protocol: str = "NETCONF") -> tuple[bool, str | None, int]:
    """(applied, rejection reason, attempts) for one sub-change. The same
    retry policy for both protocols: only a transient failure is retried, and only within the time budget."""
    send_edit, _, default_reason = _o1_client(protocol)
    reason, attempts = None, 0
    started = _monotonic()
    for delay in NETCONF_RETRY_DELAYS:
        if attempts and _monotonic() - started + delay > DISPATCH_RETRY_BUDGET_SECONDS:
            break                      # the budget (above) is spent: give up now, as for a non-retryable failure
        if delay:
            _sleep(delay)
        attempts += 1
        result = send_edit(adaptor_uri, change["managedElementRef"], attribute_changes, message_id=message_id,
                           operation=operation, managed_function_ref=change.get("managedFunctionRef"))
        if result:
            return True, None, attempts
        reason = getattr(result, "reason", None) or default_reason
        if not getattr(result, "retryable", False):
            break
    return False, reason, attempts


def _raise_dispatch_alarm(db: Session, job_id: uuid.UUID, change: dict, reason: str, attempts: int) -> None:
    target = change.get("managedFunctionRef") or change["managedElementRef"]
    db.add(Alarm(source_alarm_id=f"o1-config:{job_id}:{target}", managed_element_ref=change["managedElementRef"],
                 managed_function_ref=change.get("managedFunctionRef"), severity="major",
                 alarm_type="COMMUNICATIONS_ALARM", probable_cause=reason,
                 specific_problem=f"edit-config to {target} failed after {attempts} attempts"))


# SA-RANOAM-6-severity: TS 28.111 PerceivedSeverity is six upper-case values.
# The alarm table keeps its lowercase wire value (`severity`); the API accepts
# either case, and every alarm view also carries `perceivedSeverity` upper-case.
PERCEIVED_SEVERITIES = ("INDETERMINATE", "CRITICAL", "MAJOR", "MINOR", "WARNING", "CLEARED")


def _perceived_severity(value: str) -> str:
    """The lowercase stored form of `value`; 422 if it is not a PerceivedSeverity."""
    if value.upper() not in PERCEIVED_SEVERITIES:
        raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED,
                              detail=f"severity {value!r} is not a PerceivedSeverity ({', '.join(PERCEIVED_SEVERITIES)})")
    return value.lower()


def _valid_refs(*refs: str | None) -> None:
    """SA-RANOAM-4: a ref carrying '=' must be a well-formed DN."""
    for ref in refs:
        try:
            check_ref(ref)
        except ValueError as exc:
            raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED, detail=str(exc)) from exc


class WriteConfigRequest(BaseModel):
    requestedBy: str
    # SA-RANOAM-2: `scope` collides with the ProvMnS ScopeType, so the access
    # scope is `accessScope`. `scope` stays as a deprecated alias (same value);
    # at least one is required and both, if sent, must agree.
    accessScope: str | None = None
    scope: str | None = None
    changes: list[dict]  # each: {managedElementRef, managedFunctionRef?, attributeChanges?, operation?}
    msacRole: str | None = None

    @model_validator(mode="after")
    def _scope_and_refs(self):
        if self.accessScope is None and self.scope is None:
            raise ValueError("accessScope is required (scope is its deprecated alias)")
        if self.accessScope is not None and self.scope is not None and self.accessScope != self.scope:
            raise ValueError("accessScope and its deprecated alias scope disagree")
        self.accessScope = self.accessScope if self.accessScope is not None else self.scope
        for change in self.changes:
            for key in ("managedElementRef", "managedFunctionRef"):
                check_ref(change.get(key))  # a ref carrying '=' must be a well-formed DN
        return self


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
    _valid_refs(body.managedElementRef, body.managedFunctionRef)
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
@idempotent("ran-nf-oam", status_code=202)
def write_configuration_changes(body: WriteConfigRequest, request: Request, db: Session = Depends(get_session)):
    """WriteConfigurationChanges — RAN NF OAM LLD section 5.1's full
    sequence: MSAC gate, schema check (cache-or-fetch), decompose into
    sub_changes, PATCH each independently, aggregate.
    """
    # SA-RANOAM-1: TS 28.319 role-based access control, per sub-change, before
    # anything is dispatched. A requester with a registered Identity or a
    # defined Role is evaluated against its AccessRules; any other requester
    # keeps the legacy gate (entire-RAN needs a named msacRole).
    managed, roles = msac.resolve_roles(db, body.requestedBy, body.msacRole)
    if managed:
        denied = []
        for change in body.changes:
            op = msac.CONFIG_OPERATION.get(change.get("operation", "merge"))
            target = msac.target_path(change["managedElementRef"], change.get("managedFunctionRef"))
            if op is None or not msac.authorize(db, roles, target, op):
                denied.append(f"{change.get('operation', 'merge')} {target}")
        if denied:
            raise framework_error(FrameworkError.MSAC_ACCESS_DENIED, detail=f"{body.requestedBy} is not permitted: {'; '.join(denied)}")
    elif body.accessScope == "entire-RAN" and not body.msacRole:
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

    job = WriteConfigJob(requested_by=body.requestedBy, scope=body.accessScope, msac_role=body.msacRole)
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
        if _o1_client(me.o1_protocol) is None:
            # NETCONF and RESTCONF are dispatched; any other provisioned
            # protocol is rejected rather than silently treated as applied.
            db.add(WriteConfigSubChange(job_id=job.job_id, managed_element_ref=change["managedElementRef"],
                                         managed_function_ref=change.get("managedFunctionRef"),
                                         attribute_changes=attribute_changes, operation=operation, status="REJECTED",
                                         rejection_reason="PROTOCOL_NOT_SUPPORTED"))
            continue
        applied, reason, attempts = _dispatch_with_retries(endpoint.adaptor_uri, change, attribute_changes,
                                                           str(job.job_id), operation, me.o1_protocol)
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
    running configuration from its O1 adaptor (NETCONF <get-config>, or a
    RESTCONF GET of the data resource) — the live value on the NF, not what
    this module last asked for — so a caller can verify that a write
    actually took effect."""
    require_service(db, managed_element_ref, "PROV")
    me = db.get(ManagedEntity, managed_element_ref)
    endpoint = db.get(O1AdaptorEndpoint, me.o1_adaptor_endpoint_id) if me and me.o1_adaptor_endpoint_id else None
    if endpoint is None:
        raise framework_error(FrameworkError.ENDPOINT_UNREACHABLE, detail=f"{managed_element_ref} has no registered O1 adaptor")
    client = _o1_client(me.o1_protocol)
    if client is None:
        raise framework_error(FrameworkError.PROTOCOL_NOT_SUPPORTED,
                              detail=f"{managed_element_ref} is provisioned for {me.o1_protocol}, which has no client")
    attributes = client[1](endpoint.adaptor_uri, managed_element_ref, message_id=str(uuid.uuid4()),
                           managed_function_ref=managed_function_ref)
    if attributes is None:
        raise framework_error(FrameworkError.ENDPOINT_UNREACHABLE, detail=f"configuration read on {managed_element_ref} failed")
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
def query_alarms(managed_element_ref: str | None = None, severity: str | None = None,
                  managed_function_ref: str | None = None, limit: int = PageLimit,
                  offset: int = PageOffset, db: Session = Depends(get_session)):
    """`severity` filter (GUI pass) — the alarm console filters by ME and
    by perceivedSeverity; `severity=cleared` isolates the cleared history.
    `managed_function_ref` (W10-alarm-cellref) narrows to the alarms raised
    on one managed function, e.g. a cell's `NRCellDU=101`.
    """
    stmt = select(Alarm)
    if managed_element_ref:
        stmt = stmt.where(Alarm.managed_element_ref == managed_element_ref)
    if managed_function_ref:
        # a flat ref, a full DN, or an RDN that ends a stored DN (`NRCellDU=101`)
        stmt = stmt.where((Alarm.managed_function_ref == managed_function_ref)
                          | Alarm.managed_function_ref.endswith("," + managed_function_ref, autoescape=True))
    if severity:
        stmt = stmt.where(Alarm.severity == _perceived_severity(severity))
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [_alarm_view(a) for a in page["items"]]}


@app.post("/alarms/ingest")
def ingest_alarm(source_alarm_id: str, managed_element_ref: str, severity: str, correlation_group: str | None = None,
                  probable_cause: str | None = None, specific_problem: str | None = None, root_cause_indicator: bool = False,
                  correlated_notifications: list[uuid.UUID] = Query(default=[]), proposed_repair_actions: str | None = None,
                  alarm_type: str | None = None, managed_function_ref: str | None = None, db: Session = Depends(get_session)):
    """alarmId is ALWAYS a fresh UUID minted here, never the raising ME's
    native ID — RAN NF OAM LLD section 3.3, closing R1UCR's own flagged,
    unresolved collision risk under a fleet of N MEs.

    probableCause/specificProblem/rootCauseIndicator/correlatedNotifications/
    proposedRepairActions (HISTORY.md §5): the standard fault
    fields 3GPP TS 28.532 FaultMnS's NotifyNewAlarm carries, previously
    entirely absent from this alarm model. alarmType (HISTORY.md §7,
    TS28111_FaultNrm.yaml's AlarmRecord) was the one of these fields
    still missing after that pass.

    W10-alarm-cellref: `managed_function_ref` is the managed function the
    alarm is about inside the element (AlarmRecord's objectInstance below
    the ME), e.g. `NRCellDU=101`, so a consumer can hold that one cell
    rather than the whole element. Omitted = the element as a whole.
    """
    require_service(db, managed_element_ref, "FM")  # Wave 9 (W9-01)
    severity = _perceived_severity(severity)
    _valid_refs(managed_element_ref, managed_function_ref)
    alarm = Alarm(source_alarm_id=source_alarm_id, managed_element_ref=managed_element_ref,
                  managed_function_ref=managed_function_ref, severity=severity, correlation_group=correlation_group,
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
    jobs, delivered = _fan_out_to_dme(body.managedElementRef, body.counterType, body.measurements)
    return {"managedElementRef": body.managedElementRef, "counterType": body.counterType,
            "measurements": len(body.measurements), "dataJobs": jobs, "recordsDelivered": delivered}


def _fan_out_to_dme(managed_element_ref: str, counter_type: str, measurements: list[PmMeasurement]) -> tuple[int, int]:
    """(open data jobs, records delivered): each measurement goes to every
    data job open on `RAN.PMCounters.<counterType>`."""
    r1 = R1Client()
    type_name = f"RAN.PMCounters.{counter_type}"
    dme_type = next((t for t in r1.get("/dme/dme-types", params={"data_category": "RAN"}).json()
                     if t["typeName"] == type_name), None)
    jobs = r1.get("/dme/data-jobs", params={"dme_type_id": dme_type["dmeTypeId"], "limit": 500}).json()["items"] if dme_type else []
    delivered = 0
    for m in measurements:
        payload = {"managedElementRef": managed_element_ref, "cellId": m.cellId, "counter": counter_type,
                   "value": m.value, "timestamp": m.timestamp.isoformat()}
        if m.values is not None:
            payload["values"] = m.values
        if m.relation is not None:
            payload["relation"] = m.relation
        for job in jobs:
            r1.post(f"/dme/data-jobs/{job['dataJobId']}/records", json={"payload": payload})
            delivered += 1
    return len(jobs), delivered


# ---------------------------------------------------------------- file data reporting
# SA-RANOAM-8: TS 28.532 File Data Reporting MnS (TS28532_FileDataReportingMnS.yaml).
# The NF's O1 adaptor reports a finished performance file (`POST /pm-files`); its
# measurements go to DME exactly as `POST /pm-reports` does, the file itself is
# kept and served (`GET /pm-files/{id}/file`, listed by `GET /files`), and every
# file subscription gets notifyFileReady. Streaming (TS28532_StreamingDataMnS) is
# not built: there is no streaming transport, and `delivery_method=stream`
# remains a registration only.

FileDataType = Literal["Performance", "Trace", "Analytics", "Proprietary"]


class PmFileRequest(BaseModel):
    managedElementRef: str
    counterType: str
    measurements: list[PmMeasurement]
    fileDataType: FileDataType = "Performance"
    fileFormat: str = "json"
    fileCompression: str | None = None
    jobId: str | None = None
    fileExpirationTime: datetime.datetime | None = None


class FileSubscriptionRequest(BaseModel):
    """TS 28.623 Subscription. `filter` (a Jex condition) is not supported and
    is refused; `fileDataType` narrows the subscription to one data type."""
    model_config = ConfigDict(extra="forbid")
    consumerReference: str
    timeTick: int | None = None
    fileDataType: FileDataType | None = None


def _file_info(f: PMFile) -> dict:
    return {"fileLocation": f"/ran-nf-oam/pm-files/{f.file_id}/file", "fileSize": f.file_size,
            "fileReadyTime": as_utc(f.file_ready_time).isoformat(),
            "fileExpirationTime": as_utc(f.file_expiration_time).isoformat() if f.file_expiration_time else None,
            "fileCompression": f.file_compression, "fileFormat": f.file_format, "fileDataType": f.file_data_type,
            "jobId": f.job_id}


@app.post("/pm-files", status_code=201)
def report_pm_file(body: PmFileRequest, db: Session = Depends(get_session)):
    require_service(db, body.managedElementRef, "FILE")
    subscribed = db.scalars(select(PMSubscription).where(PMSubscription.managed_element_ref == body.managedElementRef,
                                                         PMSubscription.counter_type == body.counterType)).first()
    if subscribed is None:
        raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED,
                              detail=f"no PM subscription for {body.counterType} on {body.managedElementRef}")
    content = json.dumps({"managedElementRef": body.managedElementRef, "counterType": body.counterType,
                          "measurements": [m.model_dump(mode="json", exclude_none=True) for m in body.measurements]})
    pm_file = PMFile(managed_element_ref=body.managedElementRef, counter_type=body.counterType, file_data_type=body.fileDataType,
                     file_format=body.fileFormat, file_compression=body.fileCompression, job_id=body.jobId, content=content,
                     file_size=len(content.encode()), file_expiration_time=body.fileExpirationTime)
    db.add(pm_file)
    db.flush()
    info = _file_info(pm_file)
    targets = [(sub.subscription_id, sub.consumer_reference, sub.sequence_no + 1) for sub in db.scalars(select(FileSubscription)).all()
               if sub.file_data_type in (None, body.fileDataType)]
    for sub in db.scalars(select(FileSubscription)).all():
        if sub.file_data_type in (None, body.fileDataType):
            sub.sequence_no += 1
    file_id = str(pm_file.file_id)
    db.commit()
    for subscription_id, consumer, sequence_no in targets:
        post_webhook(consumer, json={"href": "/ran-nf-oam/file-subscriptions", "notificationId": sequence_no,
                                     "notificationType": "notifyFileReady", "eventTime": info["fileReadyTime"],
                                     "sequenceNo": sequence_no, "subscriptionId": str(subscription_id),
                                     "fileInfoList": [info]}, timeout=2.0)
    jobs, delivered = _fan_out_to_dme(body.managedElementRef, body.counterType, body.measurements)
    return {"fileId": file_id, **info, "notified": len(targets), "dataJobs": jobs, "recordsDelivered": delivered}


@app.get("/files")
def read_file_info(fileDataType: FileDataType, beginTime: datetime.datetime | None = None, endTime: datetime.datetime | None = None,
                   limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    """TS 28.532 `GET /files`: FileInfo for the files of a data type, selected by
    the time they became available. Paginated like every list here."""
    stmt = select(PMFile).where(PMFile.file_data_type == fileDataType)
    if beginTime:
        stmt = stmt.where(PMFile.file_ready_time >= beginTime)
    if endTime:
        stmt = stmt.where(PMFile.file_ready_time <= endTime)
    page = paginate(db, stmt.order_by(PMFile.file_ready_time), limit, offset)
    return {**page, "items": [_file_info(f) for f in page["items"]]}


@app.get("/pm-files/{file_id}/file")
def download_pm_file(file_id: uuid.UUID, db: Session = Depends(get_session)):
    f = db.get(PMFile, file_id)
    if f is None:
        raise framework_error(FrameworkError.NRM_OBJECT_NOT_FOUND, detail=f"no such file {file_id}")
    if f.file_expiration_time and as_utc(f.file_expiration_time) < datetime.datetime.now(datetime.UTC):
        raise framework_error(FrameworkError.NRM_OBJECT_NOT_FOUND, detail=f"file {file_id} has expired")
    return Response(content=f.content, media_type="application/json")


@app.post("/file-subscriptions", status_code=201)
def create_file_subscription(body: FileSubscriptionRequest, db: Session = Depends(get_session)):
    sub = FileSubscription(consumer_reference=body.consumerReference, file_data_type=body.fileDataType)
    db.add(sub)
    db.commit()
    return {"subscriptionId": str(sub.subscription_id), "consumerReference": sub.consumer_reference,
            "timeTick": body.timeTick, "fileDataType": sub.file_data_type}


@app.delete("/file-subscriptions/{subscription_id}", status_code=204)
def delete_file_subscription(subscription_id: uuid.UUID, db: Session = Depends(get_session)):
    sub = db.get(FileSubscription, subscription_id)
    if sub is not None:
        db.delete(sub)
        db.commit()


install_health(app, checks=[database_check, sme_token_check])  # /live, /ready and the /health alias (PR-ST-7)


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
            "managedFunctionRef": a.managed_function_ref,
            "managedFunctionClass": leaf_class(a.managed_function_ref), "managedFunctionId": leaf_id(a.managed_function_ref),
            "severity": a.severity, "perceivedSeverity": a.severity.upper(), "ackState": a.ack_state,
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
    return {**page, "items": [{"jobId": str(j.job_id), "requestedBy": j.requested_by, "accessScope": j.scope, "scope": j.scope,
             "status": j.status, "msacRole": j.msac_role} for j in page["items"]]}


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
