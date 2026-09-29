from datetime import timedelta

from sqlalchemy import Date, and_, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ACTIVE_INCIDENT_STATUSES, ActionStatus, IncidentStatus, PaymentState
from app.models import Action, Incident, Payment
from app.models.base import utcnow
from app.services.query_service import list_incident_summaries

TREND_DAYS = 14


async def dashboard_stats(session: AsyncSession) -> dict:
    total = await session.scalar(select(func.count()).select_from(Payment)) or 0
    successful = await session.scalar(
        select(func.count()).where(Payment.overall_status.in_([PaymentState.SUCCESS, PaymentState.SETTLED]))
    ) or 0
    failed = await session.scalar(select(func.count()).where(Payment.overall_status == PaymentState.FAILED)) or 0
    refunded = await session.scalar(
        select(func.count()).where(Payment.overall_status.in_([PaymentState.REFUNDED, PaymentState.REFUND_PENDING]))
    ) or 0
    volume = await session.scalar(select(func.coalesce(func.sum(Payment.amount), 0))) or 0
    active = await session.scalar(
        select(func.count()).where(Incident.status.in_([str(s) for s in ACTIVE_INCIDENT_STATUSES]))
    ) or 0
    pending_approvals = await session.scalar(
        select(func.count()).where(Action.status == ActionStatus.PENDING_APPROVAL)
    ) or 0

    # Auto-recovered: resolved with no human approver on the executed action.
    resolved_rows = (
        await session.execute(
            select(Incident.id, Payment.amount, Action.approved_by)
            .join(Payment, Payment.id == Incident.payment_id)
            .join(Action, and_(Action.incident_id == Incident.id, Action.status == ActionStatus.COMPLETED))
            .where(Incident.status == IncidentStatus.RESOLVED)
        )
    ).all()
    auto_recovered = sum(1 for r in resolved_rows if r.approved_by is None)
    human_recovered = len(resolved_rows) - auto_recovered
    amount_recovered = float(sum(r.amount for r in resolved_rows))

    since = utcnow() - timedelta(days=TREND_DAYS - 1)
    day = cast(Payment.created_at, Date)
    volume_rows = (
        await session.execute(
            select(day.label("day"), func.count(), func.coalesce(func.sum(Payment.amount), 0))
            .where(Payment.created_at >= since.replace(hour=0, minute=0, second=0, microsecond=0))
            .group_by(day)
            .order_by(day)
        )
    ).all()
    by_day = {r[0]: (r[1], float(r[2])) for r in volume_rows}

    inc_day = cast(Incident.created_at, Date)
    detected_rows = dict(
        (await session.execute(select(inc_day, func.count()).where(Incident.created_at >= since.replace(hour=0, minute=0, second=0, microsecond=0)).group_by(inc_day))).all()
    )
    res_day = cast(Incident.resolved_at, Date)
    resolved_by_day = dict(
        (await session.execute(
            select(res_day, func.count()).where(Incident.resolved_at.is_not(None)).group_by(res_day)
        )).all()
    )

    days = [(since + timedelta(days=i)).date() for i in range(TREND_DAYS)]
    payment_volume = [
        {"date": d.isoformat(), "label": d.strftime("%d %b"), "count": by_day.get(d, (0, 0.0))[0],
         "amount": by_day.get(d, (0, 0.0))[1]}
        for d in days
    ]
    recovery_trend = [
        {"date": d.isoformat(), "label": d.strftime("%d %b"), "detected": detected_rows.get(d, 0),
         "resolved": resolved_by_day.get(d, 0)}
        for d in days
    ]

    status_rows = (await session.execute(select(Payment.overall_status, func.count()).group_by(Payment.overall_status))).all()
    type_rows = (await session.execute(select(Incident.type, func.count()).group_by(Incident.type))).all()
    recent, _ = await list_incident_summaries(session, limit=10)
    matched = await session.scalar(select(func.count()).where(Payment.reconciliation_status == "MATCHED")) or 0
    reconciled_total = await session.scalar(
        select(func.count()).where(Payment.reconciliation_status.in_(["MATCHED", "MISMATCH"]))
    ) or 0
    paypal_payments = await session.scalar(select(func.count()).where(Payment.provider == "PAYPAL_SANDBOX")) or 0

    return {
        "total_payments": total,
        "successful_payments": successful,
        "failed_payments": failed,
        "refunded_payments": refunded,
        "total_volume": float(volume),
        "active_incidents": active,
        "auto_recovered": auto_recovered,
        "human_recovered": human_recovered,
        "pending_approvals": pending_approvals,
        "amount_recovered": amount_recovered,
        "success_rate": round(successful / total * 100, 1) if total else 0.0,
        "reconciliation_rate": round(matched / reconciled_total * 100, 1) if reconciled_total else 100.0,
        "paypal_payments": paypal_payments,
        "currency": "USD",
        "payment_volume": payment_volume,
        "payment_status": [{"status": s, "count": c} for s, c in sorted(status_rows, key=lambda r: -r[1])],
        "incident_types": [{"type": t, "count": c} for t, c in sorted(type_rows, key=lambda r: -r[1])],
        "recovery_trend": recovery_trend,
        "recent_incidents": [r.model_dump(mode="json") for r in recent],
    }
