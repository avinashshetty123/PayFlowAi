"""Reconciliation analytics: financial exposure, break ageing, SLA, auto-heal rate, MTTR.

Turns "N mismatches" into what a finance-ops lead actually asks: how much money is
at risk right now, how old are the breaks, which ones blew their SLA, and how
much of it the system healed on its own.
"""

import hashlib
import json
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ACTIVE_INCIDENT_STATUSES
from app.models import BankTransaction, Incident, Payment, ReconciliationRun
from app.models.base import utcnow

# Minutes allowed before an open break breaches its SLA, by notification priority.
SLA_MINUTES = {"CRITICAL": 15, "HIGH": 60, "MEDIUM": 240, "LOW": 1440}
NO_MONEY_MOVED = {"GATEWAY_TIMEOUT", "PROVIDER_DECLINED"}


def exposure_for(incident_type: str, amount: Decimal, bank_amount: Decimal | None) -> Decimal:
    """Money at risk for one break (0 when no funds moved)."""
    if incident_type in NO_MONEY_MOVED:
        return Decimal("0")
    if incident_type == "SETTLEMENT_MISMATCH" and bank_amount is not None:
        return abs(amount - bank_amount)
    return amount


def match_fingerprint(snapshot: dict, amount: Decimal, currency: str) -> str:
    """Deterministic proof of the five-way state that was reconciled."""
    payload = json.dumps({"snapshot": snapshot, "amount": str(amount), "currency": currency}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


async def summary(session: AsyncSession) -> dict:
    now = utcnow()
    active = [str(s) for s in ACTIVE_INCIDENT_STATUSES]
    rows = (await session.execute(
        select(Incident, Payment).join(Payment, Payment.id == Incident.payment_id).where(Incident.status.in_(active))
    )).all()
    bank = {}
    if rows:
        bank_rows = await session.execute(
            select(BankTransaction.payment_id, BankTransaction.amount)
            .where(BankTransaction.payment_id.in_([p.id for _, p in rows]))
        )
        bank = {pid: amt for pid, amt in bank_rows.all()}

    exposure_by_type: dict[str, dict] = {}
    aging = {"< 15m": 0, "15m–1h": 0, "1h–24h": 0, "> 24h": 0}
    breaches = []
    exposure_by_currency: dict[str, float] = {}
    for incident, payment in rows:
        exposure = exposure_for(incident.type, payment.amount, bank.get(payment.id))
        bucket = exposure_by_type.setdefault(incident.type, {"type": incident.type, "count": 0, "exposure": 0.0})
        bucket["count"] += 1
        bucket["exposure"] += float(exposure)
        exposure_by_currency[payment.currency] = exposure_by_currency.get(payment.currency, 0.0) + float(exposure)
        age = (now - incident.created_at).total_seconds() / 60
        aging["< 15m" if age < 15 else "15m–1h" if age < 60 else "1h–24h" if age < 1440 else "> 24h"] += 1
        sla = SLA_MINUTES.get(incident.severity, 240)
        if age > sla:
            breaches.append({
                "incident_id": str(incident.id), "incident_number": incident.incident_number,
                "transaction_id": payment.transaction_id, "type": incident.type, "severity": incident.severity,
                "age_minutes": round(age), "sla_minutes": sla, "status": incident.status,
                "exposure": float(exposure), "currency": payment.currency,
            })

    total_payments = await session.scalar(select(func.count()).select_from(Payment)) or 0
    matched = await session.scalar(select(func.count()).where(Payment.reconciliation_status == "MATCHED")) or 0
    resolved = (await session.execute(
        select(Incident.resolution, Incident.created_at, Incident.resolved_at).where(Incident.resolved_at.is_not(None))
    )).all()
    total_incidents = await session.scalar(select(func.count()).select_from(Incident)) or 0
    auto_healed = sum(1 for r in resolved if r.resolution in (None, "AUTOMATED"))
    durations = sorted((r.resolved_at - r.created_at).total_seconds() for r in resolved if r.resolved_at > r.created_at)
    runs_24h = await session.scalar(
        select(func.count()).select_from(ReconciliationRun).where(ReconciliationRun.started_at >= now - timedelta(hours=24))
    ) or 0

    def pct(n: int, d: int) -> float:
        return round(n / d * 100, 1) if d else 100.0

    return {
        "open_breaks": len(rows),
        "exposure_by_currency": {k: round(v, 2) for k, v in exposure_by_currency.items()},
        "exposure_by_type": sorted(exposure_by_type.values(), key=lambda b: -b["exposure"]),
        "aging": [{"bucket": k, "count": v} for k, v in aging.items()],
        "sla_breaches": sorted(breaches, key=lambda b: -b["age_minutes"]),
        "sla_policy": SLA_MINUTES,
        "match_rate": pct(matched, total_payments),
        "auto_heal_rate": pct(auto_healed, total_incidents),
        "mttr_seconds": round(durations[len(durations) // 2]) if durations else None,
        "resolved_incidents": len(resolved),
        "runs_last_24h": runs_24h,
    }
