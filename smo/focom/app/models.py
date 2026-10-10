"""SQLAlchemy models of the FOCOM module: the O-Cloud inventory, the inventory / alarm / performance subscriptions, alarms, performance and the generic
O2-IMS object table.

What it is: one class per table, on the shared `smo_shared.db.Base`. The tables are created by the Alembic revisions in `migrations/` (not by this file),
and `scripts/check_migration_matches_models.py` fails when a column here is missing from, or differs in nullability from, the migrated schema.
The table list is in `focom/README.md` (2.2).

Where it sits: read and written by `main.py`, `sites.py`, `fcaps.py`, `provisioning.py` and `common.py`; the unit tests create the tables on SQLite.

Owns: the column types and defaults. Does not own: any rule between rows. Foreign keys exist only on `resource.resource_type_id` and
`resource.resource_pool_id`; every other reference (site to location, pool to site, `parent_id`, `job_id` on a metric, the attributes of an
`o2ims_object`) is a plain value that the routes check.

Before editing: a column change is a schema revision (`CLAUDE.md`, "Schema changes are revisions"), done in the same PR. `ARRAY(String)` columns carry a
SQLite `JSON` variant so the unit tests run without Postgres; `none_as_null=True` keeps a Python `None` as SQL NULL rather than the JSON text `null`.
"""

import datetime
import uuid

from sqlalchemy import ARRAY, Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from smo_shared.db import Base


class InventorySubscription(Base):
    """A subscriber to inventory-change notifications (O2-IMS `InventorySubscription`), table `inventory_subscription`.

    `callback` is the URL notified on a resource CREATE or DELETE (the spec's name for the field); `consumer_subscription_id` is the consumer's own tracking
    id and is echoed on every notification; `resource_type_id` is an optional filter, and an unset one matches every resource type.
    """
    __tablename__ = "inventory_subscription"

    subscription_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    callback: Mapped[str] = mapped_column(String, nullable=False)
    consumer_subscription_id: Mapped[str | None] = mapped_column(String)
    resource_type_id: Mapped[str | None] = mapped_column(String)  # optional filter; unset matches every resource type


class ResourceType(Base):
    """An O2-IMS `ResourceType`, table `resource_type`. The primary key is the caller-visible string id (`generic`, `gpu-l40`, ...), not a UUID.

    The dictionary ids, `resource_kind`, `resource_class` and `extensions` are nullable: only `POST /resource-types` can set them, and the seeded and
    auto-registered types leave them empty.
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
    """An O2-IMS `ResourcePool`, table `resource_pool`. `o_cloud_site_id` names the `ocloud_site` the pool is part of (SA-FOCOM-2); it is not a foreign key."""
    __tablename__ = "resource_pool"

    resource_pool_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String)
    o_cloud_id: Mapped[str] = mapped_column(String, nullable=False)
    # SA-FOCOM-2: the O-Cloud site the pool is part of (O2IMS ResourcePool.oCloudSiteId)
    o_cloud_site_id: Mapped[str | None] = mapped_column(String)
    extensions: Mapped[list | None] = mapped_column(JSON)


class Location(Base):
    """O2-IMS `Location`: where O-Cloud sites are or can be deployed (SA-FOCOM-2), table `ocloud_location`. `coordinate` and `address` are free strings."""
    __tablename__ = "ocloud_location"

    global_location_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False, default="")
    o_cloud_id: Mapped[str] = mapped_column(String, nullable=False)
    coordinate: Mapped[str | None] = mapped_column(String)
    address: Mapped[str | None] = mapped_column(String)
    extensions: Mapped[list | None] = mapped_column(JSON)


class OCloudSite(Base):
    """O2-IMS `OCloudSite`: a place resource pools are part of (SA-FOCOM-2), table `ocloud_site`. `location_id` is the owning location (not a foreign key)."""
    __tablename__ = "ocloud_site"

    o_cloud_site_id: Mapped[str] = mapped_column(String, primary_key=True)
    location_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False, default="")
    o_cloud_id: Mapped[str] = mapped_column(String, nullable=False)
    extensions: Mapped[list | None] = mapped_column(JSON)


class Resource(Base):
    """A provisioned resource in a resource pool, table `resource`; the row `POST /resources/provision` creates and `DELETE /resources/{id}` removes.

    `parent_id` holds the parent / child resource tree of the O2-IMS inventory (a UUID, not a foreign key); no route sets it, only the shape exists
    and the tests set it directly. `global_asset_id`, `tags` and `groups` are optional as in the spec.
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
    """An O2-IMS `DeploymentManager`, table `deployment_manager`: the endpoint (`service_uri`) that manages deployments on the O-Cloud.

    Only the Phase 1 seed (`dm-0`) creates one; `supported_locations`, `capabilities` and `capacity` stay null because nothing introspects the cluster.
    `GET /inventory` takes its `oCloudId`, `name` and `description` from the `dm-0` row.
    """
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
    """An O2-IMS `AlarmEventRecord` for the infrastructure domain (SA-FOCOM-6), table `ocloud_alarm`; RAN-function alarms are RAN NF OAM's.

    `severity` stores the lowercase value this module has always returned (the GUI reads it); the routes show the spec's upper-case form as
    `perceivedSeverity`. `clear` rewrites `severity` to `cleared`, so the original severity is not kept.
    """
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
    """A subscriber to alarm notifications (O2-IMS `AlarmSubscription`), table `ocloud_alarm_subscription`.

    `filter` limits it to one of NEW, CHANGE, CLEAR or ACKNOWLEDGE; unset means every kind. The route layer validates the value; the column is a plain string.
    """
    __tablename__ = "ocloud_alarm_subscription"

    subscription_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    callback: Mapped[str] = mapped_column(String, nullable=False)
    consumer_subscription_id: Mapped[str | None] = mapped_column(String)
    filter: Mapped[str | None] = mapped_column(String)  # NEW | CHANGE | CLEAR | ACKNOWLEDGE; unset = all


class OCloudPerformanceMetric(Base):
    """An O2-IMS `PerformanceMeasurementRecord`, table `ocloud_performance_metric`.

    A scalar measurement is stored in `value`; an object-valued one in `measurement_value` (and `value` stays null). `job_id` is the string form of a
    `PerformanceJob.job_id`, or null for a record ingested with no job. The index `ix_ocloud_performance_metric_resource_metric_collected`
    (revision 0038) serves the newest-record-per-resource-and-measurement read of the utilisation routes (`fcaps.py`).
    """
    __tablename__ = "ocloud_performance_metric"
    __table_args__ = (Index("ix_ocloud_performance_metric_resource_metric_collected", "resource_ref", "metric_name", "collected_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    resource_ref: Mapped[str] = mapped_column(String, nullable=False)
    metric_name: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[float | None] = mapped_column(Float)  # the scalar form of measurementValue
    collected_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))
    job_id: Mapped[str | None] = mapped_column(String)
    measurement_value: Mapped[dict | None] = mapped_column(JSON)  # the object form
    is_suspect: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class PerformanceJob(Base):
    """An O2-IMS `PerformanceMeasurementJob`, table `ocloud_performance_job`.

    FOCOM collects nothing: `collection_interval` is stored, and a job's measured resources and collected measurements are derived from the records ingested
    for it. `status` moves to RUNNING on the first ingest for the job and back to IDLE when the job is suspended or deprecated.
    """
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
    """An O2-IMS `PerformanceSubscription` with NOTIFICATION reporting, table `ocloud_performance_subscription`.

    `global_subscription_criteria` and `measurement_reporting_frequencies` are stored as the JSON the caller sent; only the criteria are used (to decide
    which records are reported), the reporting frequencies are stored and echoed.
    """
    __tablename__ = "ocloud_performance_subscription"

    subscription_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    consumer_subscription_id: Mapped[str | None] = mapped_column(String)
    global_subscription_criteria: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    report_format: Mapped[str] = mapped_column(String, nullable=False, default="NOTIFICATION")
    callback: Mapped[str] = mapped_column(String, nullable=False)
    measurement_reporting_frequencies: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class O2imsObject(Base):
    """One row per Artifact, Cluster, Infrastructure or Provisioning object (SA-FOCOM-7), table `o2ims_object`, keyed by (`kind`, `object_id`).

    The spec's attributes are one JSON document in `attributes`, validated per kind by the pydantic models in `provisioning.py` before they are stored,
    so the database enforces nothing about them; references between objects are checked by the routes.
    """
    __tablename__ = "o2ims_object"

    kind: Mapped[str] = mapped_column(String, primary_key=True)
    object_id: Mapped[str] = mapped_column(String, primary_key=True)
    attributes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))
