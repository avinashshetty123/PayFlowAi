from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import CreatedAtMixin, UUIDPkMixin, utcnow


class Payment(UUIDPkMixin, CreatedAtMixin, Base):
    __tablename__ = "payments"

    transaction_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    customer_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")

    gateway_status: Mapped[str] = mapped_column(String(32), nullable=False)
    bank_status: Mapped[str] = mapped_column(String(32), nullable=False)
    merchant_status: Mapped[str] = mapped_column(String(32), nullable=False)
    ledger_status: Mapped[str] = mapped_column(String(32), nullable=False)
    webhook_status: Mapped[str] = mapped_column(String(32), nullable=False)

    # Canonical PayFlow state, only ever changed through PaymentStateService.
    overall_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    # Provider view (kept separate from PayFlow's normalized gateway_status).
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="SYNTHETIC", index=True)
    provider_order_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    provider_capture_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    provider_status: Mapped[str | None] = mapped_column(String(48))  # raw PayPal status, e.g. COMPLETED
    reconciliation_status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING")
    # Only what PayPal returns and PayFlow needs: payer id / email / country. No instrument data.
    payer: Mapped[dict | None] = mapped_column(JSONB)
    provider_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Provenance: every simulator record is explicitly flagged as simulated.
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="SIMULATOR")
    scenario: Mapped[str | None] = mapped_column(String(32))
    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
