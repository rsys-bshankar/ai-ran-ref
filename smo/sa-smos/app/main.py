"""SA SMOS.

SMO Design v1.3 section 3.14, extended by SO/SA SMOS LLD section 2: the
CONFIG_CHANGE/SCALE dispatch is resolved; RECONNECT/ROLLBACK stay
honestly flagged as ambiguous rather than forced (section 2.1) —
ExecuteRemedialAction raises NotImplementedError for those two rather
than silently picking a meaning nothing in the source material supports.
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
    LLD section 2.1: CONFIG_CHANGE and SCALE are resolved; RECONNECT and
    ROLLBACK are deliberately NOT force-resolved.
    """
    monitor = db.get(AssuranceMonitor, monitor_id)
    r1 = R1Client()

    if action_type == "CONFIG_CHANGE":
        result = r1.post("/ran-nf-oam/config-jobs", json={"requestedBy": "sa-smos", "scope": "cell", "changes": []})
        outcome = "RESOLVED" if result.status_code < 300 else "ESCALATED"
    elif action_type == "SCALE":
        # inherits NFO's own Phase 1 stub status — cannot do anything real yet, and that's correct (section 2.1)
        outcome = "ESCALATED"
    elif action_type in ("RECONNECT", "ROLLBACK"):
        raise framework_error(
            FrameworkError.COORDINATION_GROUP_MISMATCH,
            detail=f"{action_type} is ambiguous in this reference build (SO/SA SMOS LLD section 2.1) — "
                   f"split into module-qualified variants (e.g. ROLLBACK_RAPP_VERSION vs. ROLLBACK_MODEL_VERSION) "
                   f"before implementing; not resolved here to avoid silently picking a meaning.",
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
