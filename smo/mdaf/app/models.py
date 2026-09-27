import datetime
import uuid

from sqlalchemy import ARRAY, DateTime, JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from smo_shared.db import Base


class MDAFReport(Base):
    __tablename__ = "mdaf_report"

    report_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    analytics_type: Mapped[str] = mapped_column(String, nullable=False)
    scope: Mapped[dict | None] = mapped_column(JSON)
    input_sources: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid).with_variant(JSON(none_as_null=True), "sqlite"), nullable=False)
    output: Mapped[dict] = mapped_column(JSON, nullable=False)
    generated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))
    subscriber_attribution: Mapped[str | None] = mapped_column(String)


class MDASubscription(Base):
    __tablename__ = "mda_subscription"

    subscription_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    analytics_type: Mapped[str] = mapped_column(String, nullable=False)
    scope: Mapped[dict | None] = mapped_column(JSON)
    requested_by: Mapped[str] = mapped_column(String, nullable=False)
    notification_destination: Mapped[str | None] = mapped_column(String)  # NEW section 5: publish_report's actual delivery target — see main.py
    # Wave 3 (AI Platform Service Decomposition) — TS28.104 ThresholdInfo
    # (SPEC_AUDIT.md's MDAF section, cribbed from AIMgF's own
    # MLMFSubscription.guard_kpi_floor). threshold_info is the wire-shaped
    # list of {monitoredMDAOutputIE, thresholdDirection, thresholdValue,
    # hysteresis} the subscriber declared; threshold_state is this
    # subscription's own last-known ABOVE/BELOW side per monitored IE —
    # real edge-triggered bookkeeping, not a decorative field, since a
    # hysteresis band that never actually gates anything is exactly the
    # class of "declared but silently unused" bug this build's own audits
    # repeatedly catch elsewhere (DME's data_category, MDAF's own
    # previously no-op subscriber notify).
    threshold_info: Mapped[list[dict] | None] = mapped_column(JSON)
    threshold_state: Mapped[dict | None] = mapped_column(JSON)
