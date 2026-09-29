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
from app.schemas.investigation import AIInvestigationResult
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
    return AIInvestigationResult(
        incident_type=incident_type,
        root_cause=root,
        confidence=round(min(confidence, 0.99), 2),
        recommended_action=action,
        risk=risk,
        requires_human=requires_human,
        evidence=evidence,
        summary=summary,
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
