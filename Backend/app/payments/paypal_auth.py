"""PayPal OAuth2 client-credentials authentication with token caching.

The client secret is only ever sent to PayPal's sandbox token endpoint as HTTP
Basic auth. It is never logged, returned by the API, or sent to the frontend.
"""

import asyncio
import logging
import time

import httpx

from app.payments.base import ProviderError
from app.payments.sandbox import assert_sandbox_url

logger = logging.getLogger(__name__)

REFRESH_MARGIN_SECONDS = 120  # refresh this long before PayPal's expiry


class PayPalAuth:
    def __init__(
        self,
        client_id: str | None,
        client_secret: str | None,
        base_url: str,
        *,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
        retries: int = 3,
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._base_url = base_url
        self._timeout = timeout
        self._transport = transport
        self._retries = retries
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()
        self.token_requests = 0  # observable for tests / health

    @property
    def configured(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def invalidate(self) -> None:
        self._token = None
        self._expires_at = 0.0

    def _valid(self) -> bool:
        return bool(self._token) and time.monotonic() < self._expires_at - REFRESH_MARGIN_SECONDS

    async def get_token(self) -> str:
        if self._valid():
            return self._token  # type: ignore[return-value]
        async with self._lock:
            if self._valid():
                return self._token  # type: ignore[return-value]
            await self._fetch()
            return self._token  # type: ignore[return-value]

    async def _fetch(self) -> None:
        if not self.configured:
            raise ProviderError("PayPal credentials not configured (PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET)",
                                kind="NOT_CONFIGURED")
        url = f"{self._base_url}/v1/oauth2/token"
        assert_sandbox_url(url)
        last_error: Exception | None = None
        for attempt in range(self._retries):
            try:
                async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                    response = await client.post(
                        url,
                        data={"grant_type": "client_credentials"},
                        auth=(self._client_id or "", self._client_secret or ""),
                        headers={"Accept": "application/json"},
                    )
                self.token_requests += 1
                if response.status_code in (400, 401, 403):
                    raise ProviderError(
                        f"PayPal sandbox rejected client credentials (HTTP {response.status_code})",
                        status_code=response.status_code, kind="AUTH",
                        debug_id=response.headers.get("paypal-debug-id"),
                    )
                if response.status_code >= 500:
                    raise httpx.HTTPStatusError("server error", request=response.request, response=response)
                body = response.json()
                self._token = body["access_token"]
                self._expires_at = time.monotonic() + float(body.get("expires_in", 300))
                logger.info("Obtained PayPal sandbox access token (expires in %ss)", body.get("expires_in"))
                return
            except ProviderError:
                raise
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                last_error = exc
                await asyncio.sleep(0.4 * (2**attempt))
        raise ProviderError(f"PayPal token request failed: {last_error.__class__.__name__}", kind="TRANSPORT")
