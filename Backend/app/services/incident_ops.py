"""Human resolution for incidents the automation could not close.

Before this, an ESCALATED incident had no way out: nothing in the console could
change it, so it stayed red forever. Operators can now:

* acknowledge  - stop alert re-escalation, take ownership
* retry        - authorise the playbook remediation (policy still re-checks preconditions)
* resolve      - close after a fresh reconciliation; if systems still disagree, only with explicit risk acceptance
* close        - close as a false positive (pending actions are rejected, nothing moves)

Every path is policy-checked where money could move, idempotent, and audited.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ActionStatus, ActionType, AuditEvent, IncidentStatus, PolicyDecision
from app.core.errors import ConflictError
from app.models import Action, Approval, Incident
from app.models.base import utcnow
from app.notifications.service import ack_incident_alerts, raise_alert, resolve_incident_alerts
from app.services import orchestrator
from app.services.audit_service import AuditService
from app.services.incident_service import IncidentService
from app.services.payment_service import PaymentService, snapshot
from app.services.playbook import playbook_action
from app.services.policy_service import PolicyEngine
from app.services.reconciliation_service import ReconciliationService
from app.utils.idempotency import make_idempotency_key
from app.utils.serialization import to_jsonable

OPEN_FOR_HUMANS = (IncidentStatus.ESCALATED, IncidentStatus.OPEN, IncidentStatus.AWAITING_APPROVAL,
                   IncidentStatus.INVESTIGATING, IncidentStatus.REMEDIATING)


async def _load(session: AsyncSession, incident_id: uuid.UUID):
    incident = await IncidentService(session).reload(incident_id)
    payment = await PaymentService(session).reload(incident.payment_id)
    return incident, payment


async def acknowledge(session: AsyncSession, incident_id: uuid.UUID, *, by: str) -> Incident:
    incident, payment = await _load(session, incident_id)
    incident.acknowledged_by = by
    incident.acknowledged_at = utcnow()
    await ack_incident_alerts(session, incident.id, by=by)
    await AuditService(session).record(
        transaction_id=payment.transaction_id, incident_id=incident.id, event=AuditEvent.INCIDENT_ACKNOWLEDGED,
        actor=by, reason=f"{incident.incident_number} acknowledged by {by}",
    )
    await session.commit()
    return incident


async def retry_remediation(session: AsyncSession, incident_id: uuid.UUID, *, by: str, note: str | None = None):
    """Human-authorised re-run of the playbook action (new idempotency attempt key)."""
    incident, payment = await _load(session, incident_id)
    if incident.status not in (IncidentStatus.ESCALATED, IncidentStatus.OPEN):
        raise ConflictError(f"Incident is {incident.status}; retry is for escalated incidents")
    action_type = str(playbook_action(incident.type))
    if action_type == ActionType.ESCALATE:
        raise ConflictError(f"{incident.type} has no automated fix; resolve it manually after checking with finance")

    ctx, risk = await orchestrator._policy_context(session, incident, payment, human_approved=True, action=action_type)
    evaluation = PolicyEngine().evaluate(action_type, ctx)
    audit = AuditService(session)
    await audit.record(
        transaction_id=payment.transaction_id, incident_id=incident.id, event=AuditEvent.POLICY_EVALUATED,
        actor="policy-engine", reason=f"Human retry re-check: {evaluation.label}: {'; '.join(evaluation.reasons)}",
        evidence={"checks": [c.model_dump() for c in evaluation.checks], "requested_by": by},
        result={"decision": str(evaluation.decision)},
    )
    if evaluation.decision != PolicyDecision.ALLOW:
        await session.commit()
        raise ConflictError(
            f"Policy will not run {action_type}: {'; '.join(evaluation.reasons)}. "
            "If the systems are already consistent, use Resolve instead."
        )

    attempts = await session.scalar(select(func.count()).select_from(Action).where(Action.incident_id == incident.id)) or 0
    action = Action(
        incident_id=incident.id, action_type=action_type,
        idempotency_key=f"{make_idempotency_key(action_type, payment.transaction_id)}:attempt-{attempts + 1}",
        status=str(ActionStatus.APPROVED), requested_by=by, approved_by=by,
        reason=note or f"Human-authorised retry of {action_type}",
        policy=to_jsonable(evaluation.model_dump()), created_at=utcnow(),
    )
    session.add(action)
    await session.flush()
    session.add(Approval(action_id=action.id, decision="APPROVED", approver=by, note=note,
                         policy_recheck=to_jsonable(evaluation.model_dump())))
    incident.status = str(IncidentStatus.REMEDIATING)
    incident.acknowledged_by = incident.acknowledged_by or by
    incident.acknowledged_at = incident.acknowledged_at or utcnow()
    incident.risk_score, incident.risk_factors = risk.score, risk.as_dict()
    await ack_incident_alerts(session, incident.id, by=by)
    await audit.record(
        transaction_id=payment.transaction_id, incident_id=incident.id, event=AuditEvent.HUMAN_RETRY_REQUESTED,
        actor=by, reason=note or f"{by} authorised a retry of {action_type}",
        evidence={"idempotency_key": action.idempotency_key}, result={"action_id": str(action.id)},
    )
    await session.commit()
    return await orchestrator.run_action_stage(session, action.id)


async def resolve_manually(session: AsyncSession, incident_id: uuid.UUID, *, by: str, note: str,
                           accept_risk: bool = False) -> Incident:
    incident, payment = await _load(session, incident_id)
    if incident.status not in OPEN_FOR_HUMANS:
        raise ConflictError(f"Incident is already {incident.status}")
    result = await ReconciliationService(session).reconcile(payment)
    if not result.consistent and not accept_risk:
        raise ConflictError(
            f"Systems still disagree ({result.incident_type}: {result.summary}). Fix them first, or resolve with "
            "explicit risk acceptance."
        )
    await _reject_pending(session, incident, by=by, reason="Incident resolved manually")
    incident.status = str(IncidentStatus.RESOLVED)
    incident.resolved_at = utcnow()
    incident.resolution = "MANUAL" if result.consistent else "ACCEPTED_RISK"
    incident.resolution_note = note
    incident.final_snapshot = snapshot(payment)
    incident.acknowledged_by = incident.acknowledged_by or by
    incident.acknowledged_at = incident.acknowledged_at or utcnow()
    payment.reconciliation_status = "MATCHED" if result.consistent else "MISMATCH"
    await AuditService(session).record(
        transaction_id=payment.transaction_id, incident_id=incident.id, event=AuditEvent.INCIDENT_RESOLVED, actor=by,
        reason=f"Resolved manually by {by} ({incident.resolution}): {note}",
        evidence={"reconciliation": result.as_dict()}, result={"resolution": incident.resolution},
    )
    await raise_alert(session, incident=incident, payment=payment, kind="INCIDENT_RESOLVED",
                      detail=f"Resolved manually by {by}: {note}")
    await resolve_incident_alerts(session, incident.id)
    orchestrator._trace(session, incident, payment, "human_resolution", f"{incident.resolution} by {by}")
    await session.commit()
    return incident


async def close_incident(session: AsyncSession, incident_id: uuid.UUID, *, by: str, reason: str) -> Incident:
    incident, payment = await _load(session, incident_id)
    if incident.status not in OPEN_FOR_HUMANS:
        raise ConflictError(f"Incident is already {incident.status}")
    await _reject_pending(session, incident, by=by, reason=f"Closed as false positive: {reason}")
    incident.status = str(IncidentStatus.CLOSED)
    incident.resolved_at = utcnow()
    incident.resolution = "FALSE_POSITIVE"
    incident.resolution_note = reason
    await AuditService(session).record(
        transaction_id=payment.transaction_id, incident_id=incident.id, event=AuditEvent.INCIDENT_CLOSED, actor=by,
        reason=f"Closed as false positive by {by}: {reason}", result={"resolution": "FALSE_POSITIVE"},
    )
    await resolve_incident_alerts(session, incident.id)
    orchestrator._trace(session, incident, payment, "human_resolution", f"closed by {by}")
    await session.commit()
    return incident


async def _reject_pending(session: AsyncSession, incident: Incident, *, by: str, reason: str) -> None:
    rows = await session.scalars(select(Action).where(Action.incident_id == incident.id,
                                                      Action.status == str(ActionStatus.PENDING_APPROVAL)))
    for action in rows:
        action.status = str(ActionStatus.REJECTED)
        action.approved_by = by
        action.completed_at = utcnow()
        action.result = {"rejected": True, "reason": reason}
        session.add(Approval(action_id=action.id, decision="REJECTED", approver=by, note=reason))
