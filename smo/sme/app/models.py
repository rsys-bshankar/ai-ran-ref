import uuid

from sqlalchemy import ARRAY, Boolean, ForeignKey, JSON, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from smo_shared.db import Base


class ServiceProfile(Base):
    __tablename__ = "service_profile"
    # UNIQUE on service_name ALONE, not (service_name, producer_id) — Foundational
    # Platform LLD section 2.3's actual decision is that a DIFFERENT producer
    # registering the same serviceName is the conflict; the PAIR being unique
    # would instead only catch the same producer double-registering, which is
    # supposed to be an idempotent update-in-place, not blocked at all. Caught
    # by sme/tests/test_main.py's conflict test — the original migration had
    # this backwards relative to the LLD's own stated rule.
    __table_args__ = (UniqueConstraint("service_name"),)

    service_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    service_name: Mapped[str] = mapped_column(String, nullable=False)
    producer_id: Mapped[str] = mapped_column(String, nullable=False)
    endpoint: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False)
    full_api_versions: Mapped[list[str] | None] = mapped_column(ARRAY(String).with_variant(JSON(none_as_null=True), "sqlite"))
    service_capabilities: Mapped[dict | None] = mapped_column(JSON)
    selection_criteria: Mapped[dict | None] = mapped_column(JSON)
    module_scope: Mapped[str] = mapped_column(String, nullable=False)
    # NEW section 5: the reference's own ServiceAPIDescription carries these
    # three — ServiceProfile was flattened, missing all of them. Stored as
    # JSON rather than normalized child tables (aefProfiles is registered
    # and read back wholesale, never independently CRUD'd — same adaptation
    # as DMEType.collection_spec/TrainingJob.required_data elsewhere in this
    # build). Only the fields discover_services' own new filters need are
    # kept (aefId, protocol, dataFormat, versions[].resources[].commType) —
    # not the full CAPIF AefProfile/Resource schema (aefLocation,
    # domainName, interfaceDescriptions, custOperations, etc.).
    aef_profiles: Mapped[list[dict] | None] = mapped_column(JSON)
    api_supp_feats: Mapped[str | None] = mapped_column(String)
    shareable_info: Mapped[dict | None] = mapped_column(JSON)

    authz_policy: Mapped["ServiceAuthzPolicy"] = relationship(back_populates="service", uselist=False, cascade="all, delete-orphan")


class ServiceAuthzPolicy(Base):
    __tablename__ = "service_authz_policy"

    service_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("service_profile.service_id", ondelete="CASCADE"), primary_key=True
    )
    allowed_consumers: Mapped[list[str] | None] = mapped_column(ARRAY(String).with_variant(JSON(none_as_null=True), "sqlite"))
    gates_discovery_visibility: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    service: Mapped["ServiceProfile"] = relationship(back_populates="authz_policy")


class ProviderRegistration(Base):
    """OPEN_ITEMS.md section 5: the reference's own Provider (APF) enrolment
    (`providermanagement.go`'s `ProviderManager`, `POST`/`DELETE
    /registrations`) — the real registry `register_service`'s own
    `IsPublishingFunctionRegistered` gate needs, which this build never
    modeled at all before this pass. `apf_id` is this build's own
    flattened identity (`apfId == producerId == rAppId`, already
    established by `register_service`'s own docstring) — no separate
    provider-domain-id/function-id split, since nothing else in this
    build tracks that CAPIF three-tier (domain -> APF/AEF/AMF functions)
    hierarchy either.
    """
    __tablename__ = "provider_registration"

    apf_id: Mapped[str] = mapped_column(String, primary_key=True)
    provider_domain_info: Mapped[str | None] = mapped_column(String)


class ServiceEventSubscription(Base):
    __tablename__ = "service_event_subscription"

    subscription_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subscriber_id: Mapped[str] = mapped_column(String, nullable=False)
    event_types: Mapped[list[str]] = mapped_column(ARRAY(String).with_variant(JSON(none_as_null=True), "sqlite"), nullable=False)
    callback_uri: Mapped[str] = mapped_column(String, nullable=False)
    api_ids: Mapped[list[str] | None] = mapped_column(ARRAY(String).with_variant(JSON(none_as_null=True), "sqlite"))  # NEW section 5: CAPIFEventFilter.apiIds


EVENT_TYPES = {"SERVICE_API_AVAILABLE", "SERVICE_API_UNAVAILABLE", "SERVICE_API_UPDATE"}
