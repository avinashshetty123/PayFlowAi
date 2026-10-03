import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.core.enums import PolicyDecision
from app.schemas.common import ORMModel


class PolicyCheck(BaseModel):
    name: str
    passed: bool
    detail: str


class PolicyEvaluation(BaseModel):
    action_type: str
    decision: PolicyDecision
    label: str
    reasons: list[str]
    checks: list[PolicyCheck]
    fired_rules: list[str] = []
    risk_score: int | None = None
    policy_version: str | None = None
    overridden_ai_action: str | None = None


class ActionOut(ORMModel):
    id: uuid.UUID
    incident_id: uuid.UUID
    action_type: str
    idempotency_key: str
    status: str
    requested_by: str
    approved_by: str | None
    reason: str | None
    policy: dict
    result: dict | None
    created_at: datetime
    completed_at: datetime | None


class ApprovalQueueItem(BaseModel):
    action: ActionOut
    incident_id: uuid.UUID
    incident_number: str
    incident_type: str
    severity: str
    transaction_id: str
    amount: float
    risk: str | None
    confidence: float | None
    root_cause: str | None
    ai_summary: str | None
    recommended_action: str | None
    snapshot: dict
    currency: str = "INR"
    provider: str = "SYNTHETIC"
    provider_capture_id: str | None = None


class ApproveRequest(BaseModel):
    approver: str = Field(default="ops.manager", min_length=2, max_length=64)
    note: str | None = Field(default=None, max_length=500)


class RejectRequest(BaseModel):
    approver: str = Field(default="ops.manager", min_length=2, max_length=64)
    reason: str = Field(default="Rejected by operator", min_length=2, max_length=500)


class ActionDecisionResponse(BaseModel):
    action: ActionOut
    incident_status: str
    verification: dict | None = None
    deduplicated: bool = False
    message: str
