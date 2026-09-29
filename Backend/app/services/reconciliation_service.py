"""Deterministic five-way reconciliation: Gateway / Bank / Merchant / Ledger / Webhook.

No AI here. This engine *detects* inconsistencies; the AI investigator only
explains them afterwards.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.enums import (
    BankStatus as B,
    GatewayStatus as G,
    IncidentType,
    LedgerStatus as L,
    MerchantStatus as M,
    Severity,
    WebhookStatus as W,
)
from app.models import BankTransaction, LedgerEntry, MerchantTransaction, Payment
from app.services.payment_service import snapshot
from app.utils.serialization import money


@dataclass
class ReconciliationContext:
    amount: Decimal
    bank_amount: Decimal | None = None
    duplicate_of: str | None = None
    currency: str = "INR"
    ledger_amount: Decimal | None = None
    provider_label: str = "Gateway"


@dataclass
class ReconciliationResult:
    consistent: bool
    snapshot: dict[str, str]
    incident_type: IncidentType | None = None
    severity: Severity | None = None
    summary: str = "All systems consistent"
    findings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "consistent": self.consistent,
            "incident_type": str(self.incident_type) if self.incident_type else None,
            "severity": str(self.severity) if self.severity else None,
            "summary": self.summary,
            "findings": self.findings,
            "snapshot": self.snapshot,
        }


def _findings(snap: dict[str, str], ctx: ReconciliationContext) -> list[str]:
    bank = f"Settlement {snap['bank']}" if ctx.provider_label != "Gateway" else f"Bank {snap['bank']}"
    if ctx.bank_amount is not None:
        bank += f" ({money(ctx.bank_amount, ctx.currency)})"
    return [
        f"{ctx.provider_label} {snap['gateway']}",
        bank,
        f"Merchant {snap['merchant']}",
        f"Ledger {snap['ledger']}",
        f"Webhook {snap['webhook']}",
    ]


def compare(snap: dict[str, str], ctx: ReconciliationContext) -> ReconciliationResult:
    """Pure rule table. Order matters: the most specific / most dangerous rule wins."""
    gateway, bank, merchant, ledger, webhook = (
        snap["gateway"], snap["bank"], snap["merchant"], snap["ledger"], snap["webhook"]
    )
    facts = _findings(snap, ctx)

    def mismatch(kind: IncidentType, severity: Severity, summary: str, *extra: str) -> ReconciliationResult:
        return ReconciliationResult(
            consistent=False, snapshot=snap, incident_type=kind, severity=severity,
            summary=summary, findings=[*facts, *extra],
        )

    if ctx.duplicate_of and gateway == G.SUCCESS:
        return mismatch(
            IncidentType.DUPLICATE_PAYMENT, Severity.HIGH,
            f"Order already paid by {ctx.duplicate_of}; customer charged twice",
            f"Duplicate of {ctx.duplicate_of}",
        )
    over_limit = ctx.amount > Decimal(str(settings.refund_limit(ctx.currency)))
    if gateway == G.REFUND_FAILED:
        severity = Severity.HIGH if over_limit else Severity.MEDIUM
        return mismatch(IncidentType.REFUND_FAILURE, severity, "Refund requested by merchant but gateway refund failed")
    if merchant == M.REFUND_REQUESTED and gateway == G.SUCCESS:
        return mismatch(
            IncidentType.REFUND_REQUESTED, Severity.HIGH if over_limit else Severity.MEDIUM,
            f"Merchant cancelled the order; refund of {money(ctx.amount, ctx.currency)} is owed to the customer",
        )
    # Once PayFlow has deterministically closed the payment as FAILED, an unresolved
    # gateway report is no longer a live inconsistency.
    closed_as_failed = snap.get("overall") == "FAILED" and bank != B.SETTLED
    if gateway == G.UNKNOWN and not closed_as_failed:
        severity = Severity.CRITICAL if bank == B.SETTLED else Severity.HIGH
        return mismatch(IncidentType.UNKNOWN_STATE, severity, "Gateway state unknown; cannot confirm payment outcome")
    if gateway == G.TIMEOUT and not closed_as_failed:
        return mismatch(IncidentType.GATEWAY_TIMEOUT, Severity.MEDIUM, "Gateway timed out; payment outcome unresolved")
    if gateway == G.FAILED and bank == B.SETTLED:
        return mismatch(
            IncidentType.SETTLEMENT_MISMATCH, Severity.CRITICAL, "Bank settled funds for a payment the gateway marked failed"
        )
    if gateway == G.FAILED and snap.get("overall") != "FAILED":
        return mismatch(
            IncidentType.PROVIDER_DECLINED, Severity.MEDIUM,
            "Payment provider declined the capture but the merchant order is still open",
        )
    if gateway == G.SUCCESS and bank == B.SETTLED and ctx.bank_amount is not None and ctx.bank_amount != ctx.amount:
        return mismatch(
            IncidentType.SETTLEMENT_MISMATCH, Severity.HIGH,
            f"Settled {money(ctx.bank_amount, ctx.currency)} but captured {money(ctx.amount, ctx.currency)}",
            f"Settlement difference {money(ctx.amount - ctx.bank_amount, ctx.currency)}",
        )
    if gateway == G.SUCCESS and webhook == W.DELAYED:
        return mismatch(IncidentType.WEBHOOK_DELAY, Severity.LOW, "Captured payment awaiting delayed webhook delivery")
    if gateway == G.SUCCESS and webhook == W.NOT_RECEIVED:
        return mismatch(IncidentType.WEBHOOK_LOST, Severity.MEDIUM, "Captured payment never delivered via webhook")
    ledger_amount_wrong = (
        ledger == L.SUCCESS and ctx.ledger_amount is not None and ctx.bank_amount is not None
        and ctx.ledger_amount != ctx.bank_amount
    )
    if gateway == G.SUCCESS and bank == B.SETTLED and (ledger != L.SUCCESS or merchant != M.SUCCESS or ledger_amount_wrong):
        extra = []
        if ledger_amount_wrong:
            extra.append(f"Ledger posted {money(ctx.ledger_amount, ctx.currency)} vs settled {money(ctx.bank_amount, ctx.currency)}")
        return mismatch(
            IncidentType.LEDGER_MISMATCH, Severity.HIGH,
            "Payment settled but internal ledger/merchant records disagree", *extra,
        )
    return ReconciliationResult(consistent=True, snapshot=snap, findings=facts)


class ReconciliationService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def build_context(self, payment: Payment) -> ReconciliationContext:
        bank_amount = await self.session.scalar(
            select(BankTransaction.amount)
            .where(BankTransaction.payment_id == payment.id)
            .order_by(BankTransaction.created_at.desc())
            .limit(1)
        )
        duplicate_of = None
        order_id = await self.session.scalar(
            select(MerchantTransaction.order_id).where(MerchantTransaction.payment_id == payment.id).limit(1)
        )
        if order_id:
            duplicate_of = await self.session.scalar(
                select(Payment.transaction_id)
                .join(MerchantTransaction, MerchantTransaction.payment_id == Payment.id)
                .where(
                    and_(
                        MerchantTransaction.order_id == order_id,
                        Payment.id != payment.id,
                        Payment.customer_id == payment.customer_id,
                        Payment.created_at < payment.created_at,
                        Payment.gateway_status.in_([G.SUCCESS, G.REFUNDED]),
                    )
                )
                .order_by(Payment.created_at)
                .limit(1)
            )
        ledger_amount = await self.session.scalar(
            select(LedgerEntry.amount)
            .where(LedgerEntry.payment_id == payment.id, LedgerEntry.entry_type == "CAPTURE")
            .order_by(LedgerEntry.created_at.desc())
            .limit(1)
        )
        return ReconciliationContext(
            amount=payment.amount, bank_amount=bank_amount, duplicate_of=duplicate_of, currency=payment.currency,
            ledger_amount=ledger_amount,
            provider_label="PayPal" if payment.provider == "PAYPAL_SANDBOX" else "Gateway",
        )

    async def reconcile(self, payment: Payment) -> ReconciliationResult:
        ctx = await self.build_context(payment)
        # A refunded duplicate has already been remediated.
        if payment.gateway_status == G.REFUNDED:
            ctx.duplicate_of = None
        return compare(snapshot(payment), ctx)
