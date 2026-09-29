import logging

import redis
from redis import asyncio as aioredis

from app.core.config import settings

logger = logging.getLogger(__name__)


def redis_available(timeout: float = 0.5) -> bool:
    try:
        client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=timeout, socket_timeout=timeout)
        return bool(client.ping())
    except Exception:  # noqa: BLE001 - any failure means "unavailable"
        return False


async def async_redis_available(timeout: float = 0.5) -> bool:
    client = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=timeout, socket_timeout=timeout)
    try:
        return bool(await client.ping())
    except Exception:  # noqa: BLE001
        return False
    finally:
        await client.aclose()
