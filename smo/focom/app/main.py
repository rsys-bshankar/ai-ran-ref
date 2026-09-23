"""FOCOM SMOS (O2ims).

SMO Design v1.3 section 3.6, extended by NFO+FOCOM LLD section 1: FOCOM's
own alarm/performance domain (infrastructure — O-Cloud host/node/cluster
health) is distinguished explicitly from RAN NF OAM's RAN-function alarms.
Phase 1: degenerate single-node cluster (D-DEPLOY-FOCOM-1, unchanged).
"""

import uuid

import httpx
from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.db import get_session

from .models import InventorySubscription, OCloudAlarm, OCloudPerformanceMetric

app = FastAPI(title="FOCOM SMOS (O2ims)")

PHASE1_CLUSTER_ID = "phase1-degenerate-cluster"


class SubscribeInventoryRequest(BaseModel):
    callbackUri: str
    resourceTypeId: str | None = None


@app.get("/inventory")
def query_inventory(resource_type: str = ""):
    """QueryInventory — Phase 1: a single degenerate cluster, per
    D-DEPLOY-FOCOM-1. NFO's Instantiate calls this before placing a
    workload (NFO+FOCOM LLD section 4).
    """
    return {
        "clusterId": PHASE1_CLUSTER_ID,
        "resourcePools": [{"resourcePoolId": "pool-0", "resourceTypeId": resource_type or "generic"}],
    }


def _notify_inventory_subscribers(db: Session, event_type: str, resource_id: str, resource_type_id: str | None) -> None:
    """OPEN_ITEMS.md section 5: subscribe_inventory_changes took no
    callback parameter, stored nothing, and delivered nothing — the
    reference's real Subscription model stores a callback + filter and
    pushes typed create/modify/delete notifications on inventory
    change. Wired into the only two mutating endpoints this module has
    (provision/deprovision); resource_type_id is unknown at deprovision
    time (no ResourceType/ResourcePool schema exists yet — a separate,
    larger gap tracked in the same section), so a None event type
    always matches rather than being silently filtered out. Best-effort
    delivery, same pattern as Policy Mgmt's CreateIntent notification.
    """
    for sub in db.scalars(select(InventorySubscription)).all():
        if sub.resource_type_id is not None and resource_type_id is not None and sub.resource_type_id != resource_type_id:
            continue
        try:
            httpx.post(sub.callback_uri, json={
                "objectType": "resource", "notificationEventType": event_type,
                "resourceId": resource_id, "resourceTypeId": resource_type_id,
            }, timeout=2.0)
        except httpx.HTTPError:
            pass


@app.post("/inventory/subscriptions", status_code=201)
def subscribe_inventory_changes(body: SubscribeInventoryRequest, db: Session = Depends(get_session)):
    sub = InventorySubscription(callback_uri=body.callbackUri, resource_type_id=body.resourceTypeId)
    db.add(sub)
    db.commit()
    return {"subscriptionId": str(sub.subscription_id)}


@app.delete("/inventory/subscriptions/{subscription_id}", status_code=204)
def unsubscribe_inventory_changes(subscription_id: uuid.UUID, db: Session = Depends(get_session)):
    sub = db.get(InventorySubscription, subscription_id)
    if sub is not None:
        db.delete(sub)
        db.commit()


@app.post("/resources/provision")
def provision_resource(spec: dict, db: Session = Depends(get_session)):
    resource_id = str(uuid.uuid4())
    resource_type_id = spec.get("resourceTypeId")
    _notify_inventory_subscribers(db, "CREATE", resource_id, resource_type_id)
    return {"resourceId": resource_id, "clusterId": PHASE1_CLUSTER_ID}


@app.delete("/resources/{resource_id}")
def deprovision_resource(resource_id: str, db: Session = Depends(get_session)):
    _notify_inventory_subscribers(db, "DELETE", resource_id, None)
    return {"status": "deprovisioned"}


@app.get("/resources/{resource_id}/status")
def monitor_resource(resource_id: str):
    return {"resourceId": resource_id, "status": "healthy"}


@app.get("/alarms")
def query_ocloud_alarms(db: Session = Depends(get_session)):
    return [{"alarmId": str(a.alarm_id), "resourceRef": a.resource_ref, "severity": a.severity} for a in db.query(OCloudAlarm).all()]


@app.post("/alarms/ingest")
def ingest_ocloud_alarm(resource_ref: str, severity: str, db: Session = Depends(get_session)):
    alarm = OCloudAlarm(resource_ref=resource_ref, severity=severity)
    db.add(alarm)
    db.commit()
    return {"alarmId": str(alarm.alarm_id)}


@app.get("/performance")
def query_ocloud_performance(resource_ref: str | None = None, db: Session = Depends(get_session)):
    q = db.query(OCloudPerformanceMetric)
    if resource_ref:
        q = q.filter(OCloudPerformanceMetric.resource_ref == resource_ref)
    return [{"resourceRef": m.resource_ref, "metricName": m.metric_name, "value": m.value} for m in q.all()]
