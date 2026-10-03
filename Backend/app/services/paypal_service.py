"""PayPal Sandbox payment lifecycle inside PayFlow.

    PayPal Sandbox (real API calls)
          │  create order → buyer approves → capture → webhook
          ▼
    PayFlow event ingestion (this module)
          │
          ▼
    Failure-injection layer (demo; PayFlow infrastructure only)
          │
          ▼
    Merchant order service + internal ledger
          │
          ▼
    Deterministic reconciliation → incidents → AI → policy → action → verify

PayPal's responses are recorded exactly as returned. Injected failures only ever
affect PayFlow's own downstream systems and are labelled as such.
"""

import json
import logging
import random
import uuid
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.enums import (
    AuditEvent,
    BankStatus,
    GatewayStatus,
    LedgerStatus,
    MerchantStatus,
    PaymentState,
    Provider,
    WebhookStatus,
)
from app.core.errors import ConflictError, DomainError, NotFoundError
from app.failure_injection.scenarios import SCENARIOS, FailureScenario
from app.failure_injection.service import FailureInjectionService
from app.models import BankTransaction, LedgerEntry, MerchantTransaction, Payment, ProviderTransaction, WebhookEvent
from app.models.base import utcnow
from app.payments import ProviderError, get_provider
from app.payments.sandbox import mock_response_header
from app.services.audit_service import AuditService
from app.services.payment_service import PaymentService, PaymentStateService
from app.services.simulator_service import SimulatorService
from app.utils.idempotency import paypal_capture_key, paypal_order_key
from app.utils.serialization import money, to_jsonable

logger = logging.getLogger(__name__)

CONNECTOR = "paypal-connector"
WEBHOOK_ACTOR = "paypal-webhook"

DEMOS = ("PAYMENT_ONLY", "LEDGER_MISMATCH", "REFUND_REQUIRES_APPROVAL")
HERO_TRANSACTIONS = {"LEDGER_MISMATCH": "TXN92831", "REFUND_REQUIRES_APPROVAL": "TXN92842"}

HANDLED_WEBHOOKS = (
    "CHECKOUT.ORDER.APPROVED",
    "PAYMENT.CAPTURE.COMPLETED",
    "PAYMENT.CAPTURE.DENIED",
    "PAYMENT.CAPTURE.PENDING",
    "PAYMENT.CAPTURE.REFUNDED",
    "CHECKOUT.PAYMENT-APPROVAL.REVERSED",
)


class ProviderUnavailableError(DomainError):
    status_code = 502


def _q(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _initial_webhook_status() -> str:
    return str(WebhookStatus.AWAITING if settings.paypal_webhooks_enabled else WebhookStatus.NOT_CONFIGURED)


async def find_paypal_payment(
    session: AsyncSession, *, transaction_id: str | None = None, order_id: str | None = None,
    capture_id: str | None = None, lock: bool = False,
) -> Payment | None:
    conditions = []
    if transaction_id:
        conditions.append(Payment.transaction_id == transaction_id)
    if order_id:
        conditions.append(Payment.provider_order_id == order_id)
    if capture_id:
        conditions.append(Payment.provider_capture_id == capture_id)
    if not conditions:
        return None
    stmt = select(Payment).where(Payment.provider == str(Provider.PAYPAL_SANDBOX), or_(*conditions)).limit(1)
    if lock:
        stmt = stmt.with_for_update()
    return await session.scalar(stmt.execution_options(populate_existing=True))


# ---- create order ----------------------------------------------------------------------------


@dataclass
class CreatedOrder:
    payment: Payment
    order_id: str
    approve_url: str | None


def _scenario(value: str) -> FailureScenario:
    try:
        return FailureScenario(value)
    except ValueError as exc:
        raise DomainError(f"Unknown failure scenario {value}") from exc


def validate_failure_scenarios(scenarios: list[str]) -> list[FailureScenario]:
    chosen = [_scenario(s) for s in scenarios if s and s != FailureScenario.NONE]
    if chosen and not settings.ENABLE_FAILURE_INJECTION:
        raise DomainError("Failure injection is disabled (ENABLE_FAILURE_INJECTION=false)")
    for scenario in chosen:
        if SCENARIOS[scenario].requires_webhooks and not settings.paypal_webhooks_enabled:
            raise DomainError(f"{scenario} needs PayPal webhooks (set PAYPAL_WEBHOOK_ID and a public tunnel)")
    return chosen


async def create_paypal_payment(
    session: AsyncSession,
    *,
    amount: Decimal,
    demo: str = "PAYMENT_ONLY",
    failure_scenarios: list[str] | None = None,
    negative_test: str | None = None,
    description: str | None = None,
    return_origin: str | None = None,
) -> CreatedOrder:
    if not settings.paypal_enabled:
        raise ProviderUnavailableError("PayPal Sandbox credentials are not configured (PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET)")
    if demo not in DEMOS:
        raise DomainError(f"Unknown demo {demo}")
    scenarios = validate_failure_scenarios(failure_scenarios or [])
    if negative_test:
        try:
            mock_response_header(negative_test)
        except ValueError as exc:
            raise DomainError(str(exc)) from exc

    amount = _q(Decimal(amount))
    hero = HERO_TRANSACTIONS.get(demo)
    if hero and not await session.scalar(select(Payment.id).where(Payment.transaction_id == hero)):
        txn = hero
    else:
        txn = await SimulatorService(session).allocate_transaction_id()
    description = description or ("PayFlow Pro plan (monthly)" if demo != "REFUND_REQUIRES_APPROVAL" else "PayFlow annual add-on")
    now = utcnow()

    payment = Payment(
        transaction_id=txn,
        customer_id=f"CUST-PP-{random.randint(1000, 9999)}",
        amount=amount,
        currency="USD",
        gateway_status=str(GatewayStatus.PENDING),
        bank_status=str(BankStatus.PENDING),
        merchant_status=str(MerchantStatus.PENDING),
        ledger_status=str(LedgerStatus.PENDING),
        webhook_status=_initial_webhook_status(),
        overall_status=str(PaymentState.CREATED),
        source=str(Provider.PAYPAL_SANDBOX),
        provider=str(Provider.PAYPAL_SANDBOX),
        provider_status="ORDER_REQUESTED",
        reconciliation_status="PENDING",
        scenario=demo,
        is_simulated=False,
        provider_metadata={"demo": demo, "negative_test": negative_test, "description": description},
        created_at=now,
        updated_at=now,
    )
    session.add(payment)
    await session.flush()
    session.add(MerchantTransaction(payment_id=payment.id, order_id=f"ORD-{random.randint(100000, 999999)}",
                                    amount=amount, status=str(MerchantStatus.PENDING), created_at=now))
    session.add(LedgerEntry(payment_id=payment.id, amount=amount, entry_type="CAPTURE",
                            status=str(LedgerStatus.PENDING), created_at=now, updated_at=now))
    audit = AuditService(session)
    await audit.record(
        transaction_id=txn, event=AuditEvent.PAYMENT_CREATED, actor=CONNECTOR,
        reason=f"PayPal Sandbox order requested for {money(amount, 'USD')} ({demo.replace('_', ' ').lower()})",
        evidence={"amount": amount, "currency": "USD", "demo": demo, "negative_test": negative_test},
        result={"overall_status": payment.overall_status},
    )
    injections = FailureInjectionService(session)
    for scenario in scenarios:
        await injections.arm(payment, scenario, metadata={"armed_at": "order-creation"})
    await session.commit()

    # Globally unique: transaction numbers restart after a DB reset, and PayPal replays the cached
    # response for a reused PayPal-Request-Id (a $10 order came back for a $50 request in production).
    key = paypal_order_key(f"{txn}:{payment.id.hex[:12]}")
    base = settings.frontend_base(return_origin)
    try:
        order = await get_provider().create_order(
            reference=txn, amount=amount, currency="USD", description=description,
            return_url=f"{base}/checkout/return?txn={txn}",
            cancel_url=f"{base}/checkout/cancel?txn={txn}",
            idempotency_key=key,
        )
    except ProviderError as exc:
        session.add(ProviderTransaction(payment_id=payment.id, provider=payment.provider, kind="ORDER", status="FAILED",
                                        amount=amount, currency="USD", idempotency_key=key, debug_id=exc.debug_id,
                                        error=exc.message[:500], response=exc.as_dict()))
        payment.provider_status = "ORDER_FAILED"
        payment.gateway_status = str(GatewayStatus.FAILED)
        PaymentStateService.transition(payment, PaymentState.FAILED)
        await audit.record(
            transaction_id=txn, event=AuditEvent.PROVIDER_CALL_FAILED, actor=CONNECTOR,
            reason=f"PayPal Sandbox order creation failed: {exc.message}", evidence=exc.as_dict(),
            result={"failure_source": "PAYPAL_PROVIDER_FAILURE"},
        )
        await session.commit()
        raise ProviderUnavailableError(f"PayPal Sandbox order creation failed: {exc.message}") from exc

    if order.amount is not None and (order.amount != amount or (order.currency or "USD") != "USD"):
        # Never accept a provider object that is not the one we asked for.
        payment.provider_status = "ORDER_REJECTED"
        payment.gateway_status = str(GatewayStatus.FAILED)
        PaymentStateService.transition(payment, PaymentState.FAILED)
        await audit.record(
            transaction_id=txn, event=AuditEvent.PROVIDER_CALL_FAILED, actor=CONNECTOR,
            reason=f"PayPal returned order {order.order_id} for {money(order.amount, order.currency)} but "
                   f"{money(amount, 'USD')} was requested; rejected (idempotency conflict guard)",
            evidence={"order_id": order.order_id, "requested": amount, "returned": order.amount, "key": key},
        )
        await session.commit()
        raise ProviderUnavailableError("PayPal returned a different order than requested; refused for safety")

    payment.provider_order_id = order.order_id
    payment.provider_status = order.status
    payment.provider_metadata = {**payment.provider_metadata, "approve_url": order.approve_url}
    session.add(ProviderTransaction(payment_id=payment.id, provider=payment.provider, kind="ORDER",
                                    provider_reference=order.order_id, status=order.status, amount=order.amount,
                                    currency=order.currency, idempotency_key=key, debug_id=order.debug_id,
                                    response=to_jsonable(order.raw)))
    await PaymentService(session).add_event(
        payment, source="GATEWAY", event_type="PAYPAL_ORDER_CREATED", label="PayPal order created",
        payload={"detail": f"Order {order.order_id} ({order.status}); awaiting sandbox buyer approval",
                 "order_id": order.order_id},
    )
    await audit.record(
        transaction_id=txn, event=AuditEvent.PAYMENT_APPROVAL_STARTED, actor=CONNECTOR,
        reason=f"PayPal order {order.order_id} created; waiting for sandbox buyer approval",
        evidence={"order_id": order.order_id, "paypal_debug_id": order.debug_id},
        result={"approve_url": order.approve_url, "order_status": order.status},
    )
    await session.commit()
    return CreatedOrder(payment=payment, order_id=order.order_id, approve_url=order.approve_url)


# ---- capture -----------------------------------------------------------------------------------


@dataclass
class CaptureResult:
    payment: Payment
    status: str  # CAPTURED | ALREADY_CAPTURED | PROVIDER_FAILED
    message: str


async def capture_paypal_payment(
    session: AsyncSession, *, transaction_id: str | None = None, order_id: str | None = None, trigger: str = "api"
) -> CaptureResult:
    payment = await find_paypal_payment(session, transaction_id=transaction_id, order_id=order_id)
    if payment is None:
        raise NotFoundError("PayPal payment not found")
    if payment.provider_capture_id:
        return CaptureResult(payment, "ALREADY_CAPTURED", "Payment already captured (idempotent)")
    if not payment.provider_order_id:
        raise ConflictError("Payment has no PayPal order")
    if payment.overall_status == PaymentState.FAILED:
        raise ConflictError("Payment already failed")

    audit = AuditService(session)
    await audit.record(
        transaction_id=payment.transaction_id, event=AuditEvent.PAYMENT_CAPTURE_STARTED, actor=CONNECTOR,
        reason=f"Capturing PayPal order {payment.provider_order_id} (trigger: {trigger})",
        evidence={"idempotency_key": paypal_capture_key(payment.provider_order_id),
                  "negative_test": (payment.provider_metadata or {}).get("negative_test")},
    )
    await session.commit()

    order_ref = payment.provider_order_id
    try:
        capture = await get_provider().capture_order(
            order_ref, idempotency_key=paypal_capture_key(order_ref),
            mock_error=(payment.provider_metadata or {}).get("negative_test"),
        )
    except ProviderError as exc:
        if exc.issue in ("ORDER_NOT_APPROVED", "PAYER_ACTION_REQUIRED"):
            raise ConflictError("The sandbox buyer has not approved this order in PayPal yet") from exc
        payment = await find_paypal_payment(session, transaction_id=payment.transaction_id, lock=True)
        await record_provider_failure(session, payment, exc, stage="CAPTURE")
        await session.commit()
        return CaptureResult(payment, "PROVIDER_FAILED", f"PayPal capture failed: {exc.issue or exc.message}")

    payment = await find_paypal_payment(session, transaction_id=payment.transaction_id, lock=True)
    if payment.provider_capture_id:
        await session.commit()
        return CaptureResult(payment, "ALREADY_CAPTURED", "Captured concurrently (idempotent)")
    await apply_capture(
        session, payment, capture_id=capture.capture_id, capture_status=capture.capture_status or capture.order_status,
        amount=capture.amount, currency=capture.currency, fee=capture.fee, net=capture.net_amount, payer=capture.payer,
        debug_id=capture.debug_id, source="Orders API capture", idempotency_key=paypal_capture_key(order_ref),
    )
    await session.commit()
    return CaptureResult(payment, "CAPTURED", f"PayPal capture {capture.capture_status}")


async def apply_capture(
    session: AsyncSession, payment: Payment, *, capture_id: str | None, capture_status: str | None,
    amount: Decimal | None, currency: str | None, fee: Decimal | None = None, net: Decimal | None = None,
    payer: dict | None = None, debug_id: str | None = None, source: str, idempotency_key: str | None = None,
) -> None:
    audit = AuditService(session)
    payments = PaymentService(session)
    amount = amount or payment.amount
    status = capture_status or "UNKNOWN"
    payment.provider_capture_id = capture_id
    payment.provider_status = status
    if payer:
        payment.payer = payer
    payment.provider_metadata = {**(payment.provider_metadata or {}), "paypal_fee": fee, "net_amount": net,
                                 "captured_via": source}
    payment.provider_metadata = to_jsonable(payment.provider_metadata)
    session.add(ProviderTransaction(payment_id=payment.id, provider=payment.provider, kind="CAPTURE",
                                    provider_reference=capture_id, status=status, amount=amount, currency=currency,
                                    idempotency_key=idempotency_key, debug_id=debug_id,
                                    response=to_jsonable({"capture_id": capture_id, "status": status, "fee": fee, "net": net})))

    meta = payment.provider_metadata or {}
    if not meta.get("approved_at"):
        payment.provider_metadata = {**meta, "approved_at": utcnow().isoformat()}
        await audit.record(
            transaction_id=payment.transaction_id, event=AuditEvent.PAYMENT_APPROVED, actor=CONNECTOR,
            reason="Sandbox buyer approved the order in PayPal checkout",
            evidence={"order_id": payment.provider_order_id, "payer_id": (payer or {}).get("payer_id")},
        )
    if payment.overall_status == PaymentState.CREATED:
        PaymentStateService.transition(payment, PaymentState.PROCESSING)

    if status == "COMPLETED":
        payment.gateway_status = str(GatewayStatus.SUCCESS)
        payment.bank_status = str(BankStatus.SETTLED)
        session.add(BankTransaction(payment_id=payment.id, bank_reference=capture_id or "paypal-capture",
                                    amount=amount, status=str(BankStatus.SETTLED), settled_at=utcnow()))
        PaymentStateService.transition(payment, PaymentState.SUCCESS)
        PaymentStateService.transition(payment, PaymentState.SETTLED)
    elif status == "PENDING":
        payment.gateway_status = str(GatewayStatus.PENDING)
        PaymentStateService.transition(payment, PaymentState.PENDING)
    else:
        payment.gateway_status = str(GatewayStatus.FAILED)

    await payments.add_event(
        payment, source="GATEWAY", event_type=f"PAYPAL_CAPTURE_{status}", label=f"PayPal capture {status}",
        payload={"detail": f"Capture {capture_id} {status} for {money(amount, currency)} via {source}",
                 "capture_id": capture_id, "paypal_debug_id": debug_id},
    )
    event = AuditEvent.PAYMENT_CAPTURE_COMPLETED if status in ("COMPLETED", "PENDING") else AuditEvent.PAYMENT_CAPTURE_FAILED
    await audit.record(
        transaction_id=payment.transaction_id, event=event, actor=CONNECTOR,
        reason=f"PayPal Sandbox capture {capture_id} {status} ({money(amount, currency)})",
        evidence={"capture_id": capture_id, "paypal_fee": fee, "net_amount": net, "source": source},
        result={"provider_status": status, "gateway_status": payment.gateway_status},
    )


async def record_provider_failure(session: AsyncSession, payment: Payment, exc: ProviderError, *, stage: str) -> None:
    """A failure that PayPal (not PayFlow) reported. Transport errors leave the outcome UNKNOWN."""
    payments = PaymentService(session)
    session.add(ProviderTransaction(payment_id=payment.id, provider=payment.provider, kind=stage,
                                    provider_reference=payment.provider_order_id, status=exc.issue or "ERROR",
                                    amount=payment.amount, currency=payment.currency, debug_id=exc.debug_id,
                                    error=exc.message[:500], response=exc.as_dict()))
    if payment.overall_status == PaymentState.CREATED:
        PaymentStateService.transition(payment, PaymentState.PROCESSING)
    unknown = exc.kind == "TRANSPORT"
    payment.gateway_status = str(GatewayStatus.UNKNOWN if unknown else GatewayStatus.FAILED)
    payment.provider_status = f"{stage}_{exc.issue or ('TIMEOUT' if unknown else 'ERROR')}"
    payment.provider_metadata = {**(payment.provider_metadata or {}), "provider_failure": exc.as_dict()}
    await payments.add_event(
        payment, source="GATEWAY", event_type=f"PAYPAL_{stage}_FAILED", label=f"PayPal {stage.lower()} failed",
        payload={"detail": exc.message, "issue": exc.issue, "paypal_debug_id": exc.debug_id},
    )
    await AuditService(session).record(
        transaction_id=payment.transaction_id, event=AuditEvent.PAYMENT_CAPTURE_FAILED, actor=CONNECTOR,
        reason=f"PAYPAL_PROVIDER_FAILURE: {exc.issue or exc.message}",
        evidence=exc.as_dict(), result={"failure_source": "PAYPAL_PROVIDER_FAILURE", "outcome_known": not unknown},
    )


# ---- downstream: merchant + ledger (with failure-injection checkpoints) -------------------------------


async def apply_downstream(session: AsyncSession, payment: Payment) -> bool:
    """Propagate a successful PayPal capture to the merchant order service and the ledger.

    Idempotent. This is where demo failures are injected: PayPal's result is
    never changed, only PayFlow's own systems fail.
    """
    meta = payment.provider_metadata or {}
    if meta.get("downstream_applied_at") or payment.gateway_status != GatewayStatus.SUCCESS:
        return False
    payments = PaymentService(session)
    injections = FailureInjectionService(session)
    orders = await payments.merchant_transactions(payment.id)
    captures = [e for e in await payments.ledger_entries(payment.id) if e.entry_type == "CAPTURE"]
    bank = await payments.bank_transactions(payment.id)
    settled = bank[-1].amount if bank else payment.amount

    # Merchant order service
    if await injections.take(payment, FailureScenario.MERCHANT_UPDATE_FAILURE,
                             "Order service returned HTTP 503 while marking the order paid"):
        merchant, label, detail = MerchantStatus.FAILED, "Merchant FAILED (injected)", "Order service HTTP 503 (demo injection)"
    elif await injections.take(payment, FailureScenario.MERCHANT_LEDGER_MISMATCH,
                               "Order-paid message lost before reaching the merchant service"):
        merchant, label, detail = MerchantStatus.PENDING, "Merchant update lost (injected)", "Order-paid message dropped (demo injection)"
    else:
        merchant, label, detail = MerchantStatus.SUCCESS, "Merchant SUCCESS", "Order marked paid"
    for order in orders:
        order.status = str(merchant)
    payment.merchant_status = str(merchant)
    await payments.add_event(payment, source="MERCHANT", event_type=f"ORDER_{merchant}", label=label,
                             payload={"detail": detail, "injected": merchant != MerchantStatus.SUCCESS})

    # Internal ledger
    ledger_amount = settled
    if await injections.take(payment, FailureScenario.LEDGER_WRITE_FAILURE,
                             "Ledger write aborted: lock wait timeout exceeded"):
        ledger, label, detail = LedgerStatus.FAILED, "Ledger FAILED (injected)", "Ledger write aborted (demo injection)"
    elif await injections.take(payment, FailureScenario.LEDGER_MISMATCH, "Ledger posted a wrong amount"):
        ledger_amount = _q(settled - min(settled * Decimal("0.1"), Decimal("5.00")))
        ledger, label = LedgerStatus.SUCCESS, "Ledger amount mismatch (injected)"
        detail = f"Posted {money(ledger_amount, payment.currency)} instead of {money(settled, payment.currency)} (demo injection)"
    else:
        ledger, label, detail = LedgerStatus.SUCCESS, "Ledger SUCCESS", f"Capture of {money(settled, payment.currency)} posted"
    for entry in captures:
        entry.status = str(ledger)
        entry.amount = ledger_amount
        entry.updated_at = utcnow()
    payment.ledger_status = str(ledger)
    await payments.add_event(payment, source="LEDGER", event_type=f"LEDGER_{ledger}", label=label,
                             payload={"detail": detail, "injected": "injected" in label})

    payment.provider_metadata = {**(payment.provider_metadata or {}), "downstream_applied_at": utcnow().isoformat()}
    await AuditService(session).record(
        transaction_id=payment.transaction_id, event=AuditEvent.DOWNSTREAM_UPDATED, actor="payflow-event-processor",
        reason=f"Propagated PayPal capture: merchant {merchant}, ledger {ledger}",
        result={"merchant_status": str(merchant), "ledger_status": str(ledger), "ledger_amount": ledger_amount},
    )
    return True


# ---- post-capture failure injection (Demo B) ----------------------------------------------------------


async def inject_failure_now(session: AsyncSession, payment: Payment, scenario: str, *, by: str) -> dict:
    """Inject a demo failure into PayFlow's systems for an existing PayPal payment."""
    if not settings.ENABLE_FAILURE_INJECTION:
        raise DomainError("Failure injection is disabled (ENABLE_FAILURE_INJECTION=false)")
    scenario = _scenario(scenario)
    info = SCENARIOS[scenario]
    if scenario == FailureScenario.NONE:
        return {"state": "NONE", "reconcile": False}
    if payment.provider != Provider.PAYPAL_SANDBOX:
        raise DomainError("Failure injection applies to live PayPal Sandbox payments only")
    if info.requires_webhooks and not settings.paypal_webhooks_enabled:
        raise DomainError(f"{scenario} needs PayPal webhooks (set PAYPAL_WEBHOOK_ID and a public tunnel)")

    injections = FailureInjectionService(session)
    downstream_done = bool((payment.provider_metadata or {}).get("downstream_applied_at"))
    if not downstream_done:
        if payment.overall_status == PaymentState.FAILED:
            raise ConflictError("Payment already failed at PayPal; nothing downstream to break")
        await injections.arm(payment, scenario, by=by, metadata={"armed_at": "before-capture"})
        return {"state": "ARMED", "reconcile": False,
                "message": f"{scenario} armed; it will trigger when the PayPal capture reaches PayFlow"}
    if info.before_capture_only:
        raise ConflictError(f"{scenario} must be armed before the payment is captured")

    await injections.arm(payment, scenario, by=by, metadata={"armed_at": "after-capture"})
    payments = PaymentService(session)
    if scenario in (FailureScenario.RECONCILIATION_DELAY, FailureScenario.VERIFICATION_TIMEOUT):
        return {"state": "ARMED", "reconcile": False, "message": f"{scenario} armed for the next {info.stage} run"}
    if scenario == FailureScenario.DUPLICATE_WEBHOOK:
        original = await session.scalar(
            select(WebhookEvent).where(WebhookEvent.transaction_id == payment.transaction_id,
                                       WebhookEvent.processing_status == "PROCESSED")
            .order_by(WebhookEvent.received_at.desc()).limit(1)
        )
        if original is None:
            raise ConflictError("No processed PayPal webhook for this payment yet")
        await injections.take(payment, scenario, f"Re-delivering webhook {original.provider_event_id} to PayFlow")
        await ingest_webhook(session, headers=original.headers, raw_body=original.raw_body, replay=True)
        return {"state": "TRIGGERED", "reconcile": False, "message": "Duplicate webhook delivered and deduplicated"}

    detail = {
        FailureScenario.LEDGER_WRITE_FAILURE: "Ledger record for the capture lost (write failure)",
        FailureScenario.MERCHANT_UPDATE_FAILURE: "Merchant order reverted after order-service failure",
        FailureScenario.LEDGER_MISMATCH: "Ledger amount corrupted",
        FailureScenario.MERCHANT_LEDGER_MISMATCH: "Merchant order update lost",
    }[scenario]
    await injections.take(payment, scenario, detail)
    captures = [e for e in await payments.ledger_entries(payment.id) if e.entry_type == "CAPTURE"]
    orders = await payments.merchant_transactions(payment.id)
    if scenario == FailureScenario.LEDGER_WRITE_FAILURE:
        payment.ledger_status = str(LedgerStatus.FAILED)
        for entry in captures:
            entry.status = str(LedgerStatus.FAILED)
    elif scenario == FailureScenario.MERCHANT_UPDATE_FAILURE:
        payment.merchant_status = str(MerchantStatus.FAILED)
        for order in orders:
            order.status = str(MerchantStatus.FAILED)
    elif scenario == FailureScenario.LEDGER_MISMATCH:
        for entry in captures:
            entry.amount = _q(entry.amount - min(entry.amount * Decimal("0.1"), Decimal("5.00")))
    elif scenario == FailureScenario.MERCHANT_LEDGER_MISMATCH:
        payment.merchant_status = str(MerchantStatus.PENDING)
        for order in orders:
            order.status = str(MerchantStatus.PENDING)
    await payments.add_event(payment, source="PAYFLOW", event_type="FAILURE_INJECTED", label=f"{scenario} (injected)",
                             payload={"detail": f"DEMO FAILURE INJECTION: {detail}. PayPal result unchanged.",
                                      "injected": True})
    return {"state": "TRIGGERED", "reconcile": True, "message": f"{scenario} injected into PayFlow infrastructure"}


# ---- refund request (Demo C) ---------------------------------------------------------------------------


async def request_refund(session: AsyncSession, payment: Payment, *, reason: str, by: str) -> bool:
    if payment.merchant_status == MerchantStatus.REFUND_REQUESTED:
        return False
    if payment.provider == Provider.PAYPAL_SANDBOX and not payment.provider_capture_id:
        raise ConflictError("Payment has not been captured in PayPal yet")
    if payment.gateway_status != GatewayStatus.SUCCESS or payment.bank_status != BankStatus.SETTLED:
        raise ConflictError(f"Refund needs a completed, settled capture (provider={payment.gateway_status})")
    payments = PaymentService(session)
    payment.merchant_status = str(MerchantStatus.REFUND_REQUESTED)
    for order in await payments.merchant_transactions(payment.id):
        order.status = str(MerchantStatus.REFUND_REQUESTED)
    session.add(LedgerEntry(payment_id=payment.id, amount=payment.amount, entry_type="REFUND", status=str(LedgerStatus.PENDING)))
    await payments.add_event(payment, source="MERCHANT", event_type="REFUND_REQUESTED", label="Refund requested",
                             payload={"detail": reason})
    await AuditService(session).record(
        transaction_id=payment.transaction_id, event=AuditEvent.REFUND_REQUESTED, actor=by,
        reason=f"Refund of {money(payment.amount, payment.currency)} requested: {reason}",
        evidence={"capture_id": payment.provider_capture_id},
    )
    return True


# ---- webhooks -----------------------------------------------------------------------------------------


def webhook_refs(payload: dict) -> dict:
    """Extract PayPal identifiers from a webhook event."""
    event_type = payload.get("event_type", "")
    resource = payload.get("resource") or {}
    refs: dict[str, str | None] = {"order_id": None, "capture_id": None, "custom_id": None, "resource_id": resource.get("id")}
    if event_type.startswith("CHECKOUT."):
        refs["order_id"] = resource.get("id") if event_type.startswith("CHECKOUT.ORDER") else resource.get("order_id") or resource.get("id")
        units = resource.get("purchase_units") or [{}]
        refs["custom_id"] = units[0].get("custom_id") or units[0].get("reference_id")
    elif event_type.startswith("PAYMENT.CAPTURE."):
        refs["custom_id"] = resource.get("custom_id")
        related = ((resource.get("supplementary_data") or {}).get("related_ids") or {})
        refs["order_id"] = related.get("order_id")
        if event_type == "PAYMENT.CAPTURE.REFUNDED":
            up = next((link.get("href", "") for link in resource.get("links") or [] if link.get("rel") == "up"), "")
            refs["capture_id"] = up.rstrip("/").split("/")[-1] or None
        else:
            refs["capture_id"] = resource.get("id")
    return refs


async def ingest_webhook(
    session: AsyncSession, *, headers: dict[str, str], raw_body: str, replay: bool = False
) -> tuple[WebhookEvent, bool]:
    """Store an inbound webhook. Returns (row, duplicate). Never processes financially."""
    try:
        payload = json.loads(raw_body)
        event_id = payload["id"]
        event_type = payload["event_type"]
    except (ValueError, KeyError, TypeError) as exc:
        raise DomainError("Malformed PayPal webhook body") from exc

    audit = AuditService(session)
    existing = await session.scalar(select(WebhookEvent).where(WebhookEvent.provider_event_id == event_id))
    if existing is not None:
        existing.delivery_count += 1
        await audit.record(
            transaction_id=existing.transaction_id or "UNMATCHED", event=AuditEvent.WEBHOOK_DUPLICATE,
            actor="payflow-webhook-intake" if replay else WEBHOOK_ACTOR,
            reason=(f"Duplicate delivery #{existing.delivery_count} of {event_type} ({event_id}) ignored"
                    + (" - replayed by DEMO FAILURE INJECTION" if replay else "")),
            evidence={"provider_event_id": event_id, "original_status": existing.processing_status},
            result={"financial_action": "none (idempotent)"},
        )
        await session.commit()
        return existing, True

    refs = webhook_refs(payload)
    payment = await find_paypal_payment(session, transaction_id=refs["custom_id"], order_id=refs["order_id"],
                                        capture_id=refs["capture_id"])
    lower = {k.lower(): v for k, v in headers.items() if k.lower().startswith("paypal-")}
    row = WebhookEvent(
        provider=str(Provider.PAYPAL_SANDBOX), provider_event_id=event_id, event_type=event_type,
        resource_id=refs["resource_id"], transaction_id=payment.transaction_id if payment else refs["custom_id"],
        transmission_id=lower.get("paypal-transmission-id"), processing_status="QUEUED", headers=lower,
        raw_body=raw_body, payload=payload, received_at=utcnow(),
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        # A concurrent redelivery won the insert; treat this one as the duplicate.
        return await ingest_webhook(session, headers=headers, raw_body=raw_body, replay=replay)
    await audit.record(
        transaction_id=row.transaction_id or "UNMATCHED", event=AuditEvent.WEBHOOK_RECEIVED, actor=WEBHOOK_ACTOR,
        reason=f"{event_type} received ({event_id}); queued for signature verification",
        evidence={"provider_event_id": event_id, "transmission_id": row.transmission_id, "resource_id": row.resource_id},
    )
    await session.commit()
    return row, False


@dataclass
class WebhookOutcome:
    payment: Payment | None
    status: str
    follow_up: list[tuple[str, tuple, float]]


async def process_webhook(session: AsyncSession, webhook_id: uuid.UUID, *, delayed_delivery: bool = False) -> WebhookOutcome:
    """Verify + apply a stored webhook. Safe to run more than once."""
    row = await session.get(WebhookEvent, webhook_id, populate_existing=True)
    if row is None:
        raise NotFoundError("Webhook event not found")
    expected = "DELAYED" if delayed_delivery else "QUEUED"
    if row.processing_status != expected:
        return WebhookOutcome(None, row.processing_status, [])
    audit = AuditService(session)
    txn = row.transaction_id or "UNMATCHED"

    if row.signature_verified is None:
        verification = await get_provider().verify_webhook(headers=row.headers, raw_body=row.raw_body)
        row.signature_verified = verification.verified
        row.verification_detail = verification.detail[:300]
        if not verification.verified:
            row.processing_status = "REJECTED"
            row.processed_at = utcnow()
            await audit.record(transaction_id=txn, event=AuditEvent.WEBHOOK_REJECTED, actor="webhook-verifier",
                               reason=f"Signature verification failed: {verification.detail}",
                               evidence={"provider_event_id": row.provider_event_id})
            await session.commit()
            return WebhookOutcome(None, "REJECTED", [])
        await audit.record(transaction_id=txn, event=AuditEvent.WEBHOOK_VERIFIED, actor="webhook-verifier",
                           reason=f"{row.event_type} signature verified by PayPal ({verification.detail})",
                           evidence={"provider_event_id": row.provider_event_id, "event_type": row.event_type})

    refs = webhook_refs(row.payload)
    payment = await find_paypal_payment(session, transaction_id=refs["custom_id"], order_id=refs["order_id"],
                                        capture_id=refs["capture_id"], lock=True)
    if payment is None or row.event_type not in HANDLED_WEBHOOKS:
        row.processing_status = "IGNORED"
        row.processed_at = utcnow()
        row.error = "No matching PayFlow payment" if payment is None else "Event type not handled"
        await session.commit()
        return WebhookOutcome(payment, "IGNORED", [])
    row.transaction_id = payment.transaction_id
    follow_up: list[tuple[str, tuple, float]] = []
    injections = FailureInjectionService(session)
    payments = PaymentService(session)

    if row.event_type == "PAYMENT.CAPTURE.COMPLETED" and not delayed_delivery:
        if await injections.take(payment, FailureScenario.WEBHOOK_DROP, "Webhook intake discarded the PayPal capture webhook"):
            row.processing_status = "DROPPED"
            row.processed_at = utcnow()
            await audit.record(transaction_id=payment.transaction_id, event=AuditEvent.WEBHOOK_DROPPED,
                               actor="payflow-webhook-intake", reason="DEMO FAILURE INJECTION: verified webhook dropped")
            await session.commit()
            return WebhookOutcome(payment, "DROPPED", [])
        if await injections.take(payment, FailureScenario.WEBHOOK_DELAY,
                                 f"Webhook intake held the event for {settings.WEBHOOK_DELAY_SECONDS:.0f}s"):
            row.processing_status = "DELAYED"
            if payment.webhook_status != WebhookStatus.RECEIVED:
                payment.webhook_status = str(WebhookStatus.DELAYED)
            await payments.add_event(payment, source="WEBHOOK", event_type="WEBHOOK_DELAYED", label="Webhook delayed (injected)",
                                     payload={"detail": "Verified PayPal webhook held by PayFlow intake", "injected": True})
            await audit.record(transaction_id=payment.transaction_id, event=AuditEvent.WEBHOOK_DELAYED,
                               actor="payflow-webhook-intake",
                               reason=f"DEMO FAILURE INJECTION: webhook processing deferred {settings.WEBHOOK_DELAY_SECONDS:.0f}s")
            await session.commit()
            follow_up.append(("deliver_delayed_webhook", (str(row.id),), settings.WEBHOOK_DELAY_SECONDS))
            follow_up.append(("reconcile", (payment.transaction_id, "webhook-delayed"), 0))
            return WebhookOutcome(payment, "DELAYED", follow_up)

    resource = row.payload.get("resource") or {}
    if row.event_type == "CHECKOUT.ORDER.APPROVED":
        if not payment.provider_capture_id:
            payment.provider_status = "APPROVED"
            payment.provider_metadata = {**(payment.provider_metadata or {}), "approved_at": utcnow().isoformat()}
            await audit.record(transaction_id=payment.transaction_id, event=AuditEvent.PAYMENT_APPROVED,
                               actor=WEBHOOK_ACTOR, reason="Sandbox buyer approved the order (CHECKOUT.ORDER.APPROVED)")
            follow_up.append(("capture", (payment.transaction_id, "webhook:CHECKOUT.ORDER.APPROVED"), 0))
    elif row.event_type == "PAYMENT.CAPTURE.COMPLETED":
        payment.webhook_status = str(WebhookStatus.RECEIVED)
        if not payment.provider_capture_id:
            amount = resource.get("amount") or {}
            await apply_capture(session, payment, capture_id=resource.get("id"), capture_status="COMPLETED",
                                amount=Decimal(str(amount.get("value", payment.amount))), currency=amount.get("currency_code"),
                                source="PayPal webhook")
            follow_up.append(("post_capture", (payment.transaction_id,), 0))
        else:
            follow_up.append(("reconcile", (payment.transaction_id, "webhook"), 0))
        await payments.add_event(payment, source="WEBHOOK", event_type="WEBHOOK_RECEIVED", label="PayPal webhook received",
                                 payload={"detail": f"PAYMENT.CAPTURE.COMPLETED verified"
                                          + (" (delayed delivery)" if delayed_delivery else "")})
    elif row.event_type == "PAYMENT.CAPTURE.PENDING":
        payment.provider_status = "PENDING"
    elif row.event_type in ("PAYMENT.CAPTURE.DENIED", "CHECKOUT.PAYMENT-APPROVAL.REVERSED"):
        if payment.gateway_status != GatewayStatus.SUCCESS:
            await record_provider_failure(session, payment, ProviderError(
                f"PayPal reported {row.event_type}", issue=row.event_type.split(".")[-1], kind="DECLINED"), stage="CAPTURE")
            follow_up.append(("reconcile", (payment.transaction_id, "webhook"), 0))
    elif row.event_type == "PAYMENT.CAPTURE.REFUNDED":
        payment.provider_metadata = {**(payment.provider_metadata or {}), "refund_webhook_confirmed": True}
        await payments.add_event(payment, source="WEBHOOK", event_type="REFUND_CONFIRMED", label="PayPal refund confirmed",
                                 payload={"detail": f"PAYMENT.CAPTURE.REFUNDED ({resource.get('id')})"})

    row.processing_status = "PROCESSED"
    row.processed_at = utcnow()
    if row.event_type == "PAYMENT.CAPTURE.COMPLETED" and await injections.take(
        payment, FailureScenario.DUPLICATE_WEBHOOK, f"PayFlow will re-deliver {row.provider_event_id} to itself"
    ):
        follow_up.append(("replay_webhook", (str(row.id),), 2))
    await session.commit()
    return WebhookOutcome(payment, "PROCESSED", follow_up)

