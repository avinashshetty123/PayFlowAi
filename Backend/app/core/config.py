from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PAYPAL_SANDBOX_API = "https://api-m.sandbox.paypal.com"


class Settings(BaseSettings):
    """Application configuration, loaded from environment variables / .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "PayFlow AI"
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5433/payflow"
    # NullPool: used by tests (fresh event loop per test) and short-lived scripts.
    DB_NULL_POOL: bool = False
    REDIS_URL: str = "redis://localhost:6380/0"

    GROQ_API_KEY: str | None = None
    GROQ_MODEL: str = "llama-3.1-8b-instant"
    GROQ_TIMEOUT_SECONDS: float = 15.0

    # ---- Payment provider: PayPal Sandbox only ----
    PAYMENT_PROVIDER: Literal["paypal"] = "paypal"
    PAYPAL_CLIENT_ID: str | None = None
    PAYPAL_CLIENT_SECRET: str | None = None
    PAYPAL_ENVIRONMENT: Literal["sandbox"] = "sandbox"
    PAYPAL_WEBHOOK_ID: str | None = None
    PAYPAL_WEBHOOK_URL: str | None = None
    PAYPAL_TIMEOUT_SECONDS: float = 15.0
    PAYPAL_BRAND_NAME: str = "PayFlow Demo Store"
    # Where PayPal sends the buyer after approving / cancelling in the sandbox checkout.
    FRONTEND_URL: str = "http://localhost:3000"
    ENABLE_PAYPAL_NEGATIVE_TESTING: bool = False

    # ---- Demo failure injection (PayFlow infrastructure only, never PayPal) ----
    ENABLE_FAILURE_INJECTION: bool = True
    WEBHOOK_DELAY_SECONDS: float = Field(default=20, ge=1, le=600)
    WEBHOOK_GRACE_SECONDS: float = Field(default=30, ge=5, le=3600)
    RECONCILIATION_DELAY_SECONDS: float = Field(default=8, ge=1, le=600)

    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

    # How incident pipelines are dispatched:
    #   auto   -> Celery if a worker answers a ping, otherwise in-process background task
    #   celery -> always Celery (falls back to in-process if Redis is down)
    #   inline -> always in-process background task
    #   sync   -> run inside the request, timers skipped (automated tests / scripts)
    PIPELINE_MODE: Literal["auto", "celery", "inline", "sync"] = "auto"
    # Pause between pipeline stages so the UI can visibly follow the lifecycle.
    PIPELINE_STEP_DELAY_SECONDS: float = Field(default=0.8, ge=0, le=10)

    # Policy thresholds
    REFUND_AUTO_APPROVE_LIMIT: float = 5000.0  # INR (historical data)
    REFUND_AUTO_APPROVE_LIMIT_USD: float = 25.0  # PayPal sandbox payments
    MIN_AUTOMATION_CONFIDENCE: float = 0.75
    # Display-only conversion for the UI ("≈ ₹4,200 demo equivalent"). PayPal processes USD.
    USD_INR_DEMO_RATE: float = 84.0

    @field_validator("DATABASE_URL")
    @classmethod
    def _force_async_driver(cls, value: str) -> str:
        if value.startswith("postgres://"):
            value = "postgresql://" + value[len("postgres://") :]
        if value.startswith("postgresql://"):
            value = "postgresql+asyncpg://" + value[len("postgresql://") :]
        return value

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def groq_enabled(self) -> bool:
        return bool(self.GROQ_API_KEY)

    @property
    def paypal_enabled(self) -> bool:
        return bool(self.PAYPAL_CLIENT_ID and self.PAYPAL_CLIENT_SECRET)

    @property
    def paypal_webhooks_enabled(self) -> bool:
        return self.paypal_enabled and bool(self.PAYPAL_WEBHOOK_ID)

    @property
    def paypal_api_base(self) -> str:
        # Sandbox is the only supported environment; live endpoints are never used.
        return PAYPAL_SANDBOX_API

    def refund_limit(self, currency: str) -> float:
        return self.REFUND_AUTO_APPROVE_LIMIT_USD if currency == "USD" else self.REFUND_AUTO_APPROVE_LIMIT


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
