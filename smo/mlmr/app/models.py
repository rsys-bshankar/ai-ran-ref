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


class MLModel(Base):
    """Wave 1 (AI Platform Service Decomposition): renamed from AIMLModel to
    match TS 28.105's own vocabulary — docs/ownership/MLMR_OWNERSHIP.md.
    Table name (`aiml_model`) is unchanged: it's internal storage, not a
    public contract, so keeping it avoids an unnecessary migration.

    This row is MLMR's repository truth — identity, metadata, versioning,
    artifact location. `state`/`training_job_id`/`cleared_node_groups` stay
    on this same row for Wave 1 (a structural split, not yet the full
    aggregate redesign Wave 2 does): AIMgF and MLLF read/write them via
    `PATCH /models/{id}/lifecycle` (see main.py) rather than owning a
    separate copy, exactly as every other cross-module reference in this
    build goes through the owning service's own routes rather than a
    direct cross-module ORM import (e.g. NFO calling FOCOM's `/inventory`).

    OPEN_ITEMS.md section 5: no uniqueness/conflict check on
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


class ModelChangeSubscription(Base):
    __tablename__ = "model_change_subscription"

    subscription_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    model_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("aiml_model.model_id", ondelete="CASCADE"))  # NEW section 5: deregister_model's cascade
    consumer_id: Mapped[str] = mapped_column(String, nullable=False)
