import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import Notification, NotificationDelivery
from app.notifications import service

router = APIRouter(prefix="/notifications", tags=["notifications"])


class AckRequest(BaseModel):
    by: str = Field(default="ops.manager", min_length=2, max_length=64)


def _out(note: Notification, deliveries: list[NotificationDelivery]) -> dict:
    return {
        "id": str(note.id), "incident_id": str(note.incident_id) if note.incident_id else None,
        "transaction_id": note.transaction_id, "severity": note.severity, "category": note.category,
        "title": note.title, "body": note.body, "link": note.link, "status": note.status,
        "escalation_level": note.escalation_level, "acknowledged_by": note.acknowledged_by,
        "acknowledged_at": note.acknowledged_at, "created_at": note.created_at,
        "deliveries": [
            {"channel": d.channel, "target": d.target, "status": d.status, "attempts": d.attempts,
             "escalation_level": d.escalation_level, "error": d.error, "sent_at": d.sent_at}
            for d in deliveries
        ],
    }


@router.get("")
async def list_notifications(
    status: str | None = Query(None, pattern="^(OPEN|ACKNOWLEDGED|RESOLVED)$"),
    severity: str | None = Query(None, pattern="^P[1-4]$"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    stmt = select(Notification).order_by(Notification.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(Notification.status == status)
    if severity:
        stmt = stmt.where(Notification.severity == severity)
    notes = list(await db.scalars(stmt))
    deliveries: dict[uuid.UUID, list[NotificationDelivery]] = {}
    if notes:
        rows = await db.scalars(select(NotificationDelivery)
                                .where(NotificationDelivery.notification_id.in_([n.id for n in notes]))
                                .order_by(NotificationDelivery.created_at))
        for d in rows:
            deliveries.setdefault(d.notification_id, []).append(d)
    unread = await db.scalar(select(func.count()).where(Notification.status == "OPEN")) or 0
    critical = await db.scalar(
        select(func.count()).where(Notification.status == "OPEN", Notification.severity.in_(["P1", "P2"]))) or 0
    return {"items": [_out(n, deliveries.get(n.id, [])) for n in notes], "unread": unread, "urgent": critical}


@router.get("/channels")
async def channels() -> dict:
    return {"channels": service.channel_status(), "routes": {k: sorted(v) for k, v in service.ROUTES.items()},
            "escalation_minutes": service.settings.ALERT_ESCALATION_MINUTES}


@router.post("/{notification_id}/ack")
async def ack(notification_id: uuid.UUID, body: AckRequest | None = None, db: AsyncSession = Depends(get_db)) -> dict:
    count = await service.acknowledge(db, notification_id, by=(body or AckRequest()).by)
    return {"ok": True, "acknowledged": count}


@router.post("/ack-all")
async def ack_all(body: AckRequest | None = None, db: AsyncSession = Depends(get_db)) -> dict:
    count = await service.acknowledge(db, None, by=(body or AckRequest()).by)
    return {"ok": True, "acknowledged": count}


@router.post("/test")
async def test_channels(body: AckRequest | None = None, db: AsyncSession = Depends(get_db)) -> dict:
    results = await service.send_test(db, by=(body or AckRequest()).by)
    return {"ok": any(r["status"] == "SENT" for r in results), "results": results,
            "message": "No external channels configured" if not results else None}
