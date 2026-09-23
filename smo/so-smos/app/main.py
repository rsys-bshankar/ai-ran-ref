"""SO SMOS.

SMO Design v1.3 section 3.13, extended by SO/SA SMOS LLD section 1: the
concrete dispatch table (dispatch.py) and fail-fast execution semantics
v1.3 left as "orchestrates in sequence" with no defined failure behavior.
"""

import uuid

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.r1_client import R1Client

from .dispatch import execute_order
from .models import ServiceOrder

app = FastAPI(title="SO SMOS")


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
    order = db.get(ServiceOrder, order_id)
    for step in order.steps:
        if step["status"] == "PENDING":
            step["status"] = "CANCELLED"
    db.commit()
    return {"orderId": str(order.order_id), "steps": order.steps}
