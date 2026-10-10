"""FOCOM SMOS (O2-IMS): the FastAPI module for the O-Cloud inventory, inventory-change subscriptions and the TEIV-shaped topology export, served at
`/focom` behind R1 Termination.

What it is: the inventory routes (`/inventory`, `/resource-types`, `/resource-pools`, `/deployment-managers`), resource provision / deprovision / status,
inventory subscriptions and `/topology`, plus the app wiring. The other route groups are separate files mounted here: `sites.py` (locations, sites,
pool create / delete), `fcaps.py` (alarms and performance) and `provisioning.py` (artifact, cluster, infrastructure and provisioning-request
resources). Design record: `focom/README.md`; SMO Design v1.3 section 3.6 and NFO+FOCOM LLD section 1. FOCOM's alarm and performance domain is
infrastructure (O-Cloud host, node, cluster health), distinct from RAN NF OAM's RAN-function alarms. Phase 1 is a degenerate single-node cluster
(D-DEPLOY-FOCOM-1).

Where it sits: NFO calls `GET /inventory` (it reads `oCloudId`) and SO SMOS calls `POST /resources/provision`, both through R1 Termination. FOCOM calls
no other module; inventory notifications to subscribers are outbox rows (`smo_shared.outbox`), never a direct HTTP call from a route.

Owns: the inventory tables and the rules between them in this file. Does not own: authorization (R1 Termination introspects the token; the GUI BFF
decides which role may call provision, deprovision and alarm ingest) or any placement decision (NFO).

Before editing: (1) the docstring of a route function or a request model is published as OpenAPI text, so changing one makes
`tests_integration/test_openapi_specs.py` fail until `scripts/generate_openapi_specs.py` is rerun; maintainer notes for routes are `#` comments under
the docstring. (2) Routes that notify call `_notify_inventory_subscribers` (or `fcaps._notify`) before `db.commit()`: the outbox row is part of the same
transaction and is sent after it commits. (3) `import httpx` is kept although nothing here calls it: the tests patch `app.main.httpx.post` (see `ruff.toml`).
"""

import uuid
from typing import Literal

# Unused here on purpose: the unit tests patch `app.main.httpx.post` and the integration tests patch `loaded_apps[<module>].httpx` (see `ruff.toml`, F401).
import httpx
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.logconfig import install_logging
from smo_shared.metrics import install_metrics
from smo_shared.health import database_check, install_health
from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.openapi_security import apply_r1_gateway_security
from smo_shared.correlation import apply_correlation_id
from smo_shared.outbox import enqueue
from smo_shared.pagination import PageLimit, PageOffset, paginate

from . import fcaps, provisioning, sites
from .common import (IMS_ENDPOINT, PHASE1_CLUSTER_ID, PHASE1_DEPLOYMENT_MANAGER_ID, PHASE1_POOL_ID, PHASE1_RESOURCE_TYPE_ID,
                     SMO_REGISTRATION_SERVICE, auto_register_resource_types, ensure_phase1_topology, global_cloud_id, ioc,
                     location_view, pool_view, site_view)
from .models import DeploymentManager, InventorySubscription, Location, OCloudSite, Resource, ResourcePool, ResourceType

app = FastAPI(title="FOCOM SMOS (O2ims)")
install_logging(app)  # structured JSON logs and one access-log line per request (PR-OBS-1)
install_metrics(app)  # /metrics and request count/latency series (PR-OBS-2)
apply_r1_gateway_security(app)
apply_correlation_id(app)


install_health(app, checks=[database_check])  # /live, /ready and the /health alias (PR-ST-7)

# The routes in this file call the seeding function under this name; the implementation is in `common.py`.
_ensure_phase1_topology = ensure_phase1_topology  # seeded lazily on first read (common.py)

app.include_router(sites.router)
app.include_router(fcaps.router)
app.include_router(provisioning.router)


class SubscribeInventoryRequest(BaseModel):
    """HISTORY.md §7 item 8: ORAN.O2ims.Inventory.yaml's InventorySubscription
    names this field `callback`, not this build's own invented
    `callbackUri` — renamed to match. consumerSubscriptionId (the
    spec's own consumer-provided tracking id) was entirely absent.
    """
    callback: str
    resourceTypeId: str | None = None
    consumerSubscriptionId: str | None = None


@app.get("/inventory")
def query_inventory(resource_type: str = "", db: Session = Depends(get_session)):
    """QueryInventory — HISTORY.md §7 item 8 (formerly moderate item 2 of
    the "Moderate/breaking-shape items" list): reshaped toward the real
    O2IMS `OCloud` schema (`ORAN.O2ims.Inventory.yaml`), the spec's own
    aggregate root — this route previously returned an ad hoc
    `{clusterId, resourcePools:[...]}` shape matching neither `OCloud`
    nor any wrapped list.

    `oCloudId`/`name`/`description`/`resourceTypes`/`deploymentManagers`
    are populated from this build's own real, already-seeded topology
    (the same rows every drill-down route already reads — no new
    fabricated data). `locations`/`oCloudSites` are required
    (`minItems: 1`) and are populated from FOCOM's own Location and
    OCloudSite rows (a seeded default pair, more via `POST /locations`
    and `POST /o-cloud-sites`; SA-FOCOM-2). `globalCloudId` is a stable
    SMO-side id derived from `oCloudId`; the two endpoint fields come
    from `FOCOM_IMS_ENDPOINT` / `FOCOM_SMO_REGISTRATION_SERVICE`.

    `resource_type`, if given, now genuinely filters `resourceTypes` to
    the matching entry (or an empty list if none is registered) — real
    validation against a known `ResourceType`, closing this route's own
    previously-documented gap ("still only echoed back, not validated").
    NFO's real Instantiate caller (NFO+FOCOM LLD section 4) reads
    `oCloudId`, not this filtered list, so this has no effect on it
    either way.
    """
    # Route notes (the docstring above is published): seeds the topology, then reads `oCloudId`, `name` and `description` from the `dm-0` deployment manager
    # row. `resource_type` filters `resourceTypes` only (an unregistered type gives `[]`, not an error); `deploymentManagers`, `locations` and `oCloudSites`
    # are always complete and unpaginated, with the pools inline in each site. `globalCloudId` is derived from `oCloudId`, the two endpoint fields come
    # from `common.py`, and `extensions` is always `[]`.
    _ensure_phase1_topology(db)
    dm = db.get_one(DeploymentManager, PHASE1_DEPLOYMENT_MANAGER_ID)
    resource_types = db.scalars(select(ResourceType)).all()
    if resource_type:
        resource_types = [t for t in resource_types if t.resource_type_id == resource_type]
    return {
        "oCloudId": dm.o_cloud_id,
        "name": dm.name,
        "description": dm.description,
        "resourceTypes": [_resource_type_view(t) for t in resource_types],
        "deploymentManagers": [_deployment_manager_view(dm)],
        "locations": [location_view(db, loc) for loc in db.scalars(select(Location)).all()],
        "oCloudSites": [site_view(db, site) for site in db.scalars(select(OCloudSite)).all()],
        "globalCloudId": global_cloud_id(dm.o_cloud_id),
        "infrastructureManagementServicesEndPoint": IMS_ENDPOINT,
        "smoRegistrationService": SMO_REGISTRATION_SERVICE,
        "extensions": [],
    }


class RegisterResourceTypeRequest(BaseModel):
    """Registers a ResourceType (SA-FOCOM-9): the O2 interface treats the type
    list as read-only, so this is the SMO-side seeding path."""
    model_config = ConfigDict(extra="forbid")
    resourceTypeId: str
    name: str
    description: str | None = None
    vendor: str | None = None
    model: str | None = None
    version: str | None = None
    alarmDictionaryId: str | None = None
    performanceDictionaryId: str | None = None
    resourceKind: Literal["UNDEFINED", "LOGICAL", "PHYSICAL"] | None = None
    resourceClass: Literal["UNDEFINED", "COMPUTE", "NETWORKING", "STORAGE"] | None = None


@app.post("/resource-types", status_code=201)
def register_resource_type(body: RegisterResourceTypeRequest, db: Session = Depends(get_session)):
    # Route notes: 201 with the stored type. 422 `SCHEMA_VALIDATION_FAILED` (not 409) when the id already exists. The body forbids unknown fields. The only
    # route that sets the dictionary ids, kind and class of a type; a type registered here can then be provisioned.
    _ensure_phase1_topology(db)
    if db.get(ResourceType, body.resourceTypeId) is not None:
        raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED, detail=f"resource type {body.resourceTypeId!r} already exists")
    row = ResourceType(resource_type_id=body.resourceTypeId, name=body.name, description=body.description, vendor=body.vendor,
                       model=body.model, version=body.version, alarm_dictionary_id=body.alarmDictionaryId,
                       performance_dictionary_id=body.performanceDictionaryId, resource_kind=body.resourceKind,
                       resource_class=body.resourceClass)
    db.add(row)
    db.commit()
    return _resource_type_view(row)


@app.get("/resource-types")
def list_resource_types(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    """HISTORY.md §5: no per-resource-type/pool/resource
    drill-down endpoints existed at all — the reference exposes
    /resourceTypes, /resourceTypes/{id}, /resourcePools/{id}/resources,
    /deploymentManagers/{id} as distinct operations; FOCOM collapsed
    everything into one /inventory route with nothing behind it.
    """
    _ensure_phase1_topology(db)
    page = paginate(db, select(ResourceType), limit, offset)
    return {**page, "items": [_resource_type_view(t) for t in page["items"]]}


@app.get("/resource-types/{resource_type_id}")
def get_resource_type(resource_type_id: str, db: Session = Depends(get_session)):
    # Route notes: 404 `RESOURCE_TYPE_NOT_FOUND` for an unknown id.
    _ensure_phase1_topology(db)
    t = db.get(ResourceType, resource_type_id)
    if t is None:
        raise framework_error(FrameworkError.RESOURCE_TYPE_NOT_FOUND, detail="no such resource type")
    return _resource_type_view(t)


@app.get("/resource-pools")
def list_resource_pools(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    # Route notes: seeds the topology; paginated; each pool lists all its resource ids (`pool_view`).
    _ensure_phase1_topology(db)
    page = paginate(db, select(ResourcePool), limit, offset)
    return {**page, "items": [pool_view(db, p) for p in page["items"]]}


@app.get("/resource-pools/{resource_pool_id}")
def get_resource_pool(resource_pool_id: str, db: Session = Depends(get_session)):
    # Route notes: 404 `RESOURCE_POOL_NOT_FOUND` for an unknown id.
    _ensure_phase1_topology(db)
    p = db.get(ResourcePool, resource_pool_id)
    if p is None:
        raise framework_error(FrameworkError.RESOURCE_POOL_NOT_FOUND, detail="no such resource pool")
    return pool_view(db, p)


@app.get("/resource-pools/{resource_pool_id}/resources")
def list_pool_resources(resource_pool_id: str, limit: int = PageLimit, offset: int = PageOffset,
                         db: Session = Depends(get_session)):
    # Route notes: 404 `RESOURCE_POOL_NOT_FOUND` for an unknown pool; otherwise a page of the resources in it (an empty pool gives an empty page).
    _ensure_phase1_topology(db)
    if db.get(ResourcePool, resource_pool_id) is None:
        raise framework_error(FrameworkError.RESOURCE_POOL_NOT_FOUND, detail="no such resource pool")
    stmt = select(Resource).where(Resource.resource_pool_id == resource_pool_id)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [_resource_view(r) for r in page["items"]]}


@app.get("/deployment-managers")
def list_deployment_managers(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    # Route notes: seeds the topology; paginated. Phase 1 has the one `dm-0` row.
    _ensure_phase1_topology(db)
    page = paginate(db, select(DeploymentManager), limit, offset)
    return {**page, "items": [_deployment_manager_view(d) for d in page["items"]]}


@app.get("/deployment-managers/{deployment_manager_id}")
def get_deployment_manager(deployment_manager_id: str, db: Session = Depends(get_session)):
    # Route notes: 404 `DEPLOYMENT_MANAGER_NOT_FOUND` for an unknown id.
    _ensure_phase1_topology(db)
    d = db.get(DeploymentManager, deployment_manager_id)
    if d is None:
        raise framework_error(FrameworkError.DEPLOYMENT_MANAGER_NOT_FOUND, detail="no such deployment manager")
    return _deployment_manager_view(d)


# Name prefixes of the TEIV-shaped export: entity and relationship groups are keyed `o-ran-smo-teiv-cloud:<Name>`, ids are `urn:oran:smo:teiv:<Type>:<id>`.
TEIV_CLOUD_PREFIX = "o-ran-smo-teiv-cloud"
TEIV_URN_PREFIX = "urn:oran:smo:teiv"


@app.get("/topology")
def export_topology(db: Session = Depends(get_session)):
    """HISTORY.md §5: the Blueprint names "FOCOM's placement as
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
    # Route notes (the docstring above is published): seeds the topology, then reads every type, pool, deployment manager and resource (unpaginated). An
    # entity or relationship group appears only when it has at least one member, so an empty database exports empty lists. The relationships come from
    # the resource's own columns only (type, pool, `parent_id`); a relationship id is `<RELATIONSHIP>:<resourceId>`, so each resource has at most one of
    # each kind. Locations, sites, alarms and the O2-IMS objects of `provisioning.py` are not exported.
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
    """Enqueues a notification for each inventory subscription that wants this event; the caller commits.

    `event_type` is `CREATE` or `DELETE`. A subscription with a `resource_type_id` filter is skipped when it differs from `resource_type_id`; when
    `resource_type_id` is None (deprovisioning an id that is not a stored resource) every subscription matches, so a type-filtered subscriber still hears
    about the delete. The payload is `{objectType: "resource", notificationEventType, resourceId, resourceTypeId, consumerSubscriptionId}`; the
    subscriber's own `consumerSubscriptionId` comes back on the notification so it can route it.

    One outbox row per subscription, added to the caller's transaction and sent after its commit (PR-MSG-1.8); nothing is sent when the caller rolls back.
    A callback the SSRF guard refuses is dropped by `enqueue` with a log warning.
    """
    for sub in db.scalars(select(InventorySubscription)).all():
        if sub.resource_type_id is not None and resource_type_id is not None and sub.resource_type_id != resource_type_id:
            continue
        enqueue(db, sub.callback, {
            "objectType": "resource", "notificationEventType": event_type,
            "resourceId": resource_id, "resourceTypeId": resource_type_id,
            "consumerSubscriptionId": sub.consumer_subscription_id,
        })


@app.post("/inventory/subscriptions", status_code=201)
def subscribe_inventory_changes(body: SubscribeInventoryRequest, db: Session = Depends(get_session)):
    # Route notes: 201 with `{subscriptionId, consumerSubscriptionId}`. Neither `callback` nor `resourceTypeId` is checked here: the SSRF guard runs when a
    # notification is enqueued (a refused destination is dropped there), and a filter naming no registered type simply never matches.
    sub = InventorySubscription(callback=body.callback, resource_type_id=body.resourceTypeId,
                                 consumer_subscription_id=body.consumerSubscriptionId)
    db.add(sub)
    db.commit()
    return {"subscriptionId": str(sub.subscription_id), "consumerSubscriptionId": sub.consumer_subscription_id}


@app.delete("/inventory/subscriptions/{subscription_id}", status_code=204)
def unsubscribe_inventory_changes(subscription_id: uuid.UUID, db: Session = Depends(get_session)):
    # Route notes: idempotent, 204 for an unknown id. A `subscription_id` that is not a UUID is rejected by FastAPI (422) before the route runs.
    sub = db.get(InventorySubscription, subscription_id)
    if sub is not None:
        db.delete(sub)
        db.commit()


@app.post("/resources/provision")
def provision_resource(spec: dict, db: Session = Depends(get_session)):
    """Previously returned a random UUID and persisted nothing at all —
    HISTORY.md §5's "no model shape to extend later" gap. Now
    creates a real Resource row in the Phase 1 pool, so the new
    GET /resource-pools/{id}/resources drill-down actually has
    something behind it. An unrecognized resourceTypeId is
    auto-registered as a new ResourceType rather than rejected — Phase
    1 never validated this field, and rejecting it now would be a
    scope-creeping behavior change, not just a schema addition.
    """
    # Route notes (the docstring above is published): `spec` is an untyped dict. `resourceTypeId` defaults to `generic` when absent or empty; an unregistered
    # type is 404 `RESOURCE_TYPE_NOT_FOUND` unless `FOCOM_AUTO_REGISTER_RESOURCE_TYPES` is on. `description`, `globalAssetId`, `tags` and `groups` are
    # copied as given without checking their types. The resource always goes into `pool-0` whatever pools exist (`sites.py`). Order: seed, resolve the type,
    # insert the resource, flush for its id, enqueue the CREATE notifications, one commit. Answers 200 (not 201) with `{resourceId, clusterId}`.
    _ensure_phase1_topology(db)
    resource_type_id = spec.get("resourceTypeId") or PHASE1_RESOURCE_TYPE_ID
    if db.get(ResourceType, resource_type_id) is None:
        if not auto_register_resource_types():
            raise framework_error(FrameworkError.RESOURCE_TYPE_NOT_FOUND,
                                  detail=f"resource type {resource_type_id!r} is not registered (POST /resource-types, or set FOCOM_AUTO_REGISTER_RESOURCE_TYPES=true)")
        db.add(ResourceType(resource_type_id=resource_type_id, name=resource_type_id))
        # A real bug against real Postgres, never caught by SQLite: flushing
        # this new ResourceType row together with the new Resource row below
        # in one commit — instead of flushing it first — hits a genuine
        # ForeignKeyViolation on resource.resource_type_id. Confirmed with a
        # minimal repro against a real local Postgres 16 instance; an
        # explicit flush here (matching the pattern NFO's own Instantiate
        # already uses between its own dependent inserts) makes the new
        # ResourceType row visible before the Resource row that references
        # it is ever inserted.
        db.flush()
    # HISTORY.md §7 item 7: globalAssetId/tags/groups — real
    # ORAN.O2ims.Inventory.yaml Resource fields, previously not even
    # readable from this already-untyped spec dict, let alone persisted.
    resource = Resource(resource_type_id=resource_type_id, resource_pool_id=PHASE1_POOL_ID, description=spec.get("description"),
                         global_asset_id=spec.get("globalAssetId"), tags=spec.get("tags"), groups=spec.get("groups"))
    db.add(resource)
    db.flush()  # the resource's id, for the notification
    _notify_inventory_subscribers(db, "CREATE", str(resource.resource_id), resource_type_id)  # enqueued in this transaction (PR-MSG-1.8)
    db.commit()
    return {"resourceId": str(resource.resource_id), "clusterId": PHASE1_CLUSTER_ID}


@app.delete("/resources/{resource_id}")
def deprovision_resource(resource_id: str, db: Session = Depends(get_session)):
    # Route notes: always 200 `{status: "deprovisioned"}`. A `resource_id` that is not a UUID or names no stored resource is a no-op, but the DELETE
    # notification is still enqueued, with the id exactly as given and no type (so every subscriber matches). The delete does not look at objects that
    # name the resource (cluster resources, infrastructure resources, children through `parent_id`).
    try:
        resource = db.get(Resource, uuid.UUID(resource_id))
    except ValueError:
        resource = None  # Phase 1: an arbitrary, never-provisioned (or non-UUID) resource_id is a no-op, not an error
    resource_type_id = resource.resource_type_id if resource is not None else None
    if resource is not None:
        db.delete(resource)
    _notify_inventory_subscribers(db, "DELETE", resource_id, resource_type_id)
    db.commit()
    return {"status": "deprovisioned"}


@app.get("/resources/{resource_id}/status")
def monitor_resource(resource_id: str):
    # Route notes: a constant. Answers `healthy` for any id, existing or not, and reads no database or telemetry.
    return {"resourceId": resource_id, "status": "healthy"}


def _resource_type_view(t: ResourceType) -> dict:
    return {"resourceTypeId": t.resource_type_id, "name": t.name, "description": t.description,
            "vendor": t.vendor, "model": t.model, "version": t.version,
            "alarmDictionaryId": t.alarm_dictionary_id, "performanceDictionaryId": t.performance_dictionary_id,
            "resourceKind": t.resource_kind, "resourceClass": t.resource_class, "extensions": t.extensions}


def _resource_view(r: Resource) -> dict:
    return {"resourceId": str(r.resource_id), "resourceTypeId": r.resource_type_id, "resourcePoolId": r.resource_pool_id,
            "parentId": str(r.parent_id) if r.parent_id else None, "description": r.description,
            "globalAssetId": r.global_asset_id, "tags": r.tags, "groups": r.groups}


def _deployment_manager_view(d: DeploymentManager) -> dict:
    return {"deploymentManagerId": d.deployment_manager_id, "name": d.name, "description": d.description,
            "oCloudId": d.o_cloud_id, "serviceUri": d.service_uri,
            "supportedLocations": d.supported_locations, "capabilities": d.capabilities, "capacity": d.capacity}


@app.get("/inventory/subscriptions")
def list_inventory_subscriptions(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    """(GUI pass 2) Inventory-change subscriptions were write-only."""
    # Route notes: paginated; lists `subscriptionId`, `callback`, `consumerSubscriptionId` and `resourceTypeId`.
    page = paginate(db, select(InventorySubscription), limit, offset)
    return {**page, "items": [{"subscriptionId": str(s.subscription_id), "callback": s.callback,
             "consumerSubscriptionId": s.consumer_subscription_id, "resourceTypeId": s.resource_type_id}
            for s in page["items"]]}
