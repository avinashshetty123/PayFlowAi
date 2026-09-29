"""Server-Sent Events endpoint: GET /api/events/stream."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from redis import asyncio as aioredis

from app.core.config import settings
from app.events.bus import CHANNEL, local, recent_events

logger = logging.getLogger(__name__)
router = APIRouter(tags=["events"])

HEARTBEAT_SECONDS = 15


def _sse(event: dict) -> str:
    return f"id: {event.get('id', '')}\nevent: payflow\ndata: {json.dumps(event)}\n\n"


async def _redis_events(request: Request) -> AsyncIterator[dict] | None:
    client = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=0.5)
    try:
        pubsub = client.pubsub()
        await pubsub.subscribe(CHANNEL)
    except Exception:  # noqa: BLE001
        await client.aclose()
        return None

    async def iterator() -> AsyncIterator[dict]:
        try:
            while not await request.is_disconnected():
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=HEARTBEAT_SECONDS)
                if message is None:
                    yield {"heartbeat": True}
                    continue
                try:
                    yield json.loads(message["data"])
                except (ValueError, TypeError):
                    continue
        finally:
            try:
                await pubsub.unsubscribe(CHANNEL)
                await pubsub.aclose()
            finally:
                await client.aclose()

    return iterator()


async def _local_events(request: Request) -> AsyncIterator[dict]:
    queue = local.subscribe()
    try:
        while not await request.is_disconnected():
            try:
                yield await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                yield {"heartbeat": True}
    finally:
        local.unsubscribe(queue)


@router.get("/events/stream")
async def stream(request: Request, backlog: int = 40) -> StreamingResponse:
    async def body() -> AsyncIterator[str]:
        source = await _redis_events(request)
        transport = "redis" if source is not None else "in-process"
        yield f"event: hello\ndata: {json.dumps({'transport': transport})}\n\n"
        for event in await recent_events(min(backlog, 200)):
            yield _sse({**event, "backlog": True})
        async for event in source or _local_events(request):
            if event.get("heartbeat"):
                yield ": keep-alive\n\n"
            else:
                yield _sse(event)

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
