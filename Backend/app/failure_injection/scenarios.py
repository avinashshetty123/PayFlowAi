"""Catalogue of demo failure-injection scenarios.

Every scenario breaks something in **PayFlow's own infrastructure** (webhook
intake, merchant order service, ledger, reconciliation scheduler, verifier).
None of them alters what PayPal returned.
"""

from dataclasses import dataclass
from enum import StrEnum


class FailureScenario(StrEnum):
    NONE = "NONE"
    WEBHOOK_DELAY = "WEBHOOK_DELAY"
    WEBHOOK_DROP = "WEBHOOK_DROP"
    LEDGER_WRITE_FAILURE = "LEDGER_WRITE_FAILURE"
    MERCHANT_UPDATE_FAILURE = "MERCHANT_UPDATE_FAILURE"
    DUPLICATE_WEBHOOK = "DUPLICATE_WEBHOOK"
    LEDGER_MISMATCH = "LEDGER_MISMATCH"
    MERCHANT_LEDGER_MISMATCH = "MERCHANT_LEDGER_MISMATCH"
    RECONCILIATION_DELAY = "RECONCILIATION_DELAY"
    VERIFICATION_TIMEOUT = "VERIFICATION_TIMEOUT"


@dataclass(frozen=True)
class ScenarioInfo:
    scenario: FailureScenario
    label: str
    stage: str  # webhook | merchant | ledger | reconciliation | verification
    description: str
    requires_webhooks: bool = False
    before_capture_only: bool = False
    expected_incident: str | None = None


SCENARIOS: dict[FailureScenario, ScenarioInfo] = {
    s.scenario: s
    for s in (
        ScenarioInfo(FailureScenario.NONE, "None", "none", "No failure: the payment flows through every system normally."),
        ScenarioInfo(
            FailureScenario.WEBHOOK_DELAY, "Delay webhook", "webhook",
            "PayFlow's webhook intake holds the verified PayPal webhook before processing it.",
            requires_webhooks=True, before_capture_only=True, expected_incident="WEBHOOK_DELAY",
        ),
        ScenarioInfo(
            FailureScenario.WEBHOOK_DROP, "Drop webhook", "webhook",
            "PayFlow's webhook intake discards the PayPal capture webhook; detected after the grace period.",
            requires_webhooks=True, before_capture_only=True, expected_incident="WEBHOOK_LOST",
        ),
        ScenarioInfo(
            FailureScenario.LEDGER_WRITE_FAILURE, "Ledger write failure", "ledger",
            "The internal ledger write for the capture fails; merchant order succeeds.",
            expected_incident="LEDGER_MISMATCH",
        ),
        ScenarioInfo(
            FailureScenario.MERCHANT_UPDATE_FAILURE, "Merchant update failure", "merchant",
            "The merchant order service fails to mark the order paid.",
            expected_incident="LEDGER_MISMATCH",
        ),
        ScenarioInfo(
            FailureScenario.DUPLICATE_WEBHOOK, "Duplicate webhook", "webhook",
            "PayFlow re-delivers an already processed PayPal webhook to itself to prove idempotency.",
            requires_webhooks=True,
        ),
        ScenarioInfo(
            FailureScenario.LEDGER_MISMATCH, "Ledger amount mismatch", "ledger",
            "The ledger posts a wrong amount for the capture.",
            expected_incident="LEDGER_MISMATCH",
        ),
        ScenarioInfo(
            FailureScenario.MERCHANT_LEDGER_MISMATCH, "Merchant/ledger mismatch", "merchant",
            "The ledger posts the capture but the merchant order update is lost.",
            expected_incident="LEDGER_MISMATCH",
        ),
        ScenarioInfo(
            FailureScenario.RECONCILIATION_DELAY, "Reconciliation delay", "reconciliation",
            "The reconciliation run for this payment is postponed.",
        ),
        ScenarioInfo(
            FailureScenario.VERIFICATION_TIMEOUT, "Verification timeout", "verification",
            "The first post-action verification attempt times out; the verifier retries.",
        ),
    )
}

# Which injected scenarios explain which detected incident types.
EXPLAINS: dict[str, set[str]] = {
    "LEDGER_MISMATCH": {"LEDGER_WRITE_FAILURE", "MERCHANT_UPDATE_FAILURE", "LEDGER_MISMATCH", "MERCHANT_LEDGER_MISMATCH"},
    "WEBHOOK_DELAY": {"WEBHOOK_DELAY"},
    "WEBHOOK_LOST": {"WEBHOOK_DROP"},
}
