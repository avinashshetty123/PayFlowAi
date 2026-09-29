"""Scenario catalogue for the payment simulator.

Each scenario describes what every system (gateway, bank, merchant, ledger,
webhook) reports for a simulated payment, the canonical state path PayFlow
walks through, and the event stream that produced it. All data is simulated.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from app.core.enums import (
    BankStatus as B,
    GatewayStatus as G,
    LedgerStatus as L,
    MerchantStatus as M,
    PaymentState as P,
    Scenario,
    WebhookStatus as W,
)


@dataclass(frozen=True)
class ScenarioEvent:
    offset: int  # seconds after payment creation
    source: str  # GATEWAY | BANK | MERCHANT | LEDGER | WEBHOOK | PAYFLOW
    event_type: str
    label: str
    detail: str | None = None


@dataclass(frozen=True)
class ScenarioSpec:
    scenario: Scenario
    description: str
    gateway: str
    bank: str
    merchant: str
    ledger: str
    webhook: str
    state_path: tuple[P, ...]
    events: tuple[ScenarioEvent, ...]
    ledger_entries: tuple[tuple[str, str], ...] = (("CAPTURE", L.SUCCESS),)
    # Settlement mismatch: bank credits a different amount than was captured.
    bank_amount_factor: Decimal = Decimal("1")
    expects_incident: bool = True
    extra: dict = field(default_factory=dict)

    @property
    def duration(self) -> int:
        return max((e.offset for e in self.events), default=0)


def _created() -> ScenarioEvent:
    return ScenarioEvent(0, "PAYFLOW", "PAYMENT_CREATED", "Payment created", "Checkout initiated by customer")


def _gateway_success(offset: int = 3) -> ScenarioEvent:
    return ScenarioEvent(offset, "GATEWAY", "GATEWAY_SUCCESS", "Gateway SUCCESS", "payment.captured")


def _webhook_received(offset: int = 4) -> ScenarioEvent:
    return ScenarioEvent(offset, "WEBHOOK", "WEBHOOK_RECEIVED", "Webhook received", "payment.captured delivered (HTTP 200)")


def _bank_settled(offset: int = 6) -> ScenarioEvent:
    return ScenarioEvent(offset, "BANK", "BANK_SETTLED", "Bank SETTLED", "Settlement file entry matched (simulated)")


SCENARIOS: dict[Scenario, ScenarioSpec] = {
    Scenario.SUCCESS: ScenarioSpec(
        scenario=Scenario.SUCCESS,
        description="Happy path: every system agrees.",
        gateway=G.SUCCESS, bank=B.SETTLED, merchant=M.SUCCESS, ledger=L.SUCCESS, webhook=W.RECEIVED,
        state_path=(P.PROCESSING, P.SUCCESS, P.SETTLED),
        events=(
            _created(),
            _gateway_success(),
            _webhook_received(),
            ScenarioEvent(5, "MERCHANT", "ORDER_CONFIRMED", "Merchant SUCCESS", "Order marked paid"),
            ScenarioEvent(5, "LEDGER", "LEDGER_POSTED", "Ledger SUCCESS", "Capture posted to ledger"),
            _bank_settled(),
        ),
        expects_incident=False,
    ),
    Scenario.FAILED: ScenarioSpec(
        scenario=Scenario.FAILED,
        description="Issuer declined the payment; all systems agree it failed.",
        gateway=G.FAILED, bank=B.NOT_FOUND, merchant=M.FAILED, ledger=L.FAILED, webhook=W.RECEIVED,
        state_path=(P.PROCESSING, P.FAILED),
        events=(
            _created(),
            ScenarioEvent(3, "GATEWAY", "GATEWAY_FAILED", "Gateway FAILED", "payment.failed: card declined by issuer"),
            ScenarioEvent(4, "WEBHOOK", "WEBHOOK_RECEIVED", "Webhook received", "payment.failed delivered (HTTP 200)"),
            ScenarioEvent(5, "MERCHANT", "ORDER_FAILED", "Merchant FAILED", "Order released"),
            ScenarioEvent(5, "LEDGER", "LEDGER_VOIDED", "Ledger FAILED", "No capture posted"),
        ),
        ledger_entries=(("CAPTURE", L.FAILED),),
        expects_incident=False,
    ),
    Scenario.PENDING: ScenarioSpec(
        scenario=Scenario.PENDING,
        description="UPI collect request awaiting customer approval (within SLA).",
        gateway=G.PENDING, bank=B.PENDING, merchant=M.PENDING, ledger=L.PENDING, webhook=W.NOT_RECEIVED,
        state_path=(P.PROCESSING, P.PENDING),
        events=(
            _created(),
            ScenarioEvent(2, "GATEWAY", "GATEWAY_PENDING", "Gateway PENDING", "UPI collect request sent to customer"),
        ),
        ledger_entries=(("CAPTURE", L.PENDING),),
        expects_incident=False,
    ),
    Scenario.TIMEOUT: ScenarioSpec(
        scenario=Scenario.TIMEOUT,
        description="Acquirer timed out; the bank never debited the customer.",
        gateway=G.TIMEOUT, bank=B.NOT_FOUND, merchant=M.PENDING, ledger=L.PENDING, webhook=W.NOT_RECEIVED,
        state_path=(P.PROCESSING, P.PENDING, P.UNKNOWN),
        events=(
            _created(),
            ScenarioEvent(2, "GATEWAY", "GATEWAY_PROCESSING", "Gateway PROCESSING", "Authorisation sent to acquirer"),
            ScenarioEvent(32, "GATEWAY", "GATEWAY_TIMEOUT", "Gateway TIMEOUT", "No acquirer response after 30s"),
            ScenarioEvent(35, "BANK", "BANK_LOOKUP_EMPTY", "Bank: no debit found", "No matching UTR in bank statement"),
        ),
        ledger_entries=(("CAPTURE", L.PENDING),),
    ),
    Scenario.WEBHOOK_DELAY: ScenarioSpec(
        scenario=Scenario.WEBHOOK_DELAY,
        description="Payment captured and settled, webhook delivery is delayed.",
        gateway=G.SUCCESS, bank=B.SETTLED, merchant=M.PENDING, ledger=L.PENDING, webhook=W.DELAYED,
        state_path=(P.PROCESSING, P.PENDING),
        events=(
            _created(),
            _gateway_success(),
            ScenarioEvent(5, "WEBHOOK", "WEBHOOK_DELIVERY_FAILED", "Webhook delayed", "Attempt 1: merchant endpoint returned HTTP 504"),
            _bank_settled(7),
        ),
        ledger_entries=(("CAPTURE", L.PENDING),),
    ),
    Scenario.WEBHOOK_LOST: ScenarioSpec(
        scenario=Scenario.WEBHOOK_LOST,
        description="Payment captured and settled, webhook never arrived.",
        gateway=G.SUCCESS, bank=B.SETTLED, merchant=M.PENDING, ledger=L.PENDING, webhook=W.NOT_RECEIVED,
        state_path=(P.PROCESSING, P.PENDING),
        events=(
            _created(),
            _gateway_success(),
            _bank_settled(6),
            ScenarioEvent(9, "WEBHOOK", "WEBHOOK_SLA_BREACHED", "Webhook missing", "No payment.captured webhook within SLA"),
        ),
        ledger_entries=(("CAPTURE", L.PENDING),),
    ),
    Scenario.LEDGER_MISMATCH: ScenarioSpec(
        scenario=Scenario.LEDGER_MISMATCH,
        description="Money settled at the bank, but the merchant order and internal ledger failed to update.",
        gateway=G.SUCCESS, bank=B.SETTLED, merchant=M.FAILED, ledger=L.FAILED, webhook=W.RECEIVED,
        state_path=(P.PROCESSING, P.SUCCESS, P.SETTLED),
        events=(
            _created(),
            _gateway_success(3),
            _webhook_received(4),
            ScenarioEvent(5, "MERCHANT", "ORDER_UPDATE_FAILED", "Merchant FAILED", "Order service returned HTTP 503 during fulfilment callback"),
            ScenarioEvent(5, "LEDGER", "LEDGER_POST_FAILED", "Ledger FAILED", "Ledger write aborted: lock wait timeout exceeded"),
            _bank_settled(6),
        ),
        ledger_entries=(("CAPTURE", L.FAILED),),
    ),
    Scenario.SETTLEMENT_MISMATCH: ScenarioSpec(
        scenario=Scenario.SETTLEMENT_MISMATCH,
        description="Bank settled a different amount than the gateway captured.",
        gateway=G.SUCCESS, bank=B.SETTLED, merchant=M.SUCCESS, ledger=L.SUCCESS, webhook=W.RECEIVED,
        state_path=(P.PROCESSING, P.SUCCESS, P.SETTLED),
        events=(
            _created(),
            _gateway_success(),
            _webhook_received(),
            ScenarioEvent(5, "MERCHANT", "ORDER_CONFIRMED", "Merchant SUCCESS", "Order marked paid"),
            ScenarioEvent(5, "LEDGER", "LEDGER_POSTED", "Ledger SUCCESS", "Capture posted to ledger"),
            ScenarioEvent(8, "BANK", "BANK_SETTLED_SHORT", "Bank SETTLED (short)", "Settlement amount lower than captured amount"),
        ),
        bank_amount_factor=Decimal("0.98"),
    ),
    Scenario.DUPLICATE_PAYMENT: ScenarioSpec(
        scenario=Scenario.DUPLICATE_PAYMENT,
        description="Customer retried checkout; the same order was captured twice.",
        gateway=G.SUCCESS, bank=B.SETTLED, merchant=M.DUPLICATE, ledger=L.SUCCESS, webhook=W.RECEIVED,
        state_path=(P.PROCESSING, P.SUCCESS, P.SETTLED),
        events=(
            _created(),
            _gateway_success(),
            _webhook_received(),
            ScenarioEvent(5, "MERCHANT", "DUPLICATE_ORDER_PAYMENT", "Merchant DUPLICATE", "Order already paid by an earlier transaction"),
            ScenarioEvent(5, "LEDGER", "LEDGER_POSTED", "Ledger SUCCESS", "Second capture posted for the same order"),
            _bank_settled(),
        ),
    ),
    Scenario.REFUND_FAILURE: ScenarioSpec(
        scenario=Scenario.REFUND_FAILURE,
        description="Customer cancelled the order; the refund call to the gateway failed.",
        gateway=G.REFUND_FAILED, bank=B.SETTLED, merchant=M.REFUND_REQUESTED, ledger=L.PENDING, webhook=W.RECEIVED,
        state_path=(P.PROCESSING, P.SUCCESS, P.SETTLED, P.REFUND_PENDING),
        events=(
            _created(),
            _gateway_success(),
            _webhook_received(),
            _bank_settled(5),
            ScenarioEvent(7, "MERCHANT", "REFUND_REQUESTED", "Refund requested", "Customer cancelled order before dispatch"),
            ScenarioEvent(8, "GATEWAY", "REFUND_FAILED", "Refund FAILED", "Refund API: issuer bank unavailable (GATEWAY_ERROR)"),
        ),
        ledger_entries=(("CAPTURE", L.SUCCESS), ("REFUND", L.PENDING)),
    ),
    Scenario.UNKNOWN_STATE: ScenarioSpec(
        scenario=Scenario.UNKNOWN_STATE,
        description="Gateway status is unknown while the bank shows a settlement.",
        gateway=G.UNKNOWN, bank=B.SETTLED, merchant=M.PENDING, ledger=L.PENDING, webhook=W.NOT_RECEIVED,
        state_path=(P.PROCESSING, P.UNKNOWN),
        events=(
            _created(),
            ScenarioEvent(3, "GATEWAY", "GATEWAY_STATUS_UNKNOWN", "Gateway UNKNOWN", "Status API returned inconsistent response"),
            _bank_settled(6),
        ),
        ledger_entries=(("CAPTURE", L.PENDING),),
    ),
}


def get_scenario(scenario: Scenario | str) -> ScenarioSpec:
    return SCENARIOS[Scenario(scenario)]
