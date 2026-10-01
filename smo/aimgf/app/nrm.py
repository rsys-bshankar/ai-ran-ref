"""TS 28.105 AI/ML NRM at REST level — Wave 4 (WAVES_4_TO_10_WORK_ITEMS.md
W4-04, decision D-9). Every IOC of specs/5G_APIs/TS28105_AiMlNrm.yaml that
AIMgF owns, as a flat REST resource carrying the spec's own attribute
names (`{"id": ..., "attributes": {...}}`, the same id/attributes split
the spec's own `-Single` schemas use):

  MLTrainingFunction / MLTrainingRequest / MLTrainingProcess / MLTrainingReport
  MLTestingFunction / MLTestingRequest / MLTestingReport
  AIMLInferenceFunction / AIMLInferenceReport / AIMLInferenceEmulationFunction
  MLModelLoadingRequest / MLModelLoadingPolicy / MLModelLoadingProcess
  MLUpdateFunction / MLUpdateRequest / MLUpdateProcess / MLUpdateReport

(MLModel / MLModelRepository / MLModelCoordinationGroup are MLMR's.)

Requests are not a parallel bookkeeping layer: an MLTrainingRequest *is*
a TrainingJob and an MLTestingRequest *is* a ValidationJob, started through
the same `_start_training`/`_start_validation` core as the original job
routes, so the lifecycle gates, NFO execution runtimes and operator
approvals all still apply. The one recorded deviation from the spec is
addressing: DN-typed references are carried as this build's resource ids.
"""

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.pagination import PageLimit, PageOffset, paginate

from . import ts28105
from .main import (
    _activate_runtime, _cancel_training_job, _deploy_runtime, _fire_model_event, _get_model, _get_or_create_lifecycle, _nfo_terminate_execution,
    _start_training, _start_validation, _sync_training_process, _validate_dme_data_job_ids,
)
from .models import (
    AIMLInferenceEmulationFunction, AIMLInferenceFunction, AIMLInferenceReport, InferenceJob,
    MLModelLoadingPolicy, MLModelLoadingProcess, MLModelLoadingRequest, MLTestingFunction, MLTestingReport,
    MLTrainingFunction, MLTrainingProcess, MLTrainingReport, MLUpdateFunction, MLUpdateProcess, MLUpdateReport,
    MLUpdateRequest, TrainingJob, ValidationJob,
)
from .statemachine import ModelLifecycleEvent, ModelLifecycleState, RuntimeLifecycleState

router = APIRouter()


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _s(value) -> str | None:
    return str(value) if value is not None else None


def _get(db: Session, cls, object_id: uuid.UUID, name: str):
    obj = db.get(cls, object_id)
    if obj is None:
        raise framework_error(FrameworkError.NRM_OBJECT_NOT_FOUND, detail=f"no such {name} {object_id}")
    return obj


def _page(db: Session, stmt, limit: int, offset: int, view) -> dict:
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [view(o) for o in page["items"]]}


def _monitor(status: str, pct: int, state_info: str | None, result_info: str | None) -> dict:
    return {"status": status, "progressPercentage": pct, "progressStateInfo": state_info, "resultStateInfo": result_info}


# TrainingJob/ValidationJob status -> TS 28.105 RequestStatus. ValidationJob's
# own COMPLETED/FAILED both mean the request FINISHED; the outcome is in its
# MLTestingReport (mLTestingResult), the same place the spec puts it.
_VALIDATION_REQUEST_STATUS = {"RUNNING": "IN_PROGRESS", "SUSPENDED": "SUSPENDED", "COMPLETED": "FINISHED",
                              "FAILED": "FINISHED", "CANCELLED": "CANCELLED"}


# ================================================================ MLTrainingFunction

class MLTrainingFunctionBody(_Body):
    userLabel: str | None = None
    supportedLearningTechnology: ts28105.SupportedLearningTechnology | None = None
    fLParticipationInfo: ts28105.FLParticipationInfo | None = None
    mLKnowledge: ts28105.MLKnowledge | None = None
    mLModelRepositoryRef: uuid.UUID | None = None


def _training_function_view(f: MLTrainingFunction) -> dict:
    return {"id": str(f.ml_training_function_id), "attributes": {
        "userLabel": f.user_label, "supportedLearningTechnology": f.supported_learning_technology,
        "fLParticipationInfo": f.fl_participation_info, "mLKnowledge": f.ml_knowledge,
        "mLTrainingType": f.ml_training_type, "mLModelRepositoryRef": _s(f.ml_model_repository_ref)}}


def _apply_training_function(f: MLTrainingFunction, body: MLTrainingFunctionBody) -> None:
    f.user_label = body.userLabel
    f.supported_learning_technology = ts28105.dump(body.supportedLearningTechnology)
    f.fl_participation_info = ts28105.dump(body.fLParticipationInfo)
    f.ml_knowledge = ts28105.dump(body.mLKnowledge)
    f.ml_model_repository_ref = body.mLModelRepositoryRef


@router.post("/ml-training-functions", status_code=201)
def create_ml_training_function(body: MLTrainingFunctionBody, db: Session = Depends(get_session)):
    f = MLTrainingFunction()
    _apply_training_function(f, body)
    db.add(f)
    db.commit()
    return _training_function_view(f)


@router.get("/ml-training-functions")
def list_ml_training_functions(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    return _page(db, select(MLTrainingFunction), limit, offset, _training_function_view)


@router.get("/ml-training-functions/{function_id}")
def get_ml_training_function(function_id: uuid.UUID, db: Session = Depends(get_session)):
    return _training_function_view(_get(db, MLTrainingFunction, function_id, "MLTrainingFunction"))


@router.put("/ml-training-functions/{function_id}")
def replace_ml_training_function(function_id: uuid.UUID, body: MLTrainingFunctionBody, db: Session = Depends(get_session)):
    f = _get(db, MLTrainingFunction, function_id, "MLTrainingFunction")
    _apply_training_function(f, body)
    db.commit()
    return _training_function_view(f)


@router.delete("/ml-training-functions/{function_id}", status_code=204)
def delete_ml_training_function(function_id: uuid.UUID, db: Session = Depends(get_session)):
    f = db.get(MLTrainingFunction, function_id)
    if f is not None:
        db.delete(f)
        db.commit()


# ================================================================ MLTrainingRequest (= TrainingJob)

class MLTrainingRequestBody(_Body):
    mLModelRef: uuid.UUID | None = None
    mLModelCoordinationGroupRef: uuid.UUID | None = None
    mLTrainingFunctionRef: uuid.UUID | None = None
    trainingRequestSource: str
    aIMLInferenceName: str | None = None
    fLRequirement: ts28105.FLRequirement | None = None
    candidateTrainingDataSource: list[str] = []
    trainingDataQualityScore: float | None = None
    performanceRequirements: list[ts28105.ModelPerformance] | None = None
    rLRequirement: ts28105.RLRequirement | None = None
    trainingDataStatisticalProperties: ts28105.DataStatisticalProperties | None = None
    distributedTrainingExpectation: ts28105.DistributedTrainingExpectation | None = None
    mLKnowledgeName: str | None = None
    mLTrainingType: ts28105.MLTrainingType | None = None
    expectedInferenceScope: list[str] | None = None
    clusteringInfo: list[ts28105.ClusteringCriteria] | None = None
    # Not spec attributes — this build's own, same as on POST /training-jobs.
    dmeDataJobIds: list[uuid.UUID] = []
    notificationUri: str | None = None


class RequestFlagsBody(_Body):
    """cancelRequest / suspendRequest — the spec's in-place control flags."""
    cancelRequest: bool | None = None
    suspendRequest: bool | None = None


def _training_request_view(j: TrainingJob) -> dict:
    return {"id": str(j.training_job_id), "attributes": {
        "aIMLInferenceName": j.aiml_inference_name, "fLRequirement": j.fl_requirement,
        "candidateTrainingDataSource": j.candidate_training_data_source or [],
        "trainingDataQualityScore": j.training_data_quality_score,
        "trainingRequestSource": j.training_request_source or j.producer_id,
        "requestStatus": j.status, "performanceRequirements": j.performance_requirements,
        "rLRequirement": j.rl_requirement, "cancelRequest": j.cancel_request, "suspendRequest": j.suspend_request,
        "trainingDataStatisticalProperties": j.training_data_statistical_properties,
        "distributedTrainingExpectation": j.distributed_training_expectation,
        "mLKnowledgeName": j.ml_knowledge_name, "mLTrainingType": j.ml_training_type,
        "expectedInferenceScope": j.expected_inference_scope, "clusteringInfo": j.clustering_info,
        "mLModelRef": _s(j.model_id), "mLModelCoordinationGroupRef": _s(j.model_coordination_group_id),
        "mLTrainingFunctionRef": _s(j.ml_training_function_id)}}


@router.post("/ml-training-requests", status_code=201)
def create_ml_training_request(body: MLTrainingRequestBody, db: Session = Depends(get_session)):
    if body.mLTrainingFunctionRef is not None:
        _get(db, MLTrainingFunction, body.mLTrainingFunctionRef, "MLTrainingFunction")
    if (body.mLModelRef is None) == (body.mLModelCoordinationGroupRef is None):
        raise framework_error(FrameworkError.COORDINATION_GROUP_MISMATCH)
    _validate_dme_data_job_ids(body.dmeDataJobIds)
    job = _start_training(
        db, model_id=body.mLModelRef, group_id=body.mLModelCoordinationGroupRef, producer_id=body.trainingRequestSource,
        ml_training_type=body.mLTrainingType, ml_training_function_id=body.mLTrainingFunctionRef,
        training_request_source=body.trainingRequestSource, aiml_inference_name=body.aIMLInferenceName,
        fl_requirement=ts28105.dump(body.fLRequirement), candidate_training_data_source=body.candidateTrainingDataSource,
        training_data_quality_score=body.trainingDataQualityScore,
        performance_requirements=ts28105.dump(body.performanceRequirements),
        rl_requirement=ts28105.dump(body.rLRequirement),
        training_data_statistical_properties=ts28105.dump(body.trainingDataStatisticalProperties),
        distributed_training_expectation=ts28105.dump(body.distributedTrainingExpectation),
        ml_knowledge_name=body.mLKnowledgeName, expected_inference_scope=body.expectedInferenceScope,
        clustering_info=ts28105.dump(body.clusteringInfo), dme_data_job_ids=body.dmeDataJobIds,
        notification_uri=body.notificationUri,
    )
    db.commit()
    return _training_request_view(job)


@router.get("/ml-training-requests")
def list_ml_training_requests(ml_training_function_id: uuid.UUID | None = None, request_status: str | None = None,
                              limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    stmt = select(TrainingJob)
    if ml_training_function_id:
        stmt = stmt.where(TrainingJob.ml_training_function_id == ml_training_function_id)
    if request_status:
        stmt = stmt.where(TrainingJob.status == request_status)
    return _page(db, stmt, limit, offset, _training_request_view)


@router.get("/ml-training-requests/{request_id}")
def get_ml_training_request(request_id: uuid.UUID, db: Session = Depends(get_session)):
    return _training_request_view(_get(db, TrainingJob, request_id, "MLTrainingRequest"))


def _apply_training_flags(db: Session, job: TrainingJob, cancel: bool | None, suspend: bool | None) -> None:
    if cancel:
        if job.status in ("FINISHED", "FAILED", "CANCELLED"):
            raise framework_error(FrameworkError.TRAINING_JOB_ILLEGAL_TRANSITION,
                                   detail=f"cannot cancel a training request in status {job.status}")
        _cancel_training_job(db, job)
        return
    if suspend is True:
        if job.status != "IN_PROGRESS":
            raise framework_error(FrameworkError.TRAINING_JOB_ILLEGAL_TRANSITION,
                                   detail=f"cannot suspend a training request in status {job.status}")
        job.status, job.suspend_request = "SUSPENDED", True
    elif suspend is False and job.status == "SUSPENDED":
        job.status, job.suspend_request = "IN_PROGRESS", False
    _sync_training_process(db, job)


@router.patch("/ml-training-requests/{request_id}")
def modify_ml_training_request(request_id: uuid.UUID, body: RequestFlagsBody, db: Session = Depends(get_session)):
    job = _get(db, TrainingJob, request_id, "MLTrainingRequest")
    _apply_training_flags(db, job, body.cancelRequest, body.suspendRequest)
    db.commit()
    return _training_request_view(job)


@router.delete("/ml-training-requests/{request_id}", status_code=204)
def delete_ml_training_request(request_id: uuid.UUID, db: Session = Depends(get_session)):
    """Deleting an in-flight request cancels it (the job row is kept as
    history, like `DELETE /training-jobs/{id}`)."""
    job = db.get(TrainingJob, request_id)
    if job is not None and job.status not in ("FINISHED", "FAILED", "CANCELLED"):
        _cancel_training_job(db, job)
        db.commit()


# ================================================================ MLTrainingProcess

class ProcessFlagsBody(_Body):
    priority: int | None = None
    terminationConditions: str | None = None
    cancelProcess: bool | None = None
    suspendProcess: bool | None = None


def _training_process_view_for(db: Session):
    def view(p: MLTrainingProcess) -> dict:
        job = db.get(TrainingJob, p.training_job_id)
        report = db.scalars(select(MLTrainingReport).where(MLTrainingReport.training_job_id == p.training_job_id)
                            .order_by(MLTrainingReport.created_at.desc())).first()
        return {"id": str(p.ml_training_process_id), "attributes": {
            "priority": p.priority, "terminationConditions": p.termination_conditions,
            "progressStatus": _monitor(p.status, p.progress_percentage, p.progress_state_info, p.result_state_info),
            "cancelProcess": p.cancel_process, "suspendProcess": p.suspend_process,
            "trainingRequestRef": [str(p.training_job_id)],
            "participatingFLClientRefList": p.participating_fl_client_refs or [],
            "trainingReportRef": _s(report.ml_training_report_id) if report else None,
            "mLModelRef": _s(job.model_id) if job else None,
            "mLModelCoordinationGroupRef": _s(job.model_coordination_group_id) if job else None}}
    return view


@router.get("/ml-training-processes")
def list_ml_training_processes(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    return _page(db, select(MLTrainingProcess), limit, offset, _training_process_view_for(db))


@router.get("/ml-training-processes/{process_id}")
def get_ml_training_process(process_id: uuid.UUID, db: Session = Depends(get_session)):
    return _training_process_view_for(db)(_get(db, MLTrainingProcess, process_id, "MLTrainingProcess"))


@router.patch("/ml-training-processes/{process_id}")
def modify_ml_training_process(process_id: uuid.UUID, body: ProcessFlagsBody, db: Session = Depends(get_session)):
    """priority/terminationConditions are plain writes; cancelProcess/
    suspendProcess act on the run exactly like the request's own flags."""
    p = _get(db, MLTrainingProcess, process_id, "MLTrainingProcess")
    if body.priority is not None:
        p.priority = body.priority
    if body.terminationConditions is not None:
        p.termination_conditions = body.terminationConditions
    _apply_training_flags(db, db.get(TrainingJob, p.training_job_id), body.cancelProcess, body.suspendProcess)
    db.commit()
    return _training_process_view_for(db)(p)


@router.post("/ml-training-processes/{process_id}/progress")
def report_ml_training_progress(process_id: uuid.UUID, body: ts28105.ProcessMonitorUpdate, db: Session = Depends(get_session)):
    """The MLTF execution runtime's own progress write-back (ProcessMonitor).
    Only meaningful while the run is RUNNING; completion itself still goes
    through `POST /training-jobs/{id}/complete`."""
    p = _get(db, MLTrainingProcess, process_id, "MLTrainingProcess")
    if p.status != "RUNNING":
        raise framework_error(FrameworkError.TRAINING_JOB_ILLEGAL_TRANSITION,
                               detail=f"cannot report progress on a process in status {p.status}")
    p.progress_percentage = body.progressPercentage
    p.progress_state_info = body.progressStateInfo
    if body.resultStateInfo is not None:
        p.result_state_info = body.resultStateInfo
    db.commit()
    return _training_process_view_for(db)(p)


# ================================================================ MLTrainingReport

def _training_report_view(r: MLTrainingReport) -> dict:
    return {"id": str(r.ml_training_report_id), "attributes": {
        "usedConsumerTrainingData": r.used_consumer_training_data or [],
        "modelConfidenceIndication": r.model_confidence_indication,
        "modelPerformanceTraining": r.model_performance_training or [],
        "modelPerformanceValidation": r.model_performance_validation or [],
        "dataRatioTrainingAndValidation": r.data_ratio_training_and_validation,
        "areNewTrainingDataUsed": r.are_new_training_data_used, "fLReportPerClient": r.fl_report_per_client or [],
        "trainingProcessRef": None, "trainingRequestRef": str(r.training_job_id),
        "lastTrainingRef": _s(r.last_training_report_id), "mLModelGeneratedRef": _s(r.ml_model_generated_ref),
        "mLModelCoordinationGroupGeneratedRef": _s(r.ml_model_coordination_group_generated_ref),
        "mLTrainingFunctionRef": _s(r.ml_training_function_id), "createdAt": r.created_at.isoformat()}}


def _training_report_view_for(db: Session):
    def view(r: MLTrainingReport) -> dict:
        out = _training_report_view(r)
        process = db.scalar(select(MLTrainingProcess).where(MLTrainingProcess.training_job_id == r.training_job_id))
        out["attributes"]["trainingProcessRef"] = _s(process.ml_training_process_id) if process else None
        return out
    return view


@router.get("/ml-training-reports")
def list_ml_training_reports(model_id: uuid.UUID | None = None, limit: int = PageLimit, offset: int = PageOffset,
                             db: Session = Depends(get_session)):
    stmt = select(MLTrainingReport)
    if model_id:
        stmt = stmt.where(MLTrainingReport.ml_model_generated_ref == model_id)
    return _page(db, stmt.order_by(MLTrainingReport.created_at.desc()), limit, offset, _training_report_view_for(db))


@router.get("/ml-training-reports/{report_id}")
def get_ml_training_report(report_id: uuid.UUID, db: Session = Depends(get_session)):
    return _training_report_view_for(db)(_get(db, MLTrainingReport, report_id, "MLTrainingReport"))


# ================================================================ MLTestingFunction

class UserLabelBody(_Body):
    userLabel: str | None = None


def _testing_function_view_for(db: Session):
    def view(f: MLTestingFunction) -> dict:
        model_ids = db.scalars(select(ValidationJob.model_id).where(
            ValidationJob.ml_testing_function_id == f.ml_testing_function_id, ValidationJob.model_id.is_not(None))).all()
        return {"id": str(f.ml_testing_function_id), "attributes": {
            "userLabel": f.user_label, "mLModelRef": sorted({str(m) for m in model_ids})}}
    return view


@router.post("/ml-testing-functions", status_code=201)
def create_ml_testing_function(body: UserLabelBody, db: Session = Depends(get_session)):
    f = MLTestingFunction(user_label=body.userLabel)
    db.add(f)
    db.commit()
    return _testing_function_view_for(db)(f)


@router.get("/ml-testing-functions")
def list_ml_testing_functions(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    return _page(db, select(MLTestingFunction), limit, offset, _testing_function_view_for(db))


@router.get("/ml-testing-functions/{function_id}")
def get_ml_testing_function(function_id: uuid.UUID, db: Session = Depends(get_session)):
    return _testing_function_view_for(db)(_get(db, MLTestingFunction, function_id, "MLTestingFunction"))


@router.delete("/ml-testing-functions/{function_id}", status_code=204)
def delete_ml_testing_function(function_id: uuid.UUID, db: Session = Depends(get_session)):
    f = db.get(MLTestingFunction, function_id)
    if f is not None:
        db.delete(f)
        db.commit()


# ================================================================ MLTestingRequest (= ValidationJob)

class MLTestingRequestBody(_Body):
    mLModelRef: uuid.UUID | None = None
    mLModelCoordinationGroupRef: uuid.UUID | None = None
    mLTestingFunctionRef: uuid.UUID | None = None
    # Not spec attributes — this build's own, same as on POST /validation-jobs.
    requestSource: str = "nrm"
    trainingJobId: uuid.UUID | None = None
    validationCriteria: dict = {}
    notificationUri: str | None = None


def _testing_request_view(j: ValidationJob) -> dict:
    return {"id": str(j.validation_job_id), "attributes": {
        "requestStatus": _VALIDATION_REQUEST_STATUS.get(j.status, j.status),
        "cancelRequest": j.cancel_request, "suspendRequest": j.suspend_request,
        "mLModelRef": _s(j.model_id), "mLModelCoordinationGroupRef": _s(j.model_coordination_group_id),
        "mLTestingFunctionRef": _s(j.ml_testing_function_id)}}


@router.post("/ml-testing-requests", status_code=201)
def create_ml_testing_request(body: MLTestingRequestBody, db: Session = Depends(get_session)):
    if body.mLTestingFunctionRef is not None:
        _get(db, MLTestingFunction, body.mLTestingFunctionRef, "MLTestingFunction")
    job = _start_validation(db, model_id=body.mLModelRef, group_id=body.mLModelCoordinationGroupRef,
                            producer_id=body.requestSource, training_job_id=body.trainingJobId,
                            validation_criteria=body.validationCriteria, notification_uri=body.notificationUri,
                            ml_testing_function_id=body.mLTestingFunctionRef)
    db.commit()
    return _testing_request_view(job)


@router.get("/ml-testing-requests")
def list_ml_testing_requests(ml_testing_function_id: uuid.UUID | None = None, limit: int = PageLimit,
                             offset: int = PageOffset, db: Session = Depends(get_session)):
    stmt = select(ValidationJob)
    if ml_testing_function_id:
        stmt = stmt.where(ValidationJob.ml_testing_function_id == ml_testing_function_id)
    return _page(db, stmt, limit, offset, _testing_request_view)


@router.get("/ml-testing-requests/{request_id}")
def get_ml_testing_request(request_id: uuid.UUID, db: Session = Depends(get_session)):
    return _testing_request_view(_get(db, ValidationJob, request_id, "MLTestingRequest"))


@router.patch("/ml-testing-requests/{request_id}")
def modify_ml_testing_request(request_id: uuid.UUID, body: RequestFlagsBody, db: Session = Depends(get_session)):
    """cancelRequest tears the run down and, for a model-targeted run,
    fails the model's VALIDATING state (VALIDATION_FAILED — FAILED is the
    lifecycle's own retry point) rather than leaving it stuck VALIDATING.
    suspendRequest pauses/resumes a RUNNING job."""
    job = _get(db, ValidationJob, request_id, "MLTestingRequest")
    if body.cancelRequest:
        if job.status not in ("RUNNING", "SUSPENDED"):
            raise framework_error(FrameworkError.TRAINING_JOB_ILLEGAL_TRANSITION,
                                   detail=f"cannot cancel a testing request in status {job.status}")
        job.status, job.cancel_request = "CANCELLED", True
        _nfo_terminate_execution(job.nf_deployment_id)
        job.nf_deployment_id = None
        if job.model_id is not None:
            _fire_model_event(db, job.model_id, ModelLifecycleEvent.VALIDATION_FAILED)
    elif body.suspendRequest is True:
        if job.status != "RUNNING":
            raise framework_error(FrameworkError.TRAINING_JOB_ILLEGAL_TRANSITION,
                                   detail=f"cannot suspend a testing request in status {job.status}")
        job.status, job.suspend_request = "SUSPENDED", True
    elif body.suspendRequest is False and job.status == "SUSPENDED":
        job.status, job.suspend_request = "RUNNING", False
    db.commit()
    return _testing_request_view(job)


# ================================================================ MLTestingReport

def _testing_report_view(r: MLTestingReport) -> dict:
    return {"id": str(r.ml_testing_report_id), "attributes": {
        "modelPerformanceTesting": r.model_performance_testing or [], "mLTestingResult": r.ml_testing_result,
        "testingRequestRef": str(r.validation_job_id), "mLTestingFunctionRef": _s(r.ml_testing_function_id),
        "createdAt": r.created_at.isoformat()}}


@router.get("/ml-testing-reports")
def list_ml_testing_reports(testing_request_id: uuid.UUID | None = None, limit: int = PageLimit,
                            offset: int = PageOffset, db: Session = Depends(get_session)):
    stmt = select(MLTestingReport)
    if testing_request_id:
        stmt = stmt.where(MLTestingReport.validation_job_id == testing_request_id)
    return _page(db, stmt.order_by(MLTestingReport.created_at.desc()), limit, offset, _testing_report_view)


@router.get("/ml-testing-reports/{report_id}")
def get_ml_testing_report(report_id: uuid.UUID, db: Session = Depends(get_session)):
    return _testing_report_view(_get(db, MLTestingReport, report_id, "MLTestingReport"))


# ================================================================ AIMLInferenceFunction

class AIMLInferenceFunctionBody(_Body):
    userLabel: str | None = None
    aIMLInferenceName: str | None = None
    activationStatus: ts28105.ActivationStatus = "DEACTIVATED"
    managedActivationScope: ts28105.ManagedActivationScope | None = None


class AIMLInferenceFunctionPatch(_Body):
    activationStatus: ts28105.ActivationStatus | None = None
    managedActivationScope: ts28105.ManagedActivationScope | None = None
    userLabel: str | None = None


def _inference_function_view_for(db: Session):
    def view(f: AIMLInferenceFunction) -> dict:
        consumers = db.scalars(select(InferenceJob.consumer_ref).where(
            InferenceJob.aiml_inference_function_id == f.aiml_inference_function_id,
            InferenceJob.consumer_ref.is_not(None))).all()
        return {"id": str(f.aiml_inference_function_id), "attributes": {
            "userLabel": f.user_label, "aIMLInferenceName": f.aiml_inference_name,
            "activationStatus": f.activation_status, "managedActivationScope": f.managed_activation_scope,
            "usedByFunctionRefList": sorted(set(consumers)), "mLModelRefList": list(f.ml_model_refs or [])}}
    return view


@router.post("/aiml-inference-functions", status_code=201)
def create_aiml_inference_function(body: AIMLInferenceFunctionBody, db: Session = Depends(get_session)):
    f = AIMLInferenceFunction(user_label=body.userLabel, aiml_inference_name=body.aIMLInferenceName,
                              activation_status=body.activationStatus,
                              managed_activation_scope=ts28105.dump(body.managedActivationScope), ml_model_refs=[])
    db.add(f)
    db.commit()
    return _inference_function_view_for(db)(f)


@router.get("/aiml-inference-functions")
def list_aiml_inference_functions(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    return _page(db, select(AIMLInferenceFunction), limit, offset, _inference_function_view_for(db))


@router.get("/aiml-inference-functions/{function_id}")
def get_aiml_inference_function(function_id: uuid.UUID, db: Session = Depends(get_session)):
    return _inference_function_view_for(db)(_get(db, AIMLInferenceFunction, function_id, "AIMLInferenceFunction"))


@router.patch("/aiml-inference-functions/{function_id}")
def modify_aiml_inference_function(function_id: uuid.UUID, body: AIMLInferenceFunctionPatch, db: Session = Depends(get_session)):
    """activationStatus gates inference: a DEACTIVATED function refuses
    `POST /models/{id}/inference-jobs?aiml_inference_function_id=...`."""
    f = _get(db, AIMLInferenceFunction, function_id, "AIMLInferenceFunction")
    if body.activationStatus is not None:
        f.activation_status = body.activationStatus
    if body.managedActivationScope is not None:
        f.managed_activation_scope = ts28105.dump(body.managedActivationScope)
    if body.userLabel is not None:
        f.user_label = body.userLabel
    db.commit()
    return _inference_function_view_for(db)(f)


@router.delete("/aiml-inference-functions/{function_id}", status_code=204)
def delete_aiml_inference_function(function_id: uuid.UUID, db: Session = Depends(get_session)):
    f = db.get(AIMLInferenceFunction, function_id)
    if f is not None:
        db.delete(f)
        db.commit()


# ================================================================ AIMLInferenceEmulationFunction

def _emulation_function_view(f: AIMLInferenceEmulationFunction) -> dict:
    return {"id": str(f.aiml_inference_emulation_function_id), "attributes": {"userLabel": f.user_label}}


@router.post("/aiml-inference-emulation-functions", status_code=201)
def create_aiml_inference_emulation_function(body: UserLabelBody, db: Session = Depends(get_session)):
    f = AIMLInferenceEmulationFunction(user_label=body.userLabel)
    db.add(f)
    db.commit()
    return _emulation_function_view(f)


@router.get("/aiml-inference-emulation-functions")
def list_aiml_inference_emulation_functions(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    return _page(db, select(AIMLInferenceEmulationFunction), limit, offset, _emulation_function_view)


@router.get("/aiml-inference-emulation-functions/{function_id}")
def get_aiml_inference_emulation_function(function_id: uuid.UUID, db: Session = Depends(get_session)):
    return _emulation_function_view(_get(db, AIMLInferenceEmulationFunction, function_id, "AIMLInferenceEmulationFunction"))


@router.delete("/aiml-inference-emulation-functions/{function_id}", status_code=204)
def delete_aiml_inference_emulation_function(function_id: uuid.UUID, db: Session = Depends(get_session)):
    f = db.get(AIMLInferenceEmulationFunction, function_id)
    if f is not None:
        db.delete(f)
        db.commit()


# ================================================================ AIMLInferenceReport

class AIMLInferenceReportBody(_Body):
    """Posted by an inference/emulation runtime that reports outside the
    resolve/complete calls (e.g. periodic reporting)."""
    aIMLInferenceFunctionRef: uuid.UUID | None = None
    aIMLInferenceEmulationFunctionRef: uuid.UUID | None = None
    inferenceOutputs: list[ts28105.InferenceOutput] = []
    potentialImpactInfo: ts28105.PotentialImpactInfo | None = None
    mLModelRefList: list[uuid.UUID]


def _inference_report_view(r: AIMLInferenceReport) -> dict:
    return {"id": str(r.aiml_inference_report_id), "attributes": {
        "inferenceOutputs": r.inference_outputs or [], "potentialImpactInfo": r.potential_impact_info,
        "mLModelRefList": list(r.ml_model_refs or []),
        "aIMLInferenceFunctionRef": _s(r.aiml_inference_function_id),
        "aIMLInferenceEmulationFunctionRef": _s(r.aiml_inference_emulation_function_id),
        "inferenceJobRef": _s(r.inference_job_id), "emulationJobRef": _s(r.emulation_job_id),
        "createdAt": r.created_at.isoformat()}}


@router.post("/aiml-inference-reports", status_code=201)
def create_aiml_inference_report(body: AIMLInferenceReportBody, db: Session = Depends(get_session)):
    if (body.aIMLInferenceFunctionRef is None) == (body.aIMLInferenceEmulationFunctionRef is None):
        raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED,
                               detail="exactly one of aIMLInferenceFunctionRef/aIMLInferenceEmulationFunctionRef")
    if body.aIMLInferenceFunctionRef is not None:
        _get(db, AIMLInferenceFunction, body.aIMLInferenceFunctionRef, "AIMLInferenceFunction")
    else:
        _get(db, AIMLInferenceEmulationFunction, body.aIMLInferenceEmulationFunctionRef, "AIMLInferenceEmulationFunction")
    r = AIMLInferenceReport(aiml_inference_function_id=body.aIMLInferenceFunctionRef,
                            aiml_inference_emulation_function_id=body.aIMLInferenceEmulationFunctionRef,
                            inference_outputs=ts28105.dump(body.inferenceOutputs) or [],
                            potential_impact_info=ts28105.dump(body.potentialImpactInfo),
                            ml_model_refs=[str(m) for m in body.mLModelRefList])
    db.add(r)
    db.commit()
    return _inference_report_view(r)


@router.get("/aiml-inference-reports")
def list_aiml_inference_reports(aiml_inference_function_id: uuid.UUID | None = None,
                                aiml_inference_emulation_function_id: uuid.UUID | None = None,
                                model_id: uuid.UUID | None = None, limit: int = PageLimit, offset: int = PageOffset,
                                db: Session = Depends(get_session)):
    stmt = select(AIMLInferenceReport)
    if aiml_inference_function_id:
        stmt = stmt.where(AIMLInferenceReport.aiml_inference_function_id == aiml_inference_function_id)
    if aiml_inference_emulation_function_id:
        stmt = stmt.where(AIMLInferenceReport.aiml_inference_emulation_function_id == aiml_inference_emulation_function_id)
    rows = db.scalars(stmt.order_by(AIMLInferenceReport.created_at.desc())).all()
    if model_id:
        rows = [r for r in rows if str(model_id) in (r.ml_model_refs or [])]
    items = rows[offset:offset + limit]
    return {"items": [_inference_report_view(r) for r in items], "total": len(rows), "limit": limit, "offset": offset}


@router.get("/aiml-inference-reports/{report_id}")
def get_aiml_inference_report(report_id: uuid.UUID, db: Session = Depends(get_session)):
    return _inference_report_view(_get(db, AIMLInferenceReport, report_id, "AIMLInferenceReport"))


# ================================================================ MLModelLoadingPolicy / Request / Process

class MLModelLoadingPolicyBody(_Body):
    aIMLInferenceFunctionRef: uuid.UUID
    aIMLInferenceName: str | None = None
    policyForLoading: ts28105.AIMLManagementPolicy | None = None
    mLModelRef: list[uuid.UUID]


class MLModelLoadingRequestBody(_Body):
    aIMLInferenceFunctionRef: uuid.UUID
    mLModelToLoadRef: list[uuid.UUID]
    cancelRequest: bool = False
    suspendRequest: bool = False


def _loading_policy_view(p: MLModelLoadingPolicy) -> dict:
    return {"id": str(p.ml_model_loading_policy_id), "attributes": {
        "aIMLInferenceFunctionRef": str(p.aiml_inference_function_id), "aIMLInferenceName": p.aiml_inference_name,
        "policyForLoading": p.policy_for_loading, "mLModelRef": list(p.ml_model_refs or [])}}


def _loading_request_view(r: MLModelLoadingRequest) -> dict:
    return {"id": str(r.ml_model_loading_request_id), "attributes": {
        "aIMLInferenceFunctionRef": str(r.aiml_inference_function_id), "requestStatus": r.request_status,
        "cancelRequest": r.cancel_request, "suspendRequest": r.suspend_request,
        "mLModelToLoadRef": list(r.ml_model_to_load_refs or [])}}


def _loading_process_view(p: MLModelLoadingProcess) -> dict:
    return {"id": str(p.ml_model_loading_process_id), "attributes": {
        "aIMLInferenceFunctionRef": str(p.aiml_inference_function_id),
        "progressStatus": _monitor(p.status, p.progress_percentage, p.progress_state_info, p.result_state_info),
        "cancelProcess": p.cancel_process, "suspendProcess": p.suspend_process,
        "mLModelLoadingRequestRef": list(p.loading_request_refs or []),
        "mLModelLoadingPolicyRef": list(p.loading_policy_refs or []),
        "loadedMLModelRef": list(p.loaded_ml_model_refs or [])}}


def _check_loadable(db: Session, model_ids: list[uuid.UUID]) -> None:
    """Validated up front, before anything is deployed: a request either
    loads every named model or none."""
    for model_id in model_ids:
        _get_model(model_id)
        lifecycle = _get_or_create_lifecycle(db, model_id)
        if lifecycle.model_lifecycle_state not in (ModelLifecycleState.CERTIFIED, ModelLifecycleState.PROMOTED):
            raise framework_error(FrameworkError.MODEL_NOT_CERTIFIED,
                                   detail=f"cannot load model {model_id} in state {lifecycle.model_lifecycle_state}")
        if lifecycle.runtime_lifecycle_state in (RuntimeLifecycleState.TERMINATING, RuntimeLifecycleState.TERMINATED):
            raise framework_error(FrameworkError.LIFECYCLE_ILLEGAL_TRANSITION,
                                   detail=f"model {model_id}'s runtime is {lifecycle.runtime_lifecycle_state}")


def _run_loading(db: Session, function: AIMLInferenceFunction, model_ids: list[uuid.UUID], *,
                 request: MLModelLoadingRequest | None = None, policy: MLModelLoadingPolicy | None = None) -> MLModelLoadingProcess:
    """Loads each model onto the function: brings its serving runtime up
    through the real RuntimeLifecycle (deploy via NFO, then activate —
    whichever steps it still needs) and adds it to the function's
    mLModelRefList. Synchronous, like the rest of this build's NFO calls.
    """
    process = MLModelLoadingProcess(aiml_inference_function_id=function.aiml_inference_function_id, status="RUNNING",
                                    loading_request_refs=[str(request.ml_model_loading_request_id)] if request else [],
                                    loading_policy_refs=[str(policy.ml_model_loading_policy_id)] if policy else [],
                                    loaded_ml_model_refs=[])
    db.add(process)
    if request is not None:
        request.request_status = "IN_PROGRESS"
    db.flush()
    loaded = list(function.ml_model_refs or [])
    for i, model_id in enumerate(model_ids, start=1):
        lifecycle = _get_or_create_lifecycle(db, model_id)
        if lifecycle.runtime_lifecycle_state == RuntimeLifecycleState.NOT_DEPLOYED:
            _deploy_runtime(db, model_id)
        if lifecycle.runtime_lifecycle_state == RuntimeLifecycleState.DEPLOYED:
            _activate_runtime(db, model_id)
        if str(model_id) not in loaded:
            loaded.append(str(model_id))
        process.loaded_ml_model_refs = [*process.loaded_ml_model_refs, str(model_id)]
        process.progress_percentage = int(100 * i / len(model_ids))
    function.ml_model_refs = loaded
    process.status, process.progress_percentage, process.result_state_info = "FINISHED", 100, "SUCCEEDED"
    if request is not None:
        request.request_status = "FINISHED"
    return process


@router.post("/ml-model-loading-policies", status_code=201)
def create_ml_model_loading_policy(body: MLModelLoadingPolicyBody, db: Session = Depends(get_session)):
    _get(db, AIMLInferenceFunction, body.aIMLInferenceFunctionRef, "AIMLInferenceFunction")
    p = MLModelLoadingPolicy(aiml_inference_function_id=body.aIMLInferenceFunctionRef,
                             aiml_inference_name=body.aIMLInferenceName,
                             policy_for_loading=ts28105.dump(body.policyForLoading),
                             ml_model_refs=[str(m) for m in body.mLModelRef])
    db.add(p)
    db.commit()
    return _loading_policy_view(p)


@router.get("/ml-model-loading-policies")
def list_ml_model_loading_policies(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    return _page(db, select(MLModelLoadingPolicy), limit, offset, _loading_policy_view)


@router.get("/ml-model-loading-policies/{policy_id}")
def get_ml_model_loading_policy(policy_id: uuid.UUID, db: Session = Depends(get_session)):
    return _loading_policy_view(_get(db, MLModelLoadingPolicy, policy_id, "MLModelLoadingPolicy"))


@router.delete("/ml-model-loading-policies/{policy_id}", status_code=204)
def delete_ml_model_loading_policy(policy_id: uuid.UUID, db: Session = Depends(get_session)):
    p = db.get(MLModelLoadingPolicy, policy_id)
    if p is not None:
        db.delete(p)
        db.commit()


@router.post("/ml-model-loading-policies/{policy_id}/trigger", status_code=201)
def trigger_ml_model_loading_policy(policy_id: uuid.UUID, db: Session = Depends(get_session)):
    """Policy-driven loading: called when the policy's own condition
    (its thresholdList, evaluated by whoever monitors it — e.g. an MLMF
    subscription's breach) is met. Starts an MLModelLoadingProcess with no
    MLModelLoadingRequest behind it, the spec's policy-triggered path."""
    p = _get(db, MLModelLoadingPolicy, policy_id, "MLModelLoadingPolicy")
    function = _get(db, AIMLInferenceFunction, p.aiml_inference_function_id, "AIMLInferenceFunction")
    model_ids = [uuid.UUID(m) for m in p.ml_model_refs]
    _check_loadable(db, model_ids)
    process = _run_loading(db, function, model_ids, policy=p)
    db.commit()
    return _loading_process_view(process)


@router.post("/ml-model-loading-requests", status_code=201)
def create_ml_model_loading_request(body: MLModelLoadingRequestBody, db: Session = Depends(get_session)):
    """A request created with suspendRequest=true waits (SUSPENDED) until
    PATCHed back; cancelRequest=true records it CANCELLED and loads nothing."""
    function = _get(db, AIMLInferenceFunction, body.aIMLInferenceFunctionRef, "AIMLInferenceFunction")
    _check_loadable(db, body.mLModelToLoadRef)
    r = MLModelLoadingRequest(aiml_inference_function_id=function.aiml_inference_function_id,
                              ml_model_to_load_refs=[str(m) for m in body.mLModelToLoadRef],
                              cancel_request=body.cancelRequest, suspend_request=body.suspendRequest)
    db.add(r)
    db.flush()
    if body.cancelRequest:
        r.request_status = "CANCELLED"
    elif body.suspendRequest:
        r.request_status = "SUSPENDED"
    else:
        _run_loading(db, function, body.mLModelToLoadRef, request=r)
    db.commit()
    return _loading_request_view(r)


@router.get("/ml-model-loading-requests")
def list_ml_model_loading_requests(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    return _page(db, select(MLModelLoadingRequest), limit, offset, _loading_request_view)


@router.get("/ml-model-loading-requests/{request_id}")
def get_ml_model_loading_request(request_id: uuid.UUID, db: Session = Depends(get_session)):
    return _loading_request_view(_get(db, MLModelLoadingRequest, request_id, "MLModelLoadingRequest"))


@router.patch("/ml-model-loading-requests/{request_id}")
def modify_ml_model_loading_request(request_id: uuid.UUID, body: RequestFlagsBody, db: Session = Depends(get_session)):
    r = _get(db, MLModelLoadingRequest, request_id, "MLModelLoadingRequest")
    if r.request_status in ("FINISHED", "CANCELLED"):
        raise framework_error(FrameworkError.TRAINING_JOB_ILLEGAL_TRANSITION,
                               detail=f"loading request already {r.request_status}")
    if body.cancelRequest:
        r.cancel_request, r.request_status = True, "CANCELLED"
    elif body.suspendRequest is False and r.request_status == "SUSPENDED":
        r.suspend_request = False
        function = _get(db, AIMLInferenceFunction, r.aiml_inference_function_id, "AIMLInferenceFunction")
        model_ids = [uuid.UUID(m) for m in r.ml_model_to_load_refs]
        _check_loadable(db, model_ids)
        _run_loading(db, function, model_ids, request=r)
    elif body.suspendRequest is True:
        r.suspend_request, r.request_status = True, "SUSPENDED"
    db.commit()
    return _loading_request_view(r)


@router.get("/ml-model-loading-processes")
def list_ml_model_loading_processes(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    return _page(db, select(MLModelLoadingProcess), limit, offset, _loading_process_view)


@router.get("/ml-model-loading-processes/{process_id}")
def get_ml_model_loading_process(process_id: uuid.UUID, db: Session = Depends(get_session)):
    return _loading_process_view(_get(db, MLModelLoadingProcess, process_id, "MLModelLoadingProcess"))


# ================================================================ MLUpdateFunction / Request / Process / Report

class MLUpdateFunctionBody(_Body):
    userLabel: str | None = None
    availMLCapabilityReport: ts28105.AvailMLCapabilityReport | None = None
    mLModelRef: list[uuid.UUID] = []


class MLUpdateRequestBody(_Body):
    mLUpdateFunctionRef: uuid.UUID | None = None
    mLModelRefList: list[uuid.UUID]
    performanceGainThreshold: list[ts28105.ModelPerformance] | None = None
    newCapabilityVersionId: list[str] | None = None
    updateTimeDeadline: dict | None = None
    mLUpdateReportingPeriod: dict | None = None
    requestSource: str = "nrm"


def _update_function_view(f: MLUpdateFunction) -> dict:
    return {"id": str(f.ml_update_function_id), "attributes": {
        "userLabel": f.user_label, "availMLCapabilityReport": f.avail_ml_capability_report,
        "mLModelRef": list(f.ml_model_refs or [])}}


def _update_request_view_for(db: Session):
    def view(r: MLUpdateRequest) -> dict:
        process = db.scalar(select(MLUpdateProcess).where(MLUpdateProcess.ml_update_request_id == r.ml_update_request_id))
        return {"id": str(r.ml_update_request_id), "attributes": {
            "mLUpdateFunctionRef": _s(r.ml_update_function_id),
            "performanceGainThreshold": r.performance_gain_threshold, "newCapabilityVersionId": r.new_capability_version_ids,
            "updateTimeDeadline": r.update_time_deadline, "requestStatus": r.request_status,
            "mLUpdateReportingPeriod": r.ml_update_reporting_period,
            "cancelRequest": r.cancel_request, "suspendRequest": r.suspend_request,
            "mLUpdateProcessRef": _s(process.ml_update_process_id) if process else None,
            "mLModelRefList": list(r.ml_model_refs or [])}}
    return view


def _update_process_view_for(db: Session):
    def view(p: MLUpdateProcess) -> dict:
        report = db.scalar(select(MLUpdateReport).where(MLUpdateReport.ml_update_process_id == p.ml_update_process_id))
        jobs = db.scalars(select(TrainingJob.training_job_id).where(TrainingJob.ml_update_process_id == p.ml_update_process_id)).all()
        return {"id": str(p.ml_update_process_id), "attributes": {
            "progressStatus": _monitor(p.status, p.progress_percentage, p.progress_state_info, p.result_state_info),
            "cancelProcess": p.cancel_process, "suspendProcess": p.suspend_process,
            "mLModelRefList": list(p.ml_model_refs or []), "mLUpdateRequestRefList": [str(p.ml_update_request_id)],
            "mLUpdateReportRef": _s(report.ml_update_report_id) if report else None,
            "trainingRequestRefList": [str(j) for j in jobs]}}
    return view


def _update_report_view(r: MLUpdateReport) -> dict:
    return {"id": str(r.ml_update_report_id), "attributes": {
        "updatedMLCapability": r.updated_ml_capability, "mLModelRefList": list(r.ml_model_refs or []),
        "mLUpdateProcessRef": str(r.ml_update_process_id), "createdAt": r.created_at.isoformat()}}


@router.post("/ml-update-functions", status_code=201)
def create_ml_update_function(body: MLUpdateFunctionBody, db: Session = Depends(get_session)):
    f = MLUpdateFunction(user_label=body.userLabel, avail_ml_capability_report=ts28105.dump(body.availMLCapabilityReport),
                         ml_model_refs=[str(m) for m in body.mLModelRef])
    db.add(f)
    db.commit()
    return _update_function_view(f)


@router.get("/ml-update-functions")
def list_ml_update_functions(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    return _page(db, select(MLUpdateFunction), limit, offset, _update_function_view)


@router.get("/ml-update-functions/{function_id}")
def get_ml_update_function(function_id: uuid.UUID, db: Session = Depends(get_session)):
    return _update_function_view(_get(db, MLUpdateFunction, function_id, "MLUpdateFunction"))


@router.delete("/ml-update-functions/{function_id}", status_code=204)
def delete_ml_update_function(function_id: uuid.UUID, db: Session = Depends(get_session)):
    f = db.get(MLUpdateFunction, function_id)
    if f is not None:
        db.delete(f)
        db.commit()


@router.post("/ml-update-requests", status_code=201)
def create_ml_update_request(body: MLUpdateRequestBody, db: Session = Depends(get_session)):
    """An ML update is realised as FINE_TUNING training of every named
    model (one MLTrainingRequest each, through the normal training start
    path and its lifecycle gate), tracked by one MLUpdateProcess; when all
    of them have finished, the MLUpdateReport is written. Every model must
    be trainable (REGISTERED/PROMOTED/FAILED) — checked before any run
    starts, so the update is all-or-nothing at start."""
    if not body.mLModelRefList:
        raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED, detail="mLModelRefList must not be empty")
    if body.mLUpdateFunctionRef is not None:
        _get(db, MLUpdateFunction, body.mLUpdateFunctionRef, "MLUpdateFunction")
    for model_id in body.mLModelRefList:
        _get_model(model_id)
        state = _get_or_create_lifecycle(db, model_id).model_lifecycle_state
        if state not in (ModelLifecycleState.REGISTERED, ModelLifecycleState.PROMOTED, ModelLifecycleState.FAILED):
            raise framework_error(FrameworkError.MODEL_NOT_CERTIFIED, detail=f"cannot update model {model_id} in state {state}")
    r = MLUpdateRequest(ml_update_function_id=body.mLUpdateFunctionRef,
                        performance_gain_threshold=ts28105.dump(body.performanceGainThreshold),
                        new_capability_version_ids=body.newCapabilityVersionId, update_time_deadline=body.updateTimeDeadline,
                        ml_update_reporting_period=body.mLUpdateReportingPeriod, request_status="IN_PROGRESS",
                        ml_model_refs=[str(m) for m in body.mLModelRefList])
    db.add(r)
    db.flush()
    p = MLUpdateProcess(ml_update_request_id=r.ml_update_request_id, status="RUNNING",
                        ml_model_refs=[str(m) for m in body.mLModelRefList])
    db.add(p)
    db.flush()
    for model_id in body.mLModelRefList:
        _start_training(db, model_id=model_id, group_id=None, producer_id=body.requestSource,
                        ml_training_type="FINE_TUNING", training_request_source=body.requestSource,
                        ml_update_process_id=p.ml_update_process_id)
    db.commit()
    return _update_request_view_for(db)(r)


@router.get("/ml-update-requests")
def list_ml_update_requests(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    return _page(db, select(MLUpdateRequest), limit, offset, _update_request_view_for(db))


@router.get("/ml-update-requests/{request_id}")
def get_ml_update_request(request_id: uuid.UUID, db: Session = Depends(get_session)):
    return _update_request_view_for(db)(_get(db, MLUpdateRequest, request_id, "MLUpdateRequest"))


@router.patch("/ml-update-requests/{request_id}")
def modify_ml_update_request(request_id: uuid.UUID, body: RequestFlagsBody, db: Session = Depends(get_session)):
    """cancelRequest cancels every still-running training run of the
    update; suspendRequest suspends/resumes them together."""
    r = _get(db, MLUpdateRequest, request_id, "MLUpdateRequest")
    if r.request_status in ("FINISHED", "CANCELLED"):
        raise framework_error(FrameworkError.TRAINING_JOB_ILLEGAL_TRANSITION, detail=f"update request already {r.request_status}")
    p = db.scalar(select(MLUpdateProcess).where(MLUpdateProcess.ml_update_request_id == r.ml_update_request_id))
    jobs = db.scalars(select(TrainingJob).where(TrainingJob.ml_update_process_id == p.ml_update_process_id)).all()
    if body.cancelRequest:
        r.cancel_request, r.request_status = True, "CANCELLED"
        p.cancel_process, p.status = True, "CANCELLED"
        for job in jobs:
            if job.status in ("IN_PROGRESS", "SUSPENDED"):
                _cancel_training_job_quiet(db, job)
    elif body.suspendRequest is not None:
        for job in jobs:
            _apply_training_flags(db, job, None, body.suspendRequest)
        r.suspend_request = p.suspend_process = body.suspendRequest
        r.request_status = "SUSPENDED" if body.suspendRequest else "IN_PROGRESS"
        p.status = "SUSPENDED" if body.suspendRequest else "RUNNING"
    db.commit()
    return _update_request_view_for(db)(r)


def _cancel_training_job_quiet(db: Session, job: TrainingJob) -> None:
    """Cancel one of an update's runs without re-entering
    advance_ml_update_process (the caller already set the process's own
    terminal state)."""
    job.status, job.cancel_request = "CANCELLED", True
    _nfo_terminate_execution(job.nf_deployment_id)
    job.nf_deployment_id = None
    _sync_training_process(db, job)


def advance_ml_update_process(db: Session, process_id: uuid.UUID) -> None:
    """Called whenever one of an update's training runs finishes, fails or
    is cancelled: recomputes progress and, once every run is terminal,
    closes the process/request and writes the MLUpdateReport."""
    p = db.get(MLUpdateProcess, process_id)
    if p is None or p.status in ("FINISHED", "FAILED", "CANCELLED"):
        return
    jobs = db.scalars(select(TrainingJob).where(TrainingJob.ml_update_process_id == process_id)).all()
    done = [j for j in jobs if j.status in ("FINISHED", "FAILED", "CANCELLED")]
    p.progress_percentage = int(100 * len(done) / len(jobs)) if jobs else 100
    if len(done) < len(jobs):
        return
    r = db.get(MLUpdateRequest, p.ml_update_request_id)
    succeeded = [j for j in jobs if j.status == "FINISHED"]
    p.status = "FINISHED" if len(succeeded) == len(jobs) else "FAILED"
    p.result_state_info = f"{len(succeeded)}/{len(jobs)} models updated"
    r.request_status = "FINISHED"
    gains = []
    for j in succeeded:
        for name, score in (j.model_metrics or {}).items():
            if isinstance(score, (int, float)):
                gains.append({"performanceMetric": name, "performanceScore": float(score)})
    report = MLUpdateReport(ml_update_process_id=p.ml_update_process_id, ml_model_refs=[str(j.model_id) for j in succeeded],
                            updated_ml_capability=None)
    db.add(report)
    db.flush()
    capability = {"availMLCapabilityReportID": str(report.ml_update_report_id),
                  "mLCapabilityVersionId": (r.new_capability_version_ids or [None])[0],
                  "expectedPerformanceGains": gains}
    report.updated_ml_capability = {k: v for k, v in capability.items() if v is not None}
    if r.ml_update_function_id is not None:
        function = db.get(MLUpdateFunction, r.ml_update_function_id)
        if function is not None:
            function.avail_ml_capability_report = report.updated_ml_capability
            function.ml_model_refs = sorted(set(function.ml_model_refs or []) | {str(j.model_id) for j in succeeded})


@router.get("/ml-update-processes")
def list_ml_update_processes(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    return _page(db, select(MLUpdateProcess), limit, offset, _update_process_view_for(db))


@router.get("/ml-update-processes/{process_id}")
def get_ml_update_process(process_id: uuid.UUID, db: Session = Depends(get_session)):
    return _update_process_view_for(db)(_get(db, MLUpdateProcess, process_id, "MLUpdateProcess"))


@router.get("/ml-update-reports")
def list_ml_update_reports(limit: int = PageLimit, offset: int = PageOffset, db: Session = Depends(get_session)):
    return _page(db, select(MLUpdateReport).order_by(MLUpdateReport.created_at.desc()), limit, offset, _update_report_view)


@router.get("/ml-update-reports/{report_id}")
def get_ml_update_report(report_id: uuid.UUID, db: Session = Depends(get_session)):
    return _update_report_view(_get(db, MLUpdateReport, report_id, "MLUpdateReport"))


# ================================================================ MLModel read-only cross-references

@router.get("/ml-models/{model_id}/nrm-refs")
def get_ml_model_nrm_refs(model_id: uuid.UUID, db: Session = Depends(get_session)):
    """The read-only MLModel attributes whose truth is AIMgF's, for MLMR's
    own TS 28.105 MLModel view (`GET /mlmr/ml-models/{id}`): mLTrainingType
    (of the latest training), aIMLInferenceReportRefList and
    usedByFunctionRefList (the AIMLInferenceFunctions it is loaded on)."""
    latest_report = db.scalars(select(MLTrainingReport).where(MLTrainingReport.ml_model_generated_ref == model_id)
                               .order_by(MLTrainingReport.created_at.desc())).first()
    latest_job = db.get(TrainingJob, latest_report.training_job_id) if latest_report else None
    reports = [r for r in db.scalars(select(AIMLInferenceReport)).all() if str(model_id) in (r.ml_model_refs or [])]
    functions = [f for f in db.scalars(select(AIMLInferenceFunction)).all() if str(model_id) in (f.ml_model_refs or [])]
    return {"mLTrainingType": latest_job.ml_training_type if latest_job else None,
            "aIMLInferenceReportRefList": [str(r.aiml_inference_report_id) for r in reports],
            "usedByFunctionRefList": [str(f.aiml_inference_function_id) for f in functions]}
