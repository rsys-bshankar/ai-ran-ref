"""AI/ML Workflow SMOS (AIMgF/MLTF/MLVF/MLEF/MLMR/MLLF, +MLMF new).

SMO Design v1.3 section 3.8, extended by AI/ML Workflow LLD sections 1-8:
MLMF closes the missing monitoring surface, MLEF now hosts inference
serving, MLModelCoordinationGroup's Shape A is fully built, and
clearedNodeGroups resolves MultiNode Q2's deployment-targeting gap.
"""

import re
import uuid

from fastapi import Depends, FastAPI, File, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error

from .models import AIMLModel, FeatureGroup, InferenceJob, MLMFSubscription, MLModelCoordinationGroup, ModelArtifact, ModelChangeSubscription, PerformanceReport, TrainingJob
from .statemachine import AIML_MODEL_FSM, INFERENCE_JOB_FSM, InferenceEvent, InferenceState, ModelEvent, ModelState, should_trigger_group_retrain

app = FastAPI(title="AI/ML Workflow SMOS")


@app.get("/health")
def health_check():
    """Liveness probe. The GUI BFF's GET /modules/status fans out to
    /<module>/health through R1 Termination for every module in parallel,
    so every module answers one — previously only ran-nf-oam/a1-related
    did (as their own DME producer-health callback URL).
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


class RequestTrainingRequest(BaseModel):
    modelId: uuid.UUID | None = None
    modelCoordinationGroupId: uuid.UUID | None = None
    producerId: str
    requiredData: dict = {}
    validationCriteria: dict = {}
    notificationUri: str | None = None
    runId: str | None = None
    trainingDataset: str | None = None
    validationDataset: str | None = None
    consumerRappId: str | None = None
    producerRappId: str | None = None


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


class CreateFeatureGroupRequest(BaseModel):
    featureGroupName: str
    featureList: str
    datalakeSource: str
    host: str
    port: str
    bucket: str
    token: str
    dbOrg: str
    measurement: str
    enableDme: bool = False
    measuredObjClass: str | None = None
    dmePort: str | None = None
    sourceName: str | None = None


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
    model = AIMLModel(registration_id=str(uuid.uuid4()), model_type=body.modelType, version=body.version,
                       required_resource_type_id=body.requiredResourceTypeId, state=ModelState.REGISTERED,
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
    stmt = select(AIMLModel)
    if model_type:
        stmt = stmt.where(AIMLModel.model_type == model_type)
    return [_model_view(m) for m in db.scalars(stmt).all()]


@app.get("/models/{model_id}")
def get_model(model_id: uuid.UUID, db: Session = Depends(get_session)):
    """OPEN_ITEMS.md section 5: model CRUD was incomplete — only create
    and a type-filtered list existed, no GET-by-id at all.
    """
    model = db.get(AIMLModel, model_id)
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
    body on purpose — those are owned by the dedicated advance/training/
    artifact-upload endpoints, not a generic PUT.
    """
    model = db.get(AIMLModel, model_id)
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


@app.delete("/models/{model_id}", status_code=204)
def deregister_model(model_id: uuid.UUID, db: Session = Depends(get_session)):
    """DeleteModel (mmes_apis.go) — the reference's own repo.Delete
    explicitly cleans up its one dependent child table
    (TargetEnvironment) before deleting the parent row, in a
    transaction, rather than relying on the DB to cascade it. This
    build's schema has five FKs to aiml_model (model_artifact,
    training_job, model_change_subscription, mlmf_subscription,
    inference_job), none of which had any cascade behavior at all —
    the exact same unchecked-FK shape already found and fixed for
    DME's deregister_producer (OPEN_ITEMS.md section 5): deleting a
    model with any dependent row would either orphan it (SQLite, no FK
    enforcement) or crash with an unhandled IntegrityError (real
    Postgres). Cleaned up explicitly here, same as there, with the
    DB-level ON DELETE CASCADE added alongside this as a defense-in-
    depth backstop, not the only line of defense. Idempotent: deleting
    an unknown id is a silent no-op, matching this module's other
    DELETE routes (cancel_training).
    """
    model = db.get(AIMLModel, model_id)
    if model is None:
        return
    db.query(PerformanceReport).filter(PerformanceReport.subscription_id.in_(
        select(MLMFSubscription.subscription_id).where(MLMFSubscription.model_id == model_id)
    )).delete(synchronize_session=False)
    db.query(MLMFSubscription).filter(MLMFSubscription.model_id == model_id).delete()
    db.query(ModelChangeSubscription).filter(ModelChangeSubscription.model_id == model_id).delete()
    db.query(InferenceJob).filter(InferenceJob.model_id == model_id).delete()
    db.query(TrainingJob).filter(TrainingJob.model_id == model_id).delete()
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
    model = db.get(AIMLModel, model_id)
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
    """
    group = MLModelCoordinationGroup(member_model_ids=body.memberModelIds, member_use_cases=body.memberUseCases,
                                      shared_feature_pipeline_ref=body.sharedFeaturePipelineRef,
                                      retrain_propagation=body.retrainPropagation)
    db.add(group)
    db.commit()
    return {"groupId": str(group.group_id)}


@app.post("/training-jobs", status_code=201)
def request_training(body: RequestTrainingRequest, db: Session = Depends(get_session)):
    """RequestTraining — exactly one of modelId/modelCoordinationGroupId,
    enforced at the DB layer (exactly_one_target constraint) and checked
    here for a clean error.

    modelId-targeted requests also drive the model's own FSM:
    REGISTERED -> TRAINING (TRAIN, the very first cycle) or
    ACTIVE -> TRAINING (RETRAIN, an ordinary retrain). This used to
    always fire TRAIN regardless of the model's actual state, which
    crashed with an unhandled IllegalTransition on every retrain attempt
    (TRAIN is only legal from REGISTERED). A model already TRAINING (an
    unresolved prior job) is treated as the operator's explicit decision
    to supersede it: the orphaned job is marked CANCELLED rather than
    left silently RUNNING and unreachable, which is what the old
    unconditional `model.training_job_id = job.training_job_id`
    assignment did.
    """
    if (body.modelId is None) == (body.modelCoordinationGroupId is None):
        raise framework_error(FrameworkError.COORDINATION_GROUP_MISMATCH)

    model = db.get(AIMLModel, body.modelId) if body.modelId else None
    if model is not None and model.state not in (ModelState.REGISTERED, ModelState.ACTIVE, ModelState.TRAINING):
        raise framework_error(FrameworkError.MODEL_NOT_CERTIFIED, detail=f"cannot (re)train a model in state {model.state}")

    job = TrainingJob(model_id=body.modelId, model_coordination_group_id=body.modelCoordinationGroupId,
                       producer_id=body.producerId, required_data=body.requiredData,
                       validation_criteria=body.validationCriteria, notification_uri=body.notificationUri,
                       status="RUNNING", run_id=body.runId, training_dataset=body.trainingDataset,
                       validation_dataset=body.validationDataset, consumer_rapp_id=body.consumerRappId,
                       producer_rapp_id=body.producerRappId)
    db.add(job)
    db.flush()

    if model is not None:
        if model.state == ModelState.TRAINING:
            if model.training_job_id is not None:
                orphaned = db.get(TrainingJob, model.training_job_id)
                if orphaned is not None and orphaned.status == "RUNNING":
                    orphaned.status = "CANCELLED"
        else:
            event = ModelEvent.RETRAIN if model.state == ModelState.ACTIVE else ModelEvent.TRAIN
            model.state = AIML_MODEL_FSM.fire(ModelState(model.state), event)
        model.training_job_id = job.training_job_id

    db.commit()
    return {"trainingJobId": str(job.training_job_id)}


@app.get("/training-jobs/{training_job_id}/status")
def query_training_job_status(training_job_id: uuid.UUID, db: Session = Depends(get_session)):
    job = db.get(TrainingJob, training_job_id)
    return {
        "trainingJobId": str(job.training_job_id), "status": job.status, "runId": job.run_id,
        "trainingDataset": job.training_dataset, "validationDataset": job.validation_dataset,
        "consumerRappId": job.consumer_rapp_id, "producerRappId": job.producer_rapp_id,
    }


@app.delete("/training-jobs/{training_job_id}", status_code=204)
def cancel_training(training_job_id: uuid.UUID, db: Session = Depends(get_session)):
    job = db.get(TrainingJob, training_job_id)
    if job is not None:
        job.status = "CANCELLED"
        db.commit()


@app.post("/training-jobs/{training_job_id}/model-metrics")
def update_training_job_model_metrics(training_job_id: uuid.UUID, model_metrics: dict, db: Session = Depends(get_session)):
    """OPEN_ITEMS.md section 5: TrainingJob had no metrics-writeback
    endpoint at all. The reference's own
    POST /training-jobs/update-model-metrics/<id> (trainingjob_controller.py)
    replaces model_metrics wholesale, not a merge — same here.
    """
    job = db.get(TrainingJob, training_job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such training job")
    job.model_metrics = model_metrics
    db.commit()
    return {"trainingJobId": str(job.training_job_id), "modelMetrics": job.model_metrics}


@app.get("/training-jobs/{training_job_id}/model-metrics")
def get_training_job_model_metrics(training_job_id: uuid.UUID, db: Session = Depends(get_session)):
    job = db.get(TrainingJob, training_job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such training job")
    return job.model_metrics or {}


@app.post("/models/{model_id}/advance")
def advance_model_lifecycle(model_id: uuid.UUID, event: str, db: Session = Depends(get_session)):
    """Single endpoint driving TRAINING_COMPLETE -> VALIDATION_COMPLETE ->
    CERTIFY -> LOAD -> ACTIVATE -> RETRAIN -> DEPRECATE, each a real FSM
    transition (statemachine.py). One endpoint rather than six nearly
    identical ones, since the pattern is mechanically the same at every step.
    """
    model = db.get(AIMLModel, model_id)
    model.state = AIML_MODEL_FSM.fire(ModelState(model.state), ModelEvent(event))
    db.commit()
    return _model_view(model)


@app.post("/models/{model_id}/deploy")
def request_model_deployment(model_id: uuid.UUID, node_groups: list[str], db: Session = Depends(get_session)):
    """RequestModelDeployment — triggers MLLF. Requires CERTIFIED-or-later
    state (the AIMgF gate, unchanged from v1.3) and now stamps
    clearedNodeGroups (LLD section 5, closing MultiNode Q2's targeting gap).
    """
    model = db.get(AIMLModel, model_id)
    if model.state not in (ModelState.CERTIFIED, ModelState.LOADED, ModelState.ACTIVE):
        raise framework_error(FrameworkError.MODEL_NOT_CERTIFIED)
    model.cleared_node_groups = node_groups
    db.commit()
    return {"modelId": str(model.model_id), "clearedNodeGroups": model.cleared_node_groups}


@app.post("/models/{model_id}/inference-jobs", status_code=201)
def request_inference(model_id: uuid.UUID, notification_destination: str | None = None, db: Session = Depends(get_session)):
    """RequestInference — new, MLEF-hosted (AI/ML Workflow LLD section 3)."""
    model = db.get(AIMLModel, model_id)
    if model.state != ModelState.ACTIVE:
        raise framework_error(FrameworkError.INFERENCE_MODEL_NOT_ACTIVE)
    job = InferenceJob(model_id=model_id, status=InferenceState.RUNNING, notification_destination=notification_destination)
    db.add(job)
    db.commit()
    return {"inferenceJobId": str(job.inference_job_id)}


@app.get("/inference-jobs/{inference_job_id}/status")
def query_inference_status(inference_job_id: uuid.UUID, db: Session = Depends(get_session)):
    job = db.get(InferenceJob, inference_job_id)
    return {"inferenceJobId": str(job.inference_job_id), "status": job.status}


@app.post("/inference-jobs/{inference_job_id}/resolve")
def resolve_inference(inference_job_id: uuid.UUID, succeeded: bool, db: Session = Depends(get_session)):
    job = db.get(InferenceJob, inference_job_id)
    job.status = INFERENCE_JOB_FSM.fire(InferenceState(job.status), InferenceEvent.COMPLETE if succeeded else InferenceEvent.FAIL)
    db.commit()
    # result itself is pulled via DME against the model's outputDataType — not carried here (section 3)
    return {"inferenceJobId": str(job.inference_job_id), "status": job.status}


@app.post("/mlmf/subscriptions", status_code=201)
def subscribe_performance_monitoring(model_id: uuid.UUID, metric_types: list[str], dme_type_id: uuid.UUID, guard_kpi_floor: dict | None = None, db: Session = Depends(get_session)):
    """MLMF — new sub-function, AI/ML Workflow LLD section 2. Distinct
    domain from RAN Analytics' MDAF (model performance, not RAN behavior).
    """
    sub = MLMFSubscription(model_id=model_id, metric_types=metric_types, dme_type_id=dme_type_id, guard_kpi_floor=guard_kpi_floor)
    db.add(sub)
    db.commit()
    return {"subscriptionId": str(sub.subscription_id)}


@app.post("/mlmf/subscriptions/{subscription_id}/reports")
def report_performance(subscription_id: uuid.UUID, metrics: dict, db: Session = Depends(get_session)):
    sub = db.get(MLMFSubscription, subscription_id)
    breached = bool(sub.guard_kpi_floor) and any(metrics.get(k, 0) < v for k, v in (sub.guard_kpi_floor or {}).items())
    report = PerformanceReport(subscription_id=subscription_id, metrics=metrics, breached_floor=breached)
    db.add(report)
    db.commit()

    result = {"reportId": str(report.id), "breachedFloor": breached}
    if breached:
        model = db.get(AIMLModel, sub.model_id)
        # if this model belongs to a coordination group, decide group-scoped
        # propagation per LLD section 4.3-4.4; otherwise it's a standalone retrain trigger.
        # Filtered in Python, not a `.any()` array-membership WHERE clause —
        # that's real Postgres ARRAY syntax with no SQLite equivalent under
        # member_model_ids' JSON fallback (`.with_variant(JSON(...), "sqlite")`),
        # so it silently never worked under any test until this group-retrain
        # path finally got route-level coverage. Compared as strings, not
        # `model.model_id in g.member_model_ids` directly — Postgres's native
        # ARRAY(Uuid) round-trips real uuid.UUID objects, but SQLite's JSON
        # fallback has no UUID item type at all, so member_model_ids reads
        # back as plain strings there.
        group = next((g for g in db.scalars(select(MLModelCoordinationGroup)).all()
                       if str(model.model_id) in {str(m) for m in g.member_model_ids}), None)
        if group is not None:
            triggered = should_trigger_group_retrain(
                group.retrain_propagation, member_count=len(group.member_model_ids), breached_count=1
            )
            result["groupRetrainTriggered"] = triggered
            if triggered:
                # The actual fix (OPEN_ITEMS.md section 1's MLModelCoordinationGroup
                # x SA SMOS convergence item): this used to compute the bool and
                # stop — nothing ever fired RETRAIN on a single member model.
                result["retrainedModelIds"] = [str(mid) for mid in _trigger_group_retrain(db, group)]
    return result


@app.get("/training-jobs")
def list_training_jobs(model_id: uuid.UUID | None = None, status: str | None = None, db: Session = Depends(get_session)):
    """List read for RequestTraining's jobs — previously only a
    per-id status read existed, so an operator could never see which
    jobs were running without already holding every trainingJobId.
    """
    stmt = select(TrainingJob)
    if model_id:
        stmt = stmt.where(TrainingJob.model_id == model_id)
    if status:
        stmt = stmt.where(TrainingJob.status == status)
    return [_training_job_view(j) for j in db.scalars(stmt).all()]


@app.get("/inference-jobs")
def list_inference_jobs(model_id: uuid.UUID | None = None, status: str | None = None, db: Session = Depends(get_session)):
    """Same gap as list_training_jobs, for MLEF's InferenceJob."""
    stmt = select(InferenceJob)
    if model_id:
        stmt = stmt.where(InferenceJob.model_id == model_id)
    if status:
        stmt = stmt.where(InferenceJob.status == status)
    return [{"inferenceJobId": str(j.inference_job_id), "modelId": str(j.model_id), "status": j.status,
             "notificationDestination": j.notification_destination} for j in db.scalars(stmt).all()]


@app.get("/coordination-groups")
def list_coordination_groups(db: Session = Depends(get_session)):
    """Read side of create_coordination_group — groups were write-only."""
    return [{"groupId": str(g.group_id), "groupType": g.group_type,
             "memberModelIds": [str(m) for m in g.member_model_ids], "memberUseCases": g.member_use_cases or [],
             "sharedFeaturePipelineRef": g.shared_feature_pipeline_ref, "retrainPropagation": g.retrain_propagation}
            for g in db.scalars(select(MLModelCoordinationGroup)).all()]


@app.get("/mlmf/subscriptions")
def list_performance_subscriptions(model_id: uuid.UUID | None = None, db: Session = Depends(get_session)):
    """MLMF's subscriptions were write-only too (LLD section 2 only ever
    specified Subscribe/Report)."""
    stmt = select(MLMFSubscription)
    if model_id:
        stmt = stmt.where(MLMFSubscription.model_id == model_id)
    return [{"subscriptionId": str(sub.subscription_id), "modelId": str(sub.model_id), "metricTypes": sub.metric_types,
             "dmeTypeId": str(sub.dme_type_id), "guardKpiFloor": sub.guard_kpi_floor} for sub in db.scalars(stmt).all()]


@app.get("/mlmf/subscriptions/{subscription_id}/reports")
def list_performance_reports(subscription_id: uuid.UUID, limit: int = 100, db: Session = Depends(get_session)):
    """Read side of report_performance — newest first, so the GUI can
    chart a model's recent metrics against its guardKpiFloor."""
    if db.get(MLMFSubscription, subscription_id) is None:
        raise HTTPException(status_code=404, detail="no such MLMF subscription")
    rows = db.scalars(select(PerformanceReport).where(PerformanceReport.subscription_id == subscription_id)
                      .order_by(PerformanceReport.reported_at.desc()).limit(limit)).all()
    return [_performance_report_view(r) for r in rows]


@app.get("/mlmf/reports")
def list_recent_performance_reports(breached_only: bool = False, limit: int = 50, db: Session = Depends(get_session)):
    """Fleet-wide recent MLMF reports, newest first — the dashboard's
    view across every model, without first listing every subscription."""
    stmt = select(PerformanceReport)
    if breached_only:
        stmt = stmt.where(PerformanceReport.breached_floor.is_(True))
    rows = db.scalars(stmt.order_by(PerformanceReport.reported_at.desc()).limit(limit)).all()
    return [_performance_report_view(r) for r in rows]


def _training_job_view(j: TrainingJob) -> dict:
    return {"trainingJobId": str(j.training_job_id), "modelId": str(j.model_id) if j.model_id else None,
            "modelCoordinationGroupId": str(j.model_coordination_group_id) if j.model_coordination_group_id else None,
            "producerId": j.producer_id, "status": j.status, "runId": j.run_id,
            "trainingDataset": j.training_dataset, "validationDataset": j.validation_dataset,
            "modelMetrics": j.model_metrics}


def _performance_report_view(r: PerformanceReport) -> dict:
    return {"reportId": str(r.id), "subscriptionId": str(r.subscription_id), "metrics": r.metrics,
            "breachedFloor": r.breached_floor, "reportedAt": r.reported_at.isoformat()}


@app.post("/feature-groups", status_code=201)
def create_feature_group(body: CreateFeatureGroupRequest, db: Session = Depends(get_session)):
    """OPEN_ITEMS.md section 5: no feature-group/feature-store concept
    existed at all. Matches the reference's own
    CreateFeatureGroup (featuregroup_controller.py): name must be
    `\\w+` (word characters only) and 3-63 characters long — the same
    rule the reference applies to both TrainingJob and FeatureGroup
    names — and a duplicate `featureGroupName` 409s, matching
    `DBException` ("already exist") there. `enableDme`'s real
    DME job creation (a raw PUT to
    data-consumer/v1/info-jobs/{featureGroupName} on the feature
    group's own host:port, trainingmgr_operations.create_dme_filtered_data_job)
    is a deliberate elision — `enableDme` is stored and returned
    faithfully, just not acted on, the same no-real-southbound-compute
    pattern as elsewhere in this build.
    """
    if not re.fullmatch(r"\w+", body.featureGroupName) or not (3 <= len(body.featureGroupName) <= 63):
        raise framework_error(FrameworkError.FEATURE_GROUP_NAME_INVALID, detail=f"featureGroupName {body.featureGroupName!r} must be 3-63 word characters")
    group = FeatureGroup(
        feature_group_name=body.featureGroupName, feature_list=body.featureList, datalake_source=body.datalakeSource,
        host=body.host, port=body.port, bucket=body.bucket, token=body.token, db_org=body.dbOrg,
        measurement=body.measurement, enable_dme=body.enableDme, measured_obj_class=body.measuredObjClass,
        dme_port=body.dmePort, source_name=body.sourceName,
    )
    db.add(group)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise framework_error(FrameworkError.FEATURE_GROUP_ALREADY_REGISTERED, detail=f"feature group {body.featureGroupName!r} already exists")
    return _feature_group_view(group)


@app.get("/feature-groups")
def list_feature_groups(db: Session = Depends(get_session)):
    return {"featureGroups": [_feature_group_view(g) for g in db.scalars(select(FeatureGroup)).all()]}


def _feature_group_view(g: FeatureGroup) -> dict:
    # FeatureGroupSchema (the reference's own marshmallow schema) has no
    # exclude list, unlike TrainingJobSchema's — every column, token
    # included, round-trips through its GET/POST responses faithfully.
    return {
        "featureGroupId": str(g.feature_group_id), "featureGroupName": g.feature_group_name,
        "featureList": g.feature_list, "datalakeSource": g.datalake_source, "host": g.host, "port": g.port,
        "bucket": g.bucket, "token": g.token, "dbOrg": g.db_org, "measurement": g.measurement,
        "enableDme": g.enable_dme, "measuredObjClass": g.measured_obj_class, "dmePort": g.dme_port,
        "sourceName": g.source_name,
    }


def _trigger_group_retrain(db: Session, group: MLModelCoordinationGroup) -> list[uuid.UUID]:
    """Fires RETRAIN (the same ACTIVE -> TRAINING transition RequestTraining's
    own modelId-targeted path uses) and creates a per-model TrainingJob for
    every currently ACTIVE member. A member not in ACTIVE (already TRAINING
    from an earlier trigger, or never certified) is skipped rather than
    forced — RETRAIN is only a legal transition from ACTIVE, and this
    mirrors RequestTraining's own already-TRAINING handling instead of
    reimplementing it here.
    """
    retrained_model_ids: list[uuid.UUID] = []
    for raw_member_id in group.member_model_ids:
        # Real uuid.UUID objects on Postgres, plain strings under SQLite's
        # JSON fallback (see the group-lookup comment above) — normalized
        # once here so db.get()/TrainingJob.model_id see a real UUID either way.
        member_id = raw_member_id if isinstance(raw_member_id, uuid.UUID) else uuid.UUID(raw_member_id)
        member = db.get(AIMLModel, member_id)
        if member is None or member.state != ModelState.ACTIVE:
            continue
        job = TrainingJob(model_id=member_id, producer_id="ai-ml-workflow:group-retrain", status="RUNNING")
        db.add(job)
        db.flush()
        member.state = AIML_MODEL_FSM.fire(ModelState.ACTIVE, ModelEvent.RETRAIN)
        member.training_job_id = job.training_job_id
        retrained_model_ids.append(member_id)
    db.commit()
    return retrained_model_ids


def _model_view(m: AIMLModel) -> dict:
    return {"modelId": str(m.model_id), "modelType": m.model_type, "version": m.version, "state": m.state,
            "clearedNodeGroups": m.cleared_node_groups or [], "artifactLocation": m.artifact_location,
            "description": m.description, "author": m.author, "owner": m.owner,
            "inputDataType": m.input_data_type, "outputDataType": m.output_data_type,
            "targetEnvironments": m.target_environments or []}
