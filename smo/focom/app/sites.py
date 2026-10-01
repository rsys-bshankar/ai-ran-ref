"""O2IMS Location, OCloudSite and ResourcePool management (SA-FOCOM-2)."""

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.pagination import PageLimit, PageOffset, paginate

from .common import PHASE1_CLUSTER_ID, AttributeValuePair, ensure_phase1_topology, location_view, pool_view, site_view
from .models import Location, OCloudSite, Resource, ResourcePool

router = APIRouter()


class LocationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    globalLocationId: str | None = None
    name: str
    description: str = ""
    coordinate: str | None = None
    address: str | None = None
    extensions: list[AttributeValuePair] = []


class SiteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    oCloudSiteId: str | None = None
    locationId: str
    name: str
    description: str = ""
    extensions: list[AttributeValuePair] = []


class PoolBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resourcePoolId: str | None = None
    name: str
    description: str = ""
    oCloudSiteId: str
    extensions: list[AttributeValuePair] = []


def _missing(kind: str, object_id: str):
    return framework_error(FrameworkError.NRM_OBJECT_NOT_FOUND, detail=f"no such {kind} {object_id}")


def _conflict(detail: str):
    return framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED, detail=detail)


@router.post("/locations", status_code=201)
def create_location(body: LocationBody, db: Session = Depends(get_session)):
    ensure_phase1_topology(db)
    location_id = body.globalLocationId or str(uuid.uuid4())
    if db.get(Location, location_id) is not None:
        raise _conflict(f"location {location_id} already exists")
    loc = Location(global_location_id=location_id, name=body.name, description=body.description, o_cloud_id=PHASE1_CLUSTER_ID,
                   coordinate=body.coordinate, address=body.address, extensions=[e.model_dump() for e in body.extensions])
    db.add(loc)
    db.commit()
    return location_view(db, loc)


@router.get("/locations")
def list_locations(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    ensure_phase1_topology(db)
    page = paginate(db, select(Location), limit, offset)
    return {**page, "items": [location_view(db, loc) for loc in page["items"]]}


@router.get("/locations/{location_id}")
def get_location(location_id: str, db: Session = Depends(get_session)):
    ensure_phase1_topology(db)
    loc = db.get(Location, location_id)
    if loc is None:
        raise _missing("location", location_id)
    return location_view(db, loc)


@router.delete("/locations/{location_id}", status_code=204)
def delete_location(location_id: str, db: Session = Depends(get_session)):
    loc = db.get(Location, location_id)
    if loc is not None:
        if db.scalars(select(OCloudSite).where(OCloudSite.location_id == location_id)).first() is not None:
            raise _conflict(f"location {location_id} still has O-Cloud sites")
        db.delete(loc)
        db.commit()


@router.post("/o-cloud-sites", status_code=201)
def create_site(body: SiteBody, db: Session = Depends(get_session)):
    ensure_phase1_topology(db)
    if db.get(Location, body.locationId) is None:
        raise _conflict(f"unknown location {body.locationId}")
    site_id = body.oCloudSiteId or str(uuid.uuid4())
    if db.get(OCloudSite, site_id) is not None:
        raise _conflict(f"O-Cloud site {site_id} already exists")
    site = OCloudSite(o_cloud_site_id=site_id, location_id=body.locationId, name=body.name, description=body.description,
                      o_cloud_id=PHASE1_CLUSTER_ID, extensions=[e.model_dump() for e in body.extensions])
    db.add(site)
    db.commit()
    return site_view(db, site)


@router.get("/o-cloud-sites")
def list_sites(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    ensure_phase1_topology(db)
    page = paginate(db, select(OCloudSite), limit, offset)
    return {**page, "items": [site_view(db, s) for s in page["items"]]}


@router.get("/o-cloud-sites/{site_id}")
def get_site(site_id: str, db: Session = Depends(get_session)):
    ensure_phase1_topology(db)
    site = db.get(OCloudSite, site_id)
    if site is None:
        raise _missing("O-Cloud site", site_id)
    return site_view(db, site)


@router.delete("/o-cloud-sites/{site_id}", status_code=204)
def delete_site(site_id: str, db: Session = Depends(get_session)):
    site = db.get(OCloudSite, site_id)
    if site is not None:
        if db.scalars(select(ResourcePool).where(ResourcePool.o_cloud_site_id == site_id)).first() is not None:
            raise _conflict(f"O-Cloud site {site_id} still has resource pools")
        db.delete(site)
        db.commit()


@router.post("/resource-pools", status_code=201)
def create_resource_pool(body: PoolBody, db: Session = Depends(get_session)):
    ensure_phase1_topology(db)
    if db.get(OCloudSite, body.oCloudSiteId) is None:
        raise _conflict(f"unknown O-Cloud site {body.oCloudSiteId}")
    pool_id = body.resourcePoolId or str(uuid.uuid4())
    if db.get(ResourcePool, pool_id) is not None:
        raise _conflict(f"resource pool {pool_id} already exists")
    pool = ResourcePool(resource_pool_id=pool_id, name=body.name, description=body.description, o_cloud_id=PHASE1_CLUSTER_ID,
                        o_cloud_site_id=body.oCloudSiteId, extensions=[e.model_dump() for e in body.extensions])
    db.add(pool)
    db.commit()
    return pool_view(db, pool)


@router.delete("/resource-pools/{resource_pool_id}", status_code=204)
def delete_resource_pool(resource_pool_id: str, db: Session = Depends(get_session)):
    pool = db.get(ResourcePool, resource_pool_id)
    if pool is not None:
        if db.scalars(select(Resource).where(Resource.resource_pool_id == resource_pool_id)).first() is not None:
            raise _conflict(f"resource pool {resource_pool_id} still has resources")
        db.delete(pool)
        db.commit()
