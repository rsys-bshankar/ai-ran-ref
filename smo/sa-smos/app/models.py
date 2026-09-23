import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from smo_shared.db import Base


class AssuranceMonitor(Base):
    __tablename__ = "assurance_monitor"
    __table_args__ = (
        # Portable boolean form — "::int" cast syntax is Postgres-only and
        # fails on SQLite.
        CheckConstraint(
            "NOT (target_order_id IS NOT NULL AND target_coordination_group_id IS NOT NULL)",
            name="one_target_only",
        ),
    )

    monitor_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    target_order_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    target_coordination_group_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # NEW, SO/SA SMOS LLD section 2.2
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
