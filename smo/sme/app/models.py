import uuid

from sqlalchemy import ARRAY, Boolean, ForeignKey, JSON, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from smo_shared.db import Base


class ServiceProfile(Base):
    __tablename__ = "service_profile"
    __table_args__ = (UniqueConstraint("service_name", "producer_id"),)

    service_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    service_name: Mapped[str] = mapped_column(String, nullable=False)
    producer_id: Mapped[str] = mapped_column(String, nullable=False)
    endpoint: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False)
    full_api_versions: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    service_capabilities: Mapped[dict | None] = mapped_column(JSON)
    selection_criteria: Mapped[dict | None] = mapped_column(JSON)
    module_scope: Mapped[str] = mapped_column(String, nullable=False)

    authz_policy: Mapped["ServiceAuthzPolicy"] = relationship(back_populates="service", uselist=False)


class ServiceAuthzPolicy(Base):
    __tablename__ = "service_authz_policy"

    service_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("service_profile.service_id", ondelete="CASCADE"), primary_key=True
    )
    allowed_consumers: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    gates_discovery_visibility: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    service: Mapped["ServiceProfile"] = relationship(back_populates="authz_policy")


class ServiceEventSubscription(Base):
    __tablename__ = "service_event_subscription"

    subscription_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subscriber_id: Mapped[str] = mapped_column(String, nullable=False)
    event_types: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    callback_uri: Mapped[str] = mapped_column(String, nullable=False)


EVENT_TYPES = {"SERVICE_API_AVAILABLE", "SERVICE_API_UNAVAILABLE", "SERVICE_API_UPDATE"}
