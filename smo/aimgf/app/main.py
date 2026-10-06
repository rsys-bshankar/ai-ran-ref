"""AIMgF (AI Management Function) — TS 28.105 AI/ML NRM realization.

Wave 2 of the AI Platform Service Decomposition: the full eight-aggregate
domain model and two real state machines (see docs/architecture/
docs/ARCHITECTURE.md and docs/ARCHITECTURE.md (AIMgF)), deepening
Wave 1's structural split of the former flat `ai-ml-workflow/` module.

  - ModelLifecycle  — a model's own identity/certification path
                      (training -> validation -> emulation -> governance
                      -> deprecation/retirement). AIMgF's own storage now
                      (`.models.ModelLifecycle`), not MLMR's row: Wave 1's
                      `PATCH /mlmr/models/{id}/lifecycle` is gone —
                      `docs/ARCHITECTURE.md`'s
                      own "Lifecycle state: AIMgF ✅, MLMR ❌" is now
                      actually true, not just documented.
  - RuntimeLifecycle — a model's serving existence once PROMOTED, jointly
                      owned with NFO: AIMgF now really calls NFO's
                      descriptor/instantiate/scale/terminate routes
                      (`_nfo_*` below) for "request runtime creation/
                      termination/scaling" (docs/ARCHITECTURE.md's AIMgF list),
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

import datetime
import os
import re
import uuid
from typing import Any, Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from smo_shared.logconfig import install_logging
from smo_shared.metrics import install_metrics
from smo_shared.health import database_check, install_health, sme_token_check
from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.r1_client import R1Client
from smo_shared.statemachine import IllegalTransition
from smo_shared.openapi_security import apply_r1_gateway_security
from smo_shared.correlation import apply_correlation_id
from smo_shared.pagination import PageLimit, PageOffset, paginate
from smo_shared.outbox import enqueue
from smo_shared.versioning import install_concurrency_handler
from smo_shared.idempotency import idempotent

from .models import (
    AIMLInferenceEmulationFunction, AIMLInferenceFunction, AIMLInferenceReport, CertificationRecord, EmulationJob, FeatureGroup, InferenceJob,
    LifecycleTransition, MLMFSubscription, MLTestingReport, MLTrainingFunction, MLTrainingProcess, MLTrainingReport,
    ModelLifecycle, PerformanceReport, TRAINING_STEPS, TrainingJob, ValidationJob,
)
from . import ts28105
from .statemachine import (
    ADVANCEABLE_EVENTS, END_OF_LIFE_STATES, GOVERNANCE_EVENTS, INFERENCE_JOB_FSM, MODEL_LIFECYCLE_FSM, RUNTIME_LIFECYCLE_FSM,
    TRAINABLE_STATES, InferenceEvent, InferenceState, ModelLifecycleEvent, ModelLifecycleState, RuntimeLifecycleEvent,
    RuntimeLifecycleState, should_trigger_group_retrain,
)

app = FastAPI(title="AIMgF")
install_logging(app)  # structured JSON logs and one access-log line per request (PR-OBS-1)
install_metrics(app)  # /metrics and request count/latency series (PR-OBS-2)
install_concurrency_handler(app)  # a stale write (PR-ST-2) is a 409, not a 500
apply_r1_gateway_security(app)
apply_correlation_id(app)

_r1 = R1Client()


install_health(app, checks=[database_check, sme_token_check])  # /live, /ready and the /health alias (PR-ST-7)


def _get_model_or_none(model_id: uuid.UUID) -> dict | None:
    resp = _r1.get(f"/mlmr/models/{model_id}")
    return resp.json() if resp.status_code == 200 else None


def _get_model(model_id: uuid.UUID) -> dict:
    model = _get_model_or_none(model_id)
    if model is None:
        raise framework_error(FrameworkError.MODEL_NOT_FOUND, detail="no such model")
    return model


def _record_phase(model_id: uuid.UUID, phase: str, *, training_info: dict | None = None) -> None:
    """SA-MLMR-7: write the model's TS 29.482 `phaseInfo` back to MLMR (its
    `phase`, and the training lineage: `trainingInfo.baseModelId`, `dataSources`).
    Best-effort, like every notification here: MLMR being unreachable never
    fails a training run."""
    body: dict = {"phase": phase}
    if training_info:
        body["trainingInfo"] = training_info
    try:
        _r1.patch(f"/mlmr/models/{model_id}/phase-info", json=body)
    except Exception:  # noqa: BLE001, S110 — a transport failure only loses the lineage record
        pass


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
    docs/ARCHITECTURE.md's AIMgF Governance list.
    """
    if event in GOVERNANCE_EVENTS and decided_by is None:
        raise framework_error(FrameworkError.GOVERNANCE_DECIDER_REQUIRED, detail=f"{event} requires decidedBy")
    lifecycle = _get_or_create_lifecycle(db, model_id)
    from_state = ModelLifecycleState(lifecycle.model_lifecycle_state)
    try:
        new_state = MODEL_LIFECYCLE_FSM.fire(from_state, event)
    except IllegalTransition as exc:
        raise framework_error(FrameworkError.LIFECYCLE_ILLEGAL_TRANSITION,
                               detail=f"cannot fire {event} from model lifecycle state {from_state}") from exc
    lifecycle.model_lifecycle_state = new_state
    db.add(LifecycleTransition(model_id=model_id, fsm="MODEL", from_state=from_state, to_state=new_state, event=event))
    if event in GOVERNANCE_EVENTS:
        db.add(CertificationRecord(model_id=model_id, decision=event, decided_by=decided_by, rationale=rationale))
    # HISTORY.md OI-6.1: the operator gate's own two flags.
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
    except IllegalTransition as exc:
        raise framework_error(FrameworkError.LIFECYCLE_ILLEGAL_TRANSITION,
                               detail=f"cannot fire {event} from runtime lifecycle state {from_state}") from exc
    lifecycle.runtime_lifecycle_state = new_state
    db.add(LifecycleTransition(model_id=model_id, fsm="RUNTIME", from_state=from_state, to_state=new_state, event=event))
    db.flush()
    return lifecycle


class RuntimeProfile(BaseModel):
    """Wave 7 (W7-03): the compute an execution runtime is sized with —
    the same {cpu, memory, gpu} shape as an rApp manifest's
    runtimeProfiles entry."""
    model_config = ConfigDict(extra="forbid")

    cpu: float | None = Field(default=None, ge=0)
    memory: str | None = None
    gpu: float | None = Field(default=None, ge=0)


class RuntimeSizing(BaseModel):
    """Wave 7: optional on every Training/Validation/Emulation request.
    `runtimeProfile` overrides; otherwise `packageId` names the rApp
    package whose manifest's runtimeProfiles[<mode>] is used. Neither ->
    the runtime is created unsized, as before. `timeoutSeconds` overrides
    the stage's default execution timeout (W7-04)."""
    packageId: uuid.UUID | None = None
    runtimeProfile: RuntimeProfile | None = None
    timeoutSeconds: int | None = Field(default=None, gt=0)


class RequestTrainingRequest(RuntimeSizing):
    modelId: uuid.UUID | None = None
    modelCoordinationGroupId: uuid.UUID | None = None
    producerId: str
    requiredData: dict = {}
    # HISTORY.md OI-6.4: a separate, explicitly-typed reference to
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


class RequestValidationRequest(RuntimeSizing):
    modelId: uuid.UUID
    trainingJobId: uuid.UUID | None = None
    producerId: str
    validationCriteria: dict = {}
    # HISTORY.md OI-6.5: TS28.105-style completion notification —
    # same optional, best-effort shape TrainingJob's own notificationUri
    # already had (and never used); now genuinely fired on completion.
    notificationUri: str | None = None


class RequestEmulationRequest(RuntimeSizing):
    modelId: uuid.UUID
    producerId: str
    emulationCriteria: dict = {}
    notificationUri: str | None = None
    # Wave 4 — TS 28.105 AIMLInferenceEmulationFunction hosting this run.
    aIMLInferenceEmulationFunctionRef: uuid.UUID | None = None


class CompleteJobRequest(BaseModel):
    succeeded: bool
    metrics: dict = {}
    # HISTORY.md OI-6.5: where the real output artifact lives —
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
    # OI-5-aiml-featuregroup-dme: required when enableDme — the DME type the
    # group's data job collects, and how the trainer gets the data.
    dmeTypeId: uuid.UUID | None = None
    dataDeliveryMethod: Literal["PULL_HTTP", "PUSH_HTTP", "STREAMING_KAFKA"] = "PULL_HTTP"


class TrainingProgressRequest(BaseModel):
    """OI-5-aiml-trainingjob-steps: the execution runtime reports the step
    the run has reached."""
    step: Literal["DATA_EXTRACTION", "TRAINING", "TRAINED_MODEL"]


# ---------------------------------------------------------------- Execution runtimes (jointly with NFO)

def _nfo_create_execution_descriptor(job_kind: str, job_id: uuid.UUID, runtime_profile: dict | None = None) -> uuid.UUID:
    """HISTORY.md OI-6.2: a real NFO-backed execution runtime for
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
    workload: dict[str, Any] = {"jobKind": job_kind, "jobId": str(job_id)}
    if runtime_profile:
        workload["resources"] = runtime_profile  # Wave 7 (W7-03): the mode's runtime profile
    resp = _r1.post("/nfo/descriptors", json={
        "packageId": None, "name": f"aimgf-{job_kind.lower()}-{job_id}", "workloadTemplate": workload,
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


# ---------------------------------------------------------------- Wave 7: runtime profiles and execution timeouts

# W7-04 (SMO_Wave_10_Consolidated §13): Training 30 min, Validation 15 min,
# Emulation 30 min, Inference 5 s. Overridable per deployment by
# AIMGF_TIMEOUT_<KIND>_SECONDS and per request by `timeoutSeconds`.
DEFAULT_TIMEOUT_SECONDS = {"TRAINING": 1800, "VALIDATION": 900, "EMULATION": 1800, "INFERENCE": 5}


def _timeout_for(kind: str, override: int | None) -> int:
    if override is not None:
        return override
    return int(os.environ.get(f"AIMGF_TIMEOUT_{kind}_SECONDS", DEFAULT_TIMEOUT_SECONDS[kind]))


def _resolve_runtime_profile(kind: str, package_id: uuid.UUID | None, explicit: "RuntimeProfile | None") -> dict | None:
    """W7-03: an explicit profile wins; otherwise the rApp package's own
    manifest runtimeProfiles[kind] (read from Onboarding); otherwise none."""
    if explicit is not None:
        return explicit.model_dump(exclude_none=True)
    if package_id is None:
        return None
    resp = _r1.get(f"/onboarding/packages/{package_id}/onboarding-status")
    if resp.status_code != 200:
        raise framework_error(FrameworkError.PACKAGE_NOT_FOUND, detail=f"no such rApp package {package_id}")
    profiles = ((resp.json().get("aiCapabilities") or {}).get("runtimeProfiles") or {})
    return profiles.get(kind)


def _aware(value: datetime.datetime) -> datetime.datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=datetime.UTC)


def _overdue(started_at: datetime.datetime, timeout_seconds: int | None, now: datetime.datetime) -> bool:
    return timeout_seconds is not None and _aware(started_at) + datetime.timedelta(seconds=timeout_seconds) <= now


def _fire_if_legal(db: Session, model_id: uuid.UUID | None, event: ModelLifecycleEvent) -> None:
    """A timed-out run fails its model's lifecycle stage only if the model
    is still in that stage — never forcing an illegal transition (no
    lifecycle corruption, W7-04)."""
    if model_id is None:
        return
    try:
        _fire_model_event(db, model_id, event)
    except HTTPException:
        pass


def _expire_overdue_jobs(db: Session) -> list[dict]:
    """W7-04: every run past its deadline fails cleanly — status FAILED,
    its NFO execution runtime torn down, the model's lifecycle stage failed
    (when still legal), the training process/testing report marked, and the
    requester notified (best-effort, reason TIMEOUT). A SUSPENDED training
    run is paused and never expires; its clock restarts on resume.

    Enforced lazily (on every job read and completion, the same pattern as
    DME's producer health) and on demand via
    `POST /execution-timeouts/sweep` for a scheduler to call.
    """
    now = datetime.datetime.now(datetime.UTC)
    expired: list[tuple[str, uuid.UUID, str | None]] = []
    for job in db.scalars(select(TrainingJob).where(TrainingJob.status == "IN_PROGRESS")).all():
        if not _overdue(job.started_at, job.timeout_seconds, now):
            continue
        job.status = "FAILED"
        _nfo_terminate_execution(job.nf_deployment_id)
        job.nf_deployment_id = None
        _fire_if_legal(db, job.model_id, ModelLifecycleEvent.TRAINING_FAILED)
        _sync_training_process(db, job)
        process = db.scalar(select(MLTrainingProcess).where(MLTrainingProcess.training_job_id == job.training_job_id))
        if process is not None:
            process.result_state_info = "TIMEOUT"
        if job.ml_update_process_id is not None:
            _advance_ml_update_process(db, job.ml_update_process_id)
        expired.append(("TRAINING", job.training_job_id, job.notification_uri))
    kinds: list[tuple[str, Any, ModelLifecycleEvent]] = [("VALIDATION", ValidationJob, ModelLifecycleEvent.VALIDATION_FAILED),
                                                        ("EMULATION", EmulationJob, ModelLifecycleEvent.EMULATION_FAILED)]
    for kind, cls, event in kinds:
        for job in db.scalars(select(cls).where(cls.status == "RUNNING")).all():
            if not _overdue(job.started_at, job.timeout_seconds, now):
                continue
            job.status = "FAILED"
            job.metrics = {**(job.metrics or {}), "failureReason": "TIMEOUT"}
            _nfo_terminate_execution(job.nf_deployment_id)
            job.nf_deployment_id = None
            _fire_if_legal(db, job.model_id, event)
            job_id = job.validation_job_id if kind == "VALIDATION" else job.emulation_job_id
            if kind == "VALIDATION":
                db.add(MLTestingReport(validation_job_id=job_id, ml_testing_function_id=job.ml_testing_function_id,
                                       ml_testing_result="FAILED"))
            expired.append((kind, job_id, job.notification_uri))
    for inference in db.scalars(select(InferenceJob).where(InferenceJob.status == InferenceState.RUNNING)).all():
        if _overdue(inference.started_at, inference.timeout_seconds, now):
            inference.status = INFERENCE_JOB_FSM.fire(InferenceState(inference.status), InferenceEvent.FAIL)
            expired.append(("INFERENCE", inference.inference_job_id, inference.notification_destination))
    if expired:
        for kind, job_id, destination in expired:
            _notify_job_completion(db, destination, kind, job_id, False, None, {"failureReason": "TIMEOUT"})
        db.commit()  # the failures and their notifications commit together (PR-MSG-1.7)
    return [{"jobKind": kind, "jobId": str(job_id)} for kind, job_id, _ in expired]


@app.post("/execution-timeouts/sweep")
def sweep_execution_timeouts(db: Session = Depends(get_session)):
    """W7-04: fail every overdue Training/Validation/Emulation/Inference run
    now (for a scheduler; reads and completions also sweep lazily)."""
    # config-ref: AIMGF_TIMEOUT_TRAINING_SECONDS, AIMGF_TIMEOUT_VALIDATION_SECONDS, AIMGF_TIMEOUT_EMULATION_SECONDS, AIMGF_TIMEOUT_INFERENCE_SECONDS
    return {"expired": _expire_overdue_jobs(db), "defaultTimeoutSeconds": {k: _timeout_for(k, None) for k in DEFAULT_TIMEOUT_SECONDS}}


# ---------------------------------------------------------------- Training

def _validate_dme_data_job_ids(dme_data_job_ids: list[uuid.UUID]) -> None:
    """HISTORY.md OI-6.4: mirrors MDAF's own
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


def _notify_job_completion(db: Session, notification_uri: str | None, job_kind: str, job_id: uuid.UUID, succeeded: bool,
                            outcome_artifact_dme_type_id: uuid.UUID | None, metrics: dict) -> None:
    """HISTORY.md OI-6.5: TS28.105-style completion notification —
    `advance(TRAINING_COMPLETE)`/`complete(validationJobId, ...)`/
    `complete(emulationJobId, ...)` used to be bare state transitions
    with no side effect beyond the FSM move itself. Written to the
    transactional outbox in the caller's transaction (PR-MSG-1.7) and
    sent once the caller commits: the caller must commit after this —
    an unreachable destination never fails the completion call itself,
    and a crash after the commit no longer loses the notification.
    """
    enqueue(db, notification_uri, {
        "jobKind": job_kind, "jobId": str(job_id), "succeeded": succeeded,
        "outcomeArtifactDmeTypeId": str(outcome_artifact_dme_type_id) if outcome_artifact_dme_type_id else None,
        "metrics": metrics,
    })


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
                    runtime_profile: dict | None = None, timeout_seconds: int | None = None,
                    **job_fields) -> TrainingJob:
    """The one place a training run starts — RequestTraining
    (`POST /training-jobs`), TS 28.105 MLTrainingRequest
    (`POST /ml-training-requests`), group-retrain propagation and
    MLUpdateProcess all go through here, so every run gets the same
    lifecycle gate, NFO runtime and (Wave 4) MLTrainingProcess.

    modelId-targeted requests also drive the model's own ModelLifecycle
    FSM: REGISTERED -> TRAINING (the very first cycle), PROMOTED or
    CERTIFIED -> TRAINING (an ordinary retrain, CERTIFIED covering a
    rolled-back model) or FAILED -> TRAINING (a retry). A model already
    TRAINING (an unresolved prior job) is treated as the operator's
    explicit decision to supersede it: the orphaned job is marked
    CANCELLED rather than left silently RUNNING and unreachable. Any other
    state (mid-pipeline, DEPRECATED, RETIRED) is a 409
    LIFECYCLE_ILLEGAL_TRANSITION naming the state.
    """
    lifecycle = None
    if model_id is not None:
        _get_model(model_id)
        lifecycle = _get_or_create_lifecycle(db, model_id)
        if lifecycle.model_lifecycle_state not in TRAINABLE_STATES:
            raise framework_error(FrameworkError.LIFECYCLE_ILLEGAL_TRANSITION,
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
                       status="IN_PROGRESS", ml_training_type=ml_training_type, runtime_profile=runtime_profile,
                       timeout_seconds=_timeout_for("TRAINING", timeout_seconds), **job_fields)
    db.add(job)
    db.flush()
    db.add(MLTrainingProcess(training_job_id=job.training_job_id, priority=priority,
                             termination_conditions=termination_conditions, status="RUNNING"))
    if job.ml_training_function_id is not None:
        function = db.get(MLTrainingFunction, job.ml_training_function_id)
        if function is not None:
            function.ml_training_type = ml_training_type

    # HISTORY.md OI-6.2: MLTF's own real execution runtime —
    # closes the "MLTF trains (Phase 1: elided)" gap. Every training job
    # gets one, model-targeted or coordination-group-targeted alike: a
    # training run needs somewhere to actually execute regardless of
    # which kind of target it names, the same way the job row itself is
    # always created either way.
    descriptor_id = _nfo_create_execution_descriptor("TRAINING", job.training_job_id, job.runtime_profile)
    job.nf_deployment_descriptor_id = descriptor_id
    job.nf_deployment_id = _nfo_instantiate_execution(descriptor_id, "TRAINING", job.training_job_id)

    if lifecycle is not None and model_id is not None:      # a lifecycle exists only for a named model
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
    if model_id is not None:
        _record_training_start(model_id, ml_training_type, job)
    return job


def _record_training_start(model_id: uuid.UUID, ml_training_type: str, job: TrainingJob) -> None:
    """The model enters IN_TRAINING (its first cycle) or IN_RETRAINING; a run
    that starts from an earlier model records it as `baseModelId`: the model it
    was derived from (`sourceTrainedMLModelRef`), else the model itself."""
    info: dict = {}
    if job.training_dataset:
        info["dataSources"] = str(job.training_dataset)
    if ml_training_type != "INITIAL_TRAINING":
        model = _get_model_or_none(model_id) or {}
        info["baseModelId"] = model.get("sourceTrainedMLModelRef") or str(model_id)
    _record_phase(model_id, "IN_TRAINING" if ml_training_type == "INITIAL_TRAINING" else "IN_RETRAINING", training_info=info)


@app.post("/training-jobs", status_code=201)
@idempotent("aimgf", status_code=201)
def request_training(body: RequestTrainingRequest, request: Request, db: Session = Depends(get_session)):
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
                          consumer_rapp_id=body.consumerRappId, producer_rapp_id=body.producerRappId,
                          runtime_profile=_resolve_runtime_profile("TRAINING", body.packageId, body.runtimeProfile),
                          timeout_seconds=body.timeoutSeconds)
    db.commit()
    return {"trainingJobId": str(job.training_job_id)}


@app.get("/training-jobs/{training_job_id}/status")
def query_training_job_status(training_job_id: uuid.UUID, db: Session = Depends(get_session)):
    _expire_overdue_jobs(db)
    job = db.get(TrainingJob, training_job_id)
    if job is None:
        raise framework_error(FrameworkError.TRAINING_JOB_NOT_FOUND, detail="no such training job")
    return {
        "trainingJobId": str(job.training_job_id), "status": job.status, "runId": job.run_id,
        "trainingDataset": job.training_dataset, "validationDataset": job.validation_dataset,
        "consumerRappId": job.consumer_rapp_id, "producerRappId": job.producer_rapp_id,
        "mlTrainingType": job.ml_training_type, "dmeDataJobIds": [str(i) for i in job.dme_data_job_ids],
        "outcomeArtifactDmeTypeId": str(job.outcome_artifact_dme_type_id) if job.outcome_artifact_dme_type_id else None,
        "nfDeploymentId": str(job.nf_deployment_id) if job.nf_deployment_id else None,
        "runtimeProfile": job.runtime_profile, "timeoutSeconds": job.timeout_seconds,
        "startedAt": _aware(job.started_at).isoformat(),
        "currentStep": job.current_step, "steps": _training_steps(job),
    }


@app.post("/training-jobs/{training_job_id}/complete")
def complete_training(training_job_id: uuid.UUID, body: CompleteJobRequest, db: Session = Depends(get_session)):
    """HISTORY.md OI-6.5: Training's own completion route, at parity with
    Validation/Emulation's — job status (TrainingJob's own FINISHED/FAILED
    vocabulary, not COMPLETED), the outcome artifact, a best-effort
    completion notification, and the model's TRAINING_COMPLETE/
    TRAINING_FAILED transition (skipped for a coordination-group-targeted
    job, which has no single model to advance — the same asymmetry
    `request_training` itself already has). This is the only way a
    training run completes: `POST /models/{id}/advance` refuses the
    job-driven events (OI-2-governance-bypass).
    """
    _expire_overdue_jobs(db)
    job = db.get(TrainingJob, training_job_id)
    if job is None:
        raise framework_error(FrameworkError.TRAINING_JOB_NOT_FOUND, detail="no such training job")
    if job.status not in ("IN_PROGRESS", "SUSPENDED"):
        # Wave 7: a run that already ended (timed out, cancelled, completed)
        # can't be completed again — a late result must not resurrect it.
        raise framework_error(FrameworkError.TRAINING_JOB_ILLEGAL_TRANSITION,
                               detail=f"cannot complete a training job in status {job.status}")
    job.status = "FINISHED" if body.succeeded else "FAILED"
    job.model_metrics = body.metrics
    job.outcome_artifact_dme_type_id = body.outcomeArtifactDmeTypeId
    # HISTORY.md OI-6.2: the run is done — its execution runtime
    # is torn down right alongside it, not left running indefinitely.
    _nfo_terminate_execution(job.nf_deployment_id)
    job.nf_deployment_id = None
    if job.model_id is not None:
        event = ModelLifecycleEvent.TRAINING_COMPLETE if body.succeeded else ModelLifecycleEvent.TRAINING_FAILED
        _fire_model_event(db, job.model_id, event)
        if body.succeeded:
            _record_phase(job.model_id, "TRAINED")
    _sync_training_process(db, job)
    _write_training_report(db, job, body)
    if job.ml_update_process_id is not None:
        _advance_ml_update_process(db, job.ml_update_process_id)
    _notify_job_completion(db, job.notification_uri, "TRAINING", job.training_job_id, body.succeeded,
                            job.outcome_artifact_dme_type_id, job.model_metrics)
    db.commit()
    return _training_job_view(job)


# A training run that can still be cancelled/completed — everything else is terminal.
ACTIVE_TRAINING_STATUSES = ("IN_PROGRESS", "SUSPENDED")


@app.delete("/training-jobs/{training_job_id}", status_code=204)
def cancel_training(training_job_id: uuid.UUID, db: Session = Depends(get_session)):
    """Cancels an in-flight (IN_PROGRESS/SUSPENDED) run: its execution
    runtime is torn down and its model released from TRAINING
    (`_cancel_training_job`). Idempotent for an unknown or already
    CANCELLED job; a FINISHED/FAILED run is history and is refused (409)
    rather than rewritten to CANCELLED.
    """
    _expire_overdue_jobs(db)
    job = db.get(TrainingJob, training_job_id)
    if job is None or job.status == "CANCELLED":
        return
    if job.status not in ACTIVE_TRAINING_STATUSES:
        raise framework_error(FrameworkError.TRAINING_JOB_ILLEGAL_TRANSITION,
                               detail=f"cannot cancel a training job in status {job.status}")
    _cancel_training_job(db, job)
    db.commit()


def _release_training_model(db: Session, job: TrainingJob) -> None:
    """A cancelled run fails its model's TRAINING stage (TRAINING_FAILED ->
    FAILED, the lifecycle's own retry/retire point — TRAINING has no other
    exit) the same way a timed-out run does — but only when this job is
    the model's current run: a run superseded by a newer request must not
    fail the newer one."""
    if job.model_id is None:
        return
    lifecycle = db.get(ModelLifecycle, job.model_id)
    if lifecycle is None or lifecycle.training_job_id != job.training_job_id:
        return
    _fire_if_legal(db, job.model_id, ModelLifecycleEvent.TRAINING_FAILED)


def _cancel_training_job(db: Session, job: TrainingJob, *, advance_update: bool = True) -> None:
    """The one cancel path (DELETE /training-jobs/{id}, the NRM cancelRequest/
    cancelProcess flags, an MLUpdateRequest cancel): status CANCELLED, the
    execution runtime torn down, the model released from TRAINING, and the
    MLTrainingProcess (and, unless the caller closes it itself, the
    MLUpdateProcess) kept in step."""
    job.status = "CANCELLED"
    job.cancel_request = True
    _nfo_terminate_execution(job.nf_deployment_id)
    job.nf_deployment_id = None
    _release_training_model(db, job)
    _sync_training_process(db, job)
    if advance_update and job.ml_update_process_id is not None:
        _advance_ml_update_process(db, job.ml_update_process_id)


def _resume_training_job(db: Session, job: TrainingJob) -> None:
    """The one resume path (POST .../resume, the NRM suspendRequest/
    suspendProcess=false flags, an MLUpdateRequest resume). A suspended
    run's clock is paused (W7-04) — it restarts on resume."""
    job.status = "IN_PROGRESS"
    job.suspend_request = False
    job.started_at = datetime.datetime.now(datetime.UTC)
    _sync_training_process(db, job)


@app.post("/training-jobs/{training_job_id}/suspend")
def suspend_training(training_job_id: uuid.UUID, db: Session = Depends(get_session)):
    """HISTORY.md §7's AI/ML Workflow section item 6: TrainingJob had no
    suspend concept at all, only a hard cancel. This is deliberately a
    plain status flip, not a third state machine — the two real FSMs
    Wave 2 built (ModelLifecycleState/RuntimeLifecycleState) operate one
    level up and are untouched by a job-level suspend/resume, the same
    way job.status's other transitions (IN_PROGRESS -> FINISHED/FAILED/
    CANCELLED) reach into ModelLifecycleState only through the job's own
    completion/cancel/timeout paths, never on suspend/resume. Only
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
    _resume_training_job(db, job)
    db.commit()
    return {"trainingJobId": str(job.training_job_id), "status": job.status}


@app.post("/training-jobs/{training_job_id}/progress")
def report_training_progress(training_job_id: uuid.UUID, body: TrainingProgressRequest, db: Session = Depends(get_session)):
    """OI-5-aiml-trainingjob-steps: the run's execution runtime (the NFO
    deployment `_start_training` created) reports the step it has reached —
    DATA_EXTRACTION, then TRAINING, then TRAINED_MODEL. Forward only:
    repeating the current step is a no-op, going back is refused. Only an
    IN_PROGRESS run makes progress (a SUSPENDED one is paused; an ended one
    is history), 409 otherwise. Completion stays `POST .../complete`: this
    reports where the run is, not how it ended.
    """
    _expire_overdue_jobs(db)
    job = db.get(TrainingJob, training_job_id)
    if job is None:
        raise framework_error(FrameworkError.TRAINING_JOB_NOT_FOUND, detail="no such training job")
    if job.status != "IN_PROGRESS":
        raise framework_error(FrameworkError.TRAINING_JOB_ILLEGAL_TRANSITION,
                               detail=f"a training job in status {job.status} makes no progress")
    if TRAINING_STEPS.index(body.step) < TRAINING_STEPS.index(job.current_step):
        raise framework_error(FrameworkError.TRAINING_JOB_ILLEGAL_TRANSITION,
                               detail=f"step {body.step} is behind the run's current step {job.current_step}")
    job.current_step = body.step
    db.commit()
    return {"trainingJobId": str(job.training_job_id), "status": job.status,
            "currentStep": job.current_step, "steps": _training_steps(job)}


@app.post("/training-jobs/{training_job_id}/model-metrics")
def update_training_job_model_metrics(training_job_id: uuid.UUID, model_metrics: dict, db: Session = Depends(get_session)):
    """HISTORY.md §5: TrainingJob had no metrics-writeback
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
    _expire_overdue_jobs(db)
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
    """CreateValidation (docs/ARCHITECTURE.md's AIMgF request list) — the one
    place a validation (TS 28.105: testing) run starts, shared by
    `POST /validation-jobs` and `POST /ml-testing-requests`.

    A model-targeted run requires the model to have finished training
    (TRAINED) AND an operator to have already fired APPROVE_TRAINING
    (HISTORY.md OI-6.1) — the state check alone isn't the gate.
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
            raise framework_error(FrameworkError.LIFECYCLE_ILLEGAL_TRANSITION,
                                   detail=f"cannot request validation for a model in state {lifecycle.model_lifecycle_state}")
        if not lifecycle.training_approved:
            raise framework_error(FrameworkError.TRAINING_NOT_APPROVED,
                                   detail="an operator must advance(APPROVE_TRAINING, decidedBy) before validation can start")
    job_fields.setdefault("timeout_seconds", None)
    job_fields["timeout_seconds"] = _timeout_for("VALIDATION", job_fields["timeout_seconds"])
    job = ValidationJob(model_id=model_id, model_coordination_group_id=group_id, producer_id=producer_id,
                         status="RUNNING", **job_fields)
    db.add(job)
    db.flush()
    # HISTORY.md OI-6.2: MLVF's own real execution runtime.
    descriptor_id = _nfo_create_execution_descriptor("VALIDATION", job.validation_job_id, job.runtime_profile)
    job.nf_deployment_descriptor_id = descriptor_id
    job.nf_deployment_id = _nfo_instantiate_execution(descriptor_id, "VALIDATION", job.validation_job_id)
    if model_id is not None:
        _fire_model_event(db, model_id, ModelLifecycleEvent.CREATE_VALIDATION)
    db.flush()
    return job


@app.post("/validation-jobs", status_code=201)
@idempotent("aimgf", status_code=201)
def request_validation(body: RequestValidationRequest, request: Request, db: Session = Depends(get_session)):
    """CreateValidation — see `_start_validation`."""
    job = _start_validation(db, model_id=body.modelId, group_id=None, producer_id=body.producerId,
                            training_job_id=body.trainingJobId, validation_criteria=body.validationCriteria,
                            notification_uri=body.notificationUri,
                            runtime_profile=_resolve_runtime_profile("VALIDATION", body.packageId, body.runtimeProfile),
                            timeout_seconds=body.timeoutSeconds)
    db.commit()
    return {"validationJobId": str(job.validation_job_id)}


@app.get("/validation-jobs/{validation_job_id}/status")
def query_validation_job_status(validation_job_id: uuid.UUID, db: Session = Depends(get_session)):
    _expire_overdue_jobs(db)
    job = db.get(ValidationJob, validation_job_id)
    if job is None:
        raise framework_error(FrameworkError.VALIDATION_JOB_NOT_FOUND, detail="no such validation job")
    return _validation_job_view(job)


@app.post("/validation-jobs/{validation_job_id}/complete")
def complete_validation(validation_job_id: uuid.UUID, body: CompleteJobRequest, db: Session = Depends(get_session)):
    _expire_overdue_jobs(db)
    job = db.get(ValidationJob, validation_job_id)
    if job is None:
        raise framework_error(FrameworkError.VALIDATION_JOB_NOT_FOUND, detail="no such validation job")
    if job.status not in ("RUNNING", "SUSPENDED"):
        raise framework_error(FrameworkError.TRAINING_JOB_ILLEGAL_TRANSITION,
                               detail=f"cannot complete a validation job in status {job.status}")
    job.status = "COMPLETED" if body.succeeded else "FAILED"
    job.metrics = body.metrics
    job.outcome_artifact_dme_type_id = body.outcomeArtifactDmeTypeId
    # HISTORY.md OI-6.2: the run is done — tear down its runtime.
    _nfo_terminate_execution(job.nf_deployment_id)
    job.nf_deployment_id = None
    if job.model_id is not None:
        event = ModelLifecycleEvent.VALIDATION_COMPLETE if body.succeeded else ModelLifecycleEvent.VALIDATION_FAILED
        _fire_model_event(db, job.model_id, event)
    # Wave 4 — TS 28.105 MLTestingReport.
    db.add(MLTestingReport(validation_job_id=job.validation_job_id, ml_testing_function_id=job.ml_testing_function_id,
                           model_performance_testing=ts28105.dump(body.modelPerformanceTesting),
                           ml_testing_result="PASSED" if body.succeeded else "FAILED"))
    _notify_job_completion(db, job.notification_uri, "VALIDATION", job.validation_job_id, body.succeeded,
                            job.outcome_artifact_dme_type_id, job.metrics)
    db.commit()
    return _validation_job_view(job)


@app.get("/validation-jobs")
def list_validation_jobs(model_id: uuid.UUID | None = None, status: str | None = None, limit: int = PageLimit,
                          offset: int = PageOffset, db: Session = Depends(get_session)):
    _expire_overdue_jobs(db)
    stmt = select(ValidationJob)
    if model_id:
        stmt = stmt.where(ValidationJob.model_id == model_id)
    if status:
        stmt = stmt.where(ValidationJob.status == status)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [_validation_job_view(j) for j in page["items"]]}


# ---------------------------------------------------------------- Emulation

@app.post("/emulation-jobs", status_code=201)
@idempotent("aimgf", status_code=201)
def request_emulation(body: RequestEmulationRequest, request: Request, db: Session = Depends(get_session)):
    """CreateEmulation — new this wave, split out from Wave 1's flat
    VALIDATION_COMPLETE -> EMULATED transition the same way ValidationJob
    is. Requires the model to have passed validation (VALIDATED) AND an
    operator to have already fired APPROVE_VALIDATION (HISTORY.md OI-6.1) — the same gate shape as request_validation's own.
    """
    _get_model(body.modelId)
    lifecycle = _get_or_create_lifecycle(db, body.modelId)
    if lifecycle.model_lifecycle_state != ModelLifecycleState.VALIDATED:
        raise framework_error(FrameworkError.LIFECYCLE_ILLEGAL_TRANSITION,
                               detail=f"cannot request emulation for a model in state {lifecycle.model_lifecycle_state}")
    if not lifecycle.validation_approved:
        raise framework_error(FrameworkError.VALIDATION_NOT_APPROVED,
                               detail="an operator must advance(APPROVE_VALIDATION, decidedBy) before emulation can start")
    if body.aIMLInferenceEmulationFunctionRef is not None:
        if db.get(AIMLInferenceEmulationFunction, body.aIMLInferenceEmulationFunctionRef) is None:
            raise framework_error(FrameworkError.NRM_OBJECT_NOT_FOUND, detail="no such AIMLInferenceEmulationFunction")
    job = EmulationJob(model_id=body.modelId, producer_id=body.producerId, emulation_criteria=body.emulationCriteria,
                        status="RUNNING", notification_uri=body.notificationUri,
                        aiml_inference_emulation_function_id=body.aIMLInferenceEmulationFunctionRef,
                        runtime_profile=_resolve_runtime_profile("EMULATION", body.packageId, body.runtimeProfile),
                        timeout_seconds=_timeout_for("EMULATION", body.timeoutSeconds))
    db.add(job)
    db.flush()
    # HISTORY.md OI-6.2: MLEF's own real execution runtime.
    descriptor_id = _nfo_create_execution_descriptor("EMULATION", job.emulation_job_id, job.runtime_profile)
    job.nf_deployment_descriptor_id = descriptor_id
    job.nf_deployment_id = _nfo_instantiate_execution(descriptor_id, "EMULATION", job.emulation_job_id)
    _fire_model_event(db, body.modelId, ModelLifecycleEvent.CREATE_EMULATION)
    db.commit()
    return {"emulationJobId": str(job.emulation_job_id)}


@app.get("/emulation-jobs/{emulation_job_id}/status")
def query_emulation_job_status(emulation_job_id: uuid.UUID, db: Session = Depends(get_session)):
    _expire_overdue_jobs(db)
    job = db.get(EmulationJob, emulation_job_id)
    if job is None:
        raise framework_error(FrameworkError.EMULATION_JOB_NOT_FOUND, detail="no such emulation job")
    return _emulation_job_view(job)


@app.post("/emulation-jobs/{emulation_job_id}/complete")
def complete_emulation(emulation_job_id: uuid.UUID, body: CompleteJobRequest, db: Session = Depends(get_session)):
    _expire_overdue_jobs(db)
    job = db.get(EmulationJob, emulation_job_id)
    if job is None:
        raise framework_error(FrameworkError.EMULATION_JOB_NOT_FOUND, detail="no such emulation job")
    if job.status != "RUNNING":
        raise framework_error(FrameworkError.TRAINING_JOB_ILLEGAL_TRANSITION,
                               detail=f"cannot complete an emulation job in status {job.status}")
    job.status = "COMPLETED" if body.succeeded else "FAILED"
    job.metrics = body.metrics
    job.outcome_artifact_dme_type_id = body.outcomeArtifactDmeTypeId
    # HISTORY.md OI-6.2: the run is done — tear down its runtime.
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
    _notify_job_completion(db, job.notification_uri, "EMULATION", job.emulation_job_id, body.succeeded,
                            job.outcome_artifact_dme_type_id, job.metrics)
    db.commit()
    return _emulation_job_view(job)


@app.get("/emulation-jobs")
def list_emulation_jobs(model_id: uuid.UUID | None = None, status: str | None = None, limit: int = PageLimit,
                         offset: int = PageOffset, db: Session = Depends(get_session)):
    _expire_overdue_jobs(db)
    stmt = select(EmulationJob)
    if model_id:
        stmt = stmt.where(EmulationJob.model_id == model_id)
    if status:
        stmt = stmt.where(EmulationJob.status == status)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [_emulation_job_view(j) for j in page["items"]]}


# ---------------------------------------------------------------- ModelLifecycle: generic advance + governance

# Where each job-driven event is fired instead of `advance` (the 422's hint).
_JOB_ROUTE_FOR_EVENT = {
    ModelLifecycleEvent.CREATE_TRAINING: "POST /training-jobs",
    ModelLifecycleEvent.TRAINING_COMPLETE: "POST /training-jobs/{id}/complete",
    ModelLifecycleEvent.TRAINING_FAILED: "POST /training-jobs/{id}/complete or DELETE /training-jobs/{id}",
    ModelLifecycleEvent.CREATE_VALIDATION: "POST /validation-jobs",
    ModelLifecycleEvent.VALIDATION_COMPLETE: "POST /validation-jobs/{id}/complete",
    ModelLifecycleEvent.VALIDATION_FAILED: "POST /validation-jobs/{id}/complete",
    ModelLifecycleEvent.CREATE_EMULATION: "POST /emulation-jobs",
    ModelLifecycleEvent.EMULATION_COMPLETE: "POST /emulation-jobs/{id}/complete",
    ModelLifecycleEvent.EMULATION_FAILED: "POST /emulation-jobs/{id}/complete",
}


@app.post("/models/{model_id}/advance")
def advance_model_lifecycle(model_id: uuid.UUID, event: str, decided_by: str | None = None, rationale: str | None = None,
                             db: Session = Depends(get_session)):
    """Fires the ModelLifecycle events that have no job behind them
    (`ADVANCEABLE_EVENTS`): the eight governance decisions
    (`GOVERNANCE_EVENTS` — SUBMIT_FOR_APPROVAL, APPROVE, REJECT, CERTIFY,
    PROMOTE, ROLLBACK and HISTORY.md OI-6.1's APPROVE_TRAINING/
    APPROVE_VALIDATION), which require `decidedBy` and write a
    CertificationRecord, plus DEPRECATE/RETIRE, which aren't governance
    decisions in docs/ARCHITECTURE.md's AIMgF sense and need no decider.

    Job-driven events (CREATE_*/..._COMPLETE/..._FAILED) are refused with
    422 SCHEMA_VALIDATION_FAILED naming the job route that fires them, as
    is an unknown event — so the OI-6.1 approval gates can't be bypassed
    and no stage moves without its job row (OI-2-governance-bypass).

    RETIRE also terminates the model's serving runtime, through the same
    path as `POST /models/{id}/runtime/terminate` (NFO teardown,
    RuntimeLifecycle REQUEST_TERMINATION/TERMINATION_COMPLETE), when one
    is deployed. DEPRECATE leaves a live runtime serving (call flow 26).
    """
    try:
        ev = ModelLifecycleEvent(event)
    except ValueError as exc:
        raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED,
                               detail=f"unknown model lifecycle event {event!r}; advance accepts {sorted(ADVANCEABLE_EVENTS)}") from exc
    if ev not in ADVANCEABLE_EVENTS:
        raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED,
                               detail=f"{ev} is job-driven and cannot be advanced directly; use {_JOB_ROUTE_FOR_EVENT[ev]}")
    _get_model(model_id)
    lifecycle = _fire_model_event(db, model_id, ev, decided_by=decided_by, rationale=rationale)
    if ev == ModelLifecycleEvent.RETIRE and lifecycle.runtime_lifecycle_state in _TERMINABLE_RUNTIME_STATES:
        _terminate_runtime(db, model_id)
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

def _nfo_create_descriptor(model_id: uuid.UUID, runtime_profile: dict | None = None) -> uuid.UUID:
    """A model runtime has no onboarded ApplicationPackage behind it —
    unlike Onboarding's own CreateDescriptor call, packageId is omitted
    (NFO's own `nf_deployment_descriptor.package_id` is nullable since
    this wave, migrations/001_init.sql).
    """
    workload: dict[str, Any] = {"modelId": str(model_id), "jobKind": "INFERENCE"}
    if runtime_profile:
        workload["resources"] = runtime_profile  # Wave 7 (W7-03): the INFERENCE runtime profile
    resp = _r1.post("/nfo/descriptors", json={
        "packageId": None, "name": f"aimgf-model-{model_id}-runtime", "workloadTemplate": workload,
    })
    return uuid.UUID(resp.json()["nfDeploymentDescriptorId"])


def _nfo_instantiate(descriptor_id: uuid.UUID, model_id: uuid.UUID) -> uuid.UUID:
    resp = _r1.post("/nfo/deployments", json={
        "nfDeploymentDescriptorId": str(descriptor_id), "name": f"aimgf-model-{model_id}-runtime",
    })
    return uuid.UUID(resp.json()["nfDeploymentId"])


@app.post("/models/{model_id}/runtime/deploy", status_code=201)
def deploy_model_runtime(model_id: uuid.UUID, package_id: uuid.UUID | None = None, body: RuntimeProfile | None = None,
                         db: Session = Depends(get_session)):
    """RuntimeLifecycle's own DEPLOY — jointly owned with NFO
    (docs/ARCHITECTURE.md's AIMgF "NFO invocation: request runtime creation").
    Requires the model to have cleared governance (CERTIFIED or
    PROMOTED). The RuntimeLifecycle guard fires before any NFO call, so a
    duplicate deploy attempt (already DEPLOYMENT_REQUESTED-or-later)
    never touches NFO at all.
    """
    _get_model(model_id)
    lifecycle = _deploy_runtime(db, model_id, _resolve_runtime_profile("INFERENCE", package_id, body))
    db.commit()
    return _lifecycle_view(lifecycle)


def _deploy_runtime(db: Session, model_id: uuid.UUID, runtime_profile: dict | None = None) -> ModelLifecycle:
    lifecycle = _get_or_create_lifecycle(db, model_id)
    if lifecycle.model_lifecycle_state not in (ModelLifecycleState.CERTIFIED, ModelLifecycleState.PROMOTED):
        raise framework_error(FrameworkError.MODEL_NOT_CERTIFIED,
                               detail=f"cannot deploy a runtime for a model in state {lifecycle.model_lifecycle_state}")
    _fire_runtime_event(db, model_id, RuntimeLifecycleEvent.REQUEST_DEPLOYMENT)

    descriptor_id = _nfo_create_descriptor(model_id, runtime_profile)
    deployment_id = _nfo_instantiate(descriptor_id, model_id)
    lifecycle.runtime_profile = runtime_profile
    lifecycle.nf_deployment_descriptor_id = descriptor_id
    lifecycle.nf_deployment_id = deployment_id

    _fire_runtime_event(db, model_id, RuntimeLifecycleEvent.DEPLOYMENT_COMPLETE)
    return lifecycle


def _refuse_end_of_life(lifecycle: ModelLifecycle, action: str) -> None:
    """OI-2-model-eol-serving: a DEPRECATED or RETIRED model's runtime is
    never (re)activated or scaled — no new serving capacity for a model on
    its way out."""
    if lifecycle.model_lifecycle_state in END_OF_LIFE_STATES:
        raise framework_error(FrameworkError.LIFECYCLE_ILLEGAL_TRANSITION,
                               detail=f"cannot {action} the runtime of a {lifecycle.model_lifecycle_state} model")


def _activate_runtime(db: Session, model_id: uuid.UUID) -> ModelLifecycle:
    _refuse_end_of_life(_get_or_create_lifecycle(db, model_id), "activate")
    _fire_runtime_event(db, model_id, RuntimeLifecycleEvent.ACTIVATE)
    return _fire_runtime_event(db, model_id, RuntimeLifecycleEvent.ACTIVATION_COMPLETE)


@app.post("/models/{model_id}/runtime/activate")
def activate_model_runtime(model_id: uuid.UUID, db: Session = Depends(get_session)):
    """Marks the runtime as ready to accept inference (request_inference's
    own gate). Local-only: NFO's own deployment is already RUNNING once
    `deploy` returns (Phase 1: instantiate completes synchronously, same
    elision as elsewhere in this build) — ACTIVATE is AIMgF's own
    decision about whether traffic should be sent yet, not a further NFO
    call. Refused (409) for a DEPRECATED/RETIRED model.
    """
    lifecycle = _activate_runtime(db, model_id)
    db.commit()
    return _lifecycle_view(lifecycle)


@app.post("/models/{model_id}/runtime/scale")
def scale_model_runtime(model_id: uuid.UUID, db: Session = Depends(get_session)):
    """Refused (409) for a DEPRECATED/RETIRED model."""
    lifecycle = _get_or_create_lifecycle(db, model_id)
    _refuse_end_of_life(lifecycle, "scale")
    _fire_runtime_event(db, model_id, RuntimeLifecycleEvent.REQUEST_SCALE)
    if lifecycle.nf_deployment_id is not None:
        _r1.post(f"/nfo/deployments/{lifecycle.nf_deployment_id}/scale")
    _fire_runtime_event(db, model_id, RuntimeLifecycleEvent.SCALE_COMPLETE)
    db.commit()
    return _lifecycle_view(lifecycle)


# RuntimeLifecycle states with a REQUEST_TERMINATION edge (statemachine.py).
_TERMINABLE_RUNTIME_STATES = (RuntimeLifecycleState.DEPLOYMENT_REQUESTED, RuntimeLifecycleState.DEPLOYED,
                              RuntimeLifecycleState.ACTIVE)


def _terminate_runtime(db: Session, model_id: uuid.UUID) -> ModelLifecycle:
    """The one runtime teardown path — `POST .../runtime/terminate` and
    RETIRE (`advance_model_lifecycle`) both use it."""
    lifecycle = _get_or_create_lifecycle(db, model_id)
    _fire_runtime_event(db, model_id, RuntimeLifecycleEvent.REQUEST_TERMINATION)
    if lifecycle.nf_deployment_id is not None:
        _r1.delete(f"/nfo/deployments/{lifecycle.nf_deployment_id}")
    return _fire_runtime_event(db, model_id, RuntimeLifecycleEvent.TERMINATION_COMPLETE)


@app.post("/models/{model_id}/runtime/terminate")
def terminate_model_runtime(model_id: uuid.UUID, db: Session = Depends(get_session)):
    lifecycle = _terminate_runtime(db, model_id)
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
@idempotent("aimgf", status_code=201)
def request_inference(request: Request, model_id: uuid.UUID, notification_destination: str | None = None,
                      aiml_inference_function_id: uuid.UUID | None = None, consumer_ref: str | None = None,
                      timeout_seconds: int | None = None, db: Session = Depends(get_session)):
    """RequestInference — MLEF-hosted (AI/ML Workflow LLD section 3).
    Gated on RuntimeLifecycleState.ACTIVE (a serving question) — a
    PROMOTED-but-not-yet-deployed model, or one whose runtime is
    mid-SCALING, can't serve inference — and on the model not being
    RETIRED (OI-2-model-eol-serving; RETIRE also terminates the runtime).
    A DEPRECATED model's already-ACTIVE runtime keeps serving: deprecation
    stops new deploys/activation/scaling, not existing consumers (call
    flow 26).
    """
    _get_model(model_id)
    lifecycle = _get_or_create_lifecycle(db, model_id)
    if lifecycle.model_lifecycle_state == ModelLifecycleState.RETIRED:
        raise framework_error(FrameworkError.INFERENCE_MODEL_NOT_ACTIVE, detail="model is RETIRED")
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
    # HISTORY.md OI-6.2: MLIF's own execution runtime is the
    # model's already-live serving deployment (real since this state is
    # only reachable once deploy_model_runtime's own NFO call succeeded)
    # — a reference, not a new NFO call. See InferenceJob's own docstring
    # for why this differs from Training/Validation/Emulation.
    job = InferenceJob(model_id=model_id, status=InferenceState.RUNNING, notification_destination=notification_destination,
                        nf_deployment_id=lifecycle.nf_deployment_id, aiml_inference_function_id=aiml_inference_function_id,
                        consumer_ref=consumer_ref, timeout_seconds=_timeout_for("INFERENCE", timeout_seconds))
    db.add(job)
    db.commit()
    return {"inferenceJobId": str(job.inference_job_id)}


@app.get("/inference-jobs/{inference_job_id}/status")
def query_inference_status(inference_job_id: uuid.UUID, db: Session = Depends(get_session)):
    _expire_overdue_jobs(db)
    job = db.get(InferenceJob, inference_job_id)
    if job is None:
        raise framework_error(FrameworkError.INFERENCE_JOB_NOT_FOUND, detail="no such inference job")
    return {"inferenceJobId": str(job.inference_job_id), "status": job.status,
            "nfDeploymentId": str(job.nf_deployment_id) if job.nf_deployment_id else None,
            "timeoutSeconds": job.timeout_seconds, "startedAt": _aware(job.started_at).isoformat()}


@app.post("/inference-jobs/{inference_job_id}/resolve")
def resolve_inference(inference_job_id: uuid.UUID, succeeded: bool, body: ResolveInferenceRequest | None = None,
                      db: Session = Depends(get_session)):
    _expire_overdue_jobs(db)
    job = db.get(InferenceJob, inference_job_id)
    if job is None:
        raise framework_error(FrameworkError.INFERENCE_JOB_NOT_FOUND, detail="no such inference job")
    if job.status != InferenceState.RUNNING:
        # Wave 7: e.g. already failed on its 5 s timeout — a late result is refused.
        raise framework_error(FrameworkError.TRAINING_JOB_ILLEGAL_TRANSITION,
                               detail=f"cannot resolve an inference job in status {job.status}")
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
    _expire_overdue_jobs(db)
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

    HISTORY.md §7's `MLMFSubscription` finding, closed: `notification_destination`
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
    """HISTORY.md §7's `MLMFSubscription` finding, closed: previously
    this subscription could only be created and read, never torn down —
    idempotent, matching every other subscription-shaped resource's own
    unsubscribe route (DME/MDAF/Intent Service).
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
    db.flush()  # the report's id, for the notification

    # HISTORY.md §7's `MLMFSubscription` finding, closed: the subscriber's
    # push is a transactional-outbox row (PR-MSG-1.7), sent once the report
    # commits — an unreachable subscriber never fails the report call that
    # triggered it, and a crash after the commit no longer loses the push.
    enqueue(db, sub.notification_destination, {
        "reportId": str(report.id), "modelId": str(sub.model_id), "metrics": metrics, "breachedFloor": breached,
    })
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
    certified) is skipped rather than forced — a group retrain is a
    PROMOTED model's retrain, never a first cycle or a recovery.
    """
    retrained_model_ids: list[uuid.UUID] = []
    for raw_member_id in group["memberModelIds"]:
        member_id = uuid.UUID(raw_member_id)
        if _get_model_or_none(member_id) is None:
            continue
        lifecycle = _get_or_create_lifecycle(db, member_id)
        if lifecycle.model_lifecycle_state != ModelLifecycleState.PROMOTED:
            continue
        # HISTORY.md OI-6.2 / Wave 4: the same start path (NFO
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
            "nfDeploymentId": str(j.nf_deployment_id) if j.nf_deployment_id else None,
            "runtimeProfile": j.runtime_profile, "timeoutSeconds": j.timeout_seconds,
            "currentStep": j.current_step, "steps": _training_steps(j)}


# OI-5-aiml-trainingjob-steps: what the current step shows for each job status
_CURRENT_STEP_STATUS = {"NOT_STARTED": "NOT_STARTED", "IN_PROGRESS": "IN_PROGRESS", "SUSPENDED": "SUSPENDED",
                        "FAILED": "FAILED", "CANCELLED": "CANCELLED", "FINISHED": "FINISHED"}


def _training_steps(job: TrainingJob) -> dict:
    """Each step's status, derived from the job's `status` and the furthest
    step its runtime reported (`current_step`): steps before it FINISHED,
    the current one carries the job's state (IN_PROGRESS, SUSPENDED, or
    how the run ended), later ones NOT_STARTED. A FINISHED run finished
    every step — completion is the runtime's report that the trained
    model was produced, whichever step it last reported."""
    if job.status == "FINISHED":
        return {step: "FINISHED" for step in TRAINING_STEPS}
    current = TRAINING_STEPS.index(job.current_step)
    return {step: ("FINISHED" if i < current else _CURRENT_STEP_STATUS[job.status] if i == current else "NOT_STARTED")
            for i, step in enumerate(TRAINING_STEPS)}


def _validation_job_view(j: ValidationJob) -> dict:
    return {"validationJobId": str(j.validation_job_id), "modelId": str(j.model_id) if j.model_id else None,
            "modelCoordinationGroupId": str(j.model_coordination_group_id) if j.model_coordination_group_id else None,
            "trainingJobId": str(j.training_job_id) if j.training_job_id else None,
            "producerId": j.producer_id, "validationCriteria": j.validation_criteria or {},
            "status": j.status, "metrics": j.metrics or {},
            "outcomeArtifactDmeTypeId": str(j.outcome_artifact_dme_type_id) if j.outcome_artifact_dme_type_id else None,
            "nfDeploymentId": str(j.nf_deployment_id) if j.nf_deployment_id else None,
            "runtimeProfile": j.runtime_profile, "timeoutSeconds": j.timeout_seconds}


def _emulation_job_view(j: EmulationJob) -> dict:
    return {"emulationJobId": str(j.emulation_job_id), "modelId": str(j.model_id), "producerId": j.producer_id,
            "aIMLInferenceEmulationFunctionRef": str(j.aiml_inference_emulation_function_id) if j.aiml_inference_emulation_function_id else None,
            "emulationCriteria": j.emulation_criteria or {}, "status": j.status, "metrics": j.metrics or {},
            "outcomeArtifactDmeTypeId": str(j.outcome_artifact_dme_type_id) if j.outcome_artifact_dme_type_id else None,
            "nfDeploymentId": str(j.nf_deployment_id) if j.nf_deployment_id else None,
            "runtimeProfile": j.runtime_profile, "timeoutSeconds": j.timeout_seconds}


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
        "runtimeProfile": l.runtime_profile,
    }


# ---------------------------------------------------------------- Feature groups

@app.post("/feature-groups", status_code=201)
def create_feature_group(body: CreateFeatureGroupRequest, db: Session = Depends(get_session)):
    """HISTORY.md §5: no feature-group/feature-store concept
    existed at all. Matches the reference's own
    CreateFeatureGroup (featuregroup_controller.py): name must be
    `\\w+` (word characters only) and 3-63 characters long, and a
    duplicate `featureGroupName` 409s, matching `DBException`
    ("already exist") there.

    OI-5-aiml-featuregroup-dme: with `enableDme`, the group's DME data job
    is created first, like the reference's create_dme_filtered_data_job —
    a CONTINUOUS TRAINING-stage job of `dmeTypeId`, consumer
    `aimgf:feature-group:<name>`, whose production job definition carries
    the group's features and filters. If DME refuses it (an unknown type, a
    definition its schema rejects, a delivery method no offer commits to)
    the group is not created: 422 FEATURE_GROUP_DME_JOB_REFUSED with DME's
    reason. `dmeTypeId` is required with `enableDme`.
    """
    if not re.fullmatch(r"\w+", body.featureGroupName) or not (3 <= len(body.featureGroupName) <= 63):
        raise framework_error(FrameworkError.FEATURE_GROUP_NAME_INVALID, detail=f"featureGroupName {body.featureGroupName!r} must be 3-63 word characters")
    if db.scalar(select(FeatureGroup).where(FeatureGroup.feature_group_name == body.featureGroupName)) is not None:
        raise framework_error(FrameworkError.FEATURE_GROUP_ALREADY_REGISTERED, detail=f"feature group {body.featureGroupName!r} already exists")
    if body.enableDme and body.dmeTypeId is None:
        raise framework_error(FrameworkError.FEATURE_GROUP_DME_JOB_REFUSED, detail="enableDme needs the dmeTypeId the group's data job collects")
    data_job_id = _create_feature_group_data_job(body) if body.enableDme else None
    group = FeatureGroup(
        feature_group_name=body.featureGroupName, feature_list=body.featureList, datalake_source=body.datalakeSource,
        host=body.host, port=body.port, bucket=body.bucket, token=body.token, db_org=body.dbOrg,
        measurement=body.measurement, enable_dme=body.enableDme, measured_obj_class=body.measuredObjClass,
        dme_port=body.dmePort, source_name=body.sourceName,
        dme_type_id=body.dmeTypeId if body.enableDme else None, dme_data_job_id=data_job_id,
    )
    db.add(group)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        _terminate_feature_group_data_job(data_job_id)  # lost a race on the name: don't leave the job behind
        raise framework_error(FrameworkError.FEATURE_GROUP_ALREADY_REGISTERED, detail=f"feature group {body.featureGroupName!r} already exists") from exc
    return _feature_group_view(group)


def _create_feature_group_data_job(body: CreateFeatureGroupRequest) -> uuid.UUID:
    definition = {"featureGroupName": body.featureGroupName,
                  "features": [f.strip() for f in body.featureList.split(",") if f.strip()]}
    definition.update({k: v for k, v in (("measuredObjClass", body.measuredObjClass), ("sourceName", body.sourceName),
                                         ("measurement", body.measurement)) if v})
    resp = _r1.post("/dme/data-jobs", json={
        "dataDeliveryMode": "CONTINUOUS", "dmeTypeId": str(body.dmeTypeId), "productionJobDefinition": definition,
        "dataDeliveryMethod": body.dataDeliveryMethod, "deliveryDetails": {}, "lifecycleStage": "TRAINING",
        "consumerId": f"aimgf:feature-group:{body.featureGroupName}",
    })
    if resp.status_code >= 300:
        try:
            reason = resp.json().get("detail")
        except ValueError:
            reason = None
        if isinstance(reason, dict):
            reason = f"{reason.get('title')}: {reason.get('detail')}"
        raise framework_error(FrameworkError.FEATURE_GROUP_DME_JOB_REFUSED,
                              detail=f"DME refused the data job ({resp.status_code}): {reason or 'no reason given'}")
    return uuid.UUID(resp.json()["dataJobId"])


def _terminate_feature_group_data_job(data_job_id: uuid.UUID | None) -> str:
    """Best effort, like every other teardown here: DONE, SKIPPED or FAILED."""
    if data_job_id is None:
        return "SKIPPED"
    try:
        resp = _r1.delete(f"/dme/data-jobs/{data_job_id}")
    except httpx.HTTPError as exc:
        return f"FAILED: {exc.__class__.__name__}"
    return "DONE" if resp.status_code < 300 or resp.status_code == 404 else f"FAILED: HTTP {resp.status_code}"


@app.get("/feature-groups/{feature_group_name}")
def get_feature_group(feature_group_name: str, db: Session = Depends(get_session)):
    return _feature_group_view(_feature_group_or_404(db, feature_group_name))


@app.delete("/feature-groups/{feature_group_name}")
def delete_feature_group(feature_group_name: str, db: Session = Depends(get_session)):
    """DeleteFeatureGroup — and, for an enable_dme group, terminates its DME
    data job (best effort; the outcome is in `dmeDataJobTeardown`)."""
    group = _feature_group_or_404(db, feature_group_name)
    teardown = _terminate_feature_group_data_job(group.dme_data_job_id)
    db.delete(group)
    db.commit()
    return {"featureGroupName": feature_group_name, "dmeDataJobTeardown": teardown}


def _feature_group_or_404(db: Session, name: str) -> FeatureGroup:
    group = db.scalar(select(FeatureGroup).where(FeatureGroup.feature_group_name == name))
    if group is None:
        raise framework_error(FrameworkError.FEATURE_GROUP_NOT_FOUND, detail=f"no feature group {name!r}")
    return group


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
        "dmeTypeId": str(g.dme_type_id) if g.dme_type_id else None,
        "dmeDataJobId": str(g.dme_data_job_id) if g.dme_data_job_id else None,
    }


# ---------------------------------------------------------------- Wave 4: TS 28.105 NRM resources
# Imported last: app/nrm.py reuses the helpers above (_start_training,
# _start_validation, _deploy_runtime, ...), so it can only be loaded once
# they exist.
# Bound here, at load time — never imported lazily inside a route: the
# integration mesh's loader (tests_integration/loader.py) evicts `app.*`
# from sys.modules after loading each service, so a call-time
# `from .nrm import ...` would fail there.
from .nrm import advance_ml_update_process as _advance_ml_update_process, router as _nrm_router  # noqa: E402

app.include_router(_nrm_router)
