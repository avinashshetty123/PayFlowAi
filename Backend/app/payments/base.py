"""Provider-agnostic payment interface. PayFlow logic depends on this, not on PayPal directly."""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol


class ProviderError(Exception):
    """A failure reported by (or while talking to) the payment provider.

    ``kind`` distinguishes provider-side business failures (e.g. INSTRUMENT_DECLINED)
    from transport problems (timeout, 5xx) so incidents can be labelled
    PAYPAL_PROVIDER_FAILURE rather than a PayFlow infrastructure failure.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        issue: str | None = None,
        debug_id: str | None = None,
        kind: str = "PROVIDER_ERROR",  # PROVIDER_ERROR | DECLINED | TRANSPORT | AUTH | NOT_CONFIGURED
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.issue = issue
        self.debug_id = debug_id
        self.kind = kind

    def as_dict(self) -> dict:
        return {"message": self.message, "status_code": self.status_code, "issue": self.issue,
                "debug_id": self.debug_id, "kind": self.kind}


@dataclass
class ProviderOrder:
    order_id: str
    status: str  # CREATED | APPROVED | COMPLETED | ...
    approve_url: str | None = None
    capture_id: str | None = None
    capture_status: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    payer: dict | None = None
    debug_id: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class ProviderCapture:
    order_id: str
    order_status: str
    capture_id: str | None
    capture_status: str | None  # COMPLETED | PENDING | DECLINED | FAILED
    amount: Decimal | None
    currency: str | None
    fee: Decimal | None = None
    net_amount: Decimal | None = None
    payer: dict | None = None
    debug_id: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class ProviderRefund:
    refund_id: str
    status: str  # COMPLETED | PENDING | FAILED | CANCELLED
    amount: Decimal | None
    currency: str | None
    debug_id: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class WebhookVerification:
    verified: bool
    detail: str


class PaymentProvider(Protocol):
    name: str

    async def create_order(
        self, *, reference: str, amount: Decimal, currency: str, description: str, return_url: str, cancel_url: str,
        idempotency_key: str,
    ) -> ProviderOrder: ...

    async def get_order(self, order_id: str) -> ProviderOrder: ...

    async def capture_order(
        self, order_id: str, *, idempotency_key: str, mock_error: str | None = None
    ) -> ProviderCapture: ...

    async def refund_payment(
        self, capture_id: str, *, amount: Decimal, currency: str, idempotency_key: str, note: str | None = None
    ) -> ProviderRefund: ...

    async def get_capture(self, capture_id: str) -> dict: ...

    async def get_refund(self, refund_id: str) -> dict: ...

    async def verify_webhook(self, *, headers: dict[str, str], raw_body: str) -> WebhookVerification: ...
