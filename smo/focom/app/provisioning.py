"""O2-IMS Artifacts, Cluster, Infrastructure and Provisioning resources (SA-FOCOM-7).

What it is: REST resources with the spec's attribute names (`ORAN.O2ims.Artifacts.yaml`, `.Cluster.yaml`, `.Infrastructure.yaml`,
`.Provisioning.yaml`), stored one row per object in `o2ims_object` (`models.O2imsObject`) and validated per kind, with referential checks between
them (a NodeCluster names a NodeClusterType and an ArtifactResource, a ClusterResource names inventory resources, ...). Mounted by `main.py` through
`router`. Design record: `focom/README.md` (1.2, 2.4, 2.8).

How the routes exist: the nine plain kinds in `KINDS` get four routes each (create, list, get, delete) from `_register`, in a loop at import time;
only the ProvisioningRequest routes are written out below. To add a kind, add a pydantic body, a `KINDS` entry and, if other objects may name it, a
`REFERRERS` entry; the routes follow.

A ProvisioningRequest is resolved against an ArtifactResource (templateName + templateVersion) and fulfilled at the model level: a NodeCluster is
created, the request's `provisionedResourceSet` points at it and the status is FULFILLED in the same call (as NFO's Instantiate is synchronous).
FOCOM builds no real cluster: the NodeCluster records what was asked for, nothing is deployed on an O-Cloud, and `clusterDistributionDescription` says
so. Deleting the request deletes the cluster it created.

The Gateway / SiteNetwork / AttachmentCircuit / Port types of `ORAN.O2ims.Infrastructure.yaml` describe what an InfrastructureResource's
`extensions` carry for a network resource; they are stored there, not validated as separate schemas.

Errors: unknown attributes, a dangling reference or deleting an object another still names answer 422 `SCHEMA_VALIDATION_FAILED`; an unknown id on
get answers 404 `NRM_OBJECT_NOT_FOUND`; delete of an unknown id is 204. Ids are generated UUIDs; the caller cannot choose them.
"""

import datetime
import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.pagination import PageLimit, PageOffset, paginate

from .common import AttributeValuePair, ioc
from .models import O2imsObject, Resource

router = APIRouter()
Avps = list[AttributeValuePair]


# Base of every body in this file: `extra="forbid"`, so an attribute the spec does not name is refused with 422 instead of being stored.
class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


# Body of POST /artifact-resource-types (O2-IMS ArtifactResourceType).
class ArtifactResourceType(_Body):
    name: str
    description: str
    extensions: Avps = []


# Body of POST /artifact-resources. `name` + `version` is what a ProvisioningRequest's `templateName` + `templateVersion` is matched against; uniqueness is not enforced.
class ArtifactResource(_Body):
    name: str
    version: str
    description: str
    parameterSchema: Avps = []


# Body of POST /node-cluster-types.
class NodeClusterType(_Body):
    name: str
    description: str
    extensions: Avps = []


# Body of POST /node-clusters. `nodeClusterTypeId` and `artifactResourceId` must name existing objects; each entry of `clusterResourceIds` and
# `clusterResourceGroups` must exist too (`_check_node_cluster`).
class NodeCluster(_Body):
    clientNodeClusterId: str
    name: str
    description: str
    clusterDistributionDescription: str
    nodeClusterTypeId: str
    artifactResourceId: str
    clusterResourceIds: list[str] = []
    clusterResourceGroups: list[str] = []
    extensions: Avps = []


# Body of POST /cluster-resource-types.
class ClusterResourceType(_Body):
    name: str
    description: str
    extensions: Avps = []


# Body of POST /cluster-resources. `resourceId` must be the UUID of an inventory resource; `clusterResourceTypeId`, `memberOf` and `artifactResourceIds`
# must name existing objects (`_check_cluster_resource`).
class ClusterResource(_Body):
    name: str
    description: str
    clusterResourceTypeId: str
    resourceId: str
    memberOf: list[str] = []
    artifactResourceIds: list[str] = []
    extensions: Avps = []


# Body of POST /cluster-resource-groups. Each entry of `clusterResources` must name an existing ClusterResource.
class ClusterResourceGroup(_Body):
    name: str
    description: str
    clusterResources: list[str]
    extensions: Avps = []


# Body of POST /infrastructure-resource-types.
class InfrastructureResourceType(_Body):
    name: str
    description: str
    extensions: Avps = []


# Body of POST /infrastructure-resources. `inventoryResourceIds` must be inventory resource UUIDs; the type, the artifact and every linked infrastructure
# resource must exist (`_check_infra`). Network details (gateway, port, ...) travel in `extensions`.
class InfrastructureResource(_Body):
    name: str
    description: str
    infrastructureResourceTypeId: str
    inventoryResourceIds: list[str]
    artifactResourceId: str
    linkedInfrastructureResourceIds: list[str] = []
    extensions: Avps = []


# Body of POST /provisioning-requests. `templateName` + `templateVersion` select an ArtifactResource. `templateParameters` is read for two keys only,
# `nodeClusterTypeId` and `clientNodeClusterId`; other keys are stored on the request and not used.
class ProvisioningRequest(_Body):
    name: str
    description: str
    templateName: str
    templateVersion: str
    templateParameters: Avps = []
    provisioningRequestReference: str | None = None


def _invalid(detail: str):
    """Returns the 422 `SCHEMA_VALIDATION_FAILED` problem with `detail` (the caller raises it)."""
    return framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED, detail=detail)


def _exists(db: Session, kind: str, object_id: str) -> bool:
    """Returns True when an object of `kind` with `object_id` is stored."""
    return db.get(O2imsObject, (kind, object_id)) is not None


def _require(db: Session, kind: str, ids: str | list[str], what: str) -> None:
    """Raises 422 unless every id in `ids` (one id or a list) names a stored object of `kind`; `what` is the attribute name shown in the error.

    The error lists all the missing ids at once.
    """
    missing = [i for i in ([ids] if isinstance(ids, str) else ids) if not _exists(db, kind, i)]
    if missing:
        raise _invalid(f"{what} not found: {missing}")


def _require_inventory(db: Session, ids: list[str], what: str) -> None:
    """Raises 422 for the first id in `ids` that is not the UUID of a stored inventory `Resource` (a value that is not a UUID counts as not found).

    Inventory resources are checked against the `resource` table, not `o2ims_object`.
    """
    for i in ids:
        try:
            found = db.get(Resource, uuid.UUID(i)) is not None
        except ValueError:
            found = False
        if not found:
            raise _invalid(f"{what} {i!r} is not an inventory resource")


def _check_node_cluster(db: Session, b: NodeCluster) -> None:
    """Referential check for a NodeCluster body: type, artifact, cluster resources and groups must exist (422 otherwise)."""
    _require(db, "NodeClusterType", b.nodeClusterTypeId, "nodeClusterTypeId")
    _require(db, "ArtifactResource", b.artifactResourceId, "artifactResourceId")
    _require(db, "ClusterResource", b.clusterResourceIds, "clusterResourceIds")
    _require(db, "ClusterResourceGroup", b.clusterResourceGroups, "clusterResourceGroups")


def _check_cluster_resource(db: Session, b: ClusterResource) -> None:
    """Referential check for a ClusterResource body: type, groups, artifacts and the inventory resource must exist (422 otherwise)."""
    _require(db, "ClusterResourceType", b.clusterResourceTypeId, "clusterResourceTypeId")
    _require(db, "ClusterResourceGroup", b.memberOf, "memberOf")
    _require(db, "ArtifactResource", b.artifactResourceIds, "artifactResourceIds")
    _require_inventory(db, [b.resourceId], "resourceId")


def _check_group(db: Session, b: ClusterResourceGroup) -> None:
    """Referential check for a ClusterResourceGroup body: every listed ClusterResource must exist (422 otherwise)."""
    _require(db, "ClusterResource", b.clusterResources, "clusterResources")


def _check_infra(db: Session, b: InfrastructureResource) -> None:
    """Referential check for an InfrastructureResource body: type, artifact, linked infrastructure resources and inventory resources must exist (422 otherwise)."""
    _require(db, "InfrastructureResourceType", b.infrastructureResourceTypeId, "infrastructureResourceTypeId")
    _require(db, "ArtifactResource", b.artifactResourceId, "artifactResourceId")
    _require(db, "InfrastructureResource", b.linkedInfrastructureResourceIds, "linkedInfrastructureResourceIds")
    _require_inventory(db, b.inventoryResourceIds, "inventoryResourceIds")


# kind -> (route path, the id attribute in the view, the body model, the referential check or None). `_register` builds the routes from this table.
KINDS: dict[str, tuple[str, str, type[_Body], Any]] = {
    "ArtifactResourceType": ("artifact-resource-types", "artifactResourceTypeId", ArtifactResourceType, None),
    "ArtifactResource": ("artifact-resources", "artifactResourceId", ArtifactResource, None),
    "NodeClusterType": ("node-cluster-types", "nodeClusterTypeId", NodeClusterType, None),
    "NodeCluster": ("node-clusters", "nodeClusterId", NodeCluster, _check_node_cluster),
    "ClusterResourceType": ("cluster-resource-types", "clusterResourceTypeId", ClusterResourceType, None),
    "ClusterResource": ("cluster-resources", "clusterResourceId", ClusterResource, _check_cluster_resource),
    "ClusterResourceGroup": ("cluster-resource-groups", "clusterResourceGroupId", ClusterResourceGroup, _check_group),
    "InfrastructureResourceType": ("infrastructure-resource-types", "infrastructureResourceTypeId", InfrastructureResourceType, None),
    "InfrastructureResource": ("infrastructure-resources", "infrastructureResourceId", InfrastructureResource, _check_infra),
}
# Used by delete: a kind that other objects still name cannot be deleted. kind -> [(kind of the referrer, attribute that holds the id or a list of ids)].
# The table is written by hand and has to follow the `_check_*` functions: a reference checked on create but missing here can be deleted out from under
# the object that names it. Inventory resources are not covered (`DELETE /resources/{id}` does not look at these objects).
REFERRERS = {
    "ArtifactResource": [("NodeCluster", "artifactResourceId"), ("ClusterResource", "artifactResourceIds"),
                         ("InfrastructureResource", "artifactResourceId")],
    "NodeClusterType": [("NodeCluster", "nodeClusterTypeId")],
    "ClusterResourceType": [("ClusterResource", "clusterResourceTypeId")],
    "ClusterResource": [("NodeCluster", "clusterResourceIds"), ("ClusterResourceGroup", "clusterResources")],
    "ClusterResourceGroup": [("NodeCluster", "clusterResourceGroups"), ("ClusterResource", "memberOf")],
    "InfrastructureResourceType": [("InfrastructureResource", "infrastructureResourceTypeId")],
    "InfrastructureResource": [("InfrastructureResource", "linkedInfrastructureResourceIds")],
}


def _view(kind: str, id_attr: str, path: str, row: O2imsObject) -> dict:
    """Returns the wire form of a stored object: the `ioc` pair, its id under the kind's id attribute, then the stored attributes."""
    return {**ioc(kind, path, row.object_id), id_attr: row.object_id, **row.attributes}


def _referrers(db: Session, kind: str, object_id: str) -> list[str]:
    """Returns `"<Kind> <id>"` for every stored object that names (`kind`, `object_id`) according to `REFERRERS`; empty when nothing does.

    Scans every row of each referring kind and compares in Python, since the references live inside the JSON attributes.
    """
    found = []
    for referrer_kind, attr in REFERRERS.get(kind, []):
        for row in db.scalars(select(O2imsObject).where(O2imsObject.kind == referrer_kind)).all():
            value = row.attributes.get(attr)
            if value == object_id or (isinstance(value, list) and object_id in value):
                found.append(f"{referrer_kind} {row.object_id}")
    return found


def _register(kind: str, path: str, id_attr: str, model: type[_Body], check) -> None:
    """Adds the create / list / get / delete routes of one kind to `router`.

    - create: runs `check` (the kind's referential check, if any) then stores the body (`exclude_none`, so null attributes are not kept) under a new UUID
      and answers 201 with the view.
    - list: paginated.
    - get: 404 `NRM_OBJECT_NOT_FOUND` for an unknown id.
    - delete: idempotent (204 for an unknown id); 422 while `_referrers` finds an object that names it.

    Called once per entry of `KINDS` at import time. The four functions are closures over `kind`, `path` and `id_attr`; they are not part of the module's namespace.
    """
    # `body` is annotated with the model class passed in, so FastAPI validates the request against it (and refuses unknown attributes).
    def create(body: model, db: Session = Depends(get_session)):  # type: ignore[valid-type]
        if check is not None:
            check(db, body)
        row = O2imsObject(kind=kind, object_id=str(uuid.uuid4()), attributes=body.model_dump(exclude_none=True, mode="json"))  # type: ignore[attr-defined]  # `body` is the registered model class, which FastAPI needs as the annotation
        db.add(row)
        db.commit()
        return _view(kind, id_attr, path, row)

    def list_(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
        page = paginate(db, select(O2imsObject).where(O2imsObject.kind == kind), limit, offset)
        return {**page, "items": [_view(kind, id_attr, path, r) for r in page["items"]]}

    def get(object_id: str, db: Session = Depends(get_session)):
        row = db.get(O2imsObject, (kind, object_id))
        if row is None:
            raise framework_error(FrameworkError.NRM_OBJECT_NOT_FOUND, detail=f"no such {kind} {object_id}")
        return _view(kind, id_attr, path, row)

    def delete(object_id: str, db: Session = Depends(get_session)):
        row = db.get(O2imsObject, (kind, object_id))
        if row is not None:
            users = _referrers(db, kind, object_id)
            if users:
                raise _invalid(f"{kind} {object_id} is still referenced by {', '.join(users)}")
            db.delete(row)
            db.commit()

    # Each closure gets a distinct name per kind: FastAPI uses the function name as the route name, and the same four names for nine kinds would collide.
    for fn, name in ((create, "create"), (list_, "list"), (get, "get"), (delete, "delete")):
        fn.__name__ = f"{name}_{path.replace('-', '_')}"
    router.post(f"/{path}", status_code=201)(create)
    router.get(f"/{path}")(list_)
    router.get(f"/{path}/{{object_id}}")(get)
    router.delete(f"/{path}/{{object_id}}", status_code=204)(delete)


for _kind, (_path, _id_attr, _model, _check) in KINDS.items():
    _register(_kind, _path, _id_attr, _model, _check)


# ---------------------------------------------------------------- provisioning requests

def _now() -> str:
    """Returns the current UTC time as an ISO 8601 string (the `updateTime` of a request status)."""
    return datetime.datetime.now(datetime.UTC).isoformat()


def _request_view(row: O2imsObject) -> dict:
    """Returns the wire form of a ProvisioningRequest: the `ioc` pair, `provisioningRequestId`, then the stored attributes."""
    return {**ioc("ProvisioningRequest", "provisioning-requests", row.object_id), "provisioningRequestId": row.object_id, **row.attributes}


def _param(request: ProvisioningRequest, key: str, default: str) -> str:
    """Returns the string value of the template parameter named `key`, or `default` when the request has none."""
    return next((str(p.value) for p in request.templateParameters if p.key == key), default)


@router.post("/provisioning-requests", status_code=201)
def create_provisioning_request(body: ProvisioningRequest, db: Session = Depends(get_session)):
    """Resolve the template, create the NodeCluster, record the outcome."""
    # Route notes: 201. In order: (1) the first ArtifactResource whose name and version equal `templateName` and `templateVersion` is the template, else 422;
    # (2) the NodeClusterType is the `nodeClusterTypeId` template parameter, else the first NodeClusterType stored (the query has no order, so with several
    # types the default is whichever the database returns first), else 422; a parameter that names no stored type is 422; (3) a NodeCluster is stored
    # directly (it bypasses `_check_node_cluster`) with `clusterDistributionDescription` saying it is model-only; (4) the request is stored with
    # `provisionedResourceSet` pointing at the cluster and status FULFILLED. Both rows are added before the one commit, so a failure leaves neither.
    template = next((r for r in db.scalars(select(O2imsObject).where(O2imsObject.kind == "ArtifactResource")).all()
                     if r.attributes["name"] == body.templateName and r.attributes["version"] == body.templateVersion), None)
    if template is None:
        raise _invalid(f"no ArtifactResource {body.templateName}@{body.templateVersion} to use as the template")
    cluster_type_id = _param(body, "nodeClusterTypeId", "")
    if not cluster_type_id:
        default = next((r for r in db.scalars(select(O2imsObject).where(O2imsObject.kind == "NodeClusterType")).all()), None)
        cluster_type_id = default.object_id if default else ""
    if not cluster_type_id:
        raise _invalid("no NodeClusterType: register one or pass a nodeClusterTypeId template parameter")
    _require(db, "NodeClusterType", cluster_type_id, "nodeClusterTypeId")
    request_id, cluster_id = str(uuid.uuid4()), str(uuid.uuid4())
    db.add(O2imsObject(kind="NodeCluster", object_id=cluster_id, attributes=NodeCluster(
        clientNodeClusterId=_param(body, "clientNodeClusterId", body.name), name=body.name, description=body.description,
        clusterDistributionDescription="model-only: recorded by FOCOM, no cluster is deployed on an O-Cloud",
        nodeClusterTypeId=cluster_type_id, artifactResourceId=template.object_id).model_dump(exclude_none=True, mode="json")))
    attributes = body.model_dump(exclude_none=True, mode="json")
    attributes["provisionedResourceSet"] = {"nodeClusterId": cluster_id, "infrastructureResourceIds": []}
    attributes["status"] = {"updateTime": _now(), "message": f"fulfilled from {body.templateName}@{body.templateVersion}",
                            "provisioningPhase": "FULFILLED", "infrastructureResourceProvisioningStatus": []}
    row = O2imsObject(kind="ProvisioningRequest", object_id=request_id, attributes=attributes)
    db.add(row)
    db.commit()
    return _request_view(row)


@router.get("/provisioning-requests")
def list_provisioning_requests(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    # Route notes: paginated.
    page = paginate(db, select(O2imsObject).where(O2imsObject.kind == "ProvisioningRequest"), limit, offset)
    return {**page, "items": [_request_view(r) for r in page["items"]]}


@router.get("/provisioning-requests/{request_id}")
def get_provisioning_request(request_id: str, db: Session = Depends(get_session)):
    # Route notes: 404 `NRM_OBJECT_NOT_FOUND` for an unknown id.
    row = db.get(O2imsObject, ("ProvisioningRequest", request_id))
    if row is None:
        raise framework_error(FrameworkError.NRM_OBJECT_NOT_FOUND, detail=f"no such ProvisioningRequest {request_id}")
    return _request_view(row)


@router.delete("/provisioning-requests/{request_id}", status_code=204)
def delete_provisioning_request(request_id: str, db: Session = Depends(get_session)):
    """The request is deleted with the NodeCluster it created."""
    # Route notes: idempotent, 204 for an unknown id. Deletes the NodeCluster named in `provisionedResourceSet` (if it still exists) and the request in one
    # commit. The cluster is deleted without the `_referrers` check, so cluster resources that name it are not stopped. A cluster deleted earlier through
    # `DELETE /node-clusters/{id}` simply is not found here.
    row = db.get(O2imsObject, ("ProvisioningRequest", request_id))
    if row is None:
        return
    cluster = db.get(O2imsObject, ("NodeCluster", (row.attributes.get("provisionedResourceSet") or {}).get("nodeClusterId", "")))
    if cluster is not None:
        db.delete(cluster)
    db.delete(row)
    db.commit()
