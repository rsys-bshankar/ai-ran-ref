"""Transactional notification outbox: the table (PR-MSG-1.2).

A notification to a caller-supplied destination is written as a row in the same transaction as the change that
caused it, and sent afterwards, so a crash between the commit and the send leaves a pending row to send later
instead of losing the notification. This module holds the table; `enqueue` and `drain` (MSG-1.3, 1.4) and the
modules' adoption (MSG-1.5 onwards) follow in `OPEN_ITEMS.md`.

The table is created by schema revision `0002` (`migrations/versions/0002_notification_outbox.py`), the first revision after the baseline.
"""

import datetime
import uuid

from sqlalchemy import JSON, DateTime, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

PENDING, SENT, DEAD = "PENDING", "SENT", "DEAD"


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


class NotificationOutbox(Base):
    __tablename__ = "notification_outbox"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    module: Mapped[str] = mapped_column(String, nullable=False)          # the module that enqueued it
    destination: Mapped[str] = mapped_column(String, nullable=False)     # the caller-supplied URL
    payload: Mapped[dict | list] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default=PENDING)   # PENDING | SENT | DEAD
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
