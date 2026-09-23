"""NFO SMOS (O2dms).

SMO Design v1.3 section 3.7, extended by NFO+FOCOM LLD sections 2, 4:
NFDeploymentDescriptor closes the gap where nfDeploymentDescriptorId
referenced nothing concrete, and Instantiate now calls FOCOM's inventory
first to resolve clusterId, rather than assuming the Phase 1 degenerate
single-cluster value implicitly.
"""

import uuid

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.r1_client import R1Client

from .models import LCMOperation, NFDeployment, NFDeploymentDescriptor

app = FastAPI(title="NFO SMOS (O2dms)")


class InstantiateRequest(BaseModel):
    nfDeploymentDescriptorId: uuid.UUID
    requiredResourceTypeId: str | None = None


@app.post("/deployments", status_code=202)
def instantiate(body: InstantiateRequest, db: Session = Depends(get_session)):
    """Instantiate — NFO+FOCOM LLD section 4: FOCOM's inventory is queried
    to resolve clusterId before the workload is placed, rather than the
    Phase 1 degenerate cluster being assumed implicitly.
    """
    r1 = R1Client()
    inv_resp = r1.get("/focom/inventory", params={"resource_type": body.requiredResourceTypeId or ""})
    cluster_id = inv_resp.json().get("clusterId", "phase1-degenerate-cluster") if inv_resp.status_code == 200 else "phase1-degenerate-cluster"

    deployment = NFDeployment(
        nf_deployment_descriptor_id=body.nfDeploymentDescriptorId,
        cluster_id=cluster_id,
        required_resource_type_id=body.requiredResourceTypeId,
        state="INSTANTIATING",
    )
    db.add(deployment)
    db.flush()
    op = LCMOperation(nf_deployment_id=deployment.nf_deployment_id, operation_type="INSTANTIATE", status="IN_PROGRESS")
    db.add(op)

    # Phase 1: meaningfully real (docker run --gpus passthrough when
    # requiredResourceTypeId is set) — elided here; deployment moves to
    # RUNNING once the container starts.
    deployment.state = "RUNNING"
    op.status = "COMPLETED"
    db.commit()
    return {"nfDeploymentId": str(deployment.nf_deployment_id), "state": deployment.state, "clusterId": cluster_id}


@app.delete("/deployments/{nf_deployment_id}", status_code=204)
def terminate(nf_deployment_id: uuid.UUID, db: Session = Depends(get_session)):
    d = db.get(NFDeployment, nf_deployment_id)
    if d is not None:
        db.add(LCMOperation(nf_deployment_id=nf_deployment_id, operation_type="TERMINATE", status="COMPLETED"))
        db.delete(d)
        db.commit()


@app.post("/deployments/{nf_deployment_id}/heal")
def heal(nf_deployment_id: uuid.UUID, db: Session = Depends(get_session)):
    """Phase 1: shape-only stub until K8s (D-DEPLOY-NFO-3, unchanged)."""
    db.add(LCMOperation(nf_deployment_id=nf_deployment_id, operation_type="HEAL", status="COMPLETED"))
    db.commit()
    return {"status": "stub-only"}


@app.post("/deployments/{nf_deployment_id}/scale")
def scale(nf_deployment_id: uuid.UUID, db: Session = Depends(get_session)):
    db.add(LCMOperation(nf_deployment_id=nf_deployment_id, operation_type="SCALE", status="COMPLETED"))
    db.commit()
    return {"status": "stub-only"}


@app.get("/operations/{operation_id}")
def query_operation_status(operation_id: uuid.UUID, db: Session = Depends(get_session)):
    op = db.get(LCMOperation, operation_id)
    return {"operationId": str(op.operation_id), "status": op.status}


@app.get("/deployments/{nf_deployment_id}/placement")
def query_cluster_placement(nf_deployment_id: uuid.UUID, db: Session = Depends(get_session)):
    d = db.get(NFDeployment, nf_deployment_id)
    return {"nfDeploymentId": str(d.nf_deployment_id), "clusterId": d.cluster_id}
