"""Optional pgvector enablement. Everything keeps working when the extension is missing."""

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.rag.embedding_service import EMBEDDING_DIM

logger = logging.getLogger(__name__)


async def ensure_vector_column(conn: AsyncConnection) -> bool:
    available = await conn.scalar(text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'"))
    if not available:
        logger.info("pgvector not available; RAG will use Python cosine similarity")
        return False
    try:
        async with conn.begin_nested():
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.execute(
                text(f"ALTER TABLE historical_incidents ADD COLUMN IF NOT EXISTS embedding vector({EMBEDDING_DIM})")
            )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not enable pgvector (%s); using fallback similarity", exc)
        return False
