import uuid
from datetime import datetime

from pydantic import BaseModel

from app.schemas.action import ActionOut
from app.schemas.common import ORMModel
from app.schemas.investigation import InvestigationOut
from app.schemas.payment import FailureInjectionOut, PaymentOut, ProviderTransactionOut, WebhookEventOut


class IncidentSummary(BaseModel):
    id: uuid.UUID
    incident_number: str
    transaction_id: str
    amount: float
    type: str
    severity: str
    status: str
    root_cause: str | None
    confidence: float | None
    recommended_action: str | None
    requires_human: bool
    risk: str | None
    policy_decision: str | None
    ai_status: str
    action_status: str | None
    created_at: datetime
    resolved_at: datetime | None
    currency: str = "INR"
    provider: str = "SYNTHETIC"
    provider_status: str | None = None
    failure_source: str | None = None
    injected_scenario: str | None = None
    risk_score: int | None = None
    resolution: str | None = None
    acknowledged_by: str | None = None


class IncidentListResponse(BaseModel):
    items: list[IncidentSummary]
    total: int


class IncidentDetail(IncidentSummary):
    ai_summary: str | None
    initial_snapshot: dict
    final_snapshot: dict | None
    current_snapshot: dict
    detection_findings: list
    payment: PaymentOut
    investigation: InvestigationOut | None
    actions: list[ActionOut]
    risk_factors: dict | None = None
    acknowledged_at: datetime | None = None
    resolution_note: str | None = None
    agent_trace: list = []
    failure_injections: list[FailureInjectionOut] = []
    webhook_events: list[WebhookEventOut] = []
    provider_transactions: list[ProviderTransactionOut] = []


class TimelineItem(BaseModel):
    timestamp: datetime
    source: str
    label: str
    detail: str | None = None
    kind: str  # payment | detection | ai | policy | action | verification | incident | human
    tone: str  # ok | warn | error | info


class AuditLogOut(ORMModel):
    id: uuid.UUID
    transaction_id: str
    incident_id: uuid.UUID | None
    actor: str
    event: str
    reason: str
    evidence: dict
    result: dict
    created_at: datetime


class AuditListResponse(BaseModel):
    items: list[AuditLogOut]
    total: int


class InvestigateResponse(BaseModel):
    incident_id: uuid.UUID
    status: str
    pipeline_mode: str
    message: str
