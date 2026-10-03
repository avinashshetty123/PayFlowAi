import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import CreatedAtMixin, UUIDPkMixin


class Incident(UUIDPkMixin, CreatedAtMixin, Base):
    __tablename__ = "incidents"

    incident_number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    root_cause: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    ai_summary: Mapped[str | None] = mapped_column(Text)
    recommended_action: Mapped[str | None] = mapped_column(String(48))
    requires_human: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    risk: Mapped[str | None] = mapped_column(String(16))
    policy_decision: Mapped[str | None] = mapped_column(String(48))
    # System states when the mismatch was detected / after remediation.
    initial_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    final_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    detection_findings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # PAYPAL_PROVIDER_FAILURE vs PAYFLOW_INFRASTRUCTURE_FAILURE (injected) vs HISTORICAL
    failure_source: Mapped[str | None] = mapped_column(String(48))
    injected_scenario: Mapped[str | None] = mapped_column(String(48))

    # Deterministic risk score (0-100) with factor breakdown, computed at decision time.
    risk_score: Mapped[int | None] = mapped_column(Integer)
    risk_factors: Mapped[dict | None] = mapped_column(JSONB)
    # Human handling: acknowledgement and how the incident was closed.
    acknowledged_by: Mapped[str | None] = mapped_column(String(64))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[str | None] = mapped_column(String(32))  # AUTOMATED | HUMAN_APPROVED | MANUAL | ACCEPTED_RISK | FALSE_POSITIVE
    resolution_note: Mapped[str | None] = mapped_column(Text)
    # LangGraph agent path taken for this incident: [{node, at, outcome}]
    agent_trace: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
