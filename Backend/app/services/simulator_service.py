"""Synthetic payment generator.

Used to build the *historical* PayFlow dataset (seed) and for automated tests.
Live demo payments go through PayPal Sandbox instead (see paypal_service).
Every record produced here is labelled with a non-PayPal provider and is_simulated=True.
"""

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AuditEvent, BankStatus, PaymentState, Scenario
from app.core.errors import ConflictError
from app.models import BankTransaction, LedgerEntry, MerchantTransaction, Payment
from app.models.base import utcnow
from app.services.audit_service import AuditService
from app.services.payment_service import PaymentService, PaymentStateService
from app.simulator.scenarios import ScenarioSpec, get_scenario

SIMULATOR_ACTOR = "payment-simulator"

# Reserved for the live PayPal Sandbox hero demos (see paypal_service.HERO_TRANSACTIONS).
RESERVED_IDS = frozenset({"TXN92831", "TXN92842"})
FIRST_TXN_NUMBER = 92700


@dataclass
class SimulationResult:
    payment: Payment
    related_payment: Payment | None
    spec: ScenarioSpec


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class SimulatorService:
    def __init__(self, session: AsyncSession, rng: random.Random | None = None):
        self.session = session
        self.rng = rng or random.Random()
        self.payments = PaymentService(session)
        self.audit = AuditService(session)

    async def _exists(self, transaction_id: str) -> bool:
        found = await self.session.scalar(select(Payment.id).where(Payment.transaction_id == transaction_id))
        return found is not None

    async def allocate_transaction_id(self) -> str:
        current = await self.session.scalar(
            text(
                "SELECT COALESCE(MAX(CAST(SUBSTRING(transaction_id FROM 4) AS BIGINT)), 0) "
                "FROM payments WHERE transaction_id ~ '^TXN[0-9]+$'"
            )
        )
        number = max(int(current or 0) + 1, FIRST_TXN_NUMBER)
        while f"TXN{number}" in RESERVED_IDS:
            number += 1
        return f"TXN{number}"

    def _customer_id(self) -> str:
        return f"CUST-{self.rng.randint(1000, 9999)}"

    def _order_id(self) -> str:
        return f"ORD-{self.rng.randint(100000, 999999)}"

    def _utr(self) -> str:
        return f"UTR{self.rng.randint(10**11, 10**12 - 1)}"

    async def simulate(
        self,
        *,
        amount: Decimal,
        scenario: Scenario | str,
        transaction_id: str | None = None,
        customer_id: str | None = None,
        now: datetime | None = None,
        currency: str = "USD",
        provider: str = "SYNTHETIC",
    ) -> SimulationResult:
        self.currency, self.provider = currency, provider
        scenario = Scenario(scenario)
        amount = _money(Decimal(amount))
        spec = get_scenario(scenario)
        now = now or utcnow()

        if transaction_id and await self._exists(transaction_id):
            raise ConflictError(f"Transaction {transaction_id} already exists")

        customer_id = customer_id or self._customer_id()
        order_id = self._order_id()
        related: Payment | None = None

        if scenario == Scenario.DUPLICATE_PAYMENT:
            # The original, legitimate capture for the same order ~2 minutes earlier.
            original_txn = await self.allocate_transaction_id()
            related = await self._materialise(
                spec=get_scenario(Scenario.SUCCESS),
                transaction_id=original_txn,
                amount=amount,
                customer_id=customer_id,
                order_id=order_id,
                start=now - timedelta(seconds=150),
                scenario_label=Scenario.SUCCESS,
            )

        txn = transaction_id or await self.allocate_transaction_id()
        start = now - timedelta(seconds=spec.duration + 1)
        payment = await self._materialise(
            spec=spec,
            transaction_id=txn,
            amount=amount,
            customer_id=customer_id,
            order_id=order_id,
            start=start,
            scenario_label=scenario,
            duplicate_of=related.transaction_id if related else None,
        )
        return SimulationResult(payment=payment, related_payment=related, spec=spec)

    async def _materialise(
        self,
        *,
        spec: ScenarioSpec,
        transaction_id: str,
        amount: Decimal,
        customer_id: str,
        order_id: str,
        start: datetime,
        scenario_label: Scenario,
        duplicate_of: str | None = None,
    ) -> Payment:
        payment = Payment(
            transaction_id=transaction_id,
            customer_id=customer_id,
            amount=amount,
            currency=self.currency,
            gateway_status=str(spec.gateway),
            bank_status=str(spec.bank),
            merchant_status=str(spec.merchant),
            ledger_status=str(spec.ledger),
            webhook_status=str(spec.webhook),
            overall_status=str(PaymentState.CREATED),
            source=self.provider,
            provider=self.provider,
            provider_status=str(spec.gateway),
            reconciliation_status="PENDING",
            scenario=str(scenario_label),
            is_simulated=True,
            created_at=start,
            updated_at=start,
        )
        # Walk the canonical lifecycle through the state machine (rejects invalid paths).
        PaymentStateService.transition_path(payment, list(spec.state_path))
        payment.updated_at = start + timedelta(seconds=spec.duration)
        self.session.add(payment)
        await self.session.flush()

        if spec.bank != BankStatus.NOT_FOUND:
            settled = spec.bank in (BankStatus.SETTLED, BankStatus.REFUNDED)
            self.session.add(
                BankTransaction(
                    payment_id=payment.id,
                    bank_reference=self._utr(),
                    amount=_money(amount * spec.bank_amount_factor),
                    status=str(spec.bank),
                    settled_at=start + timedelta(seconds=spec.duration) if settled else None,
                    created_at=start + timedelta(seconds=1),
                )
            )
        self.session.add(
            MerchantTransaction(
                payment_id=payment.id,
                order_id=order_id,
                amount=amount,
                status=str(spec.merchant),
                created_at=start,
            )
        )
        for index, (entry_type, status) in enumerate(spec.ledger_entries):
            ts = start + timedelta(seconds=min(5 + index * 3, max(spec.duration, 1)))
            self.session.add(
                LedgerEntry(
                    payment_id=payment.id,
                    amount=amount,
                    entry_type=entry_type,
                    status=str(status),
                    created_at=ts,
                    updated_at=ts,
                )
            )

        await self.audit.record(
            transaction_id=transaction_id,
            event=AuditEvent.PAYMENT_CREATED,
            actor=SIMULATOR_ACTOR,
            reason=f"{'Historical' if self.provider == 'PAYFLOW_HISTORICAL' else 'Synthetic'} payment recorded ({scenario_label})",
            evidence={"amount": amount, "customer_id": customer_id, "order_id": order_id, "simulated": True},
            result={"overall_status": payment.overall_status},
            at=start,
        )
        for event in spec.events:
            at = start + timedelta(seconds=event.offset)
            payload: dict = {"detail": event.detail, "simulated": True, "order_id": order_id}
            if duplicate_of and event.event_type == "DUPLICATE_ORDER_PAYMENT":
                payload["duplicate_of"] = duplicate_of
                payload["detail"] = f"Order {order_id} already paid by {duplicate_of}"
            if event.event_type == "BANK_SETTLED_SHORT":
                payload["settled_amount"] = _money(amount * spec.bank_amount_factor)
                payload["expected_amount"] = amount
            await self.payments.add_event(
                payment, source=event.source, event_type=event.event_type, label=event.label, payload=payload, at=at
            )
            if event.event_type != "PAYMENT_CREATED":
                await self.audit.record(
                    transaction_id=transaction_id,
                    event=AuditEvent.PAYMENT_EVENT_RECEIVED,
                    actor=f"{event.source.lower()}-connector",
                    reason=event.label,
                    evidence={"event_type": event.event_type, "source": event.source, "detail": payload["detail"]},
                    at=at,
                )
        await self.session.flush()
        return payment
