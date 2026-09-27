"""AIMgF (AI Management Function) — TS 28.105 AI/ML NRM realization.

Wave 2 of the AI Platform Service Decomposition: the full eight-aggregate
domain model and two real state machines (see docs/architecture/
AI_PLATFORM_BASELINE.md and docs/ownership/AIMGF_OWNERSHIP.md), deepening
Wave 1's structural split of the former flat `ai-ml-workflow/` module.

  - ModelLifecycle  — a model's own identity/certification path
                      (training -> validation -> emulation -> governance
                      -> deprecation/retirement). AIMgF's own storage now
                      (`.models.ModelLifecycle`), not MLMR's row: Wave 1's
                      `PATCH /mlmr/models/{id}/lifecycle` is gone —
                      `docs/architecture/SERVICE_OWNERSHIP_MATRIX.md`'s
                      own "Lifecycle state: AIMgF ✅, MLMR ❌" is now
                      actually true, not just documented.
  - RuntimeLifecycle — a model's serving existence once PROMOTED, jointly
                      owned with NFO: AIMgF now really calls NFO's
                      descriptor/instantiate/scale/terminate routes
                      (`_nfo_*` below) for "request runtime creation/
                      termination/scaling" (AIMGF_OWNERSHIP.md's own list),
                      not just tracking a state that never drove anything.
                      A model runtime has no onboarded ApplicationPackage
                      behind it, unlike an rApp's own NfDeploymentDescriptor
                      — `packageId` is omitted on `_nfo_create_descriptor`
                      (NFO's own column is nullable since this wave).

MLLF's own `request_model_deployment` (node-group targeting) now reads/
writes this module's `/models/{id}/lifecycle` and
`/models/{id}/runtime/node-groups` instead of MLMR's row — MLMR is model
truth, not lifecycle truth, and never was meant to carry either field
past Wave 1's structural shortcut.
"""

import re
import uuid

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.r1_client import R1Client
from smo_shared.statemachine import IllegalTransition

from .models import (
    CertificationRecord, EmulationJob, FeatureGroup, InferenceJob, LifecycleTransition, MLMFSubscription,
    ModelLifecycle, PerformanceReport, TrainingJob, ValidationJob,
)
from .statemachine import (
    GOVERNANCE_EVENTS, INFERENCE_JOB_FSM, MODEL_LIFECYCLE_FSM, RUNTIME_LIFECYCLE_FSM, InferenceEvent, InferenceState,
    ModelLifecycleEvent, ModelLifecycleState, RuntimeLifecycleEvent, RuntimeLifecycleState, should_trigger_group_retrain,
)

app = FastAPI(title="AIMgF")

_r1 = R1Client()


@app.get("/health")
def health_check():
    """Liveness probe. The GUI BFF's GET /modules/status fans out to
    /<module>/health through R1 Termination for every module in parallel.
    """
    return {"status": "healthy"}


def _get_model_or_none(model_id: uuid.UUID) -> dict | None:
    resp = _r1.get(f"/mlmr/models/{model_id}")
    return resp.json() if resp.status_code == 200 else None


def _get_model(model_id: uuid.UUID) -> dict:
    model = _get_model_or_none(model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="no such model")
    return model


def _get_or_create_lifecycle(db: Session, model_id: uuid.UUID) -> ModelLifecycle:
    """Every model has a ModelLifecycle row lazily: MLMR's own
    `register_model` has no hook into AIMgF (a real cross-service call for
    every registration would be more coupling than Wave 2 needs), so this
    row is created here, the first time AIMgF is ever asked about the
    model, at its REGISTERED/NOT_DEPLOYED defaults.
    """
    lifecycle = db.get(ModelLifecycle, model_id)
    if lifecycle is None:
        lifecycle = ModelLifecycle(model_id=model_id)
        db.add(lifecycle)
        db.flush()
    return lifecycle


def _fire_model_event(db: Session, model_id: uuid.UUID, event: ModelLifecycleEvent,
                       decided_by: str | None = None, rationale: str | None = None) -> ModelLifecycle:
    """Fires a ModelLifecycle transition, records it (LifecycleTransition),
    and — for the four governance decisions plus their submit/reject
    framing (`GOVERNANCE_EVENTS`) — writes a CertificationRecord too, per
    AIMGF_OWNERSHIP.md's own Governance list.
    """
    if event in GOVERNANCE_EVENTS and decided_by is None:
        raise framework_error(FrameworkError.GOVERNANCE_DECIDER_REQUIRED, detail=f"{event} requires decidedBy")
    lifecycle = _get_or_create_lifecycle(db, model_id)
    from_state = ModelLifecycleState(lifecycle.model_lifecycle_state)
    try:
        new_state = MODEL_LIFECYCLE_FSM.fire(from_state, event)
    except IllegalTransition:
        raise framework_error(FrameworkError.LIFECYCLE_ILLEGAL_TRANSITION,
                               detail=f"cannot fire {event} from model lifecycle state {from_state}")
    lifecycle.model_lifecycle_state = new_state
    db.add(LifecycleTransition(model_id=model_id, fsm="MODEL", from_state=from_state, to_state=new_state, event=event))
    if event in GOVERNANCE_EVENTS:
        db.add(CertificationRecord(model_id=model_id, decision=event, decided_by=decided_by, rationale=rationale))
    db.flush()
    return lifecycle


def _fire_runtime_event(db: Session, model_id: uuid.UUID, event: RuntimeLifecycleEvent) -> ModelLifecycle:
    lifecycle = _get_or_create_lifecycle(db, model_id)
    from_state = RuntimeLifecycleState(lifecycle.runtime_lifecycle_state)
    try:
        new_state = RUNTIME_LIFECYCLE_FSM.fire(from_state, event)
    except IllegalTransition:
        raise framework_error(FrameworkError.LIFECYCLE_ILLEGAL_TRANSITION,
                               detail=f"cannot fire {event} from runtime lifecycle state {from_state}")
    lifecycle.runtime_lifecycle_state = new_state
    db.add(LifecycleTransition(model_id=model_id, fsm="RUNTIME", from_state=from_state, to_state=new_state, event=event))
    db.flush()
    return lifecycle


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


class RequestValidationRequest(BaseModel):
    modelId: uuid.UUID
    trainingJobId: uuid.UUID | None = None
    producerId: str
    validationCriteria: dict = {}


class RequestEmulationRequest(BaseModel):
    modelId: uuid.UUID
    producerId: str
    emulationCriteria: dict = {}


class CompleteJobRequest(BaseModel):
    succeeded: bool
    metrics: dict = {}


class UpdateNodeGroupsRequest(BaseModel):
    clearedNodeGroups: list[str]


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


# ---------------------------------------------------------------- Training

@app.post("/training-jobs", status_code=201)
def request_training(body: RequestTrainingRequest, db: Session = Depends(get_session)):
    """RequestTraining — exactly one of modelId/modelCoordinationGroupId,
    enforced at the DB layer (exactly_one_target constraint) and checked
    here for a clean error.

    modelId-targeted requests also drive the model's own ModelLifecycle
    FSM: REGISTERED -> TRAINING (the very first cycle) or
    PROMOTED -> TRAINING (an ordinary retrain) or FAILED -> TRAINING (a
    retry). A model already TRAINING (an unresolved prior job) is treated
    as the operator's explicit decision to supersede it: the orphaned job
    is marked CANCELLED rather than left silently RUNNING and unreachable.
    """
    if (body.modelId is None) == (body.modelCoordinationGroupId is None):
        raise framework_error(FrameworkError.COORDINATION_GROUP_MISMATCH)

    lifecycle = None
    if body.modelId is not None:
        _get_model(body.modelId)
        lifecycle = _get_or_create_lifecycle(db, body.modelId)
        if lifecycle.model_lifecycle_state not in (
            ModelLifecycleState.REGISTERED, ModelLifecycleState.PROMOTED,
            ModelLifecycleState.FAILED, ModelLifecycleState.TRAINING,
        ):
            raise framework_error(FrameworkError.MODEL_NOT_CERTIFIED,
                                   detail=f"cannot (re)train a model in state {lifecycle.model_lifecycle_state}")

    # TS28.105 AI/ML NRM's own real mLTrainingType — INITIAL_TRAINING the
    # very first cycle (model still REGISTERED), RE_TRAINING every other
    # case.
    ml_training_type = "INITIAL_TRAINING" if lifecycle is not None and lifecycle.model_lifecycle_state == ModelLifecycleState.REGISTERED else "RE_TRAINING"

    job = TrainingJob(model_id=body.modelId, model_coordination_group_id=body.modelCoordinationGroupId,
                       producer_id=body.producerId, required_data=body.requiredData,
                       validation_criteria=body.validationCriteria, notification_uri=body.notificationUri,
                       status="RUNNING", run_id=body.runId, training_dataset=body.trainingDataset,
                       validation_dataset=body.validationDataset, consumer_rapp_id=body.consumerRappId,
                       producer_rapp_id=body.producerRappId, ml_training_type=ml_training_type)
    db.add(job)
    db.flush()

    if lifecycle is not None:
        if lifecycle.model_lifecycle_state == ModelLifecycleState.TRAINING:
            existing_job_id = lifecycle.training_job_id
            if existing_job_id is not None:
                orphaned = db.get(TrainingJob, existing_job_id)
                if orphaned is not None and orphaned.status == "RUNNING":
                    orphaned.status = "CANCELLED"
        else:
            _fire_model_event(db, body.modelId, ModelLifecycleEvent.CREATE_TRAINING)
        lifecycle.training_job_id = job.training_job_id

    db.commit()
    return {"trainingJobId": str(job.training_job_id)}


@app.get("/training-jobs/{training_job_id}/status")
def query_training_job_status(training_job_id: uuid.UUID, db: Session = Depends(get_session)):
    job = db.get(TrainingJob, training_job_id)
    return {
        "trainingJobId": str(job.training_job_id), "status": job.status, "runId": job.run_id,
        "trainingDataset": job.training_dataset, "validationDataset": job.validation_dataset,
        "consumerRappId": job.consumer_rapp_id, "producerRappId": job.producer_rapp_id,
        "mlTrainingType": job.ml_training_type,
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


@app.get("/training-jobs")
def list_training_jobs(model_id: uuid.UUID | None = None, status: str | None = None, db: Session = Depends(get_session)):
    stmt = select(TrainingJob)
    if model_id:
        stmt = stmt.where(TrainingJob.model_id == model_id)
    if status:
        stmt = stmt.where(TrainingJob.status == status)
    return [_training_job_view(j) for j in db.scalars(stmt).all()]


# ---------------------------------------------------------------- Validation

@app.post("/validation-jobs", status_code=201)
def request_validation(body: RequestValidationRequest, db: Session = Depends(get_session)):
    """CreateValidation (AIMGF_OWNERSHIP.md's own request list) — new this
    wave: Wave 1's flat FSM folded validation silently into
    TRAINING_COMPLETE -> TESTED with no request/tracking of its own.
    Requires the model to have finished training (TRAINED).
    """
    _get_model(body.modelId)
    lifecycle = _get_or_create_lifecycle(db, body.modelId)
    if lifecycle.model_lifecycle_state != ModelLifecycleState.TRAINED:
        raise framework_error(FrameworkError.MODEL_NOT_CERTIFIED,
                               detail=f"cannot request validation for a model in state {lifecycle.model_lifecycle_state}")
    job = ValidationJob(model_id=body.modelId, training_job_id=body.trainingJobId, producer_id=body.producerId,
                         validation_criteria=body.validationCriteria, status="RUNNING")
    db.add(job)
    db.flush()
    _fire_model_event(db, body.modelId, ModelLifecycleEvent.CREATE_VALIDATION)
    db.commit()
    return {"validationJobId": str(job.validation_job_id)}


@app.get("/validation-jobs/{validation_job_id}/status")
def query_validation_job_status(validation_job_id: uuid.UUID, db: Session = Depends(get_session)):
    job = db.get(ValidationJob, validation_job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such validation job")
    return _validation_job_view(job)


@app.post("/validation-jobs/{validation_job_id}/complete")
def complete_validation(validation_job_id: uuid.UUID, body: CompleteJobRequest, db: Session = Depends(get_session)):
    job = db.get(ValidationJob, validation_job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such validation job")
    job.status = "COMPLETED" if body.succeeded else "FAILED"
    job.metrics = body.metrics
    event = ModelLifecycleEvent.VALIDATION_COMPLETE if body.succeeded else ModelLifecycleEvent.VALIDATION_FAILED
    _fire_model_event(db, job.model_id, event)
    db.commit()
    return _validation_job_view(job)


@app.get("/validation-jobs")
def list_validation_jobs(model_id: uuid.UUID | None = None, status: str | None = None, db: Session = Depends(get_session)):
    stmt = select(ValidationJob)
    if model_id:
        stmt = stmt.where(ValidationJob.model_id == model_id)
    if status:
        stmt = stmt.where(ValidationJob.status == status)
    return [_validation_job_view(j) for j in db.scalars(stmt).all()]


# ---------------------------------------------------------------- Emulation

@app.post("/emulation-jobs", status_code=201)
def request_emulation(body: RequestEmulationRequest, db: Session = Depends(get_session)):
    """CreateEmulation — new this wave, split out from Wave 1's flat
    VALIDATION_COMPLETE -> EMULATED transition the same way ValidationJob
    is. Requires the model to have passed validation (VALIDATED).
    """
    _get_model(body.modelId)
    lifecycle = _get_or_create_lifecycle(db, body.modelId)
    if lifecycle.model_lifecycle_state != ModelLifecycleState.VALIDATED:
        raise framework_error(FrameworkError.MODEL_NOT_CERTIFIED,
                               detail=f"cannot request emulation for a model in state {lifecycle.model_lifecycle_state}")
    job = EmulationJob(model_id=body.modelId, producer_id=body.producerId, emulation_criteria=body.emulationCriteria, status="RUNNING")
    db.add(job)
    db.flush()
    _fire_model_event(db, body.modelId, ModelLifecycleEvent.CREATE_EMULATION)
    db.commit()
    return {"emulationJobId": str(job.emulation_job_id)}


@app.get("/emulation-jobs/{emulation_job_id}/status")
def query_emulation_job_status(emulation_job_id: uuid.UUID, db: Session = Depends(get_session)):
    job = db.get(EmulationJob, emulation_job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such emulation job")
    return _emulation_job_view(job)


@app.post("/emulation-jobs/{emulation_job_id}/complete")
def complete_emulation(emulation_job_id: uuid.UUID, body: CompleteJobRequest, db: Session = Depends(get_session)):
    job = db.get(EmulationJob, emulation_job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such emulation job")
    job.status = "COMPLETED" if body.succeeded else "FAILED"
    job.metrics = body.metrics
    event = ModelLifecycleEvent.EMULATION_COMPLETE if body.succeeded else ModelLifecycleEvent.EMULATION_FAILED
    _fire_model_event(db, job.model_id, event)
    db.commit()
    return _emulation_job_view(job)


@app.get("/emulation-jobs")
def list_emulation_jobs(model_id: uuid.UUID | None = None, status: str | None = None, db: Session = Depends(get_session)):
    stmt = select(EmulationJob)
    if model_id:
        stmt = stmt.where(EmulationJob.model_id == model_id)
    if status:
        stmt = stmt.where(EmulationJob.status == status)
    return [_emulation_job_view(j) for j in db.scalars(stmt).all()]


# ---------------------------------------------------------------- ModelLifecycle: generic advance + governance

@app.post("/models/{model_id}/advance")
def advance_model_lifecycle(model_id: uuid.UUID, event: str, decided_by: str | None = None, rationale: str | None = None,
                             db: Session = Depends(get_session)):
    """Single endpoint driving every ModelLifecycle transition that isn't
    already its own request route above — SUBMIT_FOR_APPROVAL, APPROVE,
    REJECT, CERTIFY, PROMOTE, ROLLBACK, DEPRECATE, RETIRE — each a real
    FSM transition (statemachine.py). `decidedBy` is required for the six
    governance decisions (`GOVERNANCE_EVENTS`) and written onto a real
    CertificationRecord; omitted for DEPRECATE/RETIRE, which aren't
    governance decisions in AIMGF_OWNERSHIP.md's own sense.
    """
    _get_model(model_id)
    ev = ModelLifecycleEvent(event)
    lifecycle = _fire_model_event(db, model_id, ev, decided_by=decided_by, rationale=rationale)
    db.commit()
    return _lifecycle_view(lifecycle)


@app.get("/models/{model_id}/lifecycle")
def get_model_lifecycle(model_id: uuid.UUID, db: Session = Depends(get_session)):
    _get_model(model_id)
    lifecycle = _get_or_create_lifecycle(db, model_id)
    db.commit()
    return _lifecycle_view(lifecycle)


@app.get("/model-lifecycles")
def list_model_lifecycles(db: Session = Depends(get_session)):
    """(GUI) Every model AIMgF has ever been asked to act on, in one call —
    the Models table's own State/Node-groups columns would otherwise be
    an N-model-lifecycle-fetches-per-page-load problem. A model MLMR
    knows about that AIMgF has never touched yet simply has no row here
    (still REGISTERED/NOT_DEPLOYED in truth, per `_get_or_create_lifecycle`'s
    own lazy-initialization default) — the GUI falls back to that same
    default for a model missing from this list.
    """
    return [_lifecycle_view(l) for l in db.scalars(select(ModelLifecycle)).all()]


@app.get("/models/{model_id}/governance-history")
def list_governance_history(model_id: uuid.UUID, db: Session = Depends(get_session)):
    rows = db.scalars(select(CertificationRecord).where(CertificationRecord.model_id == model_id)
                       .order_by(CertificationRecord.decided_at)).all()
    return [_certification_record_view(r) for r in rows]


@app.get("/models/{model_id}/lifecycle-history")
def list_lifecycle_history(model_id: uuid.UUID, fsm: str | None = None, db: Session = Depends(get_session)):
    stmt = select(LifecycleTransition).where(LifecycleTransition.model_id == model_id)
    if fsm:
        stmt = stmt.where(LifecycleTransition.fsm == fsm)
    rows = db.scalars(stmt.order_by(LifecycleTransition.occurred_at)).all()
    return [{"fsm": r.fsm, "fromState": r.from_state, "toState": r.to_state, "event": r.event, "occurredAt": r.occurred_at.isoformat()}
            for r in rows]


# ---------------------------------------------------------------- RuntimeLifecycle (jointly with NFO)

def _nfo_create_descriptor(model_id: uuid.UUID) -> uuid.UUID:
    """A model runtime has no onboarded ApplicationPackage behind it —
    unlike Onboarding's own CreateDescriptor call, packageId is omitted
    (NFO's own `nf_deployment_descriptor.package_id` is nullable since
    this wave, migrations/001_init.sql).
    """
    resp = _r1.post("/nfo/descriptors", json={
        "packageId": None, "name": f"aimgf-model-{model_id}-runtime", "workloadTemplate": {"modelId": str(model_id)},
    })
    return uuid.UUID(resp.json()["nfDeploymentDescriptorId"])


def _nfo_instantiate(descriptor_id: uuid.UUID, model_id: uuid.UUID) -> uuid.UUID:
    resp = _r1.post("/nfo/deployments", json={
        "nfDeploymentDescriptorId": str(descriptor_id), "name": f"aimgf-model-{model_id}-runtime",
    })
    return uuid.UUID(resp.json()["nfDeploymentId"])


@app.post("/models/{model_id}/runtime/deploy", status_code=201)
def deploy_model_runtime(model_id: uuid.UUID, db: Session = Depends(get_session)):
    """RuntimeLifecycle's own DEPLOY — jointly owned with NFO
    (AIMGF_OWNERSHIP.md's "NFO invocation: request runtime creation").
    Requires the model to have cleared governance (CERTIFIED or
    PROMOTED). The RuntimeLifecycle guard fires before any NFO call, so a
    duplicate deploy attempt (already DEPLOYMENT_REQUESTED-or-later)
    never touches NFO at all.
    """
    _get_model(model_id)
    lifecycle = _get_or_create_lifecycle(db, model_id)
    if lifecycle.model_lifecycle_state not in (ModelLifecycleState.CERTIFIED, ModelLifecycleState.PROMOTED):
        raise framework_error(FrameworkError.MODEL_NOT_CERTIFIED,
                               detail=f"cannot deploy a runtime for a model in state {lifecycle.model_lifecycle_state}")
    _fire_runtime_event(db, model_id, RuntimeLifecycleEvent.REQUEST_DEPLOYMENT)

    descriptor_id = _nfo_create_descriptor(model_id)
    deployment_id = _nfo_instantiate(descriptor_id, model_id)
    lifecycle.nf_deployment_descriptor_id = descriptor_id
    lifecycle.nf_deployment_id = deployment_id

    _fire_runtime_event(db, model_id, RuntimeLifecycleEvent.DEPLOYMENT_COMPLETE)
    db.commit()
    return _lifecycle_view(lifecycle)


@app.post("/models/{model_id}/runtime/activate")
def activate_model_runtime(model_id: uuid.UUID, db: Session = Depends(get_session)):
    """Marks the runtime as ready to accept inference (request_inference's
    own gate). Local-only: NFO's own deployment is already RUNNING once
    `deploy` returns (Phase 1: instantiate completes synchronously, same
    elision as elsewhere in this build) — ACTIVATE is AIMgF's own
    decision about whether traffic should be sent yet, not a further NFO
    call.
    """
    _fire_runtime_event(db, model_id, RuntimeLifecycleEvent.ACTIVATE)
    lifecycle = _fire_runtime_event(db, model_id, RuntimeLifecycleEvent.ACTIVATION_COMPLETE)
    db.commit()
    return _lifecycle_view(lifecycle)


@app.post("/models/{model_id}/runtime/scale")
def scale_model_runtime(model_id: uuid.UUID, db: Session = Depends(get_session)):
    lifecycle = _get_or_create_lifecycle(db, model_id)
    _fire_runtime_event(db, model_id, RuntimeLifecycleEvent.REQUEST_SCALE)
    if lifecycle.nf_deployment_id is not None:
        _r1.post(f"/nfo/deployments/{lifecycle.nf_deployment_id}/scale")
    _fire_runtime_event(db, model_id, RuntimeLifecycleEvent.SCALE_COMPLETE)
    db.commit()
    return _lifecycle_view(lifecycle)


@app.post("/models/{model_id}/runtime/terminate")
def terminate_model_runtime(model_id: uuid.UUID, db: Session = Depends(get_session)):
    lifecycle = _get_or_create_lifecycle(db, model_id)
    _fire_runtime_event(db, model_id, RuntimeLifecycleEvent.REQUEST_TERMINATION)
    if lifecycle.nf_deployment_id is not None:
        _r1.delete(f"/nfo/deployments/{lifecycle.nf_deployment_id}")
    _fire_runtime_event(db, model_id, RuntimeLifecycleEvent.TERMINATION_COMPLETE)
    db.commit()
    return _lifecycle_view(lifecycle)


@app.patch("/models/{model_id}/runtime/node-groups")
def update_node_groups(model_id: uuid.UUID, body: UpdateNodeGroupsRequest, db: Session = Depends(get_session)):
    """Called by MLLF's own `request_model_deployment` (MultiNode Q2's
    targeting gap, LLD section 5) — MLLF owns the *decision* of which
    node groups a model is placed on, AIMgF owns the row it's written to
    (the same shape as Wave 1's `PATCH /mlmr/models/{id}/lifecycle`, just
    against AIMgF's own storage now instead of MLMR's).
    """
    lifecycle = _get_or_create_lifecycle(db, model_id)
    lifecycle.cleared_node_groups = body.clearedNodeGroups
    db.commit()
    return _lifecycle_view(lifecycle)


# ---------------------------------------------------------------- Inference

@app.post("/models/{model_id}/inference-jobs", status_code=201)
def request_inference(model_id: uuid.UUID, notification_destination: str | None = None, db: Session = Depends(get_session)):
    """RequestInference — MLEF-hosted (AI/ML Workflow LLD section 3).
    Gated on RuntimeLifecycleState.ACTIVE (a serving question), not
    ModelLifecycleState — a PROMOTED-but-not-yet-deployed model, or one
    whose runtime is mid-SCALING, can't serve inference either way.
    """
    _get_model(model_id)
    lifecycle = _get_or_create_lifecycle(db, model_id)
    if lifecycle.runtime_lifecycle_state != RuntimeLifecycleState.ACTIVE:
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


@app.get("/inference-jobs")
def list_inference_jobs(model_id: uuid.UUID | None = None, status: str | None = None, db: Session = Depends(get_session)):
    stmt = select(InferenceJob)
    if model_id:
        stmt = stmt.where(InferenceJob.model_id == model_id)
    if status:
        stmt = stmt.where(InferenceJob.status == status)
    return [{"inferenceJobId": str(j.inference_job_id), "modelId": str(j.model_id), "status": j.status,
             "notificationDestination": j.notification_destination} for j in db.scalars(stmt).all()]


# ---------------------------------------------------------------- MLMF performance monitoring

@app.post("/mlmf/subscriptions", status_code=201)
def subscribe_performance_monitoring(model_id: uuid.UUID, metric_types: list[str], dme_type_id: uuid.UUID, guard_kpi_floor: dict | None = None, db: Session = Depends(get_session)):
    """MLMF — new sub-function, AI/ML Workflow LLD section 2. Distinct
    domain from RAN Analytics' MDAF (model performance, not RAN behavior).
    """
    sub = MLMFSubscription(model_id=model_id, metric_types=metric_types, dme_type_id=dme_type_id, guard_kpi_floor=guard_kpi_floor)
    db.add(sub)
    db.commit()
    return {"subscriptionId": str(sub.subscription_id)}


def _find_coordination_group_for_model(model_id: uuid.UUID) -> dict | None:
    resp = _r1.get("/mlmr/coordination-groups")
    groups = resp.json() if resp.status_code == 200 else []
    return next((g for g in groups if str(model_id) in set(g["memberModelIds"])), None)


@app.post("/mlmf/subscriptions/{subscription_id}/reports")
def report_performance(subscription_id: uuid.UUID, metrics: dict, db: Session = Depends(get_session)):
    sub = db.get(MLMFSubscription, subscription_id)
    breached = bool(sub.guard_kpi_floor) and any(metrics.get(k, 0) < v for k, v in (sub.guard_kpi_floor or {}).items())
    report = PerformanceReport(subscription_id=subscription_id, metrics=metrics, breached_floor=breached)
    db.add(report)
    db.commit()

    result = {"reportId": str(report.id), "breachedFloor": breached}
    if breached:
        # if this model belongs to a coordination group, decide group-scoped
        # propagation per LLD section 4.3-4.4; otherwise it's a standalone retrain trigger.
        group = _find_coordination_group_for_model(sub.model_id)
        if group is not None:
            triggered = should_trigger_group_retrain(
                group["retrainPropagation"], member_count=len(group["memberModelIds"]), breached_count=1
            )
            result["groupRetrainTriggered"] = triggered
            if triggered:
                result["retrainedModelIds"] = [str(mid) for mid in _trigger_group_retrain(db, group)]
    return result


@app.get("/mlmf/subscriptions")
def list_performance_subscriptions(model_id: uuid.UUID | None = None, db: Session = Depends(get_session)):
    stmt = select(MLMFSubscription)
    if model_id:
        stmt = stmt.where(MLMFSubscription.model_id == model_id)
    return [{"subscriptionId": str(sub.subscription_id), "modelId": str(sub.model_id), "metricTypes": sub.metric_types,
             "dmeTypeId": str(sub.dme_type_id), "guardKpiFloor": sub.guard_kpi_floor} for sub in db.scalars(stmt).all()]


@app.get("/mlmf/subscriptions/{subscription_id}/reports")
def list_performance_reports(subscription_id: uuid.UUID, limit: int = 100, db: Session = Depends(get_session)):
    if db.get(MLMFSubscription, subscription_id) is None:
        raise HTTPException(status_code=404, detail="no such MLMF subscription")
    rows = db.scalars(select(PerformanceReport).where(PerformanceReport.subscription_id == subscription_id)
                      .order_by(PerformanceReport.reported_at.desc()).limit(limit)).all()
    return [_performance_report_view(r) for r in rows]


@app.get("/mlmf/reports")
def list_recent_performance_reports(breached_only: bool = False, limit: int = 50, db: Session = Depends(get_session)):
    stmt = select(PerformanceReport)
    if breached_only:
        stmt = stmt.where(PerformanceReport.breached_floor.is_(True))
    rows = db.scalars(stmt.order_by(PerformanceReport.reported_at.desc()).limit(limit)).all()
    return [_performance_report_view(r) for r in rows]


def _trigger_group_retrain(db: Session, group: dict) -> list[uuid.UUID]:
    """Fires CREATE_TRAINING (the same PROMOTED -> TRAINING transition
    RequestTraining's own modelId-targeted path uses) and creates a
    per-model TrainingJob for every currently PROMOTED member. A member
    not PROMOTED (already TRAINING from an earlier trigger, or never
    certified) is skipped rather than forced — CREATE_TRAINING is only a
    legal transition from PROMOTED (or REGISTERED/FAILED).
    """
    retrained_model_ids: list[uuid.UUID] = []
    for raw_member_id in group["memberModelIds"]:
        member_id = uuid.UUID(raw_member_id)
        if _get_model_or_none(member_id) is None:
            continue
        lifecycle = _get_or_create_lifecycle(db, member_id)
        if lifecycle.model_lifecycle_state != ModelLifecycleState.PROMOTED:
            continue
        job = TrainingJob(model_id=member_id, producer_id="aimgf:group-retrain", status="RUNNING", ml_training_type="RE_TRAINING")
        db.add(job)
        db.flush()
        _fire_model_event(db, member_id, ModelLifecycleEvent.CREATE_TRAINING)
        lifecycle.training_job_id = job.training_job_id
        retrained_model_ids.append(member_id)
    db.commit()
    return retrained_model_ids


def _training_job_view(j: TrainingJob) -> dict:
    return {"trainingJobId": str(j.training_job_id), "modelId": str(j.model_id) if j.model_id else None,
            "modelCoordinationGroupId": str(j.model_coordination_group_id) if j.model_coordination_group_id else None,
            "producerId": j.producer_id, "status": j.status, "runId": j.run_id,
            "trainingDataset": j.training_dataset, "validationDataset": j.validation_dataset,
            "modelMetrics": j.model_metrics, "mlTrainingType": j.ml_training_type}


def _validation_job_view(j: ValidationJob) -> dict:
    return {"validationJobId": str(j.validation_job_id), "modelId": str(j.model_id),
            "trainingJobId": str(j.training_job_id) if j.training_job_id else None,
            "producerId": j.producer_id, "validationCriteria": j.validation_criteria or {},
            "status": j.status, "metrics": j.metrics or {}}


def _emulation_job_view(j: EmulationJob) -> dict:
    return {"emulationJobId": str(j.emulation_job_id), "modelId": str(j.model_id), "producerId": j.producer_id,
            "emulationCriteria": j.emulation_criteria or {}, "status": j.status, "metrics": j.metrics or {}}


def _certification_record_view(r: CertificationRecord) -> dict:
    return {"certificationRecordId": str(r.certification_record_id), "modelId": str(r.model_id), "decision": r.decision,
            "decidedBy": r.decided_by, "rationale": r.rationale, "decidedAt": r.decided_at.isoformat()}


def _performance_report_view(r: PerformanceReport) -> dict:
    return {"reportId": str(r.id), "subscriptionId": str(r.subscription_id), "metrics": r.metrics,
            "breachedFloor": r.breached_floor, "reportedAt": r.reported_at.isoformat()}


def _lifecycle_view(l: ModelLifecycle) -> dict:
    return {
        "modelId": str(l.model_id), "modelLifecycleState": l.model_lifecycle_state,
        "runtimeLifecycleState": l.runtime_lifecycle_state,
        "trainingJobId": str(l.training_job_id) if l.training_job_id else None,
        "clearedNodeGroups": l.cleared_node_groups or [],
        "nfDeploymentDescriptorId": str(l.nf_deployment_descriptor_id) if l.nf_deployment_descriptor_id else None,
        "nfDeploymentId": str(l.nf_deployment_id) if l.nf_deployment_id else None,
    }


# ---------------------------------------------------------------- Feature groups

@app.post("/feature-groups", status_code=201)
def create_feature_group(body: CreateFeatureGroupRequest, db: Session = Depends(get_session)):
    """OPEN_ITEMS.md section 5: no feature-group/feature-store concept
    existed at all. Matches the reference's own
    CreateFeatureGroup (featuregroup_controller.py): name must be
    `\\w+` (word characters only) and 3-63 characters long, and a
    duplicate `featureGroupName` 409s, matching `DBException`
    ("already exist") there. `enableDme`'s real DME job creation is a
    deliberate elision — `enableDme` is stored and returned faithfully,
    just not acted on, the same no-real-southbound-compute pattern as
    elsewhere in this build.
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
    return {
        "featureGroupId": str(g.feature_group_id), "featureGroupName": g.feature_group_name,
        "featureList": g.feature_list, "datalakeSource": g.datalake_source, "host": g.host, "port": g.port,
        "bucket": g.bucket, "token": g.token, "dbOrg": g.db_org, "measurement": g.measurement,
        "enableDme": g.enable_dme, "measuredObjClass": g.measured_obj_class, "dmePort": g.dme_port,
        "sourceName": g.source_name,
    }
