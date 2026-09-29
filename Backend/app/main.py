import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import (
    actions, audit, dashboard, demo, failures, health, incidents, payments, paypal, reconciliation, simulator, webhooks,
)
from app.events import stream
from app.core.config import settings
from app.core.database import engine
from app.core.errors import DomainError
from app.core.logging import configure_logging
from app.payments import ProviderError

logger = logging.getLogger("payflow")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger.info(
        "PayFlow AI starting (AI=%s, pipeline=%s, paypal=%s)",
        f"groq:{settings.GROQ_MODEL}" if settings.groq_enabled else "deterministic-fallback",
        settings.PIPELINE_MODE,
        ("sandbox" + (" + webhooks" if settings.paypal_webhooks_enabled else "")) if settings.paypal_enabled else "not configured",
    )
    yield
    await engine.dispose()


app = FastAPI(
    title="PayFlow AI",
    description="Autonomous payment-operations teammate: observes PayPal Sandbox payment events, investigates "
    "inconsistencies, applies deterministic policy, executes authorized recovery actions, verifies outcomes, "
    "reconciles financial state and keeps a complete audit trail.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(DomainError)
async def domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.exception_handler(ProviderError)
async def provider_error_handler(_: Request, exc: ProviderError) -> JSONResponse:
    # Operational error from PayPal Sandbox: surface it (with PayPal's debug id), never crash.
    return JSONResponse(status_code=502, content={"detail": f"PayPal Sandbox: {exc.message}", "provider_error": exc.as_dict()})


# paypal before payments so /payments/paypal/* is never shadowed by /payments/{transaction_id}
for module in (health, dashboard, paypal, payments, simulator, incidents, actions, audit, reconciliation, demo, webhooks,
               failures, stream):
    app.include_router(module.router, prefix="/api")


@app.get("/", include_in_schema=False)
async def root() -> dict:
    return {"service": "PayFlow AI", "docs": "/docs", "health": "/api/health"}
