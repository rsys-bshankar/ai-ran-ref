import uuid

from sqlalchemy import ARRAY, JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from smo_shared.db import Base


class MDAFProducer(Base):
    __tablename__ = "mdaf_producer"

    producer_id: Mapped[str] = mapped_column(String, primary_key=True)
    analytics_type: Mapped[str] = mapped_column(String, primary_key=True)
    dme_input_types: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid).with_variant(JSON(none_as_null=True), "sqlite"), nullable=False)
    output_schema: Mapped[dict] = mapped_column(JSON, nullable=False)
