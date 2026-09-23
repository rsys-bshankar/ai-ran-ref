import datetime
import uuid

from sqlalchemy import ARRAY, CheckConstraint, ForeignKey, JSON, String, Uuid
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
    __tablename__ = "aiml_model"

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
    cleared_node_groups: Mapped[list[str] | None] = mapped_column(ARRAY(String).with_variant(JSON(none_as_null=True), "sqlite"))  # MultiNode Q2 gap closure, LLD section 5


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
    model_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("aiml_model.model_id"))
    model_coordination_group_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("ml_model_coordination_group.group_id"))
    producer_type: Mapped[str] = mapped_column(String, nullable=False, default="rApp")
    producer_id: Mapped[str] = mapped_column(String, nullable=False)
    required_data: Mapped[dict | None] = mapped_column(JSON)
    validation_criteria: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String, nullable=False, default="PENDING")
    notification_uri: Mapped[str | None] = mapped_column(String)


class ModelChangeSubscription(Base):
    __tablename__ = "model_change_subscription"

    subscription_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    model_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("aiml_model.model_id"))
    consumer_id: Mapped[str] = mapped_column(String, nullable=False)


class MLMFSubscription(Base):
    __tablename__ = "mlmf_subscription"

    subscription_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    model_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("aiml_model.model_id"))
    metric_types: Mapped[list[str]] = mapped_column(ARRAY(String).with_variant(JSON(none_as_null=True), "sqlite"), nullable=False)
    dme_type_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    guard_kpi_floor: Mapped[dict | None] = mapped_column(JSON)


class PerformanceReport(Base):
    __tablename__ = "performance_report"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subscription_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("mlmf_subscription.subscription_id"))
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False)
    breached_floor: Mapped[bool] = mapped_column(default=False)
    reported_at: Mapped[datetime.datetime] = mapped_column(default=lambda: datetime.datetime.now(datetime.UTC))


class InferenceJob(Base):
    __tablename__ = "inference_job"

    inference_job_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    model_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("aiml_model.model_id"))
    status: Mapped[str] = mapped_column(String, nullable=False, default="RUNNING")
    notification_destination: Mapped[str | None] = mapped_column(String)
