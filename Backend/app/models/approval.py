import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import CreatedAtMixin, UUIDPkMixin


class Approval(UUIDPkMixin, CreatedAtMixin, Base):
    """A human decision on an action the policy engine would not run automatically."""

    __tablename__ = "approvals"

    action_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("actions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    decision: Mapped[str] = mapped_column(String(16), nullable=False)  # APPROVED | REJECTED
    approver: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    policy_recheck: Mapped[dict | None] = mapped_column(JSONB)
