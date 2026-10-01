import datetime
import uuid

from sqlalchemy import ARRAY, Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from smo_shared.db import Base


class InventorySubscription(Base):
    """HISTORY.md §7 item 8: ORAN.O2ims.Inventory.yaml's InventorySubscription
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
    """HISTORY.md §5: no ResourceType/ResourcePool/DeploymentManager
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
    # HISTORY.md §7 item 7: ORAN.O2ims.Inventory.yaml's ResourceType
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
    # SA-FOCOM-2: the O-Cloud site the pool is part of (O2IMS ResourcePool.oCloudSiteId)
    o_cloud_site_id: Mapped[str | None] = mapped_column(String)
    extensions: Mapped[list | None] = mapped_column(JSON)


class Location(Base):
    """O2IMS Location: where O-Cloud sites are or can be deployed (SA-FOCOM-2)."""
    __tablename__ = "ocloud_location"

    global_location_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False, default="")
    o_cloud_id: Mapped[str] = mapped_column(String, nullable=False)
    coordinate: Mapped[str | None] = mapped_column(String)
    address: Mapped[str | None] = mapped_column(String)
    extensions: Mapped[list | None] = mapped_column(JSON)


class OCloudSite(Base):
    """O2IMS OCloudSite: a place resource pools are part of (SA-FOCOM-2)."""
    __tablename__ = "ocloud_site"

    o_cloud_site_id: Mapped[str] = mapped_column(String, primary_key=True)
    location_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False, default="")
    o_cloud_id: Mapped[str] = mapped_column(String, nullable=False)
    extensions: Mapped[list | None] = mapped_column(JSON)


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
    # HISTORY.md §7 item 7: ORAN.O2ims.Inventory.yaml's Resource requires
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
    # HISTORY.md §7 item 7: ORAN.O2ims.Inventory.yaml requires these three
    # (arrays of globalLocationId / AttributeValuePair / AttributeValuePair
    # respectively) — entirely absent from this model. Nullable: no route
    # in this build registers a DeploymentManager with this much detail
    # (only the Phase 1 seed in _ensure_phase1_topology, which has no
    # real capacity/capability introspection to report).
    supported_locations: Mapped[list[str] | None] = mapped_column(ARRAY(String).with_variant(JSON(none_as_null=True), "sqlite"))
    capabilities: Mapped[list | None] = mapped_column(JSON)
    capacity: Mapped[list | None] = mapped_column(JSON)


class OCloudAlarm(Base):
    """An O2IMS AlarmEventRecord (SA-FOCOM-6). `severity` keeps the lowercase
    wire value this build always used; `perceivedSeverity` is its upper-case view."""
    __tablename__ = "ocloud_alarm"

    alarm_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    resource_ref: Mapped[str] = mapped_column(String, nullable=False)  # distinct domain from RAN NF OAM's Alarm — infra, not RAN-function
    severity: Mapped[str] = mapped_column(String, nullable=False)
    raised_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))
    resource_type_id: Mapped[str | None] = mapped_column(String)
    alarm_definition_id: Mapped[str | None] = mapped_column(String)
    probable_cause_id: Mapped[str | None] = mapped_column(String)
    event_type: Mapped[str] = mapped_column(String, nullable=False, default="OTHER")
    changed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    cleared_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    extensions: Mapped[list | None] = mapped_column(JSON)


class AlarmSubscription(Base):
    __tablename__ = "ocloud_alarm_subscription"

    subscription_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    callback: Mapped[str] = mapped_column(String, nullable=False)
    consumer_subscription_id: Mapped[str | None] = mapped_column(String)
    filter: Mapped[str | None] = mapped_column(String)  # NEW | CHANGE | CLEAR | ACKNOWLEDGE; unset = all


class OCloudPerformanceMetric(Base):
    """An O2IMS PerformanceMeasurementRecord. `resource_ref` = resourceId,
    `metric_name` = performanceMeasurementDefinitionId."""
    __tablename__ = "ocloud_performance_metric"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    resource_ref: Mapped[str] = mapped_column(String, nullable=False)
    metric_name: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[float | None] = mapped_column(Float)  # the scalar form of measurementValue
    collected_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))
    job_id: Mapped[str | None] = mapped_column(String)
    measurement_value: Mapped[dict | None] = mapped_column(JSON)  # the object form
    is_suspect: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class PerformanceJob(Base):
    """O2IMS PerformanceMeasurementJob."""
    __tablename__ = "ocloud_performance_job"

    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    consumer_job_id: Mapped[str | None] = mapped_column(String)
    state: Mapped[str] = mapped_column(String, nullable=False, default="ACTIVE")  # ACTIVE | SUSPENDED | DEPRECATED
    collection_interval: Mapped[int] = mapped_column(Integer, nullable=False)
    resource_scope_criteria: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    measurement_selection_criteria: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String, nullable=False, default="IDLE")  # RUNNING | FAILED | DEGRADED | IDLE | PENDING_DELETE
    pre_installed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    qualified_resource_types: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    extensions: Mapped[list | None] = mapped_column(JSON)


class PerformanceSubscription(Base):
    """O2IMS PerformanceSubscription (NOTIFICATION reporting)."""
    __tablename__ = "ocloud_performance_subscription"

    subscription_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    consumer_subscription_id: Mapped[str | None] = mapped_column(String)
    global_subscription_criteria: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    report_format: Mapped[str] = mapped_column(String, nullable=False, default="NOTIFICATION")
    callback: Mapped[str] = mapped_column(String, nullable=False)
    measurement_reporting_frequencies: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class O2imsObject(Base):
    """Artifact, cluster, infrastructure and provisioning resources
    (SA-FOCOM-7): one row per object, the spec's attributes in `attributes`,
    validated per `kind` in provisioning.py."""
    __tablename__ = "o2ims_object"

    kind: Mapped[str] = mapped_column(String, primary_key=True)
    object_id: Mapped[str] = mapped_column(String, primary_key=True)
    attributes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))
