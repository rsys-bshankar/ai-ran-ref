"""FOCOM SMOS (O2ims).

SMO Design v1.3 section 3.6, extended by NFO+FOCOM LLD section 1: FOCOM's
own alarm/performance domain (infrastructure — O-Cloud host/node/cluster
health) is distinguished explicitly from RAN NF OAM's RAN-function alarms.
Phase 1: degenerate single-node cluster (D-DEPLOY-FOCOM-1, unchanged).
"""

import uuid

import httpx
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.db import get_session

from .models import DeploymentManager, InventorySubscription, OCloudAlarm, OCloudPerformanceMetric, Resource, ResourcePool, ResourceType

app = FastAPI(title="FOCOM SMOS (O2ims)")

PHASE1_CLUSTER_ID = "phase1-degenerate-cluster"
PHASE1_RESOURCE_TYPE_ID = "generic"
PHASE1_POOL_ID = "pool-0"
PHASE1_DEPLOYMENT_MANAGER_ID = "dm-0"


def _ensure_phase1_topology(db: Session) -> None:
    """Lazily seeds the single degenerate ResourceType/ResourcePool/
    DeploymentManager row on first read, rather than at app startup —
    no other module in this build seeds data at startup, and Phase 1's
    topology (D-DEPLOY-FOCOM-1) never changes, so this keeps the same
    pragmatic, no-new-pattern shape as the rest of this codebase.
    """
    if db.get(ResourcePool, PHASE1_POOL_ID) is None:
        db.add(ResourceType(resource_type_id=PHASE1_RESOURCE_TYPE_ID, name="generic",
                             description="Phase 1 degenerate single-node resource type"))
        db.add(ResourcePool(resource_pool_id=PHASE1_POOL_ID, name="pool-0",
                             description="Phase 1 degenerate single-node resource pool", o_cloud_id=PHASE1_CLUSTER_ID))
        db.add(DeploymentManager(deployment_manager_id=PHASE1_DEPLOYMENT_MANAGER_ID, name=PHASE1_CLUSTER_ID,
                                  description="Phase 1 degenerate single-node deployment manager",
                                  o_cloud_id=PHASE1_CLUSTER_ID, service_uri=f"http://{PHASE1_CLUSTER_ID}:6443"))
        db.commit()


class SubscribeInventoryRequest(BaseModel):
    """SPEC_AUDIT.md item 8: ORAN.O2ims.Inventory.yaml's InventorySubscription
    names this field `callback`, not this build's own invented
    `callbackUri` — renamed to match. consumerSubscriptionId (the
    spec's own consumer-provided tracking id) was entirely absent.
    """
    callback: str
    resourceTypeId: str | None = None
    consumerSubscriptionId: str | None = None


@app.get("/inventory")
def query_inventory(resource_type: str = "", db: Session = Depends(get_session)):
    """QueryInventory — Phase 1: a single degenerate cluster, per
    D-DEPLOY-FOCOM-1. NFO's Instantiate calls this before placing a
    workload (NFO+FOCOM LLD section 4).

    OPEN_ITEMS.md section 2's "FOCOM's hardcoded single-cluster stub":
    the §5 pass below gave FOCOM a real ResourceType/ResourcePool/
    DeploymentManager schema and wired every drill-down route
    (`/resource-pools`, `/deployment-managers`, ...) to it, but left this
    route — the one thing NFO's real Instantiate call actually depends
    on — still a hardcoded literal, disconnected from that schema
    entirely. Now sourced from the same seeded row every other route
    reads, so there's one real topology, not a schema plus a stale
    literal that happens to agree with it today. `resource_type` is
    still only echoed back, not validated against a known `ResourceType`
    — Phase 1 has exactly one degenerate cluster regardless of what's
    requested, matching NFO's own graceful fallback on any non-2xx
    response rather than a hard rejection.
    """
    _ensure_phase1_topology(db)
    dm = db.get(DeploymentManager, PHASE1_DEPLOYMENT_MANAGER_ID)
    pool = db.get(ResourcePool, PHASE1_POOL_ID)
    return {
        "clusterId": dm.name,
        "resourcePools": [{"resourcePoolId": pool.resource_pool_id, "resourceTypeId": resource_type or PHASE1_RESOURCE_TYPE_ID}],
    }


@app.get("/resource-types")
def list_resource_types(db: Session = Depends(get_session)):
    """OPEN_ITEMS.md section 5: no per-resource-type/pool/resource
    drill-down endpoints existed at all — the reference exposes
    /resourceTypes, /resourceTypes/{id}, /resourcePools/{id}/resources,
    /deploymentManagers/{id} as distinct operations; FOCOM collapsed
    everything into one /inventory route with nothing behind it.
    """
    _ensure_phase1_topology(db)
    return [_resource_type_view(t) for t in db.scalars(select(ResourceType)).all()]


@app.get("/resource-types/{resource_type_id}")
def get_resource_type(resource_type_id: str, db: Session = Depends(get_session)):
    _ensure_phase1_topology(db)
    t = db.get(ResourceType, resource_type_id)
    if t is None:
        raise HTTPException(status_code=404, detail="no such resource type")
    return _resource_type_view(t)


@app.get("/resource-pools")
def list_resource_pools(db: Session = Depends(get_session)):
    _ensure_phase1_topology(db)
    return [_resource_pool_view(p) for p in db.scalars(select(ResourcePool)).all()]


@app.get("/resource-pools/{resource_pool_id}")
def get_resource_pool(resource_pool_id: str, db: Session = Depends(get_session)):
    _ensure_phase1_topology(db)
    p = db.get(ResourcePool, resource_pool_id)
    if p is None:
        raise HTTPException(status_code=404, detail="no such resource pool")
    return _resource_pool_view(p)


@app.get("/resource-pools/{resource_pool_id}/resources")
def list_pool_resources(resource_pool_id: str, db: Session = Depends(get_session)):
    _ensure_phase1_topology(db)
    if db.get(ResourcePool, resource_pool_id) is None:
        raise HTTPException(status_code=404, detail="no such resource pool")
    rows = db.scalars(select(Resource).where(Resource.resource_pool_id == resource_pool_id)).all()
    return [_resource_view(r) for r in rows]


@app.get("/deployment-managers")
def list_deployment_managers(db: Session = Depends(get_session)):
    _ensure_phase1_topology(db)
    return [_deployment_manager_view(d) for d in db.scalars(select(DeploymentManager)).all()]


@app.get("/deployment-managers/{deployment_manager_id}")
def get_deployment_manager(deployment_manager_id: str, db: Session = Depends(get_session)):
    _ensure_phase1_topology(db)
    d = db.get(DeploymentManager, deployment_manager_id)
    if d is None:
        raise HTTPException(status_code=404, detail="no such deployment manager")
    return _deployment_manager_view(d)


TEIV_CLOUD_PREFIX = "o-ran-smo-teiv-cloud"
TEIV_URN_PREFIX = "urn:oran:smo:teiv"


@app.get("/topology")
def export_topology(db: Session = Depends(get_session)):
    """OPEN_ITEMS.md section 5: the Blueprint names "FOCOM's placement as
    a TEIV data source" as a confirmed integration point, but FOCOM had
    no typed entity/relationship model and no /topology-shaped endpoint
    at all — not even a stub. This exports FOCOM's real ResourceType/
    ResourcePool/DeploymentManager/Resource rows in the wire shape the
    reference's own focom-to-teiv-adapter produces (entities keyed by
    "<prefix>:<EntityType>" with {id, attributes}; relationships keyed by
    "<prefix>:<A>_<REL>_<B>" with {id, aSide, bSide, sourceIds} — see
    EntityAndRelationshipModel.java / TeivIdBuilder.java).

    The real adapter derives OCloudNamespace/NodeCluster entities from
    live FocomProvisioningRequest/O2ims Kubernetes CRDs and pushes them
    over Kafka as CloudEvents — direct kubeconfig access, CRD reads, and
    a message broker are all structurally out of scope for this
    docker-run-based Phase 1 (same elision as the rest of this module).
    So this exports FOCOM's own actual inventory instead, as a pull-based
    GET, using only the module's real foreign keys (resource -> type,
    resource -> pool, resource -> parent) rather than inventing
    relationships this schema doesn't actually track.
    """
    _ensure_phase1_topology(db)

    resource_types = db.scalars(select(ResourceType)).all()
    resource_pools = db.scalars(select(ResourcePool)).all()
    deployment_managers = db.scalars(select(DeploymentManager)).all()
    resources = db.scalars(select(Resource)).all()

    entities: list[dict] = []
    if resource_types:
        entities.append({f"{TEIV_CLOUD_PREFIX}:ResourceType": [
            {"id": f"{TEIV_URN_PREFIX}:ResourceType:{t.resource_type_id}",
             "attributes": {"name": t.name, "description": t.description, "vendor": t.vendor,
                             "model": t.model, "version": t.version}}
            for t in resource_types
        ]})
    if resource_pools:
        entities.append({f"{TEIV_CLOUD_PREFIX}:ResourcePool": [
            {"id": f"{TEIV_URN_PREFIX}:ResourcePool:{p.resource_pool_id}",
             "attributes": {"name": p.name, "description": p.description, "oCloudId": p.o_cloud_id}}
            for p in resource_pools
        ]})
    if deployment_managers:
        entities.append({f"{TEIV_CLOUD_PREFIX}:DeploymentManager": [
            {"id": f"{TEIV_URN_PREFIX}:DeploymentManager:{d.deployment_manager_id}",
             "attributes": {"name": d.name, "description": d.description, "oCloudId": d.o_cloud_id,
                             "serviceUri": d.service_uri}}
            for d in deployment_managers
        ]})
    if resources:
        entities.append({f"{TEIV_CLOUD_PREFIX}:Resource": [
            {"id": f"{TEIV_URN_PREFIX}:Resource:{r.resource_id}",
             "attributes": {"resourceTypeId": r.resource_type_id, "resourcePoolId": r.resource_pool_id,
                             "description": r.description}}
            for r in resources
        ]})

    is_of_type: list[dict] = []
    contained_in: list[dict] = []
    child_of: list[dict] = []
    for r in resources:
        resource_urn = f"{TEIV_URN_PREFIX}:Resource:{r.resource_id}"
        is_of_type.append({
            "id": f"{TEIV_URN_PREFIX}:RESOURCE_IS_OF_TYPE_RESOURCETYPE:{r.resource_id}",
            "aSide": resource_urn, "bSide": f"{TEIV_URN_PREFIX}:ResourceType:{r.resource_type_id}",
            "sourceIds": [str(r.resource_id), r.resource_type_id],
        })
        contained_in.append({
            "id": f"{TEIV_URN_PREFIX}:RESOURCE_CONTAINED_IN_RESOURCEPOOL:{r.resource_id}",
            "aSide": resource_urn, "bSide": f"{TEIV_URN_PREFIX}:ResourcePool:{r.resource_pool_id}",
            "sourceIds": [str(r.resource_id), r.resource_pool_id],
        })
        if r.parent_id is not None:
            child_of.append({
                "id": f"{TEIV_URN_PREFIX}:RESOURCE_CHILD_OF_RESOURCE:{r.resource_id}",
                "aSide": resource_urn, "bSide": f"{TEIV_URN_PREFIX}:Resource:{r.parent_id}",
                "sourceIds": [str(r.resource_id), str(r.parent_id)],
            })

    relationships: list[dict] = []
    if is_of_type:
        relationships.append({f"{TEIV_CLOUD_PREFIX}:RESOURCE_IS_OF_TYPE_RESOURCETYPE": is_of_type})
    if contained_in:
        relationships.append({f"{TEIV_CLOUD_PREFIX}:RESOURCE_CONTAINED_IN_RESOURCEPOOL": contained_in})
    if child_of:
        relationships.append({f"{TEIV_CLOUD_PREFIX}:RESOURCE_CHILD_OF_RESOURCE": child_of})

    return {"entities": entities, "relationships": relationships}


def _notify_inventory_subscribers(db: Session, event_type: str, resource_id: str, resource_type_id: str | None) -> None:
    """OPEN_ITEMS.md section 5: subscribe_inventory_changes took no
    callback parameter, stored nothing, and delivered nothing — the
    reference's real Subscription model stores a callback + filter and
    pushes typed create/modify/delete notifications on inventory
    change. Wired into the only two mutating endpoints this module has
    (provision/deprovision). A caller can still pass resource_type_id
    as None (e.g. deprovisioning an id that was never provisioned) — an
    unset filter always matches rather than being silently dropped.
    Best-effort delivery, same pattern as Policy Mgmt's CreateIntent
    notification.

    consumerSubscriptionId (SPEC_AUDIT.md item 8): the spec's own
    description is explicit that it exists "for tracking, routing, or
    identifying the subscription used to report the event" — i.e. it's
    meant to come back on the notification itself, not just be stored.
    """
    for sub in db.scalars(select(InventorySubscription)).all():
        if sub.resource_type_id is not None and resource_type_id is not None and sub.resource_type_id != resource_type_id:
            continue
        try:
            httpx.post(sub.callback, json={
                "objectType": "resource", "notificationEventType": event_type,
                "resourceId": resource_id, "resourceTypeId": resource_type_id,
                "consumerSubscriptionId": sub.consumer_subscription_id,
            }, timeout=2.0)
        except httpx.HTTPError:
            pass


@app.post("/inventory/subscriptions", status_code=201)
def subscribe_inventory_changes(body: SubscribeInventoryRequest, db: Session = Depends(get_session)):
    sub = InventorySubscription(callback=body.callback, resource_type_id=body.resourceTypeId,
                                 consumer_subscription_id=body.consumerSubscriptionId)
    db.add(sub)
    db.commit()
    return {"subscriptionId": str(sub.subscription_id), "consumerSubscriptionId": sub.consumer_subscription_id}


@app.delete("/inventory/subscriptions/{subscription_id}", status_code=204)
def unsubscribe_inventory_changes(subscription_id: uuid.UUID, db: Session = Depends(get_session)):
    sub = db.get(InventorySubscription, subscription_id)
    if sub is not None:
        db.delete(sub)
        db.commit()


@app.post("/resources/provision")
def provision_resource(spec: dict, db: Session = Depends(get_session)):
    """Previously returned a random UUID and persisted nothing at all —
    OPEN_ITEMS.md section 5's "no model shape to extend later" gap. Now
    creates a real Resource row in the Phase 1 pool, so the new
    GET /resource-pools/{id}/resources drill-down actually has
    something behind it. An unrecognized resourceTypeId is
    auto-registered as a new ResourceType rather than rejected — Phase
    1 never validated this field, and rejecting it now would be a
    scope-creeping behavior change, not just a schema addition.
    """
    _ensure_phase1_topology(db)
    resource_type_id = spec.get("resourceTypeId") or PHASE1_RESOURCE_TYPE_ID
    if db.get(ResourceType, resource_type_id) is None:
        db.add(ResourceType(resource_type_id=resource_type_id, name=resource_type_id))
    # SPEC_AUDIT.md item 7: globalAssetId/tags/groups — real
    # ORAN.O2ims.Inventory.yaml Resource fields, previously not even
    # readable from this already-untyped spec dict, let alone persisted.
    resource = Resource(resource_type_id=resource_type_id, resource_pool_id=PHASE1_POOL_ID, description=spec.get("description"),
                         global_asset_id=spec.get("globalAssetId"), tags=spec.get("tags"), groups=spec.get("groups"))
    db.add(resource)
    db.commit()
    _notify_inventory_subscribers(db, "CREATE", str(resource.resource_id), resource_type_id)
    return {"resourceId": str(resource.resource_id), "clusterId": PHASE1_CLUSTER_ID}


@app.delete("/resources/{resource_id}")
def deprovision_resource(resource_id: str, db: Session = Depends(get_session)):
    try:
        resource = db.get(Resource, uuid.UUID(resource_id))
    except ValueError:
        resource = None  # Phase 1: an arbitrary, never-provisioned (or non-UUID) resource_id is a no-op, not an error
    resource_type_id = resource.resource_type_id if resource is not None else None
    if resource is not None:
        db.delete(resource)
        db.commit()
    _notify_inventory_subscribers(db, "DELETE", resource_id, resource_type_id)
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


def _resource_type_view(t: ResourceType) -> dict:
    return {"resourceTypeId": t.resource_type_id, "name": t.name, "description": t.description,
            "vendor": t.vendor, "model": t.model, "version": t.version,
            "alarmDictionaryId": t.alarm_dictionary_id, "performanceDictionaryId": t.performance_dictionary_id,
            "resourceKind": t.resource_kind, "resourceClass": t.resource_class, "extensions": t.extensions}


def _resource_pool_view(p: ResourcePool) -> dict:
    return {"resourcePoolId": p.resource_pool_id, "name": p.name, "description": p.description, "oCloudId": p.o_cloud_id}


def _resource_view(r: Resource) -> dict:
    return {"resourceId": str(r.resource_id), "resourceTypeId": r.resource_type_id, "resourcePoolId": r.resource_pool_id,
            "parentId": str(r.parent_id) if r.parent_id else None, "description": r.description,
            "globalAssetId": r.global_asset_id, "tags": r.tags, "groups": r.groups}


def _deployment_manager_view(d: DeploymentManager) -> dict:
    return {"deploymentManagerId": d.deployment_manager_id, "name": d.name, "description": d.description,
            "oCloudId": d.o_cloud_id, "serviceUri": d.service_uri,
            "supportedLocations": d.supported_locations, "capabilities": d.capabilities, "capacity": d.capacity}
