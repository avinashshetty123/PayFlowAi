"""Real-time event bus.

- Events raised inside a DB transaction are queued on the session and published
  only **after commit**, so the UI never sees state that was rolled back.
- Transport: Redis Pub/Sub (``payflow:events``) plus a capped Redis list for
  backlog, so API processes and Celery workers share one stream.
- If Redis is unavailable, events are delivered in-process only (the API's own
  SSE clients still update). Publishing never raises.
"""

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any

import redis
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.config import settings
from app.utils.serialization import to_jsonable

logger = logging.getLogger(__name__)

CHANNEL = "payflow:events"
RECENT_KEY = "payflow:events:recent"
RECENT_LIMIT = 200
_SESSION_KEY = "payflow_pending_events"

# Audit events that map to a differently-named stream event.
STREAM_NAMES = {
    "AI_RECOMMENDATION_CREATED": "INVESTIGATION_COMPLETED",
    "VERIFICATION_PASSED": "VERIFICATION_COMPLETED",
    "VERIFICATION_FAILED": "VERIFICATION_COMPLETED",
    "APPROVAL_REQUESTED": "ACTION_AWAITING_APPROVAL",
}


class LocalBroadcaster:
    def __init__(self) -> None:
        self.subscribers: set[asyncio.Queue] = set()
        self.recent: deque[dict] = deque(maxlen=RECENT_LIMIT)

    def broadcast(self, event: dict) -> None:
        self.recent.append(event)
        for queue in list(self.subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self.subscribers.discard(queue)


local = LocalBroadcaster()

_redis_client: redis.Redis | None = None
_redis_down_until = 0.0


def _redis() -> redis.Redis | None:
    global _redis_client
    if time.monotonic() < _redis_down_until:
        return None
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=0.3, socket_timeout=0.3)
    return _redis_client


def redis_publishing_ok() -> bool:
    return time.monotonic() >= _redis_down_until


def make_event(
    event: str, *, transaction_id: str | None = None, incident_id: str | None = None,
    incident_number: str | None = None, data: dict | None = None,
) -> dict:
    return {
        "id": uuid.uuid4().hex,
        "event": event,
        "transactionId": transaction_id,
        "incidentId": str(incident_id) if incident_id else None,
        "incidentNumber": incident_number,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": to_jsonable(data or {}),
    }


def publish_now(event: dict) -> None:
    """Deliver immediately (in-process + Redis). Never raises."""
    global _redis_down_until
    local.broadcast(event)
    client = _redis()
    if client is None:
        return
    try:
        payload = json.dumps(event)
        pipe = client.pipeline()
        pipe.publish(CHANNEL, payload)
        pipe.lpush(RECENT_KEY, payload)
        pipe.ltrim(RECENT_KEY, 0, RECENT_LIMIT - 1)
        pipe.execute()
    except Exception as exc:  # noqa: BLE001 - the event stream must never break a payment flow
        _redis_down_until = time.monotonic() + 15
        logger.warning("Event bus: Redis publish failed (%s); in-process delivery only for 15s", exc.__class__.__name__)


def queue_event(session: AsyncSession | Session, event: dict) -> None:
    """Publish ``event`` after the session's transaction commits."""
    info = session.info
    info.setdefault(_SESSION_KEY, []).append(event)


def emit(
    session: AsyncSession | Session | None, event: str, *, transaction_id: str | None = None,
    incident_id: Any = None, incident_number: str | None = None, data: dict | None = None,
) -> dict:
    payload = make_event(event, transaction_id=transaction_id, incident_id=incident_id,
                         incident_number=incident_number, data=data)
    if session is None:
        publish_now(payload)
    else:
        queue_event(session, payload)
    return payload


@sa_event.listens_for(Session, "after_commit")
def _publish_after_commit(session: Session) -> None:
    for event in session.info.pop(_SESSION_KEY, []):
        publish_now(event)


@sa_event.listens_for(Session, "after_soft_rollback")
def _discard_on_rollback(session: Session, previous_transaction) -> None:
    # Savepoint rollbacks (handled failures inside a larger transaction) keep earlier events.
    if not getattr(previous_transaction, "nested", False):
        session.info.pop(_SESSION_KEY, None)


async def recent_events(limit: int = 60) -> list[dict]:
    """Backlog for newly connected clients (oldest first)."""
    client = _redis()
    if client is not None:
        try:
            rows = await asyncio.to_thread(client.lrange, RECENT_KEY, 0, limit - 1)
            return [json.loads(r) for r in reversed(rows)]
        except Exception:  # noqa: BLE001
            pass
    return list(local.recent)[-limit:]
