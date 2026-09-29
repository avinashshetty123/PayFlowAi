import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import PaymentState
from app.core.errors import InvalidTransitionError, NotFoundError
from app.models import BankTransaction, Incident, LedgerEntry, MerchantTransaction, Payment, PaymentEvent
from app.models.base import utcnow
from app.utils.serialization import to_jsonable

S = PaymentState

# Deterministic payment lifecycle. Nothing (including AI) may bypass this table.
ALLOWED_TRANSITIONS: dict[PaymentState, frozenset[PaymentState]] = {
    S.CREATED: frozenset({S.PROCESSING, S.FAILED}),
    S.PROCESSING: frozenset({S.PENDING, S.SUCCESS, S.FAILED, S.UNKNOWN}),
    S.PENDING: frozenset({S.SUCCESS, S.FAILED, S.UNKNOWN}),
    S.UNKNOWN: frozenset({S.SUCCESS, S.FAILED}),
    S.SUCCESS: frozenset({S.SETTLED, S.REFUND_PENDING}),
    S.SETTLED: frozenset({S.REFUND_PENDING}),
    S.REFUND_PENDING: frozenset({S.REFUNDED}),
    S.FAILED: frozenset(),
    S.REFUNDED: frozenset(),
}


class PaymentStateService:
    """The only component allowed to change ``payments.overall_status``."""

    @staticmethod
    def can_transition(current: str, target: str) -> bool:
        try:
            return PaymentState(target) in ALLOWED_TRANSITIONS[PaymentState(current)]
        except ValueError:
            return False

    @classmethod
    def validate(cls, current: str, target: str) -> None:
        if not cls.can_transition(current, target):
            raise InvalidTransitionError(f"Invalid payment state transition {current} -> {target}")

    @classmethod
    def validate_path(cls, start: str, path: list[str]) -> None:
        current = start
        for target in path:
            cls.validate(current, target)
            current = target

    @classmethod
    def transition(cls, payment: Payment, target: PaymentState | str) -> tuple[str, str]:
        previous = payment.overall_status
        cls.validate(previous, str(target))
        payment.overall_status = str(target)
        payment.updated_at = utcnow()
        return previous, str(target)

    @classmethod
    def transition_path(cls, payment: Payment, path: list[PaymentState | str]) -> list[tuple[str, str]]:
        return [cls.transition(payment, step) for step in path]


def snapshot(payment: Payment) -> dict[str, str]:
    return {
        "gateway": payment.gateway_status,
        "bank": payment.bank_status,
        "merchant": payment.merchant_status,
        "ledger": payment.ledger_status,
        "webhook": payment.webhook_status,
        "overall": payment.overall_status,
    }


class PaymentService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, payment_id: uuid.UUID) -> Payment:
        payment = await self.session.get(Payment, payment_id)
        if payment is None:
            raise NotFoundError(f"Payment {payment_id} not found")
        return payment

    async def get_by_transaction_id(self, transaction_id: str) -> Payment:
        payment = await self.session.scalar(select(Payment).where(Payment.transaction_id == transaction_id))
        if payment is None:
            raise NotFoundError(f"Payment {transaction_id} not found")
        return payment

    async def reload(self, payment_id: uuid.UUID) -> Payment:
        payment = await self.session.get(Payment, payment_id, populate_existing=True)
        if payment is None:
            raise NotFoundError(f"Payment {payment_id} not found")
        return payment

    async def list_payments(
        self, *, limit: int = 50, offset: int = 0, status: str | None = None, search: str | None = None,
        provider: str | None = None,
    ) -> tuple[list[Payment], int]:
        stmt = select(Payment)
        count_stmt = select(func.count()).select_from(Payment)
        if provider:
            stmt = stmt.where(Payment.provider == provider)
            count_stmt = count_stmt.where(Payment.provider == provider)
        if status:
            stmt = stmt.where(Payment.overall_status == status)
            count_stmt = count_stmt.where(Payment.overall_status == status)
        if search:
            like = f"%{search}%"
            cond = (Payment.transaction_id.ilike(like) | Payment.customer_id.ilike(like)
                    | Payment.provider_order_id.ilike(like) | Payment.provider_capture_id.ilike(like))
            stmt = stmt.where(cond)
            count_stmt = count_stmt.where(cond)
        total = await self.session.scalar(count_stmt) or 0
        rows = await self.session.scalars(stmt.order_by(Payment.created_at.desc()).limit(limit).offset(offset))
        return list(rows), total

    async def add_event(
        self,
        payment: Payment,
        *,
        source: str,
        event_type: str,
        label: str,
        payload: dict[str, Any] | None = None,
        at: datetime | None = None,
    ) -> PaymentEvent:
        event = PaymentEvent(
            payment_id=payment.id,
            source=source,
            event_type=event_type,
            payload=to_jsonable({"label": label, **(payload or {})}),
            created_at=at or utcnow(),
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def events(self, payment_id: uuid.UUID) -> list[PaymentEvent]:
        rows = await self.session.scalars(
            select(PaymentEvent).where(PaymentEvent.payment_id == payment_id).order_by(PaymentEvent.created_at)
        )
        return list(rows)

    async def bank_transactions(self, payment_id: uuid.UUID) -> list[BankTransaction]:
        rows = await self.session.scalars(
            select(BankTransaction).where(BankTransaction.payment_id == payment_id).order_by(BankTransaction.created_at)
        )
        return list(rows)

    async def merchant_transactions(self, payment_id: uuid.UUID) -> list[MerchantTransaction]:
        rows = await self.session.scalars(
            select(MerchantTransaction)
            .where(MerchantTransaction.payment_id == payment_id)
            .order_by(MerchantTransaction.created_at)
        )
        return list(rows)

    async def ledger_entries(self, payment_id: uuid.UUID) -> list[LedgerEntry]:
        rows = await self.session.scalars(
            select(LedgerEntry).where(LedgerEntry.payment_id == payment_id).order_by(LedgerEntry.created_at)
        )
        return list(rows)

    async def incidents(self, payment_id: uuid.UUID) -> list[Incident]:
        rows = await self.session.scalars(
            select(Incident).where(Incident.payment_id == payment_id).order_by(Incident.created_at)
        )
        return list(rows)
