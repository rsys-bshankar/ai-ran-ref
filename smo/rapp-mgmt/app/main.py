"""rApp Management SMOS.

SMO Design v1.3 section 3.5, extended by Onboarding/rApp Mgmt LLD sections
5-6: CreateInstance wired concretely to NFO via the TOSCA service template,
and UpgradeInstance's auto-rollback made precise (upgrade.py).
"""

import uuid
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from smo_shared.health import database_check, install_health, sme_token_check
from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error, illegal_transition_error
from smo_shared.r1_client import R1Client  # noqa: F401 — the class every R1 call here uses (tests patch it as app.main.R1Client)
from smo_shared.statemachine import IllegalTransition
from smo_shared.openapi_security import apply_r1_gateway_security
from smo_shared.correlation import apply_correlation_id
from smo_shared.pagination import PageLimit, PageOffset, paginate
from smo_shared.versioning import install_concurrency_handler
from smo_shared.idempotency import idempotent

from .models import RAppFaultReport, RAppInstance, RAppPerformanceReport
from .provisioning import (DEPLOYABLE_PACKAGE_STATES, provision_instance, register_sme_declarations,  # noqa: F401
                           release_instance_resources)
from .statemachine import RAPP_INSTANCE_FSM, InstanceEvent, InstanceState
from .upgrade import (current_instance_id, expire_overdue_upgrade, resolve_upgrade, rollback_target, start_rollback,
                      start_upgrade, version_history)

app = FastAPI(title="rApp Management SMOS")
install_concurrency_handler(app)  # a stale write (PR-ST-2) is a 409, not a 500
apply_r1_gateway_security(app)
apply_correlation_id(app)


install_health(app, checks=[database_check, sme_token_check])  # /live, /ready and the /health alias (PR-ST-7)


class CreateInstanceRequest(BaseModel):
    packageId: uuid.UUID
    config: dict = {}
    # HISTORY.md OI-6.3 — rApp Autonomy Modes: fixed at onboarding
    # (this call), not chosen per-inference-call. SHADOW (no enforcement)
    # is the safe default for every existing caller that doesn't declare
    # one. regionScope only matters for AUTONOMOUS — opaque JSON, the
    # same shape config already is.
    autonomyMode: Literal["AUTONOMOUS", "ASSIST", "SHADOW"] = "SHADOW"
    regionScope: dict | None = None


class UpgradeRequest(BaseModel):
    newPackageId: uuid.UUID


def _get_or_404(db: Session, instance_id: uuid.UUID) -> RAppInstance:
    inst = db.get(RAppInstance, instance_id)
    if inst is None:
        raise framework_error(FrameworkError.RAPP_INSTANCE_NOT_FOUND, detail=f"no such RAppInstance {instance_id}")
    return inst


def _upgrade_owner(db: Session, inst: RAppInstance) -> RAppInstance | None:
    """The old row of the upgrade `inst` takes part in: itself if it has a
    pending replacement, or the row whose pending replacement it is."""
    if inst.pending_upgrade_instance_id is not None:
        return inst
    return db.scalar(select(RAppInstance).where(RAppInstance.pending_upgrade_instance_id == inst.instance_id))


def _sweep_overdue_upgrade(db: Session, old: RAppInstance) -> bool | None:
    """The lazy upgradeTimeoutSeconds sweep (upgrade.py) for one upgrade, committed on its own.
    True: this request rolled it back. False: nothing was overdue. None: another replica
    ran the same sweep first (the write was stale, PR-ST-2), so it is done either way."""
    try:
        if not expire_overdue_upgrade(db, old):
            return False
        db.commit()
        return True
    except StaleDataError:
        db.rollback()
        return None


def _load_instance(db: Session, instance_id: uuid.UUID) -> RAppInstance:
    """404 for an unknown id; otherwise first enforces upgradeTimeoutSeconds
    on the upgrade this instance takes part in (upgrade.py's lazy timeout),
    committing the rollback on its own so a later refusal in the same
    request can't undo it. A replacement rolled back here is gone: 404."""
    inst = _get_or_404(db, instance_id)
    owner = _upgrade_owner(db, inst)
    swept = _sweep_overdue_upgrade(db, owner) if owner is not None else False
    if swept is None:  # another replica swept it first: reload, which is a 404 if the replacement is gone
        return _get_or_404(db, instance_id)
    if swept and owner.instance_id != instance_id:
        raise framework_error(FrameworkError.RAPP_INSTANCE_NOT_FOUND,
                              detail=f"RAppInstance {instance_id} was an upgrade replacement, rolled back after "
                                     f"upgradeTimeoutSeconds={owner.upgrade_timeout_seconds}")
    return inst


def _fire(inst: RAppInstance, event: InstanceEvent) -> InstanceState:
    try:
        return RAPP_INSTANCE_FSM.fire(InstanceState(inst.state), event, instance=inst)
    except IllegalTransition as exc:
        raise illegal_transition_error(exc, f"RAppInstance {inst.instance_id}")


@app.post("/instances", status_code=202)
@idempotent("rapp-mgmt", status_code=202)
def create_instance(body: CreateInstanceRequest, request: Request, db: Session = Depends(get_session)):
    """CreateInstance — requires a validated package: AVAILABLE, or PRIMED
    (AVAILABLE plus pre-provisioned resources; D-SEC-RAPP-1); 404 for an
    unknown package, 409 for any other state. NFO handoff per Onboarding/rApp
    Mgmt LLD section 5: reads the package's TOSCA service template and issues
    NFO.Instantiate; the returned nfDeploymentId is kept as workloadRef, and
    TERMINATE hands it back to NFO. The same path provisions an upgrade's
    replacement instance (provisioning.py).
    """
    inst = provision_instance(db, body.packageId, configuration=body.config, autonomy_mode=body.autonomyMode,
                              region_scope=body.regionScope)
    db.commit()
    return {"instanceId": str(inst.instance_id), "oauthClientId": inst.oauth_client_id}


@app.post("/instances/{instance_id}/bootstrap-complete")
def bootstrap_complete(instance_id: uuid.UUID, db: Session = Depends(get_session)):
    """Called once the rApp container has bootstrapped via R1 Termination
    and registered with SME/DME — closes DEPLOYING -> RUNNING. 409 if the
    instance is not DEPLOYING.
    """
    inst = _load_instance(db, instance_id)
    if InstanceState(inst.state) != InstanceState.DEPLOYING:
        raise illegal_transition_error(IllegalTransition(InstanceState(inst.state), InstanceEvent.BOOTSTRAP_OK),
                                       f"RAppInstance {instance_id}")
    register_sme_declarations(inst)
    inst.state = _fire(inst, InstanceEvent.BOOTSTRAP_OK)
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
    else (e.g. AI/ML Workflow's retraining re-entry). 409 from any state
    but FAULTED.
    """
    inst = _load_instance(db, instance_id)
    inst.state = _fire(inst, InstanceEvent.RECOVER)
    db.commit()
    return {"instanceId": str(inst.instance_id), "state": inst.state}


@app.post("/instances/{instance_id}/upgrade")
def upgrade_instance(instance_id: uuid.UUID, body: UpgradeRequest, db: Session = Depends(get_session)):
    """UpgradeInstance — Annex A.1.2.2.1. Kicks off the two-row choreography
    in upgrade.py: the old instance (must be RUNNING, else 409) goes
    UPGRADING and a replacement is provisioned exactly like CreateInstance
    does (newPackageId must be AVAILABLE or PRIMED — 404 unknown, 409
    otherwise; its own oauthClientId, NFO deployment and usage
    registration), inheriting the old instance's configuration,
    autonomyMode and regionScope. The outcome is reported through
    upgrade/resolve, or the upgrade rolls back on its own once
    upgradeTimeoutSeconds have passed unresolved.
    """
    old = _load_instance(db, instance_id)
    try:
        new = start_upgrade(db, old, body.newPackageId)
    except IllegalTransition as exc:
        raise illegal_transition_error(exc, f"RAppInstance {instance_id}")
    db.commit()
    return {"newInstanceId": str(new.instance_id), "oldInstanceState": old.state,
            "oauthClientId": new.oauth_client_id}


@app.post("/instances/{instance_id}/upgrade/resolve")
def resolve_upgrade_outcome(instance_id: uuid.UUID, succeeded: bool, db: Session = Depends(get_session)):
    """Resolves the upgrade pending on the OLD instance. succeeded=true
    commits it: the replacement becomes RUNNING (registering its SME
    declarations if it never called bootstrap-complete itself) and the old
    instance is retired like a TERMINATE — DME/SME deregistration,
    credential revocation, NFO Terminate, usage/stop — then deleted.
    succeeded=false rolls back: the replacement is torn down the same way
    and deleted, the old instance returns to RUNNING. Answers with the
    surviving instance.

    404 for an unknown instance or one with no pending upgrade; 409
    LIFECYCLE_ILLEGAL_TRANSITION if the replacement can no longer be
    committed (e.g. it crashed); 409 RAPP_UPGRADE_TIMED_OUT for
    succeeded=true after upgradeTimeoutSeconds — the upgrade has already
    been rolled back (succeeded=false after the deadline just returns the
    rolled-back survivor).
    """
    old = _get_or_404(db, instance_id)
    if old.pending_upgrade_instance_id is None:
        raise framework_error(FrameworkError.RAPP_INSTANCE_NOT_FOUND, detail=f"RAppInstance {instance_id} has no pending upgrade")
    if _sweep_overdue_upgrade(db, old) is not False:
        if succeeded:
            raise framework_error(FrameworkError.RAPP_UPGRADE_TIMED_OUT,
                                  detail=f"upgrade of RAppInstance {instance_id} exceeded upgradeTimeoutSeconds="
                                         f"{old.upgrade_timeout_seconds} and was rolled back")
        return {"instanceId": str(old.instance_id), "state": old.state, "packageId": str(old.package_id)}
    new = _get_or_404(db, old.pending_upgrade_instance_id)
    try:
        resolve_upgrade(db, old, new, new_bootstrap_succeeded=succeeded, register_identity=register_sme_declarations)
    except IllegalTransition as exc:
        raise illegal_transition_error(exc, f"upgrade of RAppInstance {instance_id}")
    db.commit()
    survivor = new if succeeded else old
    return {"instanceId": str(survivor.instance_id), "state": survivor.state, "packageId": str(survivor.package_id)}


def _resolve_current(db: Session, instance_id: uuid.UUID) -> RAppInstance:
    """A live instance, or — for an id an upgrade has since superseded — the
    instance that replaced it last (the lineage in the version history).
    404 if the id is neither."""
    current_id = current_instance_id(db, instance_id)
    if current_id is None:
        raise framework_error(FrameworkError.RAPP_INSTANCE_NOT_FOUND, detail=f"no such RAppInstance {instance_id}")
    return _load_instance(db, current_id)


def _version_view(v) -> dict:
    return {"versionId": str(v.version_id), "kind": v.kind, "instanceId": str(v.instance_id),
            "packageId": str(v.package_id), "previousInstanceId": str(v.previous_instance_id),
            "previousPackageId": str(v.previous_package_id), "previousConfiguration": v.previous_configuration,
            "rolledBackByVersionId": str(v.rolled_back_by_version_id) if v.rolled_back_by_version_id else None,
            "committedAt": v.committed_at.isoformat()}


@app.post("/instances/{instance_id}/rollback")
def rollback_instance(instance_id: uuid.UUID, db: Session = Depends(get_session)):
    """RollbackInstance (OI-1-sa-rollback) — an upgrade back to the newest
    version in this instance's history that has not been rolled back
    already, restoring the package, configuration, autonomy mode and region
    scope that version's instance ran. Same two-row choreography as
    UpgradeInstance: the current instance goes UPGRADING, the replacement
    is resolved through upgrade/resolve (or rolls back on its own after
    upgradeTimeoutSeconds, leaving the current version running). Repeated
    rollbacks walk further back rather than flip-flopping.

    `instance_id` may be one an upgrade has since superseded (an SA SMOS
    monitor registered before the upgrade): the rollback applies to the
    instance that replaced it last, named in the answer as instanceId.

    404 unknown id; 409 ROLLBACK_HISTORY_UNAVAILABLE when there is nothing
    to roll back to; 409 LIFECYCLE_ILLEGAL_TRANSITION when the current
    instance is not RUNNING; 404/409 from provisioning when the earlier
    package is gone or no longer deployable.
    """
    current = _resolve_current(db, instance_id)
    try:
        started = start_rollback(db, current)
    except IllegalTransition as exc:
        raise illegal_transition_error(exc, f"RAppInstance {current.instance_id}")
    if started is None:
        raise framework_error(FrameworkError.ROLLBACK_HISTORY_UNAVAILABLE,
                              detail=f"RAppInstance {current.instance_id} has no upgrade left to roll back")
    new, target = started
    db.commit()
    return {"instanceId": str(current.instance_id), "newInstanceId": str(new.instance_id),
            "oldInstanceState": current.state, "fromPackageId": str(current.package_id),
            "toPackageId": str(new.package_id), "rollbackOfVersionId": str(target.version_id),
            "oauthClientId": new.oauth_client_id}


@app.get("/instances/{instance_id}/versions")
def list_instance_versions(instance_id: uuid.UUID, db: Session = Depends(get_session)):
    """The version history behind an instance (OI-1-sa-rollback), newest
    first: one entry per committed upgrade or rollback, with what the
    retired instance ran. A superseded id resolves to the current instance,
    as for rollback. rollbackTarget is the version a rollback would undo
    now (null: nothing to roll back to)."""
    current = _resolve_current(db, instance_id)
    target = rollback_target(db, current.instance_id)
    return {"instanceId": str(current.instance_id), "packageId": str(current.package_id), "state": current.state,
            "workloadRef": current.workload_ref,
            "rollbackTarget": _version_view(target) if target else None,
            "versions": [_version_view(v) for v in version_history(db, current.instance_id)]}


@app.post("/instances/{instance_id}/terminate")
def terminate_instance(instance_id: uuid.UUID, db: Session = Depends(get_session)):
    """TerminateInstance — Annex A.1.2.3. Legal from RUNNING, FAULTED (a
    crashed instance is retired without recovering it first) and DEPLOYING
    (one whose container never bootstrapped); 409 otherwise, and 409 for an
    upgrade's pending replacement (resolve the upgrade instead).

    Credential revocation is part of this transition itself
    (statemachine.py's _revoke_credential action), not a separate step
    (closes RT-3), after the DME/SME deregistration keyed on that
    credential. Then the workload itself: NFO Terminate for workloadRef
    (the nfDeploymentId CreateInstance received) and usage/stop for the
    PackageUsageRegistration that Onboarding's deprime and cascade-delete
    guards read. Both are best-effort — an unreachable NFO or Onboarding
    never blocks the teardown — but their outcome is recorded in
    lastTeardown.

    HISTORY.md §5: this used to delete the instance row
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
    inst = _load_instance(db, instance_id)
    owner = _upgrade_owner(db, inst)
    if owner is not None and owner.instance_id != inst.instance_id:
        raise framework_error(FrameworkError.LIFECYCLE_ILLEGAL_TRANSITION,
                              detail=f"RAppInstance {instance_id} is the pending replacement of an upgrade of "
                                     f"{owner.instance_id}; resolve that upgrade instead")
    inst.state = _fire(inst, InstanceEvent.TERMINATE)
    inst.last_teardown = release_instance_resources(inst, "TERMINATE")
    db.commit()
    return {"instanceId": str(inst.instance_id), "state": inst.state, "lastTeardown": inst.last_teardown}


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
    inst = _load_instance(db, instance_id)
    if inst.state != InstanceState.UNDEPLOYED:
        raise framework_error(FrameworkError.RAPP_INSTANCE_NOT_UNDEPLOYED,
                               detail=f"instance {instance_id} is not UNDEPLOYED (state={inst.state})")
    db.query(RAppFaultReport).filter(RAppFaultReport.instance_id == instance_id).delete()
    db.query(RAppPerformanceReport).filter(RAppPerformanceReport.instance_id == instance_id).delete()
    db.delete(inst)
    db.commit()


@app.get("/instances/{instance_id}/config")
def get_config(instance_id: uuid.UUID, db: Session = Depends(get_session)):
    inst = _get_or_404(db, instance_id)
    return inst.configuration or {}


@app.put("/instances/{instance_id}/config")
def set_config(instance_id: uuid.UUID, config: dict, db: Session = Depends(get_session)):
    inst = _get_or_404(db, instance_id)
    inst.configuration = config
    db.commit()
    return {"status": "updated"}


@app.post("/instances/{instance_id}/performance")
def report_performance(instance_id: uuid.UUID, metrics: dict, db: Session = Depends(get_session)):
    _get_or_404(db, instance_id)
    db.add(RAppPerformanceReport(instance_id=instance_id, metrics=metrics))
    db.commit()
    return {"status": "recorded"}


@app.post("/instances/{instance_id}/fault")
def report_fault(instance_id: uuid.UUID, severity: str, description: str = "", db: Session = Depends(get_session)):
    """Records every fault report; severity=critical additionally fires
    CRASH (RUNNING -> FAULTED). A critical fault on an instance that is not
    RUNNING is refused with 409 and not recorded.
    """
    inst = _load_instance(db, instance_id)
    if severity == "critical":
        inst.state = _fire(inst, InstanceEvent.CRASH)
    db.add(RAppFaultReport(instance_id=instance_id, severity=severity, description=description))
    db.commit()
    return {"status": "recorded", "instanceState": inst.state}


@app.get("/instances")
def list_instances(state: str | None = None, limit: int = PageLimit, offset: int = PageOffset,
                    db: Session = Depends(get_session)):
    # lazy upgradeTimeoutSeconds enforcement (upgrade.py) for every upgrade in flight
    for old in list(db.scalars(select(RAppInstance).where(RAppInstance.state == InstanceState.UPGRADING))):
        _sweep_overdue_upgrade(db, old)
    stmt = select(RAppInstance)
    if state:
        stmt = stmt.where(RAppInstance.state == state)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [{"instanceId": str(i.instance_id), "packageId": str(i.package_id), "state": i.state,
             "autonomyMode": i.autonomy_mode} for i in page["items"]]}


@app.get("/instances/{instance_id}")
def get_instance(instance_id: uuid.UUID, db: Session = Depends(get_session)):
    """HISTORY.md §5: no single-instance detail read existed at
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
    HISTORY.md §7's Onboarding/rApp Mgmt finding 3, closed), and the
    caller-supplied configuration, alongside the identity/state fields
    list_instances already returns. lastTeardown is the recorded outcome of
    the most recent NFO Terminate / usage/stop this row performed or
    inherited through an upgrade. Reading an instance enforces its
    upgrade's upgradeTimeoutSeconds (an overdue upgrade rolls back).
    """
    inst = _load_instance(db, instance_id)
    return {
        "instanceId": str(inst.instance_id), "packageId": str(inst.package_id), "state": inst.state,
        "workloadRef": inst.workload_ref, "configuration": inst.configuration,
        "pendingUpgradeInstanceId": str(inst.pending_upgrade_instance_id) if inst.pending_upgrade_instance_id else None,
        "smeServiceIds": inst.sme_service_ids,
        "autonomyMode": inst.autonomy_mode, "regionScope": inst.region_scope,
        "lastTeardown": inst.last_teardown,
    }


@app.get("/instances/{instance_id}/performance")
def list_performance_reports(instance_id: uuid.UUID, limit: int = PageLimit, offset: int = PageOffset,
                              db: Session = Depends(get_session)):
    """Read side of report_performance above — previously write-only, so
    an operator had no way to see what an rApp had reported at all.
    Newest first (the GUI's KPI sparkline only ever wants the recent tail).
    """
    _get_or_404(db, instance_id)
    stmt = select(RAppPerformanceReport).where(RAppPerformanceReport.instance_id == instance_id).order_by(RAppPerformanceReport.reported_at.desc())
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [{"reportId": str(r.id), "metrics": r.metrics, "reportedAt": r.reported_at.isoformat()} for r in page["items"]]}


@app.get("/instances/{instance_id}/faults")
def list_fault_reports(instance_id: uuid.UUID, limit: int = PageLimit, offset: int = PageOffset,
                        db: Session = Depends(get_session)):
    """Read side of report_fault above, same shape as list_performance_reports."""
    _get_or_404(db, instance_id)
    stmt = select(RAppFaultReport).where(RAppFaultReport.instance_id == instance_id).order_by(RAppFaultReport.reported_at.desc())
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [{"faultId": str(r.id), "severity": r.severity, "description": r.description,
             "reportedAt": r.reported_at.isoformat()} for r in page["items"]]}
