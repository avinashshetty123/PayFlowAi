"""Guardrails, risk, kill switch, circuit breaker, human resolution, alerts, audit sealing, LangGraph, rich AI."""

import json
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select, text, update

from app.ai.groq_client import GroqClient
from app.ai.investigator import InvestigatorService
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.errors import ConflictError
from app.core.enums import Scenario
from app.models import Action, AuditLog, Incident, Notification, NotificationDelivery, Payment
from app.models.base import utcnow
from app.notifications import service as alerts
from app.services import control_service, incident_ops
from app.services.action_executor import ActionExecutor
from app.services.audit_chain import seal_pending, verify_chain
from app.services.orchestrator import approve_action, ingest_simulated_payment, run_incident_pipeline
from app.services.recon_insights import exposure_for, summary
from app.services.risk_service import score_risk

AI_WRONG_ACTION = {
    "incidentType": "LEDGER_MISMATCH", "rootCause": "Webhook missing", "confidence": 0.93,
    "recommendedAction": "RETRY_WEBHOOK", "risk": "LOW", "requiresHuman": False,
    "evidence": ["Gateway SUCCESS", "Ledger FAILED"], "summary": "Retry the webhook.",
}


async def _fresh(model, id_):
    async with SessionLocal() as s:
        return await s.get(model, id_)


async def _events(txn: str) -> list[str]:
    async with SessionLocal() as s:
        return list(await s.scalars(select(AuditLog.event).where(AuditLog.transaction_id == txn).order_by(AuditLog.seq)))


# ---- policy guardrail (production bug: RETRY_WEBHOOK executed for a LEDGER_MISMATCH) -------------------------


async def test_wrong_ai_action_is_overridden_by_playbook(kb, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(AI_WRONG_ACTION)}}]})

    original = InvestigatorService.__init__

    def init(self, session, client=None):
        original(self, session, client=GroqClient(api_key="k", model="openai/gpt-oss-20b",
                                                  transport=httpx.MockTransport(handler)))

    monkeypatch.setattr(InvestigatorService, "__init__", init)
    result = await ingest_simulated_payment(kb, amount=Decimal("40"), scenario=Scenario.LEDGER_MISMATCH)
    assert await run_incident_pipeline(result.incident.id, delay=0) == "RESOLVED"

    incident = await _fresh(Incident, result.incident.id)
    assert incident.recommended_action == "RECONCILE_LEDGER"
    async with SessionLocal() as s:
        action = await s.scalar(select(Action).where(Action.incident_id == incident.id))
    assert action.action_type == "RECONCILE_LEDGER"
    assert action.policy["overridden_ai_action"] == "RETRY_WEBHOOK"
    assert "AI_RECOMMENDATION_OVERRIDDEN" in await _events(result.payment.transaction_id)
    assert [step["node"] for step in incident.agent_trace][:2] == ["investigate", "investigate"]
    assert {"decide", "execute", "verify", "reconcile"} <= {step["node"] for step in incident.agent_trace}


async def test_groq_reasoning_model_params_and_json_repair():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return httpx.Response(400, json={"error": {"message": "Failed to validate JSON"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})

    client = GroqClient(api_key="k", model="openai/gpt-oss-20b", transport=httpx.MockTransport(handler))
    assert await client.chat_json("sys", "user") == {"ok": True}
    assert calls[0]["reasoning_effort"] == "low" and calls[0]["include_reasoning"] is False
    assert len(calls) == 2  # one repair retry after malformed JSON


async def test_fallback_investigation_is_rich(kb):
    result = await ingest_simulated_payment(kb, amount=Decimal("40"), scenario=Scenario.LEDGER_MISMATCH)
    outcome = await InvestigatorService(kb, client=GroqClient(api_key="")).investigate(result.incident, result.payment)
    r = outcome.result
    assert r.impact and r.impact.financial_exposure and r.impact.customer_impact
    assert len(r.remediation_plan) >= 3 and r.contributing_factors and r.preventive_measures
    assert r.confidence_rationale


# ---- risk score, kill switch, circuit breaker -----------------------------------------------------------------


def test_risk_score_is_explainable():
    low = score_risk(incident_type="LEDGER_MISMATCH", amount=Decimal("20"), currency="USD", ai_confidence=0.95,
                     ai_risk="LOW", action="RECONCILE_LEDGER")
    high = score_risk(incident_type="UNKNOWN_STATE", amount=Decimal("200"), currency="USD", ai_confidence=0.4,
                      ai_risk="HIGH", action="REFUND", repeat_incidents=3)
    assert low.score < 35 and low.band == "LOW"
    assert high.score >= 75 and high.band in ("HIGH", "CRITICAL")
    assert sum(f["points"] for f in high.factors) >= high.score


async def test_kill_switch_forces_human_approval(kb):
    await control_service.set_kill_switch(kb, enabled=True, reason="incident drill", by="cfo")
    result = await ingest_simulated_payment(kb, amount=Decimal("40"), scenario=Scenario.LEDGER_MISMATCH)
    assert await run_incident_pipeline(result.incident.id, delay=0) == "AWAITING_APPROVAL"
    async with SessionLocal() as s:
        action = await s.scalar(select(Action).where(Action.incident_id == result.incident.id))
    assert "CTL-KILL" in action.policy["fired_rules"]
    async with SessionLocal() as s:
        outcome = await approve_action(s, action.id, approver="cfo")
    assert outcome.incident_status == "RESOLVED"
    incident = await _fresh(Incident, result.incident.id)
    assert incident.resolution == "HUMAN_APPROVED"


async def test_circuit_breaker_caps_automation(kb, monkeypatch):
    monkeypatch.setattr(settings, "AUTOMATION_RATE_LIMIT", 1)
    first = await ingest_simulated_payment(kb, amount=Decimal("40"), scenario=Scenario.LEDGER_MISMATCH)
    assert await run_incident_pipeline(first.incident.id, delay=0) == "RESOLVED"
    second = await ingest_simulated_payment(kb, amount=Decimal("41"), scenario=Scenario.LEDGER_MISMATCH)
    assert await run_incident_pipeline(second.incident.id, delay=0) == "AWAITING_APPROVAL"


# ---- human resolution (bug: escalated incidents could never be closed) -----------------------------------------


async def test_escalated_incident_can_be_resolved_or_closed(kb):
    result = await ingest_simulated_payment(kb, amount=Decimal("60"), scenario=Scenario.SETTLEMENT_MISMATCH)
    assert await run_incident_pipeline(result.incident.id, delay=0) == "ESCALATED"
    async with SessionLocal() as s:
        with pytest.raises(ConflictError):
            await incident_ops.retry_remediation(s, result.incident.id, by="ops")  # no automated fix exists
    async with SessionLocal() as s:
        with pytest.raises(ConflictError):
            await incident_ops.resolve_manually(s, result.incident.id, by="ops", note="checked")  # still inconsistent
    async with SessionLocal() as s:
        incident = await incident_ops.resolve_manually(s, result.incident.id, by="finance", note="fee confirmed",
                                                       accept_risk=True)
    assert incident.status == "RESOLVED" and incident.resolution == "ACCEPTED_RISK"

    other = await ingest_simulated_payment(kb, amount=Decimal("66"), scenario=Scenario.UNKNOWN_STATE)
    await run_incident_pipeline(other.incident.id, delay=0)
    async with SessionLocal() as s:
        closed = await incident_ops.close_incident(s, other.incident.id, by="ops", reason="test transaction")
    assert closed.status == "CLOSED"


async def test_human_retry_after_failed_remediation(kb, monkeypatch):
    async def exploding(self, payment, action):
        raise RuntimeError("ledger service unavailable")

    monkeypatch.setattr(ActionExecutor, "_reconcile_ledger", exploding)
    result = await ingest_simulated_payment(kb, amount=Decimal("40"), scenario=Scenario.LEDGER_MISMATCH)
    assert await run_incident_pipeline(result.incident.id, delay=0) == "ESCALATED"
    monkeypatch.undo()
    async with SessionLocal() as s:
        await incident_ops.acknowledge(s, result.incident.id, by="oncall")
    async with SessionLocal() as s:
        outcome = await incident_ops.retry_remediation(s, result.incident.id, by="oncall", note="ledger back up")
    assert outcome.incident_status == "RESOLVED"
    assert outcome.action.idempotency_key.endswith(":attempt-2")
    incident = await _fresh(Incident, result.incident.id)
    assert incident.acknowledged_by == "oncall" and incident.resolution == "HUMAN_APPROVED"


# ---- alerts --------------------------------------------------------------------------------------------------


async def test_alerts_route_deliver_ack_and_escalate(kb, monkeypatch):
    monkeypatch.setattr(settings, "NTFY_TOPIC", "payflow-test-topic")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "987654")
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append((request.url.host, request.url.path))
        if "telegram" in request.url.host:
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})
        return httpx.Response(200, json={"id": "ntfy-1"})

    transport = httpx.MockTransport(handler)
    result = await ingest_simulated_payment(kb, amount=Decimal("85"), scenario=Scenario.REFUND_FAILURE)
    await run_incident_pipeline(result.incident.id, delay=0)  # HIGH severity → P2 opened + approval required
    async with SessionLocal() as s:
        notes = list(await s.scalars(select(Notification).where(Notification.incident_id == result.incident.id)))
    kinds = {n.category for n in notes}
    assert {"INCIDENT_OPENED", "APPROVAL_REQUIRED"} <= kinds

    async with SessionLocal() as s:
        delivered = await alerts.deliver_pending(s, transport=transport)
    assert delivered >= 4 and {"ntfy.sh", "api.telegram.org"} <= {h for h, _ in sent}
    async with SessionLocal() as s:
        receipts = list(await s.scalars(select(NotificationDelivery)))
    assert all(r.status == "SENT" for r in receipts) and all("…" in r.target or len(r.target) <= 6 for r in receipts)

    # Unacknowledged P2 → re-escalated
    async with SessionLocal() as s:
        await s.execute(update(Notification).values(last_alerted_at=utcnow() - timedelta(hours=1)))
        await s.commit()
    async with SessionLocal() as s:
        assert await alerts.escalate_unacknowledged(s) >= 1
    async with SessionLocal() as s:
        assert await alerts.acknowledge(s, None, by="ops") >= 1
    async with SessionLocal() as s:
        assert await alerts.escalate_unacknowledged(s) == 0


async def test_seed_style_bulk_never_pages(kb):
    from app.services.audit_service import SUPPRESS_EVENTS

    async with SessionLocal() as s:
        s.info[SUPPRESS_EVENTS] = True
        result = await ingest_simulated_payment(s, amount=Decimal("40"), scenario=Scenario.LEDGER_MISMATCH)
    async with SessionLocal() as s:
        assert not list(await s.scalars(select(Notification).where(Notification.incident_id == result.incident.id)))


# ---- tamper-evident audit -------------------------------------------------------------------------------------


async def test_audit_chain_detects_tampering(kb):
    result = await ingest_simulated_payment(kb, amount=Decimal("40"), scenario=Scenario.LEDGER_MISMATCH)
    await run_incident_pipeline(result.incident.id, delay=0)
    async with SessionLocal() as s:
        assert await seal_pending(s) > 5
    async with SessionLocal() as s:
        report = await verify_chain(s)
    assert report["verified"] and report["unsealed"] == 0
    async with SessionLocal() as s:
        await s.execute(text("UPDATE audit_logs SET reason = 'nothing to see here' WHERE chain_index = 3"))
        await s.commit()
    async with SessionLocal() as s:
        report = await verify_chain(s)
    assert not report["verified"] and report["broken_at"]["chain_index"] == 3


# ---- reconciliation insights -----------------------------------------------------------------------------------


async def test_reconciliation_exposure_and_sla(kb):
    assert exposure_for("SETTLEMENT_MISMATCH", Decimal("100"), Decimal("98")) == Decimal("2")
    assert exposure_for("PROVIDER_DECLINED", Decimal("100"), None) == Decimal("0")
    result = await ingest_simulated_payment(kb, amount=Decimal("60"), scenario=Scenario.SETTLEMENT_MISMATCH)
    await run_incident_pipeline(result.incident.id, delay=0)
    async with SessionLocal() as s:
        await s.execute(update(Incident).values(created_at=utcnow() - timedelta(hours=3)))
        await s.commit()
    async with SessionLocal() as s:
        report = await summary(s)
    assert report["open_breaks"] == 1
    assert report["exposure_by_currency"]["USD"] == pytest.approx(1.2)
    assert report["sla_breaches"] and report["sla_breaches"][0]["type"] == "SETTLEMENT_MISMATCH"


# ---- API surface --------------------------------------------------------------------------------------------------


async def test_ops_api_routes(client):
    body = (await client.post("/api/simulator/payments", json={"amount": 60, "scenario": "SETTLEMENT_MISMATCH", "sync": True})).json()
    incident_id = body["incident_id"]
    assert body["incident_status"] == "ESCALATED"
    assert (await client.post(f"/api/incidents/{incident_id}/acknowledge", json={"by": "judge"})).json()["acknowledged_by"] == "judge"
    blocked = await client.post(f"/api/incidents/{incident_id}/resolve", json={"by": "judge", "note": "checked"})
    assert blocked.status_code == 409
    ok = await client.post(f"/api/incidents/{incident_id}/resolve", json={"by": "judge", "note": "fee ok", "accept_risk": True})
    assert ok.json()["resolution"] == "ACCEPTED_RISK"

    policies = (await client.get("/api/policies")).json()
    assert policies["version"].startswith("v2-") and any(r["id"] == "PB-001" for r in policies["rules"])
    assert (await client.post("/api/policies/kill-switch", json={"enabled": True, "reason": "drill", "by": "judge"})).json()["enabled"]
    sim = (await client.post("/api/policies/simulate", json={"transaction_id": body["payment"]["transaction_id"],
                                                             "action": "RETRY_WEBHOOK"})).json()
    assert sim["evaluation"]["decision"] == "DENY" and "PB-001" in sim["evaluation"]["fired_rules"]

    graph = (await client.get("/api/agent/graph")).json()
    assert graph["engine"] == "langgraph" and graph["mermaid"]
    assert (await client.get("/api/audit/verify")).json()["verified"] is True
    assert "exposure_by_type" in (await client.get("/api/reconciliation/summary")).json()
    notes = (await client.get("/api/notifications")).json()
    assert notes["items"] and "unread" in notes
    channels = (await client.get("/api/notifications/channels")).json()
    assert {c["channel"] for c in channels["channels"]} >= {"whatsapp", "telegram", "ntfy"}


async def test_paypal_order_key_unique_and_amount_guard(paypal):
    from app.services import paypal_service
    from app.services.paypal_service import ProviderUnavailableError

    async with SessionLocal() as s:
        created = await paypal_service.create_paypal_payment(s, amount=Decimal("50"))
    key = paypal.headers_for("POST", r"/v2/checkout/orders$")[0]["paypal-request-id"]
    assert key.startswith(f"paypal:order:{created.payment.transaction_id}:") and len(key) > 30

    original = paypal._route

    def replay_old_order(method, path, request, headers):  # PayPal replays a cached $10 order
        response = original(method, path, request, headers)
        if method == "POST" and path == "/v2/checkout/orders":
            body = response.json()
            body["purchase_units"][0]["amount"]["value"] = "10.00"
            return httpx.Response(200, json=body)
        return response

    paypal._route = replay_old_order
    async with SessionLocal() as s:
        with pytest.raises(ProviderUnavailableError):
            await paypal_service.create_paypal_payment(s, amount=Decimal("50"))
    async with SessionLocal() as s:
        failed = await s.scalar(select(Payment).where(Payment.provider_status == "ORDER_REJECTED"))
    assert failed is not None and failed.overall_status == "FAILED"


async def test_whatsapp_meta_cloud_api_with_template_fallback(monkeypatch):
    from app.notifications import channels

    monkeypatch.setattr(settings, "WHATSAPP_ACCESS_TOKEN", "meta-token")
    monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "1161273803736000")
    monkeypatch.setattr(settings, "ALERT_WHATSAPP_TO", "7249254816, +44 7700 900123")
    targets = [t for t in channels.configured_targets() if t.channel == "whatsapp"]
    assert [t.target for t in targets] == ["917249254816", "447700900123"]

    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v21.0/1161273803736000/messages"
        assert request.headers["Authorization"] == "Bearer meta-token"
        body = json.loads(request.content)
        sent.append(body)
        if body["type"] == "text":  # no open 24h window -> Meta rejects free-form text
            return httpx.Response(400, json={"error": {"code": 131047, "message": "Re-engagement message"}})
        return httpx.Response(200, json={"messages": [{"id": "wamid.TEST"}]})

    message_id = await channels.send(targets[0], severity="P1", title="P1 incident", body="Ledger mismatch",
                                     link=None, transport=httpx.MockTransport(handler))
    assert message_id == "wamid.TEST"
    assert [b["type"] for b in sent] == ["text", "template"]
    assert sent[0]["to"] == "917249254816" and "Ledger mismatch" in sent[0]["text"]["body"]


def test_internal_fault_and_booked_amount_rules_require_four_eyes():
    from app.services.policy_service import PolicyContext, PolicyEngine

    base = dict(gateway="SUCCESS", bank="SETTLED", merchant="SUCCESS", ledger="FAILED", webhook="RECEIVED",
                overall="MISMATCH", amount=Decimal("10.00"), bank_amount=Decimal("10.00"), currency="USD",
                ai_confidence=0.95, incident_type="LEDGER_MISMATCH", risk_score=20)
    engine = PolicyEngine()
    # A synthetic glitch with matching amounts may still auto-heal...
    assert engine.evaluate("RECONCILE_LEDGER", PolicyContext(**base)).decision == "ALLOW"
    # ...but a failure inside PayFlow's own systems never does,
    internal = engine.evaluate("RECONCILE_LEDGER", PolicyContext(**base, failure_source="PAYFLOW_INFRASTRUCTURE_FAILURE"))
    assert internal.decision == "HUMAN_APPROVAL_REQUIRED" and "SYS-FAULT" in internal.fired_rules
    # nor does correcting a booked amount,
    wrong_amount = engine.evaluate("RECONCILE_LEDGER", PolicyContext(**{**base, "ledger": "SUCCESS"}, ledger_amount=Decimal("9.00")))
    assert wrong_amount.decision == "HUMAN_APPROVAL_REQUIRED" and "LEDGER-AMT" in wrong_amount.fired_rules
    # and a recorded human approval satisfies both.
    approved = engine.evaluate("RECONCILE_LEDGER", PolicyContext(
        **base, failure_source="PAYFLOW_INFRASTRUCTURE_FAILURE", ledger_amount=Decimal("9.00"), human_approved=True))
    assert approved.decision == "ALLOW"


def test_every_new_incident_reaches_whatsapp():
    assert "whatsapp" in alerts.ROUTES["P3"] and "whatsapp" in alerts.ROUTES["P2"]
    assert alerts.ROUTES["P4"] == set()
