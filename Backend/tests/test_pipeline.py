"""End-to-end lifecycle tests: incident -> investigation -> policy -> action -> verification -> audit."""

from decimal import Decimal

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.enums import ActionStatus, IncidentStatus, Scenario
from app.models import Action, AuditLog, Incident, Investigation, LedgerEntry, Payment
from app.services.action_executor import ActionExecutor
from app.utils.idempotency import make_idempotency_key
from app.services.orchestrator import (
    approve_action,
    ingest_simulated_payment,
    reject_action,
    run_incident_pipeline,
)


async def _fresh(model, id_):
    async with SessionLocal() as s:
        return await s.get(model, id_)


async def _audit_events(transaction_id: str) -> list[str]:
    async with SessionLocal() as s:
        return list(await s.scalars(
            select(AuditLog.event).where(AuditLog.transaction_id == transaction_id).order_by(AuditLog.created_at, AuditLog.id)
        ))


async def test_critical_ledger_mismatch_lifecycle(kb):
    """LEDGER_MISMATCH -> Incident -> Investigation -> Policy -> Reconcile -> Verify -> Resolved."""
    result = await ingest_simulated_payment(kb, amount=Decimal("4850"), scenario=Scenario.LEDGER_MISMATCH)
    incident_id = result.incident.id

    status = await run_incident_pipeline(incident_id, delay=0)
    assert status == IncidentStatus.RESOLVED

    incident = await _fresh(Incident, incident_id)
    assert incident.root_cause == "Internal ledger synchronization failure"
    assert incident.recommended_action == "RECONCILE_LEDGER"
    assert incident.risk == "LOW"
    assert incident.policy_decision == "ALLOW"
    assert incident.confidence >= 0.9
    assert incident.resolved_at is not None
    assert incident.initial_snapshot["ledger"] == "FAILED"
    assert incident.final_snapshot["ledger"] == "SUCCESS"

    payment = await _fresh(Payment, result.payment.id)
    assert (payment.gateway_status, payment.bank_status, payment.merchant_status, payment.ledger_status) == (
        "SUCCESS", "SETTLED", "SUCCESS", "SUCCESS"
    )

    async with SessionLocal() as s:
        investigation = await s.scalar(select(Investigation).where(Investigation.incident_id == incident_id))
        action = await s.scalar(select(Action).where(Action.incident_id == incident_id))
        ledger = await s.scalar(select(LedgerEntry).where(LedgerEntry.payment_id == payment.id))
    assert investigation.used_fallback  # no GROQ key in tests
    assert investigation.historical_matches[0]["title"] == "Ledger synchronization failure"
    assert "chain" not in str(investigation.evidence).lower()
    assert action.idempotency_key == make_idempotency_key("RECONCILE_LEDGER", result.payment.transaction_id)
    assert action.status == ActionStatus.COMPLETED
    assert action.result["verification"]["status"] == "PASSED"
    assert ledger.status == "SUCCESS"

    events = await _audit_events(result.payment.transaction_id)
    expected_order = [
        "PAYMENT_CREATED", "MISMATCH_DETECTED", "INVESTIGATION_STARTED", "AI_RECOMMENDATION_CREATED",
        "POLICY_EVALUATED", "ACTION_EXECUTED", "VERIFICATION_PASSED", "INCIDENT_RESOLVED",
    ]
    positions = [events.index(e) for e in expected_order]
    assert positions == sorted(positions), events


async def test_high_value_refund_requires_approval_then_executes(kb):
    result = await ingest_simulated_payment(kb, amount=Decimal("85.00"), scenario=Scenario.REFUND_FAILURE)
    status = await run_incident_pipeline(result.incident.id, delay=0)
    assert status == IncidentStatus.AWAITING_APPROVAL

    incident = await _fresh(Incident, result.incident.id)
    assert incident.recommended_action == "REFUND"
    assert incident.risk == "HIGH"
    assert incident.policy_decision == "HUMAN_APPROVAL_REQUIRED"
    assert incident.requires_human

    async with SessionLocal() as s:
        action = await s.scalar(select(Action).where(Action.incident_id == incident.id))
        assert action.status == ActionStatus.PENDING_APPROVAL
        # Nothing has moved yet.
        payment = await s.get(Payment, result.payment.id)
        assert payment.gateway_status == "REFUND_FAILED"

    async with SessionLocal() as s:
        outcome = await approve_action(s, action.id, approver="ops.manager")
    assert outcome.incident_status == IncidentStatus.RESOLVED
    assert outcome.verification["status"] == "PASSED"
    assert outcome.action.approved_by == "ops.manager"

    payment = await _fresh(Payment, result.payment.id)
    assert payment.overall_status == "REFUNDED"
    assert payment.gateway_status == "REFUNDED"
    events = await _audit_events(result.payment.transaction_id)
    assert "APPROVAL_REQUESTED" in events
    assert events.index("ACTION_APPROVED") < events.index("ACTION_EXECUTED") < events.index("VERIFICATION_PASSED")

    # Approving again is idempotent: no second refund.
    async with SessionLocal() as s:
        again = await approve_action(s, action.id, approver="ops.manager")
    assert again.deduplicated
    async with SessionLocal() as s:
        refunds = list(await s.scalars(
            select(LedgerEntry).where(LedgerEntry.payment_id == payment.id, LedgerEntry.entry_type == "REFUND")
        ))
    assert len(refunds) == 1


async def test_rejection_escalates_without_moving_money(kb):
    result = await ingest_simulated_payment(kb, amount=Decimal("85.00"), scenario=Scenario.REFUND_FAILURE)
    await run_incident_pipeline(result.incident.id, delay=0)
    async with SessionLocal() as s:
        action = await s.scalar(select(Action).where(Action.incident_id == result.incident.id))
        outcome = await reject_action(s, action.id, approver="ops.manager", reason="Customer disputes amount")
    assert outcome.incident_status == IncidentStatus.ESCALATED
    assert outcome.action.status == ActionStatus.REJECTED
    payment = await _fresh(Payment, result.payment.id)
    assert payment.gateway_status == "REFUND_FAILED"
    assert "ACTION_REJECTED" in await _audit_events(payment.transaction_id)


async def test_small_refund_is_automatic(kb):
    result = await ingest_simulated_payment(kb, amount=Decimal("21.50"), scenario=Scenario.REFUND_FAILURE)
    assert await run_incident_pipeline(result.incident.id, delay=0) == IncidentStatus.RESOLVED


async def test_webhook_delay_is_retried_and_settles(kb):
    result = await ingest_simulated_payment(kb, amount=Decimal("899"), scenario=Scenario.WEBHOOK_DELAY)
    assert await run_incident_pipeline(result.incident.id, delay=0) == IncidentStatus.RESOLVED
    payment = await _fresh(Payment, result.payment.id)
    assert payment.webhook_status == "RECEIVED"
    assert payment.overall_status == "SETTLED"


async def test_timeout_marked_failed(kb):
    result = await ingest_simulated_payment(kb, amount=Decimal("1799"), scenario=Scenario.TIMEOUT)
    assert await run_incident_pipeline(result.incident.id, delay=0) == IncidentStatus.RESOLVED
    payment = await _fresh(Payment, result.payment.id)
    assert payment.overall_status == "FAILED"


async def test_duplicate_payment_refunds_only_the_duplicate(kb):
    result = await ingest_simulated_payment(kb, amount=Decimal("12.99"), scenario=Scenario.DUPLICATE_PAYMENT)
    assert await run_incident_pipeline(result.incident.id, delay=0) == IncidentStatus.RESOLVED
    duplicate = await _fresh(Payment, result.payment.id)
    original = await _fresh(Payment, result.related_payment.id)
    assert duplicate.overall_status == "REFUNDED"
    assert original.overall_status == "SETTLED"


async def test_unknown_state_and_settlement_mismatch_escalate(kb):
    for scenario in (Scenario.UNKNOWN_STATE, Scenario.SETTLEMENT_MISMATCH):
        result = await ingest_simulated_payment(kb, amount=Decimal("5600"), scenario=scenario)
        assert await run_incident_pipeline(result.incident.id, delay=0) == IncidentStatus.ESCALATED
        async with SessionLocal() as s:
            action = await s.scalar(select(Action).where(Action.incident_id == result.incident.id))
        assert action.action_type == "ESCALATE"


async def test_failed_verification_escalates(kb, monkeypatch):
    async def broken_reconcile(self, payment, action):  # executes but does not fix the ledger
        return {"summary": "no-op"}

    monkeypatch.setattr(ActionExecutor, "_reconcile_ledger", broken_reconcile)
    result = await ingest_simulated_payment(kb, amount=Decimal("4850"), scenario=Scenario.LEDGER_MISMATCH)
    assert await run_incident_pipeline(result.incident.id, delay=0) == IncidentStatus.ESCALATED
    events = await _audit_events(result.payment.transaction_id)
    assert "VERIFICATION_FAILED" in events
    assert "INCIDENT_RESOLVED" not in events


async def test_failing_action_is_recorded_and_escalated(kb, monkeypatch):
    async def exploding(self, payment, action):
        raise RuntimeError("ledger service unavailable")

    monkeypatch.setattr(ActionExecutor, "_reconcile_ledger", exploding)
    result = await ingest_simulated_payment(kb, amount=Decimal("4850"), scenario=Scenario.LEDGER_MISMATCH)
    assert await run_incident_pipeline(result.incident.id, delay=0) == IncidentStatus.ESCALATED
    payment = await _fresh(Payment, result.payment.id)
    assert payment.ledger_status == "FAILED"  # savepoint rolled back partial changes
    assert "ACTION_FAILED" in await _audit_events(payment.transaction_id)
