import asyncio
import time

from fastapi import APIRouter, Depends
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.redis import async_redis_available
from app.events.bus import redis_publishing_ok
from app.models import WebhookEvent
from app.payments import get_provider
from app.rag.historical_service import HistoricalIncidentService
from app.notifications.service import channel_status
from app.workers.dispatcher import celery_workers_alive, resolve_mode


def agent_engine() -> str:
    from app.agents.incident_graph import LANGGRAPH_AVAILABLE

    return "langgraph" if LANGGRAPH_AVAILABLE else "sequential-fallback"

router = APIRouter(tags=["health"])

_paypal_cache: dict = {"at": -1e9, "status": "UNKNOWN"}


async def paypal_status() -> str:
    """OAuth round-trip to PayPal Sandbox, cached for 60s. Never exposes credentials."""
    if time.monotonic() - _paypal_cache["at"] < 60:
        return _paypal_cache["status"]
    try:
        status = await asyncio.wait_for(get_provider().health(), timeout=8)
    except asyncio.TimeoutError:
        status = "TIMEOUT"
    _paypal_cache.update(at=time.monotonic(), status=status)
    return status


@router.get("/health")
async def health(db: AsyncSession = Depends(get_db)) -> dict:
    try:
        await db.execute(text("SELECT 1"))
        database = "HEALTHY"
        pgvector = await HistoricalIncidentService(db).pgvector_enabled()
        last_webhook = await db.scalar(select(WebhookEvent).order_by(WebhookEvent.received_at.desc()).limit(1))
    except Exception as exc:  # noqa: BLE001
        database, pgvector, last_webhook = f"ERROR: {exc.__class__.__name__}", False, None
    redis_ok = await async_redis_available()
    workers = await asyncio.to_thread(celery_workers_alive) if redis_ok else False

    if not settings.PAYPAL_WEBHOOK_ID:
        webhook = "NOT_CONFIGURED"
    elif last_webhook is None:
        webhook = "CONFIGURED"
    else:
        webhook = "VERIFIED" if last_webhook.signature_verified else (
            "PENDING_VERIFICATION" if last_webhook.signature_verified is None else "VERIFICATION_FAILED")

    return {
        "status": "ok" if database == "HEALTHY" else "degraded",
        "service": settings.APP_NAME,
        "paypal": await paypal_status(),
        "paypal_environment": "sandbox",
        "paypal_api": settings.paypal_api_base,
        "webhook": webhook,
        "webhook_url": settings.PAYPAL_WEBHOOK_URL,
        "database": database,
        "redis": "HEALTHY" if redis_ok else "UNAVAILABLE",
        "event_bus": "redis" if redis_ok and redis_publishing_ok() else "in-process",
        "celery_workers": workers,
        "pipeline_mode": await resolve_mode(),
        "groq": "CONFIGURED" if settings.groq_enabled else "FALLBACK",
        "ai": f"groq:{settings.GROQ_MODEL}" if settings.groq_enabled else "deterministic fallback (GROQ_API_KEY not set)",
        "rag": "pgvector" if pgvector else "python cosine fallback",
        "failure_injection": settings.ENABLE_FAILURE_INJECTION,
        "platform": settings.platform,
        "public_api_url": settings.public_api_url,
        "agent_engine": agent_engine(),
        "alert_channels": [c["channel"] for c in channel_status() if c["configured"]],
        "negative_testing": settings.ENABLE_PAYPAL_NEGATIVE_TESTING,
    }
