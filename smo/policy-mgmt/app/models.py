import datetime
import uuid

from sqlalchemy import ARRAY, Boolean, ForeignKey, Integer, JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from smo_shared.db import Base


class Intent(Base):
    __tablename__ = "intent"

    intent_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_label: Mapped[str | None] = mapped_column(String)
    intent_expectations: Mapped[list] = mapped_column(JSON, nullable=False)  # opaque, TS 28.312 text not in this corpus
    intent_mgmt_purpose: Mapped[str | None] = mapped_column(String)
    intent_admin_state: Mapped[str] = mapped_column(String, nullable=False, default="ACTIVATED")
    intent_priority: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    intent_preemption_capability: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rmio_id: Mapped[str] = mapped_column(String, nullable=False)


class IntentReport(Base):
    __tablename__ = "intent_report"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    intent_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("intent.intent_id"))  # was intent_reference (bare string) pre-LLD
    intent_fulfilment_report: Mapped[dict | None] = mapped_column(JSON)
    intent_conflict_reports: Mapped[list | None] = mapped_column(JSON)
    last_updated_time: Mapped[datetime.datetime] = mapped_column(default=lambda: datetime.datetime.now(datetime.UTC))


class IntentHandlingFunction(Base):
    __tablename__ = "intent_handling_function"

    rmih_id: Mapped[str] = mapped_column(String, primary_key=True)
    sme_service_id: Mapped[str] = mapped_column(String, nullable=False)
    intent_handling_scope: Mapped[list | None] = mapped_column(JSON)
    intent_handling_capability_list: Mapped[list] = mapped_column(JSON, nullable=False)
    # Closes the Intent-to-RMIH matching/dispatch gap — CreateIntent POSTs
    # here on a capability match, same established pattern as DME's
    # producerHealthCallbackUrl.
    notification_callback_uri: Mapped[str] = mapped_column(String, nullable=False)
