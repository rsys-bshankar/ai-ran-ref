"""MLMR (ML Model Repository) — TS 28.105 AI/ML NRM realization.

Wave 1 of the AI Platform Service Decomposition split this module out of
the former flat `ai-ml-workflow/` (see docs/ARCHITECTURE.md
and docs/ARCHITECTURE.md (MLMR)); Wave 2 finished the job — MLMR is
model truth (identity, versions, artifacts, coordination groups) and has
no lifecycle logic of its own, and as of this wave no longer stores any
either: `state`/`training_job_id`/`cleared_node_groups` moved to AIMgF's
own `model_lifecycle` table, and `PATCH /models/{id}/lifecycle` below is
gone along with them. AIMgF's `GET/PATCH /aimgf/models/{id}/lifecycle`
is the real thing now.
"""

import json
import uuid

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from smo_shared.health import database_check, install_health, sme_token_check
from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.openapi_security import apply_r1_gateway_security
from smo_shared.correlation import apply_correlation_id
from smo_shared.invoker import invoker_id
from smo_shared.pagination import PageLimit, PageOffset, paginate
from smo_shared.r1_client import R1Client

from . import mlr
from .models import MODEL_DOMAINS, MLModel, MLModelCoordinationGroup, MLModelProfile, MLModelRepository, ModelArtifact

_r1 = R1Client()

app = FastAPI(title="MLMR")
apply_r1_gateway_security(app)
apply_correlation_id(app)
app.include_router(mlr.router)


install_health(app, checks=[database_check, sme_token_check])  # /live, /ready and the /health alias (PR-ST-7)


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MLContext(_Spec):
    """TS 28.105 MLContext."""
    inferenceEntityRef: list[str] | None = None
    dataProviderRef: list[str] | None = None


class SupportedPerfIndicator(_Spec):
    performanceIndicatorName: str
    isSupportedForTraining: bool = False
    isSupportedForTesting: bool = False


class MLCapabilityInfo(_Spec):
    aIMLInferenceName: str | None = None
    capabilityName: str | None = None
    mLCapabilityParameters: dict | None = None


class TS28105ModelAttributes(BaseModel):
    """Wave 4 — the writable TS 28.105 MLModel attributes, accepted on
    register and update alongside this build's own fields."""
    aIMLInferenceName: str | None = None
    expectedRunTimeContext: MLContext | None = None
    trainingContext: MLContext | None = None
    runTimeContext: MLContext | None = None
    supportedPerformanceIndicators: list[SupportedPerfIndicator] | None = Field(default=None, min_length=1)
    mLCapabilitiesInfoList: list[MLCapabilityInfo] | None = Field(default=None, min_length=1)
    inferenceScope: list[str] | None = None
    retrainingEventsMonitorRef: str | None = None
    sourceTrainedMLModelRef: uuid.UUID | None = None
    mLModelRepositoryRef: uuid.UUID | None = None


def _dump(value):
    if value is None:
        return None
    if isinstance(value, list):
        return [v.model_dump(exclude_none=True) for v in value]
    return value.model_dump(exclude_none=True)


def _apply_ts28105(db: Session, model: MLModel, body: TS28105ModelAttributes) -> None:
    if body.mLModelRepositoryRef is not None and db.get(MLModelRepository, body.mLModelRepositoryRef) is None:
        raise framework_error(FrameworkError.NRM_OBJECT_NOT_FOUND, detail=f"no such MLModelRepository {body.mLModelRepositoryRef}")
    if body.sourceTrainedMLModelRef is not None and db.get(MLModel, body.sourceTrainedMLModelRef) is None:
        raise framework_error(FrameworkError.MODEL_NOT_FOUND, detail=f"no such sourceTrainedMLModelRef {body.sourceTrainedMLModelRef}")
    model.aiml_inference_name = body.aIMLInferenceName
    model.expected_run_time_context = _dump(body.expectedRunTimeContext)
    model.training_context = _dump(body.trainingContext)
    model.run_time_context = _dump(body.runTimeContext)
    model.supported_performance_indicators = _dump(body.supportedPerformanceIndicators)
    model.ml_capabilities_info_list = _dump(body.mLCapabilitiesInfoList)
    model.inference_scope = body.inferenceScope
    model.retraining_events_monitor_ref = body.retrainingEventsMonitorRef
    model.source_trained_ml_model_ref = body.sourceTrainedMLModelRef
    model.ml_model_repository_id = body.mLModelRepositoryRef


class RegisterModelRequest(TS28105ModelAttributes, mlr.SpecAttributes):
    modelType: str
    version: str
    requiredResourceTypeId: str | None = None
    description: str | None = None
    author: str | None = None
    owner: str | None = None
    inputDataType: str | None = None
    outputDataType: str | None = None
    targetEnvironments: list[dict] = []
    domain: str | None = None  # SPEECH_RECOGNITION | IMAGE_RECOGNITION | IMAGE_PROCESSING | LOCATION_PREDICTION | CUSTOM
    customDomain: str | None = None
    vendors: list[str] | None = None


class CreateCoordinationGroupRequest(BaseModel):
    memberModelIds: list[uuid.UUID]
    memberUseCases: list[str] = []
    sharedFeaturePipelineRef: str | None = None
    retrainPropagation: str = "ANY_MEMBER_TRIGGERS"
    mLModelRepositoryRef: uuid.UUID | None = None


class UpdateModelRequest(TS28105ModelAttributes, mlr.SpecAttributes):
    modelType: str
    version: str
    requiredResourceTypeId: str | None = None
    trainingDataLineage: dict | None = None
    integrityHash: str | None = None
    description: str | None = None
    author: str | None = None
    owner: str | None = None
    inputDataType: str | None = None
    outputDataType: str | None = None
    targetEnvironments: list[dict] | None = None
    domain: str | None = None
    customDomain: str | None = None
    vendors: list[str] | None = None


def _validate_domain(domain: str | None) -> None:
    if domain is not None and domain not in MODEL_DOMAINS:
        raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED, detail=f"unknown domain {domain!r}")


@app.post("/models", status_code=201)
def register_model(body: RegisterModelRequest, db: Session = Depends(get_session)):
    """HISTORY.md §5: the reference's own RegisterModel
    (mmes_apis.go) 409s on a (modelName, modelVersion) unique-constraint
    violation — this build accepted a duplicate (modelType, version)
    registration silently, creating a second, indistinguishable row.

    Also HISTORY.md §5: registration metadata was thin — no
    I/O data type schema, no author/owner, no TargetEnvironment
    declarations, all real fields on the reference's own
    ModelRelatedInformation/ModelInformation/Metadata (modelInfo.go).
    Required there; kept optional here, since this build's own
    RegisterModel was already permissive before this pass.

    domain/customDomain/vendors (Wave 3, HISTORY.md §7's MLMR section):
    TS29482_MLR_MLModelManagement.yaml's own MLModel schema.
    """
    _validate_domain(body.domain)
    model = MLModel(registration_id=str(uuid.uuid4()), model_type=body.modelType, version=body.version,
                     required_resource_type_id=body.requiredResourceTypeId,
                     description=body.description, author=body.author, owner=body.owner,
                     input_data_type=body.inputDataType, output_data_type=body.outputDataType,
                     target_environments=body.targetEnvironments,
                     domain=body.domain, custom_domain=body.customDomain, vendors=body.vendors)
    _apply_ts28105(db, model, body)
    mlr.apply_spec_attributes(model, body)
    db.add(model)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise framework_error(FrameworkError.MODEL_ALREADY_REGISTERED, detail=f"model type {body.modelType} version {body.version} already registered")
    return {"modelId": str(model.model_id)}


@app.get("/models")
def discover_models(request: Request, model_type: str | None = None,
                    filt_criteria: str | None = Query(default=None, alias="filt-criteria"),
                    supported_features: str | None = Query(default=None, alias="supported-features"),
                    include_models: bool = Query(default=False, alias="include-models"),
                    limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    """Without `filt-criteria`: MLMR's own paginated list (filter `model_type`).
    With it: TS 29.482 ModelInformationDiscovery (SA-MLMR-9): the whole-object
    MLModel criteria are matched (see `mlr.py`) and a `DiscoveryResp` returns
    (`profiles`, or with `include-models=true`, an extension of ours, the model
    files in `mlModels`); 404 when nothing matches. `supported-features` is
    accepted and ignored (no optional feature is negotiated).
    """
    if filt_criteria is not None:
        try:
            criteria = mlr.MLModelInfo.model_validate(json.loads(filt_criteria))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED, detail=f"filt-criteria is not an MLModel: {exc}") from exc
        return mlr.discover(db, criteria, invoker_id(request), include_models, limit, offset)
    stmt = select(MLModel)
    if model_type:
        stmt = stmt.where(MLModel.model_type == model_type)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [_model_view(m) for m in page["items"]]}


@app.get("/models/{model_id}")
def get_model(model_id: uuid.UUID, db: Session = Depends(get_session)):
    """HISTORY.md §5: model CRUD was incomplete — only create
    and a type-filtered list existed, no GET-by-id at all. Also the read
    side AIMgF/MLLF call to learn a model's current state before deciding
    whether a transition/deploy is legal.
    """
    model = db.get(MLModel, model_id)
    if model is None:
        raise framework_error(FrameworkError.MODEL_NOT_FOUND, detail="no such model")
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
    itself. `artifactLocation` stays out of this body on purpose — that's
    this module's own artifact-upload route's job. Lifecycle/runtime
    state and node-group targeting are AIMgF's own `model_lifecycle` row
    entirely (Wave 2) — not this module's concern at all any more.
    """
    model = db.get(MLModel, model_id)
    if model is None:
        raise framework_error(FrameworkError.MODEL_NOT_FOUND, detail="no such model")
    if model.model_type != body.modelType or model.version != body.version:
        raise framework_error(
            FrameworkError.MODEL_IDENTITY_IMMUTABLE,
            detail=f"model {model_id} has modelType={model.model_type!r} version={model.version!r}, not the provided modelType/version",
        )
    _validate_domain(body.domain)
    model.required_resource_type_id = body.requiredResourceTypeId
    model.training_data_lineage = body.trainingDataLineage
    model.integrity_hash = body.integrityHash
    model.description, model.author, model.owner = body.description, body.author, body.owner
    model.input_data_type, model.output_data_type = body.inputDataType, body.outputDataType
    model.target_environments = body.targetEnvironments
    model.domain, model.custom_domain, model.vendors = body.domain, body.customDomain, body.vendors
    _apply_ts28105(db, model, body)
    mlr.apply_spec_attributes(model, body)  # an omitted spec attribute is kept (AIMgF writes phaseInfo)
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
    per HISTORY.md §5) already does this at the database level,
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
    db.query(MLModelProfile).filter(MLModelProfile.model_id == model_id).delete()
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
        raise framework_error(FrameworkError.MODEL_NOT_FOUND, detail="no such model")
    if not file.filename or not file.filename.endswith(".zip"):
        raise framework_error(FrameworkError.ARTIFACT_FORMAT_INVALID, detail="artifact must be a .zip file")

    content = file.file.read()
    next_version = (db.scalar(
        select(func.max(ModelArtifact.artifact_version)).where(ModelArtifact.model_id == model_id)
    ) or 0) + 1
    artifact = ModelArtifact(model_id=model_id, artifact_version=next_version, filename=file.filename, content=content,
                              size_bytes=len(content))
    db.add(artifact)
    model.artifact_location = f"model-artifact:{model_id}:{next_version}"
    db.commit()
    return {"modelId": str(model_id), "artifactId": str(artifact.artifact_id), "artifactVersion": next_version,
            "sizeBytes": artifact.size_bytes}


@app.get("/models/{model_id}/artifact/{artifact_version}")
def download_model_artifact(request: Request, model_id: uuid.UUID, artifact_version: int, db: Session = Depends(get_session)):
    """DownloadModel — same modelId+artifactVersion lookup as the reference's
    modelName+modelVersion+artifactVersion modelKey. A model's `storeDiscReqs`
    are enforced here (SA-MLMR-6): 410 `MODEL_EXPIRED` past its `duration`,
    403 `MODEL_ACCESS_DENIED` for a caller its `accessReqs` exclude.
    """
    model = db.get(MLModel, model_id)
    if model is not None:
        mlr.require_usable(model, invoker_id(request))
    artifact = db.scalar(
        select(ModelArtifact).where(ModelArtifact.model_id == model_id, ModelArtifact.artifact_version == artifact_version)
    )
    if artifact is None:
        raise framework_error(FrameworkError.ARTIFACT_VERSION_NOT_FOUND, detail="no such artifact version")
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

    if body.mLModelRepositoryRef is not None and db.get(MLModelRepository, body.mLModelRepositoryRef) is None:
        raise framework_error(FrameworkError.NRM_OBJECT_NOT_FOUND, detail=f"no such MLModelRepository {body.mLModelRepositoryRef}")
    group = MLModelCoordinationGroup(member_model_ids=body.memberModelIds, member_use_cases=body.memberUseCases,
                                      shared_feature_pipeline_ref=body.sharedFeaturePipelineRef,
                                      retrain_propagation=body.retrainPropagation,
                                      ml_model_repository_id=body.mLModelRepositoryRef)
    db.add(group)
    db.commit()
    return {"groupId": str(group.group_id)}


@app.get("/coordination-groups")
def list_coordination_groups(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    """Read side of create_coordination_group. Also AIMgF's own read path
    for group-retrain propagation (report_performance) — it has no direct
    ORM access to MLModelCoordinationGroup anymore, so it lists groups
    through this route and filters in Python, exactly as it did in-process
    before the split.
    """
    page = paginate(db, select(MLModelCoordinationGroup), limit, offset)
    return {**page, "items": [{"groupId": str(g.group_id), "groupType": g.group_type,
             "memberModelIds": [str(m) for m in g.member_model_ids], "memberUseCases": g.member_use_cases or [],
             "sharedFeaturePipelineRef": g.shared_feature_pipeline_ref, "retrainPropagation": g.retrain_propagation}
            for g in page["items"]]}


def _model_view(m: MLModel) -> dict:
    return {"modelId": str(m.model_id), "modelType": m.model_type, "version": m.version,
            "artifactLocation": m.artifact_location,
            "description": m.description, "author": m.author, "owner": m.owner,
            "inputDataType": m.input_data_type, "outputDataType": m.output_data_type,
            "targetEnvironments": m.target_environments or [],
            "domain": m.domain, "customDomain": m.custom_domain, "vendors": m.vendors or [],
            **mlr.spec_attributes_view(m), **_ts28105_writable_view(m)}


def _ts28105_writable_view(m: MLModel) -> dict:
    return {"aIMLInferenceName": m.aiml_inference_name, "expectedRunTimeContext": m.expected_run_time_context,
            "trainingContext": m.training_context, "runTimeContext": m.run_time_context,
            "supportedPerformanceIndicators": m.supported_performance_indicators or [],
            "mLCapabilitiesInfoList": m.ml_capabilities_info_list or [], "inferenceScope": m.inference_scope or [],
            "retrainingEventsMonitorRef": m.retraining_events_monitor_ref,
            "sourceTrainedMLModelRef": str(m.source_trained_ml_model_ref) if m.source_trained_ml_model_ref else None,
            "mLModelRepositoryRef": str(m.ml_model_repository_id) if m.ml_model_repository_id else None}


# ---------------------------------------------------------------- Wave 4: TS 28.105 NRM views

def _aimgf_refs(model_id: uuid.UUID) -> dict:
    """The read-only MLModel attributes whose truth is AIMgF's. Best-effort:
    an unreachable AIMgF yields empty values rather than failing the read
    of MLMR's own model truth."""
    try:
        resp = _r1.get(f"/aimgf/ml-models/{model_id}/nrm-refs")
        if resp.status_code == 200:
            return resp.json()
    except Exception:  # noqa: BLE001 — any transport failure degrades to "unknown"
        pass
    return {"mLTrainingType": None, "aIMLInferenceReportRefList": [], "usedByFunctionRefList": []}


@app.get("/ml-models/{model_id}")
def get_ml_model_nrm(model_id: uuid.UUID, db: Session = Depends(get_session)):
    """TS 28.105 MLModel IOC view: the spec's own attribute names over
    MLMR's model row, plus AIMgF's read-only cross-references."""
    m = db.get(MLModel, model_id)
    if m is None:
        raise framework_error(FrameworkError.MODEL_NOT_FOUND, detail="no such model")
    refs = _aimgf_refs(model_id)
    attrs = {"mLModelId": str(m.model_id), "mLModelVersion": m.version, **_ts28105_writable_view(m),
             "mLTrainingType": refs.get("mLTrainingType"),
             "aIMLInferenceReportRefList": refs.get("aIMLInferenceReportRefList", []),
             "usedByFunctionRefList": refs.get("usedByFunctionRefList", [])}
    return {"id": str(m.model_id), "attributes": attrs}


def _group_nrm_view(g: MLModelCoordinationGroup) -> dict:
    return {"id": str(g.group_id), "attributes": {
        "memberMLModelRefList": [str(x) for x in g.member_model_ids],
        "mLModelRepositoryRef": str(g.ml_model_repository_id) if g.ml_model_repository_id else None}}


@app.get("/ml-model-coordination-groups/{group_id}")
def get_ml_model_coordination_group_nrm(group_id: uuid.UUID, db: Session = Depends(get_session)):
    """TS 28.105 MLModelCoordinationGroup IOC view (memberMLModelRefList,
    minItems 2 — enforced at creation)."""
    g = db.get(MLModelCoordinationGroup, group_id)
    if g is None:
        raise framework_error(FrameworkError.NRM_OBJECT_NOT_FOUND, detail=f"no such MLModelCoordinationGroup {group_id}")
    return _group_nrm_view(g)


class MLModelRepositoryBody(_Spec):
    userLabel: str | None = None


def _repository_view(db: Session, r: MLModelRepository) -> dict:
    model_ids = db.scalars(select(MLModel.model_id).where(MLModel.ml_model_repository_id == r.ml_model_repository_id)).all()
    group_ids = db.scalars(select(MLModelCoordinationGroup.group_id).where(
        MLModelCoordinationGroup.ml_model_repository_id == r.ml_model_repository_id)).all()
    return {"id": str(r.ml_model_repository_id), "attributes": {"userLabel": r.user_label},
            "MLModel": [str(m) for m in model_ids], "MLModelCoordinationGroup": [str(g) for g in group_ids]}


@app.post("/ml-model-repositories", status_code=201)
def create_ml_model_repository(body: MLModelRepositoryBody, db: Session = Depends(get_session)):
    r = MLModelRepository(user_label=body.userLabel)
    db.add(r)
    db.commit()
    return _repository_view(db, r)


@app.get("/ml-model-repositories")
def list_ml_model_repositories(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    page = paginate(db, select(MLModelRepository), limit, offset)
    return {**page, "items": [_repository_view(db, r) for r in page["items"]]}


@app.get("/ml-model-repositories/{repository_id}")
def get_ml_model_repository(repository_id: uuid.UUID, db: Session = Depends(get_session)):
    r = db.get(MLModelRepository, repository_id)
    if r is None:
        raise framework_error(FrameworkError.NRM_OBJECT_NOT_FOUND, detail=f"no such MLModelRepository {repository_id}")
    return _repository_view(db, r)


@app.delete("/ml-model-repositories/{repository_id}", status_code=204)
def delete_ml_model_repository(repository_id: uuid.UUID, db: Session = Depends(get_session)):
    """Contained models/groups are not deleted — they become uncontained
    (ON DELETE SET NULL), since MLMR's model truth outlives a container."""
    r = db.get(MLModelRepository, repository_id)
    if r is None:
        return
    for m in db.scalars(select(MLModel).where(MLModel.ml_model_repository_id == repository_id)).all():
        m.ml_model_repository_id = None
    for g in db.scalars(select(MLModelCoordinationGroup).where(MLModelCoordinationGroup.ml_model_repository_id == repository_id)).all():
        g.ml_model_repository_id = None
    db.delete(r)
    db.commit()
