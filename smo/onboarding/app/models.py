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
    # Wave 1 rApp packaging extension: the optional AI Platform capability
    # declaration read from the CSAR's root-level manifest.yaml/
    # capabilities.yaml (docs/architecture/AI_PLATFORM_BASELINE.md) — which
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
