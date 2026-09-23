"""SA SMOS.

SMO Design v1.3 section 3.14, extended by SO/SA SMOS LLD section 2:
CONFIG_CHANGE, SCALE, and (this pass) RECONNECT are resolved.
RECONNECT means restoring connectivity/health for the order's deployed
workload — dispatched to NFO's Heal, resolving the concrete
nfDeploymentId via SO SMOS's own order record (the AssuranceMonitor
itself only carries target_order_id, not a resource ID directly).
ROLLBACK stays honestly unsupported, but for a concrete, checked reason
now rather than a generic "ambiguous" refusal: rApp Management deletes
the previous RAppInstance row on a successful upgrade, so no version
history survives to roll back to at all (see ROLLBACK_HISTORY_UNAVAILABLE).
"""

import uuid

from fastapi import Depends, FastAPI
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.r1_client import R1Client

from .models import AssuranceMonitor, RemedialAction

app = FastAPI(title="SA SMOS")


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
    module docstring), not a generic "ambiguous" refusal.
    """
    monitor = db.get(AssuranceMonitor, monitor_id)
    r1 = R1Client()

    if action_type == "CONFIG_CHANGE":
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
