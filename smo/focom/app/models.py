import datetime
import uuid

from sqlalchemy import ARRAY, DateTime, Float, ForeignKey, JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from smo_shared.db import Base


class InventorySubscription(Base):
    """SPEC_AUDIT.md item 8: ORAN.O2ims.Inventory.yaml's InventorySubscription
    names this field `callback`, not `callbackUri` — this build's own
    invented name, previously undocumented as a deviation. Renamed
    outright rather than documented: no cross-module caller in this
    build ever used the old name (only this module's own routes/tests).
    consumerSubscriptionId (the spec's own consumer-provided tracking
    id, nullable) was entirely absent.
    """
    __tablename__ = "inventory_subscription"

    subscription_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    callback: Mapped[str] = mapped_column(String, nullable=False)
    consumer_subscription_id: Mapped[str | None] = mapped_column(String)
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
    # SPEC_AUDIT.md item 7: ORAN.O2ims.Inventory.yaml's ResourceType
    # requires these five fields — dictionary refs, a "physicality" enum,
    # a functional-role enum, and vendor extensions — entirely absent
    # from this model. Nullable: no route in this build registers a
    # ResourceType with this much detail (only provision_resource's
    # auto-registration on an unrecognized resourceTypeId, which never
    # had this data either).
    alarm_dictionary_id: Mapped[str | None] = mapped_column(String)
    performance_dictionary_id: Mapped[str | None] = mapped_column(String)
    resource_kind: Mapped[str | None] = mapped_column(String)
    resource_class: Mapped[str | None] = mapped_column(String)
    extensions: Mapped[list | None] = mapped_column(JSON)


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
    # SPEC_AUDIT.md item 7: ORAN.O2ims.Inventory.yaml's Resource requires
    # globalAssetId/tags/groups, all absent — nullable, all optional in
    # the real spec too (globalAssetId "required only if" reportable;
    # tags/groups have no minItems).
    global_asset_id: Mapped[str | None] = mapped_column(String)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String).with_variant(JSON(none_as_null=True), "sqlite"))
    groups: Mapped[list[str] | None] = mapped_column(ARRAY(String).with_variant(JSON(none_as_null=True), "sqlite"))


class DeploymentManager(Base):
    __tablename__ = "deployment_manager"

    deployment_manager_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String)
    o_cloud_id: Mapped[str] = mapped_column(String, nullable=False)
    service_uri: Mapped[str | None] = mapped_column(String)
    # SPEC_AUDIT.md item 7: ORAN.O2ims.Inventory.yaml requires these three
    # (arrays of globalLocationId / AttributeValuePair / AttributeValuePair
    # respectively) — entirely absent from this model. Nullable: no route
    # in this build registers a DeploymentManager with this much detail
    # (only the Phase 1 seed in _ensure_phase1_topology, which has no
    # real capacity/capability introspection to report).
    supported_locations: Mapped[list[str] | None] = mapped_column(ARRAY(String).with_variant(JSON(none_as_null=True), "sqlite"))
    capabilities: Mapped[list | None] = mapped_column(JSON)
    capacity: Mapped[list | None] = mapped_column(JSON)


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
