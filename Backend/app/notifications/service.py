"""Operator alerting with fintech-grade semantics.

* Severity routing  P1/P2 → every channel · P3 → WhatsApp/push/Slack · P4 → in-app only
* Transactional outbox: the alert row is written in the same DB transaction as the incident change;
  a background pump delivers it, so a slow WhatsApp API can never block or roll back a payment flow.
* Deduplication per (incident, kind); resolution auto-closes open alerts for that incident.
* Acknowledgement tracking and re-escalation of unacknowledged P1/P2 alerts.
* Delivery receipts per channel (masked targets), retries with backoff.
"""

import logging
import uuid
from datetime import timedelta

import httpx
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.events.bus import emit
from app.models import Incident, Notification, NotificationDelivery, Payment
from app.models.base import utcnow
from app.notifications import channels
from app.utils.serialization import money

logger = logging.getLogger("payflow.notifications")

ROUTES: dict[str, set[str]] = {
    "P1": {"whatsapp", "telegram", "ntfy", "slack", "webhook"},
    "P2": {"whatsapp", "telegram", "ntfy", "slack", "webhook"},
    "P3": {"whatsapp", "ntfy", "slack", "webhook"},  # every new incident reaches the on-call phone
    "P4": set(),
}
SEVERITY_FROM_INCIDENT = {"CRITICAL": "P1", "HIGH": "P2", "MEDIUM": "P3", "LOW": "P4"}
MAX_ATTEMPTS = 3
MAX_ESCALATIONS = 3

KINDS = {
    # kind: (title template, minimum severity)
    "INCIDENT_OPENED": ("{sev} · {number} {type} detected", None),
    "APPROVAL_REQUIRED": ("{sev} · Approval needed · {number}", "P2"),
    "INCIDENT_ESCALATED": ("{sev} · {number} escalated to on-call", "P2"),
    "ACTION_FAILED": ("{sev} · Remediation failed · {number}", "P1"),
    "INCIDENT_RESOLVED": ("Resolved · {number} {type}", None),
    "TEST": ("PayFlow test alert", None),
}
_RANK = {"P1": 1, "P2": 2, "P3": 3, "P4": 4}


def _stricter(a: str, b: str | None) -> str:
    return a if b is None or _RANK[a] <= _RANK[b] else b


async def raise_alert(
    session: AsyncSession, *, incident: Incident, payment: Payment, kind: str, detail: str,
    severity: str | None = None,
) -> Notification | None:
    """Create (deduplicated) alert + pending deliveries inside the caller's transaction."""
    title_tpl, floor = KINDS[kind]
    sev = "P4" if kind == "INCIDENT_RESOLVED" else _stricter(
        severity or SEVERITY_FROM_INCIDENT.get(incident.severity, "P3"), floor)
    dedup = f"{incident.id}:{kind}"
    existing = await session.scalar(select(Notification).where(Notification.dedup_key == dedup))
    if existing is not None:
        return None

    title = title_tpl.format(sev=sev, number=incident.incident_number, type=incident.type)
    body = (f"{payment.transaction_id} · {money(payment.amount, payment.currency)} · {incident.type}\n{detail}"
            + (f"\nRoot cause: {incident.root_cause}" if incident.root_cause and kind != "INCIDENT_OPENED" else ""))
    link = f"/incidents/{incident.id}"
    note = Notification(
        dedup_key=dedup, incident_id=incident.id, transaction_id=payment.transaction_id, severity=sev,
        category=kind, title=title, body=body[:1500], link=link, status="OPEN", escalation_level=0,
        last_alerted_at=utcnow(), created_at=utcnow(),
    )
    try:
        async with session.begin_nested():
            session.add(note)
            await session.flush()
    except IntegrityError:
        return None
    _queue_deliveries(session, note, level=0)

    if kind == "INCIDENT_RESOLVED":
        await session.execute(
            update(Notification)
            .where(Notification.incident_id == incident.id, Notification.status != "RESOLVED",
                   Notification.id != note.id)
            .values(status="RESOLVED", resolved_at=utcnow())
            .execution_options(synchronize_session=False)
        )
        note.status = "RESOLVED"
        note.resolved_at = utcnow()
    emit(session, "NOTIFICATION_CREATED", transaction_id=payment.transaction_id, incident_id=incident.id,
         incident_number=incident.incident_number,
         data={"severity": sev, "title": title, "category": kind, "reason": detail})
    return note


def _queue_deliveries(session: AsyncSession, note: Notification, *, level: int) -> int:
    routed = ROUTES.get(note.severity, set())
    count = 0
    for target in channels.configured_targets():
        if target.channel in routed:
            session.add(NotificationDelivery(
                notification_id=note.id, channel=target.channel, target=target.masked, status="PENDING",
                attempts=0, escalation_level=level, created_at=utcnow(),
            ))
            count += 1
    return count


def _target_for(channel: str, masked: str) -> channels.ChannelTarget | None:
    for target in channels.configured_targets():
        if target.channel == channel and target.masked == masked:
            return target
    return None


async def deliver_pending(session: AsyncSession, *, limit: int = 20,
                          transport: httpx.AsyncBaseTransport | None = None) -> int:
    """Send pending deliveries (row-locked so several API instances never double-send)."""
    rows = list(await session.scalars(
        select(NotificationDelivery)
        .where(NotificationDelivery.status == "PENDING", NotificationDelivery.attempts < MAX_ATTEMPTS)
        .order_by(NotificationDelivery.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ))
    sent = 0
    for delivery in rows:
        note = await session.get(Notification, delivery.notification_id)
        target = _target_for(delivery.channel, delivery.target)
        delivery.attempts += 1
        if note is None or target is None:
            delivery.status, delivery.error = "FAILED", "channel no longer configured"
            continue
        prefix = f"⏰ REMINDER {delivery.escalation_level}: unacknowledged · " if delivery.escalation_level else ""
        link = f"{settings.FRONTEND_URL.rstrip('/')}{note.link}" if note.link else None
        try:
            delivery.provider_message_id = await channels.send(
                target, severity=note.severity, title=prefix + note.title, body=note.body, link=link, transport=transport)
            delivery.status, delivery.sent_at, delivery.error = "SENT", utcnow(), None
            sent += 1
        except Exception as exc:  # noqa: BLE001 - a channel outage must never break anything else
            delivery.error = str(exc)[:400]
            if delivery.attempts >= MAX_ATTEMPTS:
                delivery.status = "FAILED"
            logger.warning("Alert delivery via %s failed (attempt %s): %s", delivery.channel, delivery.attempts, exc)
    await session.commit()
    return sent


async def escalate_unacknowledged(session: AsyncSession) -> int:
    """Re-alert OPEN P1/P2 notifications nobody acknowledged within the escalation window."""
    cutoff = utcnow() - timedelta(minutes=settings.ALERT_ESCALATION_MINUTES)
    rows = list(await session.scalars(
        select(Notification)
        .where(Notification.status == "OPEN", Notification.severity.in_(["P1", "P2"]),
               Notification.escalation_level < MAX_ESCALATIONS, Notification.last_alerted_at < cutoff)
        .with_for_update(skip_locked=True)
    ))
    for note in rows:
        note.escalation_level += 1
        note.last_alerted_at = utcnow()
        _queue_deliveries(session, note, level=note.escalation_level)
        emit(session, "NOTIFICATION_ESCALATED", transaction_id=note.transaction_id, incident_id=note.incident_id,
             data={"severity": note.severity, "title": note.title, "level": note.escalation_level,
                   "reason": f"Unacknowledged after {settings.ALERT_ESCALATION_MINUTES} min"})
    await session.commit()
    return len(rows)


async def acknowledge(session: AsyncSession, notification_id: uuid.UUID | None, *, by: str,
                      incident_id: uuid.UUID | None = None) -> int:
    stmt = update(Notification).where(Notification.status == "OPEN")
    if notification_id is not None:
        stmt = stmt.where(Notification.id == notification_id)
    if incident_id is not None:
        stmt = stmt.where(Notification.incident_id == incident_id)
    result = await session.execute(
        stmt.values(status="ACKNOWLEDGED", acknowledged_by=by, acknowledged_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    emit(session, "NOTIFICATION_ACKNOWLEDGED", data={"by": by, "count": result.rowcount})
    await session.commit()
    return result.rowcount or 0


async def ack_incident_alerts(session: AsyncSession, incident_id: uuid.UUID, *, by: str) -> None:
    """Acknowledge open alerts for an incident inside the caller's transaction (no commit)."""
    await session.execute(
        update(Notification).where(Notification.incident_id == incident_id, Notification.status == "OPEN")
        .values(status="ACKNOWLEDGED", acknowledged_by=by, acknowledged_at=utcnow())
        .execution_options(synchronize_session=False)
    )


async def resolve_incident_alerts(session: AsyncSession, incident_id: uuid.UUID) -> None:
    await session.execute(
        update(Notification).where(Notification.incident_id == incident_id, Notification.status != "RESOLVED")
        .values(status="RESOLVED", resolved_at=utcnow())
        .execution_options(synchronize_session=False)
    )


async def send_test(session: AsyncSession, *, by: str, transport: httpx.AsyncBaseTransport | None = None) -> list[dict]:
    """Send a test message to every configured channel immediately (setup check)."""
    results = []
    for target in channels.configured_targets():
        try:
            message_id = await channels.send(
                target, severity="P3", title="PayFlow AI test alert",
                body=f"Channel check requested by {by}. If you can read this, {target.channel} alerts work.",
                link=settings.FRONTEND_URL, transport=transport)
            results.append({"channel": target.channel, "target": target.masked, "status": "SENT", "id": message_id})
        except Exception as exc:  # noqa: BLE001
            results.append({"channel": target.channel, "target": target.masked, "status": "FAILED", "error": str(exc)[:200]})
    return results


def channel_status() -> list[dict]:
    configured = {t.channel: t.masked for t in channels.configured_targets()}
    meta = [
        ("whatsapp", "WhatsApp (Meta Cloud API)", "WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID, ALERT_WHATSAPP_TO"),
        ("telegram", "Telegram bot", "TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID"),
        ("ntfy", "Phone push (ntfy)", "NTFY_TOPIC (install the ntfy app and subscribe to the topic)"),
        ("slack", "Slack", "SLACK_WEBHOOK_URL"),
        ("webhook", "Generic webhook (Teams/Discord/PagerDuty bridge)", "ALERT_WEBHOOK_URL"),
    ]
    return [
        {"channel": key, "label": label, "configured": key in configured, "target": configured.get(key),
         "env": env, "severities": sorted(s for s, chans in ROUTES.items() if key in chans)}
        for key, label, env in meta
    ]
