"""Deterministic, explainable risk score (0–100) for every remediation decision.

Every point is attributable to a named factor, so an auditor (or judge) can see
exactly why PayFlow automated one fix and asked a human for another.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from app.core.config import settings

TYPE_BASE = {
    "UNKNOWN_STATE": 25, "SETTLEMENT_MISMATCH": 25, "DUPLICATE_PAYMENT": 15, "REFUND_FAILURE": 15,
    "REFUND_REQUESTED": 12, "LEDGER_MISMATCH": 6, "WEBHOOK_DELAY": 4, "WEBHOOK_LOST": 6,
    "GATEWAY_TIMEOUT": 5, "PROVIDER_DECLINED": 5,
}
HUMAN_THRESHOLD = 75


@dataclass
class RiskScore:
    score: int
    band: str
    factors: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"score": self.score, "band": self.band, "threshold": HUMAN_THRESHOLD, "factors": self.factors}


def band_for(score: int) -> str:
    return "LOW" if score < 35 else "MEDIUM" if score < 60 else "HIGH" if score < 80 else "CRITICAL"


def score_risk(
    *,
    incident_type: str,
    amount: Decimal,
    currency: str,
    ai_confidence: float | None,
    ai_risk: str | None,
    action: str | None,
    repeat_incidents: int = 0,
    provider_failure: bool = False,
    internal_fault: bool = False,
    amount_mismatch: bool = False,
) -> RiskScore:
    factors: list[dict] = []

    def add(name: str, points: int, detail: str) -> None:
        if points:
            factors.append({"name": name, "points": points, "detail": detail})

    add("Incident type", TYPE_BASE.get(incident_type, 15), incident_type)

    limit = Decimal(str(settings.refund_limit(currency)))
    ratio = float(amount / limit) if limit else 0
    money_points = 30 if ratio >= 2 else 20 if ratio > 1 else 10 if ratio > 0.5 else 3
    if action != "REFUND":
        money_points = min(money_points, 10)  # non-refund fixes do not move money out
    add("Amount at risk", money_points, f"{amount} {currency} vs auto-approve limit {limit}")

    if ai_confidence is not None:
        add("AI uncertainty", round((1 - ai_confidence) * 30), f"confidence {ai_confidence:.2f}")
    add("AI risk rating", {"HIGH": 15, "MEDIUM": 5}.get(ai_risk or "", 0), f"investigator rated {ai_risk}")
    add("Repeat customer incidents", min(repeat_incidents * 5, 15), f"{repeat_incidents} prior incidents")
    add("Provider-side failure", 5 if provider_failure else 0, "PayPal reported a failure")
    add("PayFlow component failure", 25 if internal_fault else 0,
        "ledger / merchant / webhook intake failed: the fixing system is the failing system")
    add("Booked amount differs", 20 if amount_mismatch else 0, "ledger amount differs from settled amount")
    if action == "REFUND":
        add("Outbound money movement", 10, "refunds send money back to the customer")

    score = max(0, min(100, sum(f["points"] for f in factors)))
    return RiskScore(score=score, band=band_for(score), factors=factors)
