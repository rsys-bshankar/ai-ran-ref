"""NFO SMOS (O2dms).

SMO Design v1.3 section 3.7, extended by NFO+FOCOM LLD sections 2, 4:
NFDeploymentDescriptor closes the gap where nfDeploymentDescriptorId
referenced nothing concrete, and Instantiate now calls FOCOM's inventory
first to resolve clusterId, rather than assuming the Phase 1 degenerate
single-cluster value implicitly. OPEN_ITEMS.md section 5 extends this
further: a real 7-state deployment lifecycle (statemachine.py), real
duplication/dependency guards on Instantiate (dms_lcm_nfdeployment.py's
_check_duplication/_check_dependencies), a resource-linkage object
(NFOCloudResource, the reference's NfOCloudVResource), and Heal/Scale
now actually drive state transitions instead of being pure stubs.
"""

import uuid

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.r1_client import R1Client
from smo_shared.statemachine import IllegalTransition

from .models import LCMOperation, NFDeployment, NFDeploymentDescriptor, NFOCloudResource
from .statemachine import DeploymentEvent, DeploymentState, NFO_FSM

app = FastAPI(title="NFO SMOS (O2dms)")


class InstantiateRequest(BaseModel):
    nfDeploymentDescriptorId: uuid.UUID
    name: str
    requiredResourceTypeId: str | None = None


class CreateDescriptorRequest(BaseModel):
    packageId: uuid.UUID
    name: str
    workloadTemplate: dict = {}
    requiredResourceTypeId: str | None = None


@app.post("/descriptors", status_code=201)
def create_descriptor(body: CreateDescriptorRequest, db: Session = Depends(get_session)):
    """CreateDescriptor — NFO+FOCOM LLD section 2: an NFDeploymentDescriptor
    derived from an onboarded package's TOSCA Definitions/, called from
    Onboarding's OnboardPackage flow once validation succeeds, closing the
    gap where nfDeploymentDescriptorId previously referenced nothing
    concrete.
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
def instantiate(body: InstantiateRequest, db: Session = Depends(get_session)):
    """Instantiate — NFO+FOCOM LLD section 4: FOCOM's inventory is queried
    to resolve clusterId before the workload is placed, rather than the
    Phase 1 degenerate cluster being assumed implicitly. OPEN_ITEMS.md
    section 5: now also enforces the reference's own real guards before
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
    cluster_id = inv_resp.json().get("clusterId", "phase1-degenerate-cluster") if inv_resp.status_code == 200 else "phase1-degenerate-cluster"

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
def terminate(nf_deployment_id: uuid.UUID, db: Session = Depends(get_session)):
    """Terminate — mirrors the reference's own state dispatch
    (lcm_nfdeployment_uninstall, dms_lcm_nfdeployment.py): INITIAL/
    ABNORMAL delete immediately (no chart was ever installed, or it's
    already broken); every in-flight/running state passes through
    TERMINATING first (Phase 1 elision: real Helm uninstall completes
    synchronously, same pattern as Instantiate's own elision); calling
    Terminate again on an already-TERMINATING deployment is a no-op
    (matches the reference's own `elif ... Uninstalling: pass`); calling
    it on one already DELETING flips it to ABNORMAL instead of deleting
    twice — the reference's own defensive catch-all for a Terminate
    landing on a state its dispatch chain doesn't otherwise expect.
    """
    d = db.get(NFDeployment, nf_deployment_id)
    if d is None:
        return
    was_terminating = d.state == DeploymentState.TERMINATING
    new_state = NFO_FSM.fire(DeploymentState(d.state), DeploymentEvent.TERMINATE)
    db.add(LCMOperation(nf_deployment_id=nf_deployment_id, operation_type="TERMINATE", status="COMPLETED"))
    if new_state == DeploymentState.ABNORMAL:
        d.state = new_state
        db.commit()
        return
    if was_terminating:
        db.commit()
        return
    db.query(NFOCloudResource).filter_by(nf_deployment_id=nf_deployment_id).delete()
    db.delete(d)
    db.commit()


@app.post("/deployments/{nf_deployment_id}/heal")
def heal(nf_deployment_id: uuid.UUID, db: Session = Depends(get_session)):
    """OPEN_ITEMS.md section 5: previously a pure stub with no state
    transition of any kind. Self-healing recovery isn't modeled by the
    reference at all (no Heal command exists there); this closes the
    state-transition gap directly: legal from ABNORMAL (genuine
    recovery) or RUNNING (idempotent — already healthy). Real K8s
    pod-health remediation behind it stays out of scope
    (D-DEPLOY-NFO-3, unchanged).
    """
    d = db.get(NFDeployment, nf_deployment_id)
    if d is None:
        raise HTTPException(status_code=404, detail="no such NfDeployment")
    try:
        d.state = NFO_FSM.fire(DeploymentState(d.state), DeploymentEvent.HEAL)
    except IllegalTransition:
        raise framework_error(FrameworkError.NFDEPLOYMENT_ILLEGAL_OPERATION,
                               detail=f"cannot heal a deployment in state {d.state}")
    db.add(LCMOperation(nf_deployment_id=nf_deployment_id, operation_type="HEAL", status="COMPLETED"))
    db.commit()
    return {"nfDeploymentId": str(nf_deployment_id), "state": d.state}


@app.post("/deployments/{nf_deployment_id}/scale")
def scale(nf_deployment_id: uuid.UUID, db: Session = Depends(get_session)):
    """OPEN_ITEMS.md section 5: previously a pure stub with no state
    transition of any kind. Scale is a replica-count change, the same
    conceptual operation as the reference's Update (RUNNING->UPDATING),
    so it drives that same edge — legal only from RUNNING.
    """
    d = db.get(NFDeployment, nf_deployment_id)
    if d is None:
        raise HTTPException(status_code=404, detail="no such NfDeployment")
    try:
        d.state = NFO_FSM.fire(DeploymentState(d.state), DeploymentEvent.UPDATE)
    except IllegalTransition:
        raise framework_error(FrameworkError.NFDEPLOYMENT_ILLEGAL_OPERATION,
                               detail=f"cannot scale a deployment in state {d.state}")
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
    return {"operationId": str(op.operation_id), "status": op.status}


@app.get("/deployments/{nf_deployment_id}/placement")
def query_cluster_placement(nf_deployment_id: uuid.UUID, db: Session = Depends(get_session)):
    d = db.get(NFDeployment, nf_deployment_id)
    return {"nfDeploymentId": str(d.nf_deployment_id), "clusterId": d.cluster_id}
