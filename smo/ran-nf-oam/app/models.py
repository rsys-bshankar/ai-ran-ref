import datetime
import uuid

from sqlalchemy import ARRAY, Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, UniqueConstraint, Uuid, false
from sqlalchemy.orm import Mapped, mapped_column

from smo_shared.db import Base
from smo_shared.versioning import Versioned


class O1AdaptorEndpoint(Base):
    __tablename__ = "o1_adaptor_endpoint"

    endpoint_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    managed_element_ref: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    adaptor_uri: Mapped[str] = mapped_column(String, nullable=False)
    protocol_support: Mapped[list[str]] = mapped_column(ARRAY(String).with_variant(JSON(none_as_null=True), "sqlite"), nullable=False)
    registered_via: Mapped[str] = mapped_column(String, nullable=False, default="MNS_REGISTRY_NRM")
    health_status: Mapped[str] = mapped_column(String, nullable=False, default="ACTIVE")
    last_heartbeat_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    # Wave 9 (W9-01, docs/ARCHITECTURE.md axis 2): the MnS services
    # this adaptor declares (PROV/FM/PM/FILE/STREAM/SWM/SUBSCRIPTION/HEARTBEAT).
    # NULL means "whatever its vendor's capability declares".
    supported_services: Mapped[list[str] | None] = mapped_column(ARRAY(String).with_variant(JSON(none_as_null=True), "sqlite"))
    # PR-SB-1.2: how the adaptor is reached: 'http-mock' (the XML-over-HTTP mock), 'ssh' (NETCONF over SSH, RFC 6242) or 'tls' (RFC 7589, PR-SB-2.4)
    transport: Mapped[str] = mapped_column(String, nullable=False, default="http-mock", server_default="http-mock")
    # PR-SB-2.1: the NAME of the credential this adaptor is reached with (resolved at connect time from the service's own secrets,
    # netconf_ssh.credentials_for); never the secret. NULL: the shared credential of PR-SB-1.
    credential_ref: Mapped[str | None] = mapped_column(String)


class O1AdaptorHostKey(Base):
    """PR-SB-2.3: a host key an operator pinned for an ssh endpoint (the public key only; at most one per key type)."""
    __tablename__ = "o1_adaptor_host_key"
    __table_args__ = (UniqueConstraint("endpoint_id", "key_type"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    endpoint_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("o1_adaptor_endpoint.endpoint_id", ondelete="CASCADE"), nullable=False)
    key_type: Mapped[str] = mapped_column(String, nullable=False)
    public_key: Mapped[str] = mapped_column(String, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    pinned_by: Mapped[str] = mapped_column(String, nullable=False)
    pinned_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                          default=lambda: datetime.datetime.now(datetime.UTC))


class ManagedObject(Base):
    """PR-SB-6.1: one node of the managed-object containment tree (`mo_tree.py`), keyed by its distinguished name."""
    __tablename__ = "managed_object"

    dn: Mapped[str] = mapped_column(String, primary_key=True)
    parent_dn: Mapped[str | None] = mapped_column(String, ForeignKey("managed_object.dn", ondelete="CASCADE"), index=True)
    object_class: Mapped[str] = mapped_column(String, nullable=False)
    object_id: Mapped[str] = mapped_column(String, nullable=False)
    managed_element_ref: Mapped[str] = mapped_column(String, ForeignKey("managed_entity.managed_element_ref", ondelete="CASCADE"),
                                                      nullable=False, index=True)
    source: Mapped[str] = mapped_column(String, nullable=False)                     # 'registry' or 'walk'
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                           default=lambda: datetime.datetime.now(datetime.UTC))


class ManagedEntity(Base):
    __tablename__ = "managed_entity"

    managed_element_ref: Mapped[str] = mapped_column(String, primary_key=True)
    managed_function_ref: Mapped[str | None] = mapped_column(String)
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    vendor_name: Mapped[str | None] = mapped_column(String)
    o1_protocol: Mapped[str] = mapped_column(String, nullable=False)
    o1_adaptor_endpoint_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("o1_adaptor_endpoint.endpoint_id"))
    # Wave 9 (W9-06, decision D-5): per-cell guard attributes any rApp may
    # query — {cellId: {cellClass, sectorGroup, incidentZone, neighbourRefs}}.
    cell_guards: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


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
    # HISTORY.md §5: standard 3GPP TS 28.532 FaultMnS NotifyNewAlarm
    # fields (per oam's own stndDefined-r16-notify-new-alarm.json VES template)
    # this alarm model was missing entirely.
    probable_cause: Mapped[str | None] = mapped_column(String)
    specific_problem: Mapped[str | None] = mapped_column(String)
    root_cause_indicator: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    correlated_notifications: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid).with_variant(JSON(none_as_null=True), "sqlite"), nullable=False, default=list)
    proposed_repair_actions: Mapped[str | None] = mapped_column(String)
    # HISTORY.md §7: TS28111_FaultNrm.yaml's AlarmRecord requires alarmType
    # (a closed 11-value enum), which this model never had at all — nullable
    # here since not every real caller of /alarms/ingest necessarily knows
    # it, unlike the spec's own readOnly/required framing.
    alarm_type: Mapped[str | None] = mapped_column(String)
    # HISTORY.md §5: no alarm-cleared lifecycle existed at all.
    # The reference's own NotifyClearedAlarm reuses perceivedSeverity=CLEARED
    # rather than a separate state field — this build's `severity` CHECK
    # constraint already allows 'cleared' for exactly this reason, so
    # clearing an alarm sets severity to 'cleared' rather than adding a
    # parallel, redundant lifecycle field.
    cleared_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    clear_user_id: Mapped[str | None] = mapped_column(String)
    # HISTORY.md §7: the spec's AlarmRecord also carries ackUserId (who
    # acknowledged it — PATCH /alarms/{id}/ack never recorded this) and
    # alarmChangedTime (distinct from raised_at/cleared_at — the spec's own
    # "last mutated" timestamp, set whenever ack_state or severity changes).
    ack_user_id: Mapped[str | None] = mapped_column(String)
    changed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))


class MsacIdentity(Base):
    """TS 28.319 Identity. `credential` is write-only: only its hash is kept."""
    __tablename__ = "msac_identity"

    identity_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    identity_type: Mapped[str] = mapped_column(String, nullable=False)
    identity_name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    credential_hash: Mapped[str | None] = mapped_column(String)
    role_list: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # Role ids


class MsacRole(Base):
    __tablename__ = "msac_role"

    role_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    role_name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    access_rules_list: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # AccessRule ids


class MsacAccessRule(Base):
    __tablename__ = "msac_access_rule"

    rule_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    rule_name: Mapped[str] = mapped_column(String, nullable=False)
    data_node_selector: Mapped[str] = mapped_column(String, nullable=False)
    operations: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    actions: Mapped[str] = mapped_column(String, nullable=False)  # ALLOW | DENY
    component_c_data: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class PMFile(Base):
    """A performance data file (TS 28.532 File Data Reporting MnS FileInfo)."""
    __tablename__ = "pm_file"

    file_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    managed_element_ref: Mapped[str] = mapped_column(String, nullable=False)
    counter_type: Mapped[str] = mapped_column(String, nullable=False)
    file_data_type: Mapped[str] = mapped_column(String, nullable=False, default="Performance")
    file_format: Mapped[str] = mapped_column(String, nullable=False, default="json")
    file_compression: Mapped[str | None] = mapped_column(String)
    job_id: Mapped[str | None] = mapped_column(String)
    content: Mapped[str] = mapped_column(String, nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    file_ready_time: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))
    file_expiration_time: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))


class KpiDefinition(Base):
    """MGT-11.1: a KPI as a formula over counters. `counters` is a list of {counter, variable, aggregation}: which PM counter feeds which variable of
    the formula, and how that counter's samples are combined over the period (and over the cells of a group): sum, avg, min, max, last or count."""
    __tablename__ = "kpi_definition"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    formula: Mapped[str] = mapped_column(String, nullable=False)
    counters: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    unit: Mapped[str | None] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(String)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC),
                                                          onupdate=lambda: datetime.datetime.now(datetime.UTC))


class KpiSchedule(Base):
    """MSG-4: publish a KPI to DME on a timer. The worker (`app/tasks.py`) computes `kpi` over the last `lookback_seconds` every `interval_seconds` and
    delivers it as `POST /kpis/{name}/publish` does. `last_*` say what the previous run did; a run that fails is recorded, not retried before the next
    interval, so a KPI that cannot be computed is visible here and is not hammered."""
    __tablename__ = "kpi_schedule"

    schedule_id: Mapped[str] = mapped_column(String, primary_key=True)
    kpi: Mapped[str] = mapped_column(String, nullable=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    lookback_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    group_by: Mapped[str] = mapped_column(String, nullable=False, default="cell")
    managed_element_ref: Mapped[str | None] = mapped_column(String)
    cell_id: Mapped[str | None] = mapped_column(String)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_run_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String)
    last_detail: Mapped[str | None] = mapped_column(String)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC),
                                                          onupdate=lambda: datetime.datetime.now(datetime.UTC))


class RAppLimit(Base):
    """AI-10.1/10.2: what one rApp (by invoker id, its OAuth client id) may do through this module, taken from the `limits` of its manifest and pushed
    here by rApp Management when the instance finishes bootstrapping. `max_config_jobs_per_hour` caps the CM write jobs it may start in any rolling hour."""
    __tablename__ = "rapp_limit"

    invoker_id: Mapped[str] = mapped_column(String, primary_key=True)
    max_config_jobs_per_hour: Mapped[int | None] = mapped_column(Integer)
    # AI-10.3: how many managed elements one job may touch (blast radius), and how far one attribute may move from its current value in one write,
    # in percent of that value (magnitude). NULL: no such limit. At least one of the three is set.
    max_elements_per_job: Mapped[int | None] = mapped_column(Integer)
    max_change_percent: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC),
                                                          onupdate=lambda: datetime.datetime.now(datetime.UTC))


class RAppKill(Base):
    """AI-10.4: an operator stopped this rApp (by invoker id). While a row exists the rApp's config jobs are refused (`RAPP_KILLED`), and a job it
    started that is waiting between waves does not go on. Undoing is not refused: rollbacks and reverts, and halting or aborting a job, still work."""
    __tablename__ = "rapp_kill"

    invoker_id: Mapped[str] = mapped_column(String, primary_key=True)
    reason: Mapped[str | None] = mapped_column(String)
    killed_by: Mapped[str] = mapped_column(String, nullable=False)
    killed_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                          default=lambda: datetime.datetime.now(datetime.UTC))


class SafeguardSubscription(Base):
    """AI-10.6: who is told when the platform refuses an rApp (a kill switch, a rate, blast-radius or magnitude limit): `callback_uri` receives each
    refusal event (through the outbox), narrowed to the codes in `refusals` when that is not empty."""
    __tablename__ = "safeguard_subscription"

    subscription_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    callback_uri: Mapped[str] = mapped_column(String, nullable=False)
    refusals: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                           default=lambda: datetime.datetime.now(datetime.UTC))


class SafeguardRefusal(Base):
    """AI-10.6: one refusal of an rApp by a safeguard, kept whether or not anyone subscribed. `notified` is whether events went out for it: a
    repeat of the same refusal for the same rApp within `SAFEGUARD_EVENT_MIN_INTERVAL_SECONDS` is recorded but not announced again."""
    __tablename__ = "safeguard_refusal"

    refusal_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    occurred_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True,
                                                            default=lambda: datetime.datetime.now(datetime.UTC))
    invoker_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    requested_by: Mapped[str | None] = mapped_column(String)
    code: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[str | None] = mapped_column(String)
    notified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class FileSubscription(Base):
    """A File Data Reporting MnS subscription: notifyFileReady goes to `consumer_reference`."""
    __tablename__ = "file_subscription"

    subscription_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    consumer_reference: Mapped[str] = mapped_column(String, nullable=False)
    file_data_type: Mapped[str | None] = mapped_column(String)  # None = every type
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CMSchemaCache(Base):
    __tablename__ = "cm_schema_cache"

    schema_name: Mapped[str] = mapped_column(String, primary_key=True)
    revision: Mapped[str] = mapped_column(String, primary_key=True, default="")
    location: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False, default="YANG")
    cached_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))
    # Wave 9 (W9-02): the capability descriptor itself — {"classes": {IOC:
    # {attribute: {type, enum?}}}}, generated by scripts/ingest_cm_schema.py.
    descriptor: Mapped[dict | None] = mapped_column(JSON)


class VendorCapability(Base):
    """Wave 9 (W9-01/W9-04) — the per-vendor Capability Registry entry:
    which MnS services the vendor's O1 terminations implement (axis 2), whose
    data model its CM conforms to (axis 3: OWN / SPEC / COMBINED, with the
    vendor's own descriptor and the spec descriptor it is checked against),
    and which O1 transports (vendor modes) it speaks."""
    __tablename__ = "vendor_capability"

    vendor_name: Mapped[str] = mapped_column(String, primary_key=True)
    supported_services: Mapped[list[str]] = mapped_column(ARRAY(String).with_variant(JSON(none_as_null=True), "sqlite"), nullable=False)
    conformance_mode: Mapped[str] = mapped_column(String, nullable=False, default="SPEC")
    supported_vendor_modes: Mapped[list[str]] = mapped_column(ARRAY(String).with_variant(JSON(none_as_null=True), "sqlite"), nullable=False)
    schema_name: Mapped[str | None] = mapped_column(String)
    schema_revision: Mapped[str | None] = mapped_column(String)
    spec_schema_name: Mapped[str | None] = mapped_column(String)
    spec_schema_revision: Mapped[str | None] = mapped_column(String)
    discovery_uri: Mapped[str | None] = mapped_column(String)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))


class WriteConfigJob(Versioned, Base):
    __tablename__ = "write_config_job"

    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    requested_by: Mapped[str] = mapped_column(String, nullable=False)
    scope: Mapped[str] = mapped_column(String, nullable=False)
    schema_validated_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String, nullable=False, default="PENDING")
    conflict_resolution: Mapped[str | None] = mapped_column(String)
    msac_role: Mapped[str | None] = mapped_column(String)
    # MGT-1.6: set on a job that undoes another one. `rollback_forced` is true when the guard (MGT-1.7) found values changed since and the
    # requester went ahead anyway: the audit trail of an override.
    rollback_of: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    rollback_forced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    # MGT-5.1: staged rollout. `wave_size` is how many elements go in one wave (NULL: one wave, the job as before); the waves are made when the
    # job is created, `current_wave` counts the ones that have run, `next_wave_at` is when a paused job may go on, `halted_reason` why a HALTED job
    # stopped (GATE_FAILED, OPERATOR_HALT, WAVE_PAUSE, REVERT_REFUSED). The gate (MGT-5.3) and what happens when it fails (MGT-5.5) are settings too.
    wave_size: Mapped[int | None] = mapped_column(Integer)
    wave_pause_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    wave_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    current_wave: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    gate_max_new_alarms: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    on_gate_failure: Mapped[str] = mapped_column(String, nullable=False, default="halt", server_default="halt")
    next_wave_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    halted_reason: Mapped[str | None] = mapped_column(String)
    halted_detail: Mapped[str | None] = mapped_column(String)
    # AI-10.2: who asked, as R1 Termination vouches for it (the token's client id; NULL for a call that did not come through R1), and when. The
    # per-rApp rate limit counts a caller's jobs from these.
    invoker_id: Mapped[str | None] = mapped_column(String, index=True)
    created_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))


class WriteConfigSubChange(Base):
    __tablename__ = "write_config_sub_change"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("write_config_job.job_id"))
    managed_element_ref: Mapped[str] = mapped_column(String, nullable=False)
    managed_function_ref: Mapped[str | None] = mapped_column(String)
    attribute_changes: Mapped[dict] = mapped_column(JSON, nullable=False)
    # HISTORY.md §7 item 3: TS28532_ProvMnS.yaml defines four distinct MOI
    # lifecycle operations (create/replace/merge/delete) but this sub-change
    # had no operation-type field at all — every write was implicitly a
    # merge. Grounded in RFC 6241 section 7.2's real edit-config `operation`
    # attribute (this build's actually-implemented southbound protocol,
    # netconf_client.py) rather than ProvMnS's HTTP-verb-level framing.
    operation: Mapped[str] = mapped_column(String, nullable=False, default="merge")
    status: Mapped[str] = mapped_column(String, nullable=False, default="PENDING")
    rejection_reason: Mapped[str | None] = mapped_column(String)
    # PR-SB-1.7: what the adaptor said (its <rpc-error>: tag, path, message), bounded; the reason above stays the stable code
    rejection_detail: Mapped[str | None] = mapped_column(String)
    # Wave 10.1 (W10-19): edit-config attempts made, retries included
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # MGT-5.2: the request order of the sub-change and the wave it belongs to (1 for a job without waves)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    wave: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class PMSubscription(Base):
    __tablename__ = "pm_subscription"

    subscription_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    managed_element_ref: Mapped[str] = mapped_column(String, ForeignKey("managed_entity.managed_element_ref"))
    counter_type: Mapped[str] = mapped_column(String, nullable=False)
    delivery_method: Mapped[str] = mapped_column(String, nullable=False)
    southbound_engine: Mapped[str] = mapped_column(String, nullable=False)
    # HISTORY.md §7 item 4 (formerly 7): TS28550_PerfMeasJobCtrlMnS.yaml's
    # measJobCreation-RequestType carries a granularityPeriod (the sampling
    # interval, in seconds) alongside reportingPeriod/schedule/priority —
    # subscribe_pm's own docstring already confirms most of that job-control
    # shape is a deliberate scope cut (this is a DME-producer registration
    # wrapper, not a real clause-8 PM job), but granularityPeriod is needed
    # by any real PM subscription regardless of wrapper shape, and was
    # fully absent. Nullable: optional in the real spec too.
    granularity_period: Mapped[int | None] = mapped_column(Integer)


class FMSubscription(Base):
    __tablename__ = "fm_subscription"

    # HISTORY.md OI-6.7: unlike PM (subscribe_pm registers RAN NF
    # OAM as a DME producer for PMCounters.{counter_type}), FM/alarms had
    # no DME producer registration at all — an rApp/AI-ML model wanting
    # outstanding-active-alarm/alarm-history context had no DME-mediated
    # way to get it. This mirrors PMSubscription's own shape; alarm
    # clearing itself is unaffected (stays RAN NF OAM's own
    # PATCH /alarms/{id}/clear, never DME's or a consuming rApp's call).
    subscription_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    managed_element_ref: Mapped[str] = mapped_column(String, ForeignKey("managed_entity.managed_element_ref"))
    delivery_method: Mapped[str] = mapped_column(String, nullable=False)
    southbound_engine: Mapped[str] = mapped_column(String, nullable=False)


class SoftwareManagementJob(Versioned, Base):
    __tablename__ = "software_management_job"

    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    managed_element_ref: Mapped[str] = mapped_column(String, ForeignKey("managed_entity.managed_element_ref"))
    ru_instance_id: Mapped[str | None] = mapped_column(String)  # reserved, section 3.4
    phase: Mapped[str] = mapped_column(String, nullable=False, default="DOWNLOAD")
    status: Mapped[str] = mapped_column(String, nullable=False, default="PENDING")


class CMSnapshot(Base):
    """MGT-1.1: what one dispatched sub-change replaced and wrote. `before` holds the current values of the attributes the change
    names (the whole object's attributes for a delete/remove), read from the NF just before the write; NULL with `before_error`
    when that read failed. `after` is what the NF acknowledged: the written values, NULL when the change was not applied or
    removed the object. One row per dispatched sub-change; removing the sub-change removes it."""
    __tablename__ = "cm_snapshot"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    sub_change_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("write_config_sub_change.id", ondelete="CASCADE"), nullable=False, unique=True)
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("write_config_job.job_id", ondelete="CASCADE"), nullable=False)
    managed_element_ref: Mapped[str] = mapped_column(String, nullable=False)
    managed_function_ref: Mapped[str | None] = mapped_column(String)
    operation: Mapped[str] = mapped_column(String, nullable=False)
    before: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    after: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    before_error: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                          default=lambda: datetime.datetime.now(datetime.UTC))
