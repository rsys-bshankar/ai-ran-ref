"""O2IMS Artifacts, Cluster, Infrastructure and Provisioning resources (SA-FOCOM-7).

REST resources with the spec's attribute names (`ORAN.O2ims.Artifacts.yaml`,
`.Cluster.yaml`, `.Infrastructure.yaml`, `.Provisioning.yaml`), stored one row
per object in `o2ims_object` and validated per kind, with referential checks
between them (a NodeCluster names a NodeClusterType and an ArtifactResource,
a ClusterResource names inventory resources, ...).

A ProvisioningRequest is resolved against an ArtifactResource (templateName +
templateVersion) and fulfilled at the model level: a NodeCluster is created,
the request's `provisionedResourceSet` points at it and the status goes
PENDING -> PROGRESSING -> FULFILLED in the same call (as NFO's Instantiate is
synchronous). FOCOM builds no real cluster: the NodeCluster records what was
asked for, nothing is deployed on an O-Cloud, and `clusterDistributionDescription`
says so. Deleting the request deletes the cluster it created.

The Gateway / SiteNetwork / AttachmentCircuit / Port types of
`ORAN.O2ims.Infrastructure.yaml` describe what an InfrastructureResource's
`extensions` carry for a network resource; they are stored there, not
validated as separate schemas.
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


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArtifactResourceType(_Body):
    name: str
    description: str
    extensions: Avps = []


class ArtifactResource(_Body):
    name: str
    version: str
    description: str
    parameterSchema: Avps = []


class NodeClusterType(_Body):
    name: str
    description: str
    extensions: Avps = []


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


class ClusterResourceType(_Body):
    name: str
    description: str
    extensions: Avps = []


class ClusterResource(_Body):
    name: str
    description: str
    clusterResourceTypeId: str
    resourceId: str
    memberOf: list[str] = []
    artifactResourceIds: list[str] = []
    extensions: Avps = []


class ClusterResourceGroup(_Body):
    name: str
    description: str
    clusterResources: list[str]
    extensions: Avps = []


class InfrastructureResourceType(_Body):
    name: str
    description: str
    extensions: Avps = []


class InfrastructureResource(_Body):
    name: str
    description: str
    infrastructureResourceTypeId: str
    inventoryResourceIds: list[str]
    artifactResourceId: str
    linkedInfrastructureResourceIds: list[str] = []
    extensions: Avps = []


class ProvisioningRequest(_Body):
    name: str
    description: str
    templateName: str
    templateVersion: str
    templateParameters: Avps = []
    provisioningRequestReference: str | None = None


def _invalid(detail: str):
    return framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED, detail=detail)


def _exists(db: Session, kind: str, object_id: str) -> bool:
    return db.get(O2imsObject, (kind, object_id)) is not None


def _require(db: Session, kind: str, ids: str | list[str], what: str) -> None:
    missing = [i for i in ([ids] if isinstance(ids, str) else ids) if not _exists(db, kind, i)]
    if missing:
        raise _invalid(f"{what} not found: {missing}")


def _require_inventory(db: Session, ids: list[str], what: str) -> None:
    for i in ids:
        try:
            found = db.get(Resource, uuid.UUID(i)) is not None
        except ValueError:
            found = False
        if not found:
            raise _invalid(f"{what} {i!r} is not an inventory resource")


# kind -> (route, path used in objectInstance, id attribute, model, refs checker)
def _check_node_cluster(db: Session, b: NodeCluster) -> None:
    _require(db, "NodeClusterType", b.nodeClusterTypeId, "nodeClusterTypeId")
    _require(db, "ArtifactResource", b.artifactResourceId, "artifactResourceId")
    _require(db, "ClusterResource", b.clusterResourceIds, "clusterResourceIds")
    _require(db, "ClusterResourceGroup", b.clusterResourceGroups, "clusterResourceGroups")


def _check_cluster_resource(db: Session, b: ClusterResource) -> None:
    _require(db, "ClusterResourceType", b.clusterResourceTypeId, "clusterResourceTypeId")
    _require(db, "ClusterResourceGroup", b.memberOf, "memberOf")
    _require(db, "ArtifactResource", b.artifactResourceIds, "artifactResourceIds")
    _require_inventory(db, [b.resourceId], "resourceId")


def _check_group(db: Session, b: ClusterResourceGroup) -> None:
    _require(db, "ClusterResource", b.clusterResources, "clusterResources")


def _check_infra(db: Session, b: InfrastructureResource) -> None:
    _require(db, "InfrastructureResourceType", b.infrastructureResourceTypeId, "infrastructureResourceTypeId")
    _require(db, "ArtifactResource", b.artifactResourceId, "artifactResourceId")
    _require(db, "InfrastructureResource", b.linkedInfrastructureResourceIds, "linkedInfrastructureResourceIds")
    _require_inventory(db, b.inventoryResourceIds, "inventoryResourceIds")


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
# a kind that still refers to an object cannot be deleted: kind -> [(referrer kind, attribute holding the id or ids)]
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
    return {**ioc(kind, path, row.object_id), id_attr: row.object_id, **row.attributes}


def _referrers(db: Session, kind: str, object_id: str) -> list[str]:
    found = []
    for referrer_kind, attr in REFERRERS.get(kind, []):
        for row in db.scalars(select(O2imsObject).where(O2imsObject.kind == referrer_kind)).all():
            value = row.attributes.get(attr)
            if value == object_id or (isinstance(value, list) and object_id in value):
                found.append(f"{referrer_kind} {row.object_id}")
    return found


def _register(kind: str, path: str, id_attr: str, model: type[_Body], check) -> None:
    def create(body: model, db: Session = Depends(get_session)):  # type: ignore[valid-type]
        if check is not None:
            check(db, body)
        row = O2imsObject(kind=kind, object_id=str(uuid.uuid4()), attributes=body.model_dump(exclude_none=True, mode="json"))
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
    return datetime.datetime.now(datetime.UTC).isoformat()


def _request_view(row: O2imsObject) -> dict:
    return {**ioc("ProvisioningRequest", "provisioning-requests", row.object_id), "provisioningRequestId": row.object_id, **row.attributes}


def _param(request: ProvisioningRequest, key: str, default: str) -> str:
    return next((str(p.value) for p in request.templateParameters if p.key == key), default)


@router.post("/provisioning-requests", status_code=201)
def create_provisioning_request(body: ProvisioningRequest, db: Session = Depends(get_session)):
    """Resolve the template, create the NodeCluster, record the outcome."""
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
    page = paginate(db, select(O2imsObject).where(O2imsObject.kind == "ProvisioningRequest"), limit, offset)
    return {**page, "items": [_request_view(r) for r in page["items"]]}


@router.get("/provisioning-requests/{request_id}")
def get_provisioning_request(request_id: str, db: Session = Depends(get_session)):
    row = db.get(O2imsObject, ("ProvisioningRequest", request_id))
    if row is None:
        raise framework_error(FrameworkError.NRM_OBJECT_NOT_FOUND, detail=f"no such ProvisioningRequest {request_id}")
    return _request_view(row)


@router.delete("/provisioning-requests/{request_id}", status_code=204)
def delete_provisioning_request(request_id: str, db: Session = Depends(get_session)):
    """The request is deleted with the NodeCluster it created."""
    row = db.get(O2imsObject, ("ProvisioningRequest", request_id))
    if row is None:
        return
    cluster = db.get(O2imsObject, ("NodeCluster", (row.attributes.get("provisionedResourceSet") or {}).get("nodeClusterId", "")))
    if cluster is not None:
        db.delete(cluster)
    db.delete(row)
    db.commit()
