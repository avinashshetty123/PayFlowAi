"""Read-only investigation tools.

These are plain Python service functions. The investigator calls them to build
an evidence bundle for the LLM. None of them can mutate state.
"""

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.enums import BankStatus, GatewayStatus
from app.models import Payment
from app.rag.historical_service import HistoricalIncidentService
from app.services.payment_service import PaymentService, snapshot
from app.utils.serialization import money


async def get_payment(session: AsyncSession, transaction_id: str) -> dict:
    p = await PaymentService(session).get_by_transaction_id(transaction_id)
    return {
        "transaction_id": p.transaction_id,
        "amount": float(p.amount),
        "amount_display": money(p.amount, p.currency),
        "provider": p.provider,
        "provider_status": p.provider_status,
        "currency": p.currency,
        "customer_id": p.customer_id,
        "overall_status": p.overall_status,
        "simulated": p.is_simulated,
    }


async def get_gateway_status(payment: Payment) -> dict:
    return {"system": "gateway", "status": payment.gateway_status}


async def get_bank_status(session: AsyncSession, payment: Payment) -> dict:
    txns = await PaymentService(session).bank_transactions(payment.id)
    latest = txns[-1] if txns else None
    return {
        "system": "bank",
        "status": payment.bank_status,
        "settled_amount": float(latest.amount) if latest else None,
        "bank_reference": latest.bank_reference if latest else None,
        "settled_at": latest.settled_at.isoformat() if latest and latest.settled_at else None,
    }


async def get_merchant_status(session: AsyncSession, payment: Payment) -> dict:
    txns = await PaymentService(session).merchant_transactions(payment.id)
    return {
        "system": "merchant",
        "status": payment.merchant_status,
        "order_id": txns[-1].order_id if txns else None,
    }


async def get_ledger_status(session: AsyncSession, payment: Payment) -> dict:
    entries = await PaymentService(session).ledger_entries(payment.id)
    return {
        "system": "ledger",
        "status": payment.ledger_status,
        "entries": [{"type": e.entry_type, "status": e.status, "amount": float(e.amount)} for e in entries],
    }


async def get_payment_events(session: AsyncSession, payment: Payment) -> list[dict]:
    events = await PaymentService(session).events(payment.id)
    return [
        {
            "at": e.created_at.strftime("%H:%M:%S"),
            "source": e.source,
            "event": e.event_type,
            "detail": e.payload.get("detail"),
        }
        for e in events
    ]


async def search_historical_incidents(session: AsyncSession, incident_type: str, payment: Payment) -> list[dict]:
    matches = await HistoricalIncidentService(session).search(incident_type=incident_type, snapshot=snapshot(payment))
    return [m.model_dump() for m in matches]


async def check_refund_eligibility(session: AsyncSession, payment: Payment) -> dict:
    entries = await PaymentService(session).ledger_entries(payment.id)
    already_refunded = any(e.entry_type == "REFUND" and e.status == "SUCCESS" for e in entries)
    captured = payment.gateway_status in (GatewayStatus.SUCCESS, GatewayStatus.REFUND_FAILED)
    settled = payment.bank_status == BankStatus.SETTLED
    eligible = captured and settled and not already_refunded
    limit = Decimal(str(settings.refund_limit(payment.currency)))
    return {
        "eligible": eligible,
        "reason": "Captured and settled, no completed refund" if eligible else "Not captured/settled or already refunded",
        "amount": float(payment.amount),
        "requires_approval": payment.amount > limit,
        "auto_approve_limit": float(limit),
    }


async def get_webhook_events(session: AsyncSession, payment: Payment) -> list[dict]:
    from sqlalchemy import select

    from app.models import WebhookEvent

    rows = await session.scalars(
        select(WebhookEvent).where(WebhookEvent.transaction_id == payment.transaction_id).order_by(WebhookEvent.received_at)
    )
    return [
        {"event_type": w.event_type, "signature_verified": w.signature_verified, "processing_status": w.processing_status,
         "delivery_count": w.delivery_count, "received_at": w.received_at.strftime("%H:%M:%S")}
        for w in rows
    ]


async def get_customer_history(session: AsyncSession, payment: Payment) -> dict:
    """Recent incidents for the same customer (velocity / repeat-offender signal)."""
    from sqlalchemy import func, select

    from app.models import Incident

    count = await session.scalar(
        select(func.count()).select_from(Incident).join(Payment, Payment.id == Incident.payment_id)
        .where(Payment.customer_id == payment.customer_id, Payment.id != payment.id)
    )
    payments = await session.scalar(select(func.count()).select_from(Payment).where(Payment.customer_id == payment.customer_id))
    return {"customer_id": payment.customer_id, "other_incidents": int(count or 0), "payments": int(payments or 0)}


TOOL_NAMES = [
    "get_payment",
    "get_gateway_status",
    "get_bank_status",
    "get_merchant_status",
    "get_ledger_status",
    "get_payment_events",
    "search_historical_incidents",
    "check_refund_eligibility",
    "get_webhook_events",
    "get_customer_history",
]
