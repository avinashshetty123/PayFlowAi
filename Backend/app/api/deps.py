from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession
from redis import asyncio as aioredis
from app.db.session import AsyncSessionLocal
from app.core.config import settings

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session

async def get_redis():
    redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        yield redis
    finally:
        await redis.close()