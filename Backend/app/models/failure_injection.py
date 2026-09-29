import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import CreatedAtMixin, UUIDPkMixin


class FailureInjection(UUIDPkMixin, CreatedAtMixin, Base):
    """A deliberate, clearly-labelled demo failure in PayFlow's own infrastructure (never in PayPal)."""

    __tablename__ = "failure_injections"

    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scenario: Mapped[str] = mapped_column(String(48), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)  # armed, not yet triggered
    injected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # when it actually fired
    injected_by: Mapped[str] = mapped_column(String(64), nullable=False, default="demo-operator")
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
