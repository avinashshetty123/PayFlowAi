import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums import ActionType, Risk
from app.schemas.common import ORMModel


class AIInvestigationResult(BaseModel):
    """Strict contract for LLM output. Anything that fails validation triggers the fallback."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    incident_type: str = Field(alias="incidentType", min_length=3, max_length=48)
    root_cause: str = Field(alias="rootCause", min_length=3, max_length=400)
    confidence: float = Field(ge=0.0, le=1.0)
    recommended_action: ActionType = Field(alias="recommendedAction")
    risk: Risk
    requires_human: bool = Field(alias="requiresHuman")
    evidence: list[str] = Field(min_length=1, max_length=12)
    summary: str = Field(min_length=3, max_length=600)

    @field_validator("evidence")
    @classmethod
    def _trim_evidence(cls, value: list[str]) -> list[str]:
        return [str(v).strip()[:200] for v in value if str(v).strip()]

    def to_public(self) -> dict:
        return self.model_dump(by_alias=True, mode="json")


class HistoricalMatch(BaseModel):
    id: str
    title: str
    incident_type: str
    similarity: float
    root_cause: str
    resolution: str
    severity: str
    retrieval: str = "keyword"
    reference: str | None = None
    resolution_mode: str | None = None
    resolved_in_seconds: int | None = None


class InvestigationOut(ORMModel):
    id: uuid.UUID
    incident_id: uuid.UUID
    model: str
    used_fallback: bool
    summary: str
    evidence: dict
    historical_matches: list
    recommendation: dict
    latency_ms: int | None
    created_at: datetime
