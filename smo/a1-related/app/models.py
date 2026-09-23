import uuid

from sqlalchemy import ARRAY, CheckConstraint, ForeignKey, JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from smo_shared.db import Base


class A1Policy(Base):
    __tablename__ = "a1_policy"

    policy_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
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
    policy_id_list: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    policy_type_id_list: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    near_rt_ric_id_list: Mapped[list[str] | None] = mapped_column(ARRAY(String))


class A1EIType(Base):
    __tablename__ = "a1_ei_type"

    ei_type_id: Mapped[str] = mapped_column(String, primary_key=True)
    registered_by: Mapped[str] = mapped_column(String, nullable=False)
    ei_source_dme_type_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)

# A1TrainingCapability: DORMANT (A1 Related LLD section 0) — deliberately not
# modeled here. R1AP clause 9 contains only 9.1 (policy management); the
# five A1-ML operations v1.3 listed require implementing genuine A1AP
# behavior, out of this project's declared scope, categorically — not just
# "inert until Near-RT RIC exists." See the LLD for the full finding.
