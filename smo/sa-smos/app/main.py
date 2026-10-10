"""SA SMOS (Service Assurance SMOS): assurance monitors, threshold evaluation and remedial actions.

What it is: the FastAPI app of the `sa-smos` module (R1 route `/sa-smos` via R1 Termination). A monitor holds requirement thresholds and at most one target. A caller evaluates current
metrics against it and, on a breach, asks for a remedial action, which this module dispatches to the module that can perform it and records as RESOLVED or ESCALATED. The generic O1-CM
intent handler is a second, independent role in `o1cm.py` and is mounted at the end of this file.

Where it sits: called by the GUI BFF; calls SO SMOS (to resolve a deployment), NFO (heal), rApp Management (rollback, versions), AIMgF (retrain) and RAN NF OAM (config job) through
`R1Client`. Design: SMO Design v1.3 section 3.14 and SO/SA SMOS LLD section 2; README sections 1.5 and 2.

Dispatch is chosen by the monitor's target first, then by the action type (see `execute_remedial_action`): a coordination-group monitor always retrains the group; ROLLBACK needs a
rApp-instance target, because only rApp Management keeps a version history (OI-1-sa-rollback); RECONNECT means healing the monitor's NFO workload, resolved from the rApp instance or from
the order's completed DEPLOY step; SCALE always escalates (NFO scaling is a Phase 1 stub).

What it owns: the `assurance_monitor` and `remedial_action` tables (`models.py`). It evaluates only when called; nothing polls. It does not own orders, deployments, rApp versions or intents.
"""

import uuid

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.logconfig import install_logging
from smo_shared.metrics import install_metrics
from smo_shared.health import database_check, install_health, sme_token_check
from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.r1_client import R1Client
from smo_shared.openapi_security import apply_r1_gateway_security
from smo_shared.correlation import apply_correlation_id
from smo_shared.pagination import PageLimit, PageOffset, paginate

from .models import AssuranceMonitor, RemedialAction

app = FastAPI(title="SA SMOS")
install_logging(app)  # structured JSON logs and one access-log line per request (PR-OBS-1)
install_metrics(app)  # /metrics and request count/latency series (PR-OBS-2)
apply_r1_gateway_security(app)
apply_correlation_id(app)


install_health(app, checks=[database_check, sme_token_check])  # /live, /ready and the /health alias (PR-ST-7)


@app.post("/monitors", status_code=201)
def register_assurance_monitor(target_order_id: uuid.UUID | None = None, target_coordination_group_id: uuid.UUID | None = None,
                                target_rapp_instance_id: uuid.UUID | None = None,
                                analytics_subscription_id: uuid.UUID | None = None, thresholds: dict | None = None, db: Session = Depends(get_session)):
    """At most one target: a service order (NF deployment remediation), a
    model coordination group (retrain), or a rApp instance (remediation
    through rApp Management, including ROLLBACK)."""
    # Maintainer notes (not published). Takes the targets and `analytics_subscription_id` as query parameters and the thresholds ({metric: minimum}) as the JSON body. More than one
    # target is a 422 COORDINATION_GROUP_MISMATCH (the code is reused; the database CHECK `one_target_only` is the backstop). No target is allowed. None of the ids is checked against the module that owns it.
    targets = [t for t in (target_order_id, target_coordination_group_id, target_rapp_instance_id) if t is not None]
    if len(targets) > 1:
        raise framework_error(FrameworkError.COORDINATION_GROUP_MISMATCH,
                              detail="at most one of targetOrderId/targetCoordinationGroupId/targetRappInstanceId")
    monitor = AssuranceMonitor(target_order_id=target_order_id, target_coordination_group_id=target_coordination_group_id,
                                target_rapp_instance_id=target_rapp_instance_id,
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
    # Maintainer notes (not published). Pure comparison, nothing is stored: a metric is a breach when its current value is below the threshold, and a metric missing from
    # `current_metrics` counts as 0, so it breaches any positive threshold. 404 ASSURANCE_MONITOR_NOT_FOUND for an unknown monitor.
    monitor = db.get(AssuranceMonitor, monitor_id)
    if monitor is None:
        raise framework_error(FrameworkError.ASSURANCE_MONITOR_NOT_FOUND, detail="no such AssuranceMonitor")
    breaches = {k: v for k, v in monitor.requirement_thresholds.items() if current_metrics.get(k, 0) < v}
    return {"monitorId": str(monitor.monitor_id), "breaches": breaches}


@app.post("/monitors/{monitor_id}/remedial-actions", status_code=201)
def execute_remedial_action(monitor_id: uuid.UUID, action_type: str, requester_is_admin: bool = False, db: Session = Depends(get_session)):
    """ExecuteRemedialAction — gated by autoExecutionScopeConfig, admin-only
    to change (REQ-CNFG-ADM pattern, unchanged). Dispatch per SO/SA SMOS
    LLD section 2.1: CONFIG_CHANGE, SCALE, and RECONNECT are resolved;
    ROLLBACK is resolved for a rApp-instance-scoped monitor and refused
    (409 ROLLBACK_HISTORY_UNAVAILABLE) for any other (see the module
    docstring). A coordination-group-scoped monitor bypasses all four
    actionType branches below — see the module docstring's
    MLModelCoordinationGroup convergence note.

    A rApp Management refusal (nothing left to roll back, the instance not
    RUNNING, the earlier package gone) is not an error here: the action is
    recorded ESCALATED, with rApp Management's reason in `detail`. A
    RESOLVED rollback has started an upgrade back; `result` names the
    replacement instance, which rApp Management resolves like any upgrade.
    """
    # Maintainer notes (not published). Answers 201 with {actionId, outcome[, result][, detail]}; 404 ASSURANCE_MONITOR_NOT_FOUND, 409 ROLLBACK_HISTORY_UNAVAILABLE, 422 COORDINATION_GROUP_MISMATCH
    # (reused for an unknown action type).
    # The branches are tried in this order and the first match decides: coordination-group target (any action type: retrain through AIMgF), CONFIG_CHANGE, SCALE, ROLLBACK, RECONNECT on a rApp-instance
    # target, RECONNECT otherwise (order-scoped or no target), else 422. A downstream non-2xx answer is an ESCALATED outcome, not an HTTP error; only ROLLBACK returns the reason in `detail`. A transport
    # exception from R1Client is not caught and is a 500 with nothing recorded.
    # One RemedialAction row is written and committed after the dispatch. `auto_executed` is the caller-supplied `requester_is_admin` flag stored as given: nothing in this module checks the caller's role
    # and `auto_execution_scope_config` is never read.
    monitor = db.get(AssuranceMonitor, monitor_id)
    if monitor is None:
        raise framework_error(FrameworkError.ASSURANCE_MONITOR_NOT_FOUND, detail="no such AssuranceMonitor")
    r1 = R1Client()
    result, detail = None, None

    # Dispatch is decided by the target first: a coordination-group monitor retrains whatever action type was asked for, because the NF-oriented actions below have no meaning for a model group.
    if monitor.target_coordination_group_id is not None:
        resp = r1.post("/aimgf/training-jobs", json={
            "modelCoordinationGroupId": str(monitor.target_coordination_group_id), "producerId": "sa-smos",
        })
        outcome = "RESOLVED" if resp.status_code < 300 else "ESCALATED"
    elif action_type == "CONFIG_CHANGE":
        # A demonstration of the dispatch: the job is created with an empty change list, so it changes no configuration. Real CM changes go through the O1-CM handler (o1cm.py).
        result = r1.post("/ran-nf-oam/config-jobs", json={"requestedBy": "sa-smos", "scope": "cell", "changes": []})
        outcome = "RESOLVED" if result.status_code < 300 else "ESCALATED"
    elif action_type == "SCALE":
        # inherits NFO's own Phase 1 stub status — cannot do anything real yet, and that's correct (section 2.1)
        outcome = "ESCALATED"
    elif action_type == "ROLLBACK":
        if monitor.target_rapp_instance_id is None:
            raise framework_error(
                FrameworkError.ROLLBACK_HISTORY_UNAVAILABLE,
                detail="ROLLBACK needs a rApp-instance-scoped monitor (targetRappInstanceId): only rApp Management "
                       "keeps a version history; NFO keeps none for a bare NF deployment.",
            )
        resp = r1.post(f"/rapp-mgmt/instances/{monitor.target_rapp_instance_id}/rollback")
        if resp.status_code < 300:
            outcome, result = "RESOLVED", resp.json()
        else:
            outcome, detail = "ESCALATED", _problem_detail(resp)
    # This rApp-scoped RECONNECT must come before the plain RECONNECT branch below, which resolves the workload from an order and would find none for a rApp-instance monitor.
    elif action_type == "RECONNECT" and monitor.target_rapp_instance_id is not None:
        nf_deployment_id = _resolve_rapp_workload(r1, monitor.target_rapp_instance_id)
        if nf_deployment_id is None:
            outcome = "ESCALATED"
        else:
            heal_resp = r1.post(f"/nfo/deployments/{nf_deployment_id}/heal")
            outcome = "RESOLVED" if heal_resp.status_code < 300 else "ESCALATED"
    elif action_type == "RECONNECT":
        nf_deployment_id = _resolve_deployed_nf(r1, monitor.target_order_id)
        if nf_deployment_id is None:
            # No concrete deployment to reconnect — either this monitor isn't
            # order-scoped at all, or the order's DEPLOY step never completed.
            outcome = "ESCALATED"
        else:
            heal_resp = r1.post(f"/nfo/deployments/{nf_deployment_id}/heal")
            outcome = "RESOLVED" if heal_resp.status_code < 300 else "ESCALATED"
    else:
        # COORDINATION_GROUP_MISMATCH is the code used for an unknown action type (README section 2.7); it is a 422.
        raise framework_error(FrameworkError.COORDINATION_GROUP_MISMATCH, detail=f"unknown actionType {action_type}")

    # The row is written only after the downstream call returned, so a failure that raises leaves no record of the attempt.
    action = RemedialAction(monitor_id=monitor_id, action_type=action_type, auto_executed=requester_is_admin, outcome=outcome)
    db.add(action)
    db.commit()
    response = {"actionId": str(action.action_id), "outcome": outcome}
    if result is not None:
        response["result"] = result
    if detail is not None:
        response["detail"] = detail
    return response


@app.post("/monitors/{monitor_id}/escalate")
def escalate_to_operator(monitor_id: uuid.UUID, reason: str, db: Session = Depends(get_session)):
    # Maintainer notes (not published). Records a RemedialAction of type CONFIG_CHANGE with outcome ESCALATED, which is how an operator queue entry is made without dispatching anything. `reason` is echoed
    # in the answer and not stored. The monitor id is not checked: on a database that enforces the foreign key an unknown monitor fails the insert (a 500), elsewhere it leaves an orphan row.
    action = RemedialAction(monitor_id=monitor_id, action_type="CONFIG_CHANGE", auto_executed=False, outcome="ESCALATED")
    db.add(action)
    db.commit()
    return {"actionId": str(action.action_id), "reason": reason, "outcome": "ESCALATED"}


def _resolve_deployed_nf(r1: R1Client, target_order_id: uuid.UUID | None) -> str | None:
    """Returns the NFO deployment id of an order's completed DEPLOY step, or None when it cannot be resolved.

    The monitor stores only `target_order_id`, so the order is read back from SO SMOS (`GET /so-smos/orders/{id}`) and its first DEPLOY step with status COMPLETED supplies
    `result.nfDeploymentId` (the shape `so-smos/app/dispatch.py` records). None for no order, an order SO SMOS does not answer 200 for, or an order without such a step; the caller then escalates.
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


def _resolve_rapp_workload(r1: R1Client, instance_id: uuid.UUID) -> str | None:
    """Returns the NFO deployment (`workloadRef`) of the rApp instance a monitor targets, or of the instance that replaced it through an upgrade; None unless rApp Management answers 200.

    rApp Management's version history (`GET /rapp-mgmt/instances/{id}/versions`) does the resolving, so a monitor keeps working after an upgrade changes the instance id.
    """
    resp = r1.get(f"/rapp-mgmt/instances/{instance_id}/versions")
    if resp.status_code != 200:
        return None
    return resp.json().get("workloadRef")


def _problem_detail(resp) -> str:
    """The text recorded as `detail` for a refused downstream call: 'TITLE: detail' when the answer carries a problem object, the plain `detail` otherwise, `HTTP <status>` when the body is not JSON.
    """
    try:
        body = resp.json().get("detail")
    except ValueError:
        return f"HTTP {resp.status_code}"
    if isinstance(body, dict):
        return f"{body.get('title')}: {body.get('detail')}"
    return str(body)


@app.get("/monitors")
def list_assurance_monitors(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    """List read over AssuranceMonitor — previously write-only, so the
    thresholds being evaluated were invisible to an operator."""
    # Maintainer notes (not published). Paginated by smo_shared.pagination (ordered by primary key, monitor_id); items are `_monitor_view`.
    page = paginate(db, select(AssuranceMonitor), limit, offset)
    return {**page, "items": [_monitor_view(m) for m in page["items"]]}


@app.get("/monitors/{monitor_id}")
def get_assurance_monitor(monitor_id: uuid.UUID, db: Session = Depends(get_session)):
    # Maintainer notes (not published). 404 ASSURANCE_MONITOR_NOT_FOUND for an unknown monitor.
    monitor = db.get(AssuranceMonitor, monitor_id)
    if monitor is None:
        raise framework_error(FrameworkError.ASSURANCE_MONITOR_NOT_FOUND, detail="no such AssuranceMonitor")
    return _monitor_view(monitor)


@app.get("/remedial-actions")
def list_remedial_actions(monitor_id: uuid.UUID | None = None, outcome: str | None = None, limit: int = PageLimit,
                           offset: int = PageOffset, db: Session = Depends(get_session)):
    """Every RemedialAction row, optionally per-monitor or per-outcome —
    `outcome=ESCALATED` is the operator's escalation queue (the GUI
    dashboard's SA SMOS tile)."""
    # Maintainer notes (not published). `monitor_id` and `outcome` are exact-match filters, combined with AND; `outcome=ESCALATED` is the operator's queue. Paginated (ordered by primary key, action_id).
    stmt = select(RemedialAction)
    if monitor_id:
        stmt = stmt.where(RemedialAction.monitor_id == monitor_id)
    if outcome:
        stmt = stmt.where(RemedialAction.outcome == outcome)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [{"actionId": str(a.action_id), "monitorId": str(a.monitor_id), "actionType": a.action_type,
             "autoExecuted": a.auto_executed, "outcome": a.outcome} for a in page["items"]]}


def _monitor_view(m: AssuranceMonitor) -> dict:
    """The JSON view of a monitor: its id, the four optional references as strings or null, and the thresholds."""
    return {"monitorId": str(m.monitor_id),
            "targetOrderId": str(m.target_order_id) if m.target_order_id else None,
            "targetCoordinationGroupId": str(m.target_coordination_group_id) if m.target_coordination_group_id else None,
            "targetRappInstanceId": str(m.target_rapp_instance_id) if m.target_rapp_instance_id else None,
            "analyticsSubscriptionId": str(m.analytics_subscription_id) if m.analytics_subscription_id else None,
            "thresholds": m.requirement_thresholds}


# ---------------------------------------------------------------- Wave 8: generic O1-CM intent handler (W8-07)
# Imported at the end of the file and mounted last; o1cm.py has its own router and its own R1 client and does not import this module. E402 is exempted for this placement.
from .o1cm import router as _o1cm_router  # noqa: E402

app.include_router(_o1cm_router)
