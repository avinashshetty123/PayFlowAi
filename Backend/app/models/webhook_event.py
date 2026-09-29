from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import UUIDPkMixin, utcnow


class WebhookEvent(UUIDPkMixin, Base):
    """Inbound provider webhook. ``provider_event_id`` is unique, so redeliveries are detected."""

    __tablename__ = "webhook_events"

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(96), nullable=False, unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(96), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(64), index=True)
    transaction_id: Mapped[str | None] = mapped_column(String(64), index=True)
    transmission_id: Mapped[str | None] = mapped_column(String(96))
    signature_verified: Mapped[bool | None] = mapped_column(Boolean)
    verification_detail: Mapped[str | None] = mapped_column(String(300))
    # RECEIVED | QUEUED | PROCESSED | REJECTED | DELAYED | DROPPED | IGNORED | FAILED
    processing_status: Mapped[str] = mapped_column(String(24), nullable=False, default="RECEIVED")
    delivery_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    error: Mapped[str | None] = mapped_column(Text)
    headers: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    raw_body: Mapped[str] = mapped_column(Text, nullable=False)  # exact bytes, needed for signature verification
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
