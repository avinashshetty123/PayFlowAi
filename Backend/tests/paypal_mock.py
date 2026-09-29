"""A small stateful fake of the PayPal Sandbox REST API for automated tests.

Implements just what PayFlow uses (OAuth, Orders v2, Payments v2 refunds/captures,
webhook signature verification) with PayPal-shaped responses, PayPal-Request-Id
idempotency and PayPal-Mock-Response negative testing. Tests never hit the network.
"""

import itertools
import json
import re

import httpx

VALID_SIGNATURE = "valid-signature"


class FakePayPal:
    def __init__(self) -> None:
        self.orders: dict[str, dict] = {}
        self.captures: dict[str, dict] = {}
        self.refunds: dict[str, dict] = {}
        self.request_ids: dict[str, dict] = {}
        self.calls: list[tuple[str, str, dict]] = []
        self.token_requests = 0
        self.expire_token_once = False
        self.fail_next: list[int] = []  # HTTP statuses to return for the next non-auth calls
        self.verify_bodies: list[str] = []
        self._ids = itertools.count(1000)

    # ---- helpers for tests ----------------------------------------------------------------

    def approve(self, order_id: str) -> None:
        self.orders[order_id]["status"] = "APPROVED"

    def count(self, method: str, pattern: str) -> int:
        return sum(1 for m, p, _ in self.calls if m == method and re.search(pattern, p))

    def headers_for(self, method: str, pattern: str) -> list[dict]:
        return [h for m, p, h in self.calls if m == method and re.search(pattern, p)]

    def _id(self, prefix: str) -> str:
        return f"{prefix}{next(self._ids)}TEST"

    @staticmethod
    def _json(status: int, body: dict, debug_id: str = "dbg-test") -> httpx.Response:
        return httpx.Response(status, json=body, headers={"paypal-debug-id": debug_id})

    @staticmethod
    def _error(status: int, name: str, issue: str, description: str) -> httpx.Response:
        return httpx.Response(
            status,
            json={"name": name, "message": description, "details": [{"issue": issue, "description": description}]},
            headers={"paypal-debug-id": "dbg-error"},
        )

    def _order_body(self, order: dict) -> dict:
        body = {"id": order["id"], "status": order["status"], "intent": "CAPTURE",
                "purchase_units": [{"reference_id": order["reference"], "custom_id": order["reference"],
                                    "amount": {"currency_code": order["currency"], "value": order["value"]}}],
                "links": [{"rel": "payer-action", "href": f"https://www.sandbox.paypal.com/checkoutnow?token={order['id']}"}]}
        if order.get("capture_id"):
            capture = self.captures[order["capture_id"]]
            body["purchase_units"][0]["payments"] = {"captures": [capture]}
            body["payer"] = {"payer_id": "BUYER123", "email_address": "sb-buyer@personal.example.com",
                             "name": {"given_name": "Sandbox", "surname": "Buyer"}, "address": {"country_code": "US"}}
        return body

    # ---- transport -------------------------------------------------------------------------

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        headers = {k.lower(): v for k, v in request.headers.items()}
        assert request.url.host == "api-m.sandbox.paypal.com", "tests must only ever target the sandbox host"

        if path == "/v1/oauth2/token":
            self.token_requests += 1
            if request.headers.get("authorization", "").startswith("Basic ") is False:
                return self._error(401, "AUTHENTICATION_FAILURE", "INVALID_CLIENT", "missing basic auth")
            return self._json(200, {"access_token": f"A21-token-{self.token_requests}", "token_type": "Bearer",
                                    "expires_in": 32400})

        self.calls.append((method, path, headers))
        if self.expire_token_once:
            self.expire_token_once = False
            return self._error(401, "AUTHENTICATION_FAILURE", "INVALID_TOKEN", "token expired")
        if self.fail_next:
            return self._error(self.fail_next.pop(0), "INTERNAL_SERVER_ERROR", "INTERNAL_SERVER_ERROR", "sandbox error")

        request_id = headers.get("paypal-request-id")
        if request_id and request_id in self.request_ids:
            cached = self.request_ids[request_id]
            return self._json(cached["status"], cached["body"])

        response = self._route(method, path, request, headers)
        if request_id and response.status_code < 300:
            self.request_ids[request_id] = {"status": response.status_code, "body": response.json()}
        return response

    def _route(self, method: str, path: str, request: httpx.Request, headers: dict) -> httpx.Response:
        if method == "POST" and path == "/v2/checkout/orders":
            body = json.loads(request.content)
            unit = body["purchase_units"][0]
            order = {"id": self._id("ORDER"), "status": "PAYER_ACTION_REQUIRED", "reference": unit["reference_id"],
                     "currency": unit["amount"]["currency_code"], "value": unit["amount"]["value"], "capture_id": None}
            self.orders[order["id"]] = order
            return self._json(200, self._order_body(order))

        match = re.fullmatch(r"/v2/checkout/orders/([^/]+)(/capture)?", path)
        if match:
            order = self.orders.get(match.group(1))
            if order is None:
                return self._error(404, "RESOURCE_NOT_FOUND", "INVALID_RESOURCE_ID", "order not found")
            if method == "GET":
                return self._json(200, self._order_body(order))
            mock = headers.get("paypal-mock-response")
            if mock:
                code = json.loads(mock)["mock_application_codes"]
                status = 500 if code == "INTERNAL_SERVER_ERROR" else 422
                return self._error(status, "UNPROCESSABLE_ENTITY", code, f"mocked {code}")
            if order["capture_id"]:
                return self._error(422, "UNPROCESSABLE_ENTITY", "ORDER_ALREADY_CAPTURED", "already captured")
            if order["status"] != "APPROVED":
                return self._error(422, "UNPROCESSABLE_ENTITY", "ORDER_NOT_APPROVED", "payer has not approved")
            capture_id = self._id("CAP")
            self.captures[capture_id] = {
                "id": capture_id, "status": "COMPLETED", "custom_id": order["reference"],
                "amount": {"currency_code": order["currency"], "value": order["value"]},
                "seller_receivable_breakdown": {
                    "gross_amount": {"currency_code": order["currency"], "value": order["value"]},
                    "paypal_fee": {"currency_code": order["currency"], "value": "2.24"},
                    "net_amount": {"currency_code": order["currency"], "value": f"{float(order['value']) - 2.24:.2f}"},
                },
            }
            order["capture_id"] = capture_id
            order["status"] = "COMPLETED"
            return self._json(201, self._order_body(order))

        match = re.fullmatch(r"/v2/payments/captures/([^/]+)(/refund)?", path)
        if match:
            capture = self.captures.get(match.group(1))
            if capture is None:
                return self._error(404, "RESOURCE_NOT_FOUND", "INVALID_RESOURCE_ID", "capture not found")
            if method == "GET":
                return self._json(200, capture)
            body = json.loads(request.content or b"{}")
            refund_id = self._id("REF")
            refund = {"id": refund_id, "status": "COMPLETED", "amount": body.get("amount") or capture["amount"]}
            self.refunds[refund_id] = refund
            capture["status"] = "REFUNDED"
            return self._json(201, refund)

        match = re.fullmatch(r"/v2/payments/refunds/([^/]+)", path)
        if match and match.group(1) in self.refunds:
            return self._json(200, self.refunds[match.group(1)])

        if method == "POST" and path == "/v1/notifications/verify-webhook-signature":
            raw = request.content.decode()
            self.verify_bodies.append(raw)
            envelope = json.loads(raw)
            ok = envelope.get("transmission_sig") == VALID_SIGNATURE and envelope.get("webhook_id") == "WH-TEST-0001"
            return self._json(200, {"verification_status": "SUCCESS" if ok else "FAILURE"})

        return self._error(404, "NOT_FOUND", "NOT_FOUND", f"{method} {path}")

    # ---- webhook builders ------------------------------------------------------------------

    def capture_completed_event(self, order_id: str, event_id: str = "WH-EVT-1") -> dict:
        order = self.orders[order_id]
        capture = self.captures[order["capture_id"]] if order.get("capture_id") else {
            "id": "CAP-FROM-WEBHOOK", "amount": {"currency_code": order["currency"], "value": order["value"]}}
        return {
            "id": event_id, "event_version": "1.0", "resource_type": "capture",
            "event_type": "PAYMENT.CAPTURE.COMPLETED", "summary": "Payment completed",
            "resource": {**capture, "status": "COMPLETED", "custom_id": order["reference"],
                         "supplementary_data": {"related_ids": {"order_id": order_id}}},
        }


def webhook_headers(signature: str = VALID_SIGNATURE, transmission_id: str = "tx-1") -> dict:
    return {
        "PAYPAL-AUTH-ALGO": "SHA256withRSA",
        "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-TEST",
        "PAYPAL-TRANSMISSION-ID": transmission_id,
        "PAYPAL-TRANSMISSION-SIG": signature,
        "PAYPAL-TRANSMISSION-TIME": "2026-09-29T10:00:00Z",
        "Content-Type": "application/json",
    }
