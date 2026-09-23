"""AI/ML Workflow SMOS (AIMgF/MLTF/MLVF/MLEF/MLMR/MLLF, +MLMF new).

SMO Design v1.3 section 3.8, extended by AI/ML Workflow LLD sections 1-8:
MLMF closes the missing monitoring surface, MLEF now hosts inference
serving, MLModelCoordinationGroup's Shape A is fully built, and
clearedNodeGroups resolves MultiNode Q2's deployment-targeting gap.
"""

import uuid

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error

from .models import AIMLModel, InferenceJob, MLMFSubscription, MLModelCoordinationGroup, ModelChangeSubscription, PerformanceReport, TrainingJob
from .statemachine import AIML_MODEL_FSM, INFERENCE_JOB_FSM, InferenceEvent, InferenceState, ModelEvent, ModelState, should_trigger_group_retrain

app = FastAPI(title="AI/ML Workflow SMOS")


class RegisterModelRequest(BaseModel):
    modelType: str
    version: str
    requiredResourceTypeId: str | None = None


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


@app.post("/models", status_code=201)
def register_model(body: RegisterModelRequest, db: Session = Depends(get_session)):
    model = AIMLModel(registration_id=str(uuid.uuid4()), model_type=body.modelType, version=body.version,
                       required_resource_type_id=body.requiredResourceTypeId, state=ModelState.REGISTERED)
    db.add(model)
    db.commit()
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
                       status="RUNNING")
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
    return {"trainingJobId": str(job.training_job_id), "status": job.status}


@app.delete("/training-jobs/{training_job_id}", status_code=204)
def cancel_training(training_job_id: uuid.UUID, db: Session = Depends(get_session)):
    job = db.get(TrainingJob, training_job_id)
    if job is not None:
        job.status = "CANCELLED"
        db.commit()


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
            "clearedNodeGroups": m.cleared_node_groups or []}
