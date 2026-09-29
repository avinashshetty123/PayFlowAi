"""Deterministic policy engine.

The AI recommends; this engine decides. The AI's signals (low confidence,
high risk, requires_human) can only make a decision *stricter* — they can
never turn a DENY or HUMAN_APPROVAL_REQUIRED into ALLOW.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.core.config import settings
from app.core.enums import ActionType, BankStatus as B, GatewayStatus as G, LedgerStatus as L, PolicyDecision, Risk, WebhookStatus as W
from app.models import Payment
from app.schemas.action import PolicyCheck, PolicyEvaluation
from app.utils.serialization import money

DECISION_LABELS = {
    PolicyDecision.ALLOW: "AUTOMATIC ACTION ALLOWED",
    PolicyDecision.HUMAN_APPROVAL_REQUIRED: "HUMAN APPROVAL REQUIRED",
    PolicyDecision.DENY: "ACTION DENIED",
}

FINANCIAL_ACTIONS = frozenset({
    ActionType.RECONCILE_LEDGER, ActionType.RETRY_WEBHOOK, ActionType.REFUND, ActionType.MARK_PAYMENT_FAILED,
})


@dataclass
class PolicyContext:
    gateway: str
    bank: str
    merchant: str
    ledger: str
    webhook: str
    overall: str
    amount: Decimal
    bank_amount: Decimal | None = None
    ledger_amount: Decimal | None = None
    currency: str = "INR"
    provider: str | None = None
    provider_capture_id: str | None = None
    refund_completed: bool = False
    ai_confidence: float | None = None
    ai_risk: str | None = None
    ai_requires_human: bool = False
    human_approved: bool = False

    @classmethod
    def from_payment(cls, payment: Payment, **kwargs) -> "PolicyContext":
        return cls(
            gateway=payment.gateway_status,
            bank=payment.bank_status,
            merchant=payment.merchant_status,
            ledger=payment.ledger_status,
            webhook=payment.webhook_status,
            overall=payment.overall_status,
            amount=payment.amount,
            currency=payment.currency,
            provider=payment.provider,
            provider_capture_id=payment.provider_capture_id,
            **kwargs,
        )


def _check(name: str, passed: bool, detail: str) -> PolicyCheck:
    return PolicyCheck(name=name, passed=passed, detail=detail)


class PolicyEngine:
    def __init__(self, refund_limit: float | None = None, min_confidence: float | None = None):
        # An explicit limit overrides the per-currency configuration (used by tests).
        self._fixed_limit = Decimal(str(refund_limit)) if refund_limit is not None else None
        self.min_confidence = min_confidence if min_confidence is not None else settings.MIN_AUTOMATION_CONFIDENCE

    def evaluate(self, action_type: str, ctx: PolicyContext) -> PolicyEvaluation:
        try:
            action = ActionType(action_type)
        except ValueError:
            return self._result(
                action_type, PolicyDecision.HUMAN_APPROVAL_REQUIRED,
                [f"Unknown action {action_type}: human approval required"],
                [_check("known_action", False, f"{action_type} is not a registered action")],
            )

        checks, decision, reasons = self._preconditions(action, ctx)

        if decision == PolicyDecision.ALLOW and action in FINANCIAL_ACTIONS and not ctx.human_approved:
            if ctx.ai_confidence is not None and ctx.ai_confidence < self.min_confidence:
                checks.append(_check("ai_confidence", False, f"{ctx.ai_confidence:.2f} < {self.min_confidence:.2f}"))
                decision = PolicyDecision.HUMAN_APPROVAL_REQUIRED
                reasons.append("AI confidence below automation threshold")
            elif ctx.ai_confidence is not None:
                checks.append(_check("ai_confidence", True, f"{ctx.ai_confidence:.2f} >= {self.min_confidence:.2f}"))
            if ctx.ai_requires_human:
                decision = PolicyDecision.HUMAN_APPROVAL_REQUIRED
                reasons.append("Investigator flagged incident for human review")
            if ctx.ai_risk == Risk.HIGH:
                decision = PolicyDecision.HUMAN_APPROVAL_REQUIRED
                reasons.append("Investigator rated risk HIGH")

        if ctx.human_approved and decision == PolicyDecision.HUMAN_APPROVAL_REQUIRED:
            decision = PolicyDecision.ALLOW
            reasons.append("Human approval recorded")

        if not reasons:
            reasons.append("All policy preconditions satisfied")
        return self._result(action, decision, reasons, checks)

    def _preconditions(self, action: ActionType, ctx: PolicyContext) -> tuple[list[PolicyCheck], PolicyDecision, list[str]]:
        checks: list[PolicyCheck] = []
        reasons: list[str] = []

        if action == ActionType.RECONCILE_LEDGER:
            checks = [
                _check("gateway == SUCCESS", ctx.gateway == G.SUCCESS, f"gateway={ctx.gateway}"),
                _check("bank == SETTLED", ctx.bank == B.SETTLED, f"bank={ctx.bank}"),
                _check(
                    "ledger/merchant out of sync",
                    ctx.ledger != L.SUCCESS or ctx.merchant != "SUCCESS"
                    or (ctx.ledger_amount is not None and ctx.bank_amount is not None and ctx.ledger_amount != ctx.bank_amount),
                    f"ledger={ctx.ledger} merchant={ctx.merchant}",
                ),
                _check(
                    "settled amount == captured amount",
                    ctx.bank_amount is None or ctx.bank_amount == ctx.amount,
                    f"bank={ctx.bank_amount} captured={ctx.amount}",
                ),
            ]
        elif action == ActionType.RETRY_WEBHOOK:
            checks = [
                _check("gateway == SUCCESS", ctx.gateway == G.SUCCESS, f"gateway={ctx.gateway}"),
                _check("webhook != RECEIVED", ctx.webhook != W.RECEIVED, f"webhook={ctx.webhook}"),
            ]
        elif action == ActionType.REFUND:
            checks = [
                _check("payment captured", ctx.gateway in (G.SUCCESS, G.REFUND_FAILED), f"gateway={ctx.gateway}"),
                _check("bank == SETTLED", ctx.bank == B.SETTLED, f"bank={ctx.bank}"),
                _check("not already refunded", not ctx.refund_completed, f"refund_completed={ctx.refund_completed}"),
            ]
            if ctx.provider == "PAYPAL_SANDBOX":
                checks.append(_check("PayPal capture reference present", bool(ctx.provider_capture_id),
                                     f"capture_id={ctx.provider_capture_id}"))
        elif action == ActionType.MARK_PAYMENT_FAILED:
            checks = [
                _check("no bank settlement", ctx.bank != B.SETTLED, f"bank={ctx.bank}"),
                _check(
                    "gateway not captured",
                    ctx.gateway in (G.TIMEOUT, G.FAILED, G.PENDING, G.UNKNOWN),
                    f"gateway={ctx.gateway}",
                ),
            ]
        elif action == ActionType.ESCALATE:
            checks = [_check("non-financial action", True, "Escalation never moves money")]

        failed = [c for c in checks if not c.passed]
        if failed:
            reasons.extend(f"Precondition failed: {c.name} ({c.detail})" for c in failed)
            return checks, PolicyDecision.DENY, reasons

        if action == ActionType.REFUND:
            limit = self._fixed_limit if self._fixed_limit is not None else Decimal(str(settings.refund_limit(ctx.currency)))
            over = ctx.amount > limit
            checks.append(_check(
                f"amount <= {money(limit, ctx.currency)} auto-approve limit", not over,
                f"amount={money(ctx.amount, ctx.currency)}",
            ))
            if over:
                reasons.append(
                    f"Refund of {money(ctx.amount, ctx.currency)} exceeds {money(limit, ctx.currency)} auto-approve limit"
                )
                return checks, PolicyDecision.HUMAN_APPROVAL_REQUIRED, reasons

        return checks, PolicyDecision.ALLOW, reasons

    @staticmethod
    def _result(action: str, decision: PolicyDecision, reasons: list[str], checks: list[PolicyCheck]) -> PolicyEvaluation:
        return PolicyEvaluation(
            action_type=str(action), decision=decision, label=DECISION_LABELS[decision], reasons=reasons, checks=checks
        )
