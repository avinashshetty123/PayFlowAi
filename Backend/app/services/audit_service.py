import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AuditEvent
from app.events.bus import STREAM_NAMES, emit
from app.models import AuditLog
from app.models.base import utcnow
from app.utils.serialization import to_jsonable

# Set session.info[SUPPRESS_EVENTS] = True to keep bulk jobs (seeding) off the live stream.
SUPPRESS_EVENTS = "payflow_suppress_events"


class AuditService:
    """Append-only audit trail. Every important operation records actor, reason, evidence, result."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def record(
        self,
        *,
        transaction_id: str,
        event: AuditEvent | str,
        actor: str,
        reason: str,
        evidence: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        incident_id: uuid.UUID | None = None,
        at: datetime | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            transaction_id=transaction_id,
            incident_id=incident_id,
            actor=actor,
            event=str(event),
            reason=reason,
            evidence=to_jsonable(evidence or {}),
            result=to_jsonable(result or {}),
            created_at=at or utcnow(),
        )
        self.session.add(entry)
        await self.session.flush()
        if not self.session.info.get(SUPPRESS_EVENTS):
            emit(
                self.session,
                STREAM_NAMES.get(entry.event, entry.event),
                transaction_id=transaction_id,
                incident_id=incident_id,
                data={"auditEvent": entry.event, "actor": actor, "reason": reason, "result": entry.result},
            )
        return entry

    async def for_incident(self, incident_id: uuid.UUID) -> list[AuditLog]:
        rows = await self.session.scalars(
            select(AuditLog).where(AuditLog.incident_id == incident_id).order_by(AuditLog.created_at, AuditLog.id)
        )
        return list(rows)

    async def for_transaction(self, transaction_id: str) -> list[AuditLog]:
        rows = await self.session.scalars(
            select(AuditLog).where(AuditLog.transaction_id == transaction_id).order_by(AuditLog.created_at)
        )
        return list(rows)

    async def list_logs(
        self, *, limit: int = 100, offset: int = 0, event: str | None = None, search: str | None = None
    ) -> tuple[list[AuditLog], int]:
        stmt = select(AuditLog)
        count_stmt = select(func.count()).select_from(AuditLog)
        if event:
            stmt = stmt.where(AuditLog.event == event)
            count_stmt = count_stmt.where(AuditLog.event == event)
        if search:
            like = f"%{search}%"
            cond = AuditLog.transaction_id.ilike(like) | AuditLog.reason.ilike(like) | AuditLog.actor.ilike(like)
            stmt = stmt.where(cond)
            count_stmt = count_stmt.where(cond)
        total = await self.session.scalar(count_stmt) or 0
        rows = await self.session.scalars(stmt.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset))
        return list(rows), total
