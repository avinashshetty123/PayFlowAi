"""Deterministic policy engine.

The AI recommends; this engine decides. The AI's signals (low confidence,
high risk, requires_human) can only make a decision *stricter* — they can
never turn a DENY or HUMAN_APPROVAL_REQUIRED into ALLOW.
"""

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

from app.core.config import settings
from app.core.enums import ActionType, BankStatus as B, GatewayStatus as G, LedgerStatus as L, PolicyDecision, Risk, WebhookStatus as W
from app.models import Payment
from app.schemas.action import PolicyCheck, PolicyEvaluation
from app.services.playbook import allowed_actions, is_compatible
from app.services.risk_service import HUMAN_THRESHOLD
from app.utils.serialization import money

# Policy-as-code catalogue (shown in the console and hashed into every decision).
RULES: list[dict] = [
    {"id": "PB-001", "name": "Playbook compatibility", "effect": "DENY",
     "description": "An action must be in the remediation playbook for the detected incident type; otherwise the AI "
                    "recommendation is overridden by the playbook action."},
    {"id": "PRE-RECON", "name": "Reconcile preconditions", "effect": "DENY",
     "description": "RECONCILE_LEDGER only when the provider captured, funds settled, settled amount equals captured "
                    "amount, and ledger/merchant are out of sync."},
    {"id": "PRE-WEBHOOK", "name": "Webhook re-sync preconditions", "effect": "DENY",
     "description": "RETRY_WEBHOOK only for captured payments whose webhook is not RECEIVED."},
    {"id": "PRE-REFUND", "name": "Refund preconditions", "effect": "DENY",
     "description": "REFUND only for captured, settled, not-yet-refunded payments with a provider capture reference."},
    {"id": "PRE-FAIL", "name": "Mark-failed preconditions", "effect": "DENY",
     "description": "MARK_PAYMENT_FAILED only when no funds settled and the provider did not capture."},
    {"id": "LIM-REFUND", "name": "Refund auto-approve limit", "effect": "HUMAN_APPROVAL_REQUIRED",
     "description": "Refunds above the per-currency limit (USD 25 / INR 5000) need a human."},
    {"id": "SYS-FAULT", "name": "Internal system fault: four-eyes", "effect": "HUMAN_APPROVAL_REQUIRED",
     "description": "When the root cause is a failure inside PayFlow (ledger, merchant order service, webhook intake), "
                    "any fix that writes to the books needs a human sign-off: the component that would apply the "
                    "correction is the one that just failed, so an operator confirms it is healthy first."},
    {"id": "LEDGER-AMT", "name": "Booked amount correction", "effect": "HUMAN_APPROVAL_REQUIRED",
     "description": "If the ledger booked a different amount than the provider settled, the fix is a manual journal "
                    "adjustment and always follows maker-checker, whatever the amount or AI confidence."},
    {"id": "AI-CONF", "name": "AI confidence floor", "effect": "HUMAN_APPROVAL_REQUIRED",
     "description": "Financial actions need AI confidence >= MIN_AUTOMATION_CONFIDENCE."},
    {"id": "AI-FLAG", "name": "AI escalation flags", "effect": "HUMAN_APPROVAL_REQUIRED",
     "description": "AI risk HIGH or requiresHuman makes a decision stricter. The AI can never loosen a decision."},
    {"id": "RISK-SCORE", "name": "Risk score threshold", "effect": "HUMAN_APPROVAL_REQUIRED",
     "description": f"Deterministic risk score >= {HUMAN_THRESHOLD} requires a human."},
    {"id": "CTL-KILL", "name": "Automation kill switch", "effect": "HUMAN_APPROVAL_REQUIRED",
     "description": "When engaged by an operator, every financial action needs a human."},
    {"id": "CTL-BREAKER", "name": "Automation circuit breaker", "effect": "HUMAN_APPROVAL_REQUIRED",
     "description": "Caps automated financial actions per rolling window to contain runaway automation."},
    {"id": "HUMAN-OK", "name": "Human approval", "effect": "ALLOW",
     "description": "A recorded human approval satisfies approval requirements, never failed preconditions."},
]
POLICY_VERSION = "v2-" + hashlib.sha256(json.dumps(RULES, sort_keys=True).encode()).hexdigest()[:10]

DECISION_LABELS = {
    PolicyDecision.ALLOW: "AUTOMATIC ACTION ALLOWED",
    PolicyDecision.HUMAN_APPROVAL_REQUIRED: "HUMAN APPROVAL REQUIRED",
    PolicyDecision.DENY: "ACTION DENIED",
}

INTERNAL_FAULT = "PAYFLOW_INFRASTRUCTURE_FAILURE"

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
    incident_type: str | None = None
    risk_score: int | None = None
    kill_switch: bool = False
    automation_tripped: bool = False
    failure_source: str | None = None

    @property
    def amount_mismatch(self) -> bool:
        return self.ledger_amount is not None and self.bank_amount is not None and self.ledger_amount != self.bank_amount

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
        fired: list[str] = []

        if ctx.incident_type:
            compatible = is_compatible(ctx.incident_type, action)
            checks.insert(0, _check(
                "playbook compatibility", compatible,
                f"{action} for {ctx.incident_type} (allowed: {', '.join(a.value for a in allowed_actions(ctx.incident_type))})",
            ))
            if not compatible:
                decision = PolicyDecision.DENY
                fired.append("PB-001")
                reasons.insert(0, f"{action} is not a playbook action for {ctx.incident_type}")
        if decision == PolicyDecision.DENY and "PB-001" not in fired:
            fired.append({"RECONCILE_LEDGER": "PRE-RECON", "RETRY_WEBHOOK": "PRE-WEBHOOK", "REFUND": "PRE-REFUND",
                          "MARK_PAYMENT_FAILED": "PRE-FAIL"}.get(action, "PB-001"))
        if decision == PolicyDecision.HUMAN_APPROVAL_REQUIRED and action == ActionType.REFUND:
            fired.append("LIM-REFUND")

        if decision == PolicyDecision.ALLOW and action in FINANCIAL_ACTIONS and not ctx.human_approved:
            if ctx.failure_source == INTERNAL_FAULT:
                checks.append(_check("internal component healthy", False,
                                     "root cause is a PayFlow component failure: operator sign-off required"))
                decision = PolicyDecision.HUMAN_APPROVAL_REQUIRED
                fired.append("SYS-FAULT")
                reasons.append("Root cause is a failure inside PayFlow's own systems: four-eyes sign-off required "
                               "before writing to the books")
            if action == ActionType.RECONCILE_LEDGER and ctx.amount_mismatch:
                checks.append(_check("booked amount == settled amount", False,
                                     f"ledger={ctx.ledger_amount} settled={ctx.bank_amount}"))
                decision = PolicyDecision.HUMAN_APPROVAL_REQUIRED
                fired.append("LEDGER-AMT")
                reasons.append(f"Ledger booked {money(ctx.ledger_amount, ctx.currency)} but "
                               f"{money(ctx.bank_amount, ctx.currency)} settled: manual journal adjustment needs maker-checker")
            if ctx.risk_score is not None:
                over = ctx.risk_score >= HUMAN_THRESHOLD
                checks.append(_check("risk score", not over, f"{ctx.risk_score} vs threshold {HUMAN_THRESHOLD}"))
                if over:
                    decision = PolicyDecision.HUMAN_APPROVAL_REQUIRED
                    fired.append("RISK-SCORE")
                    reasons.append(f"Risk score {ctx.risk_score} >= {HUMAN_THRESHOLD}")
            if ctx.kill_switch:
                checks.append(_check("automation kill switch", False, "engaged by operator"))
                decision = PolicyDecision.HUMAN_APPROVAL_REQUIRED
                fired.append("CTL-KILL")
                reasons.append("Automation kill switch engaged")
            if ctx.automation_tripped:
                checks.append(_check("automation circuit breaker", False, "automated action budget exhausted"))
                decision = PolicyDecision.HUMAN_APPROVAL_REQUIRED
                fired.append("CTL-BREAKER")
                reasons.append("Automation circuit breaker tripped")
        if decision == PolicyDecision.ALLOW and action in FINANCIAL_ACTIONS and not ctx.human_approved:
            if ctx.ai_confidence is not None and ctx.ai_confidence < self.min_confidence:
                checks.append(_check("ai_confidence", False, f"{ctx.ai_confidence:.2f} < {self.min_confidence:.2f}"))
                decision = PolicyDecision.HUMAN_APPROVAL_REQUIRED
                fired.append("AI-CONF")
                reasons.append("AI confidence below automation threshold")
            elif ctx.ai_confidence is not None:
                checks.append(_check("ai_confidence", True, f"{ctx.ai_confidence:.2f} >= {self.min_confidence:.2f}"))
            if ctx.ai_requires_human:
                decision = PolicyDecision.HUMAN_APPROVAL_REQUIRED
                fired.append("AI-FLAG")
                reasons.append("Investigator flagged incident for human review")
            if ctx.ai_risk == Risk.HIGH:
                decision = PolicyDecision.HUMAN_APPROVAL_REQUIRED
                fired.append("AI-FLAG")
                reasons.append("Investigator rated risk HIGH")

        if ctx.human_approved and decision == PolicyDecision.HUMAN_APPROVAL_REQUIRED:
            decision = PolicyDecision.ALLOW
            fired.append("HUMAN-OK")
            reasons.append("Human approval recorded")

        if not reasons:
            reasons.append("All policy preconditions satisfied")
        result = self._result(action, decision, reasons, checks)
        result.fired_rules = list(dict.fromkeys(fired))
        result.risk_score = ctx.risk_score
        return result

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
            action_type=str(action), decision=decision, label=DECISION_LABELS[decision], reasons=reasons, checks=checks,
            policy_version=POLICY_VERSION,
        )
