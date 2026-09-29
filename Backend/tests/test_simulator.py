from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.enums import IncidentStatus, IncidentType, Scenario
from app.core.errors import ConflictError
from app.models import AuditLog, BankTransaction, Incident, LedgerEntry, MerchantTransaction, Payment, PaymentEvent
from app.services.orchestrator import ingest_simulated_payment


async def test_ledger_mismatch_generates_records_across_all_systems(db):
    result = await ingest_simulated_payment(db, amount=Decimal("4850"), scenario=Scenario.LEDGER_MISMATCH)
    payment = result.payment

    # TXN92831 / TXN92842 are reserved for the live PayPal Sandbox hero demos
    assert payment.transaction_id.startswith("TXN") and payment.transaction_id not in ("TXN92831", "TXN92842")
    assert payment.provider == "SYNTHETIC" and payment.currency == "USD"
    assert (payment.gateway_status, payment.bank_status, payment.merchant_status, payment.ledger_status,
            payment.webhook_status) == ("SUCCESS", "SETTLED", "FAILED", "FAILED", "RECEIVED")
    assert payment.overall_status == "SETTLED"
    assert payment.is_simulated

    for model in (BankTransaction, MerchantTransaction, LedgerEntry):
        count = await db.scalar(select(func.count()).select_from(model).where(model.payment_id == payment.id))
        assert count == 1
    events = await db.scalar(select(func.count()).where(PaymentEvent.payment_id == payment.id))
    assert events == 6

    assert result.incident is not None
    assert result.incident.type == IncidentType.LEDGER_MISMATCH
    assert result.incident.status == IncidentStatus.OPEN
    assert result.incident.initial_snapshot["ledger"] == "FAILED"
    audit_events = set(await db.scalars(select(AuditLog.event).where(AuditLog.transaction_id == payment.transaction_id)))
    assert {"PAYMENT_CREATED", "PAYMENT_EVENT_RECEIVED", "MISMATCH_DETECTED", "INCIDENT_CREATED"} <= audit_events


async def test_synthetic_allocation_skips_reserved_hero_ids(db):
    await ingest_simulated_payment(db, amount=Decimal("10"), scenario=Scenario.SUCCESS, transaction_id="TXN92830")
    nxt = await ingest_simulated_payment(db, amount=Decimal("10"), scenario=Scenario.SUCCESS)
    assert nxt.payment.transaction_id == "TXN92832"


async def test_refund_failure_scenario(db):
    result = await ingest_simulated_payment(db, amount=Decimal("85"), scenario=Scenario.REFUND_FAILURE)
    assert result.payment.overall_status == "REFUND_PENDING"
    assert result.incident.type == IncidentType.REFUND_FAILURE


async def test_success_creates_no_incident(db):
    result = await ingest_simulated_payment(db, amount=Decimal("1299"), scenario=Scenario.SUCCESS)
    assert result.reconciliation.consistent
    assert result.incident is None
    assert await db.scalar(select(func.count()).select_from(Incident)) == 0


async def test_duplicate_payment_creates_original_and_duplicate(db):
    result = await ingest_simulated_payment(db, amount=Decimal("1299"), scenario=Scenario.DUPLICATE_PAYMENT)
    assert result.related_payment is not None
    assert result.incident.type == IncidentType.DUPLICATE_PAYMENT
    assert await db.scalar(select(func.count()).select_from(Payment)) == 2
    # Only the duplicate is flagged, not the original.
    assert await db.scalar(select(func.count()).select_from(Incident)) == 1


async def test_explicit_duplicate_transaction_id_is_rejected(db):
    await ingest_simulated_payment(db, amount=Decimal("100"), scenario=Scenario.SUCCESS, transaction_id="TXN10001")
    with pytest.raises(ConflictError):
        await ingest_simulated_payment(db, amount=Decimal("100"), scenario=Scenario.SUCCESS, transaction_id="TXN10001")


@pytest.mark.parametrize("scenario", list(Scenario))
async def test_every_scenario_simulates(db, scenario):
    result = await ingest_simulated_payment(db, amount=Decimal("2500"), scenario=scenario)
    assert result.payment.id is not None
    assert (result.incident is None) == result.reconciliation.consistent
