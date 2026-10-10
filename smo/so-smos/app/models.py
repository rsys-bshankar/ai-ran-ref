"""The ORM table of SO SMOS: the service order and the per-step results of its execution.

Read and written by `app/main.py` only. The schema is the Alembic history in `migrations/` (model and revision change together). Unlike some other modules' models, `ServiceOrder` is
not versioned: it is written once at submit and replaced wholesale by cancel.
"""

import uuid

from sqlalchemy import JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from smo_shared.db import Base


class ServiceOrder(Base):
    """One submitted service order: its scope, its steps with their status and result, and the label it was registered under.

    `steps` is a JSON list; submit replaces the caller's steps with the executed ones (each step dict plus `status`, `result` or `error`) and cancel replaces it again with a new list
    (an in-place change to a plain JSON column is not persisted). `homing_decision` is nullable and no route in this module sets it. `rmih_registration` is the label given at submit
    (default `so-smos`) and is stored only.
    """
    __tablename__ = "service_order"

    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    scope: Mapped[str] = mapped_column(String, nullable=False)
    steps: Mapped[list] = mapped_column(JSON, nullable=False)  # [{stepType, targetModule, status, ...}]
    homing_decision: Mapped[dict | None] = mapped_column(JSON)
    rmih_registration: Mapped[str] = mapped_column(String, nullable=False)
