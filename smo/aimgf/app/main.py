"""AIMgF (AI Management Function) — TS 28.105 AI/ML NRM realization.

Wave 1 of the AI Platform Service Decomposition: split out of the former
flat `ai-ml-workflow/` module (see docs/architecture/AI_PLATFORM_BASELINE.md
and docs/ownership/AIMGF_OWNERSHIP.md). AIMgF is the AI lifecycle
orchestrator — it decides *what state* a model or runtime is in and
*whether* a transition is allowed; it does not store the model row itself
(MLMR's own repository truth) or perform loading/activation (MLLF's).

Every place this module used to read/write `AIMLModel.state`/
`training_job_id` directly via the ORM now calls MLMR through R1Client
instead (`_get_model`/`_patch_model_lifecycle` below) — the same
cross-module-call shape this build already uses everywhere else (e.g.
NFO calling FOCOM's `/inventory`), not a distributed transaction: a
`_patch_model_lifecycle` call that fails after `db.commit()` here leaves
AIMgF's own TrainingJob/state decision recorded but MLMR's row stale,
the same no-two-phase-commit honesty this build already carries for
every other cross-module write (e.g. DME's "unreachable callback never
fails the primary operation").
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

from .models import FeatureGroup, InferenceJob, MLMFSubscription, PerformanceReport, TrainingJob
from .statemachine import AIML_MODEL_FSM, INFERENCE_JOB_FSM, InferenceEvent, InferenceState, ModelEvent, ModelState, should_trigger_group_retrain

app = FastAPI(title="AIMgF")

_mlmr = R1Client()


@app.get("/health")
def health_check():
    """Liveness probe. The GUI BFF's GET /modules/status fans out to
    /<module>/health through R1 Termination for every module in parallel.
    """
    return {"status": "healthy"}


def _get_model_or_none(model_id: uuid.UUID) -> dict | None:
    resp = _mlmr.get(f"/mlmr/models/{model_id}")
    return resp.json() if resp.status_code == 200 else None


def _get_model(model_id: uuid.UUID) -> dict:
    model = _get_model_or_none(model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="no such model")
    return model


def _patch_model_lifecycle(model_id: uuid.UUID, **fields) -> dict:
    resp = _mlmr.patch(f"/mlmr/models/{model_id}/lifecycle", json=fields)
    return resp.json()


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


@app.post("/training-jobs", status_code=201)
def request_training(body: RequestTrainingRequest, db: Session = Depends(get_session)):
    """RequestTraining — exactly one of modelId/modelCoordinationGroupId,
    enforced at the DB layer (exactly_one_target constraint) and checked
    here for a clean error.

    modelId-targeted requests also drive the model's own FSM:
    REGISTERED -> TRAINING (TRAIN, the very first cycle) or
    ACTIVE -> TRAINING (RETRAIN, an ordinary retrain). A model already
    TRAINING (an unresolved prior job) is treated as the operator's
    explicit decision to supersede it: the orphaned job is marked
    CANCELLED rather than left silently RUNNING and unreachable.
    """
    if (body.modelId is None) == (body.modelCoordinationGroupId is None):
        raise framework_error(FrameworkError.COORDINATION_GROUP_MISMATCH)

    model = _get_model(body.modelId) if body.modelId else None
    if model is not None and model["state"] not in (ModelState.REGISTERED, ModelState.ACTIVE, ModelState.TRAINING):
        raise framework_error(FrameworkError.MODEL_NOT_CERTIFIED, detail=f"cannot (re)train a model in state {model['state']}")

    # TS28.105 AI/ML NRM's own real mLTrainingType — INITIAL_TRAINING the
    # very first cycle (model still REGISTERED), RE_TRAINING every other
    # case.
    ml_training_type = "INITIAL_TRAINING" if model is not None and model["state"] == ModelState.REGISTERED else "RE_TRAINING"

    job = TrainingJob(model_id=body.modelId, model_coordination_group_id=body.modelCoordinationGroupId,
                       producer_id=body.producerId, required_data=body.requiredData,
                       validation_criteria=body.validationCriteria, notification_uri=body.notificationUri,
                       status="RUNNING", run_id=body.runId, training_dataset=body.trainingDataset,
                       validation_dataset=body.validationDataset, consumer_rapp_id=body.consumerRappId,
                       producer_rapp_id=body.producerRappId, ml_training_type=ml_training_type)
    db.add(job)
    db.flush()

    if model is not None:
        new_state = None
        if model["state"] == ModelState.TRAINING:
            existing_job_id = model.get("trainingJobId")
            if existing_job_id is not None:
                orphaned = db.get(TrainingJob, uuid.UUID(existing_job_id))
                if orphaned is not None and orphaned.status == "RUNNING":
                    orphaned.status = "CANCELLED"
        else:
            event = ModelEvent.RETRAIN if model["state"] == ModelState.ACTIVE else ModelEvent.TRAIN
            new_state = AIML_MODEL_FSM.fire(ModelState(model["state"]), event)
        _patch_model_lifecycle(body.modelId, state=new_state, trainingJobId=str(job.training_job_id))

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


@app.post("/models/{model_id}/advance")
def advance_model_lifecycle(model_id: uuid.UUID, event: str, db: Session = Depends(get_session)):
    """Single endpoint driving TRAINING_COMPLETE -> VALIDATION_COMPLETE ->
    CERTIFY -> LOAD -> ACTIVATE -> RETRAIN -> DEPRECATE, each a real FSM
    transition (statemachine.py). One endpoint rather than six nearly
    identical ones, since the pattern is mechanically the same at every step.
    """
    model = _get_model(model_id)
    new_state = AIML_MODEL_FSM.fire(ModelState(model["state"]), ModelEvent(event))
    return _patch_model_lifecycle(model_id, state=new_state)


@app.post("/models/{model_id}/inference-jobs", status_code=201)
def request_inference(model_id: uuid.UUID, notification_destination: str | None = None, db: Session = Depends(get_session)):
    """RequestInference — MLEF-hosted (AI/ML Workflow LLD section 3)."""
    model = _get_model(model_id)
    if model["state"] != ModelState.ACTIVE:
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


def _find_coordination_group_for_model(model_id: uuid.UUID) -> dict | None:
    resp = _mlmr.get("/mlmr/coordination-groups")
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


@app.get("/training-jobs")
def list_training_jobs(model_id: uuid.UUID | None = None, status: str | None = None, db: Session = Depends(get_session)):
    stmt = select(TrainingJob)
    if model_id:
        stmt = stmt.where(TrainingJob.model_id == model_id)
    if status:
        stmt = stmt.where(TrainingJob.status == status)
    return [_training_job_view(j) for j in db.scalars(stmt).all()]


@app.get("/inference-jobs")
def list_inference_jobs(model_id: uuid.UUID | None = None, status: str | None = None, db: Session = Depends(get_session)):
    stmt = select(InferenceJob)
    if model_id:
        stmt = stmt.where(InferenceJob.model_id == model_id)
    if status:
        stmt = stmt.where(InferenceJob.status == status)
    return [{"inferenceJobId": str(j.inference_job_id), "modelId": str(j.model_id), "status": j.status,
             "notificationDestination": j.notification_destination} for j in db.scalars(stmt).all()]


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


def _training_job_view(j: TrainingJob) -> dict:
    return {"trainingJobId": str(j.training_job_id), "modelId": str(j.model_id) if j.model_id else None,
            "modelCoordinationGroupId": str(j.model_coordination_group_id) if j.model_coordination_group_id else None,
            "producerId": j.producer_id, "status": j.status, "runId": j.run_id,
            "trainingDataset": j.training_dataset, "validationDataset": j.validation_dataset,
            "modelMetrics": j.model_metrics, "mlTrainingType": j.ml_training_type}


def _performance_report_view(r: PerformanceReport) -> dict:
    return {"reportId": str(r.id), "subscriptionId": str(r.subscription_id), "metrics": r.metrics,
            "breachedFloor": r.breached_floor, "reportedAt": r.reported_at.isoformat()}


def _trigger_group_retrain(db: Session, group: dict) -> list[uuid.UUID]:
    """Fires RETRAIN (the same ACTIVE -> TRAINING transition RequestTraining's
    own modelId-targeted path uses) and creates a per-model TrainingJob for
    every currently ACTIVE member. A member not in ACTIVE (already TRAINING
    from an earlier trigger, or never certified) is skipped rather than
    forced — RETRAIN is only a legal transition from ACTIVE.
    """
    retrained_model_ids: list[uuid.UUID] = []
    for raw_member_id in group["memberModelIds"]:
        member_id = uuid.UUID(raw_member_id)
        member = _get_model_or_none(member_id)
        if member is None or member["state"] != ModelState.ACTIVE:
            continue
        job = TrainingJob(model_id=member_id, producer_id="aimgf:group-retrain", status="RUNNING", ml_training_type="RE_TRAINING")
        db.add(job)
        db.flush()
        new_state = AIML_MODEL_FSM.fire(ModelState.ACTIVE, ModelEvent.RETRAIN)
        _patch_model_lifecycle(member_id, state=new_state, trainingJobId=str(job.training_job_id))
        retrained_model_ids.append(member_id)
    db.commit()
    return retrained_model_ids


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
