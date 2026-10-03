"""Demo data: wipe + reseed the *historical PayFlow* dataset.

These records are clearly labelled provider=PAYFLOW_HISTORICAL. They are not
PayPal data. Incident histories are produced by running the real pipeline
(deterministic investigator, fast and offline), then shifting timestamps into
the past. Live demo payments are created separately through PayPal Sandbox.
"""

import logging
import random
import time
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import SessionLocal
from app.core.enums import Scenario
from app.models.base import utcnow
from app.rag.historical_service import HistoricalIncidentService
from app.services.audit_chain import reset_chain
from app.services.audit_service import SUPPRESS_EVENTS
from app.services.orchestrator import ingest_simulated_payment, run_action_stage, run_investigation_stage

logger = logging.getLogger(__name__)

HISTORICAL = {"currency": "USD", "provider": "PAYFLOW_HISTORICAL"}

TABLES = (
    "notification_deliveries", "notifications", "app_settings",
    "approvals", "reconciliation_runs", "failure_injections", "webhook_events", "provider_transactions",
    "audit_logs", "actions", "investigations", "incidents", "ledger_entries", "merchant_transactions",
    "bank_transactions", "payment_events", "payments", "historical_incidents",
)

COMMON_AMOUNTS = [4.99, 9.99, 12.99, 19.99, 24.5, 29.0, 35.0, 49.99, 50.0, 64.0, 75.0, 99.0, 120.0, 149.99, 7.5, 15.0]

# (scenario, amount, hours ago) — auto-remediated through the full pipeline
RESOLVED_INCIDENTS = [
    (Scenario.LEDGER_MISMATCH, 12.99, 300), (Scenario.LEDGER_MISMATCH, 29.00, 230), (Scenario.LEDGER_MISMATCH, 95.00, 150),
    (Scenario.LEDGER_MISMATCH, 41.50, 40), (Scenario.WEBHOOK_DELAY, 10.75, 270), (Scenario.WEBHOOK_DELAY, 19.99, 90),
    (Scenario.WEBHOOK_LOST, 26.80, 200), (Scenario.WEBHOOK_LOST, 7.75, 60), (Scenario.TIMEOUT, 21.40, 180),
    (Scenario.TIMEOUT, 47.60, 20), (Scenario.DUPLICATE_PAYMENT, 15.49, 120), (Scenario.REFUND_FAILURE, 21.50, 30),
]
# (scenario, amount, run pipeline?, hours ago)
ACTIVE_INCIDENTS = [
    (Scenario.SETTLEMENT_MISMATCH, 184.50, True, 6.0),
    (Scenario.UNKNOWN_STATE, 66.70, True, 4.5),
    (Scenario.REFUND_FAILURE, 129.99, True, 3.0),
    (Scenario.DUPLICATE_PAYMENT, 74.00, True, 2.0),
    (Scenario.TIMEOUT, 33.30, False, 0.5),  # left OPEN so the operator can trigger an investigation
]


async def truncate_all(session: AsyncSession) -> None:
    await session.execute(text(f"TRUNCATE TABLE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
    await session.commit()


async def _backdate(session: AsyncSession, payment_ids: list, transaction_ids: list[str], delta: timedelta) -> None:
    params = {"ids": payment_ids, "d": delta}
    statements = [
        "UPDATE payments SET created_at = created_at - CAST(:d AS interval), updated_at = updated_at - CAST(:d AS interval) WHERE id = ANY(:ids)",
        "UPDATE payment_events SET created_at = created_at - CAST(:d AS interval) WHERE payment_id = ANY(:ids)",
        "UPDATE bank_transactions SET created_at = created_at - CAST(:d AS interval), settled_at = settled_at - CAST(:d AS interval) WHERE payment_id = ANY(:ids)",
        "UPDATE merchant_transactions SET created_at = created_at - CAST(:d AS interval) WHERE payment_id = ANY(:ids)",
        "UPDATE ledger_entries SET created_at = created_at - CAST(:d AS interval), updated_at = updated_at - CAST(:d AS interval) WHERE payment_id = ANY(:ids)",
        "UPDATE investigations SET created_at = created_at - CAST(:d AS interval) WHERE incident_id IN (SELECT id FROM incidents WHERE payment_id = ANY(:ids))",
        "UPDATE actions SET created_at = created_at - CAST(:d AS interval), completed_at = completed_at - CAST(:d AS interval) WHERE incident_id IN (SELECT id FROM incidents WHERE payment_id = ANY(:ids))",
        "UPDATE incidents SET created_at = created_at - CAST(:d AS interval), resolved_at = resolved_at - CAST(:d AS interval) WHERE payment_id = ANY(:ids)",
    ]
    for sql in statements:
        await session.execute(text(sql), params)
    await session.execute(
        text("UPDATE audit_logs SET created_at = created_at - CAST(:d AS interval) WHERE transaction_id = ANY(:txns)"),
        {"d": delta, "txns": transaction_ids},
    )
    await session.commit()


async def _stretch_pipeline(session: AsyncSession, incident_id, seconds_between: int = 1) -> None:
    """Spread instantly-executed pipeline audit entries ~1s apart for a readable timeline."""
    rows = (
        await session.execute(
            text("SELECT id FROM audit_logs WHERE incident_id = :i ORDER BY created_at, id"), {"i": incident_id}
        )
    ).all()
    for index, row in enumerate(rows):
        await session.execute(
            text("UPDATE audit_logs SET created_at = created_at + CAST(:s AS interval) WHERE id = :id"),
            {"s": timedelta(seconds=index * seconds_between), "id": row.id},
        )
    await session.execute(
        text(
            "UPDATE incidents SET resolved_at = (SELECT MAX(created_at) FROM audit_logs "
            "WHERE incident_id = :i AND event = 'INCIDENT_RESOLVED') WHERE id = :i AND resolved_at IS NOT NULL"
        ),
        {"i": incident_id},
    )
    await session.commit()


async def seed_demo_data(factory: async_sessionmaker[AsyncSession] | None = None, *, seed: int = 42) -> dict:
    started = time.perf_counter()
    rng = random.Random(seed)
    factory = factory or SessionLocal
    now = utcnow()
    counts = {"payments": 0, "incidents": 0, "historical_incidents": 0}

    async with factory() as session:
        session.info[SUPPRESS_EVENTS] = True  # keep bulk history off the live event stream
        await truncate_all(session)
        counts["historical_incidents"] = await HistoricalIncidentService(session).seed()
        await session.commit()

        # Consistent traffic over the last 14 days, created in chronological order.
        plan: list[tuple[float, Scenario, int]] = []
        for _ in range(110):
            roll = rng.random()
            scenario = Scenario.SUCCESS if roll < 0.80 else (Scenario.FAILED if roll < 0.93 else Scenario.PENDING)
            hours_ago = rng.uniform(0.1, 1.0) if scenario == Scenario.PENDING else rng.uniform(1.0, 14 * 24 - 1)
            amount = rng.choice(COMMON_AMOUNTS) if rng.random() < 0.6 else round(rng.randint(300, 18000) / 100, 2)
            plan.append((hours_ago, scenario, amount))

        incident_plan = [(h, s, a, True) for s, a, h in RESOLVED_INCIDENTS] + [(h, s, a, run) for s, a, run, h in ACTIVE_INCIDENTS]
        timeline: list[tuple[float, Scenario, int, bool | None]] = [(h, s, a, None) for h, s, a in plan] + incident_plan
        timeline.sort(key=lambda item: -item[0])

        for hours_ago, scenario, amount, run_pipeline in timeline:
            target = now - timedelta(hours=hours_ago)
            if run_pipeline is None:
                result = await ingest_simulated_payment(session, amount=Decimal(str(amount)), scenario=scenario, rng=rng, now=target,
                                                        **HISTORICAL)
                counts["payments"] += 1
                continue

            result = await ingest_simulated_payment(session, amount=Decimal(str(amount)), scenario=scenario, rng=rng, **HISTORICAL)
            counts["payments"] += 2 if result.related_payment else 1
            if result.incident is not None:
                counts["incidents"] += 1
                if run_pipeline:
                    action_id = await run_investigation_stage(session, result.incident.id, force_fallback=True)
                    if action_id is not None:
                        await run_action_stage(session, action_id)
                    await _stretch_pipeline(session, result.incident.id)
            ids = [result.payment.id] + ([result.related_payment.id] if result.related_payment else [])
            txns = [result.payment.transaction_id] + (
                [result.related_payment.transaction_id] if result.related_payment else []
            )
            await _backdate(session, ids, txns, utcnow() - target)

    async with factory() as session:
        await reset_chain(session)  # backdating rewrote timestamps: let the sealer re-chain everything
    counts["seconds"] = round(time.perf_counter() - started, 2)
    logger.info("Seeded demo data: %s", counts)
    return counts
