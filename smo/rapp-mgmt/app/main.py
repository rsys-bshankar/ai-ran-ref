"""rApp Management SMOS.

SMO Design v1.3 section 3.5, extended by Onboarding/rApp Mgmt LLD sections
5-6: CreateInstance wired concretely to NFO via the TOSCA service template,
and UpgradeInstance's auto-rollback made precise (upgrade.py).
"""

import uuid
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.r1_client import R1Client
from smo_shared.openapi_security import apply_r1_gateway_security
from smo_shared.correlation import apply_correlation_id
from smo_shared.pagination import PageLimit, PageOffset, paginate

from .models import RAppFaultReport, RAppInstance, RAppPerformanceReport
from .statemachine import RAPP_INSTANCE_FSM, InstanceEvent, InstanceState
from .upgrade import resolve_upgrade, start_upgrade

app = FastAPI(title="rApp Management SMOS")
apply_r1_gateway_security(app)
apply_correlation_id(app)


@app.get("/health")
def health_check():
    """Liveness probe. The GUI BFF's GET /modules/status fans out to
    /<module>/health through R1 Termination for every module in parallel,
    so every module answers one — previously only ran-nf-oam/a1-related
    did (as their own DME producer-health callback URL).
    """
    return {"status": "healthy"}


class CreateInstanceRequest(BaseModel):
    packageId: uuid.UUID
    config: dict = {}
    # OPEN_ITEMS.md section 6.3 — rApp Autonomy Modes: fixed at onboarding
    # (this call), not chosen per-inference-call. SHADOW (no enforcement)
    # is the safe default for every existing caller that doesn't declare
    # one. regionScope only matters for AUTONOMOUS — opaque JSON, the
    # same shape config already is.
    autonomyMode: Literal["AUTONOMOUS", "ASSIST", "SHADOW"] = "SHADOW"
    regionScope: dict | None = None


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

    inst = RAppInstance(package_id=body.packageId, configuration=body.config, state=InstanceState.DEPLOYING, oauth_client_id=str(uuid.uuid4()),
                         autonomy_mode=body.autonomyMode, region_scope=body.regionScope)
    db.add(inst)
    db.flush()

    nfo_resp = r1.post("/nfo/deployments", json={
        "nfDeploymentDescriptorId": nf_deployment_descriptor_id,  # the real descriptor, per section 5
        "name": f"rapp-instance-{inst.instance_id}",  # NFO's own duplication guard (OPEN_ITEMS.md section 5) needs a real name
        "requiredResourceTypeId": body.config.get("requiredResourceTypeId"),
    })
    # NFO Instantiate answers 202 Accepted (nfo/app/main.py) — a 200-only check
    # dropped every real workloadRef.
    inst.workload_ref = nfo_resp.json().get("nfDeploymentId") if nfo_resp.status_code in (200, 202) else None

    usage_resp = r1.post(f"/onboarding/packages/{body.packageId}/usage/start", params={"consumer_id": str(inst.instance_id)})
    if usage_resp.status_code == 200:
        inst.package_usage_registration_id = uuid.UUID(usage_resp.json()["registrationId"])
    db.commit()
    return {"instanceId": str(inst.instance_id), "oauthClientId": inst.oauth_client_id}


def _sme_provider_registration_body(provider: dict, apf_id: str) -> dict:
    """A CSAR's own `Files/Sme/providers/*.json` may already be this
    build's own real `ProviderRegistrationRequest` body
    (`samples/hello-world-rapp/`'s own established convention —
    `apfId`/`providerDomainInfo`) or the real external CAPIF
    `APIProviderEnrolmentDetails` shape (`apiProvDomInfo`/`apiProvFuncs`,
    grounded against `nonrtric-plt-rappmanager`'s own real sample
    packages) — `providerDomainInfo` reads whichever key is actually
    present. `apfId` is always this instance's own `oauth_client_id`
    (the "one instance, one identity" convention already used for its
    DME producer_id), never whatever a CSAR's own JSON declares — two
    instances of the same package must never collide on one shared,
    hardcoded apfId.
    """
    return {"apfId": apf_id, "providerDomainInfo": provider.get("providerDomainInfo") or provider.get("apiProvDomInfo")}


def _sme_service_registration_body(service_api: dict, apf_id: str) -> dict:
    """A CSAR's own `Files/Sme/serviceapis/*.json` may already be this
    build's own real `ServiceRegistration` body
    (`samples/hello-world-rapp/`'s own established convention —
    `serviceName`/`endpoint`/`version`/`moduleScope`/... directly) or
    the real external CAPIF `ServiceAPIDescription` shape
    (`apiName`/`aefProfiles` with nested `versions`/
    `interfaceDescriptions`, grounded against
    `nonrtric-plt-rappmanager`'s own real sample packages) needing a
    real field-by-field mapping — `serviceName`'s presence distinguishes
    the two. `producerId` is always this instance's own `oauth_client_id`
    either way, never whatever the CSAR's own JSON declares, same
    reasoning as `_sme_provider_registration_body` above. For the real
    CAPIF shape, serviceName/endpoint/version/moduleScope are this
    build's own required fields with no CAPIF equivalent, so a fixed,
    documented default fills each one where the real sample has nothing
    to say (its own `apiVersion` is even an empty string).
    `aefProfiles` passes through byte-for-byte in both cases: this
    build's own field is an untyped `list[dict]`, so neither shape needs
    lossy reshaping there.

    `serviceName` is always suffixed with this instance's own apfId —
    caught by running this against this repo's own real, already-shipped
    demo CSAR (`samples/hello-world-rapp/`), not assumed: SME's own
    `register_service` treats `serviceName` as globally unique across
    every producer (`sme/app/main.py`'s own documented Section 2.3
    rule), so a CSAR's fixed, package-level `serviceName` would
    otherwise collide the moment a second instance of the same package
    tries to register the identical name under its own, different
    apfId — a real `SERVICE_NAME_CONFLICT`, not a hypothetical one.
    """
    if "serviceName" in service_api:
        return {**service_api, "producerId": apf_id, "serviceName": f"{service_api['serviceName']}-{apf_id}"}
    first_profile = (service_api.get("aefProfiles") or [{}])[0]
    first_version = (first_profile.get("versions") or [{}])[0]
    first_iface = (first_profile.get("interfaceDescriptions") or [{}])[0]
    endpoint = f"http://{first_iface['ipv4Addr']}:{first_iface['port']}" if first_iface.get("ipv4Addr") else "http://unknown"
    return {
        "serviceName": f"{service_api.get('apiName', 'unnamed-service')}-{apf_id}", "producerId": apf_id,
        "endpoint": endpoint, "version": first_version.get("apiVersion") or "1.0",
        "moduleScope": "rapp", "aefProfiles": service_api.get("aefProfiles", []),
    }


def _register_sme_declarations(inst: RAppInstance) -> None:
    """SPEC_AUDIT.md's Onboarding/rApp Mgmt finding 3 (SME auto-
    registration): real O-RAN SC rApp Manager behavior
    (SmeDeployer.deployRappInstance) registers a package's CSAR-bundled
    Files/Sme/providers/ + Files/Sme/serviceapis/ declarations with SME
    per *instance*, at deploy time — not at onboarding or priming (the
    reference's own primeRapp is a documented no-op for SME). This
    build's own bootstrap-complete is already the established stand-in
    for "the rApp container has bootstrapped ... and registered with
    SME/DME" (this route's own docstring predates this actually doing
    so) — the natural, already-grounded hook, not a new subsystem.

    Uses this instance's own oauth_client_id as its SME apfId, the same
    "one instance, one identity" convention already used for its DME
    producer_id (statemachine.py's _reconsider_dme_registration).
    Best-effort: an unreachable SME must never block bootstrap from
    completing, the same "unreachable callback never fails the primary
    operation" precedent used throughout this build.
    """
    r1 = R1Client()
    pkg_resp = r1.get(f"/onboarding/packages/{inst.package_id}/onboarding-status")
    declarations = pkg_resp.json().get("smeDeclarations") if pkg_resp.status_code == 200 else None
    if not declarations:
        return
    apf_id = inst.oauth_client_id
    try:
        for provider in declarations.get("providers", []):
            r1.post("/sme/provider-registrations", json=_sme_provider_registration_body(provider, apf_id))
        service_ids = []
        for service_api in declarations.get("serviceApis", []):
            resp = r1.post(f"/sme/published-apis/v1/{apf_id}/service-apis", json=_sme_service_registration_body(service_api, apf_id))
            if resp.status_code == 201:
                service_ids.append(resp.json()["serviceId"])
        inst.sme_service_ids = service_ids or None
    except httpx.HTTPError:
        pass


@app.post("/instances/{instance_id}/bootstrap-complete")
def bootstrap_complete(instance_id: uuid.UUID, db: Session = Depends(get_session)):
    """Called once the rApp container has bootstrapped via R1 Termination
    and registered with SME/DME — closes DEPLOYING -> RUNNING.
    """
    inst = db.get(RAppInstance, instance_id)
    _register_sme_declarations(inst)
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
        raise framework_error(FrameworkError.RAPP_INSTANCE_NOT_FOUND, detail="no such RAppInstance")
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
def list_instances(state: str | None = None, limit: int = PageLimit, offset: int = PageOffset,
                    db: Session = Depends(get_session)):
    stmt = select(RAppInstance)
    if state:
        stmt = stmt.where(RAppInstance.state == state)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [{"instanceId": str(i.instance_id), "packageId": str(i.package_id), "state": i.state,
             "autonomyMode": i.autonomy_mode} for i in page["items"]]}


@app.get("/instances/{instance_id}")
def get_instance(instance_id: uuid.UUID, db: Session = Depends(get_session)):
    """OPEN_ITEMS.md section 5: no single-instance detail read existed at
    all — only the list route above and single-field sub-resources
    (config via get_config/set_config). The reference's own
    GET .../instance/{id} returns nested ACM/SME/DME resource records
    (composition IDs, provider-function IDs, producer/consumer type
    lists) — the ACM part stays out of scope, unchanged: CreateInstance
    never accepts that caller-supplied deploy descriptor in the first
    place (real ACM/Helm/K8s deployment is the declared elision), so
    echoing it back would mean inventing descriptor data, not exposing
    something this build already computes. What this genuinely does
    expose: workloadRef (the real NFO nfDeploymentId CreateInstance
    received back), smeServiceIds (the real SME serviceId(s)
    bootstrap-complete's own SME auto-registration received back —
    SPEC_AUDIT.md's Onboarding/rApp Mgmt finding 3, closed), and the
    caller-supplied configuration, alongside the identity/state fields
    list_instances already returns.
    """
    inst = db.get(RAppInstance, instance_id)
    if inst is None:
        raise framework_error(FrameworkError.RAPP_INSTANCE_NOT_FOUND, detail="no such RAppInstance")
    return {
        "instanceId": str(inst.instance_id), "packageId": str(inst.package_id), "state": inst.state,
        "workloadRef": inst.workload_ref, "configuration": inst.configuration,
        "pendingUpgradeInstanceId": str(inst.pending_upgrade_instance_id) if inst.pending_upgrade_instance_id else None,
        "smeServiceIds": inst.sme_service_ids,
        "autonomyMode": inst.autonomy_mode, "regionScope": inst.region_scope,
    }


@app.get("/instances/{instance_id}/performance")
def list_performance_reports(instance_id: uuid.UUID, limit: int = PageLimit, offset: int = PageOffset,
                              db: Session = Depends(get_session)):
    """Read side of report_performance above — previously write-only, so
    an operator had no way to see what an rApp had reported at all.
    Newest first (the GUI's KPI sparkline only ever wants the recent tail).
    """
    if db.get(RAppInstance, instance_id) is None:
        raise framework_error(FrameworkError.RAPP_INSTANCE_NOT_FOUND, detail="no such RAppInstance")
    stmt = select(RAppPerformanceReport).where(RAppPerformanceReport.instance_id == instance_id).order_by(RAppPerformanceReport.reported_at.desc())
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [{"reportId": str(r.id), "metrics": r.metrics, "reportedAt": r.reported_at.isoformat()} for r in page["items"]]}


@app.get("/instances/{instance_id}/faults")
def list_fault_reports(instance_id: uuid.UUID, limit: int = PageLimit, offset: int = PageOffset,
                        db: Session = Depends(get_session)):
    """Read side of report_fault above, same shape as list_performance_reports."""
    if db.get(RAppInstance, instance_id) is None:
        raise framework_error(FrameworkError.RAPP_INSTANCE_NOT_FOUND, detail="no such RAppInstance")
    stmt = select(RAppFaultReport).where(RAppFaultReport.instance_id == instance_id).order_by(RAppFaultReport.reported_at.desc())
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [{"faultId": str(r.id), "severity": r.severity, "description": r.description,
             "reportedAt": r.reported_at.isoformat()} for r in page["items"]]}
