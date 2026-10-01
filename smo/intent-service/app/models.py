import datetime
import uuid

from sqlalchemy import ARRAY, Boolean, ForeignKey, Integer, JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from smo_shared.db import Base


class Intent(Base):
    __tablename__ = "intent"

    intent_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_label: Mapped[str | None] = mapped_column(String)
    intent_expectations: Mapped[list] = mapped_column(JSON, nullable=False)  # opaque, TS 28.312 text not in this corpus
    intent_mgmt_purpose: Mapped[str | None] = mapped_column(String)
    intent_admin_state: Mapped[str] = mapped_column(String, nullable=False, default="ACTIVATED")
    intent_priority: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    intent_preemption_capability: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rmio_id: Mapped[str] = mapped_column(String, nullable=False)
    # Wave 3 (docs/ownership/INTENT_SERVICE_OWNERSHIP.md's "Open item
    # carried into Wave 3"): consumer-side RMIH selection — TS28.312's
    # own NRM containment (IntentHandlingFunction *contains* Intent).
    # ON DELETE CASCADE matches that containment literally.
    rmih_id: Mapped[str] = mapped_column(String, ForeignKey("intent_handling_function.rmih_id", ondelete="CASCADE"), nullable=False)


class IntentReport(Base):
    __tablename__ = "intent_report"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    intent_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("intent.intent_id", ondelete="CASCADE"))  # was intent_reference (bare string) pre-LLD; NEW: delete_intent's cascade
    intent_fulfilment_report: Mapped[dict | None] = mapped_column(JSON)
    intent_conflict_reports: Mapped[list | None] = mapped_column(JSON)
    last_updated_time: Mapped[datetime.datetime] = mapped_column(default=lambda: datetime.datetime.now(datetime.UTC))


class IntentHandlingFunction(Base):
    __tablename__ = "intent_handling_function"

    rmih_id: Mapped[str] = mapped_column(String, primary_key=True)
    sme_service_id: Mapped[str] = mapped_column(String, nullable=False)
    intent_handling_scope: Mapped[list | None] = mapped_column(JSON)
    intent_handling_capability_list: Mapped[list] = mapped_column(JSON, nullable=False)
    # Closes the Intent-to-RMIH matching/dispatch gap — CreateIntent POSTs
    # here on a capability match, same established pattern as DME's
    # producerHealthCallbackUrl.
    notification_destination: Mapped[str] = mapped_column(String, nullable=False)


class AutonomyDispatch(Base):
    """OPEN_ITEMS.md section 6.3 — rApp Autonomy Modes: closes call flow
    02/03's own "no automated hand-off from inference outcome to Intent"
    gap and call flow 09's own "who creates an Intent and why" gap. A
    real, queryable record of each inference-driven dispatch decision —
    one per RequestAutonomyDispatch call — distinct from `Intent` itself
    since not every mode actually produces one (SHADOW never does; ASSIST
    doesn't until an operator resolves it).

    instance_id/model_id are bare UUIDs, not ORM ForeignKeys — rapp-mgmt
    and MLMR run in their own processes, the same cross-module-reference
    shape used throughout this build (e.g. TrainingJob's own model_id).
    rmih_id is a real FK: IntentHandlingFunction lives in this same
    module/table.
    """
    __tablename__ = "autonomy_dispatch"

    dispatch_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    instance_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)  # -> rapp_instance (rApp Mgmt)
    model_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # -> aiml_model (MLMR) — the triggering inference, if any
    # Snapshotted from the instance at dispatch time, not re-read later —
    # a later mode change on the instance should never silently rewrite
    # what already happened here.
    autonomy_mode: Mapped[str] = mapped_column(String, nullable=False)
    expectations: Mapped[list] = mapped_column(JSON, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    rmih_id: Mapped[str] = mapped_column(String, ForeignKey("intent_handling_function.rmih_id", ondelete="CASCADE"), nullable=False)
    intent_mgmt_purpose: Mapped[str | None] = mapped_column(String)
    intent_handling_scope: Mapped[str | None] = mapped_column(String)
    # The scope actually applied — AUTONOMOUS's own pre-configured
    # rapp_instance.region_scope, or an operator's own resolve-time
    # value for ASSIST. Null for SHADOW (nothing is ever enforced) and
    # for an ASSIST dispatch still AWAITING_SCOPE.
    region_scope: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String, nullable=False)  # AWAITING_SCOPE | DISPATCHED | SHADOWED
    intent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("intent.intent_id", ondelete="SET NULL"))
    # All three modes always notify the operator — not mode-gated.
    notification_destination: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime.datetime] = mapped_column(default=lambda: datetime.datetime.now(datetime.UTC))
