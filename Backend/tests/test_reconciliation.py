from decimal import Decimal

import pytest

from app.core.enums import IncidentType, Scenario, Severity
from app.services.reconciliation_service import ReconciliationContext, compare
from app.simulator.scenarios import get_scenario


def snap(gateway, bank, merchant, ledger, webhook, overall="SETTLED"):
    return {"gateway": gateway, "bank": bank, "merchant": merchant, "ledger": ledger, "webhook": webhook, "overall": overall}


def ctx(amount="4850", bank_amount="4850", duplicate_of=None):
    return ReconciliationContext(
        amount=Decimal(amount), bank_amount=Decimal(bank_amount) if bank_amount else None, duplicate_of=duplicate_of
    )


def test_hero_ledger_mismatch_is_detected():
    result = compare(snap("SUCCESS", "SETTLED", "FAILED", "FAILED", "RECEIVED"), ctx())
    assert not result.consistent
    assert result.incident_type == IncidentType.LEDGER_MISMATCH
    assert result.severity == Severity.HIGH
    assert "Gateway SUCCESS" in result.findings
    assert "Ledger FAILED" in result.findings


def test_all_systems_agree_is_consistent():
    result = compare(snap("SUCCESS", "SETTLED", "SUCCESS", "SUCCESS", "RECEIVED"), ctx())
    assert result.consistent
    assert result.incident_type is None


def test_settlement_amount_mismatch():
    result = compare(snap("SUCCESS", "SETTLED", "SUCCESS", "SUCCESS", "RECEIVED"), ctx(bank_amount="4753"))
    assert result.incident_type == IncidentType.SETTLEMENT_MISMATCH


@pytest.mark.parametrize(
    ("webhook", "expected"), [("DELAYED", IncidentType.WEBHOOK_DELAY), ("NOT_RECEIVED", IncidentType.WEBHOOK_LOST)]
)
def test_webhook_problems(webhook, expected):
    result = compare(snap("SUCCESS", "SETTLED", "PENDING", "PENDING", webhook, "PENDING"), ctx())
    assert result.incident_type == expected


def test_duplicate_payment_wins_over_other_rules():
    result = compare(snap("SUCCESS", "SETTLED", "DUPLICATE", "SUCCESS", "RECEIVED"), ctx(duplicate_of="TXN92700"))
    assert result.incident_type == IncidentType.DUPLICATE_PAYMENT


def test_refund_failure_severity_depends_on_amount():
    high = compare(snap("REFUND_FAILED", "SETTLED", "REFUND_REQUESTED", "PENDING", "RECEIVED"), ctx("8500", "8500"))
    medium = compare(snap("REFUND_FAILED", "SETTLED", "REFUND_REQUESTED", "PENDING", "RECEIVED"), ctx("2150", "2150"))
    assert high.incident_type == medium.incident_type == IncidentType.REFUND_FAILURE
    assert high.severity == Severity.HIGH
    assert medium.severity == Severity.MEDIUM


def test_timeout_closed_as_failed_is_consistent():
    open_timeout = compare(snap("TIMEOUT", "NOT_FOUND", "PENDING", "PENDING", "NOT_RECEIVED", "UNKNOWN"), ctx(bank_amount=None))
    closed = compare(snap("TIMEOUT", "NOT_FOUND", "FAILED", "FAILED", "NOT_RECEIVED", "FAILED"), ctx(bank_amount=None))
    assert open_timeout.incident_type == IncidentType.GATEWAY_TIMEOUT
    assert closed.consistent


@pytest.mark.parametrize("scenario", list(Scenario))
def test_every_scenario_matches_its_declared_expectation(scenario):
    spec = get_scenario(scenario)
    bank_amount = None if spec.bank == "NOT_FOUND" else Decimal("1000") * spec.bank_amount_factor
    result = compare(
        snap(spec.gateway, spec.bank, spec.merchant, spec.ledger, spec.webhook, spec.state_path[-1]),
        ReconciliationContext(
            amount=Decimal("1000"), bank_amount=bank_amount,
            duplicate_of="TXN1" if scenario == Scenario.DUPLICATE_PAYMENT else None,
        ),
    )
    assert result.consistent is (not spec.expects_incident)
