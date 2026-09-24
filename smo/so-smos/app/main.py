"""SO SMOS.

SMO Design v1.3 section 3.13, extended by SO/SA SMOS LLD section 1: the
concrete dispatch table (dispatch.py) and fail-fast execution semantics
v1.3 left as "orchestrates in sequence" with no defined failure behavior.
"""

import uuid

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.r1_client import R1Client

from .dispatch import execute_order
from .models import ServiceOrder

app = FastAPI(title="SO SMOS")


@app.get("/health")
def health_check():
    """Liveness probe. The GUI BFF's GET /modules/status fans out to
    /<module>/health through R1 Termination for every module in parallel,
    so every module answers one — previously only ran-nf-oam/a1-related
    did (as their own DME producer-health callback URL).
    """
    return {"status": "healthy"}


class SubmitOrderRequest(BaseModel):
    scope: str
    steps: list[dict]
    rmihRegistration: str = "so-smos"


@app.post("/orders", status_code=202)
def submit_service_order(body: SubmitOrderRequest, db: Session = Depends(get_session)):
    order = ServiceOrder(scope=body.scope, steps=body.steps, rmih_registration=body.rmihRegistration)
    db.add(order)
    db.flush()

    r1 = R1Client()
    results = execute_order(r1, body.steps)
    order.steps = results
    db.commit()
    return {"orderId": str(order.order_id), "steps": results}


@app.get("/orders/{order_id}")
def query_order_status(order_id: uuid.UUID, db: Session = Depends(get_session)):
    order = db.get(ServiceOrder, order_id)
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
    order = db.get(ServiceOrder, order_id)
    order.steps = [{**step, "status": "CANCELLED"} if step["status"] == "PENDING" else step for step in order.steps]
    db.commit()
    return {"orderId": str(order.order_id), "steps": order.steps}


@app.get("/orders")
def list_service_orders(db: Session = Depends(get_session)):
    """List read over ServiceOrder — only GET-by-id existed. Each order's
    own steps come back whole, same as query_order_status."""
    return [{"orderId": str(o.order_id), "scope": o.scope, "steps": o.steps, "homingDecision": o.homing_decision,
             "rmihRegistration": o.rmih_registration} for o in db.scalars(select(ServiceOrder)).all()]
