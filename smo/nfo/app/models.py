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
    __tablename__ = "nf_deployment"

    nf_deployment_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    nf_deployment_descriptor_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("nf_deployment_descriptor.nf_deployment_descriptor_id"))
    cluster_id: Mapped[str] = mapped_column(String, nullable=False)  # degenerate single value, Phase 1
    state: Mapped[str] = mapped_column(String, nullable=False, default="INSTANTIATING")
    workload_ref: Mapped[str | None] = mapped_column(String)
    required_resource_type_id: Mapped[str | None] = mapped_column(String)
    config_secrets: Mapped[dict | None] = mapped_column(JSON)  # reference to secrets store only, never plaintext


class LCMOperation(Base):
    __tablename__ = "lcm_operation"

    operation_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    nf_deployment_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("nf_deployment.nf_deployment_id"))
    operation_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="PENDING")
