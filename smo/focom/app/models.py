import datetime
import uuid

from sqlalchemy import DateTime, Float, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from smo_shared.db import Base


class InventorySubscription(Base):
    __tablename__ = "inventory_subscription"

    subscription_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    callback_uri: Mapped[str] = mapped_column(String, nullable=False)
    resource_type_id: Mapped[str | None] = mapped_column(String)  # optional filter; unset matches every resource type


class ResourceType(Base):
    """OPEN_ITEMS.md section 5: no ResourceType/ResourcePool/DeploymentManager
    schema existed at all — not just an empty collection behind the
    documented single-cluster limitation, but no model shape to extend
    later. String PK (not a random UUID), matching this module's existing
    literal-ID convention for Phase 1's degenerate concepts
    (PHASE1_CLUSTER_ID, the "pool-0" resource pool).
    """
    __tablename__ = "resource_type"

    resource_type_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String)
    vendor: Mapped[str | None] = mapped_column(String)
    model: Mapped[str | None] = mapped_column(String)
    version: Mapped[str | None] = mapped_column(String)


class ResourcePool(Base):
    __tablename__ = "resource_pool"

    resource_pool_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String)
    o_cloud_id: Mapped[str] = mapped_column(String, nullable=False)


class Resource(Base):
    """A provisioned resource within a ResourcePool — what
    provision_resource/deprovision_resource actually persist now,
    replacing the previous stub that returned a random UUID and stored
    nothing. parent_id supports the reference's parent/child resource
    tree (pserver -> CPU/RAM/interfaces/...); real hardware telemetry
    populating that tree stays out of scope, same as elsewhere in this
    build — only the shape exists.
    """
    __tablename__ = "resource"

    resource_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    resource_type_id: Mapped[str] = mapped_column(String, ForeignKey("resource_type.resource_type_id"), nullable=False)
    resource_pool_id: Mapped[str] = mapped_column(String, ForeignKey("resource_pool.resource_pool_id"), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    description: Mapped[str | None] = mapped_column(String)


class DeploymentManager(Base):
    __tablename__ = "deployment_manager"

    deployment_manager_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String)
    o_cloud_id: Mapped[str] = mapped_column(String, nullable=False)
    service_uri: Mapped[str | None] = mapped_column(String)


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
