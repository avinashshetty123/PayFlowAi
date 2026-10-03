"""Operational controls: automation kill switch and automation circuit breaker."""

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.enums import AuditEvent
from app.models import Action
from app.models.app_setting import AppSetting
from app.models.base import utcnow
from app.services.audit_service import AuditService

KILL_SWITCH = "automation_kill_switch"
FINANCIAL = ("RECONCILE_LEDGER", "RETRY_WEBHOOK", "REFUND", "MARK_PAYMENT_FAILED")


async def kill_switch(session: AsyncSession) -> dict:
    row = await session.get(AppSetting, KILL_SWITCH)
    value = dict(row.value) if row else {}
    return {"enabled": bool(value.get("enabled")), "reason": value.get("reason"),
            "updated_by": row.updated_by if row else None, "updated_at": row.updated_at if row else None}


async def set_kill_switch(session: AsyncSession, *, enabled: bool, reason: str, by: str) -> dict:
    row = await session.get(AppSetting, KILL_SWITCH)
    if row is None:
        row = AppSetting(key=KILL_SWITCH, value={})
        session.add(row)
    row.value = {"enabled": enabled, "reason": reason}
    row.updated_by = by
    row.updated_at = utcnow()
    await AuditService(session).record(
        transaction_id="SYSTEM", event=AuditEvent.POLICY_EVALUATED, actor=by,
        reason=f"Automation kill switch {'ENGAGED' if enabled else 'released'}: {reason}",
        result={"kill_switch": enabled},
    )
    await session.commit()
    return await kill_switch(session)


async def automation_rate(session: AsyncSession) -> dict:
    """Automated (non-human) financial actions in the rolling window."""
    since = utcnow() - timedelta(minutes=settings.AUTOMATION_WINDOW_MINUTES)
    count = await session.scalar(
        select(func.count()).select_from(Action).where(
            Action.created_at >= since, Action.approved_by.is_(None), Action.action_type.in_(FINANCIAL),
            Action.status.in_(["APPROVED", "EXECUTING", "COMPLETED"]),
        )
    ) or 0
    limit = settings.AUTOMATION_RATE_LIMIT
    return {"count": int(count), "limit": limit, "window_minutes": settings.AUTOMATION_WINDOW_MINUTES,
            "tripped": count >= limit}
