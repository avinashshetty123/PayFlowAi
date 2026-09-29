"""Post-action verification: re-read every system and confirm the expected end state."""

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ActionType, BankStatus, GatewayStatus, LedgerStatus, MerchantStatus, PaymentState, WebhookStatus
from app.services.payment_service import PaymentService, snapshot


@dataclass
class VerificationResult:
    status: str  # PASSED | FAILED | NOT_APPLICABLE
    checks: list[dict] = field(default_factory=list)
    snapshot: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == "PASSED"

    def as_dict(self) -> dict:
        return {"status": self.status, "checks": self.checks, "snapshot": self.snapshot}


def _eq(name: str, actual, expected) -> dict:
    return {"name": name, "expected": str(expected), "actual": str(actual), "passed": str(actual) == str(expected)}


def _ne(name: str, actual, forbidden) -> dict:
    return {"name": name, "expected": f"!= {forbidden}", "actual": str(actual), "passed": str(actual) != str(forbidden)}


class VerificationService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.payments = PaymentService(session)

    async def verify(self, action_type: str, payment_id: uuid.UUID) -> VerificationResult:
        payment = await self.payments.reload(payment_id)  # fresh read, never trust cached state
        snap = snapshot(payment)
        if action_type == ActionType.ESCALATE:
            return VerificationResult(status="NOT_APPLICABLE", snapshot=snap)

        ledger = await self.payments.ledger_entries(payment.id)
        bank = await self.payments.bank_transactions(payment.id)
        checks: list[dict] = []

        if action_type == ActionType.RECONCILE_LEDGER:
            capture = next((e for e in ledger if e.entry_type == "CAPTURE" and e.status == LedgerStatus.SUCCESS), None)
            checks = [
                _eq("gateway", payment.gateway_status, GatewayStatus.SUCCESS),
                _eq("bank", payment.bank_status, BankStatus.SETTLED),
                _eq("merchant", payment.merchant_status, MerchantStatus.SUCCESS),
                _eq("ledger", payment.ledger_status, LedgerStatus.SUCCESS),
                {
                    "name": "ledger amount == settled amount",
                    "expected": str(bank[-1].amount) if bank else "n/a",
                    "actual": str(capture.amount) if capture else "missing",
                    "passed": bool(capture and bank and capture.amount == bank[-1].amount),
                },
            ]
        elif action_type == ActionType.RETRY_WEBHOOK:
            checks = [
                {"name": "webhook", "expected": "RECEIVED or RESYNCED", "actual": payment.webhook_status,
                 "passed": payment.webhook_status in (WebhookStatus.RECEIVED, WebhookStatus.RESYNCED)},
                _eq("merchant", payment.merchant_status, MerchantStatus.SUCCESS),
                _eq("ledger", payment.ledger_status, LedgerStatus.SUCCESS),
                _eq("overall", payment.overall_status,
                    PaymentState.SETTLED if payment.bank_status == BankStatus.SETTLED else PaymentState.SUCCESS),
            ]
        elif action_type == ActionType.REFUND:
            refund = next((e for e in ledger if e.entry_type == "REFUND" and e.status == LedgerStatus.SUCCESS), None)
            checks = [
                _eq("gateway", payment.gateway_status, GatewayStatus.REFUNDED),
                _eq("overall", payment.overall_status, PaymentState.REFUNDED),
                {"name": "refund ledger entry posted", "expected": "SUCCESS",
                 "actual": refund.status if refund else "missing", "passed": refund is not None},
            ]
        elif action_type == ActionType.MARK_PAYMENT_FAILED:
            checks = [
                _eq("overall", payment.overall_status, PaymentState.FAILED),
                _ne("bank", payment.bank_status, BankStatus.SETTLED),
                _ne("ledger", payment.ledger_status, LedgerStatus.SUCCESS),
            ]
        else:
            checks = [{"name": "known action", "expected": "registered", "actual": action_type, "passed": False}]

        if payment.provider == "PAYPAL_SANDBOX":
            checks.extend(await self._provider_checks(action_type, payment, ledger))

        status = "PASSED" if checks and all(c["passed"] for c in checks) else "FAILED"
        return VerificationResult(status=status, checks=checks, snapshot=snap)

    async def _provider_checks(self, action_type: str, payment, ledger) -> list[dict]:
        """Independent confirmation from PayPal Sandbox, not just PayFlow's own tables."""
        from app.payments import ProviderError, get_provider

        provider = get_provider()
        try:
            if action_type == ActionType.RECONCILE_LEDGER and payment.provider_capture_id:
                capture = await provider.get_capture(payment.provider_capture_id)
                capture_entry = next((e for e in ledger if e.entry_type == "CAPTURE"), None)
                return [
                    _eq("PayPal capture status", capture["status"], "COMPLETED"),
                    {"name": "ledger amount == PayPal capture amount", "expected": str(capture["amount"]),
                     "actual": str(capture_entry.amount) if capture_entry else "missing",
                     "passed": bool(capture_entry and capture_entry.amount == capture["amount"])},
                ]
            if action_type == ActionType.REFUND:
                refund_id = (payment.provider_metadata or {}).get("refund_id")
                if not refund_id:
                    return [{"name": "PayPal refund recorded", "expected": "refund id", "actual": "missing", "passed": False}]
                refund = await provider.get_refund(refund_id)
                return [_eq("PayPal refund status", refund["status"], "COMPLETED")]
            if action_type == ActionType.RETRY_WEBHOOK and payment.provider_order_id:
                order = await provider.get_order(payment.provider_order_id)
                return [_eq("PayPal capture status (API)", order.capture_status, "COMPLETED")]
            if action_type == ActionType.MARK_PAYMENT_FAILED and payment.provider_order_id:
                order = await provider.get_order(payment.provider_order_id)
                return [_ne("PayPal capture status (API)", order.capture_status, "COMPLETED")]
        except ProviderError as exc:
            return [{"name": "PayPal re-check", "expected": "reachable", "actual": exc.message[:120], "passed": False}]
        return []
