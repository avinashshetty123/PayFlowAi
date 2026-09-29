"""Action executor: the ONLY place remediation mutates financial state.

- PayPal Sandbox payments: refunds call the real PayPal Sandbox refund API and a
  missing webhook is replaced by a PayPal order-status pull. Historical/synthetic
  payments use simulated side effects. No live money ever moves.
- Idempotency: one action per (type, transaction), enforced by a unique DB constraint.
- Execution is claimed atomically (APPROVED -> EXECUTING), so a second request
  returns the previous result instead of executing twice.
"""

import logging
import random
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import (
    ActionStatus,
    ActionType,
    AuditEvent,
    BankStatus,
    GatewayStatus,
    LedgerStatus,
    MerchantStatus,
    PaymentState,
    PolicyDecision,
    WebhookStatus,
)
from app.core.errors import ActionNotExecutableError, NotFoundError
from app.models import Action, Incident, LedgerEntry, Payment, ProviderTransaction
from app.models.base import utcnow
from app.schemas.action import PolicyEvaluation
from app.services.audit_service import AuditService
from app.services.payment_service import PaymentService, PaymentStateService, snapshot
from app.utils.idempotency import make_idempotency_key, paypal_refund_key
from app.utils.serialization import money, to_jsonable

logger = logging.getLogger(__name__)

EXECUTOR_ACTOR = "action-executor"


@dataclass
class ExecutionOutcome:
    action: Action
    success: bool
    deduplicated: bool = False
    result: dict = field(default_factory=dict)
    error: str | None = None


class ActionExecutor:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.payments = PaymentService(session)
        self.audit = AuditService(session)

    # ---- request ---------------------------------------------------------------------

    async def request(
        self,
        *,
        incident: Incident,
        payment: Payment,
        action_type: str,
        policy: PolicyEvaluation,
        requested_by: str,
        reason: str,
    ) -> tuple[Action, bool]:
        """Create (or return the existing) action for this incident. Returns (action, created)."""
        key = make_idempotency_key(action_type, payment.transaction_id)
        existing = await self.session.scalar(select(Action).where(Action.idempotency_key == key))
        if existing is not None:
            return existing, False

        status = ActionStatus.APPROVED if policy.decision == PolicyDecision.ALLOW else ActionStatus.PENDING_APPROVAL
        action = Action(
            incident_id=incident.id,
            action_type=str(action_type),
            idempotency_key=key,
            status=str(status),
            requested_by=requested_by,
            reason=reason,
            policy=to_jsonable(policy.model_dump()),
            created_at=utcnow(),
        )
        try:
            async with self.session.begin_nested():
                self.session.add(action)
                await self.session.flush()
        except IntegrityError:
            # Lost a race with a concurrent request: the unique constraint wins.
            existing = await self.session.scalar(select(Action).where(Action.idempotency_key == key))
            if existing is None:
                raise
            return existing, False
        return action, True

    # ---- execute ---------------------------------------------------------------------

    async def execute(self, action_id: uuid.UUID, *, actor: str = EXECUTOR_ACTOR) -> ExecutionOutcome:
        claimed = await self.session.execute(
            update(Action)
            .where(Action.id == action_id, Action.status == str(ActionStatus.APPROVED))
            .values(status=str(ActionStatus.EXECUTING))
            .returning(Action.id)
            .execution_options(synchronize_session=False)
        )
        claimed_id = claimed.scalar_one_or_none()
        action = await self.session.get(Action, action_id, populate_existing=True)
        if action is None:
            raise NotFoundError(f"Action {action_id} not found")
        incident = await self.session.get(Incident, action.incident_id)
        payment = await self.payments.reload(incident.payment_id)

        if claimed_id is None:
            if action.status == ActionStatus.COMPLETED:
                await self.audit.record(
                    transaction_id=payment.transaction_id,
                    incident_id=incident.id,
                    event=AuditEvent.ACTION_DEDUPLICATED,
                    actor=actor,
                    reason=f"Duplicate request for {action.idempotency_key}; returning previous result",
                    evidence={"idempotency_key": action.idempotency_key},
                    result={"previous_status": action.status},
                )
                return ExecutionOutcome(action=action, success=True, deduplicated=True, result=action.result or {})
            raise ActionNotExecutableError(f"Action {action.idempotency_key} is {action.status}, not APPROVED")

        handler = {
            ActionType.RECONCILE_LEDGER: self._reconcile_ledger,
            ActionType.RETRY_WEBHOOK: self._retry_webhook,
            ActionType.REFUND: self._refund,
            ActionType.MARK_PAYMENT_FAILED: self._mark_failed,
            ActionType.ESCALATE: self._escalate,
        }.get(action.action_type)

        before = snapshot(payment)
        try:
            if handler is None:
                raise ActionNotExecutableError(f"No executor registered for {action.action_type}")
            async with self.session.begin_nested():
                details = await handler(payment, action)
                await self.session.flush()
        except Exception as exc:  # noqa: BLE001 - failures are recorded, never swallowed silently
            logger.exception("Action %s failed", action.idempotency_key)
            payment = await self.payments.reload(payment.id)
            action.status = str(ActionStatus.FAILED)
            action.completed_at = utcnow()
            action.result = {"error": str(exc), "before": before, "after": snapshot(payment)}
            await self.audit.record(
                transaction_id=payment.transaction_id,
                incident_id=incident.id,
                event=AuditEvent.ACTION_FAILED,
                actor=actor,
                reason=f"{action.action_type} failed: {exc}",
                evidence={"idempotency_key": action.idempotency_key, "before": before},
                result=action.result,
            )
            await self.session.flush()
            return ExecutionOutcome(action=action, success=False, result=action.result, error=str(exc))

        after = snapshot(payment)
        changes = [{"system": k, "from": before[k], "to": after[k]} for k in before if before[k] != after[k]]
        result = {"simulated": True, "before": before, "after": after, "changes": changes, **details}
        action.status = str(ActionStatus.COMPLETED)
        action.completed_at = utcnow()
        action.result = to_jsonable(result)
        await self.audit.record(
            transaction_id=payment.transaction_id,
            incident_id=incident.id,
            event=AuditEvent.ACTION_EXECUTED,
            actor=actor,
            reason=details.get("summary", f"{action.action_type} executed"),
            evidence={"action_type": action.action_type, "idempotency_key": action.idempotency_key,
                      "before": before, "approved_by": action.approved_by},
            result={"after": after, "changes": changes},
        )
        await self.session.flush()
        emit_action = {"action_type": action.action_type, "idempotency_key": action.idempotency_key}
        return ExecutionOutcome(action=action, success=True, result={**action.result, **emit_action})

    # ---- handlers (simulated side effects) ----------------------------------------------

    async def _event(self, payment: Payment, source: str, event_type: str, label: str, detail: str) -> None:
        await self.payments.add_event(
            payment, source=source, event_type=event_type, label=label,
            payload={"detail": detail, "simulated": True, "actor": EXECUTOR_ACTOR},
        )

    async def _capture_entries(self, payment: Payment) -> list[LedgerEntry]:
        return [e for e in await self.payments.ledger_entries(payment.id) if e.entry_type == "CAPTURE"]

    async def _reconcile_ledger(self, payment: Payment, action: Action) -> dict:
        bank_txns = await self.payments.bank_transactions(payment.id)
        settled_amount = bank_txns[-1].amount if bank_txns else payment.amount
        captures = await self._capture_entries(payment)
        if captures:
            for entry in captures:
                entry.status = str(LedgerStatus.SUCCESS)
                entry.amount = settled_amount
                entry.updated_at = utcnow()
        else:
            self.session.add(LedgerEntry(payment_id=payment.id, amount=settled_amount, entry_type="CAPTURE",
                                         status=str(LedgerStatus.SUCCESS)))
        for order in await self.payments.merchant_transactions(payment.id):
            order.status = str(MerchantStatus.SUCCESS)
        payment.ledger_status = str(LedgerStatus.SUCCESS)
        payment.merchant_status = str(MerchantStatus.SUCCESS)
        source = "PayPal capture record" if self._is_paypal(payment) else "bank settlement record"
        amount_text = money(settled_amount, payment.currency)
        await self._event(payment, "LEDGER", "LEDGER_RECONCILED", "Ledger reconciled",
                          f"Capture of {amount_text} posted from {source}")
        return {"summary": f"Ledger reconciled: capture of {amount_text} posted from {source}, merchant order marked paid",
                "ledger_amount": settled_amount}

    @staticmethod
    def _is_paypal(payment: Payment) -> bool:
        return payment.provider == "PAYPAL_SANDBOX"

    async def _retry_webhook(self, payment: Payment, action: Action) -> dict:
        if self._is_paypal(payment):
            return await self._resync_from_paypal(payment)
        payment.webhook_status = str(WebhookStatus.RECEIVED)
        payment.merchant_status = str(MerchantStatus.SUCCESS)
        payment.ledger_status = str(LedgerStatus.SUCCESS)
        for entry in await self._capture_entries(payment):
            entry.status = str(LedgerStatus.SUCCESS)
            entry.updated_at = utcnow()
        for order in await self.payments.merchant_transactions(payment.id):
            order.status = str(MerchantStatus.SUCCESS)
        if payment.overall_status in (PaymentState.PENDING, PaymentState.PROCESSING, PaymentState.UNKNOWN):
            PaymentStateService.transition(payment, PaymentState.SUCCESS)
        if payment.overall_status == PaymentState.SUCCESS and payment.bank_status == BankStatus.SETTLED:
            PaymentStateService.transition(payment, PaymentState.SETTLED)
        await self._event(payment, "WEBHOOK", "WEBHOOK_REDELIVERED", "Webhook redelivered",
                          "payment.captured re-sent from gateway status API (HTTP 200)")
        return {"summary": "Webhook redelivered; merchant order and ledger updated", "delivery_attempt": "manual-retry"}

    async def _resync_from_paypal(self, payment: Payment) -> dict:
        """Replace a missing webhook with an authoritative PayPal order-status pull."""
        from app.payments import get_provider

        order = await get_provider().get_order(payment.provider_order_id)
        self.session.add(ProviderTransaction(
            payment_id=payment.id, provider=payment.provider, kind="STATUS_SYNC", provider_reference=order.order_id,
            status=order.capture_status or order.status, amount=order.amount, currency=order.currency,
            debug_id=order.debug_id, response=to_jsonable(order.raw),
        ))
        if order.capture_status != "COMPLETED":
            raise RuntimeError(f"PayPal reports capture status {order.capture_status or order.status}; cannot re-sync")
        payment.webhook_status = str(WebhookStatus.RESYNCED)
        payment.provider_status = order.capture_status
        await self._event(payment, "WEBHOOK", "WEBHOOK_RESYNCED", "Webhook re-synced via PayPal API",
                          f"GET /v2/checkout/orders/{order.order_id} → capture {order.capture_status}")
        return {"summary": f"Missing webhook replaced by PayPal order-status pull (capture {order.capture_status})",
                "provider_order_status": order.status, "paypal_debug_id": order.debug_id}

    async def _refund(self, payment: Payment, action: Action) -> dict:
        if self._is_paypal(payment):
            refund_ref = await self._refund_via_paypal(payment)
        else:
            refund_ref = f"rfnd_sim_{random.randint(10**9, 10**10 - 1)}"
        refunds = [e for e in await self.payments.ledger_entries(payment.id) if e.entry_type == "REFUND"]
        if refunds:
            for entry in refunds:
                entry.status = str(LedgerStatus.SUCCESS)
                entry.updated_at = utcnow()
        else:
            self.session.add(LedgerEntry(payment_id=payment.id, amount=payment.amount, entry_type="REFUND",
                                         status=str(LedgerStatus.SUCCESS)))
        for txn in await self.payments.bank_transactions(payment.id):
            txn.status = str(BankStatus.REFUNDED)
        for order in await self.payments.merchant_transactions(payment.id):
            order.status = str(MerchantStatus.REFUNDED)
        payment.gateway_status = str(GatewayStatus.REFUNDED)
        payment.bank_status = str(BankStatus.REFUNDED)
        payment.merchant_status = str(MerchantStatus.REFUNDED)
        payment.ledger_status = str(LedgerStatus.REFUNDED)
        if payment.overall_status in (PaymentState.SUCCESS, PaymentState.SETTLED):
            PaymentStateService.transition(payment, PaymentState.REFUND_PENDING)
        PaymentStateService.transition(payment, PaymentState.REFUNDED)
        amount_text = money(payment.amount, payment.currency)
        where = "PayPal Sandbox refund" if self._is_paypal(payment) else "Simulated refund"
        await self._event(payment, "GATEWAY", "REFUND_PROCESSED", "Refund processed",
                          f"{where} {refund_ref} of {amount_text} completed")
        return {"summary": f"{where} of {amount_text} executed ({refund_ref})", "refund_reference": refund_ref,
                "refund_amount": payment.amount}

    async def _refund_via_paypal(self, payment: Payment) -> str:
        from app.payments import get_provider

        if not payment.provider_capture_id:
            raise RuntimeError("No PayPal capture id recorded; refund impossible")
        key = paypal_refund_key(payment.provider_capture_id)
        refund = await get_provider().refund_payment(
            payment.provider_capture_id, amount=payment.amount, currency=payment.currency, idempotency_key=key,
            note=f"Refund for {payment.transaction_id}",
        )
        self.session.add(ProviderTransaction(
            payment_id=payment.id, provider=payment.provider, kind="REFUND", provider_reference=refund.refund_id,
            status=refund.status, amount=refund.amount, currency=refund.currency, idempotency_key=key,
            debug_id=refund.debug_id, response=to_jsonable(refund.raw),
        ))
        if refund.status != "COMPLETED":
            raise RuntimeError(f"PayPal refund {refund.refund_id} is {refund.status}, not COMPLETED")
        payment.provider_status = "REFUNDED"
        payment.provider_metadata = {**(payment.provider_metadata or {}), "refund_id": refund.refund_id}
        return refund.refund_id

    async def _mark_failed(self, payment: Payment, action: Action) -> dict:
        PaymentStateService.transition(payment, PaymentState.FAILED)
        payment.merchant_status = str(MerchantStatus.FAILED)
        payment.ledger_status = str(LedgerStatus.FAILED)
        for entry in await self._capture_entries(payment):
            entry.status = str(LedgerStatus.FAILED)
            entry.updated_at = utcnow()
        for order in await self.payments.merchant_transactions(payment.id):
            order.status = str(MerchantStatus.FAILED)
        detail = ("PayPal did not complete the capture; merchant order released" if self._is_paypal(payment)
                  else "No bank debit found; order released, customer may retry")
        await self._event(payment, "PAYFLOW", "PAYMENT_MARKED_FAILED", "Payment marked FAILED", detail)
        return {"summary": f"Payment marked failed: {detail}"}

    async def _escalate(self, payment: Payment, action: Action) -> dict:
        ticket = f"OPS-{random.randint(1000, 9999)}"
        await self._event(payment, "PAYFLOW", "ESCALATED", "Escalated to on-call", f"Ticket {ticket} opened for payments-oncall")
        return {"summary": f"Escalated to payments on-call ({ticket})", "ticket": ticket, "escalated_to": "payments-oncall"}
