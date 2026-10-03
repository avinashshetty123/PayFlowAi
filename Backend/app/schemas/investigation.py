import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums import ActionType, Risk
from app.schemas.common import ORMModel


class ImpactAssessment(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    customer_impact: str | None = Field(default=None, alias="customerImpact", max_length=300)
    financial_exposure: str | None = Field(default=None, alias="financialExposure", max_length=200)
    blast_radius: str | None = Field(default=None, alias="blastRadius", max_length=200)
    urgency: str | None = Field(default=None, max_length=40)


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
    # ---- richer analysis (optional so a terse model answer still validates) ----
    impact: "ImpactAssessment | None" = None
    contributing_factors: list[str] = Field(default_factory=list, alias="contributingFactors", max_length=8)
    remediation_plan: list[str] = Field(default_factory=list, alias="remediationPlan", max_length=8)
    preventive_measures: list[str] = Field(default_factory=list, alias="preventiveMeasures", max_length=6)
    anomalies: list[str] = Field(default_factory=list, max_length=6)
    confidence_rationale: str | None = Field(default=None, alias="confidenceRationale", max_length=400)

    @field_validator("evidence", "contributing_factors", "remediation_plan", "preventive_measures", "anomalies")
    @classmethod
    def _trim_list(cls, value: list[str]) -> list[str]:
        return [str(v).strip()[:240] for v in value if str(v).strip()]

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
AIInvestigationResult.model_rebuild()
