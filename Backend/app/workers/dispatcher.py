"""Chooses where background work runs: Celery (Redis) when a worker is alive,
otherwise an in-process asyncio task. The demo never depends on Redis/Celery.
"""

import asyncio
import logging
import time
import uuid

from fastapi import BackgroundTasks

from app.core.config import settings
from app.core.redis import redis_available
from app.services.orchestrator import run_incident_pipeline

logger = logging.getLogger(__name__)

_WORKER_CACHE_SECONDS = 15.0
_worker_cache: dict[str, float | bool] = {"at": -1e9, "alive": False}
_background: set[asyncio.Task] = set()


def celery_workers_alive() -> bool:
    now = time.monotonic()
    if now - float(_worker_cache["at"]) < _WORKER_CACHE_SECONDS:
        return bool(_worker_cache["alive"])
    alive = False
    if redis_available():
        try:
            from app.workers.celery_app import celery_app

            alive = bool(celery_app.control.ping(timeout=0.7))
        except Exception:  # noqa: BLE001
            alive = False
    _worker_cache.update(at=now, alive=alive)
    return alive


TIMER_JOBS = {"check_webhook_arrival", "deliver_delayed_webhook"}


async def resolve_mode() -> str:
    if settings.PIPELINE_MODE in ("inline", "sync"):
        return settings.PIPELINE_MODE
    if settings.PIPELINE_MODE == "celery":
        return "celery" if await asyncio.to_thread(redis_available) else "inline"
    return "celery" if await asyncio.to_thread(celery_workers_alive) else "inline"


def _spawn(incident_id: uuid.UUID) -> None:
    task = asyncio.create_task(run_incident_pipeline(incident_id))
    _background.add(task)
    task.add_done_callback(_background.discard)


async def dispatch_incident_pipeline(incident_id: uuid.UUID, background_tasks: BackgroundTasks | None = None) -> str:
    mode = await resolve_mode()
    if mode == "sync":
        await run_incident_pipeline(incident_id, delay=0)
        return "sync"
    if mode == "celery":
        try:
            from app.workers.tasks import investigate_incident

            await asyncio.to_thread(investigate_incident.apply_async, args=[str(incident_id)])
            return "celery"
        except Exception as exc:  # noqa: BLE001
            logger.warning("Celery dispatch failed (%s); running pipeline in-process", exc)
    if background_tasks is not None:
        background_tasks.add_task(run_incident_pipeline, incident_id)
    else:
        _spawn(incident_id)
    return "in-process"


# ---- PayPal event-pipeline jobs ---------------------------------------------------------------


async def dispatch_job(name: str, args: tuple | list, countdown: float = 0) -> str:
    """Run a pipeline job via Celery (with countdown) or as an in-process task."""
    mode = await resolve_mode()
    if mode == "sync":
        await run_job_chain(name, tuple(args), skip=TIMER_JOBS)
        return "sync"
    if mode == "celery":
        try:
            from app.workers.tasks import run_job

            await asyncio.to_thread(run_job.apply_async, args=[name, list(args)], countdown=countdown)
            return "celery"
        except Exception as exc:  # noqa: BLE001
            logger.warning("Celery job dispatch failed (%s); running %s in-process", exc, name)
    task = asyncio.create_task(_run_job_in_process(name, tuple(args), countdown))
    _background.add(task)
    task.add_done_callback(_background.discard)
    return "in-process"


async def _run_job_in_process(name: str, args: tuple, countdown: float) -> None:
    from app.core.database import SessionLocal
    from app.workers.jobs import JOBS

    if countdown > 0:
        await asyncio.sleep(countdown)
    try:
        result = await JOBS[name](SessionLocal, *args)
    except Exception:  # noqa: BLE001 - failures are logged; incident pipelines record their own failures
        logger.exception("In-process job %s%s failed", name, args)
        return
    for incident_id in result.incidents:
        await run_incident_pipeline(incident_id)
    for job, job_args, delay in result.jobs:
        await dispatch_job(job, job_args, delay)


async def run_job_chain(name: str, args: tuple, *, factory=None, skip: set[str] | None = None,
                        pipeline_delay: float = 0) -> list[uuid.UUID]:
    """Run a job and all follow-ups synchronously, ignoring countdowns (tests / scripts)."""
    from app.core.database import SessionLocal
    from app.workers.jobs import JOBS

    factory = factory or SessionLocal
    skip = skip or set()
    incidents: list[uuid.UUID] = []
    queue: list[tuple[str, tuple]] = [(name, tuple(args))]
    while queue:
        job, job_args = queue.pop(0)
        if job in skip:
            continue
        result = await JOBS[job](factory, *job_args)
        for incident_id in result.incidents:
            incidents.append(incident_id)
            await run_incident_pipeline(incident_id, factory=factory, delay=pipeline_delay)
        queue.extend((j, tuple(a)) for j, a, _ in result.jobs)
    return incidents
