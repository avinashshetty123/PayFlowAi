import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AuditEvent
from app.models import Incident
from app.services.audit_service import AuditService

logger = logging.getLogger("payflow.notifications")

CHANNEL = "#payments-ops (simulated)"


async def notify(session: AsyncSession, *, incident: Incident, transaction_id: str, message: str) -> None:
    """Simulated ops notification (Slack/PagerDuty stand-in), recorded in the audit log."""
    logger.info("[%s] %s %s: %s", CHANNEL, incident.incident_number, transaction_id, message)
    await AuditService(session).record(
        transaction_id=transaction_id,
        incident_id=incident.id,
        event=AuditEvent.NOTIFICATION_SENT,
        actor="notifier",
        reason=message,
        result={"channel": CHANNEL, "delivered": True},
    )
