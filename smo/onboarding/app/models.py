"""The ORM tables of the Onboarding module: the application package, its artifacts and its usage registrations.

What it is: SQLAlchemy models over `application_package`, `artifact` and `package_usage_registration`; the schema itself is the Alembic history in `migrations/`
(the model and the revision change together, `scripts/check_migration_matches_models.py` compares them).

Where it sits: read and written by `app/main.py` and by the guards in `app/statemachine.py`. `ApplicationPackage` is versioned (`smo_shared.versioning.Versioned`),
so a stale write is a 409 (PR-ST-2).

What it does not own: NFO's `nf_deployment_descriptor` table, which references `application_package` back (see the note on `nf_deployment_descriptor_id`).
"""

import uuid

import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from smo_shared.db import Base
from smo_shared.versioning import Versioned

# Five of the eight PackageState values (no PRIMING, PRIMED or DEPRIMING); no code reads this set. The state machine's `PackageState` is the list that matters.
PACKAGE_STATES = {"ONBOARDING", "AVAILABLE", "DEPRECATED", "DELETING", "FAILED"}


class ApplicationPackage(Versioned, Base):
    """One onboarded rApp (or other application) package: identity read from its descriptor, lifecycle state, and the declarations read from its CSAR.

    `name`, `version` and `vendor` hold placeholders until the descriptor is read. `integrity_hash` (SHA-256 of the package bytes) is the identity used for duplicate
    detection, not `descriptor_id`. `state` is a PackageState value. `parent_package_id` points at another package and is read by the delete guard; no route sets it.
    `manifest_ref` is the location the package was fetched from.
    """
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
    # The four descriptor fields of the real O-RAN SC rApp Manager ASD schema (`asd_types.yaml`'s `tosca.nodes.asd` node type, checked against the real
    # `nonrtric-plt-rappmanager` sample CSARs); the schema requires them next to application_name, application_version and provider. Package
    # identity and uniqueness stay on integrity_hash: these are stored for spec fidelity and are not used as a key.
    descriptor_id: Mapped[str | None] = mapped_column(String)
    descriptor_invariant_id: Mapped[str | None] = mapped_column(String)
    descriptor_version: Mapped[str | None] = mapped_column(String)
    schema_version: Mapped[str | None] = mapped_column(String)
    # HISTORY.md §7's Onboarding/rApp Mgmt finding 3 (SME auto-registration): the CSAR's own Files/Sme/providers/*.json and Files/Sme/serviceapis/*.json
    # (real CAPIF APIProviderEnrolmentDetails / ServiceAPIDescription content), read once at onboarding and registered per instance at rapp-mgmt's
    # bootstrap-complete (see rapp-mgmt/app/main.py). None for a package whose CSAR has neither directory.
    sme_declarations: Mapped[dict | None] = mapped_column(JSON)
    # The optional AI Platform capability declaration read from the CSAR's root-level manifest.yaml / capabilities.yaml (docs/ARCHITECTURE.md): which of
    # sdk/'s six namespaces (data/analytics/models/lifecycle/intent/platform) the rApp consumes or provides, plus the runtime profiles, limits and operator
    # page of the manifest. None for a package that has neither file.
    ai_capabilities: Mapped[dict | None] = mapped_column(JSON)
    # NFO+FOCOM LLD section 2: set from NFO's CreateDescriptor answer once the package's validation succeeds, so rApp Management deploys with a real
    # nfDeploymentDescriptorId and not the packageId. nf_deployment_descriptor is NFO's table, and it references application_package back: a genuine mutual
    # reference, which migrations/001_init.sql closes by adding this column's FK via ALTER TABLE once both tables exist.
    # Cross-module reference: enforced by the FK in migrations/001_init.sql, not
    # declared as an ORM ForeignKey — this module runs in its own process, where
    # the other module's table isn't in the metadata and an ORM FK can't resolve
    # (NoReferencedTableError on flush). tests_integration/test_module_isolation.py.
    nf_deployment_descriptor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # -> nf_deployment_descriptor (NFO)


class Artifact(Base):
    """One file inside a package's `Artifacts/` directory: `path` within the CSAR and `access_url` (`<package location>#<path>`).

    Only the reference is stored, not the bytes. Rows are deleted with their package (ondelete CASCADE).
    """
    __tablename__ = "artifact"

    artifact_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    package_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("application_package.package_id", ondelete="CASCADE"))
    path: Mapped[str] = mapped_column(String, nullable=False)
    access_url: Mapped[str] = mapped_column(String, nullable=False)


class PackageUsageRegistration(Base):
    """One consumer (an rApp instance id) using a package.

    A row with `stopped_at` null is open and blocks DEPRIME and DELETE of the package (`statemachine._no_active_instances`, `_no_blocking_dependents`). rApp Management
    opens it on CreateInstance and stops it on TerminateInstance.
    """
    __tablename__ = "package_usage_registration"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    package_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("application_package.package_id"))
    consumer_id: Mapped[str] = mapped_column(String, nullable=False)
    stopped_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
