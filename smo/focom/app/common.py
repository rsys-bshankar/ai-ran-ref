"""Constants, the lazily seeded Phase 1 topology and the view builders shared by every FOCOM route file.

What it is: the Phase 1 identifiers (`PHASE1_*`, `SEEDED_RESOURCE_TYPES`), the two endpoint strings `GET /inventory` reports, the seeding function
`ensure_phase1_topology` and the dict builders for the O2-IMS objects that more than one route file returns (`location_view`, `site_view`,
`pool_view`, and `ioc`, the `objectClass` / `objectInstance` pair every view carries). Design record: `focom/README.md` (1.5, 2.2); the
degenerate single-cluster topology is D-DEPLOY-FOCOM-1.

Where it sits: imported by `main.py`, `sites.py`, `fcaps.py` and `provisioning.py`; it imports only `models.py` and `smo_shared.mtls`. It makes
no HTTP call and owns no route.

Owns: the seed rows and the shape of the three views above. Does not own: the other views (resource type, resource, deployment manager live in
`main.py`; alarms, performance and the O2-IMS object kinds live in `fcaps.py` and `provisioning.py`).

Before editing: `ensure_phase1_topology` is called at the start of the read routes and it commits, so it is a write that happens on a GET; the
ids it seeds are the ones the tests, NFO (`oCloudId`) and SO SMOS (`pool-0`) rely on, so changing a value here is a contract change.
"""

import os
import uuid
from typing import Any

from pydantic import BaseModel
from smo_shared import mtls
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import DeploymentManager, Location, OCloudSite, Resource, ResourcePool, ResourceType

PHASE1_CLUSTER_ID = "phase1-degenerate-cluster"
PHASE1_RESOURCE_TYPE_ID = "generic"
PHASE1_POOL_ID = "pool-0"
PHASE1_DEPLOYMENT_MANAGER_ID = "dm-0"
PHASE1_LOCATION_ID = "loc-0"
PHASE1_SITE_ID = "site-0"

# The resource types callers provision (SO SMOS INFRA steps, the demo runbook).
# Seeded so SA-FOCOM-9 can refuse an unknown type.
SEEDED_RESOURCE_TYPES = {
    PHASE1_RESOURCE_TYPE_ID: "Phase 1 degenerate single-node resource type",
    "gpu-l40": "NVIDIA L40 GPU node",
    "pserver": "Physical server",
}

# O2IMS infrastructureManagementServicesEndPoint / smoRegistrationService
# Both values are read once at import (unlike `auto_register_resource_types`, which reads its flag per call), so a changed variable needs a restart.
# `mtls.http_url` turns the default `http://` registration service into `https://` when mTLS is on; the endpoint `/focom` is a path, not a URL.
IMS_ENDPOINT = os.environ.get("FOCOM_IMS_ENDPOINT", "/focom")
SMO_REGISTRATION_SERVICE = mtls.http_url(os.environ.get("FOCOM_SMO_REGISTRATION_SERVICE", "http://r1-termination:8000"))


def auto_register_resource_types() -> bool:
    """Returns True when `FOCOM_AUTO_REGISTER_RESOURCE_TYPES` is `1`, `true` or `yes` (any case); False otherwise, which is the default (SA-FOCOM-9).

    When True, `POST /resources/provision` registers an unknown `resourceTypeId` instead of refusing it with 404. The variable is read at each call, not at
    import, so a test can set it with `monkeypatch.setenv`; this function is the only reader.
    """
    return os.environ.get("FOCOM_AUTO_REGISTER_RESOURCE_TYPES", "").lower() in ("1", "true", "yes")


def global_cloud_id(o_cloud_id: str) -> str:
    """Returns the SMO-assigned global id of an O-Cloud: a UUIDv5 of `o-cloud:<oCloudId>` in the URL namespace.

    Deterministic on purpose: the same `oCloudId` gives the same `globalCloudId` on every call, on every replica and after a restart, with nothing stored.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"o-cloud:{o_cloud_id}"))


# O2-IMS AttributeValuePair: `{key, value}` with a value of any JSON type. Used for the `extensions` lists of the site model and the O2-IMS objects.
# A docstring is deliberately not used: it would change the OpenAPI schema.
class AttributeValuePair(BaseModel):
    key: str
    value: Any


def ioc(object_class: str, path: str, object_id: str) -> dict:
    """Returns the InformationObjectClass pair every O2-IMS view carries: `objectClass` and `objectInstance`, the object's URL `/focom/<path>/<id>`.

    `path` is the collection segment of the route (`locations`, `resource-pools`, ...). The `/focom` prefix is written here and is not taken from
    `IMS_ENDPOINT`.
    """
    return {"objectClass": object_class, "objectInstance": f"/focom/{path}/{object_id}"}


def ensure_phase1_topology(db: Session) -> None:
    """Inserts whatever part of the Phase 1 topology is missing and commits once, only if something was inserted (D-DEPLOY-FOCOM-1).

    Seeds the resource types in `SEEDED_RESOURCE_TYPES`, `loc-0`, `site-0`, `pool-0` and `dm-0`. Idempotent: existing rows are never overwritten, with one
    exception: a `pool-0` whose `o_cloud_site_id` is empty (a row created before the site model existed) is linked to `site-0`.

    Called at the start of the read routes (and of the routes that need a seeded row) instead of at startup, so no module needs a startup seeding step.
    Two first requests racing on an empty database can both try to insert the same primary key; the loser's commit fails and that request answers 500
    (the other request seeds the rows, so a retry succeeds).
    """
    changed = False
    for type_id, description in SEEDED_RESOURCE_TYPES.items():
        if db.get(ResourceType, type_id) is None:
            db.add(ResourceType(resource_type_id=type_id, name=type_id, description=description))
            changed = True
    if db.get(Location, PHASE1_LOCATION_ID) is None:
        db.add(Location(global_location_id=PHASE1_LOCATION_ID, name="location-0", description="Phase 1 degenerate location",
                        o_cloud_id=PHASE1_CLUSTER_ID))
        changed = True
    if db.get(OCloudSite, PHASE1_SITE_ID) is None:
        db.add(OCloudSite(o_cloud_site_id=PHASE1_SITE_ID, location_id=PHASE1_LOCATION_ID, name="site-0",
                          description="Phase 1 degenerate O-Cloud site", o_cloud_id=PHASE1_CLUSTER_ID))
        changed = True
    pool = db.get(ResourcePool, PHASE1_POOL_ID)
    if pool is None:
        db.add(ResourcePool(resource_pool_id=PHASE1_POOL_ID, name="pool-0", description="Phase 1 degenerate single-node resource pool",
                            o_cloud_id=PHASE1_CLUSTER_ID, o_cloud_site_id=PHASE1_SITE_ID))
        changed = True
    elif pool.o_cloud_site_id is None:
        pool.o_cloud_site_id = PHASE1_SITE_ID
        changed = True
    if db.get(DeploymentManager, PHASE1_DEPLOYMENT_MANAGER_ID) is None:
        db.add(DeploymentManager(deployment_manager_id=PHASE1_DEPLOYMENT_MANAGER_ID, name=PHASE1_CLUSTER_ID,
                                 description="Phase 1 degenerate single-node deployment manager",
                                 o_cloud_id=PHASE1_CLUSTER_ID, service_uri=f"http://{PHASE1_CLUSTER_ID}:6443"))
        changed = True
    if changed:
        db.commit()


def location_view(db: Session, loc: Location) -> dict:
    """Returns the O2-IMS `Location` object for `loc`, with `oCloudSiteIds` listing the sites that name it (one query).

    `coordinate` and `address` are returned as stored (plain strings); `extensions` is `[]` when none were set.
    """
    site_ids = [s.o_cloud_site_id for s in db.scalars(select(OCloudSite).where(OCloudSite.location_id == loc.global_location_id)).all()]
    return {**ioc("Location", "locations", loc.global_location_id), "globalLocationId": loc.global_location_id, "name": loc.name,
            "description": loc.description, "oCloudId": loc.o_cloud_id, "oCloudSiteIds": site_ids,
            "coordinate": loc.coordinate, "address": loc.address, "extensions": loc.extensions or []}


def pool_view(db: Session, p: ResourcePool) -> dict:
    """Returns the O2-IMS `ResourcePool` object for `p`, with `resources` listing the ids (as strings) of the resources in the pool (one query).

    The list is not paginated: a pool with many resources returns all their ids in every pool view, including the inline pools of `site_view`.
    """
    resource_ids = [str(r) for r in db.scalars(select(Resource.resource_id).where(Resource.resource_pool_id == p.resource_pool_id)).all()]
    return {**ioc("ResourcePool", "resource-pools", p.resource_pool_id), "resourcePoolId": p.resource_pool_id, "name": p.name,
            "description": p.description, "oCloudId": p.o_cloud_id, "oCloudSiteId": p.o_cloud_site_id,
            "resources": resource_ids, "extensions": p.extensions or []}


def site_view(db: Session, s: OCloudSite) -> dict:
    """Returns the O2-IMS `OCloudSite` object for `s`, with its pools inline in `resourcePools` (each a full `pool_view`)."""
    pools = db.scalars(select(ResourcePool).where(ResourcePool.o_cloud_site_id == s.o_cloud_site_id)).all()
    return {**ioc("OCloudSite", "o-cloud-sites", s.o_cloud_site_id), "oCloudSiteId": s.o_cloud_site_id, "locationId": s.location_id,
            "name": s.name, "description": s.description, "oCloudId": s.o_cloud_id,
            "resourcePools": [pool_view(db, p) for p in pools], "extensions": s.extensions or []}
