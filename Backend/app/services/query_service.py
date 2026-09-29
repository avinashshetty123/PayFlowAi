"""Read models: turn ORM rows into API response schemas (never expose ORM objects directly)."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ActionStatus, ActionType, IncidentStatus
from app.models import Action, FailureInjection, Incident, Investigation, Payment, ProviderTransaction, WebhookEvent
from app.schemas.action import ActionOut, ApprovalQueueItem
from app.schemas.incident import AuditLogOut, IncidentDetail, IncidentSummary, TimelineItem
from app.schemas.investigation import InvestigationOut
from app.schemas.payment import FailureInjectionOut, PaymentOut, ProviderTransactionOut, WebhookEventOut
from app.services.audit_service import AuditService
from app.services.incident_service import IncidentService
from app.services.payment_service import PaymentService, snapshot
from app.services.reconciliation_service import ReconciliationService


def _ai_status(incident: Incident, investigation: Investigation | None) -> str:
    if investigation is not None:
        return "ANALYZED_FALLBACK" if investigation.used_fallback else "ANALYZED"
    if incident.status == IncidentStatus.INVESTIGATING:
        return "ANALYZING"
    return "QUEUED"


def summarize(
    incident: Incident, payment: Payment, investigation: Investigation | None, action: Action | None
) -> IncidentSummary:
    return IncidentSummary(
        id=incident.id,
        incident_number=incident.incident_number,
        transaction_id=payment.transaction_id,
        amount=float(payment.amount),
        type=incident.type,
        severity=incident.severity,
        status=incident.status,
        root_cause=incident.root_cause,
        confidence=incident.confidence,
        recommended_action=incident.recommended_action,
        requires_human=incident.requires_human,
        risk=incident.risk,
        policy_decision=incident.policy_decision,
        ai_status=_ai_status(incident, investigation),
        action_status=action.status if action else None,
        created_at=incident.created_at,
        resolved_at=incident.resolved_at,
        currency=payment.currency,
        provider=payment.provider,
        provider_status=payment.provider_status,
        failure_source=incident.failure_source,
        injected_scenario=incident.injected_scenario,
    )


async def list_incident_summaries(
    session: AsyncSession, *, limit: int = 50, offset: int = 0, status: str | None = None, active: bool | None = None
) -> tuple[list[IncidentSummary], int]:
    service = IncidentService(session)
    rows, total = await service.list_with_payments(limit=limit, offset=offset, status=status, active=active)
    ids = [incident.id for incident, _ in rows]
    actions = await service.latest_actions(ids)
    investigations = await service.investigated_ids(ids)
    return [summarize(i, p, investigations.get(i.id), actions.get(i.id)) for i, p in rows], total


async def incident_detail(session: AsyncSession, incident_id: uuid.UUID) -> IncidentDetail:
    service = IncidentService(session)
    incident = await service.get(incident_id)
    payment = await PaymentService(session).get(incident.payment_id)
    investigation = await service.latest_investigation(incident.id)
    actions = await service.actions(incident.id)
    base = summarize(incident, payment, investigation, actions[-1] if actions else None)
    return IncidentDetail(
        **base.model_dump(),
        ai_summary=incident.ai_summary,
        initial_snapshot=incident.initial_snapshot or {},
        final_snapshot=incident.final_snapshot,
        current_snapshot=snapshot(payment),
        detection_findings=incident.detection_findings or [],
        payment=PaymentOut.model_validate(payment),
        investigation=InvestigationOut.model_validate(investigation) if investigation else None,
        actions=[ActionOut.model_validate(a) for a in actions],
        failure_injections=await failure_injections_out(session, payment.id),
        webhook_events=await webhook_events_out(session, payment.transaction_id),
        provider_transactions=await provider_transactions_out(session, payment.id),
    )


async def failure_injections_out(session: AsyncSession, payment_id: uuid.UUID) -> list[FailureInjectionOut]:
    rows = await session.scalars(
        select(FailureInjection).where(FailureInjection.payment_id == payment_id).order_by(FailureInjection.created_at)
    )
    return [
        FailureInjectionOut(id=r.id, scenario=r.scenario, enabled=r.enabled, injected_by=r.injected_by,
                            created_at=r.created_at, injected_at=r.injected_at, metadata=r.metadata_ or {})
        for r in rows
    ]


async def webhook_events_out(session: AsyncSession, transaction_id: str) -> list[WebhookEventOut]:
    rows = await session.scalars(
        select(WebhookEvent).where(WebhookEvent.transaction_id == transaction_id).order_by(WebhookEvent.received_at)
    )
    return [WebhookEventOut.model_validate(r) for r in rows]


async def provider_transactions_out(session: AsyncSession, payment_id: uuid.UUID) -> list[ProviderTransactionOut]:
    rows = await session.scalars(
        select(ProviderTransaction).where(ProviderTransaction.payment_id == payment_id).order_by(ProviderTransaction.created_at)
    )
    return [ProviderTransactionOut.model_validate(r) for r in rows]


_EVENT_TONES = {
    "error": {
        "ORDER_UPDATE_FAILED", "LEDGER_POST_FAILED", "GATEWAY_FAILED", "REFUND_FAILED", "GATEWAY_TIMEOUT",
        "GATEWAY_STATUS_UNKNOWN", "BANK_SETTLED_SHORT", "DUPLICATE_ORDER_PAYMENT", "WEBHOOK_SLA_BREACHED",
    },
    "warn": {
        "WEBHOOK_DELIVERY_FAILED", "GATEWAY_PENDING", "REFUND_REQUESTED", "BANK_LOOKUP_EMPTY", "ORDER_FAILED",
        "LEDGER_VOIDED", "GATEWAY_PROCESSING",
    },
}

_ACTION_LABELS = {
    ActionType.RECONCILE_LEDGER: "Ledger reconciled",
    ActionType.RETRY_WEBHOOK: "Webhook redelivered",
    ActionType.REFUND: "Refund executed",
    ActionType.MARK_PAYMENT_FAILED: "Payment marked failed",
    ActionType.ESCALATE: "Escalated to on-call",
}

# audit event -> (label, kind, tone); None label means "skip"
_AUDIT_TIMELINE: dict[str, tuple[str | None, str, str]] = {
    "MISMATCH_DETECTED": ("Mismatch detected", "detection", "error"),
    "INVESTIGATION_STARTED": ("AI investigation started", "ai", "info"),
    "HISTORICAL_MATCH_FOUND": ("Historical match found", "ai", "info"),
    "AI_RECOMMENDATION_CREATED": ("Root cause identified", "ai", "ok"),
    "POLICY_EVALUATED": ("Policy evaluated", "policy", "ok"),
    "APPROVAL_REQUESTED": ("Human approval requested", "human", "warn"),
    "ACTION_APPROVED": ("Action approved", "human", "ok"),
    "ACTION_REJECTED": ("Action rejected", "human", "error"),
    "ACTION_EXECUTED": ("Action executed", "action", "ok"),
    "ACTION_FAILED": ("Action failed", "action", "error"),
    "ACTION_DEDUPLICATED": ("Duplicate request ignored (idempotent)", "action", "info"),
    "VERIFICATION_PASSED": ("Verification passed", "verification", "ok"),
    "VERIFICATION_FAILED": ("Verification failed", "verification", "error"),
    "INCIDENT_RESOLVED": ("Incident resolved", "incident", "ok"),
    "INCIDENT_ESCALATED": ("Incident escalated", "incident", "warn"),
    "JOB_FAILED": ("Background job failed", "incident", "error"),
    "ACTION_CREATED": ("Action created", "action", "info"),
    "VERIFICATION_STARTED": ("Verification started", "verification", "info"),
    "VERIFICATION_RETRY": ("Verification retry", "verification", "warn"),
    "FAILURE_INJECTED": ("Demo failure injected", "injection", "warn"),
    "RECONCILIATION_COMPLETED": ("Reconciliation completed", "detection", "info"),
}

# Provider / webhook audit events shown on the incident timeline (payment-level, no incident id).
_PAYMENT_AUDIT_TIMELINE: dict[str, tuple[str, str, str]] = {
    "PAYMENT_APPROVED": ("Buyer approved in PayPal", "provider", "ok"),
    "PAYMENT_CAPTURE_STARTED": ("Capture requested", "provider", "info"),
    "WEBHOOK_RECEIVED": ("Webhook received", "webhook", "info"),
    "WEBHOOK_VERIFIED": ("Webhook signature verified", "webhook", "ok"),
    "WEBHOOK_REJECTED": ("Webhook rejected", "webhook", "error"),
    "WEBHOOK_DUPLICATE": ("Duplicate webhook ignored", "webhook", "warn"),
    "WEBHOOK_DELAYED": ("Webhook delayed (injected)", "injection", "warn"),
    "WEBHOOK_DROPPED": ("Webhook dropped (injected)", "injection", "error"),
    "FAILURE_INJECTED": ("Demo failure injected", "injection", "warn"),
    "RECONCILIATION_STARTED": ("Reconciliation started", "detection", "info"),
    "REFUND_REQUESTED": ("Refund requested", "payment", "warn"),
}


async def incident_timeline(session: AsyncSession, incident_id: uuid.UUID) -> list[TimelineItem]:
    incident = await IncidentService(session).get(incident_id)
    payments = PaymentService(session)
    items: list[TimelineItem] = []

    for event in await payments.events(incident.payment_id):
        if event.payload.get("actor") == "action-executor":
            continue  # remediation side effects are represented by ACTION_EXECUTED
        tone = next((t for t, types in _EVENT_TONES.items() if event.event_type in types), "ok")
        items.append(TimelineItem(
            timestamp=event.created_at, source=event.source, label=event.payload.get("label", event.event_type),
            detail=event.payload.get("detail"), kind="payment", tone=tone,
        ))

    payment = await payments.get(incident.payment_id)
    for log in await AuditService(session).for_transaction(payment.transaction_id):
        if log.incident_id is None and log.event in _PAYMENT_AUDIT_TIMELINE:
            label, kind, tone = _PAYMENT_AUDIT_TIMELINE[log.event]
            items.append(TimelineItem(timestamp=log.created_at, source=log.actor, label=label, detail=log.reason,
                                      kind=kind, tone=tone))

    for log in await AuditService(session).for_incident(incident.id):
        spec = _AUDIT_TIMELINE.get(log.event)
        if spec is None or spec[0] is None:
            continue
        label, kind, tone = spec
        if log.event == "ACTION_EXECUTED":
            label = _ACTION_LABELS.get(log.evidence.get("action_type"), label)
        elif log.event == "ACTION_APPROVED":
            label = f"Approved by {log.actor}"
        elif log.event == "ACTION_REJECTED":
            label = f"Rejected by {log.actor}"
        elif log.event == "POLICY_EVALUATED":
            decision = log.result.get("decision")
            tone = "ok" if decision == "ALLOW" else ("warn" if decision == "HUMAN_APPROVAL_REQUIRED" else "error")
        items.append(TimelineItem(
            timestamp=log.created_at, source=log.actor, label=label, detail=log.reason, kind=kind, tone=tone,
        ))

    items.sort(key=lambda i: i.timestamp)
    return items


async def incident_audit(session: AsyncSession, incident_id: uuid.UUID) -> list[AuditLogOut]:
    incident = await IncidentService(session).get(incident_id)
    payment = await PaymentService(session).get(incident.payment_id)
    logs = await AuditService(session).for_transaction(payment.transaction_id)
    return [AuditLogOut.model_validate(log) for log in logs if log.incident_id in (None, incident.id)]


async def approval_queue(session: AsyncSession, status: str | None = ActionStatus.PENDING_APPROVAL) -> list[ApprovalQueueItem]:
    stmt = (
        select(Action, Incident, Payment)
        .join(Incident, Incident.id == Action.incident_id)
        .join(Payment, Payment.id == Incident.payment_id)
        .order_by(Action.created_at.desc())
    )
    if status:
        stmt = stmt.where(Action.status == str(status))
    else:
        stmt = stmt.where(Action.approved_by.is_not(None))
    rows = (await session.execute(stmt.limit(100))).all()
    return [
        ApprovalQueueItem(
            action=ActionOut.model_validate(action),
            incident_id=incident.id,
            incident_number=incident.incident_number,
            incident_type=incident.type,
            severity=incident.severity,
            transaction_id=payment.transaction_id,
            amount=float(payment.amount),
            risk=incident.risk,
            confidence=incident.confidence,
            root_cause=incident.root_cause,
            ai_summary=incident.ai_summary,
            recommended_action=incident.recommended_action,
            snapshot=snapshot(payment),
            currency=payment.currency,
            provider=payment.provider,
            provider_capture_id=payment.provider_capture_id,
        )
        for action, incident, payment in rows
    ]


async def reconciliation_matrix(session: AsyncSession, limit: int = 60) -> dict:
    payments, _ = await PaymentService(session).list_payments(limit=limit)
    service = ReconciliationService(session)
    incident_rows = await session.execute(
        select(Incident.payment_id, Incident.incident_number, Incident.status, Incident.id)
        .where(Incident.payment_id.in_([p.id for p in payments]))
        .order_by(Incident.created_at)
    )
    incidents = {row.payment_id: row for row in incident_rows}
    rows = []
    for payment in payments:
        result = await service.reconcile(payment)
        inc = incidents.get(payment.id)
        rows.append({
            "transaction_id": payment.transaction_id,
            "amount": float(payment.amount),
            "currency": payment.currency,
            "provider": payment.provider,
            "provider_status": payment.provider_status,
            "created_at": payment.created_at,
            "snapshot": snapshot(payment),
            "consistent": result.consistent,
            "mismatch_type": str(result.incident_type) if result.incident_type else None,
            "summary": result.summary,
            "incident_id": str(inc.id) if inc else None,
            "incident_number": inc.incident_number if inc else None,
            "incident_status": inc.status if inc else None,
        })
    mismatched = sum(1 for r in rows if not r["consistent"])
    return {
        "summary": {"checked": len(rows), "consistent": len(rows) - mismatched, "mismatched": mismatched,
                    "remediated": sum(1 for r in rows if r["consistent"] and r["incident_status"] == "RESOLVED")},
        "rows": rows,
    }
