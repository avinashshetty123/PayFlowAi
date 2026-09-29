"""PayFlow lifecycle orchestration:

OBSERVE -> INVESTIGATE -> DECIDE -> ACT -> VERIFY -> RECONCILE -> AUDIT

Every stage commits before the next one so the UI (and auditors) can follow
progress, and so each stage can be retried independently by a worker.
"""

import asyncio
import logging
import random
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.investigator import InvestigatorService
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.enums import (
    ActionStatus,
    ActionType,
    AuditEvent,
    IncidentStatus,
    PaymentState,
    PolicyDecision,
    Provider,
    ReconciliationStatus,
    Scenario,
)
from app.core.errors import ConflictError, NotFoundError
from app.models import Action, Approval, BankTransaction, Incident, Investigation, LedgerEntry, Payment, ReconciliationRun
from app.models.base import utcnow
from app.services.action_executor import EXECUTOR_ACTOR, ActionExecutor
from app.services.audit_service import AuditService
from app.services.incident_service import IncidentService
from app.services.notification_service import notify
from app.services.payment_service import PaymentService, PaymentStateService, snapshot
from app.services.policy_service import PolicyContext, PolicyEngine
from app.services.reconciliation_service import ReconciliationResult, ReconciliationService
from app.services.simulator_service import SimulatorService
from app.services.verification_service import VerificationService
from app.utils.serialization import money, to_jsonable

logger = logging.getLogger(__name__)

AI_ACTOR = "ai-investigator"
POLICY_ACTOR = "policy-engine"
VERIFIER_ACTOR = "verification-service"
ORCHESTRATOR_ACTOR = "payflow-orchestrator"

TERMINAL_INCIDENT_STATUSES = (IncidentStatus.RESOLVED,)


async def _pause(delay: float) -> None:
    if delay > 0:
        await asyncio.sleep(delay)


async def _bank_amount(session: AsyncSession, payment: Payment) -> Decimal | None:
    return await session.scalar(
        select(BankTransaction.amount)
        .where(BankTransaction.payment_id == payment.id)
        .order_by(BankTransaction.created_at.desc())
        .limit(1)
    )


async def _ledger_amount(session: AsyncSession, payment: Payment) -> Decimal | None:
    return await session.scalar(
        select(LedgerEntry.amount)
        .where(LedgerEntry.payment_id == payment.id, LedgerEntry.entry_type == "CAPTURE")
        .order_by(LedgerEntry.created_at.desc())
        .limit(1)
    )


async def _refund_completed(session: AsyncSession, payment: Payment) -> bool:
    found = await session.scalar(
        select(LedgerEntry.id).where(
            LedgerEntry.payment_id == payment.id, LedgerEntry.entry_type == "REFUND", LedgerEntry.status == "SUCCESS"
        )
    )
    return found is not None


# ---- OBSERVE: ingest + deterministic detection ------------------------------------------


@dataclass
class IngestResult:
    payment: Payment
    related_payment: Payment | None
    reconciliation: ReconciliationResult
    incident: Incident | None


async def detect(
    session: AsyncSession, payment: Payment, *, trigger: str = "event"
) -> tuple[ReconciliationResult, Incident | None]:
    """Deterministic reconciliation of one payment; opens an incident on mismatch."""
    started = utcnow()
    result = await ReconciliationService(session).reconcile(payment)
    incident = None
    if not result.consistent:
        incident, _ = await IncidentService(session).create_from_reconciliation(payment, result)
    payment.reconciliation_status = str(
        ReconciliationStatus.MATCHED if result.consistent else ReconciliationStatus.MISMATCH
    )
    session.add(ReconciliationRun(
        payment_id=payment.id, trigger=trigger, checked=1, mismatches=0 if result.consistent else 1,
        result=to_jsonable(result.as_dict()), started_at=started, finished_at=utcnow(),
    ))
    if payment.provider == Provider.PAYPAL_SANDBOX:
        await AuditService(session).record(
            transaction_id=payment.transaction_id, incident_id=incident.id if incident else None,
            event=AuditEvent.RECONCILIATION_COMPLETED, actor="reconciliation-engine",
            reason=result.summary if not result.consistent else "All systems consistent",
            evidence={"snapshot": result.snapshot, "trigger": trigger},
            result={"consistent": result.consistent, "incident_type": str(result.incident_type) if result.incident_type else None},
        )
    return result, incident


async def ingest_simulated_payment(
    session: AsyncSession,
    *,
    amount: Decimal,
    scenario: Scenario | str,
    transaction_id: str | None = None,
    customer_id: str | None = None,
    rng: random.Random | None = None,
    now: datetime | None = None,
    currency: str = "USD",
    provider: str = "SYNTHETIC",
) -> IngestResult:
    sim = await SimulatorService(session, rng).simulate(
        amount=amount, scenario=scenario, transaction_id=transaction_id, customer_id=customer_id, now=now,
        currency=currency, provider=provider,
    )
    if sim.related_payment is not None:
        await detect(session, sim.related_payment)
    result, incident = await detect(session, sim.payment)
    await session.commit()
    return IngestResult(payment=sim.payment, related_payment=sim.related_payment, reconciliation=result, incident=incident)


# ---- INVESTIGATE + DECIDE ------------------------------------------------------------------


async def run_investigation_stage(
    session: AsyncSession, incident_id: uuid.UUID, *, force_fallback: bool = False, delay: float = 0.0
) -> uuid.UUID | None:
    """Investigate an incident and evaluate policy. Returns an action id ready to execute, if any."""
    incidents = IncidentService(session)
    audit = AuditService(session)
    incident = await incidents.reload(incident_id)
    payment = await PaymentService(session).reload(incident.payment_id)
    txn = payment.transaction_id

    if incident.status in TERMINAL_INCIDENT_STATUSES:
        return None

    incident.status = str(IncidentStatus.INVESTIGATING)
    await audit.record(
        transaction_id=txn, incident_id=incident.id, event=AuditEvent.INVESTIGATION_STARTED, actor=AI_ACTOR,
        reason="Collecting evidence from gateway, bank, merchant, ledger, webhook and historical incidents",
        evidence={"incident_type": incident.type, "snapshot": snapshot(payment)},
    )
    await session.commit()
    await _pause(delay)

    outcome = await InvestigatorService(session).investigate(incident, payment, force_fallback=force_fallback)
    result = outcome.result
    session.add(
        Investigation(
            incident_id=incident.id,
            model=outcome.model,
            used_fallback=outcome.used_fallback,
            summary=result.summary,
            evidence=to_jsonable({
                "facts": result.evidence,
                "tool_calls": outcome.tool_calls,
                "fallback_reason": outcome.fallback_reason,
                "bundle": outcome.bundle,
            }),
            historical_matches=to_jsonable(outcome.matches),
            recommendation=result.to_public(),
            latency_ms=outcome.latency_ms,
            created_at=utcnow(),
        )
    )
    incident.root_cause = result.root_cause
    incident.confidence = result.confidence
    incident.ai_summary = result.summary
    incident.recommended_action = str(result.recommended_action)
    incident.risk = str(result.risk)
    incident.requires_human = result.requires_human

    if outcome.matches:
        top = outcome.matches[0]
        await audit.record(
            transaction_id=txn, incident_id=incident.id, event=AuditEvent.HISTORICAL_MATCH_FOUND, actor="rag-service",
            reason=f"{top['title']} ({round(top['similarity'] * 100)}% similarity)",
            evidence={"retrieval": top.get("retrieval"), "matches": [m["title"] for m in outcome.matches]},
            result={"resolution": top["resolution"]},
        )
    await audit.record(
        transaction_id=txn, incident_id=incident.id, event=AuditEvent.AI_RECOMMENDATION_CREATED,
        actor=f"{AI_ACTOR} ({outcome.model})",
        reason=f"Root cause: {result.root_cause}",
        evidence={"facts": result.evidence, "used_fallback": outcome.used_fallback,
                  "fallback_reason": outcome.fallback_reason},
        result=result.to_public(),
    )
    await session.commit()
    await _pause(delay)

    # DECIDE: deterministic policy, AI cannot override it.
    ctx = PolicyContext.from_payment(
        payment,
        bank_amount=await _bank_amount(session, payment),
        ledger_amount=await _ledger_amount(session, payment),
        refund_completed=await _refund_completed(session, payment),
        ai_confidence=result.confidence,
        ai_risk=str(result.risk),
        ai_requires_human=result.requires_human,
    )
    engine = PolicyEngine()
    evaluation = engine.evaluate(str(result.recommended_action), ctx)
    incident.policy_decision = str(evaluation.decision)
    await audit.record(
        transaction_id=txn, incident_id=incident.id, event=AuditEvent.POLICY_EVALUATED, actor=POLICY_ACTOR,
        reason=f"{evaluation.label}: {'; '.join(evaluation.reasons)}",
        evidence={"action": evaluation.action_type, "checks": [c.model_dump() for c in evaluation.checks]},
        result={"decision": str(evaluation.decision)},
    )
    await session.commit()
    await _pause(delay)

    executor = ActionExecutor(session)
    if evaluation.decision == PolicyDecision.DENY:
        escalation = engine.evaluate(ActionType.ESCALATE, ctx)
        action, created = await executor.request(
            incident=incident, payment=payment, action_type=ActionType.ESCALATE, policy=escalation,
            requested_by=POLICY_ACTOR,
            reason=f"Policy denied {evaluation.action_type}: {'; '.join(evaluation.reasons)}",
        )
    else:
        action, created = await executor.request(
            incident=incident, payment=payment, action_type=str(result.recommended_action), policy=evaluation,
            requested_by=AI_ACTOR, reason=result.root_cause,
        )

    if created:
        await audit.record(
            transaction_id=txn, incident_id=incident.id, event=AuditEvent.ACTION_CREATED, actor=ORCHESTRATOR_ACTOR,
            reason=f"{action.action_type} requested ({action.idempotency_key}) - {action.status.replace('_', ' ')}",
            evidence={"idempotency_key": action.idempotency_key, "requested_by": action.requested_by},
            result={"action_id": str(action.id), "status": action.status},
        )

    if action.status == ActionStatus.PENDING_APPROVAL:
        incident.status = str(IncidentStatus.AWAITING_APPROVAL)
        incident.requires_human = True
        await audit.record(
            transaction_id=txn, incident_id=incident.id, event=AuditEvent.APPROVAL_REQUESTED, actor=POLICY_ACTOR,
            reason=f"{action.action_type} of {money(payment.amount, payment.currency)} requires human approval",
            evidence={"idempotency_key": action.idempotency_key, "reasons": evaluation.reasons},
            result={"action_id": str(action.id)},
        )
        await notify(session, incident=incident, transaction_id=txn,
                     message=f"Approval needed: {action.action_type} {money(payment.amount, payment.currency)} for {txn}")
        await session.commit()
        return None
    if action.status in (ActionStatus.APPROVED, ActionStatus.COMPLETED):
        incident.status = str(IncidentStatus.REMEDIATING)
        await session.commit()
        return action.id

    # Previously rejected / failed action for this transaction: never retry silently.
    incident.status = str(IncidentStatus.ESCALATED)
    await audit.record(
        transaction_id=txn, incident_id=incident.id, event=AuditEvent.INCIDENT_ESCALATED, actor=ORCHESTRATOR_ACTOR,
        reason=f"Existing action {action.idempotency_key} is {action.status}; manual handling required",
    )
    await session.commit()
    return None


# ---- ACT + VERIFY ----------------------------------------------------------------------------


@dataclass
class ActionStageResult:
    action: Action
    incident_status: str
    verification: dict | None
    deduplicated: bool
    message: str


async def run_action_stage(
    session: AsyncSession, action_id: uuid.UUID, *, actor: str = EXECUTOR_ACTOR, delay: float = 0.0
) -> ActionStageResult:
    audit = AuditService(session)
    outcome = await ActionExecutor(session).execute(action_id, actor=actor)
    action = outcome.action
    incident = await IncidentService(session).reload(action.incident_id)
    payment = await PaymentService(session).reload(incident.payment_id)
    txn = payment.transaction_id

    if outcome.deduplicated:
        previous = (action.result or {}).get("verification") or {}
        if incident.status in (IncidentStatus.INVESTIGATING, IncidentStatus.REMEDIATING):
            incident.status = str(
                IncidentStatus.RESOLVED if previous.get("status") == "PASSED" else IncidentStatus.ESCALATED
            )
        await session.commit()
        return ActionStageResult(action, incident.status, previous or None, True,
                                 f"Idempotent replay of {action.idempotency_key}: previous result returned")

    if not outcome.success:
        incident.status = str(IncidentStatus.ESCALATED)
        incident.final_snapshot = snapshot(payment)
        await audit.record(
            transaction_id=txn, incident_id=incident.id, event=AuditEvent.INCIDENT_ESCALATED, actor=ORCHESTRATOR_ACTOR,
            reason=f"Action {action.action_type} failed: {outcome.error}",
        )
        await notify(session, incident=incident, transaction_id=txn, message=f"Escalated: {action.action_type} failed")
        await session.commit()
        return ActionStageResult(action, incident.status, None, False, f"{action.action_type} failed")

    await session.commit()
    await _pause(delay)

    verification = await _verify_with_retry(session, action, payment, incident)
    action.result = to_jsonable({**(action.result or {}), "verification": verification.as_dict()})
    incident.final_snapshot = verification.snapshot

    if verification.status == "NOT_APPLICABLE":
        incident.status = str(IncidentStatus.ESCALATED)
        await audit.record(
            transaction_id=txn, incident_id=incident.id, event=AuditEvent.INCIDENT_ESCALATED, actor=ORCHESTRATOR_ACTOR,
            reason=action.reason or "Escalated for human investigation",
            result={"ticket": (action.result or {}).get("ticket")},
        )
        await notify(session, incident=incident, transaction_id=txn,
                     message=f"Escalated {incident.incident_number} ({incident.type}) to payments on-call")
        message = "Incident escalated to on-call"
    elif verification.passed:
        await audit.record(
            transaction_id=txn, incident_id=incident.id, event=AuditEvent.VERIFICATION_PASSED, actor=VERIFIER_ACTOR,
            reason=f"All {len(verification.checks)} post-action checks passed",
            evidence={"checks": verification.checks}, result={"snapshot": verification.snapshot},
        )
        await session.commit()
        await _pause(delay)
        await _post_remediation_reconcile(session, payment, incident)
        incident.status = str(IncidentStatus.RESOLVED)
        incident.resolved_at = utcnow()
        await audit.record(
            transaction_id=txn, incident_id=incident.id, event=AuditEvent.INCIDENT_RESOLVED, actor=ORCHESTRATOR_ACTOR,
            reason=f"{incident.incident_number} resolved via {action.action_type}",
            result={"final_snapshot": verification.snapshot},
        )
        message = "Action executed and verified; incident resolved"
    else:
        await audit.record(
            transaction_id=txn, incident_id=incident.id, event=AuditEvent.VERIFICATION_FAILED, actor=VERIFIER_ACTOR,
            reason="Post-action state does not match expectations",
            evidence={"checks": verification.checks}, result={"snapshot": verification.snapshot},
        )
        incident.status = str(IncidentStatus.ESCALATED)
        await audit.record(
            transaction_id=txn, incident_id=incident.id, event=AuditEvent.INCIDENT_ESCALATED, actor=ORCHESTRATOR_ACTOR,
            reason="Verification failed after remediation",
        )
        message = "Verification failed; incident escalated"
    await session.commit()
    return ActionStageResult(action, incident.status, verification.as_dict(), False, message)


async def _post_remediation_reconcile(session: AsyncSession, payment: Payment, incident: Incident) -> None:
    """RECONCILE stage: re-compare all systems after a verified fix and record the run."""
    payment = await PaymentService(session).reload(payment.id)
    result = await ReconciliationService(session).reconcile(payment)
    payment.reconciliation_status = str(
        ReconciliationStatus.MATCHED if result.consistent else ReconciliationStatus.MISMATCH
    )
    session.add(ReconciliationRun(
        payment_id=payment.id, trigger="post-remediation", checked=1, mismatches=0 if result.consistent else 1,
        result=to_jsonable(result.as_dict()), started_at=utcnow(), finished_at=utcnow(),
    ))
    await AuditService(session).record(
        transaction_id=payment.transaction_id, incident_id=incident.id, event=AuditEvent.RECONCILIATION_COMPLETED,
        actor="reconciliation-engine",
        reason="Post-remediation reconciliation: all systems agree" if result.consistent
        else f"Post-remediation reconciliation still reports {result.incident_type}",
        evidence={"snapshot": result.snapshot}, result={"consistent": result.consistent},
    )


VERIFY_ATTEMPTS = 3


async def _verify_with_retry(session: AsyncSession, action: Action, payment: Payment, incident: Incident):
    """Verification with bounded retries. VERIFICATION_TIMEOUT injection makes the first attempt time out."""
    from app.failure_injection.service import FailureInjectionService, VerificationTimeout

    audit = AuditService(session)
    injections = FailureInjectionService(session)
    await audit.record(
        transaction_id=payment.transaction_id, incident_id=incident.id, event=AuditEvent.VERIFICATION_STARTED,
        actor=VERIFIER_ACTOR, reason=f"Re-reading all systems after {action.action_type}",
    )
    await session.commit()
    for attempt in range(1, VERIFY_ATTEMPTS + 1):
        try:
            if await injections.take(payment, "VERIFICATION_TIMEOUT", "Verifier did not get a response within 5s"):
                await session.commit()
                raise VerificationTimeout()
            return await VerificationService(session).verify(action.action_type, payment.id)
        except VerificationTimeout as exc:
            await audit.record(
                transaction_id=payment.transaction_id, incident_id=incident.id, event=AuditEvent.VERIFICATION_RETRY,
                actor=VERIFIER_ACTOR, reason=f"Attempt {attempt} timed out ({exc}); retrying with backoff",
            )
            await session.commit()
            await asyncio.sleep(min(0.5 * attempt, 2.0) if settings.PIPELINE_STEP_DELAY_SECONDS else 0)
    return await VerificationService(session).verify(action.action_type, payment.id)


# ---- full pipeline (in-process worker) -----------------------------------------------------------


async def run_incident_pipeline(
    incident_id: uuid.UUID,
    *,
    factory: async_sessionmaker[AsyncSession] | None = None,
    force_fallback: bool = False,
    delay: float | None = None,
) -> str | None:
    factory = factory or SessionLocal
    delay = settings.PIPELINE_STEP_DELAY_SECONDS if delay is None else delay
    try:
        async with factory() as session:
            action_id = await run_investigation_stage(session, incident_id, force_fallback=force_fallback, delay=delay)
            if action_id is not None:
                await _pause(delay)
                await run_action_stage(session, action_id, delay=delay)
            incident = await IncidentService(session).reload(incident_id)
            return incident.status
    except Exception as exc:  # noqa: BLE001 - failed jobs must never disappear silently
        logger.exception("Incident pipeline failed for %s", incident_id)
        await record_pipeline_failure(factory, incident_id, f"pipeline: {exc}")
        return None


async def record_pipeline_failure(
    factory: async_sessionmaker[AsyncSession], incident_id: uuid.UUID | str, error: str, job: str = "incident-pipeline"
) -> None:
    try:
        async with factory() as session:
            incident = await session.get(Incident, uuid.UUID(str(incident_id)))
            if incident is None:
                return
            payment = await session.get(Payment, incident.payment_id)
            incident.status = str(IncidentStatus.ESCALATED)
            audit = AuditService(session)
            await audit.record(
                transaction_id=payment.transaction_id, incident_id=incident.id, event=AuditEvent.JOB_FAILED,
                actor=job, reason=f"Background job failed: {error}"[:1000],
            )
            await audit.record(
                transaction_id=payment.transaction_id, incident_id=incident.id, event=AuditEvent.INCIDENT_ESCALATED,
                actor=ORCHESTRATOR_ACTOR, reason="Escalated after background job failure",
            )
            await session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("Could not record pipeline failure for %s", incident_id)


# ---- HUMAN APPROVAL ----------------------------------------------------------------------------


async def approve_action(session: AsyncSession, action_id: uuid.UUID, *, approver: str, note: str | None = None) -> ActionStageResult:
    audit = AuditService(session)
    action = await session.get(Action, action_id, populate_existing=True)
    if action is None:
        raise NotFoundError(f"Action {action_id} not found")
    incident = await IncidentService(session).reload(action.incident_id)
    payment = await PaymentService(session).reload(incident.payment_id)

    if action.status == ActionStatus.COMPLETED:
        return ActionStageResult(action, incident.status, (action.result or {}).get("verification"), True,
                                 "Action already executed; previous result returned")
    if action.status != ActionStatus.PENDING_APPROVAL:
        raise ConflictError(f"Action is {action.status}; only PENDING_APPROVAL actions can be approved")

    # Policy re-check at approval time: state may have changed since the recommendation.
    ctx = PolicyContext.from_payment(
        payment,
        bank_amount=await _bank_amount(session, payment),
        ledger_amount=await _ledger_amount(session, payment),
        refund_completed=await _refund_completed(session, payment),
        ai_confidence=incident.confidence,
        ai_risk=incident.risk,
        ai_requires_human=incident.requires_human,
        human_approved=True,
    )
    evaluation = PolicyEngine().evaluate(action.action_type, ctx)
    await audit.record(
        transaction_id=payment.transaction_id, incident_id=incident.id, event=AuditEvent.POLICY_EVALUATED,
        actor=POLICY_ACTOR, reason=f"Approval re-check: {evaluation.label}: {'; '.join(evaluation.reasons)}",
        evidence={"checks": [c.model_dump() for c in evaluation.checks], "approver": approver},
        result={"decision": str(evaluation.decision)},
    )
    if evaluation.decision != PolicyDecision.ALLOW:
        action.status = str(ActionStatus.FAILED)
        action.completed_at = utcnow()
        action.result = {"error": "Policy re-check denied the action", "reasons": evaluation.reasons}
        incident.status = str(IncidentStatus.ESCALATED)
        await audit.record(
            transaction_id=payment.transaction_id, incident_id=incident.id, event=AuditEvent.INCIDENT_ESCALATED,
            actor=POLICY_ACTOR, reason="Policy re-check failed after approval; escalated",
        )
        await session.commit()
        return ActionStageResult(action, incident.status, None, False, "Policy re-check denied the action")

    session.add(Approval(action_id=action.id, decision="APPROVED", approver=approver, note=note,
                         policy_recheck=to_jsonable(evaluation.model_dump())))
    action.status = str(ActionStatus.APPROVED)
    action.approved_by = approver
    action.policy = to_jsonable({**(action.policy or {}), "approval_recheck": evaluation.model_dump()})
    incident.status = str(IncidentStatus.REMEDIATING)
    await audit.record(
        transaction_id=payment.transaction_id, incident_id=incident.id, event=AuditEvent.ACTION_APPROVED,
        actor=approver, reason=note or f"{action.action_type} of {money(payment.amount, payment.currency)} approved by {approver}",
        evidence={"idempotency_key": action.idempotency_key},
        result={"status": action.status},
    )
    await session.commit()
    return await run_action_stage(session, action.id, actor=EXECUTOR_ACTOR)


async def reject_action(session: AsyncSession, action_id: uuid.UUID, *, approver: str, reason: str) -> ActionStageResult:
    audit = AuditService(session)
    action = await session.get(Action, action_id, populate_existing=True)
    if action is None:
        raise NotFoundError(f"Action {action_id} not found")
    if action.status != ActionStatus.PENDING_APPROVAL:
        raise ConflictError(f"Action is {action.status}; only PENDING_APPROVAL actions can be rejected")
    incident = await IncidentService(session).reload(action.incident_id)
    payment = await PaymentService(session).reload(incident.payment_id)

    session.add(Approval(action_id=action.id, decision="REJECTED", approver=approver, note=reason))
    action.status = str(ActionStatus.REJECTED)
    action.approved_by = approver
    action.completed_at = utcnow()
    action.result = {"rejected": True, "reason": reason}
    incident.status = str(IncidentStatus.ESCALATED)
    incident.final_snapshot = snapshot(payment)
    await audit.record(
        transaction_id=payment.transaction_id, incident_id=incident.id, event=AuditEvent.ACTION_REJECTED,
        actor=approver, reason=reason, evidence={"idempotency_key": action.idempotency_key},
        result={"status": action.status},
    )
    await audit.record(
        transaction_id=payment.transaction_id, incident_id=incident.id, event=AuditEvent.INCIDENT_ESCALATED,
        actor=ORCHESTRATOR_ACTOR, reason="Recommended action rejected by operator; manual handling required",
    )
    await session.commit()
    return ActionStageResult(action, incident.status, None, False, "Action rejected; incident escalated")


# ---- external events (webhooks / connectors) --------------------------------------------------------

EXTERNAL_STATUS_FIELDS = {"gateway_status", "bank_status", "merchant_status", "ledger_status", "webhook_status"}


async def apply_payment_event(
    session: AsyncSession,
    *,
    transaction_id: str,
    source: str,
    event_type: str,
    status_updates: dict[str, str] | None = None,
    target_state: str | None = None,
    payload: dict | None = None,
) -> Incident | None:
    """Record an external event, apply whitelisted status updates, reconcile."""
    payments = PaymentService(session)
    payment = await payments.get_by_transaction_id(transaction_id)
    for key, value in (status_updates or {}).items():
        if key in EXTERNAL_STATUS_FIELDS:
            setattr(payment, key, value)
    if target_state and target_state != payment.overall_status:
        PaymentStateService.transition(payment, PaymentState(target_state))
    await payments.add_event(payment, source=source, event_type=event_type, label=event_type.replace("_", " ").title(),
                             payload=payload or {})
    await AuditService(session).record(
        transaction_id=transaction_id, event=AuditEvent.PAYMENT_EVENT_RECEIVED, actor=f"{source.lower()}-connector",
        reason=f"{source} {event_type}", evidence={"status_updates": status_updates or {}, "payload": payload or {}},
    )
    _, incident = await detect(session, payment)
    await session.commit()
    return incident
