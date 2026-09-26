"""MLMR (ML Model Repository) — TS 28.105 AI/ML NRM realization.

Wave 1 of the AI Platform Service Decomposition: split out of the former
flat `ai-ml-workflow/` module (see docs/architecture/AI_PLATFORM_BASELINE.md
and docs/ownership/MLMR_OWNERSHIP.md). MLMR is model truth — identity,
versions, artifacts, coordination groups — and has no lifecycle logic of
its own: it never fires a state transition. `state`/`training_job_id`/
`cleared_node_groups` still live on its own `MLModel` row this wave (the
full aggregate split is Wave 2's job), but only AIMgF and MLLF ever write
them, through `PATCH /models/{id}/lifecycle` below — MLMR itself only
ever writes them via that one endpoint, never by deciding a transition.
"""

import uuid

from fastapi import Depends, FastAPI, File, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error

from .models import MLModel, MLModelCoordinationGroup, ModelArtifact

app = FastAPI(title="MLMR")


@app.get("/health")
def health_check():
    """Liveness probe. The GUI BFF's GET /modules/status fans out to
    /<module>/health through R1 Termination for every module in parallel.
    """
    return {"status": "healthy"}


class RegisterModelRequest(BaseModel):
    modelType: str
    version: str
    requiredResourceTypeId: str | None = None
    description: str | None = None
    author: str | None = None
    owner: str | None = None
    inputDataType: str | None = None
    outputDataType: str | None = None
    targetEnvironments: list[dict] = []


class CreateCoordinationGroupRequest(BaseModel):
    memberModelIds: list[uuid.UUID]
    memberUseCases: list[str] = []
    sharedFeaturePipelineRef: str | None = None
    retrainPropagation: str = "ANY_MEMBER_TRIGGERS"


class UpdateModelRequest(BaseModel):
    modelType: str
    version: str
    requiredResourceTypeId: str | None = None
    trainingDataLineage: dict | None = None
    integrityHash: str | None = None
    clearedNodeGroups: list[str] | None = None
    description: str | None = None
    author: str | None = None
    owner: str | None = None
    inputDataType: str | None = None
    outputDataType: str | None = None
    targetEnvironments: list[dict] | None = None


class UpdateLifecycleRequest(BaseModel):
    """Internal, cross-service-only surface — called by AIMgF after it
    fires a lifecycle transition and by MLLF after a deploy request, never
    by a GUI or rApp directly (not listed in gui-bff/app/rbac.py's own
    module rules for exactly that reason). Every field is optional: each
    caller only ever supplies the field(s) it owns the decision for.
    """
    state: str | None = None
    trainingJobId: uuid.UUID | None = None
    clearedNodeGroups: list[str] | None = None


@app.post("/models", status_code=201)
def register_model(body: RegisterModelRequest, db: Session = Depends(get_session)):
    """OPEN_ITEMS.md section 5: the reference's own RegisterModel
    (mmes_apis.go) 409s on a (modelName, modelVersion) unique-constraint
    violation — this build accepted a duplicate (modelType, version)
    registration silently, creating a second, indistinguishable row.

    Also OPEN_ITEMS.md section 5: registration metadata was thin — no
    I/O data type schema, no author/owner, no TargetEnvironment
    declarations, all real fields on the reference's own
    ModelRelatedInformation/ModelInformation/Metadata (modelInfo.go).
    Required there; kept optional here, since this build's own
    RegisterModel was already permissive before this pass.
    """
    model = MLModel(registration_id=str(uuid.uuid4()), model_type=body.modelType, version=body.version,
                     required_resource_type_id=body.requiredResourceTypeId, state="REGISTERED",
                     description=body.description, author=body.author, owner=body.owner,
                     input_data_type=body.inputDataType, output_data_type=body.outputDataType,
                     target_environments=body.targetEnvironments)
    db.add(model)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise framework_error(FrameworkError.MODEL_ALREADY_REGISTERED, detail=f"model type {body.modelType} version {body.version} already registered")
    return {"modelId": str(model.model_id), "state": model.state}


@app.get("/models")
def discover_models(model_type: str | None = None, db: Session = Depends(get_session)):
    stmt = select(MLModel)
    if model_type:
        stmt = stmt.where(MLModel.model_type == model_type)
    return [_model_view(m) for m in db.scalars(stmt).all()]


@app.get("/models/{model_id}")
def get_model(model_id: uuid.UUID, db: Session = Depends(get_session)):
    """OPEN_ITEMS.md section 5: model CRUD was incomplete — only create
    and a type-filtered list existed, no GET-by-id at all. Also the read
    side AIMgF/MLLF call to learn a model's current state before deciding
    whether a transition/deploy is legal.
    """
    model = db.get(MLModel, model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="no such model")
    return _model_view(model)


@app.put("/models/{model_id}")
def update_model(model_id: uuid.UUID, body: UpdateModelRequest, db: Session = Depends(get_session)):
    """UpdateModel (mmes_apis.go) — 404s on an unknown id, same as
    GetModelInfoById there. The reference also rejects a modelName/
    modelVersion mismatch between the path's existing record and the
    body with a 400 ("model with id ... has different modelName and
    modelVersion than provided") rather than silently renaming it —
    (model_type, version) is this build's own identity for the same
    reference ModelID composite key `register_model`'s uniqueness
    constraint already treats as immutable identity, so this endpoint
    only ever updates the metadata fields around it, never the identity
    itself. `state`/`trainingJobId`/`artifactLocation` stay out of this
    body on purpose — those are owned by AIMgF/MLLF's own
    `PATCH .../lifecycle` and this module's own artifact-upload route,
    not a generic PUT.
    """
    model = db.get(MLModel, model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="no such model")
    if model.model_type != body.modelType or model.version != body.version:
        raise framework_error(
            FrameworkError.MODEL_IDENTITY_IMMUTABLE,
            detail=f"model {model_id} has modelType={model.model_type!r} version={model.version!r}, not the provided modelType/version",
        )
    model.required_resource_type_id = body.requiredResourceTypeId
    model.training_data_lineage = body.trainingDataLineage
    model.integrity_hash = body.integrityHash
    model.cleared_node_groups = body.clearedNodeGroups
    model.description, model.author, model.owner = body.description, body.author, body.owner
    model.input_data_type, model.output_data_type = body.inputDataType, body.outputDataType
    model.target_environments = body.targetEnvironments
    db.commit()
    return _model_view(model)


@app.patch("/models/{model_id}/lifecycle")
def update_model_lifecycle(model_id: uuid.UUID, body: UpdateLifecycleRequest, db: Session = Depends(get_session)):
    """Wave 1's minimal necessary cross-service plumbing: AIMgF owns the
    decision of what `state`/`trainingJobId` a model moves to (its own
    FSM, per docs/ownership/AIMGF_OWNERSHIP.md) and MLLF owns
    `clearedNodeGroups` (docs/ownership/MLLF_OWNERSHIP.md) — but only MLMR
    can write its own row. Not a generic PATCH: only these three fields,
    matching exactly what used to be direct ORM writes from the same
    process before this split, no new business logic.
    """
    model = db.get(MLModel, model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="no such model")
    if body.state is not None:
        model.state = body.state
    if body.trainingJobId is not None:
        model.training_job_id = body.trainingJobId
    if body.clearedNodeGroups is not None:
        model.cleared_node_groups = body.clearedNodeGroups
    db.commit()
    return _model_view(model)


@app.delete("/models/{model_id}", status_code=204)
def deregister_model(model_id: uuid.UUID, db: Session = Depends(get_session)):
    """DeleteModel (mmes_apis.go). Only cleans up MLMR's own dependent
    table (ModelArtifact) directly — before Wave 1's split, this route
    also explicitly cleaned up TrainingJob/InferenceJob/
    ModelChangeSubscription/MLMFSubscription/PerformanceReport, all of
    which now live in AIMgF's own process and are no longer importable
    here. Those rows are still cleaned up: `migrations/001_init.sql`'s
    own `ON DELETE CASCADE` on every one of those FKs (added as
    defense-in-depth alongside the original application-level cleanup,
    per OPEN_ITEMS.md section 5) already does this at the database level,
    which is authoritative here since every module shares one physical
    Postgres instance in this build's topology. That cascade is real
    against Postgres but NOT exercised by this module's own SQLite-backed
    unit tests (SQLite doesn't enforce FK constraints by default, and this
    module's own isolated test schema doesn't even declare AIMgF's tables)
    — verified separately against a live Postgres instance instead,
    the same honest carve-out this codebase already uses elsewhere for a
    SQLite-only enforcement gap (e.g. the coordination group's own
    `member_model_ids` array-length CHECK constraint). Idempotent:
    deleting an unknown id is a silent no-op, matching this module's
    other DELETE routes.
    """
    model = db.get(MLModel, model_id)
    if model is None:
        return
    db.query(ModelArtifact).filter(ModelArtifact.model_id == model_id).delete()
    db.delete(model)
    db.commit()


@app.post("/models/{model_id}/artifact", status_code=201)
def upload_model_artifact(model_id: uuid.UUID, file: UploadFile = File(...), db: Session = Depends(get_session)):
    """UploadModel (aiml-fw-awmf-modelmgmtservice apis/mmes_apis.go) — the
    reference looks the model up first (404 if unregistered), validates a
    .zip suffix (415 otherwise), then stamps a fresh artifactVersion,
    separate from modelVersion, auto-incremented per model. Real S3 storage
    is elided (see ModelArtifact's docstring); the bytes are stored for real
    here instead of being discarded, so upload+download round-trip.
    """
    model = db.get(MLModel, model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="no such model")
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(status_code=415, detail="artifact must be a .zip file")

    content = file.file.read()
    next_version = (db.scalar(
        select(func.max(ModelArtifact.artifact_version)).where(ModelArtifact.model_id == model_id)
    ) or 0) + 1
    artifact = ModelArtifact(model_id=model_id, artifact_version=next_version, filename=file.filename, content=content)
    db.add(artifact)
    model.artifact_location = f"model-artifact:{model_id}:{next_version}"
    db.commit()
    return {"modelId": str(model_id), "artifactId": str(artifact.artifact_id), "artifactVersion": next_version}


@app.get("/models/{model_id}/artifact/{artifact_version}")
def download_model_artifact(model_id: uuid.UUID, artifact_version: int, db: Session = Depends(get_session)):
    """DownloadModel — same modelId+artifactVersion lookup as the reference's
    modelName+modelVersion+artifactVersion modelKey.
    """
    artifact = db.scalar(
        select(ModelArtifact).where(ModelArtifact.model_id == model_id, ModelArtifact.artifact_version == artifact_version)
    )
    if artifact is None:
        raise HTTPException(status_code=404, detail="no such artifact version")
    return Response(
        content=artifact.content, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{artifact.filename}"'},
    )


@app.post("/coordination-groups", status_code=201)
def create_coordination_group(body: CreateCoordinationGroupRequest, db: Session = Depends(get_session)):
    """AI/ML Workflow LLD section 4.5's call flow, step 1: register the
    group as a sibling of individual model registrations.

    A coordination group of fewer than two members isn't a coordination
    of anything — the migration's own `member_model_ids` CHECK constraint
    (array_length >= 2) already enforces this at the DB layer, but with
    no pre-validation here it surfaced as an unhandled IntegrityError
    (bare 500) rather than a clean error. Found running this route
    against real Postgres — SQLite's schema never enforces it, so no
    unit test had ever caught it either.
    """
    if len(body.memberModelIds) < 2:
        raise framework_error(FrameworkError.COORDINATION_GROUP_TOO_SMALL, detail="a coordination group needs at least 2 memberModelIds")

    group = MLModelCoordinationGroup(member_model_ids=body.memberModelIds, member_use_cases=body.memberUseCases,
                                      shared_feature_pipeline_ref=body.sharedFeaturePipelineRef,
                                      retrain_propagation=body.retrainPropagation)
    db.add(group)
    db.commit()
    return {"groupId": str(group.group_id)}


@app.get("/coordination-groups")
def list_coordination_groups(db: Session = Depends(get_session)):
    """Read side of create_coordination_group. Also AIMgF's own read path
    for group-retrain propagation (report_performance) — it has no direct
    ORM access to MLModelCoordinationGroup anymore, so it lists groups
    through this route and filters in Python, exactly as it did in-process
    before the split.
    """
    return [{"groupId": str(g.group_id), "groupType": g.group_type,
             "memberModelIds": [str(m) for m in g.member_model_ids], "memberUseCases": g.member_use_cases or [],
             "sharedFeaturePipelineRef": g.shared_feature_pipeline_ref, "retrainPropagation": g.retrain_propagation}
            for g in db.scalars(select(MLModelCoordinationGroup)).all()]


def _model_view(m: MLModel) -> dict:
    return {"modelId": str(m.model_id), "modelType": m.model_type, "version": m.version, "state": m.state,
            "trainingJobId": str(m.training_job_id) if m.training_job_id else None,
            "clearedNodeGroups": m.cleared_node_groups or [], "artifactLocation": m.artifact_location,
            "description": m.description, "author": m.author, "owner": m.owner,
            "inputDataType": m.input_data_type, "outputDataType": m.output_data_type,
            "targetEnvironments": m.target_environments or []}
