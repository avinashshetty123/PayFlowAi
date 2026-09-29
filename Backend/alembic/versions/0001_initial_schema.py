"""Initial PayFlow schema (+ optional pgvector embedding column)

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-29
"""
import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()
TS = sa.DateTime(timezone=True)
EMBEDDING_DIM = 256


def _id() -> sa.Column:
    return sa.Column("id", UUID, primary_key=True)


def _created() -> sa.Column:
    return sa.Column("created_at", TS, nullable=False)


def _fk(name: str, target: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, UUID, sa.ForeignKey(target, ondelete="CASCADE"), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "payments",
        _id(),
        sa.Column("transaction_id", sa.String(64), nullable=False),
        sa.Column("customer_id", sa.String(64), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("gateway_status", sa.String(32), nullable=False),
        sa.Column("bank_status", sa.String(32), nullable=False),
        sa.Column("merchant_status", sa.String(32), nullable=False),
        sa.Column("ledger_status", sa.String(32), nullable=False),
        sa.Column("webhook_status", sa.String(32), nullable=False),
        sa.Column("overall_status", sa.String(32), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("scenario", sa.String(32)),
        sa.Column("is_simulated", sa.Boolean(), nullable=False),
        _created(),
        sa.Column("updated_at", TS, nullable=False),
    )
    op.create_index("ix_payments_transaction_id", "payments", ["transaction_id"], unique=True)
    op.create_index("ix_payments_customer_id", "payments", ["customer_id"])
    op.create_index("ix_payments_overall_status", "payments", ["overall_status"])
    op.create_index("ix_payments_created_at", "payments", ["created_at"])

    op.create_table(
        "payment_events",
        _id(),
        _fk("payment_id", "payments.id"),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        _created(),
    )
    op.create_index("ix_payment_events_payment_id", "payment_events", ["payment_id"])
    op.create_index("ix_payment_events_created_at", "payment_events", ["created_at"])

    op.create_table(
        "bank_transactions",
        _id(),
        _fk("payment_id", "payments.id"),
        sa.Column("bank_reference", sa.String(64), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("settled_at", TS),
        _created(),
    )
    op.create_index("ix_bank_transactions_payment_id", "bank_transactions", ["payment_id"])
    op.create_index("ix_bank_transactions_created_at", "bank_transactions", ["created_at"])

    op.create_table(
        "merchant_transactions",
        _id(),
        _fk("payment_id", "payments.id"),
        sa.Column("order_id", sa.String(64), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        _created(),
    )
    op.create_index("ix_merchant_transactions_payment_id", "merchant_transactions", ["payment_id"])
    op.create_index("ix_merchant_transactions_order_id", "merchant_transactions", ["order_id"])
    op.create_index("ix_merchant_transactions_created_at", "merchant_transactions", ["created_at"])

    op.create_table(
        "ledger_entries",
        _id(),
        _fk("payment_id", "payments.id"),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("entry_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        _created(),
        sa.Column("updated_at", TS, nullable=False),
    )
    op.create_index("ix_ledger_entries_payment_id", "ledger_entries", ["payment_id"])
    op.create_index("ix_ledger_entries_created_at", "ledger_entries", ["created_at"])

    op.create_table(
        "incidents",
        _id(),
        sa.Column("incident_number", sa.String(32), nullable=False),
        _fk("payment_id", "payments.id"),
        sa.Column("type", sa.String(48), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("root_cause", sa.Text()),
        sa.Column("confidence", sa.Float()),
        sa.Column("ai_summary", sa.Text()),
        sa.Column("recommended_action", sa.String(48)),
        sa.Column("requires_human", sa.Boolean(), nullable=False),
        sa.Column("risk", sa.String(16)),
        sa.Column("policy_decision", sa.String(48)),
        sa.Column("initial_snapshot", JSONB, nullable=False),
        sa.Column("final_snapshot", JSONB),
        sa.Column("detection_findings", JSONB, nullable=False),
        _created(),
        sa.Column("resolved_at", TS),
    )
    op.create_index("ix_incidents_incident_number", "incidents", ["incident_number"], unique=True)
    op.create_index("ix_incidents_payment_id", "incidents", ["payment_id"])
    op.create_index("ix_incidents_type", "incidents", ["type"])
    op.create_index("ix_incidents_status", "incidents", ["status"])
    op.create_index("ix_incidents_created_at", "incidents", ["created_at"])

    op.create_table(
        "investigations",
        _id(),
        _fk("incident_id", "incidents.id"),
        sa.Column("model", sa.String(96), nullable=False),
        sa.Column("used_fallback", sa.Boolean(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("evidence", JSONB, nullable=False),
        sa.Column("historical_matches", JSONB, nullable=False),
        sa.Column("recommendation", JSONB, nullable=False),
        sa.Column("latency_ms", sa.Integer()),
        _created(),
    )
    op.create_index("ix_investigations_incident_id", "investigations", ["incident_id"])
    op.create_index("ix_investigations_created_at", "investigations", ["created_at"])

    op.create_table(
        "actions",
        _id(),
        _fk("incident_id", "incidents.id"),
        sa.Column("action_type", sa.String(48), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("requested_by", sa.String(64), nullable=False),
        sa.Column("approved_by", sa.String(64)),
        sa.Column("reason", sa.Text()),
        sa.Column("policy", JSONB, nullable=False),
        sa.Column("result", JSONB),
        _created(),
        sa.Column("completed_at", TS),
        sa.UniqueConstraint("idempotency_key", name="uq_actions_idempotency_key"),
    )
    op.create_index("ix_actions_incident_id", "actions", ["incident_id"])
    op.create_index("ix_actions_status", "actions", ["status"])
    op.create_index("ix_actions_created_at", "actions", ["created_at"])

    op.create_table(
        "audit_logs",
        _id(),
        sa.Column("transaction_id", sa.String(64), nullable=False),
        _fk("incident_id", "incidents.id", nullable=True),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("event", sa.String(48), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence", JSONB, nullable=False),
        sa.Column("result", JSONB, nullable=False),
        _created(),
    )
    op.create_index("ix_audit_logs_transaction_id", "audit_logs", ["transaction_id"])
    op.create_index("ix_audit_logs_incident_id", "audit_logs", ["incident_id"])
    op.create_index("ix_audit_logs_event", "audit_logs", ["event"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])

    op.create_table(
        "historical_incidents",
        _id(),
        sa.Column("incident_type", sa.String(48), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("symptoms", JSONB, nullable=False),
        sa.Column("root_cause", sa.Text(), nullable=False),
        sa.Column("resolution", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("embedding_json", JSONB),
        _created(),
    )
    op.create_index("ix_historical_incidents_incident_type", "historical_incidents", ["incident_type"])
    op.create_index("ix_historical_incidents_created_at", "historical_incidents", ["created_at"])

    _enable_pgvector()


def _enable_pgvector() -> None:
    """Add a pgvector column when the extension exists; otherwise RAG uses the JSON fallback."""
    bind = op.get_bind()
    available = bind.execute(sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")).scalar()
    if not available:
        log.warning("pgvector extension not available; skipping vector column (fallback similarity will be used)")
        return
    try:
        with bind.begin_nested():
            bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
            bind.execute(sa.text(f"ALTER TABLE historical_incidents ADD COLUMN embedding vector({EMBEDDING_DIM})"))
            bind.execute(sa.text(
                "CREATE INDEX ix_historical_incidents_embedding ON historical_incidents "
                "USING hnsw (embedding vector_cosine_ops)"
            ))
        log.info("pgvector enabled for historical_incidents.embedding")
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not enable pgvector (%s); fallback similarity will be used", exc)


def downgrade() -> None:
    for table in (
        "audit_logs", "actions", "investigations", "incidents", "ledger_entries", "merchant_transactions",
        "bank_transactions", "payment_events", "payments", "historical_incidents",
    ):
        op.drop_table(table)
