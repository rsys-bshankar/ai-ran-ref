import datetime
import uuid

from sqlalchemy import ARRAY, CheckConstraint, ForeignKey, Integer, JSON, LargeBinary, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from smo_shared.db import Base

# Wave 3 (AI Platform Service Decomposition) — TS29482_MLR_MLModelManagement.yaml's
# MLModelDomain enum, SPEC_AUDIT.md's MLMR section.
MODEL_DOMAINS = {"SPEECH_RECOGNITION", "IMAGE_RECOGNITION", "IMAGE_PROCESSING", "LOCATION_PREDICTION", "CUSTOM"}


class MLModelRepository(Base):
    """Wave 4 — TS 28.105 MLModelRepository IOC: the container MLModels
    and MLModelCoordinationGroups are registered into. Optional for
    either — a model/group with no repository is simply uncontained, as
    every pre-Wave-4 row is.
    """
    __tablename__ = "ml_model_repository"

    ml_model_repository_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_label: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime.datetime] = mapped_column(default=lambda: datetime.datetime.now(datetime.UTC))


class MLModelCoordinationGroup(Base):
    __tablename__ = "ml_model_coordination_group"

    group_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    group_type: Mapped[str] = mapped_column(String, nullable=False, default="SHARED_MODEL")
    member_model_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid).with_variant(JSON(none_as_null=True), "sqlite"), nullable=False)
    member_use_cases: Mapped[list[str] | None] = mapped_column(ARRAY(String).with_variant(JSON(none_as_null=True), "sqlite"))
    shared_feature_pipeline_ref: Mapped[str | None] = mapped_column(String)
    retrain_propagation: Mapped[str] = mapped_column(String, nullable=False, default="ANY_MEMBER_TRIGGERS")
    ml_model_repository_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("ml_model_repository.ml_model_repository_id", ondelete="SET NULL"))


class MLModel(Base):
    """Wave 1 (AI Platform Service Decomposition): renamed from AIMLModel to
    match TS 28.105's own vocabulary — docs/ownership/MLMR_OWNERSHIP.md.
    Table name (`aiml_model`) is unchanged: it's internal storage, not a
    public contract, so keeping it avoids an unnecessary migration.

    This row is MLMR's repository truth — identity, metadata, versioning,
    artifact location. Wave 1 left `state`/`training_job_id`/
    `cleared_node_groups` on this same row as a structural shortcut
    (AIMgF/MLLF read/wrote them via `PATCH /models/{id}/lifecycle`); Wave 2
    moves all three to AIMgF's own `model_lifecycle` table — MLMR is model
    truth, not lifecycle truth (docs/architecture/SERVICE_OWNERSHIP_MATRIX.md:
    "Lifecycle state: AIMgF ✅, MLMR ❌") — so this row no longer carries
    them at all.

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
    # Wave 3: TS29482_MLR_MLModelManagement.yaml's MLModel schema
    # (SPEC_AUDIT.md's MLMR section) — domain/customDomain mirror the
    # spec's own domain+CUSTOM-string pairing; vendors ties into DME's
    # own Wave 3 multi-vendor provenance principle
    # (docs/ownership/DME_OWNERSHIP.md), same theme applied to model
    # identity rather than data-source identity.
    domain: Mapped[str | None] = mapped_column(String)
    custom_domain: Mapped[str | None] = mapped_column(String)
    vendors: Mapped[list[str] | None] = mapped_column(ARRAY(String).with_variant(JSON(none_as_null=True), "sqlite"))
    # Wave 4 — TS 28.105 MLModel IOC attributes (TS28105_AiMlNrm.yaml),
    # spec-shaped JSON for the complex datatypes. mLModelId/mLModelVersion
    # are model_id/version; the read-only mLTrainingType,
    # aIMLInferenceReportRefList and usedByFunctionRefList are AIMgF's
    # truth and are joined in by `GET /ml-models/{id}`, not stored here.
    aiml_inference_name: Mapped[str | None] = mapped_column(String)
    expected_run_time_context: Mapped[dict | None] = mapped_column(JSON)
    training_context: Mapped[dict | None] = mapped_column(JSON)
    run_time_context: Mapped[dict | None] = mapped_column(JSON)
    supported_performance_indicators: Mapped[list | None] = mapped_column(JSON)
    ml_capabilities_info_list: Mapped[list | None] = mapped_column(JSON)
    inference_scope: Mapped[list | None] = mapped_column(JSON)
    retraining_events_monitor_ref: Mapped[str | None] = mapped_column(String)
    source_trained_ml_model_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    ml_model_repository_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("ml_model_repository.ml_model_repository_id", ondelete="SET NULL"))


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
    # Wave 3: TS29482_MLR_MLModelManagement.yaml's MLModel.mlModelSize
    # (SPEC_AUDIT.md's MLMR section) — computed from the real uploaded
    # bytes, not a separately-declared value that could drift from them.
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)


class ModelChangeSubscription(Base):
    __tablename__ = "model_change_subscription"

    subscription_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    model_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("aiml_model.model_id", ondelete="CASCADE"))  # NEW section 5: deregister_model's cascade
    consumer_id: Mapped[str] = mapped_column(String, nullable=False)
