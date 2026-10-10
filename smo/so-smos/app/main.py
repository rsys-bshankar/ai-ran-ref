"""SO SMOS (Service Orchestration SMOS): the HTTP routes to submit, read, cancel and list multi-step service orders.

What it is: the FastAPI app of the `so-smos` module (R1 route `/so-smos` via R1 Termination). `POST /orders` runs a list of steps in order through the dispatch table in
`dispatch.py` and stores the executed steps on a `ServiceOrder` row. The other routes read, cancel and list those rows.

Where it sits: called by the GUI BFF and by SA SMOS (which reads an order to find a monitor's deployment); it calls RAN NF OAM, NFO, FOCOM and AIMgF through `R1Client`, one call
per step. Design: SMO Design v1.3 section 3.13 and SO/SA SMOS LLD section 1; README sections 1.5 and 2.

What it owns: the `service_order` table (`models.py`). It performs no step itself and does not roll back completed steps.

Before editing: execution is synchronous, inside the request, and the order row is committed once, after every step has run (see `submit_service_order`).
"""

import uuid

from fastapi import Depends, FastAPI
from pydantic import BaseModel, field_validator
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

from .dispatch import execute_order
from .models import ServiceOrder

app = FastAPI(title="SO SMOS")
install_logging(app)  # structured JSON logs and one access-log line per request (PR-OBS-1)
install_metrics(app)  # /metrics and request count/latency series (PR-OBS-2)
apply_r1_gateway_security(app)
apply_correlation_id(app)


install_health(app, checks=[database_check, sme_token_check])  # /live, /ready and the /health alias (PR-ST-7)


# Request body of POST /orders. `scope` is free text; `steps` is a list of dicts, each of which must carry a string `stepType` and `targetModule` (checked by the validator, a 422
# otherwise) plus whatever fields its dispatcher reads (README section 2.4). `rmihRegistration` is stored with the order and used by nothing else in this module.
class SubmitOrderRequest(BaseModel):
    scope: str
    steps: list[dict]
    rmihRegistration: str = "so-smos"

    @field_validator("steps")
    @classmethod
    def _each_step_names_its_type_and_target(cls, steps: list[dict]) -> list[dict]:
        for step in steps:
            if not isinstance(step.get("stepType"), str) or not isinstance(step.get("targetModule"), str):
                raise ValueError("every step needs a string stepType and targetModule")
        return steps


@app.post("/orders", status_code=202)
def submit_service_order(body: SubmitOrderRequest, db: Session = Depends(get_session)):
    # Maintainer notes (not published). Answers 202 with {orderId, steps} after every step has run; a step failure is in the body (`status` FAILED, `error`), not an HTTP status.
    # The order row is flushed, then all steps are dispatched, then `order.steps` is replaced by the executed steps and the row is committed once. So the order is not visible to
    # readers while it executes, and if the process dies midway the downstream calls already made leave no order record. `steps` is assigned a new list (not mutated) because a plain JSON
    # column does not track in-place changes.
    order = ServiceOrder(scope=body.scope, steps=body.steps, rmih_registration=body.rmihRegistration)
    db.add(order)
    db.flush()

    r1 = R1Client()
    # execute_order catches every exception from a dispatcher, so this call does not raise for a downstream failure.
    results = execute_order(r1, body.steps)
    order.steps = results
    db.commit()
    return {"orderId": str(order.order_id), "steps": results}


def _order_or_404(db: Session, order_id: uuid.UUID) -> ServiceOrder:
    """Returns the order or raises the SERVICE_ORDER_NOT_FOUND framework error (404)."""
    order = db.get(ServiceOrder, order_id)
    if order is None:
        raise framework_error(FrameworkError.SERVICE_ORDER_NOT_FOUND, detail=f"unknown orderId {order_id}")
    return order


@app.get("/orders/{order_id}")
def query_order_status(order_id: uuid.UUID, db: Session = Depends(get_session)):
    # Maintainer notes (not published). 404 SERVICE_ORDER_NOT_FOUND for an unknown id. `homingDecision` is returned as stored; no route in this module writes it.
    order = _order_or_404(db, order_id)
    return {"orderId": str(order.order_id), "steps": order.steps, "homingDecision": order.homing_decision}


@app.post("/orders/{order_id}/cancel")
def cancel_order(order_id: uuid.UUID, db: Session = Depends(get_session)):
    """Reassigns order.steps to a NEW list rather than mutating the
    existing one's dicts in place — a plain JSON column's list is never
    tracked by SQLAlchemy's change detection on in-place mutation
    (that needs sqlalchemy.ext.mutable), so the old in-place version of
    this route silently never persisted the CANCELLED status at all:
    commit()'s default expire-on-commit re-fetched the unchanged row
    right back, discarding the edit.
    """
    # Maintainer notes (not published). 404 SERVICE_ORDER_NOT_FOUND for an unknown id. Only PENDING steps become CANCELLED; the order's status is not checked, so cancelling twice or
    # cancelling a fully executed order changes nothing and still answers 200. Because execution is synchronous, an order that is still running cannot be cancelled: the only PENDING
    # steps are those left behind by a failed step.
    order = _order_or_404(db, order_id)
    order.steps = [{**step, "status": "CANCELLED"} if step["status"] == "PENDING" else step for step in order.steps]
    db.commit()
    return {"orderId": str(order.order_id), "steps": order.steps}


@app.get("/orders")
def list_service_orders(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    """List read over ServiceOrder — only GET-by-id existed. Each order's
    own steps come back whole, same as query_order_status."""
    # Maintainer notes (not published). Paginated by smo_shared.pagination (ordered by primary key, order_id). Each item carries the whole `steps` list, so a page of large orders is large.
    page = paginate(db, select(ServiceOrder), limit, offset)
    return {**page, "items": [{"orderId": str(o.order_id), "scope": o.scope, "steps": o.steps, "homingDecision": o.homing_decision,
             "rmihRegistration": o.rmih_registration} for o in page["items"]]}
