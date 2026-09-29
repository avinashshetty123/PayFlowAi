from pydantic import BaseModel

from app.schemas.incident import IncidentSummary


class VolumePoint(BaseModel):
    date: str
    label: str
    count: int
    amount: float


class RecoveryPoint(BaseModel):
    date: str
    label: str
    detected: int
    resolved: int


class StatusCount(BaseModel):
    status: str
    count: int


class TypeCount(BaseModel):
    type: str
    count: int


class DashboardStats(BaseModel):
    total_payments: int
    successful_payments: int
    failed_payments: int
    refunded_payments: int
    total_volume: float
    active_incidents: int
    auto_recovered: int
    human_recovered: int
    pending_approvals: int
    amount_recovered: float
    success_rate: float
    reconciliation_rate: float
    paypal_payments: int
    currency: str
    payment_volume: list[VolumePoint]
    payment_status: list[StatusCount]
    incident_types: list[TypeCount]
    recovery_trend: list[RecoveryPoint]
    recent_incidents: list[IncidentSummary]
