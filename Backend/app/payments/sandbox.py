"""Sandbox-only guard rails for PayPal.

PayFlow never talks to PayPal live. Every outbound URL is checked against the
sandbox host, and negative-testing codes are limited to a documented allowlist.
"""

import json
from urllib.parse import urlparse

from app.core.config import PAYPAL_SANDBOX_API, settings

SANDBOX_HOST = urlparse(PAYPAL_SANDBOX_API).hostname  # api-m.sandbox.paypal.com

# PayPal sandbox negative-testing codes exposed in the UI. They are sent to PayPal via the
# ``PayPal-Mock-Response`` header; PayPal itself produces the error. Only enabled when
# ENABLE_PAYPAL_NEGATIVE_TESTING=true. Confirm availability per endpoint in PayPal's docs.
NEGATIVE_TEST_CODES: tuple[str, ...] = (
    "INSTRUMENT_DECLINED",
    "TRANSACTION_REFUSED",
    "INSUFFICIENT_FUNDS",
    "INTERNAL_SERVER_ERROR",
)


class LiveEndpointBlockedError(RuntimeError):
    pass


def assert_sandbox_url(url: str) -> None:
    host = urlparse(url).hostname
    if host != SANDBOX_HOST:
        raise LiveEndpointBlockedError(f"Refusing to call non-sandbox PayPal host: {host}")


def mock_response_header(code: str | None) -> dict[str, str]:
    if not code:
        return {}
    if not settings.ENABLE_PAYPAL_NEGATIVE_TESTING:
        raise ValueError("PayPal negative testing is disabled (ENABLE_PAYPAL_NEGATIVE_TESTING=false)")
    if code not in NEGATIVE_TEST_CODES:
        raise ValueError(f"Unsupported PayPal negative-testing code: {code}")
    return {"PayPal-Mock-Response": json.dumps({"mock_application_codes": code})}
