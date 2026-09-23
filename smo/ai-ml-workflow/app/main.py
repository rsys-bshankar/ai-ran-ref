"""AI/ML Workflow SMOS (AIMgF/MLTF/MLVF/MLEF/MLMR/MLLF, +MLMF new).

SMO Design v1.3 section 3.8, extended by AI/ML Workflow LLD sections 1-8:
MLMF closes the missing monitoring surface, MLEF now hosts inference
serving, MLModelCoordinationGroup's Shape A is fully built, and
clearedNodeGroups resolves MultiNode Q2's deployment-targeting gap.
"""

import uuid

from fastapi import Depends, FastAPI
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
    """
    if (body.modelId is None) == (body.modelCoordinationGroupId is None):
        raise framework_error(FrameworkError.COORDINATION_GROUP_MISMATCH)
    job = TrainingJob(model_id=body.modelId, model_coordination_group_id=body.modelCoordinationGroupId,
                       producer_id=body.producerId, required_data=body.requiredData,
                       validation_criteria=body.validationCriteria, notification_uri=body.notificationUri,
                       status="RUNNING")
    db.add(job)
    if body.modelId:
        model = db.get(AIMLModel, body.modelId)
        model.state = AIML_MODEL_FSM.fire(ModelState(model.state), ModelEvent.TRAIN)
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
        group = db.scalar(select(MLModelCoordinationGroup).where(MLModelCoordinationGroup.member_model_ids.any(model.model_id)))
        if group is not None:
            result["groupRetrainTriggered"] = should_trigger_group_retrain(
                group.retrain_propagation, member_count=len(group.member_model_ids), breached_count=1
            )
    return result


def _model_view(m: AIMLModel) -> dict:
    return {"modelId": str(m.model_id), "modelType": m.model_type, "version": m.version, "state": m.state,
            "clearedNodeGroups": m.cleared_node_groups or []}
