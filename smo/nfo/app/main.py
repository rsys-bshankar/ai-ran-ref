"""NFO SMOS (O2dms).

SMO Design v1.3 section 3.7, extended by NFO+FOCOM LLD sections 2, 4:
NFDeploymentDescriptor closes the gap where nfDeploymentDescriptorId
referenced nothing concrete, and Instantiate now calls FOCOM's inventory
first to resolve clusterId, rather than assuming the Phase 1 degenerate
single-cluster value implicitly. HISTORY.md §5 extends this
further: a real 7-state deployment lifecycle (statemachine.py), real
duplication/dependency guards on Instantiate (dms_lcm_nfdeployment.py's
_check_duplication/_check_dependencies), a resource-linkage object
(NFOCloudResource, the reference's NfOCloudVResource), and Heal/Scale
now actually drive state transitions instead of being pure stubs.
"""

import uuid
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.logconfig import install_logging
from smo_shared.metrics import install_metrics
from smo_shared.health import database_check, install_health, sme_token_check
from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.r1_client import R1Client
from smo_shared.statemachine import IllegalTransition
from smo_shared.openapi_security import apply_r1_gateway_security
from smo_shared.correlation import apply_correlation_id
from smo_shared.pagination import PageLimit, PageOffset, paginate
from smo_shared.versioning import install_concurrency_handler
from smo_shared.idempotency import idempotent

from .models import LCMOperation, NFDeployment, NFDeploymentDescriptor, NFOCloudResource
from .statemachine import DeploymentEvent, DeploymentState, NFO_FSM

app = FastAPI(title="NFO SMOS (O2dms)")
install_logging(app)  # structured JSON logs and one access-log line per request (PR-OBS-1)
install_metrics(app)  # /metrics and request count/latency series (PR-OBS-2)
install_concurrency_handler(app)  # a stale write (PR-ST-2) is a 409, not a 500
apply_r1_gateway_security(app)
apply_correlation_id(app)


install_health(app, checks=[database_check, sme_token_check])  # /live, /ready and the /health alias (PR-ST-7)


class InstantiateRequest(BaseModel):
    nfDeploymentDescriptorId: uuid.UUID
    name: str
    requiredResourceTypeId: str | None = None


class CreateDescriptorRequest(BaseModel):
    packageId: uuid.UUID | None = None
    name: str
    workloadTemplate: dict = {}
    requiredResourceTypeId: str | None = None


@app.post("/descriptors", status_code=201)
def create_descriptor(body: CreateDescriptorRequest, db: Session = Depends(get_session)):
    """CreateDescriptor — NFO+FOCOM LLD section 2: an NFDeploymentDescriptor
    derived from an onboarded package's TOSCA Definitions/, called from
    Onboarding's OnboardPackage flow once validation succeeds, closing the
    gap where nfDeploymentDescriptorId previously referenced nothing
    concrete. `packageId` is optional since Wave 2 (AI Platform Service
    Decomposition, docs/ARCHITECTURE.md (AIMgF)): AIMgF's own Runtime
    Lifecycle now also creates a descriptor per model runtime, and a
    model runtime has no onboarded ApplicationPackage behind it — every
    package-derived descriptor (Onboarding's own flow, unchanged) still
    always sets it.
    """
    descriptor = NFDeploymentDescriptor(
        package_id=body.packageId, name=body.name,
        required_resource_type_id=body.requiredResourceTypeId,
        workload_template=body.workloadTemplate,
    )
    db.add(descriptor)
    db.commit()
    return {"nfDeploymentDescriptorId": str(descriptor.nf_deployment_descriptor_id)}


@app.post("/deployments", status_code=202)
@idempotent("nfo", status_code=202)
def instantiate(body: InstantiateRequest, request: Request, db: Session = Depends(get_session)):
    """Instantiate — NFO+FOCOM LLD section 4: FOCOM's inventory is queried
    to resolve clusterId before the workload is placed, rather than the
    Phase 1 degenerate cluster being assumed implicitly. HISTORY.md §5: now also enforces the reference's own real guards before
    creating anything — _check_dependencies (the descriptor must exist)
    and _check_duplication (no two deployments may share a name, and a
    descriptor may only be deployed once).
    """
    if db.get(NFDeploymentDescriptor, body.nfDeploymentDescriptorId) is None:
        raise framework_error(FrameworkError.NFDEPLOYMENT_DESCRIPTOR_NOT_FOUND,
                               detail=f"no such NFDeploymentDescriptor: {body.nfDeploymentDescriptorId}")
    if db.scalar(select(NFDeployment).where(NFDeployment.name == body.name)) is not None:
        raise framework_error(FrameworkError.NFDEPLOYMENT_NAME_CONFLICT,
                               detail=f"NfDeployment with name {body.name} exists already")
    if db.scalar(select(NFDeployment).where(NFDeployment.nf_deployment_descriptor_id == body.nfDeploymentDescriptorId)) is not None:
        raise framework_error(FrameworkError.NFDEPLOYMENT_DESCRIPTOR_ALREADY_DEPLOYED,
                               detail=f"NfDeploymentDescriptor {body.nfDeploymentDescriptorId} already deployed")

    r1 = R1Client()
    inv_resp = r1.get("/focom/inventory", params={"resource_type": body.requiredResourceTypeId or ""})
    # HISTORY.md §7 item 8: FOCOM's /inventory reshaped toward the real
    # O2IMS OCloud schema — oCloudId, not the previously invented
    # clusterId. Same graceful fallback on any non-2xx response.
    cluster_id = inv_resp.json().get("oCloudId", "phase1-degenerate-cluster") if inv_resp.status_code == 200 else "phase1-degenerate-cluster"

    deployment = NFDeployment(
        nf_deployment_descriptor_id=body.nfDeploymentDescriptorId,
        name=body.name,
        cluster_id=cluster_id,
        required_resource_type_id=body.requiredResourceTypeId,
        state=DeploymentState.INITIAL,
    )
    db.add(deployment)
    db.flush()
    deployment.state = NFO_FSM.fire(DeploymentState.INITIAL, DeploymentEvent.INSTANTIATE)
    op = LCMOperation(nf_deployment_id=deployment.nf_deployment_id, operation_type="INSTANTIATE", status="IN_PROGRESS")
    db.add(op)

    # Phase 1: meaningfully real (docker run --gpus passthrough when
    # requiredResourceTypeId is set) — elided here; deployment moves to
    # RUNNING once the container starts.
    deployment.state = NFO_FSM.fire(deployment.state, DeploymentEvent.INSTANTIATE_COMPLETE)
    op.status = "COMPLETED"
    db.add(NFOCloudResource(nf_deployment_id=deployment.nf_deployment_id, resource_ref=cluster_id))
    db.commit()
    return {"nfDeploymentId": str(deployment.nf_deployment_id), "state": deployment.state, "clusterId": cluster_id}


@app.delete("/deployments/{nf_deployment_id}", status_code=204)
def terminate(nf_deployment_id: uuid.UUID, async_uninstall: bool = False, db: Session = Depends(get_session)):
    """Terminate — mirrors the reference's own state dispatch
    (lcm_nfdeployment_uninstall, dms_lcm_nfdeployment.py): INITIAL/
    ABNORMAL go straight to DELETING (no chart was ever installed, or it's
    already broken); every in-flight/running state is uninstalled first
    (TERMINATING); calling Terminate again on an already-TERMINATING
    deployment is a no-op (the reference's own `elif ... Uninstalling:
    pass`); calling it on one already DELETING flips it to ABNORMAL instead
    of deleting twice — the reference's own defensive catch-all.

    OI-3-nfo-abnormal: by default the Phase 1 elision completes the
    uninstall and the deletion at once — TERMINATING -> DELETING -> the
    record removed — as every SMO caller (rApp Management, AIMgF, SO SMOS)
    expects: 204, and the descriptor is free to deploy again.
    `async_uninstall=true` instead leaves the deployment TERMINATING (or
    DELETING) with its TERMINATE operation IN_PROGRESS and answers 202: the
    deployment manager reports how it ends through
    `POST /deployments/{id}/dms-notifications`, which is how DELETING and
    ABNORMAL are reached and observed.
    """
    d = db.get(NFDeployment, nf_deployment_id)
    if d is None:
        return
    was_terminating = d.state == DeploymentState.TERMINATING
    new_state = NFO_FSM.fire(DeploymentState(d.state), DeploymentEvent.TERMINATE)
    if new_state == DeploymentState.ABNORMAL:
        db.add(LCMOperation(nf_deployment_id=nf_deployment_id, operation_type="TERMINATE", status="FAILED"))
        d.state = new_state
        d.abnormal_reason = "TERMINATE while DELETING"
        db.commit()
        return _accepted(d) if async_uninstall else None
    if was_terminating:
        return _accepted(d) if async_uninstall else None
    d.state = new_state
    if async_uninstall:
        db.add(LCMOperation(nf_deployment_id=nf_deployment_id, operation_type="TERMINATE", status="IN_PROGRESS"))
        db.commit()
        return _accepted(d)
    # Phase 1 elision: the uninstall (Helm) and the resource release both
    # complete synchronously.
    if d.state == DeploymentState.TERMINATING:
        d.state = NFO_FSM.fire(DeploymentState(d.state), DeploymentEvent.UNINSTALL_COMPLETE)
    db.add(LCMOperation(nf_deployment_id=nf_deployment_id, operation_type="TERMINATE", status="COMPLETED"))
    _remove_deployment(db, d)
    db.commit()


def _accepted(d: NFDeployment) -> JSONResponse:
    return JSONResponse(status_code=202, content={"nfDeploymentId": str(d.nf_deployment_id), "state": d.state})


def _remove_deployment(db: Session, d: NFDeployment) -> None:
    """The deployment is gone: its resource links, its operation history and
    the record itself (the descriptor may be deployed again)."""
    db.query(NFOCloudResource).filter_by(nf_deployment_id=d.nf_deployment_id).delete()
    # LCMOperation.nf_deployment_id has a real FK, same as NFOCloudResource
    # above — deleting the deployment without clearing its own operation
    # history (including a TERMINATE row just added) violates it.
    # `Query.delete()` issues its DELETE immediately against the database,
    # and this session is `autoflush=False` (smo_shared/db.py), so it
    # never sees a pending, unflushed TERMINATE row — only a `flush()`
    # first makes it visible to the very next statement. Caught running
    # a genuine deploy -> terminate sequence against real Postgres —
    # SQLite's test harness doesn't enforce FK constraints by default.
    db.flush()
    db.query(LCMOperation).filter_by(nf_deployment_id=d.nf_deployment_id).delete()
    db.delete(d)


class DmsNotification(BaseModel):
    """OI-3-nfo-abnormal: the deployment manager's (O2 DMS) report on a
    deployment. `detail` says why, for a failure."""
    event: Literal["UNINSTALL_COMPLETE", "UNINSTALL_FAILED", "DELETE_COMPLETE", "DELETE_FAILED", "RUNTIME_FAILURE"]
    detail: str | None = None


@app.post("/deployments/{nf_deployment_id}/dms-notifications")
def receive_dms_notification(nf_deployment_id: uuid.UUID, body: DmsNotification, db: Session = Depends(get_session)):
    """OI-3-nfo-abnormal — how an asynchronous Terminate ends, and how a
    broken workload is reported:

    - `UNINSTALL_COMPLETE`: TERMINATING -> DELETING (resources being released);
    - `DELETE_COMPLETE`: DELETING -> the deployment is removed (answers `state: DELETED`);
    - `UNINSTALL_FAILED` / `DELETE_FAILED`: -> ABNORMAL, the TERMINATE operation FAILED;
    - `RUNTIME_FAILURE`: INSTANTIATING / RUNNING / UPDATING -> ABNORMAL.

    An ABNORMAL deployment keeps the reason in `abnormalReason`; Heal
    recovers it (-> RUNNING) and Terminate retires it (-> DELETING).
    404 for an unknown deployment, 409 NFDEPLOYMENT_ILLEGAL_OPERATION for an
    event its state can't take.
    """
    d = db.get(NFDeployment, nf_deployment_id)
    if d is None:
        raise framework_error(FrameworkError.NFDEPLOYMENT_NOT_FOUND, detail="no such NfDeployment")
    if body.event == "DELETE_COMPLETE":
        if d.state != DeploymentState.DELETING:
            raise framework_error(FrameworkError.NFDEPLOYMENT_ILLEGAL_OPERATION,
                                  detail=f"DELETE_COMPLETE for a deployment in state {d.state}, not DELETING")
        _remove_deployment(db, d)
        db.commit()
        return {"nfDeploymentId": str(nf_deployment_id), "state": "DELETED"}
    try:
        d.state = NFO_FSM.fire(DeploymentState(d.state), DeploymentEvent(body.event))
    except IllegalTransition as exc:
        raise framework_error(FrameworkError.NFDEPLOYMENT_ILLEGAL_OPERATION,
                              detail=f"{body.event} for a deployment in state {d.state}") from exc
    if d.state == DeploymentState.ABNORMAL:
        d.abnormal_reason = f"{body.event}: {body.detail}" if body.detail else body.event
        op = _open_terminate(db, nf_deployment_id)
        if op is not None:
            op.status = "FAILED"
    db.commit()
    return {"nfDeploymentId": str(nf_deployment_id), "state": d.state, "abnormalReason": d.abnormal_reason}


def _open_terminate(db: Session, nf_deployment_id: uuid.UUID) -> LCMOperation | None:
    return db.scalar(select(LCMOperation).where(LCMOperation.nf_deployment_id == nf_deployment_id,
                                                LCMOperation.operation_type == "TERMINATE",
                                                LCMOperation.status == "IN_PROGRESS"))


@app.get("/deployments/{nf_deployment_id}")
def get_deployment(nf_deployment_id: uuid.UUID, db: Session = Depends(get_session)):
    d = db.get(NFDeployment, nf_deployment_id)
    if d is None:
        raise framework_error(FrameworkError.NFDEPLOYMENT_NOT_FOUND, detail="no such NfDeployment")
    return _deployment_view(d)


@app.post("/deployments/{nf_deployment_id}/heal")
def heal(nf_deployment_id: uuid.UUID, db: Session = Depends(get_session)):
    """HISTORY.md §5: previously a pure stub with no state
    transition of any kind. Self-healing recovery isn't modeled by the
    reference at all (no Heal command exists there); this closes the
    state-transition gap directly: legal from ABNORMAL (genuine
    recovery) or RUNNING (idempotent — already healthy). Real K8s
    pod-health remediation behind it stays out of scope
    (D-DEPLOY-NFO-3, unchanged).
    """
    d = db.get(NFDeployment, nf_deployment_id)
    if d is None:
        raise framework_error(FrameworkError.NFDEPLOYMENT_NOT_FOUND, detail="no such NfDeployment")
    try:
        d.state = NFO_FSM.fire(DeploymentState(d.state), DeploymentEvent.HEAL)
    except IllegalTransition as exc:
        raise framework_error(FrameworkError.NFDEPLOYMENT_ILLEGAL_OPERATION,
                               detail=f"cannot heal a deployment in state {d.state}") from exc
    d.abnormal_reason = None
    db.add(LCMOperation(nf_deployment_id=nf_deployment_id, operation_type="HEAL", status="COMPLETED"))
    db.commit()
    return {"nfDeploymentId": str(nf_deployment_id), "state": d.state}


@app.post("/deployments/{nf_deployment_id}/scale")
@idempotent("nfo")
def scale(nf_deployment_id: uuid.UUID, request: Request, db: Session = Depends(get_session)):
    """HISTORY.md §5: previously a pure stub with no state
    transition of any kind. Scale is a replica-count change, the same
    conceptual operation as the reference's Update (RUNNING->UPDATING),
    so it drives that same edge — legal only from RUNNING.
    """
    d = db.get(NFDeployment, nf_deployment_id)
    if d is None:
        raise framework_error(FrameworkError.NFDEPLOYMENT_NOT_FOUND, detail="no such NfDeployment")
    try:
        d.state = NFO_FSM.fire(DeploymentState(d.state), DeploymentEvent.UPDATE)
    except IllegalTransition as exc:
        raise framework_error(FrameworkError.NFDEPLOYMENT_ILLEGAL_OPERATION,
                               detail=f"cannot scale a deployment in state {d.state}") from exc
    op = LCMOperation(nf_deployment_id=nf_deployment_id, operation_type="SCALE", status="IN_PROGRESS")
    db.add(op)
    # Phase 1 elision, same pattern as Instantiate: real Helm upgrade
    # (adjusting replica count) happens here; completes synchronously
    # rather than staying observably UPDATING.
    d.state = NFO_FSM.fire(d.state, DeploymentEvent.UPDATE_COMPLETE)
    op.status = "COMPLETED"
    db.commit()
    return {"nfDeploymentId": str(nf_deployment_id), "state": d.state}


@app.get("/deployments/{nf_deployment_id}/resources")
def query_deployment_resources(nf_deployment_id: uuid.UUID, db: Session = Depends(get_session)):
    """The reference's own NfOCloudVResource, genuinely queryable rather
    than just a column nothing ever reads.
    """
    rows = db.scalars(select(NFOCloudResource).where(NFOCloudResource.nf_deployment_id == nf_deployment_id)).all()
    return [{"resourceLinkId": str(r.resource_link_id), "resourceRef": r.resource_ref, "vresourceType": r.vresource_type} for r in rows]


@app.get("/operations/{operation_id}")
def query_operation_status(operation_id: uuid.UUID, db: Session = Depends(get_session)):
    op = db.get(LCMOperation, operation_id)
    if op is None:
        raise framework_error(FrameworkError.LCM_OPERATION_NOT_FOUND, detail="no such LCM operation")
    return {"operationId": str(op.operation_id), "status": op.status}


@app.get("/deployments/{nf_deployment_id}/placement")
def query_cluster_placement(nf_deployment_id: uuid.UUID, db: Session = Depends(get_session)):
    d = db.get(NFDeployment, nf_deployment_id)
    if d is None:
        raise framework_error(FrameworkError.NFDEPLOYMENT_NOT_FOUND, detail="no such NfDeployment")
    return {"nfDeploymentId": str(d.nf_deployment_id), "clusterId": d.cluster_id}


@app.get("/deployments")
def list_deployments(state: str | None = None, limit: int = PageLimit, offset: int = PageOffset,
                      db: Session = Depends(get_session)):
    """List read over NFDeployment — every other deployment route is
    keyed by an id the caller already holds, so there was no way to see
    the workload fleet (or which deployments sit ABNORMAL) at all.
    """
    stmt = select(NFDeployment)
    if state:
        stmt = stmt.where(NFDeployment.state == state)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [_deployment_view(d) for d in page["items"]]}


def _deployment_view(d: NFDeployment) -> dict:
    return {"nfDeploymentId": str(d.nf_deployment_id), "name": d.name, "state": d.state, "clusterId": d.cluster_id,
            "nfDeploymentDescriptorId": str(d.nf_deployment_descriptor_id), "workloadRef": d.workload_ref,
            "requiredResourceTypeId": d.required_resource_type_id, "abnormalReason": d.abnormal_reason}


@app.get("/descriptors")
def list_descriptors(package_id: uuid.UUID | None = None, limit: int = PageLimit, offset: int = PageOffset,
                      db: Session = Depends(get_session)):
    """(GUI pass 2) NFDeploymentDescriptors created by Onboarding's validation
    pipeline, or by AIMgF's own Runtime Lifecycle for a model runtime
    (packageId is None for those — see CreateDescriptorRequest's own docstring).
    """
    stmt = select(NFDeploymentDescriptor)
    if package_id:
        stmt = stmt.where(NFDeploymentDescriptor.package_id == package_id)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [{"nfDeploymentDescriptorId": str(d.nf_deployment_descriptor_id),
             "packageId": str(d.package_id) if d.package_id else None, "name": d.name,
             "requiredResourceTypeId": d.required_resource_type_id, "workloadTemplate": d.workload_template}
            for d in page["items"]]}


@app.get("/deployments/{nf_deployment_id}/operations")
def list_deployment_operations(nf_deployment_id: uuid.UUID, limit: int = PageLimit, offset: int = PageOffset,
                                db: Session = Depends(get_session)):
    """(GUI pass 2) A deployment's LCM operation history (Instantiate/Heal/Scale/
    Terminate); only a single operation id could be looked up before.
    """
    stmt = select(LCMOperation).where(LCMOperation.nf_deployment_id == nf_deployment_id).order_by(LCMOperation.created_at, LCMOperation.operation_id)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [{"operationId": str(o.operation_id), "operationType": o.operation_type, "status": o.status}
            for o in page["items"]]}
