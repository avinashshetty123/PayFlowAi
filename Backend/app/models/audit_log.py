import uuid

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import CreatedAtMixin, UUIDPkMixin


class AuditLog(UUIDPkMixin, CreatedAtMixin, Base):
    __tablename__ = "audit_logs"

    transaction_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    event: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Tamper-evident sealing: rows are hash-chained by a background sealer in chain_index order.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True, nullable=False)
    chain_index: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    hash: Mapped[str | None] = mapped_column(String(64))
    sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
