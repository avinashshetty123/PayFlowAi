"""In-API background pump (runs on Render/Railway/local alike, no extra worker needed).

Every few seconds: deliver the alert outbox, seal new audit rows into the hash chain;
every 30s: re-escalate unacknowledged P1/P2 alerts. Each step uses its own session and
never raises, so a channel or DB hiccup can't take the API down.
"""

import asyncio
import logging
import time

from app.core.config import settings
from app.core.database import SessionLocal

logger = logging.getLogger("payflow.pump")

TICK_SECONDS = 2.0
ESCALATE_EVERY = 30.0


async def _step(name: str, fn) -> None:
    try:
        async with SessionLocal() as session:
            await fn(session)
    except Exception:  # noqa: BLE001
        logger.exception("Background step %s failed", name)


async def pump(stop: asyncio.Event) -> None:
    from app.notifications.service import deliver_pending, escalate_unacknowledged
    from app.services.audit_chain import seal_pending

    last_escalation = 0.0
    while not stop.is_set():
        await _step("deliver_alerts", deliver_pending)
        await _step("seal_audit", seal_pending)
        if time.monotonic() - last_escalation >= ESCALATE_EVERY:
            last_escalation = time.monotonic()
            await _step("escalate_alerts", escalate_unacknowledged)
        try:
            await asyncio.wait_for(stop.wait(), timeout=TICK_SECONDS)
        except asyncio.TimeoutError:
            pass


def start() -> tuple[asyncio.Event, asyncio.Task] | None:
    if not settings.BACKGROUND_PUMP_ENABLED:
        return None
    stop = asyncio.Event()
    return stop, asyncio.create_task(pump(stop))
