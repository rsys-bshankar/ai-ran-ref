import datetime
import uuid

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from smo_shared.db import Base


class AssuranceMonitor(Base):
    __tablename__ = "assurance_monitor"
    __table_args__ = (
        # Portable boolean form — "::int" cast syntax is Postgres-only and
        # fails on SQLite.
        CheckConstraint(
            "(CASE WHEN target_order_id IS NULL THEN 0 ELSE 1 END"
            " + CASE WHEN target_coordination_group_id IS NULL THEN 0 ELSE 1 END"
            " + CASE WHEN target_rapp_instance_id IS NULL THEN 0 ELSE 1 END) <= 1",
            name="one_target_only",
        ),
    )

    monitor_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    target_order_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    target_coordination_group_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # NEW, SO/SA SMOS LLD section 2.2
    # OI-1-sa-rollback: a monitor on one rApp instance, so ROLLBACK has a target
    # with a version history (rApp Management's). A bare cross-module ref: the
    # instance id may be superseded by an upgrade; rApp Management resolves it.
    target_rapp_instance_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    analytics_subscription_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    requirement_thresholds: Mapped[dict] = mapped_column(JSON, nullable=False)


class RemedialAction(Base):
    __tablename__ = "remedial_action"

    action_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    monitor_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("assurance_monitor.monitor_id"))
    action_type: Mapped[str] = mapped_column(String, nullable=False)
    auto_executed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auto_execution_scope_config: Mapped[str | None] = mapped_column(String)
    outcome: Mapped[str | None] = mapped_column(String)


class O1CmEnactment(Base):
    """Wave 8 (HISTORY.md W8-07, decision D-1): one record
    per Intent the generic O1-CM intent handler enacted — which DME actions
    (and so which RAN NF OAM config jobs) it issued, and the outcome it
    reported back as the Intent's fulfilment."""
    __tablename__ = "o1_cm_enactment"

    enactment_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    intent_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)  # -> intent (Intent Service), bare cross-module ref
    status: Mapped[str] = mapped_column(String, nullable=False)  # FULFILLED | NOT_FULFILLED
    actions: Mapped[list] = mapped_column(JSON, nullable=False)  # [{expectationId, actionId, forwardedJobId, status}]
    unsupported_targets: Mapped[list] = mapped_column(JSON, nullable=False)
    intent_report_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                           default=lambda: datetime.datetime.now(datetime.UTC))
