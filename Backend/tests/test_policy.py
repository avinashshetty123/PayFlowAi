from decimal import Decimal

from app.core.enums import PolicyDecision
from app.services.policy_service import PolicyContext, PolicyEngine

engine = PolicyEngine(refund_limit=5000, min_confidence=0.75)


def ctx(**overrides) -> PolicyContext:
    base = dict(
        gateway="SUCCESS", bank="SETTLED", merchant="FAILED", ledger="FAILED", webhook="RECEIVED", overall="SETTLED",
        amount=Decimal("4850"), bank_amount=Decimal("4850"), ai_confidence=0.96, ai_risk="LOW",
    )
    base.update(overrides)
    return PolicyContext(**base)


def test_reconcile_ledger_allowed_automatically():
    result = engine.evaluate("RECONCILE_LEDGER", ctx())
    assert result.decision == PolicyDecision.ALLOW
    assert result.label == "AUTOMATIC ACTION ALLOWED"
    assert all(c.passed for c in result.checks)


def test_reconcile_denied_when_bank_not_settled():
    result = engine.evaluate("RECONCILE_LEDGER", ctx(bank="PENDING"))
    assert result.decision == PolicyDecision.DENY


def test_reconcile_denied_when_ledger_and_merchant_already_in_sync():
    assert engine.evaluate("RECONCILE_LEDGER", ctx(ledger="SUCCESS", merchant="SUCCESS")).decision == PolicyDecision.DENY


def test_reconcile_allowed_for_merchant_only_or_amount_mismatch():
    assert engine.evaluate("RECONCILE_LEDGER", ctx(ledger="SUCCESS", merchant="PENDING")).decision == PolicyDecision.ALLOW
    wrong_amount = ctx(ledger="SUCCESS", merchant="SUCCESS", ledger_amount=Decimal("45"))
    assert engine.evaluate("RECONCILE_LEDGER", wrong_amount).decision == PolicyDecision.ALLOW


def test_usd_refund_threshold_from_settings():
    usd = PolicyEngine(min_confidence=0.75)
    small = ctx(gateway="SUCCESS", currency="USD", amount=Decimal("20"), ai_risk="MEDIUM", provider="PAYPAL_SANDBOX",
                provider_capture_id="CAP1")
    big = ctx(gateway="SUCCESS", currency="USD", amount=Decimal("50"), ai_risk="MEDIUM", provider="PAYPAL_SANDBOX",
              provider_capture_id="CAP1")
    assert usd.evaluate("REFUND", small).decision == PolicyDecision.ALLOW
    assert usd.evaluate("REFUND", big).decision == PolicyDecision.HUMAN_APPROVAL_REQUIRED


def test_paypal_refund_requires_capture_reference():
    no_capture = ctx(gateway="SUCCESS", currency="USD", amount=Decimal("10"), provider="PAYPAL_SANDBOX", ai_risk="LOW")
    assert PolicyEngine().evaluate("REFUND", no_capture).decision == PolicyDecision.DENY


def test_reconcile_denied_when_amounts_disagree():
    assert engine.evaluate("RECONCILE_LEDGER", ctx(bank_amount=Decimal("4700"))).decision == PolicyDecision.DENY


def test_small_refund_allowed():
    result = engine.evaluate("REFUND", ctx(gateway="REFUND_FAILED", amount=Decimal("2150"), ai_risk="MEDIUM"))
    assert result.decision == PolicyDecision.ALLOW


def test_refund_over_5000_requires_human_approval():
    result = engine.evaluate("REFUND", ctx(gateway="REFUND_FAILED", amount=Decimal("8500"), ai_risk="HIGH"))
    assert result.decision == PolicyDecision.HUMAN_APPROVAL_REQUIRED


def test_human_approval_satisfies_approval_requirement():
    result = engine.evaluate(
        "REFUND", ctx(gateway="REFUND_FAILED", amount=Decimal("8500"), ai_risk="HIGH", human_approved=True)
    )
    assert result.decision == PolicyDecision.ALLOW


def test_human_approval_cannot_override_failed_preconditions():
    result = engine.evaluate("REFUND", ctx(gateway="REFUNDED", bank="REFUNDED", human_approved=True))
    assert result.decision == PolicyDecision.DENY


def test_low_ai_confidence_escalates_to_human():
    assert engine.evaluate("RECONCILE_LEDGER", ctx(ai_confidence=0.5)).decision == PolicyDecision.HUMAN_APPROVAL_REQUIRED


def test_ai_cannot_loosen_a_denial():
    # Even a maximally confident, low-risk AI recommendation cannot bypass a failed precondition.
    result = engine.evaluate("MARK_PAYMENT_FAILED", ctx(ai_confidence=0.99, ai_risk="LOW"))
    assert result.decision == PolicyDecision.DENY


def test_unknown_action_requires_human():
    assert engine.evaluate("WIRE_MONEY_OFFSHORE", ctx()).decision == PolicyDecision.HUMAN_APPROVAL_REQUIRED


def test_escalation_always_allowed():
    assert engine.evaluate("ESCALATE", ctx(gateway="UNKNOWN", ai_requires_human=True)).decision == PolicyDecision.ALLOW


def test_mark_failed_requires_no_bank_settlement():
    ok = engine.evaluate("MARK_PAYMENT_FAILED", ctx(gateway="TIMEOUT", bank="NOT_FOUND", bank_amount=None))
    assert ok.decision == PolicyDecision.ALLOW
