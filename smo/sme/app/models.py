import datetime
import uuid

from sqlalchemy import ARRAY, Boolean, DateTime, ForeignKey, JSON, String, UniqueConstraint, Uuid
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


class InvokerRegistration(Base):
    """OPEN_ITEMS.md section 2: the reference's own API Invoker onboarding
    (`invokermanagement.go`'s `InvokerRegister`) — a real prerequisite for
    the Security/token API (`securityservice.go`'s
    `PostSecuritiesSecurityIdToken`, gated on
    `IsInvokerRegistered`/`VerifyInvokerSecret`), which this build never
    modeled at all.

    SPEC_AUDIT.md SME item 1: the real CAPIF core's
    `APIInvokerEnrolmentDetails`/`OnboardingInformation` onboarding is
    public-key-based — the client supplies `apiInvokerPublicKey`
    (`public_key` here); the server *generates* both `api_invoker_id`
    and the onboarding secret and hands them back
    (`invokermanagementapi/typeupdate.go`'s `createId`/
    `getOnboardingSecret`). This build previously took both as
    client-supplied input instead — a self-asserted identity and a
    client-chosen secret, the weaker trust direction the real spec
    documents `apiInvokerId` as "shall not be present" in the client's
    own request. Flipped to match. `public_key` is stored but not yet
    cryptographically used anywhere (no signature verification exists
    in this build); `onboarding_secret_hash` is still a genuine, checked
    secret at token-issuance time, never stored in cleartext — only a
    salted `scrypt` hash (`salt:digest` hex), so a DB leak (backup, SQL
    injection elsewhere, a dump) can't hand out reusable client
    credentials directly.
    """
    __tablename__ = "invoker_registration"

    api_invoker_id: Mapped[str] = mapped_column(String, primary_key=True)
    public_key: Mapped[str] = mapped_column(String, nullable=False)
    onboarding_secret_hash: Mapped[str] = mapped_column(String, nullable=False)


class IssuedAccessToken(Base):
    """OPEN_ITEMS.md section 2: "No real OAuth2/token enforcement at R1
    Termination — only a comment and a tokenEndPoint URI in the bootstrap
    response; no actual validation code path." The reference's own
    AccessTokenRsp is a real signed JWT (`keycloak.GetToken`, an external
    IdP this build doesn't run — the same no-real-southbound-integration
    elision as everywhere else); this is the honest, opaque-token
    substitute: a real, server-tracked bearer token with a real expiry,
    validated by R1 Termination via POST /oauth2/introspect (RFC 7662) on
    every proxied request rather than by self-contained signature
    verification. Security review: the raw token is returned to the
    caller once and never stored — only its SHA-256 hash
    (`access_token_hash`), so a DB leak can't hand out live, reusable
    bearer tokens directly (unlike `onboarding_secret_hash`, a `scrypt`
    KDF isn't needed here: the token is already 256 bits of real
    randomness from `secrets.token_urlsafe`, not a low-entropy
    human-chosen secret).
    """
    __tablename__ = "issued_access_token"

    access_token_hash: Mapped[str] = mapped_column(String, primary_key=True)
    api_invoker_id: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ServiceEventSubscription(Base):
    __tablename__ = "service_event_subscription"

    subscription_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subscriber_id: Mapped[str] = mapped_column(String, nullable=False)
    event_types: Mapped[list[str]] = mapped_column(ARRAY(String).with_variant(JSON(none_as_null=True), "sqlite"), nullable=False)
    callback_uri: Mapped[str] = mapped_column(String, nullable=False)
    api_ids: Mapped[list[str] | None] = mapped_column(ARRAY(String).with_variant(JSON(none_as_null=True), "sqlite"))  # NEW section 5: CAPIFEventFilter.apiIds


EVENT_TYPES = {"SERVICE_API_AVAILABLE", "SERVICE_API_UNAVAILABLE", "SERVICE_API_UPDATE"}
