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
import logging
import json
import os
import time
import uuid
from typing import Literal

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from smo_shared.errors import illegal_transition_error
from smo_shared.statemachine import IllegalTransition
from sqlalchemy import delete, func, select
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
from smo_shared.outbox import enqueue
from smo_shared.versioning import install_concurrency_handler
from smo_shared.idempotency import idempotent
from smo_shared.invoker import invoker_id

from .models import Alarm, RAppLimit, CMSchemaCache, CMSnapshot, FileSubscription, KpiDefinition, VendorCapability, FMSubscription, ManagedEntity, ManagedObject, O1AdaptorEndpoint, O1AdaptorHostKey, PMFile, PMSubscription, SoftwareManagementJob, WriteConfigJob, WriteConfigSubChange
from . import msac
from .ldn import check_ref, leaf_class, leaf_id
from . import mo_tree
from . import topology
from . import yang_payload
from . import kpi, kpi_formula
from . import netconf_tls
from . import restconf_client
from . import netconf_ssh
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
log = logging.getLogger("ran-nf-oam")
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
# MGT-1: read the current values of what a change names, just before sending it, and keep them with the result (cm_snapshot).
# The read is one more exchange per sub-change (at most NETCONF_TIMEOUT_SECONDS); set false to skip it and the table.
CM_SNAPSHOTS = os.environ.get("RAN_NF_OAM_CM_SNAPSHOTS", "true").lower() not in ("0", "false", "no")
# MGT-1.8 / DB-3.2: how long a snapshot is kept before `POST /config-history/purge` may delete it, in days; 0 keeps them for ever (the default,
# so nothing is deleted by an upgrade). The purge runs when an operator or a scheduler calls it; nothing in the service deletes on its own.
CM_SNAPSHOT_RETENTION_DAYS = int(os.environ.get("RAN_NF_OAM_CM_SNAPSHOT_RETENTION_DAYS", "0") or 0)


def worst_case_dispatch_seconds() -> float:
    """The longest one sub-change's dispatch can take: the retry time allowed by the budget, plus the last attempt."""
    return min(sum(NETCONF_RETRY_DELAYS), DISPATCH_RETRY_BUDGET_SECONDS) + NETCONF_TIMEOUT_SECONDS


# OI-1-cm-sync-restconf: the O1 protocols this module dispatches CM over —
# o1_protocol -> (edit, read, reason for an unexplained failure). Resolved
# at call time so tests can patch either client function.
def _o1_client(protocol: str, transport: str = "http-mock"):
    if protocol == "NETCONF" and transport in ("ssh", "tls"):          # one pair of functions: the URI scheme picks SSH or TLS (PR-SB-2.4)
        return netconf_ssh.send_edit_config, netconf_ssh.send_get_config, "NETCONF_RPC_FAILED"
    if protocol == "NETCONF":
        return send_edit_config, send_get_config, "NETCONF_RPC_FAILED"
    if protocol == "RESTCONF":
        return restconf_client.send_edit, restconf_client.send_get, "RESTCONF_REQUEST_FAILED"
    return None


def worst_case_sub_change_seconds() -> float:
    """`worst_case_dispatch_seconds()` plus the before-image read, when snapshots are on."""
    return worst_case_dispatch_seconds() + (NETCONF_TIMEOUT_SECONDS if CM_SNAPSHOTS else 0.0)


def _capture_before(me, endpoint, change: dict, attribute_changes: dict, ssh_options: dict | None = None) -> tuple[dict | None, str | None]:
    """(before values, error): the NF's current values of the named attributes, or of the whole object when the change names none
    (delete/remove). A failed read does not stop the write; it is recorded so nobody mistakes a missing image for an empty one."""
    read = _o1_client(me.o1_protocol, endpoint.transport)[1]
    try:
        current = read(endpoint.adaptor_uri, change["managedElementRef"], message_id=str(uuid.uuid4()),
                       managed_function_ref=change.get("managedFunctionRef"), **(ssh_options or {}))
    except Exception as exc:                                   # noqa: BLE001 - a client bug must not lose the write itself
        return None, f"before-image read raised {type(exc).__name__}"
    if current is None:
        return None, "before-image read failed"
    return ({name: current.get(name) for name in attribute_changes} if attribute_changes else dict(current)), None


def _ssh_options(db: Session, endpoint) -> dict:
    """What only the ssh clients take, per endpoint: the name of its credential (PR-SB-2.1) and the host keys an operator pinned for it
    (PR-SB-2.3). Empty for any other transport (the HTTP and RESTCONF clients have no such parameters)."""
    if endpoint.transport == "tls":
        return {"credential_ref": endpoint.credential_ref}             # TLS trusts a CA file, not pinned keys
    if endpoint.transport != "ssh":
        return {}
    keys = db.execute(select(O1AdaptorHostKey.key_type, O1AdaptorHostKey.public_key)
                      .where(O1AdaptorHostKey.endpoint_id == endpoint.endpoint_id)).all()
    return {"credential_ref": endpoint.credential_ref, "host_keys": [(k.key_type, k.public_key) for k in keys]}


def _dispatch_with_retries(adaptor_uri: str, change: dict, attribute_changes: dict, message_id: str,
                           operation: str, protocol: str = "NETCONF", transport: str = "http-mock",
                           ssh_options: dict | None = None) -> tuple[bool, str | None, int, str | None]:
    """(applied, rejection reason, attempts, adaptor detail) for one sub-change. The same
    retry policy for both protocols: only a transient failure is retried, and only within the time budget."""
    send_edit, _, default_reason = _o1_client(protocol, transport)
    reason, attempts, detail = None, 0, None
    started = _monotonic()
    for delay in NETCONF_RETRY_DELAYS:
        if attempts and _monotonic() - started + delay > DISPATCH_RETRY_BUDGET_SECONDS:
            break                      # the budget (above) is spent: give up now, as for a non-retryable failure
        if delay:
            _sleep(delay)
        attempts += 1
        result = send_edit(adaptor_uri, change["managedElementRef"], attribute_changes, message_id=message_id,
                           operation=operation, managed_function_ref=change.get("managedFunctionRef"),
                           **(ssh_options or {}))
        if result:
            return True, None, attempts, None
        reason = getattr(result, "reason", None) or default_reason
        detail = getattr(result, "detail", None)
        if not getattr(result, "retryable", False):
            break
    return False, reason, attempts, detail


def _transaction_group_key(me, endpoint) -> str | None:
    """PR-SB-1.10: the element a change is grouped under for a candidate transaction, or None when its endpoint does not take part (an
    ssh or tls NETCONF endpoint registered with `?datastore=candidate`; everything else is dispatched one sub-change at a time)."""
    if me is None or endpoint is None or me.o1_protocol != "NETCONF" or endpoint.transport not in ("ssh", "tls"):
        return None
    return me.managed_element_ref if yang_payload.datastore_of(endpoint.adaptor_uri) == "candidate" else None


def _dispatch_group_with_retries(adaptor_uri: str, changes: list[dict], message_id: str, ssh_options: dict | None
                                 ) -> list[tuple[bool, str | None, int, str | None]]:
    """One candidate transaction for several sub-changes of one element, with the retry policy of `_dispatch_with_retries` applied to
    the whole unit (only a transient failure of the connection is retried). One (applied, reason, attempts, detail) per change."""
    edits = [{"target_ref": c["managedElementRef"], "attribute_changes": c.get("attributeChanges", {}),
              "operation": c.get("operation", "merge"), "managed_function_ref": c.get("managedFunctionRef")} for c in changes]
    results, attempts = [], 0
    started = _monotonic()
    for delay in NETCONF_RETRY_DELAYS:
        if attempts and _monotonic() - started + delay > DISPATCH_RETRY_BUDGET_SECONDS:
            break
        if delay:
            _sleep(delay)
        attempts += 1
        results = netconf_ssh.send_edit_configs(adaptor_uri, edits, message_id, **(ssh_options or {}))
        if all(results) or not any(getattr(r, "retryable", False) for r in results):
            break
    return [(bool(r), None if r else (getattr(r, "reason", None) or "NETCONF_RPC_FAILED"), attempts,
             None if r else getattr(r, "detail", None)) for r in results]


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
    # MGT-3.1: run every check (MSAC, service presence, data model incl. YANG leaf constraints) and send nothing
    dryRun: bool = False
    # MGT-5.1: a staged rollout. The elements of the job go in waves of `waveSize` elements (all the changes of one element in one wave); after each
    # wave but the last the health gate runs (MGT-5.3): a rejected sub-change, or more than `gateMaxNewAlarms` new critical or major alarms on the
    # wave's elements since it started, fails it. A failed gate halts the job (`onGateFailure` "halt", MGT-5.4) or undoes the applied waves ("revert",
    # MGT-5.5). `wavePauseSeconds` holds the job between waves until that time has passed. No `waveSize`: one wave, as before.
    waveSize: int | None = Field(default=None, ge=1)
    wavePauseSeconds: int = Field(default=0, ge=0)
    gateMaxNewAlarms: int = Field(default=0, ge=0)
    onGateFailure: Literal["halt", "revert"] = "halt"

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
    # PR-SB-1.2: how the adaptor is reached. `ssh`: NETCONF over SSH (RFC 6242), adaptorUri is ssh://user@host[:port].
    # `tls` (PR-SB-2.4): NETCONF over TLS (RFC 7589), adaptorUri is tls://host[:port], authenticated by a client certificate.
    transport: Literal["http-mock", "ssh", "tls"] = "http-mock"
    # PR-SB-2.1: the name of the credential to use (never the secret); only for transport ssh, and it must be one this service has been given.
    # Checked in the route, not here: a validation error of the body model repeats the input, and a pasted secret must not be echoed.
    credentialRef: str | None = None

    @model_validator(mode="after")
    def _transport_matches(self):
        if self.transport == "ssh":
            if self.o1Protocol != "NETCONF":
                raise ValueError("transport ssh carries NETCONF only")
            try:
                netconf_ssh.parse_ssh_uri(self.adaptorUri)
            except netconf_ssh.NetconfSshError as exc:
                raise ValueError(exc.detail) from exc
        elif self.transport == "tls":
            if self.o1Protocol != "NETCONF":
                raise ValueError("transport tls carries NETCONF only")
            try:
                netconf_tls.parse_tls_uri(self.adaptorUri)
            except netconf_ssh.NetconfSshError as exc:
                raise ValueError(exc.detail) from exc
        elif self.adaptorUri.lower().startswith("ssh:"):
            raise ValueError("an ssh:// adaptorUri needs transport ssh")
        elif self.adaptorUri.lower().startswith("tls:"):
            raise ValueError("a tls:// adaptorUri needs transport tls")
        return self


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
    if body.credentialRef is not None:
        try:
            if body.transport not in ("ssh", "tls"):
                raise ValueError("credentialRef applies to transport ssh or tls only")
            netconf_ssh.check_credential_ref(body.credentialRef)
        except ValueError as exc:
            raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED, detail=str(exc)) from None     # the message never repeats the value
    check_vendor_mode(db, body.vendorName, body.o1Protocol)
    _valid_refs(body.managedElementRef, body.managedFunctionRef)
    cap = db.get(VendorCapability, body.vendorName) if body.vendorName else None
    if cap is not None and body.supportedServices is not None and not set(body.supportedServices) <= set(cap.supported_services):
        raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED,
                              detail=f"supportedServices {body.supportedServices} exceed vendor {body.vendorName!r}'s {cap.supported_services}")
    endpoint = O1AdaptorEndpoint(managed_element_ref=body.managedElementRef, adaptor_uri=body.adaptorUri,
                                  protocol_support=body.protocolSupport, health_status=EndpointHealth.DISCOVERED.value,
                                  supported_services=body.supportedServices, transport=body.transport, credential_ref=body.credentialRef)
    db.add(endpoint)
    db.flush()
    me = ManagedEntity(managed_element_ref=body.managedElementRef, managed_function_ref=body.managedFunctionRef,
                        entity_type=body.entityType, vendor_name=body.vendorName, o1_protocol=body.o1Protocol,
                        o1_adaptor_endpoint_id=endpoint.endpoint_id)
    db.add(me)
    db.flush()
    mo_tree.sync_registry(db, me)              # PR-SB-6: the element's root (and the function it was registered with) join the containment tree
    db.commit()
    return {"endpointId": str(endpoint.endpoint_id), "managedElementRef": me.managed_element_ref, "healthStatus": endpoint.health_status}


class PinHostKeyRequest(BaseModel):
    keyType: str
    publicKey: str
    pinnedBy: str


def _ssh_endpoint(db: Session, endpoint_id: uuid.UUID) -> O1AdaptorEndpoint:
    endpoint = db.get(O1AdaptorEndpoint, endpoint_id)
    if endpoint is None:
        raise framework_error(FrameworkError.O1_ENDPOINT_NOT_FOUND, detail=f"no such O1 adaptor endpoint {endpoint_id}")
    if endpoint.transport != "ssh":
        raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED, detail="host keys apply to endpoints with transport ssh")
    return endpoint


def _host_key_view(row: O1AdaptorHostKey) -> dict:
    return {"keyType": row.key_type, "fingerprint": row.fingerprint, "pinnedBy": row.pinned_by, "pinnedAt": as_utc(row.pinned_at).isoformat()}


@app.put("/o1-adaptor-endpoints/{endpoint_id}/host-keys")
def pin_host_key(endpoint_id: uuid.UUID, body: PinHostKeyRequest, db: Session = Depends(get_session)):
    """PR-SB-2.3: pin the SSH host key an ssh endpoint must present (one per key type). The operator supplies the public key from a source
    they trust (the device's own label, `ssh-keygen -lf`, a signed inventory): this build never learns a key by connecting, so there is no
    trust on first use. A connection whose server key is not pinned (or differs from the pinned key of its type) is refused. Pinning a
    different key for a type that already has one replaces it (`replaced: true`): the one way to accept a changed key, by a named operator."""
    endpoint = _ssh_endpoint(db, endpoint_id)
    try:
        key = netconf_ssh.parse_host_key(body.keyType, body.publicKey)
    except ValueError as exc:
        raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED, detail=str(exc)) from None
    existing = db.scalars(select(O1AdaptorHostKey).where(O1AdaptorHostKey.endpoint_id == endpoint.endpoint_id,
                                                         O1AdaptorHostKey.key_type == key.get_name())).first()
    fingerprint = netconf_ssh.host_key_fingerprint(key)
    replaced = existing is not None and existing.fingerprint != fingerprint
    if existing is None:
        existing = O1AdaptorHostKey(endpoint_id=endpoint.endpoint_id, key_type=key.get_name())
        db.add(existing)
    existing.public_key, existing.fingerprint, existing.pinned_by = key.get_base64(), fingerprint, body.pinnedBy
    existing.pinned_at = datetime.datetime.now(datetime.UTC)
    if replaced:
        log.warning("host key for endpoint %s (%s) replaced by %s: %s", endpoint_id, key.get_name(), body.pinnedBy, fingerprint)
    db.commit()
    return {**_host_key_view(existing), "replaced": replaced}


@app.get("/o1-adaptor-endpoints/{endpoint_id}/host-keys")
def list_host_keys(endpoint_id: uuid.UUID, db: Session = Depends(get_session)):
    endpoint = _ssh_endpoint(db, endpoint_id)
    rows = db.scalars(select(O1AdaptorHostKey).where(O1AdaptorHostKey.endpoint_id == endpoint.endpoint_id)
                      .order_by(O1AdaptorHostKey.key_type)).all()
    return {"items": [_host_key_view(r) for r in rows]}


@app.delete("/o1-adaptor-endpoints/{endpoint_id}/host-keys/{key_type}", status_code=204)
def unpin_host_key(endpoint_id: uuid.UUID, key_type: str, db: Session = Depends(get_session)):
    endpoint = _ssh_endpoint(db, endpoint_id)
    row = db.scalars(select(O1AdaptorHostKey).where(O1AdaptorHostKey.endpoint_id == endpoint.endpoint_id,
                                                    O1AdaptorHostKey.key_type == key_type)).first()
    if row is None:
        raise framework_error(FrameworkError.O1_HOST_KEY_NOT_FOUND, detail=f"no {key_type} host key is pinned for this endpoint")
    db.delete(row)
    db.commit()


# --- PR-SB-6: the managed-object containment tree -------------------------------------------------------------------------------------------


def _managed_object(db: Session, dn: str) -> ManagedObject:
    obj = db.get(ManagedObject, dn)
    if obj is None:
        raise framework_error(FrameworkError.MANAGED_OBJECT_NOT_FOUND, detail=f"no managed object {dn!r} in the tree")
    return obj


@app.get("/managed-objects/{dn}")
def read_managed_object(dn: str, db: Session = Depends(get_session)):
    """PR-SB-6: one node of the containment tree by its distinguished name (`ManagedElement=ME-1,GNBDUFunction=1,NRCellDU=101`)."""
    return mo_tree.view(_managed_object(db, dn))


@app.get("/managed-objects/{dn}/children")
def list_managed_object_children(dn: str, limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    """PR-SB-6.3: the direct children of a node (404 `MANAGED_OBJECT_NOT_FOUND` when the node itself is not in the tree), ordered by class then id."""
    _managed_object(db, dn)
    page = paginate(db, mo_tree.children_stmt(dn), limit, offset)
    return {**page, "items": [mo_tree.view(o) for o in page["items"]]}


@app.get("/managed-objects/{dn}/subtree")
def read_managed_object_subtree(dn: str, depth: int = Query(default=mo_tree.MAX_SUBTREE_DEPTH, ge=0, le=mo_tree.MAX_SUBTREE_DEPTH),
                                db: Session = Depends(get_session)):
    """PR-SB-6.4: a node and its descendants as a nested tree (`children` on each node), down to `depth` levels below it (default and most 16).
    At most 1000 nodes are returned; `truncated` says when that cut the answer short."""
    _managed_object(db, dn)
    tree, truncated = mo_tree.subtree(db, dn, depth)
    return {"tree": tree, "truncated": truncated}


@app.post("/managed-entities/{managed_element_ref}/managed-objects/refresh")
def refresh_managed_objects(managed_element_ref: str, db: Session = Depends(get_session)):
    """PR-SB-6.2: read the element's server with a whole-container `get-config` and make the containment tree match what it reports: new objects
    are added with `source=walk`, walked objects it no longer reports are removed, and registry objects are kept. Needs an ssh or tls endpoint
    registered with `?model=` (a server without a model has nothing to walk): 409 `PROTOCOL_NOT_SUPPORTED` otherwise, 503 when the read fails."""
    me = db.get(ManagedEntity, managed_element_ref)
    if me is None:
        raise framework_error(FrameworkError.MANAGED_ENTITY_NOT_FOUND, detail=f"no managed element {managed_element_ref!r}")
    endpoint = db.get(O1AdaptorEndpoint, me.o1_adaptor_endpoint_id) if me.o1_adaptor_endpoint_id else None
    if endpoint is None:
        raise framework_error(FrameworkError.ENDPOINT_UNREACHABLE, detail=f"{managed_element_ref} has no registered O1 adaptor")
    if endpoint.transport not in ("ssh", "tls") or not yang_payload.model_of(endpoint.adaptor_uri):
        raise framework_error(FrameworkError.PROTOCOL_NOT_SUPPORTED,
                              detail="a walk needs an ssh or tls endpoint registered with ?model=<name>: only a server with a model reports its objects")
    paths = netconf_ssh.send_walk(endpoint.adaptor_uri, str(uuid.uuid4()), **_ssh_options(db, endpoint))
    if paths is None:
        raise framework_error(FrameworkError.ENDPOINT_UNREACHABLE, detail=f"the walk of {managed_element_ref} failed")
    summary = mo_tree.apply_walk(db, managed_element_ref, paths)
    db.commit()
    return {"managedElementRef": managed_element_ref, **summary}


TEIV_RAN_PREFIX = "o-ran-smo-teiv-ran"
TEIV_URN_PREFIX = "urn:oran:smo:teiv"


@app.get("/topology")
def export_topology(managed_element_ref: str | None = None, db: Session = Depends(get_session)):
    """PR-SB-6.7: the containment tree in the wire shape FOCOM's `/topology` already uses for the TEIV adapter (entities keyed `<prefix>:<Entity>`
    with `{id, attributes}`; relationships keyed `<prefix>:<A>_<REL>_<B>` with `{id, aSide, bSide, sourceIds}`). One generic `ManagedObject` entity
    per node and one `MANAGEDOBJECT_CHILD_OF_MANAGEDOBJECT` relationship per parent link, the child on the a-side. This is this build's own export
    of what it holds, not the TEIV RAN domain model (which has typed entities such as GNBDUFunction). Link types: `/topology/links`, `/topology/relation` (MGT-10.2)."""
    stmt = select(ManagedObject).order_by(ManagedObject.dn)
    if managed_element_ref:
        stmt = stmt.where(ManagedObject.managed_element_ref == managed_element_ref)
    objects = db.scalars(stmt).all()
    urn = lambda dn: f"{TEIV_URN_PREFIX}:ManagedObject:{dn}"  # noqa: E731
    entities = [{f"{TEIV_RAN_PREFIX}:ManagedObject": [
        {"id": urn(o.dn), "attributes": {"dn": o.dn, "class": o.object_class, "objectId": o.object_id,
                                          "managedElementRef": o.managed_element_ref, "source": o.source}} for o in objects]}] if objects else []
    present = {o.dn for o in objects}
    child_of = [{"id": f"{TEIV_URN_PREFIX}:MANAGEDOBJECT_CHILD_OF_MANAGEDOBJECT:{o.dn}", "aSide": urn(o.dn), "bSide": urn(o.parent_dn),
                 "sourceIds": [o.dn, o.parent_dn]} for o in objects if o.parent_dn in present]
    relationships = [{f"{TEIV_RAN_PREFIX}:MANAGEDOBJECT_CHILD_OF_MANAGEDOBJECT": child_of}] if child_of else []
    return {"entities": entities, "relationships": relationships}


@app.get("/topology/links")
def topology_links(managed_element_ref: str | None = None, link_type: Literal["INTRA_ELEMENT", "INTER_ELEMENT", "AMBIGUOUS", "EXTERNAL"] | None = None,
                   db: Session = Depends(get_session)):
    """PR-MGT-10.2: the neighbour relations declared in the cell guards, each with its link type (`topology.py`): both cells on one element
    (`INTRA_ELEMENT`), on different elements (`INTER_ELEMENT`), a cell id several elements claim (`AMBIGUOUS`) or none does (`EXTERNAL`), and whether
    the other side declares the relation back (`reciprocal`). `managed_element_ref` keeps the links with that element at either end."""
    return {"items": topology.cell_links(db, managed_element_ref, link_type)}


@app.get("/topology/relation")
def topology_relation(a: str, b: str, db: Session = Depends(get_session)):
    """PR-MGT-10.2: how the managed object `a` (a DN) stands to `b` in the containment tree: SAME, ANCESTOR (a contains b), DESCENDANT, SIBLING,
    SAME_ELEMENT or DIFFERENT_ELEMENT. 404 when either is not in the tree."""
    objects = []
    for dn in (a, b):
        obj = db.get(ManagedObject, dn)
        if obj is None:
            raise framework_error(FrameworkError.MANAGED_OBJECT_NOT_FOUND, detail=f"{dn!r} is not in the containment tree")
        objects.append(obj)
    return {"a": a, "b": b, "relation": topology.containment_relation(db, *objects)}


def _enforce_mo_tree() -> bool:
    """PR-SB-6.5: `RAN_NF_OAM_ENFORCE_MO_TREE` (off by default): a sub-change whose target DN is not in the containment tree is rejected with
    `MANAGED_OBJECT_NOT_FOUND` before anything is sent. Read at each call, so it can be switched with a restart and in tests."""
    return os.environ.get("RAN_NF_OAM_ENFORCE_MO_TREE", "").strip().lower() in ("1", "true", "yes", "on")


def _dispatch_blocker(db: Session, change: dict):
    """(rejection reason or None, managed entity, endpoint): whether a change can be sent at all, from what is registered now."""
    me = db.get(ManagedEntity, change["managedElementRef"])
    if me is None or me.o1_adaptor_endpoint_id is None:
        return "ENDPOINT_UNREACHABLE", me, None
    endpoint = db.get(O1AdaptorEndpoint, me.o1_adaptor_endpoint_id)
    # Live-computed staleness at the point health is actually consulted — the same "no scheduler exists anywhere in this
    # build" pattern as DME's producer health and A1 Related's service supervision sweep — rather than depending on
    # something having already called POST /o1-adaptor-endpoints/discover first.
    _age_endpoint_health(endpoint, datetime.datetime.now(datetime.UTC))
    if endpoint.health_status in ("UNREACHABLE", "DEGRADED"):
        return "ENDPOINT_UNREACHABLE", me, endpoint
    if _o1_client(me.o1_protocol, endpoint.transport) is None:
        # NETCONF and RESTCONF are dispatched; any other provisioned protocol is rejected rather than silently treated as applied.
        return "PROTOCOL_NOT_SUPPORTED", me, endpoint
    if _enforce_mo_tree() and not mo_tree.exists(db, mo_tree.target_dn(change["managedElementRef"], change.get("managedFunctionRef"))):
        return "MANAGED_OBJECT_NOT_FOUND", me, endpoint        # PR-SB-6.5: the target is not in the containment tree
    return None, me, endpoint


@app.post("/config-jobs", status_code=202)
@idempotent("ran-nf-oam", status_code=202)
def write_configuration_changes(body: WriteConfigRequest, request: Request, db: Session = Depends(get_session)):
    """WriteConfigurationChanges — RAN NF OAM LLD section 5.1's full
    sequence: MSAC gate, schema check (cache-or-fetch), decompose into
    sub_changes, PATCH each independently, aggregate.
    """
    caller = invoker_id(request)
    _enforce_rapp_limit(db, caller)
    return _execute_write(body, db, invoker=caller)


RATE_WINDOW = datetime.timedelta(hours=1)


def _enforce_rapp_limit(db: Session, caller: str | None) -> None:
    """AI-10.2: 429 when `caller` (the invoker id R1 vouches for) has already started as many write jobs in the last hour as its limit
    (`PUT /rapp-limits/{invoker_id}`, from the manifest of the rApp) allows. A caller with no limit, or none that R1 identified, is not counted.
    Rollbacks and automatic reverts do not pass here: undoing a change must never be refused because the rApp used its budget."""
    limit = db.get(RAppLimit, caller) if caller else None
    if limit is None:
        return
    since = datetime.datetime.now(datetime.UTC) - RATE_WINDOW
    used = db.scalar(select(func.count()).select_from(WriteConfigJob).where(WriteConfigJob.invoker_id == caller, WriteConfigJob.created_at >= since)) or 0
    if used >= limit.max_config_jobs_per_hour:
        error = framework_error(FrameworkError.RAPP_RATE_LIMITED,
                                detail=f"{caller} has started {used} config jobs in the last hour; its limit is {limit.max_config_jobs_per_hour}")
        error.headers = {"Retry-After": "60"}
        raise error


def _execute_write(body: WriteConfigRequest, db: Session, rollback_of: uuid.UUID | None = None, rollback_forced: bool = False,
                   invoker: str | None = None):
    """The body of `POST /config-jobs`, shared with the rollback route (MGT-1.6), which builds the same request from a recorded job and so
    goes through the same MSAC, schema, dispatch and snapshot steps as any other write."""
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
    elements = list(dict.fromkeys(c["managedElementRef"] for c in body.changes))
    size = body.waveSize or len(elements) or 1
    wave_of = {element: index // size + 1 for index, element in enumerate(elements)}
    if body.dryRun:
        # MGT-3.1/3.2: the checks above passed; each change's verdict adds what the dispatch loop would decide from the registry
        # (no endpoint, endpoint down, no client for its protocol). No job row, no southbound call, no outbox row.
        verdicts = []
        for c in body.changes:
            blocker = _dispatch_blocker(db, c)[0]
            verdicts.append({"managedElementRef": c["managedElementRef"], "managedFunctionRef": c.get("managedFunctionRef"),
                             "operation": c.get("operation", "merge"),
                             "verdict": "PASS" if blocker is None else "WOULD_REJECT", "reason": blocker})
        return JSONResponse(status_code=200, content={
            "dryRun": True, "status": "VALIDATED" if all(v["verdict"] == "PASS" for v in verdicts) else "WOULD_REJECT_SOME",
            "waves": [[e for e in elements if wave_of[e] == w] for w in range(1, max(wave_of.values(), default=1) + 1)],
            "changes": verdicts})

    job = WriteConfigJob(requested_by=body.requestedBy, scope=body.accessScope, msac_role=body.msacRole, rollback_of=rollback_of,
                         rollback_forced=rollback_forced, wave_size=body.waveSize, wave_pause_seconds=body.wavePauseSeconds,
                         wave_count=max(wave_of.values(), default=1), gate_max_new_alarms=body.gateMaxNewAlarms,
                         on_gate_failure=body.onGateFailure, invoker_id=invoker)
    db.add(job)
    db.flush()

    # schema check — cache hit or fetch via Configuration Schema Info (clause 8.3)
    job.schema_validated_at = datetime.datetime.now(datetime.UTC)
    job.status = WRITE_CONFIG_JOB_FSM.fire(JobState.PENDING, JobEvent.PRECHECK_PASS)
    db.flush()

    # MGT-5.2: every sub-change exists from the start, PENDING, with its place in the request and its wave; a wave dispatches its own.
    # HISTORY.md §7 item 3: `operation` is RFC 6241 section 7.2's real edit-config attribute — a delete/remove legitimately carries no
    # attributeChanges at all, so this does not assume the key is always present the way a merge-only model could.
    for index, change in enumerate(body.changes):
        db.add(WriteConfigSubChange(job_id=job.job_id, managed_element_ref=change["managedElementRef"],
                                     managed_function_ref=change.get("managedFunctionRef"),
                                     attribute_changes=change.get("attributeChanges", {}), operation=change.get("operation", "merge"),
                                     status="PENDING", position=index, wave=wave_of[change["managedElementRef"]]))
    db.flush()
    _advance(db, job)
    db.commit()
    return _job_summary(job)


def _job_summary(job: WriteConfigJob) -> dict:
    return {"jobId": str(job.job_id), "status": job.status, "wave": job.current_wave, "waveCount": job.wave_count,
            "haltedReason": job.halted_reason}


def _wave_rows(db: Session, job: WriteConfigJob, wave: int) -> list[WriteConfigSubChange]:
    return list(db.scalars(select(WriteConfigSubChange).where(WriteConfigSubChange.job_id == job.job_id, WriteConfigSubChange.wave == wave)
                           .order_by(WriteConfigSubChange.position)).all())


def _dispatch_wave(db: Session, job: WriteConfigJob, rows: list[WriteConfigSubChange]) -> None:
    """Dispatch the sub-changes of one wave and record each outcome on its row (and its snapshot)."""
    changes = [{"managedElementRef": r.managed_element_ref, "managedFunctionRef": r.managed_function_ref,
                "attributeChanges": r.attribute_changes, "operation": r.operation} for r in rows]
    # PR-SB-1.10: the sub-changes of one element behind a `?datastore=candidate` endpoint are one candidate transaction (lock once, every
    # edit, one commit): they all take effect or none does. A lone sub-change for an element is the same transaction of one.
    checks = [_dispatch_blocker(db, c) for c in changes]
    members: dict[str, list[int]] = {}
    for index, (blocker, me, endpoint) in enumerate(checks):
        key = _transaction_group_key(me, endpoint) if blocker is None else None
        if key is not None:
            members.setdefault(key, []).append(index)
    grouped = {i: key for key, indexes in members.items() if len(indexes) > 1 for i in indexes}
    group_outcomes: dict[int, tuple] = {}

    for index, (row, change) in enumerate(zip(rows, changes)):
        attribute_changes, operation = change["attributeChanges"], change["operation"]
        blocker, me, endpoint = checks[index]
        if blocker is not None:
            row.status, row.rejection_reason = "REJECTED", blocker
            continue
        ssh_options = _ssh_options(db, endpoint)
        if index in grouped:
            if index not in group_outcomes:
                indexes = members[grouped[index]]
                befores = {i: (_capture_before(me, endpoint, changes[i], changes[i]["attributeChanges"], ssh_options)
                               if CM_SNAPSHOTS else (None, None)) for i in indexes}
                outcomes = _dispatch_group_with_retries(endpoint.adaptor_uri, [changes[i] for i in indexes], str(job.job_id), ssh_options)
                group_outcomes.update({i: (*befores[i], *outcome) for i, outcome in zip(indexes, outcomes)})
            before, before_error, applied, reason, attempts, detail = group_outcomes[index]
        else:
            before, before_error = (_capture_before(me, endpoint, change, attribute_changes, ssh_options) if CM_SNAPSHOTS else (None, None))
            applied, reason, attempts, detail = _dispatch_with_retries(endpoint.adaptor_uri, change, attribute_changes,
                                                               str(job.job_id), operation, me.o1_protocol, endpoint.transport,
                                                                       ssh_options)
        if not applied and attempts > 1:
            _raise_dispatch_alarm(db, job.job_id, change, reason, attempts)
        row.status, row.rejection_reason, row.rejection_detail, row.attempts = ("APPLIED" if applied else "REJECTED"), reason, detail, attempts
        if CM_SNAPSHOTS:
            db.flush()                         # the snapshot's foreign key needs its sub-change row to exist first (Postgres enforces it)
            db.add(CMSnapshot(sub_change_id=row.id, job_id=job.job_id, managed_element_ref=row.managed_element_ref,
                              managed_function_ref=row.managed_function_ref, operation=operation, before=before,
                              before_error=before_error,
                              after=attribute_changes if applied and operation not in ("delete", "remove") else None))
    db.flush()


# MGT-5.3: the health gate hook. Each gate looks at the wave that just ran and returns why it fails, or None. A gate that reads KPIs
# (MGT-11) is another function in this list.
def _gate_rejections(db: Session, job: WriteConfigJob, rows: list[WriteConfigSubChange], started: datetime.datetime) -> str | None:
    rejected = [r for r in rows if r.status == "REJECTED"]
    if rejected:
        first = rejected[0]
        return (f"{len(rejected)} sub-change(s) of wave {rows[0].wave} were rejected (first: "
                f"{first.managed_function_ref or first.managed_element_ref}: {first.rejection_reason})")
    return None


def _gate_alarms(db: Session, job: WriteConfigJob, rows: list[WriteConfigSubChange], started: datetime.datetime) -> str | None:
    elements = {r.managed_element_ref for r in rows}
    raised = db.scalar(select(func.count()).select_from(Alarm).where(
        Alarm.managed_element_ref.in_(elements), Alarm.raised_at >= started, Alarm.severity.in_(("critical", "major")))) or 0
    if raised > job.gate_max_new_alarms:
        return f"{raised} new critical or major alarm(s) on the elements of wave {rows[0].wave} (limit {job.gate_max_new_alarms})"
    return None


HEALTH_GATES = [_gate_rejections, _gate_alarms]


def _finish(db: Session, job: WriteConfigJob) -> None:
    statuses = [sc.status for sc in db.scalars(select(WriteConfigSubChange).where(WriteConfigSubChange.job_id == job.job_id)).all()]
    job.status = WRITE_CONFIG_JOB_FSM.fire(JobState(job.status), aggregate_event(statuses))
    job.next_wave_at = None


def _halt(db: Session, job: WriteConfigJob, reason: str, detail: str | None, next_at: datetime.datetime | None = None) -> None:
    job.status = WRITE_CONFIG_JOB_FSM.fire(JobState(job.status), JobEvent.HALT)
    job.halted_reason, job.halted_detail, job.next_wave_at = reason, detail, next_at
    log.warning("config job %s halted after wave %s of %s: %s %s", job.job_id, job.current_wave, job.wave_count, reason, detail or "")


def _advance(db: Session, job: WriteConfigJob) -> None:
    """Run the waves of a job from the next one until it ends or halts (MGT-5.2 to 5.5)."""
    while True:
        wave = job.current_wave + 1
        started = datetime.datetime.now(datetime.UTC)
        rows = _wave_rows(db, job, wave)
        _dispatch_wave(db, job, rows)
        job.current_wave = wave
        db.flush()
        if wave >= job.wave_count:
            _finish(db, job)
            return
        failure = next((f for f in (gate(db, job, rows, started) for gate in HEALTH_GATES) if f), None)
        if failure:
            if job.on_gate_failure == "revert":
                _auto_revert(db, job, failure)
            else:
                _halt(db, job, "GATE_FAILED", failure)
            return
        if job.wave_pause_seconds > 0:
            _halt(db, job, "WAVE_PAUSE", None, datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=job.wave_pause_seconds))
            return


def _auto_revert(db: Session, job: WriteConfigJob, failure: str) -> None:
    """MGT-5.5: undo what the waves so far applied, with the rollback of MGT-1.6 (a new job, the same checks, the changed-since guard). If
    the revert cannot be made safely, or does not complete, the job halts and says so: nothing is left half-undone in silence."""
    changes, expected, problems = _rollback_plan(db, job)
    changed = _changed_since(db, expected) if not problems else []
    if problems or changed:
        why = "; ".join(problems) if problems else f"{len(changed)} value(s) changed since the waves wrote them"
        _halt(db, job, "REVERT_REFUSED", f"{failure}; not reverted: {why}")
        return
    db.flush()
    undo = _execute_write(WriteConfigRequest(requestedBy=job.requested_by, accessScope=job.scope, msacRole=job.msac_role, changes=changes),
                          db, rollback_of=job.job_id)
    if undo["status"] != "COMPLETED":
        _halt(db, job, "REVERT_REFUSED", f"{failure}; the revert job {undo['jobId']} ended {undo['status']}")
        return
    for row in db.scalars(select(WriteConfigSubChange).where(WriteConfigSubChange.job_id == job.job_id)).all():
        if row.status == "APPLIED":
            row.status = "REVERTED"
        elif row.status == "PENDING":
            row.status, row.rejection_reason = "REJECTED", "WAVE_NOT_RUN"
    job.halted_reason, job.halted_detail = "GATE_FAILED", f"{failure}; reverted by job {undo['jobId']}"
    _finish(db, job)


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
    client = _o1_client(me.o1_protocol, endpoint.transport)
    if client is None:
        raise framework_error(FrameworkError.PROTOCOL_NOT_SUPPORTED,
                              detail=f"{managed_element_ref} is provisioned for {me.o1_protocol}, which has no client")
    attributes = client[1](endpoint.adaptor_uri, managed_element_ref, message_id=str(uuid.uuid4()),
                           managed_function_ref=managed_function_ref, **_ssh_options(db, endpoint))
    if attributes is None:
        raise framework_error(FrameworkError.ENDPOINT_UNREACHABLE, detail=f"configuration read on {managed_element_ref} failed")
    return {"managedElementRef": managed_element_ref, "managedFunctionRef": managed_function_ref, "attributes": attributes}


@app.get("/managed-entities/{managed_element_ref}/config-history")
def read_configuration_history(managed_element_ref: str, managed_function_ref: str | None = None, limit: int = PageLimit,
                               offset: int = PageOffset, db: Session = Depends(get_session)):
    """MGT-1.4: what each dispatched write to this element replaced and wrote, newest first (`beforeError` says why a before
    image is missing). Read from `cm_snapshot`; a write rejected before dispatch has no row."""
    stmt = select(CMSnapshot).where(CMSnapshot.managed_element_ref == managed_element_ref)
    if managed_function_ref:
        stmt = stmt.where(CMSnapshot.managed_function_ref == managed_function_ref)
    page = paginate(db, stmt.order_by(CMSnapshot.created_at.desc(), CMSnapshot.snapshot_id), limit, offset)
    statuses = {sc.id: sc.status for sc in db.scalars(
        select(WriteConfigSubChange).where(WriteConfigSubChange.id.in_([r.sub_change_id for r in page["items"]]))).all()} if page["items"] else {}
    return {**page, "items": [
        {"snapshotId": str(r.snapshot_id), "jobId": str(r.job_id), "subChangeStatus": statuses.get(r.sub_change_id),
         "managedElementRef": r.managed_element_ref, "managedFunctionRef": r.managed_function_ref, "operation": r.operation,
         "before": r.before, "after": r.after, "beforeError": r.before_error, "createdAt": as_utc(r.created_at).isoformat()}
        for r in page["items"]]}


def _snapshot_or_404(db: Session, managed_element_ref: str, snapshot_id: uuid.UUID) -> CMSnapshot:
    row = db.get(CMSnapshot, snapshot_id)
    if row is None or row.managed_element_ref != managed_element_ref:
        raise framework_error(FrameworkError.CM_SNAPSHOT_NOT_FOUND, detail=f"no snapshot {snapshot_id} of {managed_element_ref}")
    return row


def _image(row: CMSnapshot) -> dict:
    """The values of the attributes a snapshot's write touched, as they stood just after it: what was there before, with what the NF
    acknowledged laid over it (a write that was not applied leaves the before image)."""
    image = dict(row.before or {})
    if row.after is not None:
        image.update(row.after)
    return image


@app.get("/managed-entities/{managed_element_ref}/config-history/diff")
def diff_configuration_snapshots(managed_element_ref: str, from_snapshot: uuid.UUID, to_snapshot: uuid.UUID, db: Session = Depends(get_session)):
    """MGT-1.5: how the attributes two snapshots of one managed object touched differ. Each snapshot's image is `before` with `after` laid
    over it (`_image`); the diff is over the attributes either image holds. Only attributes a write named are ever known, so an attribute
    neither snapshot touched is not reported as unchanged."""
    first, second = (_snapshot_or_404(db, managed_element_ref, from_snapshot), _snapshot_or_404(db, managed_element_ref, to_snapshot))
    if first.managed_function_ref != second.managed_function_ref:
        raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED,
                              detail=f"the snapshots are of different managed functions ({first.managed_function_ref!r} and {second.managed_function_ref!r})")
    before_image, after_image = _image(first), _image(second)
    changed = [{"attribute": k, "from": before_image[k], "to": after_image[k]}
               for k in sorted(before_image.keys() & after_image.keys()) if str(before_image[k]) != str(after_image[k])]
    return {"managedElementRef": managed_element_ref, "managedFunctionRef": first.managed_function_ref,
            "fromSnapshot": str(first.snapshot_id), "toSnapshot": str(second.snapshot_id),
            "changed": changed,
            "onlyInFrom": {k: before_image[k] for k in sorted(before_image.keys() - after_image.keys())},
            "onlyInTo": {k: after_image[k] for k in sorted(after_image.keys() - before_image.keys())}}


@app.post("/config-history/purge")
def purge_configuration_history(older_than_days: int | None = None, db: Session = Depends(get_session)):
    """MGT-1.8: delete the snapshots older than `older_than_days` (default `RAN_NF_OAM_CM_SNAPSHOT_RETENTION_DAYS`; 422 when neither is set, so
    a purge never runs with no age). The jobs and their sub-changes stay; a job whose snapshots are gone can no longer be rolled back."""
    days = older_than_days if older_than_days is not None else CM_SNAPSHOT_RETENTION_DAYS
    if days <= 0:
        raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED,
                              detail="older_than_days is required (and positive) unless RAN_NF_OAM_CM_SNAPSHOT_RETENTION_DAYS is set")
    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)
    deleted = db.execute(delete(CMSnapshot).where(CMSnapshot.created_at < cutoff)).rowcount
    db.commit()
    return {"deleted": deleted, "olderThan": cutoff.isoformat()}


class RollbackRequest(BaseModel):
    requestedBy: str
    accessScope: str | None = None            # default: the scope of the job being undone
    msacRole: str | None = None
    force: bool = False                       # MGT-1.7: go ahead although values changed since the job wrote them
    dryRun: bool = False


def _rollback_plan(db: Session, job: WriteConfigJob, elements: set[str] | None = None) -> tuple[list[dict], dict, list[str]]:
    """(changes that undo the job, in reverse order; the values each target should hold now if nothing touched it since (None: absent);
    problems that make an undo impossible). Only sub-changes that were applied, and only what the snapshots recorded, can be undone. `elements`
    limits the plan to those elements (a revert on a KPI regression undoes only where the KPI regressed)."""
    stmt = (select(CMSnapshot).join(WriteConfigSubChange, WriteConfigSubChange.id == CMSnapshot.sub_change_id)
            .where(CMSnapshot.job_id == job.job_id, WriteConfigSubChange.status == "APPLIED").order_by(CMSnapshot.created_at, CMSnapshot.snapshot_id))
    if elements is not None:
        stmt = stmt.where(CMSnapshot.managed_element_ref.in_(elements))
    rows = db.execute(stmt).scalars().all()
    problems: list[str] = []
    if not rows:
        return [], {}, ["the job applied nothing that has a snapshot (nothing was applied, snapshots are off, or they were purged)"]
    expected: dict[tuple, dict | None] = {}
    undo: list[dict] = []
    for row in rows:
        target = (row.managed_element_ref, row.managed_function_ref)
        label = row.managed_function_ref or row.managed_element_ref
        base = {"managedElementRef": row.managed_element_ref, **({"managedFunctionRef": row.managed_function_ref} if row.managed_function_ref else {})}
        after = row.after or {}
        if row.operation in ("merge", "replace"):
            if row.before is None:
                problems.append(f"{label}: no before image ({row.before_error or 'not recorded'})")
                continue
            absent = sorted(k for k in after if row.before.get(k) is None)       # the before image holds None for an attribute that was not there
            if absent:
                problems.append(f"{label}: {', '.join(absent)} had no value before the write, which cannot be restored")
                continue
            undo.append({**base, "operation": "merge", "attributeChanges": {k: row.before[k] for k in after}})
            expected[target] = {**(expected.get(target) or {}), **after}
        elif row.operation == "create":
            undo.append({**base, "operation": "delete", "attributeChanges": {}})
            expected[target] = {**(expected.get(target) or {}), **after}
        elif row.operation in ("delete", "remove"):
            if not row.before:
                problems.append(f"{label}: the deleted object's values were not recorded")
                continue
            undo.append({**base, "operation": "create", "attributeChanges": dict(row.before)})
            expected[target] = None
        else:
            problems.append(f"{label}: no way to undo a {row.operation!r} write")
    undo.reverse()
    return undo, expected, problems


def _changed_since(db: Session, expected: dict) -> list[dict]:
    """MGT-1.7: read each target now and list what no longer matches what the job left there."""
    found = []
    for (element, function), want in expected.items():
        me = db.get(ManagedEntity, element)
        endpoint = db.get(O1AdaptorEndpoint, me.o1_adaptor_endpoint_id) if me and me.o1_adaptor_endpoint_id else None
        client = _o1_client(me.o1_protocol, endpoint.transport) if endpoint is not None else None
        if client is None:
            found.append({"managedElementRef": element, "managedFunctionRef": function, "attribute": None, "expected": want, "actual": None,
                          "error": "the element has no reachable O1 adaptor to read"})
            continue
        try:
            current = client[1](endpoint.adaptor_uri, element, message_id=str(uuid.uuid4()), managed_function_ref=function, **_ssh_options(db, endpoint))
        except Exception:                                          # noqa: BLE001 - a client bug must not become a silent "unchanged"
            current = None
        if current is None:
            found.append({"managedElementRef": element, "managedFunctionRef": function, "attribute": None, "expected": want, "actual": None,
                          "error": "the current values could not be read"})
        elif want is None:
            if current:
                found.append({"managedElementRef": element, "managedFunctionRef": function, "attribute": None, "expected": None, "actual": current})
        else:
            found.extend({"managedElementRef": element, "managedFunctionRef": function, "attribute": k, "expected": v, "actual": current.get(k)}
                         for k, v in want.items() if str(current.get(k)) != str(v))
    return found


@app.post("/config-jobs/{job_id}/rollback", status_code=202)
@idempotent("ran-nf-oam", status_code=202)
def rollback_configuration_job(job_id: uuid.UUID, body: RollbackRequest, request: Request, db: Session = Depends(get_session)):
    """MGT-1.6: undo a job with a new write job built from its snapshots (reverse order, the recorded before values), which goes through MSAC,
    the schema check and dispatch like any other write: `requestedBy` is the actor and the new job names `rollbackOf`. MGT-1.7: if what the job
    wrote has been changed since, 409 `CONFIG_CHANGED_SINCE` unless `force`; `dryRun` returns the plan and the differences without writing."""
    job = db.get(WriteConfigJob, job_id)
    if job is None:
        raise framework_error(FrameworkError.CONFIG_JOB_NOT_FOUND, detail=f"no configuration job {job_id}")
    changes, expected, problems = _rollback_plan(db, job)
    if problems:
        raise framework_error(FrameworkError.ROLLBACK_NOT_POSSIBLE, detail="; ".join(problems))
    changed = _changed_since(db, expected)
    if body.dryRun:
        return JSONResponse(status_code=200, content={"dryRun": True, "rollbackOf": str(job_id), "changes": changes, "changedSince": changed,
                                                      "status": "CHANGED_SINCE" if changed else "VALIDATED"})
    if changed and not body.force:
        first = changed[0]
        raise framework_error(FrameworkError.CONFIG_CHANGED_SINCE,
                              detail=f"{len(changed)} value(s) differ from what job {job_id} wrote, for example "
                                     f"{first['managedFunctionRef'] or first['managedElementRef']} {first['attribute']}: expected {first['expected']!r}, "
                                     f"found {first['actual']!r}; send force=true to restore anyway")
    request_body = WriteConfigRequest(requestedBy=body.requestedBy, accessScope=body.accessScope or job.scope, msacRole=body.msacRole, changes=changes)
    result = _execute_write(request_body, db, rollback_of=job_id, rollback_forced=bool(changed))
    return {**result, "rollbackOf": str(job_id), "forced": bool(changed)}


class KpiCheckRequest(BaseModel):
    requestedBy: str
    kpi: str
    baselineMinutes: int = Field(default=60, ge=1, le=10080)             # the KPI over this long before the job ran
    observationMinutes: int = Field(default=60, ge=1, le=10080)          # ... and over this long from when it ran
    maxRegressionPercent: float = Field(default=10.0, ge=0)
    direction: Literal["higher", "lower"] = "higher"                      # which way is better: a drop (higher) or a rise (lower) is a regression
    minSamples: int = Field(default=1, ge=1)                              # per window and element: fewer is INSUFFICIENT_DATA, never a verdict
    revert: bool = False
    accessScope: str | None = None
    msacRole: str | None = None
    force: bool = False                                                   # revert although values changed since (MGT-1.7)


@app.post("/config-jobs/{job_id}/kpi-check")
def check_configuration_job_kpi(job_id: uuid.UUID, body: KpiCheckRequest, db: Session = Depends(get_session)):
    """AI-10.5: did a KPI regress where this job wrote? For each element the job applied changes to, the KPI over the window before the job
    (`baselineMinutes`) is compared with the KPI over the window from the job on (`observationMinutes`); a worse result than
    `maxRegressionPercent` is REGRESSED. With `revert`, the regressed elements are rolled back with the rollback of MGT-1.6 (a new job,
    MSAC, the changed-since guard); where the data is too thin the verdict is INSUFFICIENT_DATA and nothing is reverted. This is a check
    to call, by an rApp, the SMO's autonomy or a scheduler, once the observation window has some data: nothing here runs on its own."""
    job = db.get(WriteConfigJob, job_id)
    if job is None:
        raise framework_error(FrameworkError.CONFIG_JOB_NOT_FOUND, detail=f"no configuration job {job_id}")
    definition = _kpi_or_404(db, body.kpi)
    anchor = as_utc(job.schema_validated_at) if job.schema_validated_at else datetime.datetime.now(datetime.UTC)
    before_window = (anchor - datetime.timedelta(minutes=body.baselineMinutes), anchor)
    after_window = (anchor, anchor + datetime.timedelta(minutes=body.observationMinutes))
    elements = sorted({r.managed_element_ref for r in db.scalars(select(WriteConfigSubChange).where(
        WriteConfigSubChange.job_id == job_id, WriteConfigSubChange.status == "APPLIED")).all()})
    results = []
    for element in elements:
        base = kpi.compute(db, definition, *before_window, "all", element)["items"][0]
        seen = kpi.compute(db, definition, *after_window, "all", element)["items"][0]
        entry = {"managedElementRef": element, "baseline": base["value"], "observed": seen["value"],
                 "baselineSamples": base["samples"], "observedSamples": seen["samples"], "changePercent": None}
        if base["samples"] < body.minSamples or seen["samples"] < body.minSamples or base["value"] is None or seen["value"] is None:
            entry["verdict"], entry["reason"] = "INSUFFICIENT_DATA", "NOT_ENOUGH_SAMPLES_OR_UNDEFINED"
        elif base["value"] == 0:
            entry["verdict"], entry["reason"] = "INSUFFICIENT_DATA", "BASELINE_ZERO"
        else:
            worse = (base["value"] - seen["value"]) if body.direction == "higher" else (seen["value"] - base["value"])
            entry["changePercent"] = round(-100.0 * worse / abs(base["value"]), 4)         # signed like the KPI: negative is a drop
            entry["verdict"] = "REGRESSED" if 100.0 * worse / abs(base["value"]) > body.maxRegressionPercent else "OK"
        results.append(entry)
    regressed = [r["managedElementRef"] for r in results if r["verdict"] == "REGRESSED"]
    verdict = "REGRESSED" if regressed else ("INSUFFICIENT_DATA" if any(r["verdict"] == "INSUFFICIENT_DATA" for r in results) or not results else "OK")
    answer = {"jobId": str(job_id), "kpi": body.kpi, "verdict": verdict, "elements": results, "reverted": False, "revertJobId": None}
    if regressed and body.revert:
        changes, expected, problems = _rollback_plan(db, job, set(regressed))
        if problems:
            raise framework_error(FrameworkError.ROLLBACK_NOT_POSSIBLE, detail="; ".join(problems))
        changed = _changed_since(db, expected)
        if changed and not body.force:
            raise framework_error(FrameworkError.CONFIG_CHANGED_SINCE,
                                  detail=f"{len(changed)} value(s) differ from what job {job_id} wrote; the KPI regressed on {', '.join(regressed)} "
                                         "but the revert would overwrite a later change; send force=true to restore anyway")
        undo = _execute_write(WriteConfigRequest(requestedBy=body.requestedBy, accessScope=body.accessScope or job.scope, msacRole=body.msacRole,
                                                 changes=changes), db, rollback_of=job_id, rollback_forced=bool(changed))
        answer.update(reverted=undo["status"] == "COMPLETED", revertJobId=undo["jobId"], revertStatus=undo["status"])
    return answer


class WaveActionRequest(BaseModel):
    requestedBy: str
    force: bool = False                       # continue: go on although the pause has not elapsed


def _halted_job(db: Session, job_id: uuid.UUID, event: JobEvent) -> WriteConfigJob:
    job = db.get(WriteConfigJob, job_id)
    if job is None:
        raise framework_error(FrameworkError.CONFIG_JOB_NOT_FOUND, detail=f"no configuration job {job_id}")
    if job.status != JobState.HALTED:
        raise illegal_transition_error(IllegalTransition(JobState(job.status), event), f"configuration job {job_id}")
    return job


def _resume(db: Session, job: WriteConfigJob) -> dict:
    job.status = WRITE_CONFIG_JOB_FSM.fire(JobState.HALTED, JobEvent.RESUME)
    job.halted_reason = job.halted_detail = job.next_wave_at = None
    db.flush()
    _advance(db, job)
    db.commit()
    return _job_summary(job)


@app.post("/config-jobs/{job_id}/continue", status_code=202)
def continue_configuration_job(job_id: uuid.UUID, body: WaveActionRequest, db: Session = Depends(get_session)):
    """MGT-5.4: run the next wave of a halted job. A job held by its wave pause goes on only once the pause has elapsed, unless `force`; after a
    failed gate or an operator's halt, calling this is the operator's decision to go on."""
    job = _halted_job(db, job_id, JobEvent.RESUME)
    if job.halted_reason == "WAVE_PAUSE" and job.next_wave_at and as_utc(job.next_wave_at) > datetime.datetime.now(datetime.UTC) and not body.force:
        raise framework_error(FrameworkError.WAVE_PAUSE_NOT_ELAPSED,
                              detail=f"the pause between waves ends at {as_utc(job.next_wave_at).isoformat()}; send force=true to go on now")
    log.info("config job %s continued by %s after %s", job_id, body.requestedBy, job.halted_reason)
    return _resume(db, job)


@app.post("/config-jobs/{job_id}/halt")
def halt_configuration_job(job_id: uuid.UUID, body: WaveActionRequest, db: Session = Depends(get_session)):
    """MGT-5.4: stop a job that is waiting between waves from going on by itself (a pause becomes an operator halt). A job already halted for
    another reason stays as it is. A job is only ever between waves while HALTED, so any other state is 409."""
    job = _halted_job(db, job_id, JobEvent.HALT)
    if job.halted_reason == "WAVE_PAUSE":
        job.halted_reason, job.halted_detail, job.next_wave_at = "OPERATOR_HALT", f"halted by {body.requestedBy}", None
        db.commit()
    return _job_summary(job)


@app.post("/config-jobs/{job_id}/abort")
def abort_configuration_job(job_id: uuid.UUID, body: WaveActionRequest, db: Session = Depends(get_session)):
    """MGT-5.4: end a halted job here. The waves that have run stay as they are (undo them with the rollback route); the waves that have not run
    are rejected `WAVE_NOT_RUN`, and the job ends `PARTIAL_SUCCESS` or `FAILED` from what it did."""
    job = _halted_job(db, job_id, JobEvent.AGGREGATE_MIXED)
    for row in db.scalars(select(WriteConfigSubChange).where(WriteConfigSubChange.job_id == job_id, WriteConfigSubChange.status == "PENDING")).all():
        row.status, row.rejection_reason = "REJECTED", "WAVE_NOT_RUN"
    db.flush()
    job.halted_detail = f"aborted by {body.requestedBy} ({job.halted_reason})"
    _finish(db, job)
    db.commit()
    return _job_summary(job)


@app.post("/config-jobs/advance-due")
def advance_due_configuration_jobs(db: Session = Depends(get_session)):
    """MGT-5.1: for a scheduler. Runs the next wave of every job whose pause between waves has elapsed; a job halted for any other reason is left
    for an operator. Returns what each advanced job did."""
    now = datetime.datetime.now(datetime.UTC)
    due = db.scalars(select(WriteConfigJob).where(WriteConfigJob.status == JobState.HALTED, WriteConfigJob.halted_reason == "WAVE_PAUSE",
                                                  WriteConfigJob.next_wave_at <= now).order_by(WriteConfigJob.next_wave_at)).all()
    return {"advanced": [_resume(db, job) for job in due]}


# ---------------------------------------------------------------- KPIs (PR-MGT-11)


class KpiCounter(BaseModel):
    counter: str
    variable: str | None = None
    aggregation: Literal["sum", "avg", "min", "max", "last", "count"] = "sum"


class KpiDefinitionRequest(BaseModel):
    formula: str
    counters: list[KpiCounter] | None = None
    unit: str | None = None
    description: str | None = None


def _kpi_view(row: KpiDefinition) -> dict:
    return {"name": row.name, "formula": row.formula, "counters": row.counters, "unit": row.unit, "description": row.description}


def _kpi_or_404(db: Session, name: str) -> KpiDefinition:
    row = db.get(KpiDefinition, name)
    if row is None:
        raise framework_error(FrameworkError.KPI_NOT_FOUND, detail=f"no KPI {name!r}")
    return row


@app.put("/kpi-definitions/{name}")
def define_kpi(name: str, body: KpiDefinitionRequest, db: Session = Depends(get_session)):
    """MGT-11.1: create or replace a KPI: a formula over named counters (`kpi_formula.py`: arithmetic, comparisons, a few functions, nothing else)
    and the counter table that says which PM counter feeds which variable and how its samples are combined. Refused (422) when the formula is not
    acceptable or the table does not match it."""
    if not kpi.NAME.match(name):
        raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED, detail="a KPI name starts with a letter and uses letters, digits, '_', '.', '-' (64 at most)")
    try:
        table = kpi.normalise_counters(body.formula, [c.model_dump() for c in body.counters] if body.counters else None)
    except kpi_formula.FormulaError as exc:
        raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED, detail=str(exc)) from None
    row = db.get(KpiDefinition, name)
    if row is None:
        row = KpiDefinition(name=name)
        db.add(row)
    row.formula, row.counters, row.unit, row.description = body.formula.strip(), table, body.unit, body.description
    db.commit()
    return _kpi_view(row)


@app.get("/kpi-definitions")
def list_kpi_definitions(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    page = paginate(db, select(KpiDefinition).order_by(KpiDefinition.name), limit, offset)
    return {**page, "items": [_kpi_view(r) for r in page["items"]]}


@app.get("/kpi-definitions/{name}")
def read_kpi_definition(name: str, db: Session = Depends(get_session)):
    return _kpi_view(_kpi_or_404(db, name))


@app.delete("/kpi-definitions/{name}", status_code=204)
def delete_kpi_definition(name: str, db: Session = Depends(get_session)):
    db.delete(_kpi_or_404(db, name))
    db.commit()
    return Response(status_code=204)


class RAppLimitRequest(BaseModel):
    maxConfigJobsPerHour: int = Field(ge=1, le=100_000)


def _limit_view(row: RAppLimit) -> dict:
    return {"invokerId": row.invoker_id, "maxConfigJobsPerHour": row.max_config_jobs_per_hour, "updatedAt": row.updated_at}


def _limit_or_404(db: Session, invoker: str) -> RAppLimit:
    row = db.get(RAppLimit, invoker)
    if row is None:
        raise framework_error(FrameworkError.RAPP_LIMIT_NOT_FOUND, detail=f"no limit is set for {invoker}")
    return row


def _not_own_limit(request: Request, invoker: str) -> None:
    """An rApp may not change its own limit. (Any valid token reaches every route behind R1 today; this at least keeps a caller from lifting its own cap.)"""
    if invoker_id(request) == invoker:
        raise framework_error(FrameworkError.RAPP_LIMIT_SELF_CHANGE, detail="a caller cannot change its own limit")


@app.put("/rapp-limits/{invoker_id_}")
def set_rapp_limit(invoker_id_: str, body: RAppLimitRequest, request: Request, db: Session = Depends(get_session)):
    """AI-10.1/10.2: set what one rApp (by its OAuth client id) may do here. Called by rApp Management when the instance finishes bootstrapping,
    with the limits its manifest declares."""
    _not_own_limit(request, invoker_id_)
    row = db.get(RAppLimit, invoker_id_)
    if row is None:
        row = RAppLimit(invoker_id=invoker_id_)
        db.add(row)
    row.max_config_jobs_per_hour = body.maxConfigJobsPerHour
    db.commit()
    return _limit_view(row)


@app.get("/rapp-limits/{invoker_id_}")
def read_rapp_limit(invoker_id_: str, db: Session = Depends(get_session)):
    """The limit set for an rApp, and how many config jobs it has started in the last hour."""
    row = _limit_or_404(db, invoker_id_)
    since = datetime.datetime.now(datetime.UTC) - RATE_WINDOW
    used = db.scalar(select(func.count()).select_from(WriteConfigJob).where(WriteConfigJob.invoker_id == invoker_id_, WriteConfigJob.created_at >= since)) or 0
    return {**_limit_view(row), "configJobsLastHour": used}


@app.delete("/rapp-limits/{invoker_id_}", status_code=204)
def delete_rapp_limit(invoker_id_: str, request: Request, db: Session = Depends(get_session)):
    _not_own_limit(request, invoker_id_)
    db.delete(_limit_or_404(db, invoker_id_))
    db.commit()
    return Response(status_code=204)


@app.get("/kpis/{name}")
def compute_kpi(name: str, from_time: datetime.datetime, to_time: datetime.datetime | None = None, group_by: Literal[
                "cell", "element", "sectorGroup", "incidentZone", "all"] = "cell", managed_element_ref: str | None = None,
                cell_id: str | None = None, db: Session = Depends(get_session)):
    """MGT-11.3/11.4/11.5: the KPI over [from_time, to_time) (to_time: now), per cell, per element, per sector group or incident zone (the cell
    guards of the registry), or over everything asked for. A ratio is computed from the group's summed counters, not from its cells' ratios.
    `managed_element_ref` and `cell_id` narrow what is read. A group without data has a null `value` and a `reason`."""
    definition = _kpi_or_404(db, name)
    start = from_time if from_time.tzinfo else from_time.replace(tzinfo=datetime.UTC)
    end = (to_time if to_time is None or to_time.tzinfo else to_time.replace(tzinfo=datetime.UTC)) or datetime.datetime.now(datetime.UTC)
    if end <= start:
        raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED, detail="to_time must be after from_time")
    return kpi.compute(db, definition, start, end, group_by, managed_element_ref, cell_id)


@app.get("/config-jobs/{job_id}")
def query_write_config_job_status(job_id: uuid.UUID, db: Session = Depends(get_session)):
    job = db.get(WriteConfigJob, job_id)
    sub_changes = db.scalars(select(WriteConfigSubChange).where(WriteConfigSubChange.job_id == job_id).order_by(WriteConfigSubChange.position)).all()
    return {"jobId": str(job.job_id), "status": job.status, "requestedBy": job.requested_by,
            "rollbackOf": str(job.rollback_of) if job.rollback_of else None, "rollbackForced": job.rollback_forced,
            "waveSize": job.wave_size, "waveCount": job.wave_count, "currentWave": job.current_wave,
            "wavePauseSeconds": job.wave_pause_seconds, "onGateFailure": job.on_gate_failure, "gateMaxNewAlarms": job.gate_max_new_alarms,
            "haltedReason": job.halted_reason, "haltedDetail": job.halted_detail,
            "nextWaveAt": as_utc(job.next_wave_at).isoformat() if job.next_wave_at else None,
            "subChanges": [{"managedElementRef": sc.managed_element_ref, "wave": sc.wave, "managedFunctionRef": sc.managed_function_ref,
                            "operation": sc.operation, "status": sc.status, "rejectionReason": sc.rejection_reason,
                            "rejectionDetail": sc.rejection_detail, "attempts": sc.attempts} for sc in sub_changes]}


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


def _get_alarm(db: Session, alarm_id: uuid.UUID) -> Alarm:
    """MGT-8.1: an unknown alarm is a 404, not a 500 from `None.ack_state`."""
    alarm = db.get(Alarm, alarm_id)
    if alarm is None:
        raise framework_error(FrameworkError.ALARM_NOT_FOUND, detail=f"no such alarm {alarm_id}")
    return alarm


@app.patch("/alarms/{alarm_id}/ack")
def change_alarm_ack_state(alarm_id: uuid.UUID, new_state: Literal["ACKNOWLEDGED", "UNACKNOWLEDGED"], ack_user_id: str | None = None, db: Session = Depends(get_session)):
    """ackUserId (HISTORY.md §7, TS28111_FaultNrm.yaml's AlarmRecord) —
    who acknowledged it, never recorded before. alarmChangedTime (the
    spec's own "last mutated" timestamp, distinct from raised_at/
    cleared_at) updates here and in clear_alarm below, the two places
    this build actually mutates an existing alarm.
    """
    alarm = _get_alarm(db, alarm_id)
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
    alarm = _get_alarm(db, alarm_id)
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
    for subscription_id, consumer, sequence_no in targets:  # outbox rows, committed with the file and the new sequence numbers (PR-MSG-1.9)
        enqueue(db, consumer, {"href": "/ran-nf-oam/file-subscriptions", "notificationId": sequence_no,
                               "notificationType": "notifyFileReady", "eventTime": info["fileReadyTime"],
                               "sequenceNo": sequence_no, "subscriptionId": str(subscription_id),
                               "fileInfoList": [info]})
    db.commit()
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
             "protocolSupport": ep.protocol_support, "registeredVia": ep.registered_via, "transport": ep.transport, "credentialRef": ep.credential_ref, "healthStatus": ep.health_status,
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
