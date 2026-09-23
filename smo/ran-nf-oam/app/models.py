import datetime
import uuid

from sqlalchemy import ARRAY, Boolean, DateTime, ForeignKey, JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from smo_shared.db import Base


class O1AdaptorEndpoint(Base):
    __tablename__ = "o1_adaptor_endpoint"

    endpoint_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    managed_element_ref: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    adaptor_uri: Mapped[str] = mapped_column(String, nullable=False)
    protocol_support: Mapped[list[str]] = mapped_column(ARRAY(String).with_variant(JSON(none_as_null=True), "sqlite"), nullable=False)
    registered_via: Mapped[str] = mapped_column(String, nullable=False, default="MNS_REGISTRY_NRM")
    health_status: Mapped[str] = mapped_column(String, nullable=False, default="ACTIVE")
    last_heartbeat_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))


class ManagedEntity(Base):
    __tablename__ = "managed_entity"

    managed_element_ref: Mapped[str] = mapped_column(String, primary_key=True)
    managed_function_ref: Mapped[str | None] = mapped_column(String)
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    vendor_name: Mapped[str | None] = mapped_column(String)
    o1_protocol: Mapped[str] = mapped_column(String, nullable=False)
    o1_adaptor_endpoint_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("o1_adaptor_endpoint.endpoint_id"))


class Alarm(Base):
    __tablename__ = "alarm"

    alarm_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_alarm_id: Mapped[str] = mapped_column(String, nullable=False)
    managed_element_ref: Mapped[str] = mapped_column(String, ForeignKey("managed_entity.managed_element_ref"))
    managed_function_ref: Mapped[str | None] = mapped_column(String)
    severity: Mapped[str] = mapped_column(String, nullable=False)  # this build's own wire name for 3GPP's perceivedSeverity
    ack_state: Mapped[str] = mapped_column(String, nullable=False, default="UNACKNOWLEDGED")
    correlation_group: Mapped[str | None] = mapped_column(String)
    raised_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))
    # OPEN_ITEMS.md section 5: standard 3GPP TS 28.532 FaultMnS NotifyNewAlarm
    # fields (per oam's own stndDefined-r16-notify-new-alarm.json VES template)
    # this alarm model was missing entirely.
    probable_cause: Mapped[str | None] = mapped_column(String)
    specific_problem: Mapped[str | None] = mapped_column(String)
    root_cause_indicator: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    correlated_notifications: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid).with_variant(JSON(none_as_null=True), "sqlite"), nullable=False, default=list)
    proposed_repair_actions: Mapped[str | None] = mapped_column(String)


class CMSchemaCache(Base):
    __tablename__ = "cm_schema_cache"

    schema_name: Mapped[str] = mapped_column(String, primary_key=True)
    revision: Mapped[str] = mapped_column(String, primary_key=True, default="")
    location: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False, default="YANG")
    cached_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))


class WriteConfigJob(Base):
    __tablename__ = "write_config_job"

    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    requested_by: Mapped[str] = mapped_column(String, nullable=False)
    scope: Mapped[str] = mapped_column(String, nullable=False)
    schema_validated_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String, nullable=False, default="PENDING")
    conflict_resolution: Mapped[str | None] = mapped_column(String)
    msac_role: Mapped[str | None] = mapped_column(String)


class WriteConfigSubChange(Base):
    __tablename__ = "write_config_sub_change"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("write_config_job.job_id"))
    managed_element_ref: Mapped[str] = mapped_column(String, nullable=False)
    managed_function_ref: Mapped[str | None] = mapped_column(String)
    attribute_changes: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="PENDING")
    rejection_reason: Mapped[str | None] = mapped_column(String)


class PMSubscription(Base):
    __tablename__ = "pm_subscription"

    subscription_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    managed_element_ref: Mapped[str] = mapped_column(String, ForeignKey("managed_entity.managed_element_ref"))
    counter_type: Mapped[str] = mapped_column(String, nullable=False)
    delivery_method: Mapped[str] = mapped_column(String, nullable=False)
    southbound_engine: Mapped[str] = mapped_column(String, nullable=False)


class SoftwareManagementJob(Base):
    __tablename__ = "software_management_job"

    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    managed_element_ref: Mapped[str] = mapped_column(String, ForeignKey("managed_entity.managed_element_ref"))
    ru_instance_id: Mapped[str | None] = mapped_column(String)  # reserved, section 3.4
    phase: Mapped[str] = mapped_column(String, nullable=False, default="DOWNLOAD")
    status: Mapped[str] = mapped_column(String, nullable=False, default="PENDING")
