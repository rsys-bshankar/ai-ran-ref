"""rApp Management SMOS.

SMO Design v1.3 section 3.5, extended by Onboarding/rApp Mgmt LLD sections
5-6: CreateInstance wired concretely to NFO via the TOSCA service template,
and UpgradeInstance's auto-rollback made precise (upgrade.py).
"""

import uuid

import httpx
from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.r1_client import R1Client

from .models import RAppFaultReport, RAppInstance, RAppPerformanceReport
from .statemachine import RAPP_INSTANCE_FSM, InstanceEvent, InstanceState
from .upgrade import resolve_upgrade, start_upgrade

app = FastAPI(title="rApp Management SMOS")


class CreateInstanceRequest(BaseModel):
    packageId: uuid.UUID
    config: dict = {}


class UpgradeRequest(BaseModel):
    newPackageId: uuid.UUID


@app.post("/instances", status_code=202)
def create_instance(body: CreateInstanceRequest, db: Session = Depends(get_session)):
    """CreateInstance — requires packageId.state == AVAILABLE (D-SEC-RAPP-1,
    unchanged). NFO handoff per Onboarding/rApp Mgmt LLD section 5: reads
    the package's TOSCA service template and issues NFO.Instantiate.
    """
    r1 = R1Client()
    pkg_resp = r1.get(f"/onboarding/packages/{body.packageId}/onboarding-status")
    if pkg_resp.status_code != 200 or pkg_resp.json().get("state") != "AVAILABLE":
        raise framework_error(FrameworkError.MODEL_NOT_CERTIFIED, detail="package is not AVAILABLE")
    nf_deployment_descriptor_id = pkg_resp.json().get("nfDeploymentDescriptorId")
    if not nf_deployment_descriptor_id:
        # Every package that reaches AVAILABLE has one — OnboardPackage's own
        # validation pipeline creates it via NFO's CreateDescriptor (NFO+FOCOM
        # LLD section 2). Missing here means an inconsistent record, not a
        # normal refusal.
        raise framework_error(FrameworkError.MODEL_NOT_CERTIFIED, detail="package has no nfDeploymentDescriptorId")

    inst = RAppInstance(package_id=body.packageId, configuration=body.config, state=InstanceState.DEPLOYING, oauth_client_id=str(uuid.uuid4()))
    db.add(inst)
    db.flush()

    nfo_resp = r1.post("/nfo/deployments", json={
        "nfDeploymentDescriptorId": nf_deployment_descriptor_id,  # the real descriptor, per section 5
        "requiredResourceTypeId": body.config.get("requiredResourceTypeId"),
    })
    inst.workload_ref = nfo_resp.json().get("nfDeploymentId") if nfo_resp.status_code == 200 else None
    db.commit()
    return {"instanceId": str(inst.instance_id), "oauthClientId": inst.oauth_client_id}


@app.post("/instances/{instance_id}/bootstrap-complete")
def bootstrap_complete(instance_id: uuid.UUID, db: Session = Depends(get_session)):
    """Called once the rApp container has bootstrapped via R1 Termination
    and registered with SME/DME — closes DEPLOYING -> RUNNING.
    """
    inst = db.get(RAppInstance, instance_id)
    inst.state = RAPP_INSTANCE_FSM.fire(InstanceState(inst.state), InstanceEvent.BOOTSTRAP_OK, instance=inst)
    db.commit()
    return {"instanceId": str(inst.instance_id), "state": inst.state}


@app.post("/instances/{instance_id}/upgrade")
def upgrade_instance(instance_id: uuid.UUID, body: UpgradeRequest, db: Session = Depends(get_session)):
    """UpgradeInstance — Annex A.1.2.2.1. Kicks off the two-row choreography
    in upgrade.py; the actual success/failure resolution happens when the
    new instance's own bootstrap-complete (or a timeout/fault) fires
    resolve_upgrade — modeled here as immediate resolution for the Phase 1
    reference (a real deployment would poll/await bootstrap-complete
    asynchronously within upgrade_timeout_seconds).
    """
    old = db.get(RAppInstance, instance_id)
    new = start_upgrade(db, old, body.newPackageId)
    db.commit()
    return {"newInstanceId": str(new.instance_id), "oldInstanceState": old.state}


@app.post("/instances/{instance_id}/upgrade/resolve")
def resolve_upgrade_outcome(instance_id: uuid.UUID, succeeded: bool, db: Session = Depends(get_session)):
    old = db.get(RAppInstance, instance_id)
    new = db.get(RAppInstance, old.pending_upgrade_instance_id)
    resolve_upgrade(db, old, new, new_bootstrap_succeeded=succeeded)
    db.commit()
    survivor = new if succeeded else old
    return {"instanceId": str(survivor.instance_id), "state": survivor.state, "packageId": str(survivor.package_id)}


@app.post("/instances/{instance_id}/terminate")
def terminate_instance(instance_id: uuid.UUID, db: Session = Depends(get_session)):
    """TerminateInstance — Annex A.1.2.3. Credential revocation is part of
    this transition itself (statemachine.py's _revoke_credential action),
    not a separate step (closes RT-3).
    """
    inst = db.get(RAppInstance, instance_id)
    inst.state = RAPP_INSTANCE_FSM.fire(InstanceState(inst.state), InstanceEvent.TERMINATE, instance=inst)
    db.commit()
    db.delete(inst)
    db.commit()
    return {"status": "terminated"}


@app.get("/instances/{instance_id}/config")
def get_config(instance_id: uuid.UUID, db: Session = Depends(get_session)):
    inst = db.get(RAppInstance, instance_id)
    return inst.configuration or {}


@app.put("/instances/{instance_id}/config")
def set_config(instance_id: uuid.UUID, config: dict, db: Session = Depends(get_session)):
    inst = db.get(RAppInstance, instance_id)
    inst.configuration = config
    db.commit()
    return {"status": "updated"}


@app.post("/instances/{instance_id}/performance")
def report_performance(instance_id: uuid.UUID, metrics: dict, db: Session = Depends(get_session)):
    db.add(RAppPerformanceReport(instance_id=instance_id, metrics=metrics))
    db.commit()
    return {"status": "recorded"}


@app.post("/instances/{instance_id}/fault")
def report_fault(instance_id: uuid.UUID, severity: str, description: str = "", db: Session = Depends(get_session)):
    inst = db.get(RAppInstance, instance_id)
    db.add(RAppFaultReport(instance_id=instance_id, severity=severity, description=description))
    if severity == "critical":
        inst.state = RAPP_INSTANCE_FSM.fire(InstanceState(inst.state), InstanceEvent.CRASH, instance=inst)
    db.commit()
    return {"status": "recorded", "instanceState": inst.state}


@app.get("/instances")
def list_instances(state: str | None = None, db: Session = Depends(get_session)):
    stmt = select(RAppInstance)
    if state:
        stmt = stmt.where(RAppInstance.state == state)
    return [{"instanceId": str(i.instance_id), "packageId": str(i.package_id), "state": i.state} for i in db.scalars(stmt).all()]
