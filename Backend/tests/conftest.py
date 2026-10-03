"""Test configuration.

Tests run against a real PostgreSQL database (``payflow_test`` on the same
server as DATABASE_URL, or TEST_DATABASE_URL). Environment is configured
*before* any app module is imported so settings/engine pick it up.
"""

import asyncio
import os
import sys
from pathlib import Path

from dotenv import dotenv_values

_backend = Path(__file__).resolve().parent.parent
_env = {**dotenv_values(_backend / ".env"), **dotenv_values(_backend / ".env.local")}
_base_url = os.environ.get("DATABASE_URL") or _env.get("DATABASE_URL") or "postgresql+asyncpg://postgres:postgres@localhost:5432/payflow"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or _base_url.rsplit("/", 1)[0] + "/payflow_test"

os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["DB_NULL_POOL"] = "true"
os.environ["PIPELINE_MODE"] = "sync"
os.environ["BACKGROUND_PUMP_ENABLED"] = "false"
for _channel_var in ("WHATSAPP_ACCESS_TOKEN", "TELEGRAM_BOT_TOKEN", "NTFY_TOPIC", "SLACK_WEBHOOK_URL", "ALERT_WEBHOOK_URL"):
    os.environ[_channel_var] = ""
os.environ["PIPELINE_STEP_DELAY_SECONDS"] = "0"
os.environ["GROQ_API_KEY"] = ""  # deterministic investigator unless a test injects a client
# PayPal: tests never call the real sandbox; a mocked transport is injected (see paypal_mock.py).
os.environ["PAYPAL_CLIENT_ID"] = "test-client-id"
os.environ["PAYPAL_CLIENT_SECRET"] = "test-client-secret"
os.environ["PAYPAL_WEBHOOK_ID"] = "WH-TEST-0001"
os.environ["ENABLE_PAYPAL_NEGATIVE_TESTING"] = "true"
os.environ["ENABLE_FAILURE_INJECTION"] = "true"
# Unreachable Redis: tests use the in-process event bus and never publish into a dev dashboard.
os.environ["REDIS_URL"] = "redis://127.0.0.1:6399/0"

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

import app.models  # noqa: E402,F401
from app.core.database import Base, SessionLocal  # noqa: E402
from app.rag.pgvector_setup import ensure_vector_column  # noqa: E402
from app.services.demo_service import TABLES  # noqa: E402


async def _prepare_database() -> None:
    server_url, db_name = TEST_DATABASE_URL.rsplit("/", 1)
    admin = create_async_engine(f"{server_url}/postgres", isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        exists = await conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": db_name})
        if not exists:
            await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    await admin.dispose()

    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("DROP TABLE IF EXISTS historical_incidents CASCADE"))
        await conn.run_sync(Base.metadata.create_all)
        await ensure_vector_column(conn)
    await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    asyncio.run(_prepare_database())


@pytest_asyncio.fixture
async def db():
    async with SessionLocal() as session:
        await session.execute(text(f"TRUNCATE TABLE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
        await session.commit()
        yield session


@pytest_asyncio.fixture
async def kb(db):
    """Historical incident knowledge base for RAG."""
    from app.rag.historical_service import HistoricalIncidentService

    await HistoricalIncidentService(db).seed()
    await db.commit()
    return db


@pytest.fixture
def paypal(db):
    """Inject a PayPalProvider backed by the in-memory PayPal Sandbox fake (fresh DB per test)."""
    import httpx

    from app.payments import PayPalProvider, set_provider
    from tests.paypal_mock import FakePayPal

    fake = FakePayPal()
    set_provider(PayPalProvider(transport=httpx.MockTransport(fake.handler), backoff=0))
    yield fake
    set_provider(None)


@pytest_asyncio.fixture
async def client(kb):
    import httpx

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
