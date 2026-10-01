import uuid

import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from smo_shared.db import Base

PACKAGE_STATES = {"ONBOARDING", "AVAILABLE", "DEPRECATED", "DELETING", "FAILED"}


class ApplicationPackage(Base):
    __tablename__ = "application_package"

    package_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    application_type: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    vendor: Mapped[str | None] = mapped_column(String)
    version: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False, default="ONBOARDING")
    parent_package_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("application_package.package_id"))
    manifest_ref: Mapped[str] = mapped_column(String, nullable=False)
    tosca_entry_definitions: Mapped[str | None] = mapped_column(String)
    signature_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    integrity_hash: Mapped[str | None] = mapped_column(String)
    # Real O-RAN SC rApp Manager ASD schema (`asd_types.yaml`'s
    # `tosca.nodes.asd` node type, grounded against the real
    # `nonrtric-plt-rappmanager` sample CSARs, not a summary) — all four
    # required alongside application_name/application_version/provider,
    # never captured before this pass. Package identity/uniqueness stays
    # on integrity_hash (unchanged, lower-risk than switching to
    # descriptor_id) — these are surfaced for real spec fidelity, not
    # used as a key.
    descriptor_id: Mapped[str | None] = mapped_column(String)
    descriptor_invariant_id: Mapped[str | None] = mapped_column(String)
    descriptor_version: Mapped[str | None] = mapped_column(String)
    schema_version: Mapped[str | None] = mapped_column(String)
    # HISTORY.md §7's Onboarding/rApp Mgmt finding 3 (SME auto-registration):
    # the CSAR's own Files/Sme/providers/*.json + Files/Sme/serviceapis/*.json
    # (real CAPIF APIProviderEnrolmentDetails/ServiceAPIDescription content),
    # read once at onboarding time and registered per-instance at
    # rapp-mgmt's bootstrap-complete (see rapp-mgmt/app/main.py). None for a
    # package whose CSAR declares neither directory.
    sme_declarations: Mapped[dict | None] = mapped_column(JSON)
    # Wave 1 rApp packaging extension: the optional AI Platform capability
    # declaration read from the CSAR's root-level manifest.yaml/
    # capabilities.yaml (docs/ARCHITECTURE.md) — which
    # of sdk/'s six namespaces (data/analytics/models/lifecycle/intent/
    # platform) this rApp consumes or provides. None for any package built
    # before this extension, or one that simply omits both files.
    ai_capabilities: Mapped[dict | None] = mapped_column(JSON)
    # NFO+FOCOM LLD section 2: populated by NFO's CreateDescriptor once
    # OnboardPackage's own validation succeeds — the actual fix for the
    # gap where rApp Management used to pass packageId where NFO expected
    # a real nfDeploymentDescriptorId. nf_deployment_descriptor is NFO's table,
    # and it references application_package back — a genuine mutual reference,
    # which migrations/001_init.sql closes by adding this column's FK via ALTER
    # TABLE once both tables exist.
    # Cross-module reference: enforced by the FK in migrations/001_init.sql, not
    # declared as an ORM ForeignKey — this module runs in its own process, where
    # the other module's table isn't in the metadata and an ORM FK can't resolve
    # (NoReferencedTableError on flush). tests_integration/test_module_isolation.py.
    nf_deployment_descriptor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # -> nf_deployment_descriptor (NFO)


class Artifact(Base):
    __tablename__ = "artifact"

    artifact_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    package_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("application_package.package_id", ondelete="CASCADE"))
    path: Mapped[str] = mapped_column(String, nullable=False)
    access_url: Mapped[str] = mapped_column(String, nullable=False)


class PackageUsageRegistration(Base):
    __tablename__ = "package_usage_registration"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    package_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("application_package.package_id"))
    consumer_id: Mapped[str] = mapped_column(String, nullable=False)
    stopped_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
