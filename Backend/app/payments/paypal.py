"""PayPal Sandbox provider (Orders v2, Payments v2, webhook signature verification).

- Sandbox only: every URL is checked by ``assert_sandbox_url``.
- Idempotent: every mutating call sends ``PayPal-Request-Id`` so retries never double-charge/refund.
- Retries timeouts / 429 / 5xx with backoff; a 401 refreshes the OAuth token once.
"""

import asyncio
import json
import logging
from decimal import Decimal
from typing import Any

import httpx

from app.core.config import settings
from app.payments.base import ProviderCapture, ProviderError, ProviderOrder, ProviderRefund, WebhookVerification
from app.payments.paypal_auth import PayPalAuth
from app.payments.sandbox import assert_sandbox_url, mock_response_header

logger = logging.getLogger(__name__)

WEBHOOK_HEADERS = {
    "auth_algo": "paypal-auth-algo",
    "cert_url": "paypal-cert-url",
    "transmission_id": "paypal-transmission-id",
    "transmission_sig": "paypal-transmission-sig",
    "transmission_time": "paypal-transmission-time",
}
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
DECLINE_ISSUES = {
    "INSTRUMENT_DECLINED", "TRANSACTION_REFUSED", "INSUFFICIENT_FUNDS", "PAYER_CANNOT_PAY",
    "PAYER_ACCOUNT_LOCKED_OR_CLOSED", "PAYEE_ACCOUNT_RESTRICTED", "COMPLIANCE_VIOLATION",
}


def _money(value: dict | None) -> tuple[Decimal | None, str | None]:
    if not value:
        return None, None
    return Decimal(str(value.get("value"))), value.get("currency_code")


def _payer(body: dict) -> dict | None:
    payer = body.get("payer") or {}
    if not payer:
        return None
    name = payer.get("name") or {}
    # Deliberately minimal: identifiers only, no instrument or address data.
    return {
        "payer_id": payer.get("payer_id"),
        "email": payer.get("email_address"),
        "name": " ".join(p for p in (name.get("given_name"), name.get("surname")) if p) or None,
        "country": (payer.get("address") or {}).get("country_code"),
    }


def _first_capture(body: dict) -> dict:
    for unit in body.get("purchase_units") or []:
        captures = (unit.get("payments") or {}).get("captures") or []
        if captures:
            return captures[0]
    return {}


class PayPalProvider:
    name = "PAYPAL_SANDBOX"

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        *,
        webhook_id: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        retries: int = 3,
        backoff: float = 0.4,
    ):
        self.base_url = base_url or settings.paypal_api_base
        assert_sandbox_url(self.base_url)
        self.webhook_id = webhook_id if webhook_id is not None else settings.PAYPAL_WEBHOOK_ID
        self.timeout = timeout or settings.PAYPAL_TIMEOUT_SECONDS
        self.transport = transport
        self.retries = retries
        self.backoff = backoff
        self.auth = PayPalAuth(
            client_id if client_id is not None else settings.PAYPAL_CLIENT_ID,
            client_secret if client_secret is not None else settings.PAYPAL_CLIENT_SECRET,
            self.base_url,
            timeout=self.timeout,
            transport=transport,
        )

    @property
    def configured(self) -> bool:
        return self.auth.configured

    # ---- HTTP core -------------------------------------------------------------------------

    async def _request(
        self, method: str, path: str, *, json_body: Any = None, content: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[dict, str | None]:
        url = f"{self.base_url}{path}"
        assert_sandbox_url(url)
        refreshed = False
        attempt = 0
        while True:
            token = await self.auth.get_token()
            request_headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                               "Accept": "application/json", **(headers or {})}
            try:
                async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                    response = await client.request(method, url, json=json_body, content=content, headers=request_headers)
            except httpx.TimeoutException as exc:
                if attempt < self.retries - 1:
                    attempt += 1
                    await asyncio.sleep(self.backoff * 2**attempt)
                    continue
                raise ProviderError(f"PayPal sandbox timed out on {method} {path}", kind="TRANSPORT") from exc
            except httpx.HTTPError as exc:
                if attempt < self.retries - 1:
                    attempt += 1
                    await asyncio.sleep(self.backoff * 2**attempt)
                    continue
                raise ProviderError(f"PayPal sandbox unreachable: {exc.__class__.__name__}", kind="TRANSPORT") from exc

            debug_id = response.headers.get("paypal-debug-id")
            if response.status_code == 401 and not refreshed:
                self.auth.invalidate()
                refreshed = True
                continue
            if response.status_code in RETRYABLE_STATUS and attempt < self.retries - 1:
                attempt += 1
                await asyncio.sleep(self.backoff * 2**attempt)
                continue
            try:
                body = response.json() if response.content else {}
            except ValueError:
                body = {}
            if response.status_code >= 400:
                details = body.get("details") or [{}]
                issue = details[0].get("issue") or body.get("name")
                description = details[0].get("description") or body.get("message") or response.reason_phrase
                kind = "DECLINED" if issue in DECLINE_ISSUES else ("TRANSPORT" if response.status_code >= 500 else "PROVIDER_ERROR")
                raise ProviderError(
                    f"PayPal {method} {path} failed: {issue or response.status_code} - {description}",
                    status_code=response.status_code, issue=issue, debug_id=debug_id, kind=kind,
                )
            return body, debug_id

    # ---- Orders ----------------------------------------------------------------------------

    async def create_order(
        self, *, reference: str, amount: Decimal, currency: str, description: str, return_url: str, cancel_url: str,
        idempotency_key: str,
    ) -> ProviderOrder:
        value = f"{amount:.2f}"
        body = {
            "intent": "CAPTURE",
            "purchase_units": [{
                "reference_id": reference,
                "custom_id": reference,
                "description": description[:127],
                "amount": {"currency_code": currency, "value": value},
            }],
            "application_context": {
                "brand_name": settings.PAYPAL_BRAND_NAME,
                "user_action": "PAY_NOW",
                "shipping_preference": "NO_SHIPPING",
                "landing_page": "LOGIN",
                "locale": "en-US",
                "return_url": return_url,
                "cancel_url": cancel_url,
            },
        }
        data, debug_id = await self._request(
            "POST", "/v2/checkout/orders", json_body=body,
            headers={"PayPal-Request-Id": idempotency_key, "Prefer": "return=representation"},
        )
        return self._order(data, debug_id)

    async def get_order(self, order_id: str) -> ProviderOrder:
        data, debug_id = await self._request("GET", f"/v2/checkout/orders/{order_id}")
        return self._order(data, debug_id)

    @staticmethod
    def _order(data: dict, debug_id: str | None) -> ProviderOrder:
        links = {link.get("rel"): link.get("href") for link in data.get("links") or []}
        unit = (data.get("purchase_units") or [{}])[0]
        amount, currency = _money(unit.get("amount"))
        capture = _first_capture(data)
        return ProviderOrder(
            order_id=data["id"],
            status=data.get("status", "UNKNOWN"),
            approve_url=links.get("payer-action") or links.get("approve"),
            capture_id=capture.get("id"),
            capture_status=capture.get("status"),
            amount=amount,
            currency=currency,
            payer=_payer(data),
            debug_id=debug_id,
            raw={"id": data.get("id"), "status": data.get("status"), "intent": data.get("intent")},
        )

    async def capture_order(self, order_id: str, *, idempotency_key: str, mock_error: str | None = None) -> ProviderCapture:
        headers = {"PayPal-Request-Id": idempotency_key, "Prefer": "return=representation", **mock_response_header(mock_error)}
        try:
            data, debug_id = await self._request("POST", f"/v2/checkout/orders/{order_id}/capture", content="{}", headers=headers)
        except ProviderError as exc:
            if exc.issue == "ORDER_ALREADY_CAPTURED":
                order = await self.get_order(order_id)
                return ProviderCapture(
                    order_id=order_id, order_status=order.status, capture_id=order.capture_id,
                    capture_status=order.capture_status, amount=order.amount, currency=order.currency,
                    payer=order.payer, debug_id=order.debug_id, raw={"already_captured": True},
                )
            raise
        capture = _first_capture(data)
        amount, currency = _money(capture.get("amount"))
        breakdown = capture.get("seller_receivable_breakdown") or {}
        fee, _ = _money(breakdown.get("paypal_fee"))
        net, _ = _money(breakdown.get("net_amount"))
        return ProviderCapture(
            order_id=data.get("id", order_id),
            order_status=data.get("status", "UNKNOWN"),
            capture_id=capture.get("id"),
            capture_status=capture.get("status"),
            amount=amount,
            currency=currency,
            fee=fee,
            net_amount=net,
            payer=_payer(data),
            debug_id=debug_id,
            raw={"order_status": data.get("status"), "capture_status": capture.get("status"),
                 "status_details": capture.get("status_details"), "create_time": capture.get("create_time")},
        )

    # ---- Payments --------------------------------------------------------------------------

    async def refund_payment(
        self, capture_id: str, *, amount: Decimal, currency: str, idempotency_key: str, note: str | None = None
    ) -> ProviderRefund:
        body: dict = {"amount": {"value": f"{amount:.2f}", "currency_code": currency}}
        if note:
            body["note_to_payer"] = note[:255]
        data, debug_id = await self._request(
            "POST", f"/v2/payments/captures/{capture_id}/refund", json_body=body,
            headers={"PayPal-Request-Id": idempotency_key, "Prefer": "return=representation"},
        )
        refund_amount, refund_currency = _money(data.get("amount"))
        return ProviderRefund(
            refund_id=data["id"], status=data.get("status", "UNKNOWN"), amount=refund_amount or amount,
            currency=refund_currency or currency, debug_id=debug_id,
            raw={"id": data.get("id"), "status": data.get("status"), "status_details": data.get("status_details")},
        )

    async def get_capture(self, capture_id: str) -> dict:
        data, _ = await self._request("GET", f"/v2/payments/captures/{capture_id}")
        amount, currency = _money(data.get("amount"))
        return {"id": data.get("id"), "status": data.get("status"), "amount": amount, "currency": currency}

    async def get_refund(self, refund_id: str) -> dict:
        data, _ = await self._request("GET", f"/v2/payments/refunds/{refund_id}")
        amount, currency = _money(data.get("amount"))
        return {"id": data.get("id"), "status": data.get("status"), "amount": amount, "currency": currency}

    # ---- Webhooks --------------------------------------------------------------------------

    async def verify_webhook(self, *, headers: dict[str, str], raw_body: str) -> WebhookVerification:
        """Verify with PayPal's verify-webhook-signature API.

        The original raw body is spliced in verbatim (not re-serialized) so the
        signature is checked against exactly the bytes PayPal sent.
        """
        if not self.webhook_id:
            return WebhookVerification(False, "PAYPAL_WEBHOOK_ID not configured")
        lower = {k.lower(): v for k, v in headers.items()}
        missing = [h for h in WEBHOOK_HEADERS.values() if not lower.get(h)]
        if missing:
            return WebhookVerification(False, f"Missing PayPal transmission headers: {', '.join(missing)}")
        try:
            json.loads(raw_body)
        except ValueError:
            return WebhookVerification(False, "Webhook body is not valid JSON")
        envelope = {field: lower[header] for field, header in WEBHOOK_HEADERS.items()}
        envelope["webhook_id"] = self.webhook_id
        content = json.dumps(envelope)[:-1] + ', "webhook_event": ' + raw_body + "}"
        try:
            data, _ = await self._request("POST", "/v1/notifications/verify-webhook-signature", content=content)
        except ProviderError as exc:
            return WebhookVerification(False, f"Verification call failed: {exc.message}"[:280])
        status = data.get("verification_status")
        return WebhookVerification(status == "SUCCESS", f"verification_status={status}")

    async def health(self) -> str:
        if not self.configured:
            return "NOT_CONFIGURED"
        try:
            await self.auth.get_token()
            return "CONNECTED"
        except ProviderError as exc:
            return "AUTH_FAILED" if exc.kind == "AUTH" else "UNREACHABLE"
