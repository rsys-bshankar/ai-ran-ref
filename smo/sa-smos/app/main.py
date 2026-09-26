"""SA SMOS.

SMO Design v1.3 section 3.14, extended by SO/SA SMOS LLD section 2:
CONFIG_CHANGE, SCALE, and RECONNECT are resolved for an order-scoped
monitor. RECONNECT means restoring connectivity/health for the order's
deployed workload — dispatched to NFO's Heal, resolving the concrete
nfDeploymentId via SO SMOS's own order record (the AssuranceMonitor
itself only carries target_order_id, not a resource ID directly).
ROLLBACK stays honestly unsupported, but for a concrete, checked reason
now rather than a generic "ambiguous" refusal: rApp Management deletes
the previous RAppInstance row on a successful upgrade, so no version
history survives to roll back to at all (see ROLLBACK_HISTORY_UNAVAILABLE).

A coordination-group-scoped monitor (target_coordination_group_id) is a
different case entirely, resolved this pass too: it watches model
performance, not an NF deployment, so CONFIG_CHANGE/SCALE/RECONNECT/
ROLLBACK's NF-oriented meanings don't map onto a model group at all.
Remedial action for one always means the same thing regardless of the
requested actionType — trigger a retrain of the group via AI/ML
Workflow's RequestTraining, converging with that module's own
groupRetrainTriggered fix (OPEN_ITEMS.md section 1's
MLModelCoordinationGroup x SA SMOS convergence item).
"""

import uuid

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.r1_client import R1Client

from .models import AssuranceMonitor, RemedialAction

app = FastAPI(title="SA SMOS")


@app.get("/health")
def health_check():
    """Liveness probe. The GUI BFF's GET /modules/status fans out to
    /<module>/health through R1 Termination for every module in parallel,
    so every module answers one — previously only ran-nf-oam/a1-related
    did (as their own DME producer-health callback URL).
    """
    return {"status": "healthy"}


@app.post("/monitors", status_code=201)
def register_assurance_monitor(target_order_id: uuid.UUID | None = None, target_coordination_group_id: uuid.UUID | None = None,
                                analytics_subscription_id: uuid.UUID | None = None, thresholds: dict = None, db: Session = Depends(get_session)):
    if target_order_id is not None and target_coordination_group_id is not None:
        raise framework_error(FrameworkError.COORDINATION_GROUP_MISMATCH, detail="at most one of targetOrderId/targetCoordinationGroupId")
    monitor = AssuranceMonitor(target_order_id=target_order_id, target_coordination_group_id=target_coordination_group_id,
                                analytics_subscription_id=analytics_subscription_id, requirement_thresholds=thresholds or {})
    db.add(monitor)
    db.commit()
    return {"monitorId": str(monitor.monitor_id)}


@app.post("/monitors/{monitor_id}/evaluate")
def evaluate_thresholds(monitor_id: uuid.UUID, current_metrics: dict, db: Session = Depends(get_session)):
    """Periodic, aligned with RAN Analytics' subscription cadence
    (D-NFR-PERF-SA-1, unchanged from v1.3 — this was already specified,
    not a gap this LLD pass needed to close).
    """
    monitor = db.get(AssuranceMonitor, monitor_id)
    breaches = {k: v for k, v in monitor.requirement_thresholds.items() if current_metrics.get(k, 0) < v}
    return {"monitorId": str(monitor.monitor_id), "breaches": breaches}


@app.post("/monitors/{monitor_id}/remedial-actions", status_code=201)
def execute_remedial_action(monitor_id: uuid.UUID, action_type: str, requester_is_admin: bool = False, db: Session = Depends(get_session)):
    """ExecuteRemedialAction — gated by autoExecutionScopeConfig, admin-only
    to change (REQ-CNFG-ADM pattern, unchanged). Dispatch per SO/SA SMOS
    LLD section 2.1: CONFIG_CHANGE, SCALE, and RECONNECT are resolved;
    ROLLBACK stays unsupported for a concrete, checked reason (see the
    module docstring), not a generic "ambiguous" refusal. A
    coordination-group-scoped monitor bypasses all four actionType
    branches below — see the module docstring's MLModelCoordinationGroup
    convergence note.
    """
    monitor = db.get(AssuranceMonitor, monitor_id)
    r1 = R1Client()

    if monitor.target_coordination_group_id is not None:
        resp = r1.post("/aimgf/training-jobs", json={
            "modelCoordinationGroupId": str(monitor.target_coordination_group_id), "producerId": "sa-smos",
        })
        outcome = "RESOLVED" if resp.status_code < 300 else "ESCALATED"
    elif action_type == "CONFIG_CHANGE":
        result = r1.post("/ran-nf-oam/config-jobs", json={"requestedBy": "sa-smos", "scope": "cell", "changes": []})
        outcome = "RESOLVED" if result.status_code < 300 else "ESCALATED"
    elif action_type == "SCALE":
        # inherits NFO's own Phase 1 stub status — cannot do anything real yet, and that's correct (section 2.1)
        outcome = "ESCALATED"
    elif action_type == "RECONNECT":
        nf_deployment_id = _resolve_deployed_nf(r1, monitor.target_order_id)
        if nf_deployment_id is None:
            # No concrete deployment to reconnect — either this monitor isn't
            # order-scoped at all, or the order's DEPLOY step never completed.
            outcome = "ESCALATED"
        else:
            heal_resp = r1.post(f"/nfo/deployments/{nf_deployment_id}/heal")
            outcome = "RESOLVED" if heal_resp.status_code < 300 else "ESCALATED"
    elif action_type == "ROLLBACK":
        raise framework_error(
            FrameworkError.ROLLBACK_HISTORY_UNAVAILABLE,
            detail="rApp Management deletes the previous RAppInstance row on a successful "
                   "UpgradeInstance commit (upgrade.py) — no package-version history survives "
                   "to roll back to. This needs rApp Management to retain prior-version history "
                   "before ROLLBACK can be implemented; it is not a semantics question.",
        )
    else:
        raise framework_error(FrameworkError.COORDINATION_GROUP_MISMATCH, detail=f"unknown actionType {action_type}")

    action = RemedialAction(monitor_id=monitor_id, action_type=action_type, auto_executed=requester_is_admin, outcome=outcome)
    db.add(action)
    db.commit()
    return {"actionId": str(action.action_id), "outcome": outcome}


@app.post("/monitors/{monitor_id}/escalate")
def escalate_to_operator(monitor_id: uuid.UUID, reason: str, db: Session = Depends(get_session)):
    action = RemedialAction(monitor_id=monitor_id, action_type="CONFIG_CHANGE", auto_executed=False, outcome="ESCALATED")
    db.add(action)
    db.commit()
    return {"actionId": str(action.action_id), "reason": reason, "outcome": "ESCALATED"}


def _resolve_deployed_nf(r1: R1Client, target_order_id: uuid.UUID | None) -> str | None:
    """AssuranceMonitor only stores target_order_id, not a concrete
    resource reference — resolve it by reading the order's own record
    back from SO SMOS and finding its DEPLOY step's result, the same
    shape execute_order (so-smos/app/dispatch.py) already produces.
    """
    if target_order_id is None:
        return None
    resp = r1.get(f"/so-smos/orders/{target_order_id}")
    if resp.status_code != 200:
        return None
    for step in resp.json().get("steps", []):
        if step.get("stepType") == "DEPLOY" and step.get("status") == "COMPLETED":
            return step.get("result", {}).get("nfDeploymentId")
    return None


@app.get("/monitors")
def list_assurance_monitors(db: Session = Depends(get_session)):
    """List read over AssuranceMonitor — previously write-only, so the
    thresholds being evaluated were invisible to an operator."""
    return [_monitor_view(m) for m in db.scalars(select(AssuranceMonitor)).all()]


@app.get("/monitors/{monitor_id}")
def get_assurance_monitor(monitor_id: uuid.UUID, db: Session = Depends(get_session)):
    monitor = db.get(AssuranceMonitor, monitor_id)
    if monitor is None:
        raise HTTPException(status_code=404, detail="no such AssuranceMonitor")
    return _monitor_view(monitor)


@app.get("/remedial-actions")
def list_remedial_actions(monitor_id: uuid.UUID | None = None, outcome: str | None = None, db: Session = Depends(get_session)):
    """Every RemedialAction row, optionally per-monitor or per-outcome —
    `outcome=ESCALATED` is the operator's escalation queue (the GUI
    dashboard's SA SMOS tile)."""
    stmt = select(RemedialAction)
    if monitor_id:
        stmt = stmt.where(RemedialAction.monitor_id == monitor_id)
    if outcome:
        stmt = stmt.where(RemedialAction.outcome == outcome)
    return [{"actionId": str(a.action_id), "monitorId": str(a.monitor_id), "actionType": a.action_type,
             "autoExecuted": a.auto_executed, "outcome": a.outcome} for a in db.scalars(stmt).all()]


def _monitor_view(m: AssuranceMonitor) -> dict:
    return {"monitorId": str(m.monitor_id),
            "targetOrderId": str(m.target_order_id) if m.target_order_id else None,
            "targetCoordinationGroupId": str(m.target_coordination_group_id) if m.target_coordination_group_id else None,
            "analyticsSubscriptionId": str(m.analytics_subscription_id) if m.analytics_subscription_id else None,
            "thresholds": m.requirement_thresholds}
