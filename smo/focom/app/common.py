"""Shared FOCOM constants, the lazily seeded Phase 1 topology, and the O2IMS
InformationObjectClass fields every resource view carries."""

import os
import uuid
from typing import Any

from pydantic import BaseModel
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
IMS_ENDPOINT = os.environ.get("FOCOM_IMS_ENDPOINT", "/focom")
SMO_REGISTRATION_SERVICE = os.environ.get("FOCOM_SMO_REGISTRATION_SERVICE", "http://r1-termination:8000")


def auto_register_resource_types() -> bool:
    """SA-FOCOM-9: legacy behaviour (an unknown resourceTypeId on provision is
    registered) only when explicitly enabled; read at call time."""
    return os.environ.get("FOCOM_AUTO_REGISTER_RESOURCE_TYPES", "").lower() in ("1", "true", "yes")


def global_cloud_id(o_cloud_id: str) -> str:
    """The SMO-assigned, globally unique O-Cloud id: stable for a given oCloudId."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"o-cloud:{o_cloud_id}"))


class AttributeValuePair(BaseModel):
    key: str
    value: Any


def ioc(object_class: str, path: str, object_id: str) -> dict:
    """InformationObjectClass: objectClass + objectInstance (the resource's URL)."""
    return {"objectClass": object_class, "objectInstance": f"/focom/{path}/{object_id}"}


def ensure_phase1_topology(db: Session) -> None:
    """Lazily seeds the single degenerate ResourceType / ResourcePool /
    DeploymentManager / Location / OCloudSite on first read (D-DEPLOY-FOCOM-1)."""
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
    site_ids = [s.o_cloud_site_id for s in db.scalars(select(OCloudSite).where(OCloudSite.location_id == loc.global_location_id)).all()]
    return {**ioc("Location", "locations", loc.global_location_id), "globalLocationId": loc.global_location_id, "name": loc.name,
            "description": loc.description, "oCloudId": loc.o_cloud_id, "oCloudSiteIds": site_ids,
            "coordinate": loc.coordinate, "address": loc.address, "extensions": loc.extensions or []}


def pool_view(db: Session, p: ResourcePool) -> dict:
    resource_ids = [str(r) for r in db.scalars(select(Resource.resource_id).where(Resource.resource_pool_id == p.resource_pool_id)).all()]
    return {**ioc("ResourcePool", "resource-pools", p.resource_pool_id), "resourcePoolId": p.resource_pool_id, "name": p.name,
            "description": p.description, "oCloudId": p.o_cloud_id, "oCloudSiteId": p.o_cloud_site_id,
            "resources": resource_ids, "extensions": p.extensions or []}


def site_view(db: Session, s: OCloudSite) -> dict:
    pools = db.scalars(select(ResourcePool).where(ResourcePool.o_cloud_site_id == s.o_cloud_site_id)).all()
    return {**ioc("OCloudSite", "o-cloud-sites", s.o_cloud_site_id), "oCloudSiteId": s.o_cloud_site_id, "locationId": s.location_id,
            "name": s.name, "description": s.description, "oCloudId": s.o_cloud_id,
            "resourcePools": [pool_view(db, p) for p in pools], "extensions": s.extensions or []}
