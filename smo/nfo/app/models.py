import uuid

from sqlalchemy import ForeignKey, JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from smo_shared.db import Base


class NFDeploymentDescriptor(Base):
    __tablename__ = "nf_deployment_descriptor"

    nf_deployment_descriptor_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    package_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("application_package.package_id"))
    name: Mapped[str] = mapped_column(String, nullable=False)
    required_resource_type_id: Mapped[str | None] = mapped_column(String)
    workload_template: Mapped[dict] = mapped_column(JSON, nullable=False)


class NFDeployment(Base):
    """OPEN_ITEMS.md section 5: `name` didn't exist at all — the
    reference's own duplication guard (`_check_duplication`,
    dms_lcm_nfdeployment.py) rejects a second NfDeployment with the same
    name or targeting the same descriptorId, which this build couldn't
    even express without a real name column.
    """
    __tablename__ = "nf_deployment"

    nf_deployment_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    nf_deployment_descriptor_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("nf_deployment_descriptor.nf_deployment_descriptor_id"))
    name: Mapped[str] = mapped_column(String, nullable=False)
    cluster_id: Mapped[str] = mapped_column(String, nullable=False)  # degenerate single value, Phase 1
    state: Mapped[str] = mapped_column(String, nullable=False, default="INITIAL")
    workload_ref: Mapped[str | None] = mapped_column(String)
    required_resource_type_id: Mapped[str | None] = mapped_column(String)
    config_secrets: Mapped[dict | None] = mapped_column(JSON)  # reference to secrets store only, never plaintext


class NFOCloudResource(Base):
    """The reference's own NfOCloudVResource (o2dms/domain/dms.py) — the
    resource-linkage object OPEN_ITEMS.md section 5 flagged as entirely
    missing. Real per-vResource granularity (CPU/RAM/interface-level
    linkage) needs actual K8s pod introspection, out of scope same as
    elsewhere; resource_ref is the clusterId Instantiate already
    resolves via FOCOM's inventory — the real granularity this module
    tracks today.
    """
    __tablename__ = "nf_ocloud_resource"

    resource_link_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    nf_deployment_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("nf_deployment.nf_deployment_id"), nullable=False)
    resource_ref: Mapped[str] = mapped_column(String, nullable=False)
    vresource_type: Mapped[str] = mapped_column(String, nullable=False, default="COMPUTE")


class LCMOperation(Base):
    __tablename__ = "lcm_operation"

    operation_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    nf_deployment_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("nf_deployment.nf_deployment_id"))
    operation_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="PENDING")
