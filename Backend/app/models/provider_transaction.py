import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import CreatedAtMixin, UUIDPkMixin


class ProviderTransaction(UUIDPkMixin, CreatedAtMixin, Base):
    """Every call PayFlow made to the payment provider (order, capture, refund, status sync) and its outcome."""

    __tablename__ = "provider_transactions"

    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)  # ORDER | CAPTURE | REFUND | STATUS_SYNC
    provider_reference: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(48), nullable=False)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    debug_id: Mapped[str | None] = mapped_column(String(64))  # PayPal-Debug-Id for support tickets
    error: Mapped[str | None] = mapped_column(String(500))
    # Trimmed provider response (ids, statuses, amounts). Never card or bank details.
    response: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
