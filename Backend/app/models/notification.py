import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import CreatedAtMixin, UUIDPkMixin


class Notification(UUIDPkMixin, CreatedAtMixin, Base):
    """An operator alert. Written in the same transaction as the event that caused it (outbox)."""

    __tablename__ = "notifications"

    dedup_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    transaction_id: Mapped[str | None] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(4), nullable=False, index=True)  # P1..P4
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    link: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN", index=True)  # OPEN | ACKNOWLEDGED | RESOLVED
    escalation_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    acknowledged_by: Mapped[str | None] = mapped_column(String(64))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationDelivery(UUIDPkMixin, CreatedAtMixin, Base):
    """One delivery attempt per channel (delivery receipt)."""

    __tablename__ = "notification_deliveries"

    notification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(String(24), nullable=False)  # whatsapp | telegram | ntfy | slack | webhook
    target: Mapped[str] = mapped_column(String(120), nullable=False)  # masked
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING", index=True)  # PENDING | SENT | FAILED
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    escalation_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_message_id: Mapped[str | None] = mapped_column(String(120))
    error: Mapped[str | None] = mapped_column(String(400))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
