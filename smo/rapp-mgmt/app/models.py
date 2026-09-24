import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from smo_shared.db import Base


class RAppInstance(Base):
    __tablename__ = "rapp_instance"

    instance_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    package_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("application_package.package_id"))
    state: Mapped[str] = mapped_column(String, nullable=False, default="DEPLOYING")
    configuration: Mapped[dict | None] = mapped_column(JSON)
    workload_ref: Mapped[str | None] = mapped_column(String)
    oauth_client_id: Mapped[str | None] = mapped_column(String)  # == rAppId (identity.py); None once revoked
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))
    upgrade_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)  # confirmed default, LLD section 6 (OPEN_ITEMS.md section 1)
    pending_upgrade_instance_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # links old row to its in-flight replacement
    package_usage_registration_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("package_usage_registration.id"))


class RAppFaultReport(Base):
    __tablename__ = "rapp_fault_report"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    instance_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("rapp_instance.instance_id", ondelete="CASCADE"))  # NEW section 5: delete_instance's cascade
    severity: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String)


class RAppPerformanceReport(Base):
    __tablename__ = "rapp_performance_report"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    instance_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("rapp_instance.instance_id", ondelete="CASCADE"))  # NEW section 5: delete_instance's cascade
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False)
