"""AI investigator: gathers evidence with read-only tools, asks Groq for a structured
recommendation, validates it with Pydantic, and falls back to deterministic rules.

The investigator only *recommends*. It never mutates payments, ledgers or refunds.
"""

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import tools
from app.ai.groq_client import GroqClient, GroqUnavailableError
from app.ai.prompts import SYSTEM_PROMPT, build_user_prompt
from app.core.config import settings
from app.core.enums import ActionType, IncidentType, Risk
from app.models import Incident, Payment
from app.schemas.investigation import AIInvestigationResult, ImpactAssessment
from app.utils.serialization import money
from app.services.payment_service import snapshot

logger = logging.getLogger(__name__)

FALLBACK_MODEL = "payflow-deterministic-v1"


@dataclass
class InvestigationOutcome:
    result: AIInvestigationResult
    model: str
    used_fallback: bool
    bundle: dict
    matches: list[dict]
    tool_calls: list[str] = field(default_factory=list)
    fallback_reason: str | None = None
    latency_ms: int = 0


# incident type -> (root cause, action, base confidence, systems cited as evidence, summary, requires human)
_PLAYBOOK: dict[str, tuple[str, ActionType, float, tuple[str, ...], str, bool]] = {
    IncidentType.LEDGER_MISMATCH: (
        "Internal ledger synchronization failure",
        ActionType.RECONCILE_LEDGER, 0.92, ("gateway", "bank", "ledger", "merchant"),
        "Payment successfully settled but internal ledger was not updated.", False,
    ),
    IncidentType.WEBHOOK_DELAY: (
        "Merchant webhook endpoint timed out; gateway delivery retry is pending",
        ActionType.RETRY_WEBHOOK, 0.87, ("gateway", "bank", "webhook", "merchant"),
        "Payment captured and settled, but the webhook that updates order and ledger is delayed.", False,
    ),
    IncidentType.WEBHOOK_LOST: (
        "Webhook delivery failed and gateway retries were exhausted",
        ActionType.RETRY_WEBHOOK, 0.85, ("gateway", "bank", "webhook", "ledger"),
        "Payment captured and settled, but PayFlow never received the webhook.", False,
    ),
    IncidentType.SETTLEMENT_MISMATCH: (
        "Bank settled a different amount than captured (unexpected fee deduction or partial settlement)",
        ActionType.ESCALATE, 0.80, ("gateway", "bank"),
        "Settlement amount disagrees with the captured amount; finance must review before any ledger change.", True,
    ),
    IncidentType.DUPLICATE_PAYMENT: (
        "Customer retried checkout after a client timeout; the same order was captured twice",
        ActionType.REFUND, 0.89, ("gateway", "bank", "merchant"),
        "Duplicate capture detected for an already-paid order; the duplicate should be refunded.", False,
    ),
    IncidentType.REFUND_FAILURE: (
        "Refund API call failed due to a transient issuer bank outage",
        ActionType.REFUND, 0.87, ("gateway", "bank", "merchant"),
        "Refund is still owed to the customer after the gateway refund call failed.", False,
    ),
    IncidentType.GATEWAY_TIMEOUT: (
        "Acquirer did not respond and no funds reached the bank",
        ActionType.MARK_PAYMENT_FAILED, 0.88, ("gateway", "bank", "webhook"),
        "Gateway timed out and the bank shows no debit, so the payment can be safely marked failed.", False,
    ),
    IncidentType.PROVIDER_DECLINED: (
        "Payment provider declined the capture; PayFlow's merchant order was never released",
        ActionType.MARK_PAYMENT_FAILED, 0.88, ("gateway", "merchant", "bank"),
        "The provider (not PayFlow) failed the payment; the open order should be closed as failed.", False,
    ),
    IncidentType.REFUND_REQUESTED: (
        "Merchant cancelled a completed order; the captured amount must be refunded",
        ActionType.REFUND, 0.9, ("gateway", "bank", "merchant"),
        "Capture completed and settled, but the merchant cancelled the order, so the customer is owed a refund.", False,
    ),
    IncidentType.UNKNOWN_STATE: (
        "Gateway status API returned an inconsistent state while the bank shows a settlement",
        ActionType.ESCALATE, 0.62, ("gateway", "bank", "ledger"),
        "Outcome cannot be confirmed from gateway data; a human must verify with the gateway before remediation.", True,
    ),
}


# incident type -> (contributing factors, remediation plan, preventive measures, customer impact)
_DETAIL: dict[str, tuple[list[str], list[str], list[str], str]] = {
    IncidentType.LEDGER_MISMATCH: (
        ["Downstream write executed after the provider capture without a transactional outbox",
         "No automatic retry on the ledger/merchant write path"],
        ["Re-read the authoritative capture amount from the provider", "Post the missing capture entry to the ledger",
         "Mark the merchant order paid", "Verify ledger amount equals the provider capture amount",
         "Re-run five-way reconciliation and close the break"],
        ["Write ledger entries through a transactional outbox", "Alert when ledger lag exceeds 60 seconds"],
        "Customer was charged but the order may not show as paid until the ledger is reconciled.",
    ),
    IncidentType.WEBHOOK_DELAY: (
        ["Webhook intake slow or unavailable", "Order/ledger updates depend solely on webhook delivery"],
        ["Pull the order status from the provider API", "Apply the captured status to merchant and ledger",
         "Verify webhook state is RECEIVED or RESYNCED", "Reconcile all systems"],
        ["Poll provider status when a webhook exceeds its SLA", "Scale webhook intake horizontally"],
        "Customer paid; order confirmation is delayed until the webhook is processed.",
    ),
    IncidentType.WEBHOOK_LOST: (
        ["Webhook dropped before verification or processing", "No reconciliation poll after the grace period"],
        ["Pull the authoritative order status from the provider API", "Mark the webhook as re-synced",
         "Verify capture status and downstream state", "Reconcile all systems"],
        ["Persist raw webhooks before acknowledging", "Run a scheduled status poll for unconfirmed captures"],
        "Customer paid; PayFlow had no confirmation event until it re-synced with the provider.",
    ),
    IncidentType.SETTLEMENT_MISMATCH: (
        ["Provider settled a different amount than PayFlow requested",
         "Possible idempotency-key replay or unexpected fee deduction"],
        ["Freeze automated ledger changes for this payment", "Compare requested vs provider-settled amounts",
         "Escalate to finance with provider debug ids", "Resolve manually once the correct amount is confirmed"],
        ["Make provider idempotency keys globally unique", "Reject provider responses whose amount differs from the request"],
        "Customer may have been charged an amount different from the order total.",
    ),
    IncidentType.DUPLICATE_PAYMENT: (
        ["Client retried checkout after a timeout", "Checkout API accepted a second capture for the same order"],
        ["Identify the original and duplicate captures", "Refund the duplicate capture only",
         "Verify the refund with the provider", "Reconcile the order"],
        ["Enforce idempotency keys on checkout", "Disable the pay button after the first submit"],
        "Customer was charged twice for the same order.",
    ),
    IncidentType.REFUND_FAILURE: (
        ["Refund call failed at the provider", "No automatic refund retry"],
        ["Check refund eligibility", "Retry the refund with an idempotency key", "Verify the refund status with the provider",
         "Close the merchant refund request"],
        ["Retry transient refund failures with backoff", "Alert on refunds pending > 15 minutes"],
        "Customer is still owed money after a failed refund.",
    ),
    IncidentType.REFUND_REQUESTED: (
        ["Merchant cancelled the order after capture"],
        ["Check refund eligibility and amount limits", "Obtain approval if above the auto-approve limit",
         "Execute the refund via the provider API", "Verify the refund and reconcile"],
        ["Offer cancellation before capture where possible"],
        "Customer is waiting for a refund of a cancelled order.",
    ),
    IncidentType.GATEWAY_TIMEOUT: (
        ["Acquirer did not respond within the timeout", "No funds reached the bank"],
        ["Confirm no capture exists at the provider", "Mark the payment failed and release the order",
         "Verify no settlement exists"],
        ["Route to a secondary acquirer on repeated timeouts"],
        "Customer saw a pending payment; no money was taken.",
    ),
    IncidentType.PROVIDER_DECLINED: (
        ["The provider declined the capture", "PayFlow order remained open after the decline"],
        ["Confirm the capture is not completed at the provider", "Mark the payment failed and release the order",
         "Verify with the provider API"],
        ["Close orders automatically on provider decline webhooks"],
        "Customer's payment was declined; the order should be released so they can retry.",
    ),
    IncidentType.UNKNOWN_STATE: (
        ["Provider status ambiguous", "Settlement evidence conflicts with provider status"],
        ["Freeze automation for this payment", "Escalate to payments on-call with provider debug ids",
         "Confirm the outcome with provider support", "Resolve manually"],
        ["Add provider status polling with exponential backoff"],
        "Customer's payment outcome is unknown; they may have been charged.",
    ),
}


SYSTEM_LABELS = {"gateway": "Gateway", "bank": "Bank", "merchant": "Merchant", "ledger": "Ledger", "webhook": "Webhook"}
PAYPAL_LABELS = {"gateway": "PayPal capture", "bank": "PayPal settlement", "merchant": "Merchant order",
                 "ledger": "Ledger", "webhook": "Webhook"}


def deterministic_investigation(
    incident_type: str, snap: dict[str, str], amount: Decimal, matches: list[dict], *,
    currency: str = "INR", provider: str | None = None, provider_status: str | None = None,
    injected_scenario: str | None = None, webhook_verified: bool | None = None,
) -> AIInvestigationResult:
    root, action, base, systems, summary, requires_human = _PLAYBOOK.get(
        incident_type,
        ("Unclassified inconsistency across payment systems", ActionType.ESCALATE, 0.5, ("gateway", "bank"),
         "Unrecognised mismatch pattern; escalating to a human operator.", True),
    )
    top = matches[0] if matches else None
    confidence = base
    if top and top.get("incident_type") == incident_type:
        confidence += 0.05 * float(top.get("similarity", 0))
    risk = Risk.LOW
    if action == ActionType.REFUND:
        risk = Risk.HIGH if amount > Decimal(str(settings.refund_limit(currency))) else Risk.MEDIUM
    elif action == ActionType.ESCALATE:
        risk = Risk.HIGH if incident_type == IncidentType.UNKNOWN_STATE else Risk.MEDIUM
    paypal = provider == "PAYPAL_SANDBOX"
    labels = PAYPAL_LABELS if paypal else SYSTEM_LABELS
    evidence = []
    for system in systems:
        if system not in snap:
            continue
        value = provider_status if (paypal and system == "gateway" and provider_status) else snap[system]
        evidence.append(f"{labels[system]} {value}")
    if paypal and webhook_verified:
        evidence.append("PayPal webhook signature verified")
    if injected_scenario:
        evidence.append(f"Injected PayFlow infrastructure failure: {injected_scenario} (demo)")
        if incident_type == IncidentType.LEDGER_MISMATCH:
            root = "Ledger synchronization failure: PayFlow ledger write failed after a successful PayPal capture"
    if top:
        evidence.append(f"Historical match: {top['title']} ({round(float(top['similarity']) * 100)}%)")
    factors, plan, prevention, customer_impact = _DETAIL.get(
        incident_type, (["Unclassified mismatch"], ["Escalate to payments on-call"], ["Add a playbook"], "Unknown"))
    if injected_scenario:
        factors = [f"Deliberate PayFlow demo failure injection: {injected_scenario}", *factors]
    systemic = sum(1 for m in matches if m.get("incident_type") == incident_type and float(m.get("similarity", 0)) > 0.8)
    return AIInvestigationResult(
        incident_type=incident_type,
        root_cause=root,
        confidence=round(min(confidence, 0.99), 2),
        confidence_rationale=(
            f"Playbook confidence {base:.2f} for {incident_type}"
            + (f", raised by a {round(float(top['similarity']) * 100)}% historical match" if top else "")
            + "; deterministic rules, no model call."
        ),
        recommended_action=action,
        risk=risk,
        requires_human=requires_human,
        evidence=evidence,
        summary=summary,
        impact=ImpactAssessment(
            customer_impact=customer_impact,
            financial_exposure=f"{money(amount, currency)} affected",
            blast_radius=("Single payment; pattern seen before in historical incidents" if systemic
                          else "Single payment; no systemic pattern detected"),
            urgency="IMMEDIATE" if risk == Risk.HIGH else ("HIGH" if action != ActionType.ESCALATE else "NORMAL"),
        ),
        contributing_factors=factors,
        remediation_plan=plan,
        preventive_measures=prevention,
    )


class InvestigatorService:
    def __init__(self, session: AsyncSession, client: GroqClient | None = None):
        self.session = session
        self.client = client or GroqClient()

    async def collect_evidence(self, incident: Incident, payment: Payment) -> tuple[dict, list[dict], list[str]]:
        findings = list(incident.detection_findings or [])
        bundle = {
            "incident": {"number": incident.incident_number, "detected_type": incident.type, "severity": incident.severity,
                         "detection_findings": findings},
            "payment": await tools.get_payment(self.session, payment.transaction_id),
            "gateway": await tools.get_gateway_status(payment),
            "bank": await tools.get_bank_status(self.session, payment),
            "merchant": await tools.get_merchant_status(self.session, payment),
            "ledger": await tools.get_ledger_status(self.session, payment),
            "webhook": {"system": "webhook", "status": payment.webhook_status},
            "timeline": await tools.get_payment_events(self.session, payment),
            "refund_eligibility": await tools.check_refund_eligibility(self.session, payment),
        }
        matches = await tools.search_historical_incidents(self.session, incident.type, payment)
        bundle["provider"] = {
            "name": payment.provider,
            "provider_status": payment.provider_status,
            "order_id": payment.provider_order_id,
            "capture_id": payment.provider_capture_id,
            "currency": payment.currency,
        }
        bundle["failure_injection"] = {
            "failure_source": incident.failure_source,
            "injected_scenario": incident.injected_scenario,
            "note": "Injected failures are deliberate PayFlow demo failures, never PayPal failures.",
        }
        bundle["webhooks"] = await tools.get_webhook_events(self.session, payment)
        bundle["customer_history"] = await tools.get_customer_history(self.session, payment)
        bundle["risk_score"] = (incident.risk_factors or {}).get("score_breakdown") if hasattr(incident, "risk_factors") else None
        bundle["historical_matches"] = [
            {k: m[k] for k in ("title", "incident_type", "similarity", "root_cause", "resolution")} for m in matches
        ]
        return bundle, matches, list(tools.TOOL_NAMES)

    async def investigate(self, incident: Incident, payment: Payment, *, force_fallback: bool = False) -> InvestigationOutcome:
        started = time.perf_counter()
        bundle, matches, tool_calls = await self.collect_evidence(incident, payment)
        snap = snapshot(payment)

        fallback_reason: str | None = None
        if force_fallback:
            fallback_reason = "Fallback forced"
        elif not self.client.configured:
            fallback_reason = "GROQ_API_KEY not configured"
        else:
            try:
                raw = await self.client.chat_json(SYSTEM_PROMPT, build_user_prompt(bundle))
                result = AIInvestigationResult.model_validate(raw)
                return InvestigationOutcome(
                    result=result,
                    model=f"groq:{self.client.model}",
                    used_fallback=False,
                    bundle=bundle,
                    matches=matches,
                    tool_calls=tool_calls,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            except GroqUnavailableError as exc:
                fallback_reason = str(exc)
            except ValidationError as exc:
                fallback_reason = f"Groq output failed schema validation ({exc.error_count()} errors)"
            logger.warning("Groq investigation fell back to deterministic rules: %s", fallback_reason)

        webhooks = bundle.get("webhooks") or []
        result = deterministic_investigation(
            incident.type, snap, payment.amount, matches, currency=payment.currency, provider=payment.provider,
            provider_status=payment.provider_status, injected_scenario=incident.injected_scenario,
            webhook_verified=any(w.get("signature_verified") for w in webhooks),
        )
        return InvestigationOutcome(
            result=result,
            model=FALLBACK_MODEL,
            used_fallback=True,
            bundle=bundle,
            matches=matches,
            tool_calls=tool_calls,
            fallback_reason=fallback_reason,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
