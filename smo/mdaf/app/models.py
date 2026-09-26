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
