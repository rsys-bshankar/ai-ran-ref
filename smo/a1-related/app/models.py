import datetime
import uuid

from sqlalchemy import ARRAY, CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from smo_shared.db import Base


class A1Policy(Base):
    __tablename__ = "a1_policy"

    policy_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # The Near-RT RIC's OWN internal identifier for this policy — NOT the
    # same UUID as policy_id. A1 Related generates policy_id as the
    # R1-facing identifier (section 1.1); a real Near-RT RIC generates its
    # own identifier for the same policy independently. Caught by the
    # cross-service integration suite: update/delete/status calls were
    # using policy_id against the mock, which had no record under that ID
    # at all — SUSPENDED ("unknown policyId") on every status query.
    near_rt_ric_policy_id: Mapped[str | None] = mapped_column(String)
    policy_type_id: Mapped[str] = mapped_column(String, nullable=False)
    creator_id: Mapped[str] = mapped_column(String, nullable=False)  # == rAppId
    near_rt_ric_id: Mapped[str] = mapped_column(String, nullable=False)
    policy_object: Mapped[dict] = mapped_column(JSON, nullable=False)  # opaque, A1TD-owned — never interpreted
    enforcement_status: Mapped[str] = mapped_column(String, nullable=False, default="PENDING")  # LOCAL MIRROR ONLY, section 1.1
    rejection_reason: Mapped[str | None] = mapped_column(String)


class PolicyStatusSubscription(Base):
    __tablename__ = "policy_status_subscription"
    __table_args__ = (CheckConstraint("NOT (subscription_scope IS NOT NULL AND policy_id_list IS NOT NULL)"),)

    subscription_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    notification_destination: Mapped[str] = mapped_column(String, nullable=False)
    subscription_scope: Mapped[str | None] = mapped_column(String)  # OWN | OTHERS | ALL
    policy_id_list: Mapped[list[str] | None] = mapped_column(ARRAY(String).with_variant(JSON(none_as_null=True), "sqlite"))
    policy_type_id_list: Mapped[list[str] | None] = mapped_column(ARRAY(String).with_variant(JSON(none_as_null=True), "sqlite"))
    near_rt_ric_id_list: Mapped[list[str] | None] = mapped_column(ARRAY(String).with_variant(JSON(none_as_null=True), "sqlite"))


class A1EIType(Base):
    __tablename__ = "a1_ei_type"

    ei_type_id: Mapped[str] = mapped_column(String, primary_key=True)
    registered_by: Mapped[str] = mapped_column(String, nullable=False)
    ei_source_dme_type_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)


class A1ServiceRegistration(Base):
    """OPEN_ITEMS.md section 5: the reference's own Service Registry and
    Supervision (`pms-api-v3.json`'s `/services`/`ServiceRegistrationInfo`/
    `ServiceStatus`) — service_id is caller-supplied (the reference's own
    `serviceId` is required, never server-generated), matching creator_id's
    own identity space on A1Policy (a service here is the same rApp/
    consumer identity that creates policies).
    """
    __tablename__ = "a1_service_registration"

    service_id: Mapped[str] = mapped_column(String, primary_key=True)
    callback_url: Mapped[str | None] = mapped_column(String)
    keep_alive_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 0 == supervision disabled, per the reference's own schema
    last_activity_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))

# A1TrainingCapability: DORMANT (A1 Related LLD section 0) — deliberately not
# modeled here. R1AP clause 9 contains only 9.1 (policy management); the
# five A1-ML operations v1.3 listed require implementing genuine A1AP
# behavior, out of this project's declared scope, categorically — not just
# "inert until Near-RT RIC exists." See the LLD for the full finding.
