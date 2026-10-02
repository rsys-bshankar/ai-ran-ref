import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from smo_shared.db import Base
from smo_shared.versioning import Versioned


class RAppInstance(Versioned, Base):
    __tablename__ = "rapp_instance"

    instance_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Cross-module reference: enforced by the FK in migrations/001_init.sql, not
    # declared as an ORM ForeignKey — this module runs in its own process, where
    # the other module's table isn't in the metadata and an ORM FK can't resolve
    # (NoReferencedTableError on flush). tests_integration/test_module_isolation.py.
    package_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # -> application_package (Onboarding)
    state: Mapped[str] = mapped_column(String, nullable=False, default="DEPLOYING")
    configuration: Mapped[dict | None] = mapped_column(JSON)
    workload_ref: Mapped[str | None] = mapped_column(String)
    oauth_client_id: Mapped[str | None] = mapped_column(String)  # == rAppId (identity.py); None once revoked
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))
    upgrade_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)  # confirmed default, LLD section 6 (HISTORY.md §1)
    pending_upgrade_instance_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # links old row to its in-flight replacement
    package_usage_registration_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # -> package_usage_registration (Onboarding), cross-module like package_id
    # HISTORY.md §7's Onboarding/rApp Mgmt finding 3 (SME auto-registration):
    # SME serviceId(s) this instance registered at bootstrap-complete from
    # the package's own CSAR-bundled Files/Sme/serviceapis/ declarations —
    # None if the package declared none, or before bootstrap-complete ran.
    # Deregistered (best-effort) on TERMINATE/CRASH, same as oauth_client_id
    # is used as this instance's own SME apfId throughout.
    sme_service_ids: Mapped[list[str] | None] = mapped_column(JSON)
    # HISTORY.md OI-6.3 — rApp Autonomy Modes: a per-instance
    # property fixed at onboarding (CreateInstance), not something chosen
    # per-inference-call. Defaults to SHADOW — the safest, no-enforcement
    # mode — for every existing caller that doesn't declare one, the same
    # permissive-by-default shape optional fields already use throughout
    # this build (e.g. TrainingJob.dmeDataJobIds). region_scope is
    # AUTONOMOUS's own pre-configured RAN node/cell/slice scope — opaque
    # JSON, same shape as configuration above; meaningless for
    # ASSIST (operator decides scope per-dispatch) and SHADOW (nothing is
    # ever enforced).
    autonomy_mode: Mapped[str] = mapped_column(String, nullable=False, default="SHADOW")
    region_scope: Mapped[dict | None] = mapped_column(JSON)
    # OI-2-terminate-workload / OI-2-upgrade-completeness: the outcome of
    # the most recent best-effort workload teardown this row performed or
    # inherited — its own TERMINATE, the old row's teardown on an upgrade
    # commit (recorded on the replacement), or the replacement's teardown on
    # a rollback/timeout (recorded on the old row). Shape:
    # {instanceId, reason, nfoTerminate, usageStop, at}; each step is DONE,
    # SKIPPED (nothing to release) or FAILED: <why>. None until a teardown ran.
    last_teardown: Mapped[dict | None] = mapped_column(JSON)
    # OI-1-sa-rollback: set on a replacement provisioned by a rollback, naming
    # the UPGRADE version it undoes; resolve_upgrade's commit marks that
    # version rolled back. None for an ordinary upgrade's replacement.
    rollback_of_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class RAppInstanceVersion(Base):
    """OI-1-sa-rollback: one row per committed upgrade or rollback — the
    version history an upgrade commit used to throw away with the old row.

    Instance ids are bare references (no FK): the retired row is deleted on
    commit, and the history must outlive it. The snapshot is what the
    retired instance ran — package, configuration, autonomy mode, region
    scope — so a rollback can re-provision exactly that. Each commit also
    chains the lineage: `previous_instance_id` -> `instance_id`, which is how
    a caller holding a superseded instance id (an SA SMOS monitor
    registered before the upgrade) finds the current one.
    """
    __tablename__ = "rapp_instance_version"

    version_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    instance_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)           # the row that became current
    previous_instance_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)  # the row it retired
    package_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    previous_package_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    previous_configuration: Mapped[dict | None] = mapped_column(JSON)
    previous_autonomy_mode: Mapped[str] = mapped_column(String, nullable=False)
    previous_region_scope: Mapped[dict | None] = mapped_column(JSON)
    kind: Mapped[str] = mapped_column(String, nullable=False)  # UPGRADE | ROLLBACK
    # UPGRADE rows only: the ROLLBACK version that undid this one. A rolled-back
    # upgrade is skipped when looking for the next version to roll back to.
    rolled_back_by_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    committed_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                            default=lambda: datetime.datetime.now(datetime.UTC))


class RAppFaultReport(Base):
    __tablename__ = "rapp_fault_report"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    instance_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("rapp_instance.instance_id", ondelete="CASCADE"))  # NEW section 5: delete_instance's cascade
    severity: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String)
    # NEW (GUI pass): GET /instances/{id}/faults returns these newest-first;
    # with no timestamp there was no stable order to return them in at all.
    reported_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.UTC))


class RAppPerformanceReport(Base):
    __tablename__ = "rapp_performance_report"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    instance_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("rapp_instance.instance_id", ondelete="CASCADE"))  # NEW section 5: delete_instance's cascade
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False)
    reported_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.UTC))  # NEW (GUI pass), same reason as RAppFaultReport's
