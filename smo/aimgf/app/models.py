import datetime
import uuid

from sqlalchemy import ARRAY, CheckConstraint, ForeignKey, JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from smo_shared.db import Base


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


class InferenceJob(Base):
    __tablename__ = "inference_job"

    inference_job_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    model_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)  # bare UUID — see TrainingJob's docstring
    status: Mapped[str] = mapped_column(String, nullable=False, default="RUNNING")
    notification_destination: Mapped[str | None] = mapped_column(String)


class MLMFSubscription(Base):
    __tablename__ = "mlmf_subscription"

    subscription_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    model_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)  # bare UUID — see TrainingJob's docstring
    metric_types: Mapped[list[str]] = mapped_column(ARRAY(String).with_variant(JSON(none_as_null=True), "sqlite"), nullable=False)
    dme_type_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    guard_kpi_floor: Mapped[dict | None] = mapped_column(JSON)


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
    sets" per docs/architecture/SERVICE_OWNERSHIP_MATRIX.md) is
    explicitly frozen unchanged this wave, so moving it there is out of
    scope. The reference registers FeatureGroup through its own Training
    Manager sub-service, co-located with TrainingJob in the same real
    repo this build's training routes map to — the closest real-world
    precedent, and consistent with this module's own header docstring
    already listing MLMF (a monitoring/governance concern) alongside
    AIMgF rather than MLMR/MLLF. Flagged here for the record in case a
    later wave wants to revisit it.

    OPEN_ITEMS.md section 5: no feature-group/feature-store concept
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
