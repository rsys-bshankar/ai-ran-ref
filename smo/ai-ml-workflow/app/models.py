import datetime
import uuid

from sqlalchemy import ARRAY, CheckConstraint, ForeignKey, Integer, JSON, LargeBinary, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from smo_shared.db import Base


class MLModelCoordinationGroup(Base):
    __tablename__ = "ml_model_coordination_group"

    group_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    group_type: Mapped[str] = mapped_column(String, nullable=False, default="SHARED_MODEL")
    member_model_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid).with_variant(JSON(none_as_null=True), "sqlite"), nullable=False)
    member_use_cases: Mapped[list[str] | None] = mapped_column(ARRAY(String).with_variant(JSON(none_as_null=True), "sqlite"))
    shared_feature_pipeline_ref: Mapped[str | None] = mapped_column(String)
    retrain_propagation: Mapped[str] = mapped_column(String, nullable=False, default="ANY_MEMBER_TRIGGERS")


class AIMLModel(Base):
    """OPEN_ITEMS.md section 5: no uniqueness/conflict check on
    (model_type, version) existed — duplicate registrations silently
    succeeded where the reference 409s. The reference's own ModelID
    (modelInfo.go) is a composite primary key on
    (modelName, modelVersion); this build never introduced a separate
    name field, so model_type plays that identifying role already —
    the same adaptation this codebase already made elsewhere (e.g.
    DMEType's own (namespace, name, version) UniqueConstraint).
    """
    __tablename__ = "aiml_model"
    __table_args__ = (UniqueConstraint("model_type", "version"),)

    model_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    registration_id: Mapped[str] = mapped_column(String, nullable=False)
    model_type: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False, default="REGISTERED")
    training_job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    training_data_lineage: Mapped[dict | None] = mapped_column(JSON)
    integrity_hash: Mapped[str | None] = mapped_column(String)
    artifact_location: Mapped[str | None] = mapped_column(String)
    required_resource_type_id: Mapped[str | None] = mapped_column(String)
    # NEW section 5: the reference's own ModelRelatedInformation/
    # ModelInformation/Metadata/TargetEnvironment (modelInfo.go) — all
    # required there, kept optional here since this build's own
    # RegisterModel was already permissive before this pass and nothing
    # should retroactively reject an existing caller. target_environments
    # stored as JSON, not a normalized child table — registered and read
    # back wholesale, the same adaptation this build already uses for
    # SME's aefProfiles/DMEType.collection_spec.
    description: Mapped[str | None] = mapped_column(String)
    author: Mapped[str | None] = mapped_column(String)
    owner: Mapped[str | None] = mapped_column(String)
    input_data_type: Mapped[str | None] = mapped_column(String)
    output_data_type: Mapped[str | None] = mapped_column(String)
    target_environments: Mapped[list[dict] | None] = mapped_column(JSON)
    cleared_node_groups: Mapped[list[str] | None] = mapped_column(ARRAY(String).with_variant(JSON(none_as_null=True), "sqlite"))  # MultiNode Q2 gap closure, LLD section 5


class ModelArtifact(Base):
    """OPEN_ITEMS.md section 5: the reference's real UploadModel/DownloadModel
    (S3-backed), with an auto-incrementing artifactVersion distinct from
    modelVersion. Real S3 storage is a total, deliberate elision in this
    build (same as elsewhere), so `content` holds the actual uploaded bytes
    in-DB — the honest, minimal, self-contained substitute: upload and
    download genuinely round-trip within the sandbox rather than being a
    metadata-only stub.
    """

    __tablename__ = "model_artifact"
    __table_args__ = (
        CheckConstraint("artifact_version >= 1", name="artifact_version_positive"),
    )

    artifact_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    model_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("aiml_model.model_id", ondelete="CASCADE"), nullable=False)  # NEW section 5: deregister_model's cascade, same shape as DME's dme_type FKs
    artifact_version: Mapped[int] = mapped_column(Integer, nullable=False)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    uploaded_at: Mapped[datetime.datetime] = mapped_column(default=lambda: datetime.datetime.now(datetime.UTC))


class TrainingJob(Base):
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
    model_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("aiml_model.model_id", ondelete="CASCADE"))  # NEW section 5: deregister_model's cascade
    model_coordination_group_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("ml_model_coordination_group.group_id"))
    producer_type: Mapped[str] = mapped_column(String, nullable=False, default="rApp")
    producer_id: Mapped[str] = mapped_column(String, nullable=False)
    required_data: Mapped[dict | None] = mapped_column(JSON)
    validation_criteria: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String, nullable=False, default="PENDING")
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
    # NEW SPEC_AUDIT.md: TS28.105 AI/ML NRM's own real, closed 4-value
    # mLTrainingType enum on both MLModel and MLTrainingRequest —
    # request_training already computes this exact INITIAL_TRAINING-vs-
    # RE_TRAINING distinction internally (as a ModelEvent.TRAIN/RETRAIN
    # FSM choice) but never stored or returned it anywhere.
    # PRE_SPECIALISED_TRAINING/FINE_TUNING have no equivalent concept in
    # this build, so only two of the spec's four values are ever
    # produced here — an honest partial mapping, not a fabricated one.
    ml_training_type: Mapped[str | None] = mapped_column(String)


class ModelChangeSubscription(Base):
    __tablename__ = "model_change_subscription"

    subscription_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    model_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("aiml_model.model_id", ondelete="CASCADE"))  # NEW section 5: deregister_model's cascade
    consumer_id: Mapped[str] = mapped_column(String, nullable=False)


class MLMFSubscription(Base):
    __tablename__ = "mlmf_subscription"

    subscription_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    model_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("aiml_model.model_id", ondelete="CASCADE"))  # NEW section 5: deregister_model's cascade
    metric_types: Mapped[list[str]] = mapped_column(ARRAY(String).with_variant(JSON(none_as_null=True), "sqlite"), nullable=False)
    dme_type_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    guard_kpi_floor: Mapped[dict | None] = mapped_column(JSON)


class PerformanceReport(Base):
    __tablename__ = "performance_report"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subscription_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("mlmf_subscription.subscription_id", ondelete="CASCADE"))  # NEW section 5: deregister_model's cascade, transitively via mlmf_subscription
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False)
    breached_floor: Mapped[bool] = mapped_column(default=False)
    reported_at: Mapped[datetime.datetime] = mapped_column(default=lambda: datetime.datetime.now(datetime.UTC))


class InferenceJob(Base):
    __tablename__ = "inference_job"

    inference_job_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    model_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("aiml_model.model_id", ondelete="CASCADE"))  # NEW section 5: deregister_model's cascade
    status: Mapped[str] = mapped_column(String, nullable=False, default="RUNNING")
    notification_destination: Mapped[str | None] = mapped_column(String)


class FeatureGroup(Base):
    """OPEN_ITEMS.md section 5: no feature-group/feature-store concept
    existed at all — the reference's own FeatureGroup
    (aiml-fw-awmf-tm's trainingmgr/models/featuregroup.py), registered
    through its own Training Manager sub-service, co-located with
    TrainingJob in the same real repo this build's ai-ml-workflow
    module maps to. Real Cassandra-backed feature storage (the ADOPT
    target, aiml-fw-athp-sdk-feature-store) and the reference's own
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
