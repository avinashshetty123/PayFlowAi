"""Background jobs for the PayPal event pipeline.

Each job opens its own session, does one step, and returns a ``JobResult``:
incidents to investigate and follow-up jobs (optionally delayed). The same job
functions run in Celery workers or in-process (see dispatcher) so the demo
works with or without Redis.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.enums import AuditEvent, MerchantStatus, Provider, WebhookStatus
from app.failure_injection.scenarios import FailureScenario
from app.failure_injection.service import FailureInjectionService
from app.models import WebhookEvent
from app.services import paypal_service
from app.services.audit_service import AuditService
from app.services.orchestrator import detect
from app.services.payment_service import PaymentService

logger = logging.getLogger(__name__)

Factory = async_sessionmaker[AsyncSession]


@dataclass
class JobResult:
    incidents: list[uuid.UUID] = field(default_factory=list)
    jobs: list[tuple[str, tuple, float]] = field(default_factory=list)

    def extend(self, jobs: list[tuple[str, tuple, float]]) -> "JobResult":
        self.jobs.extend(jobs)
        return self


async def job_capture(factory: Factory, transaction_id: str, trigger: str = "job") -> JobResult:
    async with factory() as session:
        result = await paypal_service.capture_paypal_payment(session, transaction_id=transaction_id, trigger=trigger)
        if result.status == "CAPTURED":
            return JobResult(jobs=[("post_capture", (transaction_id,), 0)])
        if result.status == "PROVIDER_FAILED":
            return JobResult(jobs=[("reconcile", (transaction_id, "capture-failed"), 0)])
        return JobResult()


async def job_post_capture(factory: Factory, transaction_id: str) -> JobResult:
    """Propagate the capture downstream (injection checkpoints), then schedule reconciliation."""
    async with factory() as session:
        payment = await PaymentService(session).get_by_transaction_id(transaction_id)
        await paypal_service.apply_downstream(session, payment)
        await session.commit()
        result = JobResult()
        delay = 0.0
        if await FailureInjectionService(session).take(
            payment, FailureScenario.RECONCILIATION_DELAY,
            f"Reconciliation run postponed by {settings.RECONCILIATION_DELAY_SECONDS:.0f}s",
        ):
            delay = settings.RECONCILIATION_DELAY_SECONDS
            await session.commit()
        result.jobs.append(("reconcile", (transaction_id, "post-capture"), delay))
        if payment.webhook_status == WebhookStatus.AWAITING:
            result.jobs.append(("check_webhook_arrival", (transaction_id,), settings.WEBHOOK_GRACE_SECONDS))
        return result


async def job_reconcile(factory: Factory, transaction_id: str, trigger: str = "event") -> JobResult:
    async with factory() as session:
        payment = await PaymentService(session).get_by_transaction_id(transaction_id)
        if payment.provider == Provider.PAYPAL_SANDBOX:
            await AuditService(session).record(
                transaction_id=transaction_id, event=AuditEvent.RECONCILIATION_STARTED, actor="reconciliation-engine",
                reason=f"Comparing PayPal, settlement, merchant, ledger and webhook ({trigger})",
            )
        reconciliation, incident = await detect(session, payment, trigger=trigger)
        await session.commit()
        result = JobResult(incidents=[incident.id] if incident is not None and incident.status == "OPEN" else [])

        # Demo C: once the payment is clean, the merchant cancels the order and asks for a refund.
        demo = (payment.provider_metadata or {}).get("demo")
        if (
            reconciliation.consistent and demo == "REFUND_REQUIRES_APPROVAL"
            and payment.merchant_status == MerchantStatus.SUCCESS
            and not (payment.provider_metadata or {}).get("refund_requested")
        ):
            payment.provider_metadata = {**(payment.provider_metadata or {}), "refund_requested": True}
            await paypal_service.request_refund(session, payment, reason="Customer cancelled the annual add-on",
                                                by="merchant-portal")
            await session.commit()
            result.jobs.append(("reconcile", (transaction_id, "refund-requested"), 1))
        return result


async def job_process_webhook(factory: Factory, webhook_id: str) -> JobResult:
    async with factory() as session:
        outcome = await paypal_service.process_webhook(session, uuid.UUID(webhook_id))
        return JobResult(jobs=list(outcome.follow_up))


async def job_deliver_delayed_webhook(factory: Factory, webhook_id: str) -> JobResult:
    async with factory() as session:
        outcome = await paypal_service.process_webhook(session, uuid.UUID(webhook_id), delayed_delivery=True)
        jobs = list(outcome.follow_up)
        if outcome.payment is not None and not any(j[0] == "reconcile" for j in jobs):
            jobs.append(("reconcile", (outcome.payment.transaction_id, "delayed-webhook"), 0))
        return JobResult(jobs=jobs)


async def job_check_webhook_arrival(factory: Factory, transaction_id: str) -> JobResult:
    """Grace period expired: if PayPal's capture webhook never reached PayFlow, flag it."""
    async with factory() as session:
        payment = await PaymentService(session).get_by_transaction_id(transaction_id)
        if payment.webhook_status != WebhookStatus.AWAITING:
            return JobResult()
        payment.webhook_status = str(WebhookStatus.NOT_RECEIVED)
        await PaymentService(session).add_event(
            payment, source="WEBHOOK", event_type="WEBHOOK_SLA_BREACHED", label="Webhook missing",
            payload={"detail": f"No verified PayPal capture webhook within {settings.WEBHOOK_GRACE_SECONDS:.0f}s"},
        )
        await session.commit()
        return JobResult(jobs=[("reconcile", (transaction_id, "webhook-grace-expired"), 0)])


async def job_replay_webhook(factory: Factory, webhook_id: str) -> JobResult:
    """DUPLICATE_WEBHOOK injection: re-deliver a processed event to PayFlow's own intake."""
    async with factory() as session:
        row = await session.get(WebhookEvent, uuid.UUID(webhook_id))
        if row is not None:
            await paypal_service.ingest_webhook(session, headers=row.headers, raw_body=row.raw_body, replay=True)
        return JobResult()


JOBS: dict[str, Callable[..., Awaitable[JobResult]]] = {
    "capture": job_capture,
    "post_capture": job_post_capture,
    "reconcile": job_reconcile,
    "process_webhook": job_process_webhook,
    "deliver_delayed_webhook": job_deliver_delayed_webhook,
    "check_webhook_arrival": job_check_webhook_arrival,
    "replay_webhook": job_replay_webhook,
}
