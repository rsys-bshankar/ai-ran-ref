import datetime
import uuid

from sqlalchemy import ARRAY, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from smo_shared.db import Base


class ModelLifecycle(Base):
    """Wave 2's own lifecycle-state truth (docs/ARCHITECTURE.md (AIMgF),
    docs/ARCHITECTURE.md: "Lifecycle state: AIMgF
    ✅, MLMR ❌"). Replaces Wave 1's `PATCH /mlmr/models/{id}/lifecycle`
    (which left state/trainingJobId/clearedNodeGroups on MLMR's own row as
    a structural shortcut) — AIMgF now owns this row outright, one per
    model, created alongside every model's first TrainingJob.

    `nf_deployment_descriptor_id`/`nf_deployment_id` are AIMgF's own
    handles onto NFO's runtime — bare UUIDs, not ORM ForeignKeys: NFO
    runs in its own process, where this module's metadata never has
    `nf_deployment_descriptor`/`nf_deployment` declared (NoReferencedTableError
    on flush otherwise), the same cross-module-reference shape already used
    throughout this build (e.g. onboarding's own `nf_deployment_descriptor_id`).
    Referential integrity is enforced at the DB level instead
    (migrations/001_init.sql's own FK on the descriptor column).
    """
    __tablename__ = "model_lifecycle"

    model_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)  # -> aiml_model (MLMR)
    model_lifecycle_state: Mapped[str] = mapped_column(String, nullable=False, default="REGISTERED")
    runtime_lifecycle_state: Mapped[str] = mapped_column(String, nullable=False, default="NOT_DEPLOYED")
    training_job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    cleared_node_groups: Mapped[list[str] | None] = mapped_column(ARRAY(String).with_variant(JSON(none_as_null=True), "sqlite"))
    nf_deployment_descriptor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # -> nf_deployment_descriptor (NFO)
    nf_deployment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # -> nf_deployment (NFO)
    # HISTORY.md OI-6.1: operator gate on Training->Validation->
    # Emulation. Reset to False whenever CREATE_TRAINING fires (a fresh
    # training run re-requires approval) — a stale approval from a prior
    # cycle should never silently carry forward into a new one.
    training_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    validation_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Wave 7 (W7-03): the INFERENCE runtime profile the serving runtime was
    # deployed with.
    runtime_profile: Mapped[dict | None] = mapped_column(JSON)


class ValidationJob(Base):
    """New this wave — AIMgF's own "Create Validation" request/tracking
    aggregate (docs/ARCHITECTURE.md's AIMgF "Owns" list), split out
    from being folded silently into TrainingJob's own TRAINING_COMPLETE ->
    TESTED transition in Wave 1's flat FSM.
    """
    __tablename__ = "validation_job"

    __table_args__ = (
        CheckConstraint(
            "(model_id IS NOT NULL AND model_coordination_group_id IS NULL) "
            "OR (model_id IS NULL AND model_coordination_group_id IS NOT NULL)",
            name="validation_exactly_one_target",
        ),
    )

    validation_job_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Wave 4 (TS 28.105 MLTestingRequest.mLModelCoordinationGroupRef):
    # exactly one of model_id/model_coordination_group_id, the same
    # exactly_one_target shape TrainingJob already has — and the same
    # asymmetry: a group-targeted run drives no single model's lifecycle.
    model_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    model_coordination_group_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    training_job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("training_job.training_job_id"))
    producer_id: Mapped[str] = mapped_column(String, nullable=False)
    validation_criteria: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String, nullable=False, default="RUNNING")
    metrics: Mapped[dict | None] = mapped_column(JSON)
    # HISTORY.md OI-6.5: same additive pair TrainingJob gained —
    # who to best-effort notify on completion (set at request time, the
    # requester's own callback), and where the validated artifact lives
    # (a DME DmeTypeId, set on completion).
    notification_uri: Mapped[str | None] = mapped_column(String)
    outcome_artifact_dme_type_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    # Wave 4 — TS 28.105 MLTestingRequest: the containing MLTestingFunction
    # (optional) and the spec's own cancelRequest/suspendRequest flags.
    ml_testing_function_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("ml_testing_function.ml_testing_function_id", ondelete="SET NULL"))
    cancel_request: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    suspend_request: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Wave 7 (W7-03/W7-04): the compute the run was sized with (from the
    # rApp package's runtimeProfiles, or an explicit override), and its
    # execution timeout — `started_at` + `timeout_seconds` is the deadline
    # `_expire_overdue_jobs` enforces.
    runtime_profile: Mapped[dict | None] = mapped_column(JSON)
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                           default=lambda: datetime.datetime.now(datetime.UTC))
    timeout_seconds: Mapped[int | None] = mapped_column(Integer)
    # HISTORY.md OI-6.2: a real NFO-backed execution runtime for
    # this validation run — same bare-UUID cross-module-reference shape as
    # ModelLifecycle's own nf_deployment_descriptor_id/nf_deployment_id
    # (NFO runs in its own process; referential integrity enforced at the
    # DB level by migrations/001_init.sql's own FK on the descriptor
    # column). Set on request, cleared on completion once NFO tears the
    # transient deployment down.
    nf_deployment_descriptor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    nf_deployment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class EmulationJob(Base):
    """New this wave — AIMgF's own "Create Emulation" request/tracking
    aggregate, split out from Wave 1's flat VALIDATION_COMPLETE -> EMULATED
    transition the same way ValidationJob is.
    """
    __tablename__ = "emulation_job"

    emulation_job_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    model_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    producer_id: Mapped[str] = mapped_column(String, nullable=False)
    emulation_criteria: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String, nullable=False, default="RUNNING")
    metrics: Mapped[dict | None] = mapped_column(JSON)
    # HISTORY.md OI-6.5: same pair as ValidationJob's own.
    notification_uri: Mapped[str | None] = mapped_column(String)
    outcome_artifact_dme_type_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    # Wave 4 — TS 28.105 AIMLInferenceEmulationFunction that hosts this run
    # (optional); completion writes an AIMLInferenceReport under it.
    aiml_inference_emulation_function_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("aiml_inference_emulation_function.aiml_inference_emulation_function_id", ondelete="SET NULL"))
    # Wave 7 (W7-03/W7-04): the compute the run was sized with (from the
    # rApp package's runtimeProfiles, or an explicit override), and its
    # execution timeout — `started_at` + `timeout_seconds` is the deadline
    # `_expire_overdue_jobs` enforces.
    runtime_profile: Mapped[dict | None] = mapped_column(JSON)
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                           default=lambda: datetime.datetime.now(datetime.UTC))
    timeout_seconds: Mapped[int | None] = mapped_column(Integer)
    # HISTORY.md OI-6.2: same pair as ValidationJob's own.
    nf_deployment_descriptor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    nf_deployment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class CertificationRecord(Base):
    """New this wave — a real, queryable record for every governance
    decision (docs/ARCHITECTURE.md's AIMgF Governance list: Approval,
    Certification, Promotion, Rollback — plus the submit/reject pair
    framing approval), written by `advance_model_lifecycle` whenever the
    fired event is one of `statemachine.GOVERNANCE_EVENTS`.
    """
    __tablename__ = "certification_record"

    certification_record_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    model_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    decision: Mapped[str] = mapped_column(String, nullable=False)
    decided_by: Mapped[str] = mapped_column(String, nullable=False)
    rationale: Mapped[str | None] = mapped_column(String)
    decided_at: Mapped[datetime.datetime] = mapped_column(default=lambda: datetime.datetime.now(datetime.UTC))


class LifecycleTransition(Base):
    """New this wave — an audit trail of every ModelLifecycle/
    RuntimeLifecycle FSM transition, so "how did this model get here" is
    a real query rather than something only reconstructable from
    TrainingJob/ValidationJob/EmulationJob/CertificationRecord timestamps.
    """
    __tablename__ = "lifecycle_transition"

    lifecycle_transition_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    model_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    fsm: Mapped[str] = mapped_column(String, nullable=False)  # "MODEL" or "RUNTIME"
    from_state: Mapped[str] = mapped_column(String, nullable=False)
    to_state: Mapped[str] = mapped_column(String, nullable=False)
    event: Mapped[str] = mapped_column(String, nullable=False)
    occurred_at: Mapped[datetime.datetime] = mapped_column(default=lambda: datetime.datetime.now(datetime.UTC))


class TrainingJob(Base):
    """model_id/model_coordination_group_id are bare UUIDs, not
    ForeignKeys, since Wave 1's split moved MLModel/MLModelCoordinationGroup
    to MLMR's own process — AIMgF never imports MLMR's models, the same
    cross-module-reference shape already used elsewhere in this build
    (e.g. sa-smos's own `target_coordination_group_id`). Referential
    integrity across that boundary is still enforced at the database
    level: migrations/001_init.sql's own `ON DELETE CASCADE` on this
    column is unaffected by the code split, since every module shares one
    physical Postgres instance.
    """
    __tablename__ = "training_job"
    __table_args__ = (
        # Portable boolean form — "::int" cast syntax is Postgres-only and
        # fails on SQLite (caught by sa-smos/tests, which shares this same
        # exactly-one-of-two-targets shape).
        CheckConstraint(
            "(model_id IS NOT NULL AND model_coordination_group_id IS NULL) "
            "OR (model_id IS NULL AND model_coordination_group_id IS NOT NULL)",
            name="exactly_one_target",
        ),
    )

    training_job_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    model_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    model_coordination_group_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    producer_type: Mapped[str] = mapped_column(String, nullable=False, default="rApp")
    producer_id: Mapped[str] = mapped_column(String, nullable=False)
    required_data: Mapped[dict | None] = mapped_column(JSON)
    # HISTORY.md OI-6.4: `requiredData` itself stays the opaque
    # blob it always was — this is a separate, optional, explicitly-typed
    # reference to the real DME DataJob(s) training actually consumed,
    # the same "additive, not replacing the existing field" shape DME's
    # own sourceDomain/sourceContext already used for the analogous
    # producer-side declaration. Validated against DME on request (see
    # `_validate_dme_data_job_ids` in main.py); empty/omitted skips the
    # check, matching every other optional cross-reference in this build.
    dme_data_job_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid).with_variant(JSON(none_as_null=True), "sqlite"), nullable=False, default=list)
    validation_criteria: Mapped[dict | None] = mapped_column(JSON)
    # HISTORY.md §7's `requestStatus` vocabulary finding, closed: renamed
    # to TS28.105's own real 6-value enum (NOT_STARTED/IN_PROGRESS/
    # SUSPENDED/FINISHED/CANCELLED/CANCELLING) — SUSPENDED/CANCELLED
    # already matched (SUSPENDED adopted in an earlier pass). FAILED is
    # this build's own honest addition beyond the spec (a training job
    # that genuinely fails needs a distinct terminal state the spec
    # doesn't model); CANCELLING is never produced — this build has no
    # asynchronous in-flight-cancellation step, the same "spec value
    # this build's own design never reaches" honesty as
    # PRE_SPECIALISED_TRAINING/FINE_TUNING on `ml_training_type`.
    status: Mapped[str] = mapped_column(String, nullable=False, default="NOT_STARTED")
    notification_uri: Mapped[str | None] = mapped_column(String)
    # NEW section 5: the reference's own TrainingJob (trainingmgr/models/trainingjob.py)
    # carries these too — run_id, distinct training/validation dataset
    # references, separate consumer/producer rApp ids, and a
    # model_metrics writeback target. Its real two-axis (step x status)
    # tracking (steps_state/TrainingJobStatus) is deliberately NOT
    # adopted here — replacing this build's existing flat `status`
    # field with a step state machine is a bigger, riskier rework of
    # already-shipped behavior, not a purely additive field; left for a
    # future pass.
    run_id: Mapped[str | None] = mapped_column(String)
    training_dataset: Mapped[str | None] = mapped_column(String)
    validation_dataset: Mapped[str | None] = mapped_column(String)
    consumer_rapp_id: Mapped[str | None] = mapped_column(String)
    producer_rapp_id: Mapped[str | None] = mapped_column(String)
    model_metrics: Mapped[dict | None] = mapped_column(JSON)
    # HISTORY.md OI-6.5: where the training run's real output
    # artifact lives — a DME DmeTypeId reference, the same "route it
    # through DME" shape MLModel's own outputDataType already uses for an
    # inference result, rather than inventing a second data-plane path.
    # Set on completion (POST .../complete), not at request time — the
    # artifact doesn't exist until training actually produces one.
    outcome_artifact_dme_type_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    # HISTORY.md §7: TS28.105 AI/ML NRM's own real, closed 4-value
    # mLTrainingType enum on both MLModel and MLTrainingRequest —
    # request_training already computes this exact INITIAL_TRAINING-vs-
    # RE_TRAINING distinction internally (as a ModelEvent.TRAIN/RETRAIN
    # FSM choice) but never stored or returned it anywhere.
    # PRE_SPECIALISED_TRAINING/FINE_TUNING have no equivalent concept in
    # this build, so only two of the spec's four values are ever
    # produced here — an honest partial mapping, not a fabricated one.
    ml_training_type: Mapped[str | None] = mapped_column(String)
    # HISTORY.md OI-6.2: a real NFO-backed execution runtime for
    # this training run — same pair as ValidationJob/EmulationJob's own.
    nf_deployment_descriptor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    nf_deployment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    # Wave 4 — TS 28.105 MLTrainingRequest: a TrainingJob *is* the spec's
    # MLTrainingRequest (`GET /ml-training-requests/{id}` is this row), so
    # every remaining spec attribute lives here rather than on a parallel
    # table that would have to be kept in sync with it. Complex datatypes
    # (FLRequirement, RLRequirement, ModelPerformance[], ...) are stored as
    # their spec-shaped JSON, validated on the way in (app/ts28105.py).
    ml_training_function_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("ml_training_function.ml_training_function_id", ondelete="SET NULL"))
    aiml_inference_name: Mapped[str | None] = mapped_column(String)
    fl_requirement: Mapped[dict | None] = mapped_column(JSON)
    candidate_training_data_source: Mapped[list | None] = mapped_column(JSON)
    training_data_quality_score: Mapped[float | None] = mapped_column(Float)
    training_request_source: Mapped[str | None] = mapped_column(String)
    performance_requirements: Mapped[list | None] = mapped_column(JSON)
    rl_requirement: Mapped[dict | None] = mapped_column(JSON)
    cancel_request: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    suspend_request: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    training_data_statistical_properties: Mapped[dict | None] = mapped_column(JSON)
    distributed_training_expectation: Mapped[dict | None] = mapped_column(JSON)
    ml_knowledge_name: Mapped[str | None] = mapped_column(String)
    expected_inference_scope: Mapped[list | None] = mapped_column(JSON)
    clustering_info: Mapped[list | None] = mapped_column(JSON)
    # Set when an MLUpdateProcess started this run (FINE_TUNING) — its
    # completion advances that process.
    ml_update_process_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("ml_update_process.ml_update_process_id", ondelete="SET NULL"))
    # Wave 7 (W7-03/W7-04): the compute the run was sized with (from the
    # rApp package's runtimeProfiles, or an explicit override), and its
    # execution timeout — `started_at` + `timeout_seconds` is the deadline
    # `_expire_overdue_jobs` enforces.
    runtime_profile: Mapped[dict | None] = mapped_column(JSON)
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                           default=lambda: datetime.datetime.now(datetime.UTC))
    timeout_seconds: Mapped[int | None] = mapped_column(Integer)


class InferenceJob(Base):
    __tablename__ = "inference_job"

    inference_job_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    model_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)  # bare UUID — see TrainingJob's docstring
    status: Mapped[str] = mapped_column(String, nullable=False, default="RUNNING")
    # HISTORY.md OI-6.2: unlike TrainingJob/ValidationJob/
    # EmulationJob (each a transient batch run that gets its own fresh
    # NFO descriptor+deployment, torn down on completion), an
    # InferenceJob doesn't create a new NFO deployment of its own —
    # request_inference is already gated on RuntimeLifecycleState.ACTIVE,
    # which means ModelLifecycle.nf_deployment_id (deploy_model_runtime's
    # own real NFO call) is already a live serving deployment. Creating a
    # second, parallel one per inference call would duplicate that
    # runtime rather than use it. This is a read-only reference, stamped
    # from the lifecycle at request time — MLIF's own "which NFO
    # deployment actually served this inference" becomes a real,
    # queryable fact instead of unlinked.
    nf_deployment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    notification_destination: Mapped[str | None] = mapped_column(String)
    # Wave 4 — the TS 28.105 AIMLInferenceFunction this inference ran on
    # (optional), and who consumed it (feeds usedByFunctionRefList).
    aiml_inference_function_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("aiml_inference_function.aiml_inference_function_id", ondelete="SET NULL"))
    consumer_ref: Mapped[str | None] = mapped_column(String)
    # Wave 7 (W7-04): inference deadline (default 5 s).
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                           default=lambda: datetime.datetime.now(datetime.UTC))
    timeout_seconds: Mapped[int | None] = mapped_column(Integer)


class MLMFSubscription(Base):
    __tablename__ = "mlmf_subscription"

    subscription_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    model_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)  # bare UUID — see TrainingJob's docstring
    metric_types: Mapped[list[str]] = mapped_column(ARRAY(String).with_variant(JSON(none_as_null=True), "sqlite"), nullable=False)
    dme_type_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    guard_kpi_floor: Mapped[dict | None] = mapped_column(JSON)
    # HISTORY.md §7's `MLMFSubscription` finding, closed: every other
    # subscription-shaped resource in this build (DME/MDAF/A1-Related/
    # Intent Service) notifies a real notification_destination and can
    # be torn down with a real DELETE — this one could previously only
    # be created and read.
    notification_destination: Mapped[str | None] = mapped_column(String)


class PerformanceReport(Base):
    __tablename__ = "performance_report"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subscription_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("mlmf_subscription.subscription_id", ondelete="CASCADE"))
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False)
    breached_floor: Mapped[bool] = mapped_column(default=False)
    reported_at: Mapped[datetime.datetime] = mapped_column(default=lambda: datetime.datetime.now(datetime.UTC))


class FeatureGroup(Base):
    """Classified under AIMgF, not MLMR/MLLF: not mentioned by either
    service's own docs/ownership/*.md (a genuine Wave 0 gap — neither
    document anticipated it), and DME (which owns "datasets, feature
    sets" per docs/ARCHITECTURE.md) is
    explicitly frozen unchanged this wave, so moving it there is out of
    scope. The reference registers FeatureGroup through its own Training
    Manager sub-service, co-located with TrainingJob in the same real
    repo this build's training routes map to — the closest real-world
    precedent, and consistent with this module's own header docstring
    already listing MLMF (a monitoring/governance concern) alongside
    AIMgF rather than MLMR/MLLF. Flagged here for the record in case a
    later wave wants to revisit it.

    HISTORY.md §5: no feature-group/feature-store concept
    existed at all — the reference's own FeatureGroup
    (aiml-fw-awmf-tm's trainingmgr/models/featuregroup.py). Real
    Cassandra-backed feature storage (the ADOPT target,
    aiml-fw-athp-sdk-feature-store) and the reference's own
    enable_dme-triggered real DME PUT
    (data-consumer/v1/info-jobs/{featureGroupName},
    trainingmgr_operations.create_dme_filtered_data_job) are both
    deliberate elisions here, consistent with this build's
    no-real-southbound-compute design elsewhere — `enable_dme` is
    still stored and returned faithfully, just not acted on.
    """

    __tablename__ = "feature_group"

    feature_group_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    feature_group_name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    feature_list: Mapped[str] = mapped_column(String, nullable=False)
    datalake_source: Mapped[str] = mapped_column(String, nullable=False)
    host: Mapped[str] = mapped_column(String, nullable=False)
    port: Mapped[str] = mapped_column(String, nullable=False)
    bucket: Mapped[str] = mapped_column(String, nullable=False)
    token: Mapped[str] = mapped_column(String, nullable=False)
    db_org: Mapped[str] = mapped_column(String, nullable=False)
    measurement: Mapped[str] = mapped_column(String, nullable=False)
    enable_dme: Mapped[bool] = mapped_column(nullable=False, default=False)
    measured_obj_class: Mapped[str | None] = mapped_column(String)
    dme_port: Mapped[str | None] = mapped_column(String)
    source_name: Mapped[str | None] = mapped_column(String)


# ---------------------------------------------------------------- Wave 4: TS 28.105 AI/ML NRM IOCs
#
# Every IOC of TS28105_AiMlNrm.yaml at REST level (docs/ROADMAP.md
# decision D-9): flat REST resources with the spec's own attribute names and
# enums, no DN containment tree (the one recorded deviation — addressing).
# Requests are backed by the real job aggregates (MLTrainingRequest =
# TrainingJob, MLTestingRequest = ValidationJob); everything else that had
# no equivalent is a new table here. MLModel/MLModelRepository/
# MLModelCoordinationGroup are MLMR's (mlmr/app/models.py).

def _now():
    return datetime.datetime.now(datetime.UTC)


class MLTrainingFunction(Base):
    __tablename__ = "ml_training_function"

    ml_training_function_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_label: Mapped[str | None] = mapped_column(String)
    supported_learning_technology: Mapped[dict | None] = mapped_column(JSON)
    fl_participation_info: Mapped[dict | None] = mapped_column(JSON)
    ml_knowledge: Mapped[dict | None] = mapped_column(JSON)
    # readOnly in the spec: the mLTrainingType of the most recent training
    # this function ran, stamped by request_training.
    ml_training_type: Mapped[str | None] = mapped_column(String)
    ml_model_repository_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # -> ml_model_repository (MLMR)
    created_at: Mapped[datetime.datetime] = mapped_column(default=_now)


class MLTrainingProcess(Base):
    """One per TrainingJob (= MLTrainingRequest), created alongside it by
    every training entry point, so the spec's request/process split is
    real for every run, not only for runs started via the NRM routes.
    """
    __tablename__ = "ml_training_process"

    ml_training_process_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    training_job_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("training_job.training_job_id", ondelete="CASCADE"), nullable=False, unique=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    termination_conditions: Mapped[str | None] = mapped_column(String)
    # ProcessMonitor
    status: Mapped[str] = mapped_column(String, nullable=False, default="RUNNING")
    progress_percentage: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_state_info: Mapped[str | None] = mapped_column(String)
    result_state_info: Mapped[str | None] = mapped_column(String)
    cancel_process: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    suspend_process: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    participating_fl_client_refs: Mapped[list | None] = mapped_column(JSON)


class MLTrainingReport(Base):
    __tablename__ = "ml_training_report"

    ml_training_report_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    training_job_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("training_job.training_job_id", ondelete="CASCADE"), nullable=False)
    ml_training_function_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("ml_training_function.ml_training_function_id", ondelete="SET NULL"))
    used_consumer_training_data: Mapped[list | None] = mapped_column(JSON)
    model_confidence_indication: Mapped[int | None] = mapped_column(Integer)
    model_performance_training: Mapped[list | None] = mapped_column(JSON)
    model_performance_validation: Mapped[list | None] = mapped_column(JSON)
    data_ratio_training_and_validation: Mapped[int | None] = mapped_column(Integer)
    are_new_training_data_used: Mapped[bool | None] = mapped_column(Boolean)
    fl_report_per_client: Mapped[list | None] = mapped_column(JSON)
    last_training_report_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    ml_model_generated_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    ml_model_coordination_group_generated_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime.datetime] = mapped_column(default=_now)


class MLTestingFunction(Base):
    __tablename__ = "ml_testing_function"

    ml_testing_function_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_label: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime.datetime] = mapped_column(default=_now)


class MLTestingReport(Base):
    __tablename__ = "ml_testing_report"

    ml_testing_report_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    validation_job_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("validation_job.validation_job_id", ondelete="CASCADE"), nullable=False)
    ml_testing_function_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("ml_testing_function.ml_testing_function_id", ondelete="SET NULL"))
    model_performance_testing: Mapped[list | None] = mapped_column(JSON)
    ml_testing_result: Mapped[str] = mapped_column(String, nullable=False)  # PASSED | FAILED
    created_at: Mapped[datetime.datetime] = mapped_column(default=_now)


class AIMLInferenceFunction(Base):
    __tablename__ = "aiml_inference_function"

    aiml_inference_function_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_label: Mapped[str | None] = mapped_column(String)
    aiml_inference_name: Mapped[str | None] = mapped_column(String)
    activation_status: Mapped[str] = mapped_column(String, nullable=False, default="DEACTIVATED")  # ACTIVATED | DEACTIVATED
    managed_activation_scope: Mapped[dict | None] = mapped_column(JSON)
    # readOnly in the spec: models loaded onto this function by an
    # MLModelLoadingProcess.
    ml_model_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime.datetime] = mapped_column(default=_now)


class AIMLInferenceEmulationFunction(Base):
    __tablename__ = "aiml_inference_emulation_function"

    aiml_inference_emulation_function_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_label: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime.datetime] = mapped_column(default=_now)


class AIMLInferenceReport(Base):
    __tablename__ = "aiml_inference_report"

    aiml_inference_report_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    aiml_inference_function_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("aiml_inference_function.aiml_inference_function_id", ondelete="CASCADE"))
    aiml_inference_emulation_function_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("aiml_inference_emulation_function.aiml_inference_emulation_function_id", ondelete="CASCADE"))
    inference_job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("inference_job.inference_job_id", ondelete="SET NULL"))
    emulation_job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("emulation_job.emulation_job_id", ondelete="SET NULL"))
    inference_outputs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    potential_impact_info: Mapped[dict | None] = mapped_column(JSON)
    ml_model_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime.datetime] = mapped_column(default=_now)


class MLModelLoadingPolicy(Base):
    __tablename__ = "ml_model_loading_policy"

    ml_model_loading_policy_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    aiml_inference_function_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("aiml_inference_function.aiml_inference_function_id", ondelete="CASCADE"), nullable=False)
    aiml_inference_name: Mapped[str | None] = mapped_column(String)
    policy_for_loading: Mapped[dict | None] = mapped_column(JSON)  # AIMLManagementPolicy
    ml_model_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class MLModelLoadingRequest(Base):
    __tablename__ = "ml_model_loading_request"

    ml_model_loading_request_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    aiml_inference_function_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("aiml_inference_function.aiml_inference_function_id", ondelete="CASCADE"), nullable=False)
    request_status: Mapped[str] = mapped_column(String, nullable=False, default="NOT_STARTED")
    cancel_request: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    suspend_request: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ml_model_to_load_refs: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(default=_now)


class MLModelLoadingProcess(Base):
    __tablename__ = "ml_model_loading_process"

    ml_model_loading_process_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    aiml_inference_function_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("aiml_inference_function.aiml_inference_function_id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="RUNNING")
    progress_percentage: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_state_info: Mapped[str | None] = mapped_column(String)
    result_state_info: Mapped[str | None] = mapped_column(String)
    cancel_process: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    suspend_process: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    loading_request_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    loading_policy_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    loaded_ml_model_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class MLUpdateFunction(Base):
    __tablename__ = "ml_update_function"

    ml_update_function_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_label: Mapped[str | None] = mapped_column(String)
    avail_ml_capability_report: Mapped[dict | None] = mapped_column(JSON)
    ml_model_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime.datetime] = mapped_column(default=_now)


class MLUpdateRequest(Base):
    __tablename__ = "ml_update_request"

    ml_update_request_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ml_update_function_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("ml_update_function.ml_update_function_id", ondelete="SET NULL"))
    performance_gain_threshold: Mapped[list | None] = mapped_column(JSON)
    new_capability_version_ids: Mapped[list | None] = mapped_column(JSON)
    update_time_deadline: Mapped[dict | None] = mapped_column(JSON)
    request_status: Mapped[str] = mapped_column(String, nullable=False, default="NOT_STARTED")
    ml_update_reporting_period: Mapped[dict | None] = mapped_column(JSON)
    cancel_request: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    suspend_request: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ml_model_refs: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(default=_now)


class MLUpdateProcess(Base):
    __tablename__ = "ml_update_process"

    ml_update_process_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ml_update_request_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("ml_update_request.ml_update_request_id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="RUNNING")
    progress_percentage: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_state_info: Mapped[str | None] = mapped_column(String)
    result_state_info: Mapped[str | None] = mapped_column(String)
    cancel_process: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    suspend_process: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ml_model_refs: Mapped[list] = mapped_column(JSON, nullable=False)


class MLUpdateReport(Base):
    __tablename__ = "ml_update_report"

    ml_update_report_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ml_update_process_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("ml_update_process.ml_update_process_id", ondelete="CASCADE"), nullable=False)
    updated_ml_capability: Mapped[dict | None] = mapped_column(JSON)  # AvailMLCapabilityReport
    ml_model_refs: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(default=_now)
