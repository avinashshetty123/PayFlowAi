"""Celery tasks. Each task runs its async body in a fresh event loop with a
non-pooled engine, retries with exponential backoff, and records a JOB_FAILED
audit entry (and escalates the incident) when retries are exhausted.
"""

import asyncio
import logging
import sys
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from celery import Task
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.database import build_engine
from app.core.enums import AuditEvent
from app.core.errors import DomainError
from app.models import Incident, Payment
from app.services import orchestrator
from app.services.audit_service import AuditService
from app.services.notification_service import notify
from app.services.payment_service import PaymentService
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

Factory = async_sessionmaker[AsyncSession]

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def run_async(body: Callable[[Factory], Awaitable[Any]]) -> Any:
    async def runner() -> Any:
        engine = build_engine(null_pool=True)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            return await body(factory)
        finally:
            await engine.dispose()

    return asyncio.run(runner())


class PayFlowTask(Task):
    autoretry_for = (Exception,)
    dont_autoretry_for = (DomainError,)  # business-rule rejections are final, not transient
    retry_backoff = 2
    retry_backoff_max = 60
    retry_jitter = True
    max_retries = 4
    acks_late = True

    def on_failure(self, exc, task_id, args, kwargs, einfo):  # noqa: ANN001
        """Dead-letter: make the failure visible instead of letting the job vanish."""
        logger.error("Task %s[%s] failed permanently: %s", self.name, task_id, exc)
        if self.name in ("payflow.investigate_incident",) and args:
            run_async(lambda f: orchestrator.record_pipeline_failure(f, args[0], str(exc), job=self.name))
        elif self.name == "payflow.execute_action" and args:
            run_async(lambda f: _record_action_failure(f, args[0], str(exc), self.name))
        elif self.name == "payflow.run_job" and len(args) > 1:
            ref = str(args[1][0]) if args[1] else "N/A"
            run_async(lambda f: _record_generic_failure(f, ref, f"{args[0]}: {exc}", self.name))
        elif args:
            run_async(lambda f: _record_generic_failure(f, str(args[0]), str(exc), self.name))


async def _record_action_failure(factory: Factory, action_id: str, error: str, job: str) -> None:
    from app.models import Action

    async with factory() as session:
        action = await session.get(Action, uuid.UUID(action_id))
        if action is not None:
            await orchestrator.record_pipeline_failure(factory, action.incident_id, error, job=job)


async def _record_generic_failure(factory: Factory, ref: str, error: str, job: str) -> None:
    async with factory() as session:
        await AuditService(session).record(
            transaction_id=ref[:64], event=AuditEvent.JOB_FAILED, actor=job, reason=f"Background job failed: {error}"[:1000]
        )
        await session.commit()


@celery_app.task(base=PayFlowTask, bind=True, name="payflow.investigate_incident")
def investigate_incident(self: Task, incident_id: str, force_fallback: bool = False) -> dict:
    async def body(factory: Factory):
        async with factory() as session:
            return await orchestrator.run_investigation_stage(
                session, uuid.UUID(incident_id), force_fallback=force_fallback,
                delay=settings.PIPELINE_STEP_DELAY_SECONDS,
            )

    action_id = run_async(body)
    if action_id is not None:
        execute_action.apply_async(args=[str(action_id)], countdown=settings.PIPELINE_STEP_DELAY_SECONDS)
    return {"incident_id": incident_id, "action_id": str(action_id) if action_id else None}


@celery_app.task(base=PayFlowTask, bind=True, name="payflow.execute_action")
def execute_action(self: Task, action_id: str) -> dict:
    async def body(factory: Factory):
        async with factory() as session:
            result = await orchestrator.run_action_stage(
                session, uuid.UUID(action_id), delay=settings.PIPELINE_STEP_DELAY_SECONDS
            )
            return {"action_id": action_id, "incident_status": result.incident_status, "message": result.message}

    outcome = run_async(body)
    send_notification.apply_async(args=[action_id, outcome["message"]])
    return outcome


@celery_app.task(base=PayFlowTask, bind=True, name="payflow.process_payment_event")
def process_payment_event(
    self: Task,
    transaction_id: str,
    source: str,
    event_type: str,
    status_updates: dict | None = None,
    target_state: str | None = None,
    payload: dict | None = None,
) -> dict:
    async def body(factory: Factory):
        async with factory() as session:
            incident = await orchestrator.apply_payment_event(
                session, transaction_id=transaction_id, source=source, event_type=event_type,
                status_updates=status_updates, target_state=target_state, payload=payload,
            )
            return str(incident.id) if incident else None

    incident_id = run_async(body)
    if incident_id:
        investigate_incident.apply_async(args=[incident_id])
    return {"transaction_id": transaction_id, "incident_id": incident_id}


@celery_app.task(base=PayFlowTask, bind=True, name="payflow.run_reconciliation")
def run_reconciliation(self: Task, transaction_id: str) -> dict:
    async def body(factory: Factory):
        async with factory() as session:
            payment = await PaymentService(session).get_by_transaction_id(transaction_id)
            result, incident = await orchestrator.detect(session, payment)
            await session.commit()
            return result.consistent, (str(incident.id) if incident else None)

    consistent, incident_id = run_async(body)
    if incident_id:
        investigate_incident.apply_async(args=[incident_id])
    return {"transaction_id": transaction_id, "consistent": consistent, "incident_id": incident_id}


@celery_app.task(base=PayFlowTask, bind=True, name="payflow.send_notification")
def send_notification(self: Task, action_id: str, message: str) -> dict:
    async def body(factory: Factory):
        from app.models import Action

        async with factory() as session:
            action = await session.get(Action, uuid.UUID(action_id))
            if action is None:
                return False
            incident = await session.get(Incident, action.incident_id)
            payment = await session.get(Payment, incident.payment_id)
            await notify(session, incident=incident, transaction_id=payment.transaction_id, message=message)
            await session.commit()
            return True

    return {"delivered": run_async(body)}


@celery_app.task(base=PayFlowTask, bind=True, name="payflow.run_job")
def run_job(self: Task, name: str, args: list) -> dict:
    """Generic PayPal event-pipeline job (capture, post_capture, reconcile, webhooks...)."""
    from app.workers.jobs import JOBS

    result = run_async(lambda f: JOBS[name](f, *args))
    for incident_id in result.incidents:
        investigate_incident.apply_async(args=[str(incident_id)])
    for job, job_args, countdown in result.jobs:
        run_job.apply_async(args=[job, list(job_args)], countdown=countdown)
    return {"job": name, "incidents": [str(i) for i in result.incidents], "follow_up": [j[0] for j in result.jobs]}
