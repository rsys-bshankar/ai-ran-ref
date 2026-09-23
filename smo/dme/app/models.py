import uuid

from sqlalchemy import ARRAY, ForeignKey, JSON, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from smo_shared.db import Base

DELIVERY_METHODS = {"PULL_HTTP", "PUSH_HTTP", "STREAMING_KAFKA"}  # R1AP's exact wire values, Foundational LLD section 3.2


class DMEType(Base):
    __tablename__ = "dme_type"
    __table_args__ = (UniqueConstraint("namespace", "name", "version"),)

    dme_type_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    namespace: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False)
    type_name: Mapped[str] = mapped_column(String, nullable=False)
    producer_id: Mapped[str] = mapped_column(String, nullable=False)
    data_production_schema: Mapped[dict] = mapped_column(JSON, nullable=False)
    collection_spec: Mapped[dict | None] = mapped_column(JSON)
    producer_health_callback_url: Mapped[str] = mapped_column(String, nullable=False)

    @property
    def dme_type_id_struct(self) -> dict:
        """Computed at read time — R1AP's actual wire identity (Annex B.4),
        derived from our internal UUID PK. Foundational Platform LLD section 3.1.
        """
        return {"namespace": self.namespace, "name": self.name, "version": self.version}


class DMEDeliverySchema(Base):
    __tablename__ = "dme_delivery_schema"

    delivery_schema_id: Mapped[str] = mapped_column(String, primary_key=True)
    dme_type_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("dme_type.dme_type_id", ondelete="CASCADE"))
    schema_type: Mapped[str] = mapped_column(String, nullable=False)
    schema: Mapped[dict] = mapped_column(JSON, nullable=False)


class DataJob(Base):
    __tablename__ = "data_job"

    data_job_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    data_delivery_mode: Mapped[str] = mapped_column(String, nullable=False)
    dme_type_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("dme_type.dme_type_id"))
    production_job_definition: Mapped[dict | None] = mapped_column(JSON)
    data_delivery_method: Mapped[str] = mapped_column(String, nullable=False)
    delivery_details: Mapped[dict | None] = mapped_column(JSON)
    consumer_id: Mapped[str] = mapped_column(String, nullable=False)  # rAppId, or 'DME_FRAMEWORK' (section 3.7)
    status: Mapped[str] = mapped_column(String, nullable=False, default="PENDING")


class DataOffer(Base):
    __tablename__ = "data_offer"

    offer_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    dme_type_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("dme_type.dme_type_id"))
    data_delivery_methods_offered: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    data_delivery_method_committed: Mapped[str | None] = mapped_column(String)
    data_availability_notification_uri: Mapped[str | None] = mapped_column(String)  # REVERSED direction, section 3.5
    data_offer_termination_notification_uri: Mapped[str] = mapped_column(String, nullable=False)
