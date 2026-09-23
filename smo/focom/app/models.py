import datetime
import uuid

from sqlalchemy import DateTime, Float, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from smo_shared.db import Base


class InventorySubscription(Base):
    __tablename__ = "inventory_subscription"

    subscription_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    callback_uri: Mapped[str] = mapped_column(String, nullable=False)
    resource_type_id: Mapped[str | None] = mapped_column(String)  # optional filter; unset matches every resource type


class OCloudAlarm(Base):
    __tablename__ = "ocloud_alarm"

    alarm_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    resource_ref: Mapped[str] = mapped_column(String, nullable=False)  # distinct domain from RAN NF OAM's Alarm — infra, not RAN-function
    severity: Mapped[str] = mapped_column(String, nullable=False)
    raised_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))


class OCloudPerformanceMetric(Base):
    __tablename__ = "ocloud_performance_metric"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    resource_ref: Mapped[str] = mapped_column(String, nullable=False)
    metric_name: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    collected_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))
