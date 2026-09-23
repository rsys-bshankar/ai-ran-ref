import uuid

from sqlalchemy import JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from smo_shared.db import Base


class ServiceOrder(Base):
    __tablename__ = "service_order"

    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    scope: Mapped[str] = mapped_column(String, nullable=False)
    steps: Mapped[list] = mapped_column(JSON, nullable=False)  # [{stepType, targetModule, status, ...}]
    homing_decision: Mapped[dict | None] = mapped_column(JSON)
    rmih_registration: Mapped[str] = mapped_column(String, nullable=False)
