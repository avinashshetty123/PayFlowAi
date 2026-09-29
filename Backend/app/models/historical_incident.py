from sqlalchemy import Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import CreatedAtMixin, UUIDPkMixin


class HistoricalIncident(UUIDPkMixin, CreatedAtMixin, Base):
    """Knowledge base of historical PayFlow incidents for RAG (not PayPal data).

    When pgvector is installed the migration adds an ``embedding vector(256)``
    column that is read/written with raw SQL (see HistoricalIncidentService).
    ``embedding_json`` always holds the same vector for the non-pgvector fallback.
    """

    __tablename__ = "historical_incidents"

    reference: Mapped[str | None] = mapped_column(String(24))  # e.g. INC-892
    incident_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    symptoms: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    root_cause: Mapped[str] = mapped_column(Text, nullable=False)
    resolution: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    resolution_mode: Mapped[str | None] = mapped_column(String(24))  # AUTOMATIC | HUMAN
    resolved_in_seconds: Mapped[int | None] = mapped_column(Integer)
    embedding_json: Mapped[list | None] = mapped_column(JSONB)
