"""FOCOM SMOS (O2ims).

SMO Design v1.3 section 3.6, extended by NFO+FOCOM LLD section 1: FOCOM's
own alarm/performance domain (infrastructure — O-Cloud host/node/cluster
health) is distinguished explicitly from RAN NF OAM's RAN-function alarms.
Phase 1: degenerate single-node cluster (D-DEPLOY-FOCOM-1, unchanged).
"""

import uuid

from fastapi import Depends, FastAPI
from sqlalchemy.orm import Session

from smo_shared.db import get_session

from .models import OCloudAlarm, OCloudPerformanceMetric

app = FastAPI(title="FOCOM SMOS (O2ims)")

PHASE1_CLUSTER_ID = "phase1-degenerate-cluster"


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


@app.post("/inventory/subscriptions")
def subscribe_inventory_changes():
    return {"status": "subscribed"}  # Phase 1: no-op, single degenerate cluster never changes


@app.post("/resources/provision")
def provision_resource(spec: dict):
    return {"resourceId": str(uuid.uuid4()), "clusterId": PHASE1_CLUSTER_ID}


@app.delete("/resources/{resource_id}")
def deprovision_resource(resource_id: str):
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
