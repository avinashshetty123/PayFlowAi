from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.core.config import settings


class Base(DeclarativeBase):
    pass


def build_engine(url: str | None = None, *, null_pool: bool = False) -> AsyncEngine:
    kwargs: dict = {"pool_pre_ping": True}
    if null_pool:
        # Celery tasks run each job in a fresh event loop; pooled asyncpg
        # connections cannot be shared across loops.
        kwargs = {"poolclass": NullPool}
    return create_async_engine(url or settings.DATABASE_URL, **kwargs)


engine: AsyncEngine = build_engine(null_pool=settings.DB_NULL_POOL)
SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: one session per request, never shared globally."""
    async with SessionLocal() as session:
        yield session


@asynccontextmanager
async def session_scope(factory: async_sessionmaker[AsyncSession] | None = None):
    """Session for background work (pipelines, workers, seed)."""
    async with (factory or SessionLocal)() as session:
        yield session
