"""PayPal Sandbox integration tests (mocked HTTP, no network, no credentials)."""

import json
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.core.errors import ConflictError
from app.events.bus import local
from app.models import Action, AuditLog, Incident, LedgerEntry, Payment, WebhookEvent
from app.payments import PayPalProvider, ProviderError
from app.payments.sandbox import LiveEndpointBlockedError, assert_sandbox_url
from app.services import paypal_service
from app.services.orchestrator import approve_action
from app.workers.dispatcher import run_job_chain
from tests.paypal_mock import FakePayPal, webhook_headers

TIMERS = {"check_webhook_arrival", "deliver_delayed_webhook"}


async def _payment(txn: str) -> Payment:
    async with SessionLocal() as s:
        return await s.scalar(select(Payment).where(Payment.transaction_id == txn))


async def _incident(txn: str) -> Incident | None:
    async with SessionLocal() as s:
        return await s.scalar(
            select(Incident).join(Payment, Payment.id == Incident.payment_id)
            .where(Payment.transaction_id == txn).order_by(Incident.created_at.desc()).limit(1)
        )


async def _events(txn: str) -> list[str]:
    async with SessionLocal() as s:
        return list(await s.scalars(
            select(AuditLog.event).where(AuditLog.transaction_id == txn).order_by(AuditLog.created_at, AuditLog.id)
        ))


async def _create_and_capture(paypal: FakePayPal, *, demo="PAYMENT_ONLY", failures=(), amount="50.00", negative=None) -> str:
    async with SessionLocal() as s:
        created = await paypal_service.create_paypal_payment(
            s, amount=Decimal(amount), demo=demo, failure_scenarios=list(failures), negative_test=negative)
        txn, order_id = created.payment.transaction_id, created.order_id
    paypal.approve(order_id)
    async with SessionLocal() as s:
        await paypal_service.capture_paypal_payment(s, transaction_id=txn)
    return txn


# ---- authentication / sandbox guard -------------------------------------------------------------


async def test_oauth_token_is_cached_and_refreshed_on_401(paypal):
    from app.payments import get_provider

    provider = get_provider()
    await provider.get_order.__self__.auth.get_token()
    await provider.auth.get_token()
    assert paypal.token_requests == 1  # cached
    async with SessionLocal() as s:
        created = await paypal_service.create_paypal_payment(s, amount=Decimal("10"))
    paypal.expire_token_once = True
    order = await provider.get_order(created.order_id)
    assert order.order_id == created.order_id
    assert paypal.token_requests == 2  # 401 → refreshed exactly once


async def test_bad_credentials_raise_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_client"})

    provider = PayPalProvider("bad", "bad", transport=httpx.MockTransport(handler), backoff=0)
    assert await provider.health() == "AUTH_FAILED"
    with pytest.raises(ProviderError) as exc:
        await provider.auth.get_token()
    assert exc.value.kind == "AUTH"
    assert "bad" not in exc.value.message  # secret never echoed


def test_live_endpoints_are_blocked():
    assert_sandbox_url("https://api-m.sandbox.paypal.com/v2/checkout/orders")
    with pytest.raises(LiveEndpointBlockedError):
        assert_sandbox_url("https://api-m.paypal.com/v2/checkout/orders")
    with pytest.raises(LiveEndpointBlockedError):
        PayPalProvider("id", "secret", base_url="https://api-m.paypal.com")


async def test_transient_5xx_is_retried(paypal):
    paypal.fail_next = [503]
    async with SessionLocal() as s:
        created = await paypal_service.create_paypal_payment(s, amount=Decimal("10"))
    assert created.order_id.startswith("ORDER")


# ---- orders / capture ------------------------------------------------------------------------------


async def test_create_order_records_provider_state(paypal):
    async with SessionLocal() as s:
        created = await paypal_service.create_paypal_payment(s, amount=Decimal("50"), demo="LEDGER_MISMATCH")
    assert created.payment.transaction_id == "TXN92831"  # hero id for the live ledger demo
    assert created.approve_url.startswith("https://www.sandbox.paypal.com/")
    payment = await _payment("TXN92831")
    assert (payment.provider, payment.currency, payment.provider_order_id) == ("PAYPAL_SANDBOX", "USD", created.order_id)
    assert payment.overall_status == "CREATED" and not payment.is_simulated
    assert paypal.headers_for("POST", r"/v2/checkout/orders$")[0]["paypal-request-id"] == "paypal:order:TXN92831"
    assert "PAYMENT_APPROVAL_STARTED" in await _events("TXN92831")


async def test_capture_before_approval_is_refused(paypal):
    async with SessionLocal() as s:
        created = await paypal_service.create_paypal_payment(s, amount=Decimal("10"))
    async with SessionLocal() as s:
        with pytest.raises(ConflictError):
            await paypal_service.capture_paypal_payment(s, transaction_id=created.payment.transaction_id)
    payment = await _payment(created.payment.transaction_id)
    assert payment.provider_capture_id is None and payment.gateway_status == "PENDING"


async def test_capture_is_idempotent(paypal):
    txn = await _create_and_capture(paypal)
    payment = await _payment(txn)
    assert payment.provider_status == "COMPLETED" and payment.gateway_status == "SUCCESS"
    assert payment.bank_status == "SETTLED" and payment.overall_status == "SETTLED"
    assert payment.payer["payer_id"] == "BUYER123"
    header = paypal.headers_for("POST", r"/capture$")[0]
    assert header["paypal-request-id"] == f"paypal:capture:{payment.provider_order_id}"
    async with SessionLocal() as s:
        again = await paypal_service.capture_paypal_payment(s, transaction_id=txn)
    assert again.status == "ALREADY_CAPTURED"
    assert paypal.count("POST", r"/capture$") == 1


# ---- hero demo: real-time ledger mismatch -----------------------------------------------------------


async def test_realtime_ledger_mismatch_end_to_end(paypal, kb):
    local.recent.clear()
    txn = await _create_and_capture(paypal, demo="LEDGER_MISMATCH", failures=["LEDGER_WRITE_FAILURE"])
    await run_job_chain("post_capture", (txn,), skip=TIMERS)

    incident = await _incident(txn)
    assert incident.type == "LEDGER_MISMATCH"
    assert incident.failure_source == "PAYFLOW_INFRASTRUCTURE_FAILURE"
    assert incident.injected_scenario == "LEDGER_WRITE_FAILURE"
    assert incident.initial_snapshot["gateway"] == "SUCCESS" and incident.initial_snapshot["ledger"] == "FAILED"
    assert incident.status == "RESOLVED"
    assert incident.policy_decision == "ALLOW"

    payment = await _payment(txn)
    assert payment.provider_status == "COMPLETED"  # PayPal's result was never altered
    assert (payment.merchant_status, payment.ledger_status, payment.reconciliation_status) == ("SUCCESS", "SUCCESS", "MATCHED")
    async with SessionLocal() as s:
        action = await s.scalar(select(Action).where(Action.incident_id == incident.id))
    assert action.idempotency_key == f"payflow:reconcile:{txn}"
    checks = {c["name"]: c["passed"] for c in action.result["verification"]["checks"]}
    assert checks["PayPal capture status"] and checks["ledger amount == PayPal capture amount"]

    streamed = [e["event"] for e in local.recent if e.get("transactionId") == txn]
    for name in ("PAYMENT_CAPTURE_COMPLETED", "FAILURE_INJECTED", "RECONCILIATION_STARTED", "INCIDENT_CREATED",
                 "INVESTIGATION_STARTED", "INVESTIGATION_COMPLETED", "POLICY_EVALUATED", "ACTION_CREATED",
                 "ACTION_EXECUTED", "VERIFICATION_STARTED", "VERIFICATION_COMPLETED", "INCIDENT_RESOLVED"):
        assert name in streamed, name


async def test_verification_timeout_is_retried(paypal, kb):
    txn = await _create_and_capture(paypal, failures=["LEDGER_WRITE_FAILURE", "VERIFICATION_TIMEOUT"])
    await run_job_chain("post_capture", (txn,), skip=TIMERS)
    assert (await _incident(txn)).status == "RESOLVED"
    assert "VERIFICATION_RETRY" in await _events(txn)


@pytest.mark.parametrize("scenario", ["MERCHANT_UPDATE_FAILURE", "LEDGER_MISMATCH", "MERCHANT_LEDGER_MISMATCH"])
async def test_other_downstream_injections_are_recovered(paypal, kb, scenario):
    txn = await _create_and_capture(paypal, failures=[scenario])
    await run_job_chain("post_capture", (txn,), skip=TIMERS)
    incident = await _incident(txn)
    assert incident.type == "LEDGER_MISMATCH" and incident.injected_scenario == scenario
    assert incident.status == "RESOLVED"
    async with SessionLocal() as s:
        ledger = await s.scalar(select(LedgerEntry).where(LedgerEntry.payment_id == incident.payment_id))
    assert ledger.amount == Decimal("50.00")


async def test_post_capture_injection_via_api(paypal, client):
    txn = await _create_and_capture(paypal)
    await run_job_chain("post_capture", (txn,), skip=TIMERS)
    assert await _incident(txn) is None  # clean payment first

    response = await client.post("/api/failures/inject", json={"transaction_id": txn, "scenario": "LEDGER_WRITE_FAILURE"})
    assert response.status_code == 200, response.text
    assert response.json()["source"] == "PAYFLOW_DEMO_ENVIRONMENT"
    incident = await _incident(txn)
    assert incident.injected_scenario == "LEDGER_WRITE_FAILURE" and incident.status == "RESOLVED"

    bad = await client.post("/api/failures/inject", json={"transaction_id": txn, "scenario": "NOT_A_SCENARIO"})
    assert bad.status_code == 400
    before = await client.post("/api/failures/inject", json={"transaction_id": txn, "scenario": "WEBHOOK_DROP"})
    assert before.status_code == 409  # must be armed before capture


# ---- negative testing: provider failure ------------------------------------------------------------


async def test_paypal_decline_is_a_provider_failure(paypal, kb):
    txn = await _create_and_capture(paypal, negative="INSTRUMENT_DECLINED", amount="20")
    assert json.loads(paypal.headers_for("POST", r"/capture$")[0]["paypal-mock-response"]) == {
        "mock_application_codes": "INSTRUMENT_DECLINED"}
    await run_job_chain("reconcile", (txn, "capture-failed"), skip=TIMERS)
    incident = await _incident(txn)
    assert incident.type == "PROVIDER_DECLINED"
    assert incident.failure_source == "PAYPAL_PROVIDER_FAILURE"
    assert incident.injected_scenario is None
    assert incident.recommended_action == "MARK_PAYMENT_FAILED" and incident.status == "RESOLVED"
    assert (await _payment(txn)).overall_status == "FAILED"


# ---- refund demo: human approval + real refund API ------------------------------------------------------


async def test_refund_requires_approval_then_calls_paypal_once(paypal, kb):
    txn = await _create_and_capture(paypal, demo="REFUND_REQUIRES_APPROVAL", amount="50")
    assert txn == "TXN92842"
    await run_job_chain("post_capture", (txn,), skip=TIMERS)
    incident = await _incident(txn)
    assert incident.type == "REFUND_REQUESTED" and incident.status == "AWAITING_APPROVAL"
    assert incident.policy_decision == "HUMAN_APPROVAL_REQUIRED"
    assert paypal.count("POST", r"/refund$") == 0  # nothing moves before a human approves

    async with SessionLocal() as s:
        action = await s.scalar(select(Action).where(Action.incident_id == incident.id))
    async with SessionLocal() as s:
        outcome = await approve_action(s, action.id, approver="demo.judge")
    assert outcome.incident_status == "RESOLVED" and outcome.verification["status"] == "PASSED"
    payment = await _payment(txn)
    assert payment.provider_status == "REFUNDED" and payment.overall_status == "REFUNDED"
    refund_headers = paypal.headers_for("POST", r"/refund$")
    assert refund_headers[0]["paypal-request-id"] == f"paypal:refund:{payment.provider_capture_id}"

    async with SessionLocal() as s:
        again = await approve_action(s, action.id, approver="demo.judge")
    assert again.deduplicated
    assert paypal.count("POST", r"/refund$") == 1


# ---- webhooks ------------------------------------------------------------------------------------------


async def _deliver(client, event: dict, signature: str = "valid-signature"):
    raw = json.dumps(event, separators=(",", ":"))
    return await client.post("/api/webhooks/paypal", content=raw, headers=webhook_headers(signature)), raw


async def test_webhook_verified_processed_and_deduplicated(paypal, client):
    txn = await _create_and_capture(paypal)
    order_id = (await _payment(txn)).provider_order_id
    event = paypal.capture_completed_event(order_id, "WH-EVT-OK")

    response, raw = await _deliver(client, event)
    assert response.status_code == 200 and response.json()["duplicate"] is False
    # PayPal's verification API received the raw body verbatim.
    assert raw in paypal.verify_bodies[-1]
    async with SessionLocal() as s:
        row = await s.scalar(select(WebhookEvent).where(WebhookEvent.provider_event_id == "WH-EVT-OK"))
    assert row.signature_verified is True and row.processing_status == "PROCESSED"
    assert (await _payment(txn)).webhook_status == "RECEIVED"

    duplicate, _ = await _deliver(client, event)
    assert duplicate.json()["duplicate"] is True
    async with SessionLocal() as s:
        row = await s.scalar(select(WebhookEvent).where(WebhookEvent.provider_event_id == "WH-EVT-OK"))
        rows = await s.scalar(select(func.count()).select_from(WebhookEvent))
    assert row.delivery_count == 2 and rows == 1
    assert "WEBHOOK_DUPLICATE" in await _events(txn)
    assert paypal.count("POST", r"/capture$") == 1


async def test_webhook_with_bad_signature_is_rejected(paypal, client):
    async with SessionLocal() as s:
        created = await paypal_service.create_paypal_payment(s, amount=Decimal("10"))
    paypal.approve(created.order_id)
    event = {"id": "WH-FORGED", "event_type": "CHECKOUT.ORDER.APPROVED",
             "resource": {"id": created.order_id, "purchase_units": [{"custom_id": created.payment.transaction_id}]}}
    response, _ = await _deliver(client, event, signature="forged")
    assert response.status_code == 200  # acknowledged, but never acted on
    async with SessionLocal() as s:
        row = await s.scalar(select(WebhookEvent).where(WebhookEvent.provider_event_id == "WH-FORGED"))
    assert row.signature_verified is False and row.processing_status == "REJECTED"
    assert (await _payment(created.payment.transaction_id)).provider_capture_id is None
    assert paypal.count("POST", r"/capture$") == 0


async def test_malformed_webhook_is_400(client):
    response = await client.post("/api/webhooks/paypal", content="not-json", headers=webhook_headers())
    assert response.status_code == 400


async def test_approval_webhook_triggers_capture(paypal, client, kb):
    async with SessionLocal() as s:
        created = await paypal_service.create_paypal_payment(s, amount=Decimal("15"))
    paypal.approve(created.order_id)
    event = {"id": "WH-APPROVED", "event_type": "CHECKOUT.ORDER.APPROVED",
             "resource": {"id": created.order_id, "purchase_units": [{"custom_id": created.payment.transaction_id}]}}
    await _deliver(client, event)
    payment = await _payment(created.payment.transaction_id)
    assert payment.provider_capture_id and payment.ledger_status == "SUCCESS"


async def test_dropped_webhook_detected_and_resynced_from_paypal(paypal, client):
    txn = await _create_and_capture(paypal, failures=["WEBHOOK_DROP"])
    await run_job_chain("post_capture", (txn,), skip=TIMERS)
    order_id = (await _payment(txn)).provider_order_id
    await _deliver(client, paypal.capture_completed_event(order_id, "WH-DROP"))
    async with SessionLocal() as s:
        row = await s.scalar(select(WebhookEvent).where(WebhookEvent.provider_event_id == "WH-DROP"))
    assert row.processing_status == "DROPPED"

    await run_job_chain("check_webhook_arrival", (txn,))  # grace period expired
    incident = await _incident(txn)
    assert incident.type == "WEBHOOK_LOST" and incident.injected_scenario == "WEBHOOK_DROP"
    assert incident.status == "RESOLVED"
    assert (await _payment(txn)).webhook_status == "RESYNCED"
    assert paypal.count("GET", r"/v2/checkout/orders/") >= 1


async def test_delayed_webhook_incident_then_late_delivery(paypal, client):
    txn = await _create_and_capture(paypal, failures=["WEBHOOK_DELAY"])
    await run_job_chain("post_capture", (txn,), skip=TIMERS)
    order_id = (await _payment(txn)).provider_order_id
    await _deliver(client, paypal.capture_completed_event(order_id, "WH-DELAY"))
    incident = await _incident(txn)
    assert incident.type == "WEBHOOK_DELAY" and incident.status == "RESOLVED"

    async with SessionLocal() as s:
        row = await s.scalar(select(WebhookEvent).where(WebhookEvent.provider_event_id == "WH-DELAY"))
    await run_job_chain("deliver_delayed_webhook", (str(row.id),))
    assert (await _payment(txn)).webhook_status == "RECEIVED"


async def test_duplicate_webhook_injection_is_idempotent(paypal, client):
    txn = await _create_and_capture(paypal, failures=["DUPLICATE_WEBHOOK"])
    await run_job_chain("post_capture", (txn,), skip=TIMERS)
    order_id = (await _payment(txn)).provider_order_id
    await _deliver(client, paypal.capture_completed_event(order_id, "WH-DUP"))
    events = await _events(txn)
    assert events.count("WEBHOOK_DUPLICATE") == 1
    assert paypal.count("POST", r"/capture$") == 1
    assert await _incident(txn) is None


# ---- API surface -------------------------------------------------------------------------------------


async def test_paypal_api_routes(paypal, client):
    created = await client.post("/api/payments/paypal/create-order", json={"amount": "50.00", "demo": "PAYMENT_ONLY"})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["payment"]["inr_equivalent"] == 4200.0 and body["approve_url"]
    txn = body["payment"]["transaction_id"]

    early = await client.post("/api/payments/paypal/capture", json={"transaction_id": txn})
    assert early.status_code == 409
    paypal.approve(body["order_id"])
    captured = await client.post("/api/payments/paypal/capture", json={"order_id": body["order_id"]})
    assert captured.json()["status"] == "CAPTURED"

    status = (await client.get(f"/api/payments/{txn}/status")).json()
    assert status["provider_status"] == "COMPLETED" and status["reconciliation_status"] == "MATCHED"
    detail = (await client.get(f"/api/payments/{txn}")).json()
    assert {t["kind"] for t in detail["provider_transactions"]} >= {"ORDER", "CAPTURE"}

    scenarios = (await client.get("/api/failures/scenarios")).json()
    assert scenarios["label"] == "DEMO FAILURE INJECTION"
    assert {s["scenario"] for s in scenarios["scenarios"]} >= {"LEDGER_WRITE_FAILURE", "WEBHOOK_DROP", "VERIFICATION_TIMEOUT"}

    demo = await client.post("/api/demo/real-time-ledger-mismatch", json={})
    assert demo.status_code == 201 and demo.json()["armed_failures"] == ["LEDGER_WRITE_FAILURE"]

    health = (await client.get("/api/health")).json()
    assert health["paypal_environment"] == "sandbox" and "test-client-secret" not in json.dumps(health)


async def test_paypal_outage_returns_502_not_500(paypal, client):
    paypal.fail_next = [500, 500, 500]
    response = await client.post("/api/payments/paypal/create-order", json={"amount": "10.00"})
    assert response.status_code == 502
    assert "PayPal Sandbox" in response.json()["detail"]
