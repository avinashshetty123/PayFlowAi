from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal
from app.core.enums import ActionStatus, PolicyDecision, Scenario
from app.models import Action, AuditLog, LedgerEntry
from app.schemas.action import PolicyEvaluation
from app.services.action_executor import ActionExecutor
from app.services.orchestrator import ingest_simulated_payment, run_action_stage, run_incident_pipeline
from app.services.payment_service import PaymentService
from app.utils.idempotency import make_idempotency_key, paypal_capture_key, paypal_refund_key

ALLOW = PolicyEvaluation(action_type="RECONCILE_LEDGER", decision=PolicyDecision.ALLOW, label="x", reasons=[], checks=[])


def test_key_format():
    assert make_idempotency_key("RECONCILE_LEDGER", "TXN92831") == "payflow:reconcile:TXN92831"
    assert make_idempotency_key("REFUND", "TXN92842") == "payflow:refund:TXN92842"
    assert paypal_capture_key("5O190127TN364715T") == "paypal:capture:5O190127TN364715T"
    assert paypal_refund_key("3C679366HH908993F") == "paypal:refund:3C679366HH908993F"


async def test_requesting_twice_returns_same_action(db):
    result = await ingest_simulated_payment(db, amount=Decimal("4850"), scenario=Scenario.LEDGER_MISMATCH)
    executor = ActionExecutor(db)
    kwargs = dict(incident=result.incident, payment=result.payment, action_type="RECONCILE_LEDGER", policy=ALLOW,
                  requested_by="test", reason="test")
    first, created_first = await executor.request(**kwargs)
    second, created_second = await executor.request(**kwargs)
    await db.commit()
    assert created_first and not created_second
    assert first.id == second.id
    assert await db.scalar(select(func.count()).select_from(Action)) == 1


async def test_unique_constraint_blocks_duplicate_rows(db):
    result = await ingest_simulated_payment(db, amount=Decimal("4850"), scenario=Scenario.LEDGER_MISMATCH)
    for _ in range(2):
        db.add(Action(incident_id=result.incident.id, action_type="RECONCILE_LEDGER",
                      idempotency_key=make_idempotency_key("RECONCILE_LEDGER", result.payment.transaction_id), status="APPROVED", requested_by="test", policy={}))
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


async def test_executing_twice_does_not_execute_twice(kb):
    result = await ingest_simulated_payment(kb, amount=Decimal("4850"), scenario=Scenario.LEDGER_MISMATCH)
    await run_incident_pipeline(result.incident.id, delay=0)
    txn = result.payment.transaction_id

    async with SessionLocal() as s:
        action = await s.scalar(
            select(Action).where(Action.idempotency_key == make_idempotency_key("RECONCILE_LEDGER", txn))
        )
        assert action.status == ActionStatus.COMPLETED
        replay = await run_action_stage(s, action.id)
    assert replay.deduplicated
    assert replay.verification["status"] == "PASSED"

    async with SessionLocal() as s:
        captures = await PaymentService(s).ledger_entries(result.payment.id)
        executed = await s.scalar(
            select(func.count()).where(AuditLog.event == "ACTION_EXECUTED", AuditLog.transaction_id == txn)
        )
        deduped = await s.scalar(
            select(func.count()).where(AuditLog.event == "ACTION_DEDUPLICATED", AuditLog.transaction_id == txn)
        )
    assert len([c for c in captures if isinstance(c, LedgerEntry)]) == 1
    assert executed == 1
    assert deduped == 1


async def test_reinvestigating_resolved_incident_is_a_noop(kb):
    result = await ingest_simulated_payment(kb, amount=Decimal("4850"), scenario=Scenario.LEDGER_MISMATCH)
    await run_incident_pipeline(result.incident.id, delay=0)
    assert await run_incident_pipeline(result.incident.id, delay=0) == "RESOLVED"
    async with SessionLocal() as s:
        assert await s.scalar(select(func.count()).select_from(Action)) == 1
