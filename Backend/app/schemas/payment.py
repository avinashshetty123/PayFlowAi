import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, computed_field

from app.core.config import settings

from app.core.enums import Scenario
from app.schemas.common import ORMModel


class PaymentOut(ORMModel):
    id: uuid.UUID
    transaction_id: str
    customer_id: str
    amount: float
    currency: str
    gateway_status: str
    bank_status: str
    merchant_status: str
    ledger_status: str
    webhook_status: str
    overall_status: str
    source: str
    scenario: str | None
    is_simulated: bool
    provider: str
    provider_order_id: str | None
    provider_capture_id: str | None
    provider_status: str | None
    reconciliation_status: str
    payer: dict | None
    created_at: datetime
    updated_at: datetime

    @computed_field  # display only: PayPal processes USD, INR is a demo equivalent
    @property
    def inr_equivalent(self) -> float | None:
        return round(self.amount * settings.USD_INR_DEMO_RATE, 0) if self.currency == "USD" else None


class ProviderTransactionOut(ORMModel):
    id: uuid.UUID
    kind: str
    provider_reference: str | None
    status: str
    amount: float | None
    currency: str | None
    idempotency_key: str | None
    debug_id: str | None
    error: str | None
    created_at: datetime


class WebhookEventOut(ORMModel):
    id: uuid.UUID
    provider: str
    provider_event_id: str
    event_type: str
    transaction_id: str | None
    transmission_id: str | None
    signature_verified: bool | None
    verification_detail: str | None
    processing_status: str
    delivery_count: int
    error: str | None
    received_at: datetime
    processed_at: datetime | None


class FailureInjectionOut(BaseModel):
    id: uuid.UUID
    scenario: str
    enabled: bool
    injected_by: str
    created_at: datetime
    injected_at: datetime | None
    metadata: dict


class PaymentEventOut(ORMModel):
    id: uuid.UUID
    source: str
    event_type: str
    payload: dict
    created_at: datetime


class BankTransactionOut(ORMModel):
    id: uuid.UUID
    bank_reference: str
    amount: float
    status: str
    settled_at: datetime | None
    created_at: datetime


class MerchantTransactionOut(ORMModel):
    id: uuid.UUID
    order_id: str
    amount: float
    status: str
    created_at: datetime


class LedgerEntryOut(ORMModel):
    id: uuid.UUID
    amount: float
    entry_type: str
    status: str
    created_at: datetime
    updated_at: datetime


class PaymentListResponse(BaseModel):
    items: list[PaymentOut]
    total: int


class PaymentIncidentRef(BaseModel):
    id: uuid.UUID
    incident_number: str
    type: str
    status: str
    severity: str


class PaymentDetail(BaseModel):
    payment: PaymentOut
    events: list[PaymentEventOut]
    bank_transactions: list[BankTransactionOut]
    merchant_transactions: list[MerchantTransactionOut]
    ledger_entries: list[LedgerEntryOut]
    incidents: list[PaymentIncidentRef]
    provider_transactions: list[ProviderTransactionOut] = []
    webhook_events: list[WebhookEventOut] = []
    failure_injections: list[FailureInjectionOut] = []
    approve_url: str | None = None


class SimulatePaymentRequest(BaseModel):
    amount: Decimal = Field(gt=0, le=10_000_000, decimal_places=2, examples=[4850])
    scenario: Scenario = Field(examples=["LEDGER_MISMATCH"])
    transaction_id: str | None = Field(default=None, pattern=r"^TXN\d{4,12}$")
    customer_id: str | None = Field(default=None, max_length=64)
    # Run the whole incident pipeline inside the request (used by tests / scripts).
    sync: bool = False


class ReconciliationOut(BaseModel):
    consistent: bool
    incident_type: str | None
    severity: str | None
    summary: str
    findings: list[str]
    snapshot: dict


class SimulatePaymentResponse(BaseModel):
    payment: PaymentOut
    related_payment: PaymentOut | None = None
    reconciliation: ReconciliationOut
    incident_id: uuid.UUID | None = None
    incident_number: str | None = None
    incident_status: str | None = None
    pipeline_mode: str | None = None


class CreatePayPalOrderRequest(BaseModel):
    amount: Decimal = Field(default=Decimal("50.00"), gt=0, le=1000, decimal_places=2)
    demo: str = Field(default="PAYMENT_ONLY", pattern="^(PAYMENT_ONLY|LEDGER_MISMATCH|REFUND_REQUIRES_APPROVAL)$")
    failure_scenarios: list[str] = Field(default_factory=list, max_length=4)
    negative_test: str | None = None
    description: str | None = Field(default=None, max_length=120)


class CreatePayPalOrderResponse(BaseModel):
    payment: PaymentOut
    order_id: str
    approve_url: str | None
    armed_failures: list[str]


class CaptureRequest(BaseModel):
    transaction_id: str | None = None
    order_id: str | None = None


class CaptureResponse(BaseModel):
    payment: PaymentOut
    status: str
    message: str
    pipeline_mode: str | None = None


class PaymentStatusOut(BaseModel):
    transaction_id: str
    provider: str
    provider_status: str | None
    overall_status: str
    gateway_status: str
    bank_status: str
    merchant_status: str
    ledger_status: str
    webhook_status: str
    reconciliation_status: str
    captured: bool
    open_incident_id: uuid.UUID | None = None


class RefundRequest(BaseModel):
    reason: str = Field(default="Customer requested a refund", max_length=300)
    requested_by: str = Field(default="merchant-portal", max_length=64)


class InjectFailureRequest(BaseModel):
    transaction_id: str
    scenario: str
    injected_by: str = Field(default="demo-operator", max_length=64)
