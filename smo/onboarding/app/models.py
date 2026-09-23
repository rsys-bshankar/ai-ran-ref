import uuid

import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Uuid
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
    # NFO+FOCOM LLD section 2: populated by NFO's CreateDescriptor once
    # OnboardPackage's own validation succeeds — the actual fix for the
    # gap where rApp Management used to pass packageId where NFO expected
    # a real nfDeploymentDescriptorId. use_alter=True: nf_deployment_descriptor
    # itself has a FK back to application_package (which package it was
    # derived from), so this pair is a genuine mutual reference — same as
    # migrations/001_init.sql adding this column via ALTER TABLE after
    # nf_deployment_descriptor exists, this tells SQLAlchemy to emit the
    # constraint the same way rather than inline, breaking the cycle for
    # dependency-sort purposes (create_all/drop_all ordering).
    nf_deployment_descriptor_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("nf_deployment_descriptor.nf_deployment_descriptor_id", use_alter=True, name="fk_application_package_nf_deployment_descriptor_id")
    )


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
