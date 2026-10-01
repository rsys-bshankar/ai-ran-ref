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

import httpx
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.r1_client import R1Client
from smo_shared.statemachine import IllegalTransition
from smo_shared.openapi_security import apply_r1_gateway_security
from smo_shared.correlation import apply_correlation_id
from smo_shared.pagination import PageLimit, PageOffset, paginate
from smo_shared.webhook import post_webhook

from .models import (
    AIMLInferenceFunction, AIMLInferenceReport, CertificationRecord, EmulationJob, FeatureGroup, InferenceJob,
    LifecycleTransition, MLMFSubscription, MLTestingReport, MLTrainingFunction, MLTrainingProcess, MLTrainingReport,
    ModelLifecycle, PerformanceReport, TrainingJob, ValidationJob,
)
from . import ts28105
from .statemachine import (
    GOVERNANCE_EVENTS, INFERENCE_JOB_FSM, MODEL_LIFECYCLE_FSM, RUNTIME_LIFECYCLE_FSM, InferenceEvent, InferenceState,
    ModelLifecycleEvent, ModelLifecycleState, RuntimeLifecycleEvent, RuntimeLifecycleState, should_trigger_group_retrain,
)

app = FastAPI(title="AIMgF")
apply_r1_gateway_security(app)
apply_correlation_id(app)

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
        raise framework_error(FrameworkError.MODEL_NOT_FOUND, detail="no such model")
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
    # OPEN_ITEMS.md section 6.1: the operator gate's own two flags.
    # APPROVE_TRAINING/APPROVE_VALIDATION set them; a fresh CREATE_TRAINING
    # (first cycle or retrain) resets both — a stale approval from a prior
    # pipeline run must never silently carry forward into a new one.
    if event == ModelLifecycleEvent.APPROVE_TRAINING:
        lifecycle.training_approved = True
    elif event == ModelLifecycleEvent.APPROVE_VALIDATION:
        lifecycle.validation_approved = True
    elif event == ModelLifecycleEvent.CREATE_TRAINING:
        lifecycle.training_approved = False
        lifecycle.validation_approved = False
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
    # OPEN_ITEMS.md section 6.4: a separate, explicitly-typed reference to
    # the real DME DataJob(s) training actually consumes — requiredData
    # itself stays the opaque blob it always was. Optional and additive:
    # an empty/omitted list skips the check entirely, the same permissive
    # shape DME's own sourceDomain/sourceContext already uses for an
    # optional cross-reference.
    dmeDataJobIds: list[uuid.UUID] = []
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
    # OPEN_ITEMS.md section 6.5: TS28.105-style completion notification —
    # same optional, best-effort shape TrainingJob's own notificationUri
    # already had (and never used); now genuinely fired on completion.
    notificationUri: str | None = None


class RequestEmulationRequest(BaseModel):
    modelId: uuid.UUID
    producerId: str
    emulationCriteria: dict = {}
    notificationUri: str | None = None
    # Wave 4 — TS 28.105 AIMLInferenceEmulationFunction hosting this run.
    aIMLInferenceEmulationFunctionRef: uuid.UUID | None = None


class CompleteJobRequest(BaseModel):
    succeeded: bool
    metrics: dict = {}
    # OPEN_ITEMS.md section 6.5: where the real output artifact lives —
    # a DME DmeTypeId reference, the same "route it through DME" shape
    # MLModel's own outputDataType already uses. Optional: a job the
    # producer doesn't attach an artifact to (e.g. a failed run) simply
    # leaves this null.
    outcomeArtifactDmeTypeId: uuid.UUID | None = None
    # Wave 4 — TS 28.105 report attributes the executing runtime supplies
    # on completion; all optional. Training -> MLTrainingReport, Validation
    # -> MLTestingReport (modelPerformanceTesting), Emulation ->
    # AIMLInferenceReport (inferenceOutputs/potentialImpactInfo).
    modelPerformanceTraining: list[ts28105.ModelPerformance] | None = None
    modelPerformanceValidation: list[ts28105.ModelPerformance] | None = None
    modelPerformanceTesting: list[ts28105.ModelPerformance] | None = None
    modelConfidenceIndication: int | None = None
    usedConsumerTrainingData: list[str] | None = None
    dataRatioTrainingAndValidation: int | None = None
    areNewTrainingDataUsed: bool | None = None
    fLReportPerClient: list[ts28105.FLReportPerClient] | None = None
    inferenceOutputs: list[ts28105.InferenceOutput] | None = None
    potentialImpactInfo: ts28105.PotentialImpactInfo | None = None


class ResolveInferenceRequest(BaseModel):
    """Wave 4 — optional body on resolve: the inference's own TS 28.105
    AIMLInferenceReport content."""
    inferenceOutputs: list[ts28105.InferenceOutput] = []
    potentialImpactInfo: ts28105.PotentialImpactInfo | None = None


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


# ---------------------------------------------------------------- Execution runtimes (jointly with NFO)

def _nfo_create_execution_descriptor(job_kind: str, job_id: uuid.UUID) -> uuid.UUID:
    """OPEN_ITEMS.md section 6.2: a real NFO-backed execution runtime for
    Training/Validation/Emulation — "MLTF trains (Phase 1: elided)" /
    "MLVF validates (Phase 1: elided)" / "MLEF emulates (Phase 1: elided)"
    were bare comments with no NFO call behind them at all, a structurally
    different elision from RuntimeLifecycle's own genuine
    descriptor/instantiate/terminate calls (`_nfo_create_descriptor`/
    `_nfo_instantiate` above), which exist only for a model's serving
    runtime, post-certification. Same shape as those — no onboarded
    ApplicationPackage behind a transient execution job either, so
    packageId is omitted — parameterized by job kind/id instead of
    model id, since all four (Training/Validation/Emulation share this
    helper; Inference doesn't, see InferenceJob's own docstring) use one
    workload-template shape rather than each inventing its own.
    """
    resp = _r1.post("/nfo/descriptors", json={
        "packageId": None, "name": f"aimgf-{job_kind.lower()}-{job_id}",
        "workloadTemplate": {"jobKind": job_kind, "jobId": str(job_id)},
    })
    return uuid.UUID(resp.json()["nfDeploymentDescriptorId"])


def _nfo_instantiate_execution(descriptor_id: uuid.UUID, job_kind: str, job_id: uuid.UUID) -> uuid.UUID:
    resp = _r1.post("/nfo/deployments", json={
        "nfDeploymentDescriptorId": str(descriptor_id), "name": f"aimgf-{job_kind.lower()}-{job_id}",
    })
    return uuid.UUID(resp.json()["nfDeploymentId"])


def _nfo_terminate_execution(nf_deployment_id: uuid.UUID | None) -> None:
    """Unlike RuntimeLifecycle's own long-lived serving deployment
    (terminated only by an explicit `terminate_model_runtime` call), a
    Training/Validation/Emulation job's own execution runtime is
    transient by nature — the run is done once the job completes, so its
    NFO deployment is torn down right alongside the job's own completion,
    not left running indefinitely. A job whose request never actually
    reached NFO (nf_deployment_id still None — can't happen on the happy
    path, but completion is reachable from other states too) is a no-op.
    """
    if nf_deployment_id is not None:
        _r1.delete(f"/nfo/deployments/{nf_deployment_id}")


# ---------------------------------------------------------------- Training

def _validate_dme_data_job_ids(dme_data_job_ids: list[uuid.UUID]) -> None:
    """OPEN_ITEMS.md section 6.4: mirrors MDAF's own
    `_validate_input_sources_are_real_dme_artifacts` (`mdaf/app/main.py`)
    exactly — every declared id must resolve to a real DME `DataJob`.
    AIMgF doesn't fetch the data itself here either, only proves the
    reference is real, the same division of responsibility MDAF's own
    docstring states. An empty list (the default) is a no-op: this check
    is additive, not a new hard requirement on every training request.
    """
    for data_job_id in dme_data_job_ids:
        resp = _r1.get(f"/dme/data-jobs/{data_job_id}")
        if resp.status_code != 200:
            raise framework_error(FrameworkError.DME_ARTIFACT_NOT_FOUND, detail=f"no such DME data job {data_job_id}")


def _notify_job_completion(notification_uri: str | None, job_kind: str, job_id: uuid.UUID, succeeded: bool,
                            outcome_artifact_dme_type_id: uuid.UUID | None, metrics: dict) -> None:
    """OPEN_ITEMS.md section 6.5: TS28.105-style completion notification —
    `advance(TRAINING_COMPLETE)`/`complete(validationJobId, ...)`/
    `complete(emulationJobId, ...)` used to be bare state transitions
    with no side effect beyond the FSM move itself. Best-effort, the
    same pattern as every other subscription-shaped notification in
    this build (report_performance's own subscriber push, MDAF's
    publish_report, Intent Service's CreateIntent) — an unreachable
    destination never fails the completion call itself.
    """
    post_webhook(notification_uri, json={
        "jobKind": job_kind, "jobId": str(job_id), "succeeded": succeeded,
        "outcomeArtifactDmeTypeId": str(outcome_artifact_dme_type_id) if outcome_artifact_dme_type_id else None,
        "metrics": metrics,
    }, timeout=2.0)


# Training status -> TS 28.105 ProcessMonitor.status of its MLTrainingProcess.
_PROCESS_STATUS_FOR_JOB = {"NOT_STARTED": "NOT_RUNNING", "IN_PROGRESS": "RUNNING", "SUSPENDED": "SUSPENDED",
                           "FINISHED": "FINISHED", "FAILED": "FAILED", "CANCELLED": "CANCELLED"}


def _sync_training_process(db: Session, job: TrainingJob) -> None:
    """Keeps a job's MLTrainingProcess (Wave 4, TS 28.105) in step with
    the job's own status, whichever route moved it."""
    process = db.scalar(select(MLTrainingProcess).where(MLTrainingProcess.training_job_id == job.training_job_id))
    if process is None:
        return
    process.status = _PROCESS_STATUS_FOR_JOB.get(job.status, job.status)
    process.suspend_process = job.status == "SUSPENDED"
    process.cancel_process = job.status == "CANCELLED"
    if job.status == "FINISHED":
        process.progress_percentage = 100
        process.result_state_info = "SUCCEEDED"
    elif job.status in ("FAILED", "CANCELLED"):
        process.result_state_info = job.status


def _start_training(db: Session, *, model_id: uuid.UUID | None, group_id: uuid.UUID | None, producer_id: str,
                    ml_training_type: str | None = None, priority: int = 0, termination_conditions: str | None = None,
                    **job_fields) -> TrainingJob:
    """The one place a training run starts — RequestTraining
    (`POST /training-jobs`), TS 28.105 MLTrainingRequest
    (`POST /ml-training-requests`), group-retrain propagation and
    MLUpdateProcess all go through here, so every run gets the same
    lifecycle gate, NFO runtime and (Wave 4) MLTrainingProcess.

    modelId-targeted requests also drive the model's own ModelLifecycle
    FSM: REGISTERED -> TRAINING (the very first cycle) or
    PROMOTED -> TRAINING (an ordinary retrain) or FAILED -> TRAINING (a
    retry). A model already TRAINING (an unresolved prior job) is treated
    as the operator's explicit decision to supersede it: the orphaned job
    is marked CANCELLED rather than left silently RUNNING and unreachable.
    """
    lifecycle = None
    if model_id is not None:
        _get_model(model_id)
        lifecycle = _get_or_create_lifecycle(db, model_id)
        if lifecycle.model_lifecycle_state not in (
            ModelLifecycleState.REGISTERED, ModelLifecycleState.PROMOTED,
            ModelLifecycleState.FAILED, ModelLifecycleState.TRAINING,
        ):
            raise framework_error(FrameworkError.MODEL_NOT_CERTIFIED,
                                   detail=f"cannot (re)train a model in state {lifecycle.model_lifecycle_state}")

    # TS28.105 AI/ML NRM's own real mLTrainingType — INITIAL_TRAINING the
    # very first cycle (model still REGISTERED), RE_TRAINING every other
    # case, unless the requester names one (Wave 4: MLTrainingRequest's
    # mLTrainingType is writable — PRE_SPECIALISED_TRAINING/FINE_TUNING are
    # the requester's to declare). INITIAL_TRAINING is only meaningful for
    # a model that has never been trained.
    is_first_cycle = lifecycle is not None and lifecycle.model_lifecycle_state == ModelLifecycleState.REGISTERED
    if ml_training_type is None:
        ml_training_type = "INITIAL_TRAINING" if is_first_cycle else "RE_TRAINING"
    elif ml_training_type == "INITIAL_TRAINING" and lifecycle is not None and not is_first_cycle:
        raise framework_error(FrameworkError.LIFECYCLE_ILLEGAL_TRANSITION,
                               detail=f"INITIAL_TRAINING requires a REGISTERED model, not {lifecycle.model_lifecycle_state}")

    job = TrainingJob(model_id=model_id, model_coordination_group_id=group_id, producer_id=producer_id,
                       status="IN_PROGRESS", ml_training_type=ml_training_type, **job_fields)
    db.add(job)
    db.flush()
    db.add(MLTrainingProcess(training_job_id=job.training_job_id, priority=priority,
                             termination_conditions=termination_conditions, status="RUNNING"))
    if job.ml_training_function_id is not None:
        function = db.get(MLTrainingFunction, job.ml_training_function_id)
        if function is not None:
            function.ml_training_type = ml_training_type

    # OPEN_ITEMS.md section 6.2: MLTF's own real execution runtime —
    # closes the "MLTF trains (Phase 1: elided)" gap. Every training job
    # gets one, model-targeted or coordination-group-targeted alike: a
    # training run needs somewhere to actually execute regardless of
    # which kind of target it names, the same way the job row itself is
    # always created either way.
    descriptor_id = _nfo_create_execution_descriptor("TRAINING", job.training_job_id)
    job.nf_deployment_descriptor_id = descriptor_id
    job.nf_deployment_id = _nfo_instantiate_execution(descriptor_id, "TRAINING", job.training_job_id)

    if lifecycle is not None:
        if lifecycle.model_lifecycle_state == ModelLifecycleState.TRAINING:
            existing_job_id = lifecycle.training_job_id
            if existing_job_id is not None:
                orphaned = db.get(TrainingJob, existing_job_id)
                if orphaned is not None and orphaned.status == "IN_PROGRESS":
                    orphaned.status = "CANCELLED"
                    # the orphaned job's own execution runtime is abandoned
                    # right alongside it — left running otherwise.
                    _nfo_terminate_execution(orphaned.nf_deployment_id)
                    orphaned.nf_deployment_id = None
                    _sync_training_process(db, orphaned)
        else:
            _fire_model_event(db, model_id, ModelLifecycleEvent.CREATE_TRAINING)
        lifecycle.training_job_id = job.training_job_id
    db.flush()
    return job


@app.post("/training-jobs", status_code=201)
def request_training(body: RequestTrainingRequest, db: Session = Depends(get_session)):
    """RequestTraining — exactly one of modelId/modelCoordinationGroupId,
    enforced at the DB layer (exactly_one_target constraint) and checked
    here for a clean error. See `_start_training` for the lifecycle rules.
    """
    if (body.modelId is None) == (body.modelCoordinationGroupId is None):
        raise framework_error(FrameworkError.COORDINATION_GROUP_MISMATCH)
    _validate_dme_data_job_ids(body.dmeDataJobIds)
    job = _start_training(db, model_id=body.modelId, group_id=body.modelCoordinationGroupId,
                          producer_id=body.producerId, required_data=body.requiredData,
                          dme_data_job_ids=body.dmeDataJobIds, validation_criteria=body.validationCriteria,
                          notification_uri=body.notificationUri, run_id=body.runId,
                          training_dataset=body.trainingDataset, validation_dataset=body.validationDataset,
                          consumer_rapp_id=body.consumerRappId, producer_rapp_id=body.producerRappId)
    db.commit()
    return {"trainingJobId": str(job.training_job_id)}


@app.get("/training-jobs/{training_job_id}/status")
def query_training_job_status(training_job_id: uuid.UUID, db: Session = Depends(get_session)):
    job = db.get(TrainingJob, training_job_id)
    return {
        "trainingJobId": str(job.training_job_id), "status": job.status, "runId": job.run_id,
        "trainingDataset": job.training_dataset, "validationDataset": job.validation_dataset,
        "consumerRappId": job.consumer_rapp_id, "producerRappId": job.producer_rapp_id,
        "mlTrainingType": job.ml_training_type, "dmeDataJobIds": [str(i) for i in job.dme_data_job_ids],
        "outcomeArtifactDmeTypeId": str(job.outcome_artifact_dme_type_id) if job.outcome_artifact_dme_type_id else None,
        "nfDeploymentId": str(job.nf_deployment_id) if job.nf_deployment_id else None,
    }


@app.post("/training-jobs/{training_job_id}/complete")
def complete_training(training_job_id: uuid.UUID, body: CompleteJobRequest, db: Session = Depends(get_session)):
    """OPEN_ITEMS.md section 6.5, closed: unlike Validation/Emulation
    (which already had their own dedicated `.../complete` routes),
    Training's own completion only ever went through the generic
    `POST /models/{id}/advance(TRAINING_COMPLETE)` route — real for the
    model's own lifecycle state, but with no way to record what the run
    actually produced or notify anyone. This is additive, not a
    replacement: that generic route still fires
    TRAINING_COMPLETE/TRAINING_FAILED directly for any caller that
    doesn't need job-level bookkeeping. This route brings Training up to
    the same real request/tracking-aggregate parity Validation/Emulation
    already had — job status (TrainingJob's own FINISHED/FAILED
    vocabulary, not COMPLETED), the outcome artifact, and a best-effort
    completion notification, plus the same model-lifecycle transition
    the generic route fires (skipped for a coordination-group-targeted
    job, which has no single model to advance — the same asymmetry
    `request_training` itself already has).
    """
    job = db.get(TrainingJob, training_job_id)
    if job is None:
        raise framework_error(FrameworkError.TRAINING_JOB_NOT_FOUND, detail="no such training job")
    job.status = "FINISHED" if body.succeeded else "FAILED"
    job.model_metrics = body.metrics
    job.outcome_artifact_dme_type_id = body.outcomeArtifactDmeTypeId
    # OPEN_ITEMS.md section 6.2: the run is done — its execution runtime
    # is torn down right alongside it, not left running indefinitely.
    _nfo_terminate_execution(job.nf_deployment_id)
    job.nf_deployment_id = None
    if job.model_id is not None:
        event = ModelLifecycleEvent.TRAINING_COMPLETE if body.succeeded else ModelLifecycleEvent.TRAINING_FAILED
        _fire_model_event(db, job.model_id, event)
    _sync_training_process(db, job)
    _write_training_report(db, job, body)
    if job.ml_update_process_id is not None:
        from .nrm import advance_ml_update_process
        advance_ml_update_process(db, job.ml_update_process_id)
    db.commit()
    _notify_job_completion(job.notification_uri, "TRAINING", job.training_job_id, body.succeeded,
                            job.outcome_artifact_dme_type_id, job.model_metrics)
    return _training_job_view(job)


@app.delete("/training-jobs/{training_job_id}", status_code=204)
def cancel_training(training_job_id: uuid.UUID, db: Session = Depends(get_session)):
    job = db.get(TrainingJob, training_job_id)
    if job is not None:
        _cancel_training_job(db, job)
        db.commit()


def _cancel_training_job(db: Session, job: TrainingJob) -> None:
    job.status = "CANCELLED"
    job.cancel_request = True
    _nfo_terminate_execution(job.nf_deployment_id)
    job.nf_deployment_id = None
    _sync_training_process(db, job)
    if job.ml_update_process_id is not None:
        from .nrm import advance_ml_update_process
        advance_ml_update_process(db, job.ml_update_process_id)


@app.post("/training-jobs/{training_job_id}/suspend")
def suspend_training(training_job_id: uuid.UUID, db: Session = Depends(get_session)):
    """SPEC_AUDIT.md's AI/ML Workflow section item 6: TrainingJob had no
    suspend concept at all, only a hard cancel. This is deliberately a
    plain status flip, not a third state machine — the two real FSMs
    Wave 2 built (ModelLifecycleState/RuntimeLifecycleState) operate one
    level up and are untouched by a job-level suspend/resume, the same
    way job.status's other transitions (IN_PROGRESS -> FINISHED/FAILED/
    CANCELLED) already don't reach into ModelLifecycleState either —
    only an explicit `POST /models/{id}/advance` call does that. Only
    legal from IN_PROGRESS, matching the reference's own request-flag
    semantics (a suspend request only makes sense against an in-flight job).
    """
    job = db.get(TrainingJob, training_job_id)
    if job is None:
        raise framework_error(FrameworkError.TRAINING_JOB_NOT_FOUND, detail="no such training job")
    if job.status != "IN_PROGRESS":
        raise framework_error(FrameworkError.TRAINING_JOB_ILLEGAL_TRANSITION,
                               detail=f"cannot suspend a training job in status {job.status}")
    job.status = "SUSPENDED"
    job.suspend_request = True
    _sync_training_process(db, job)
    db.commit()
    return {"trainingJobId": str(job.training_job_id), "status": job.status}


@app.post("/training-jobs/{training_job_id}/resume")
def resume_training(training_job_id: uuid.UUID, db: Session = Depends(get_session)):
    job = db.get(TrainingJob, training_job_id)
    if job is None:
        raise framework_error(FrameworkError.TRAINING_JOB_NOT_FOUND, detail="no such training job")
    if job.status != "SUSPENDED":
        raise framework_error(FrameworkError.TRAINING_JOB_ILLEGAL_TRANSITION,
                               detail=f"cannot resume a training job in status {job.status}")
    job.status = "IN_PROGRESS"
    job.suspend_request = False
    _sync_training_process(db, job)
    db.commit()
    return {"trainingJobId": str(job.training_job_id), "status": job.status}


@app.post("/training-jobs/{training_job_id}/model-metrics")
def update_training_job_model_metrics(training_job_id: uuid.UUID, model_metrics: dict, db: Session = Depends(get_session)):
    """OPEN_ITEMS.md section 5: TrainingJob had no metrics-writeback
    endpoint at all. The reference's own
    POST /training-jobs/update-model-metrics/<id> (trainingjob_controller.py)
    replaces model_metrics wholesale, not a merge — same here.
    """
    job = db.get(TrainingJob, training_job_id)
    if job is None:
        raise framework_error(FrameworkError.TRAINING_JOB_NOT_FOUND, detail="no such training job")
    job.model_metrics = model_metrics
    db.commit()
    return {"trainingJobId": str(job.training_job_id), "modelMetrics": job.model_metrics}


@app.get("/training-jobs/{training_job_id}/model-metrics")
def get_training_job_model_metrics(training_job_id: uuid.UUID, db: Session = Depends(get_session)):
    job = db.get(TrainingJob, training_job_id)
    if job is None:
        raise framework_error(FrameworkError.TRAINING_JOB_NOT_FOUND, detail="no such training job")
    return job.model_metrics or {}


@app.get("/training-jobs")
def list_training_jobs(model_id: uuid.UUID | None = None, status: str | None = None, limit: int = PageLimit,
                        offset: int = PageOffset, db: Session = Depends(get_session)):
    stmt = select(TrainingJob)
    if model_id:
        stmt = stmt.where(TrainingJob.model_id == model_id)
    if status:
        stmt = stmt.where(TrainingJob.status == status)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [_training_job_view(j) for j in page["items"]]}


# ---------------------------------------------------------------- Validation

def _start_validation(db: Session, *, model_id: uuid.UUID | None, group_id: uuid.UUID | None, producer_id: str,
                      **job_fields) -> ValidationJob:
    """CreateValidation (AIMGF_OWNERSHIP.md's own request list) — the one
    place a validation (TS 28.105: testing) run starts, shared by
    `POST /validation-jobs` and `POST /ml-testing-requests`.

    A model-targeted run requires the model to have finished training
    (TRAINED) AND an operator to have already fired APPROVE_TRAINING
    (OPEN_ITEMS.md section 6.1) — the state check alone isn't the gate.
    A coordination-group-targeted run (Wave 4, MLTestingRequest's
    mLModelCoordinationGroupRef) tests the group as a unit and, exactly
    like a group-targeted TrainingJob, drives no single member's lifecycle.
    """
    if (model_id is None) == (group_id is None):
        raise framework_error(FrameworkError.COORDINATION_GROUP_MISMATCH)
    if model_id is not None:
        _get_model(model_id)
        lifecycle = _get_or_create_lifecycle(db, model_id)
        if lifecycle.model_lifecycle_state != ModelLifecycleState.TRAINED:
            raise framework_error(FrameworkError.MODEL_NOT_CERTIFIED,
                                   detail=f"cannot request validation for a model in state {lifecycle.model_lifecycle_state}")
        if not lifecycle.training_approved:
            raise framework_error(FrameworkError.TRAINING_NOT_APPROVED,
                                   detail="an operator must advance(APPROVE_TRAINING, decidedBy) before validation can start")
    job = ValidationJob(model_id=model_id, model_coordination_group_id=group_id, producer_id=producer_id,
                         status="RUNNING", **job_fields)
    db.add(job)
    db.flush()
    # OPEN_ITEMS.md section 6.2: MLVF's own real execution runtime.
    descriptor_id = _nfo_create_execution_descriptor("VALIDATION", job.validation_job_id)
    job.nf_deployment_descriptor_id = descriptor_id
    job.nf_deployment_id = _nfo_instantiate_execution(descriptor_id, "VALIDATION", job.validation_job_id)
    if model_id is not None:
        _fire_model_event(db, model_id, ModelLifecycleEvent.CREATE_VALIDATION)
    db.flush()
    return job


@app.post("/validation-jobs", status_code=201)
def request_validation(body: RequestValidationRequest, db: Session = Depends(get_session)):
    """CreateValidation — see `_start_validation`."""
    job = _start_validation(db, model_id=body.modelId, group_id=None, producer_id=body.producerId,
                            training_job_id=body.trainingJobId, validation_criteria=body.validationCriteria,
                            notification_uri=body.notificationUri)
    db.commit()
    return {"validationJobId": str(job.validation_job_id)}


@app.get("/validation-jobs/{validation_job_id}/status")
def query_validation_job_status(validation_job_id: uuid.UUID, db: Session = Depends(get_session)):
    job = db.get(ValidationJob, validation_job_id)
    if job is None:
        raise framework_error(FrameworkError.VALIDATION_JOB_NOT_FOUND, detail="no such validation job")
    return _validation_job_view(job)


@app.post("/validation-jobs/{validation_job_id}/complete")
def complete_validation(validation_job_id: uuid.UUID, body: CompleteJobRequest, db: Session = Depends(get_session)):
    job = db.get(ValidationJob, validation_job_id)
    if job is None:
        raise framework_error(FrameworkError.VALIDATION_JOB_NOT_FOUND, detail="no such validation job")
    if job.status not in ("RUNNING", "SUSPENDED"):
        raise framework_error(FrameworkError.TRAINING_JOB_ILLEGAL_TRANSITION,
                               detail=f"cannot complete a validation job in status {job.status}")
    job.status = "COMPLETED" if body.succeeded else "FAILED"
    job.metrics = body.metrics
    job.outcome_artifact_dme_type_id = body.outcomeArtifactDmeTypeId
    # OPEN_ITEMS.md section 6.2: the run is done — tear down its runtime.
    _nfo_terminate_execution(job.nf_deployment_id)
    job.nf_deployment_id = None
    if job.model_id is not None:
        event = ModelLifecycleEvent.VALIDATION_COMPLETE if body.succeeded else ModelLifecycleEvent.VALIDATION_FAILED
        _fire_model_event(db, job.model_id, event)
    # Wave 4 — TS 28.105 MLTestingReport.
    db.add(MLTestingReport(validation_job_id=job.validation_job_id, ml_testing_function_id=job.ml_testing_function_id,
                           model_performance_testing=ts28105.dump(body.modelPerformanceTesting),
                           ml_testing_result="PASSED" if body.succeeded else "FAILED"))
    db.commit()
    _notify_job_completion(job.notification_uri, "VALIDATION", job.validation_job_id, body.succeeded,
                            job.outcome_artifact_dme_type_id, job.metrics)
    return _validation_job_view(job)


@app.get("/validation-jobs")
def list_validation_jobs(model_id: uuid.UUID | None = None, status: str | None = None, limit: int = PageLimit,
                          offset: int = PageOffset, db: Session = Depends(get_session)):
    stmt = select(ValidationJob)
    if model_id:
        stmt = stmt.where(ValidationJob.model_id == model_id)
    if status:
        stmt = stmt.where(ValidationJob.status == status)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [_validation_job_view(j) for j in page["items"]]}


# ---------------------------------------------------------------- Emulation

@app.post("/emulation-jobs", status_code=201)
def request_emulation(body: RequestEmulationRequest, db: Session = Depends(get_session)):
    """CreateEmulation — new this wave, split out from Wave 1's flat
    VALIDATION_COMPLETE -> EMULATED transition the same way ValidationJob
    is. Requires the model to have passed validation (VALIDATED) AND an
    operator to have already fired APPROVE_VALIDATION (OPEN_ITEMS.md
    section 6.1) — the same gate shape as request_validation's own.
    """
    _get_model(body.modelId)
    lifecycle = _get_or_create_lifecycle(db, body.modelId)
    if lifecycle.model_lifecycle_state != ModelLifecycleState.VALIDATED:
        raise framework_error(FrameworkError.MODEL_NOT_CERTIFIED,
                               detail=f"cannot request emulation for a model in state {lifecycle.model_lifecycle_state}")
    if not lifecycle.validation_approved:
        raise framework_error(FrameworkError.VALIDATION_NOT_APPROVED,
                               detail="an operator must advance(APPROVE_VALIDATION, decidedBy) before emulation can start")
    if body.aIMLInferenceEmulationFunctionRef is not None:
        from .models import AIMLInferenceEmulationFunction
        if db.get(AIMLInferenceEmulationFunction, body.aIMLInferenceEmulationFunctionRef) is None:
            raise framework_error(FrameworkError.NRM_OBJECT_NOT_FOUND, detail="no such AIMLInferenceEmulationFunction")
    job = EmulationJob(model_id=body.modelId, producer_id=body.producerId, emulation_criteria=body.emulationCriteria,
                        status="RUNNING", notification_uri=body.notificationUri,
                        aiml_inference_emulation_function_id=body.aIMLInferenceEmulationFunctionRef)
    db.add(job)
    db.flush()
    # OPEN_ITEMS.md section 6.2: MLEF's own real execution runtime.
    descriptor_id = _nfo_create_execution_descriptor("EMULATION", job.emulation_job_id)
    job.nf_deployment_descriptor_id = descriptor_id
    job.nf_deployment_id = _nfo_instantiate_execution(descriptor_id, "EMULATION", job.emulation_job_id)
    _fire_model_event(db, body.modelId, ModelLifecycleEvent.CREATE_EMULATION)
    db.commit()
    return {"emulationJobId": str(job.emulation_job_id)}


@app.get("/emulation-jobs/{emulation_job_id}/status")
def query_emulation_job_status(emulation_job_id: uuid.UUID, db: Session = Depends(get_session)):
    job = db.get(EmulationJob, emulation_job_id)
    if job is None:
        raise framework_error(FrameworkError.EMULATION_JOB_NOT_FOUND, detail="no such emulation job")
    return _emulation_job_view(job)


@app.post("/emulation-jobs/{emulation_job_id}/complete")
def complete_emulation(emulation_job_id: uuid.UUID, body: CompleteJobRequest, db: Session = Depends(get_session)):
    job = db.get(EmulationJob, emulation_job_id)
    if job is None:
        raise framework_error(FrameworkError.EMULATION_JOB_NOT_FOUND, detail="no such emulation job")
    job.status = "COMPLETED" if body.succeeded else "FAILED"
    job.metrics = body.metrics
    job.outcome_artifact_dme_type_id = body.outcomeArtifactDmeTypeId
    # OPEN_ITEMS.md section 6.2: the run is done — tear down its runtime.
    _nfo_terminate_execution(job.nf_deployment_id)
    job.nf_deployment_id = None
    event = ModelLifecycleEvent.EMULATION_COMPLETE if body.succeeded else ModelLifecycleEvent.EMULATION_FAILED
    _fire_model_event(db, job.model_id, event)
    # Wave 4 — TS 28.105: an emulation run's result is an
    # AIMLInferenceReport under its AIMLInferenceEmulationFunction.
    if body.succeeded:
        db.add(AIMLInferenceReport(aiml_inference_emulation_function_id=job.aiml_inference_emulation_function_id,
                                   emulation_job_id=job.emulation_job_id,
                                   inference_outputs=ts28105.dump(body.inferenceOutputs) or [],
                                   potential_impact_info=ts28105.dump(body.potentialImpactInfo),
                                   ml_model_refs=[str(job.model_id)]))
    db.commit()
    _notify_job_completion(job.notification_uri, "EMULATION", job.emulation_job_id, body.succeeded,
                            job.outcome_artifact_dme_type_id, job.metrics)
    return _emulation_job_view(job)


@app.get("/emulation-jobs")
def list_emulation_jobs(model_id: uuid.UUID | None = None, status: str | None = None, limit: int = PageLimit,
                         offset: int = PageOffset, db: Session = Depends(get_session)):
    stmt = select(EmulationJob)
    if model_id:
        stmt = stmt.where(EmulationJob.model_id == model_id)
    if status:
        stmt = stmt.where(EmulationJob.status == status)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [_emulation_job_view(j) for j in page["items"]]}


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
def list_model_lifecycles(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    """(GUI) Every model AIMgF has ever been asked to act on — the Models
    table's own State/Node-groups columns would otherwise be an
    N-model-lifecycle-fetches-per-page-load problem. A model MLMR knows
    about that AIMgF has never touched yet simply has no row here (still
    REGISTERED/NOT_DEPLOYED in truth, per `_get_or_create_lifecycle`'s own
    lazy-initialization default) — the GUI falls back to that same
    default for a model missing from this list. Wave 3: paginated like
    every other list route now, so the GUI's own "give me the whole
    picture" use case fetches a large enough page rather than assuming
    an unbounded response.
    """
    page = paginate(db, select(ModelLifecycle), limit, offset)
    return {**page, "items": [_lifecycle_view(l) for l in page["items"]]}


@app.get("/models/{model_id}/governance-history")
def list_governance_history(model_id: uuid.UUID, limit: int = PageLimit, offset: int = PageOffset,
                             db: Session = Depends(get_session)):
    stmt = select(CertificationRecord).where(CertificationRecord.model_id == model_id).order_by(CertificationRecord.decided_at)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [_certification_record_view(r) for r in page["items"]]}


@app.get("/models/{model_id}/lifecycle-history")
def list_lifecycle_history(model_id: uuid.UUID, fsm: str | None = None, limit: int = PageLimit,
                            offset: int = PageOffset, db: Session = Depends(get_session)):
    stmt = select(LifecycleTransition).where(LifecycleTransition.model_id == model_id)
    if fsm:
        stmt = stmt.where(LifecycleTransition.fsm == fsm)
    page = paginate(db, stmt.order_by(LifecycleTransition.occurred_at), limit, offset)
    return {**page, "items": [{"fsm": r.fsm, "fromState": r.from_state, "toState": r.to_state, "event": r.event,
                                "occurredAt": r.occurred_at.isoformat()} for r in page["items"]]}


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
    lifecycle = _deploy_runtime(db, model_id)
    db.commit()
    return _lifecycle_view(lifecycle)


def _deploy_runtime(db: Session, model_id: uuid.UUID) -> ModelLifecycle:
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
    return lifecycle


def _activate_runtime(db: Session, model_id: uuid.UUID) -> ModelLifecycle:
    _fire_runtime_event(db, model_id, RuntimeLifecycleEvent.ACTIVATE)
    return _fire_runtime_event(db, model_id, RuntimeLifecycleEvent.ACTIVATION_COMPLETE)


@app.post("/models/{model_id}/runtime/activate")
def activate_model_runtime(model_id: uuid.UUID, db: Session = Depends(get_session)):
    """Marks the runtime as ready to accept inference (request_inference's
    own gate). Local-only: NFO's own deployment is already RUNNING once
    `deploy` returns (Phase 1: instantiate completes synchronously, same
    elision as elsewhere in this build) — ACTIVATE is AIMgF's own
    decision about whether traffic should be sent yet, not a further NFO
    call.
    """
    lifecycle = _activate_runtime(db, model_id)
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
def request_inference(model_id: uuid.UUID, notification_destination: str | None = None,
                      aiml_inference_function_id: uuid.UUID | None = None, consumer_ref: str | None = None,
                      db: Session = Depends(get_session)):
    """RequestInference — MLEF-hosted (AI/ML Workflow LLD section 3).
    Gated on RuntimeLifecycleState.ACTIVE (a serving question), not
    ModelLifecycleState — a PROMOTED-but-not-yet-deployed model, or one
    whose runtime is mid-SCALING, can't serve inference either way.
    """
    _get_model(model_id)
    lifecycle = _get_or_create_lifecycle(db, model_id)
    if lifecycle.runtime_lifecycle_state != RuntimeLifecycleState.ACTIVE:
        raise framework_error(FrameworkError.INFERENCE_MODEL_NOT_ACTIVE)
    # Wave 4 — TS 28.105 AIMLInferenceFunction: when named, it must be
    # ACTIVATED and actually have this model loaded (MLModelLoadingProcess).
    if aiml_inference_function_id is not None:
        function = db.get(AIMLInferenceFunction, aiml_inference_function_id)
        if function is None:
            raise framework_error(FrameworkError.NRM_OBJECT_NOT_FOUND, detail="no such AIMLInferenceFunction")
        if function.activation_status != "ACTIVATED":
            raise framework_error(FrameworkError.INFERENCE_FUNCTION_NOT_ACTIVATED)
        if str(model_id) not in function.ml_model_refs:
            raise framework_error(FrameworkError.MODEL_NOT_LOADED,
                                   detail=f"model {model_id} is not loaded on AIMLInferenceFunction {aiml_inference_function_id}")
    # OPEN_ITEMS.md section 6.2: MLIF's own execution runtime is the
    # model's already-live serving deployment (real since this state is
    # only reachable once deploy_model_runtime's own NFO call succeeded)
    # — a reference, not a new NFO call. See InferenceJob's own docstring
    # for why this differs from Training/Validation/Emulation.
    job = InferenceJob(model_id=model_id, status=InferenceState.RUNNING, notification_destination=notification_destination,
                        nf_deployment_id=lifecycle.nf_deployment_id, aiml_inference_function_id=aiml_inference_function_id,
                        consumer_ref=consumer_ref)
    db.add(job)
    db.commit()
    return {"inferenceJobId": str(job.inference_job_id)}


@app.get("/inference-jobs/{inference_job_id}/status")
def query_inference_status(inference_job_id: uuid.UUID, db: Session = Depends(get_session)):
    job = db.get(InferenceJob, inference_job_id)
    return {"inferenceJobId": str(job.inference_job_id), "status": job.status,
            "nfDeploymentId": str(job.nf_deployment_id) if job.nf_deployment_id else None}


@app.post("/inference-jobs/{inference_job_id}/resolve")
def resolve_inference(inference_job_id: uuid.UUID, succeeded: bool, body: ResolveInferenceRequest | None = None,
                      db: Session = Depends(get_session)):
    job = db.get(InferenceJob, inference_job_id)
    job.status = INFERENCE_JOB_FSM.fire(InferenceState(job.status), InferenceEvent.COMPLETE if succeeded else InferenceEvent.FAIL)
    # Wave 4 — TS 28.105 AIMLInferenceReport for a successful inference.
    # The bulk result is still pulled via DME against the model's
    # outputDataType (section 3); this is the report the NRM exposes.
    report_id = None
    if succeeded:
        body = body or ResolveInferenceRequest()
        report = AIMLInferenceReport(aiml_inference_function_id=job.aiml_inference_function_id,
                                     inference_job_id=job.inference_job_id,
                                     inference_outputs=ts28105.dump(body.inferenceOutputs) or [],
                                     potential_impact_info=ts28105.dump(body.potentialImpactInfo),
                                     ml_model_refs=[str(job.model_id)])
        db.add(report)
        db.flush()
        report_id = str(report.aiml_inference_report_id)
    db.commit()
    return {"inferenceJobId": str(job.inference_job_id), "status": job.status, "aIMLInferenceReportId": report_id}


@app.get("/inference-jobs")
def list_inference_jobs(model_id: uuid.UUID | None = None, status: str | None = None, limit: int = PageLimit,
                         offset: int = PageOffset, db: Session = Depends(get_session)):
    stmt = select(InferenceJob)
    if model_id:
        stmt = stmt.where(InferenceJob.model_id == model_id)
    if status:
        stmt = stmt.where(InferenceJob.status == status)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [{"inferenceJobId": str(j.inference_job_id), "modelId": str(j.model_id), "status": j.status,
             "notificationDestination": j.notification_destination,
             "nfDeploymentId": str(j.nf_deployment_id) if j.nf_deployment_id else None} for j in page["items"]]}


# ---------------------------------------------------------------- MLMF performance monitoring

@app.post("/mlmf/subscriptions", status_code=201)
def subscribe_performance_monitoring(model_id: uuid.UUID, metric_types: list[str], dme_type_id: uuid.UUID, guard_kpi_floor: dict | None = None,
                                      notification_destination: str | None = None, db: Session = Depends(get_session)):
    """MLMF — new sub-function, AI/ML Workflow LLD section 2. Distinct
    domain from RAN Analytics' MDAF (model performance, not RAN behavior).

    SPEC_AUDIT.md's `MLMFSubscription` finding, closed: `notification_destination`
    (optional, matching every other subscription-shaped resource's own
    permissive shape — a purely poll-based consumer may still omit it).
    """
    sub = MLMFSubscription(model_id=model_id, metric_types=metric_types, dme_type_id=dme_type_id, guard_kpi_floor=guard_kpi_floor,
                            notification_destination=notification_destination)
    db.add(sub)
    db.commit()
    return {"subscriptionId": str(sub.subscription_id)}


@app.delete("/mlmf/subscriptions/{subscription_id}", status_code=204)
def unsubscribe_performance_monitoring(subscription_id: uuid.UUID, db: Session = Depends(get_session)):
    """SPEC_AUDIT.md's `MLMFSubscription` finding, closed: previously
    this subscription could only be created and read, never torn down —
    idempotent, matching every other subscription-shaped resource's own
    unsubscribe route (DME/MDAF/A1-Related/Intent Service).
    """
    sub = db.get(MLMFSubscription, subscription_id)
    if sub is not None:
        db.delete(sub)
        db.commit()


def _find_coordination_group_for_model(model_id: uuid.UUID) -> dict | None:
    resp = _r1.get("/mlmr/coordination-groups")
    groups = resp.json()["items"] if resp.status_code == 200 else []
    return next((g for g in groups if str(model_id) in set(g["memberModelIds"])), None)


@app.post("/mlmf/subscriptions/{subscription_id}/reports")
def report_performance(subscription_id: uuid.UUID, metrics: dict, db: Session = Depends(get_session)):
    """`docs/call-flows/13-mlmf-subscription-lifecycle.md`'s own gap,
    closed: a report against an unsubscribed or never-existed
    subscription used to raise an unhandled `AttributeError` (a bare
    500) — `sub` was read directly with no null-check. Now a clean
    404, matching every comparable cross-reference elsewhere in this
    build.
    """
    sub = db.get(MLMFSubscription, subscription_id)
    if sub is None:
        raise framework_error(FrameworkError.MLMF_SUBSCRIPTION_NOT_FOUND, detail="no such MLMF subscription")
    breached = bool(sub.guard_kpi_floor) and any(metrics.get(k, 0) < v for k, v in (sub.guard_kpi_floor or {}).items())
    report = PerformanceReport(subscription_id=subscription_id, metrics=metrics, breached_floor=breached)
    db.add(report)
    db.commit()

    # SPEC_AUDIT.md's `MLMFSubscription` finding, closed: best-effort,
    # same pattern as every other subscription notification in this
    # build — an unreachable subscriber never fails the report call
    # that triggered it.
    post_webhook(sub.notification_destination, json={
        "reportId": str(report.id), "modelId": str(sub.model_id), "metrics": metrics, "breachedFloor": breached,
    }, timeout=2.0)

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
def list_performance_subscriptions(model_id: uuid.UUID | None = None, limit: int = PageLimit, offset: int = PageOffset,
                                    db: Session = Depends(get_session)):
    stmt = select(MLMFSubscription)
    if model_id:
        stmt = stmt.where(MLMFSubscription.model_id == model_id)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [{"subscriptionId": str(sub.subscription_id), "modelId": str(sub.model_id), "metricTypes": sub.metric_types,
             "dmeTypeId": str(sub.dme_type_id), "guardKpiFloor": sub.guard_kpi_floor,
             "notificationDestination": sub.notification_destination} for sub in page["items"]]}


@app.get("/mlmf/subscriptions/{subscription_id}/reports")
def list_performance_reports(subscription_id: uuid.UUID, limit: int = PageLimit, offset: int = PageOffset,
                              db: Session = Depends(get_session)):
    if db.get(MLMFSubscription, subscription_id) is None:
        raise framework_error(FrameworkError.MLMF_SUBSCRIPTION_NOT_FOUND, detail="no such MLMF subscription")
    stmt = select(PerformanceReport).where(PerformanceReport.subscription_id == subscription_id).order_by(PerformanceReport.reported_at.desc())
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [_performance_report_view(r) for r in page["items"]]}


@app.get("/mlmf/reports")
def list_recent_performance_reports(breached_only: bool = False, limit: int = PageLimit, offset: int = PageOffset,
                                     db: Session = Depends(get_session)):
    stmt = select(PerformanceReport)
    if breached_only:
        stmt = stmt.where(PerformanceReport.breached_floor.is_(True))
    page = paginate(db, stmt.order_by(PerformanceReport.reported_at.desc()), limit, offset)
    return {**page, "items": [_performance_report_view(r) for r in page["items"]]}


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
        # OPEN_ITEMS.md section 6.2 / Wave 4: the same start path (NFO
        # runtime, MLTrainingProcess, CREATE_TRAINING) a directly-requested
        # retrain gets via request_training.
        _start_training(db, model_id=member_id, group_id=None, producer_id="aimgf:group-retrain",
                        ml_training_type="RE_TRAINING")
        retrained_model_ids.append(member_id)
    db.commit()
    return retrained_model_ids


def _write_training_report(db: Session, job: TrainingJob, body: CompleteJobRequest) -> MLTrainingReport:
    """Wave 4 — TS 28.105 MLTrainingReport, written on every training
    completion. lastTrainingRef chains to the same target's previous
    report; mLModelGeneratedRef is only set when the run succeeded.
    """
    previous = None
    if job.model_id is not None or job.model_coordination_group_id is not None:
        stmt = select(MLTrainingReport).join(TrainingJob, MLTrainingReport.training_job_id == TrainingJob.training_job_id)
        if job.model_id is not None:
            stmt = stmt.where(TrainingJob.model_id == job.model_id)
        else:
            stmt = stmt.where(TrainingJob.model_coordination_group_id == job.model_coordination_group_id)
        previous = db.scalars(stmt.order_by(MLTrainingReport.created_at.desc())).first()
    report = MLTrainingReport(
        training_job_id=job.training_job_id, ml_training_function_id=job.ml_training_function_id,
        used_consumer_training_data=body.usedConsumerTrainingData,
        model_confidence_indication=body.modelConfidenceIndication,
        model_performance_training=ts28105.dump(body.modelPerformanceTraining),
        model_performance_validation=ts28105.dump(body.modelPerformanceValidation),
        data_ratio_training_and_validation=body.dataRatioTrainingAndValidation,
        are_new_training_data_used=body.areNewTrainingDataUsed,
        fl_report_per_client=ts28105.dump(body.fLReportPerClient),
        last_training_report_id=previous.ml_training_report_id if previous else None,
        ml_model_generated_ref=job.model_id if body.succeeded else None,
        ml_model_coordination_group_generated_ref=job.model_coordination_group_id if body.succeeded else None,
    )
    db.add(report)
    db.flush()
    return report


def _training_job_view(j: TrainingJob) -> dict:
    return {"trainingJobId": str(j.training_job_id), "modelId": str(j.model_id) if j.model_id else None,
            "modelCoordinationGroupId": str(j.model_coordination_group_id) if j.model_coordination_group_id else None,
            "producerId": j.producer_id, "status": j.status, "runId": j.run_id,
            "trainingDataset": j.training_dataset, "validationDataset": j.validation_dataset,
            "modelMetrics": j.model_metrics, "mlTrainingType": j.ml_training_type,
            "outcomeArtifactDmeTypeId": str(j.outcome_artifact_dme_type_id) if j.outcome_artifact_dme_type_id else None,
            "nfDeploymentId": str(j.nf_deployment_id) if j.nf_deployment_id else None}


def _validation_job_view(j: ValidationJob) -> dict:
    return {"validationJobId": str(j.validation_job_id), "modelId": str(j.model_id) if j.model_id else None,
            "modelCoordinationGroupId": str(j.model_coordination_group_id) if j.model_coordination_group_id else None,
            "trainingJobId": str(j.training_job_id) if j.training_job_id else None,
            "producerId": j.producer_id, "validationCriteria": j.validation_criteria or {},
            "status": j.status, "metrics": j.metrics or {},
            "outcomeArtifactDmeTypeId": str(j.outcome_artifact_dme_type_id) if j.outcome_artifact_dme_type_id else None,
            "nfDeploymentId": str(j.nf_deployment_id) if j.nf_deployment_id else None}


def _emulation_job_view(j: EmulationJob) -> dict:
    return {"emulationJobId": str(j.emulation_job_id), "modelId": str(j.model_id), "producerId": j.producer_id,
            "aIMLInferenceEmulationFunctionRef": str(j.aiml_inference_emulation_function_id) if j.aiml_inference_emulation_function_id else None,
            "emulationCriteria": j.emulation_criteria or {}, "status": j.status, "metrics": j.metrics or {},
            "outcomeArtifactDmeTypeId": str(j.outcome_artifact_dme_type_id) if j.outcome_artifact_dme_type_id else None,
            "nfDeploymentId": str(j.nf_deployment_id) if j.nf_deployment_id else None}


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
        "trainingApproved": l.training_approved, "validationApproved": l.validation_approved,
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
def list_feature_groups(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    page = paginate(db, select(FeatureGroup), limit, offset)
    return {**page, "items": [_feature_group_view(g) for g in page["items"]]}


def _feature_group_view(g: FeatureGroup) -> dict:
    return {
        "featureGroupId": str(g.feature_group_id), "featureGroupName": g.feature_group_name,
        "featureList": g.feature_list, "datalakeSource": g.datalake_source, "host": g.host, "port": g.port,
        "bucket": g.bucket, "token": g.token, "dbOrg": g.db_org, "measurement": g.measurement,
        "enableDme": g.enable_dme, "measuredObjClass": g.measured_obj_class, "dmePort": g.dme_port,
        "sourceName": g.source_name,
    }


# ---------------------------------------------------------------- Wave 4: TS 28.105 NRM resources
# Imported last: app/nrm.py reuses the helpers above (_start_training,
# _start_validation, _deploy_runtime, ...), so it can only be loaded once
# they exist.
from .nrm import router as _nrm_router  # noqa: E402

app.include_router(_nrm_router)
