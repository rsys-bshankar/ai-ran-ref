"""rApp Management SMOS.

SMO Design v1.3 section 3.5, extended by Onboarding/rApp Mgmt LLD sections
5-6: CreateInstance wired concretely to NFO via the TOSCA service template,
and UpgradeInstance's auto-rollback made precise (upgrade.py).
"""

import uuid

import httpx
from fastapi import Depends, FastAPI, HTTPException
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
        "name": f"rapp-instance-{inst.instance_id}",  # NFO's own duplication guard (OPEN_ITEMS.md section 5) needs a real name
        "requiredResourceTypeId": body.config.get("requiredResourceTypeId"),
    })
    inst.workload_ref = nfo_resp.json().get("nfDeploymentId") if nfo_resp.status_code == 200 else None

    usage_resp = r1.post(f"/onboarding/packages/{body.packageId}/usage/start", params={"consumer_id": str(inst.instance_id)})
    if usage_resp.status_code == 200:
        inst.package_usage_registration_id = uuid.UUID(usage_resp.json()["registrationId"])
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


@app.post("/instances/{instance_id}/recover")
def recover_instance(instance_id: uuid.UUID, db: Session = Depends(get_session)):
    """RECOVER — v1.3's own FAULTED exit, concretized: the FSM transition
    (FAULTED -> DEPLOYING) existed but no route ever fired it, so a
    critically-faulted instance had no API path back to RUNNING at all.
    Re-enters at the same point CreateInstance does — the container must
    re-bootstrap and call bootstrap-complete again, matching the "no
    lightweight update path" principle this build applies everywhere
    else (e.g. AI/ML Workflow's retraining re-entry).
    """
    inst = db.get(RAppInstance, instance_id)
    inst.state = RAPP_INSTANCE_FSM.fire(InstanceState(inst.state), InstanceEvent.RECOVER, instance=inst)
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
    not a separate step (closes RT-3). Also stops this instance's
    PackageUsageRegistration — the actual fix for Onboarding's
    cascade-delete guard, previously unreachable from ordinary rApp
    deployment since nothing called usage/stop.

    OPEN_ITEMS.md section 5: this used to delete the instance row
    outright, in the same call — undeploy and delete collapsed into one
    irreversible step, with no way to observe an instance post-teardown
    or to delete one that was already torn down some other way (e.g.
    CRASH). The reference's own split (`RappService.undeployRappInstance`/
    `deleteRappInstance`, DEPLOYED -> UNDEPLOYING -> UNDEPLOYED, delete
    only legal from UNDEPLOYED) is adopted here: TERMINATE now only tears
    the workload down (this action) and lands in the terminal UNDEPLOYED
    state with the row still present; removing the row itself is the
    separate `delete_instance` below.
    """
    inst = db.get(RAppInstance, instance_id)
    registration_id = inst.package_usage_registration_id
    inst.state = RAPP_INSTANCE_FSM.fire(InstanceState(inst.state), InstanceEvent.TERMINATE, instance=inst)
    db.commit()
    if registration_id is not None:
        R1Client().post(f"/onboarding/packages/{inst.package_id}/usage/{registration_id}/stop")
    return {"instanceId": str(inst.instance_id), "state": inst.state}


@app.delete("/instances/{instance_id}", status_code=204)
def delete_instance(instance_id: uuid.UUID, db: Session = Depends(get_session)):
    """DeleteRappInstance — the reference's own standalone delete, distinct
    from undeploy (`RappService.deleteRappInstance`'s guard: "Unable to
    delete rApp instance %s as it is not in UNDEPLOYED state"). Only legal
    once TERMINATE has already landed the instance in UNDEPLOYED — a
    running or faulted instance can't be deleted out from under itself.

    Also closes the same FK-cascade bug class already found and fixed for
    DME's `deregister_producer`/AI-ML Workflow's `deregister_model`: the
    instance's `rapp_fault_report`/`rapp_performance_report` rows had no
    `ON DELETE CASCADE` (fixed alongside this), so this cleans them up
    explicitly as a second, directly-testable line of defense.
    """
    inst = db.get(RAppInstance, instance_id)
    if inst is None:
        raise HTTPException(status_code=404, detail="no such RAppInstance")
    if inst.state != InstanceState.UNDEPLOYED:
        raise framework_error(FrameworkError.RAPP_INSTANCE_NOT_UNDEPLOYED,
                               detail=f"instance {instance_id} is not UNDEPLOYED (state={inst.state})")
    db.query(RAppFaultReport).filter(RAppFaultReport.instance_id == instance_id).delete()
    db.query(RAppPerformanceReport).filter(RAppPerformanceReport.instance_id == instance_id).delete()
    db.delete(inst)
    db.commit()


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


@app.get("/instances/{instance_id}")
def get_instance(instance_id: uuid.UUID, db: Session = Depends(get_session)):
    """OPEN_ITEMS.md section 5: no single-instance detail read existed at
    all — only the list route above and single-field sub-resources
    (config via get_config/set_config). The reference's own
    GET .../instance/{id} returns nested ACM/SME/DME resource records
    (composition IDs, provider-function IDs, producer/consumer type
    lists) — that part stays out of scope, unchanged: CreateInstance
    never accepts that caller-supplied deploy descriptor in the first
    place (real ACM/Helm/K8s deployment is the declared elision), so
    echoing it back would mean inventing descriptor data, not exposing
    something this build already computes. What this genuinely does
    expose: workloadRef (the real NFO nfDeploymentId CreateInstance
    received back — the one real resource reference this build tracks)
    and the caller-supplied configuration, alongside the identity/state
    fields list_instances already returns.
    """
    inst = db.get(RAppInstance, instance_id)
    if inst is None:
        raise HTTPException(status_code=404, detail="no such RAppInstance")
    return {
        "instanceId": str(inst.instance_id), "packageId": str(inst.package_id), "state": inst.state,
        "workloadRef": inst.workload_ref, "configuration": inst.configuration,
        "pendingUpgradeInstanceId": str(inst.pending_upgrade_instance_id) if inst.pending_upgrade_instance_id else None,
    }
