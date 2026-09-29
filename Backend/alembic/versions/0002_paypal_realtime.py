"""PayPal Sandbox provider, webhooks, failure injection, reconciliation runs, approvals

Revision ID: 0002_paypal_realtime
Revises: 0001_initial
Create Date: 2026-09-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_paypal_realtime"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()
TS = sa.DateTime(timezone=True)


def _fk(name: str, target: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, UUID, sa.ForeignKey(target, ondelete="CASCADE"), nullable=nullable)


def upgrade() -> None:
    # payments: provider view kept separate from PayFlow's normalized statuses
    op.add_column("payments", sa.Column("provider", sa.String(32), nullable=False, server_default="SYNTHETIC"))
    op.add_column("payments", sa.Column("provider_order_id", sa.String(64)))
    op.add_column("payments", sa.Column("provider_capture_id", sa.String(64)))
    op.add_column("payments", sa.Column("provider_status", sa.String(48)))
    op.add_column("payments", sa.Column("reconciliation_status", sa.String(24), nullable=False, server_default="PENDING"))
    op.add_column("payments", sa.Column("payer", JSONB))
    op.add_column("payments", sa.Column("provider_metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.create_index("ix_payments_provider", "payments", ["provider"])
    op.create_unique_constraint("payments_provider_order_id_key", "payments", ["provider_order_id"])
    op.create_unique_constraint("payments_provider_capture_id_key", "payments", ["provider_capture_id"])

    op.add_column("incidents", sa.Column("failure_source", sa.String(48)))
    op.add_column("incidents", sa.Column("injected_scenario", sa.String(48)))

    op.add_column("historical_incidents", sa.Column("reference", sa.String(24)))
    op.add_column("historical_incidents", sa.Column("resolution_mode", sa.String(24)))
    op.add_column("historical_incidents", sa.Column("resolved_in_seconds", sa.Integer()))

    op.create_table(
        "provider_transactions",
        sa.Column("id", UUID, primary_key=True),
        _fk("payment_id", "payments.id"),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("provider_reference", sa.String(64)),
        sa.Column("status", sa.String(48), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2)),
        sa.Column("currency", sa.String(3)),
        sa.Column("idempotency_key", sa.String(128)),
        sa.Column("debug_id", sa.String(64)),
        sa.Column("error", sa.String(500)),
        sa.Column("response", JSONB, nullable=False),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index("ix_provider_transactions_payment_id", "provider_transactions", ["payment_id"])
    op.create_index("ix_provider_transactions_provider_reference", "provider_transactions", ["provider_reference"])
    op.create_index("ix_provider_transactions_created_at", "provider_transactions", ["created_at"])

    op.create_table(
        "webhook_events",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_event_id", sa.String(96), nullable=False),
        sa.Column("event_type", sa.String(96), nullable=False),
        sa.Column("resource_id", sa.String(64)),
        sa.Column("transaction_id", sa.String(64)),
        sa.Column("transmission_id", sa.String(96)),
        sa.Column("signature_verified", sa.Boolean()),
        sa.Column("verification_detail", sa.String(300)),
        sa.Column("processing_status", sa.String(24), nullable=False),
        sa.Column("delivery_count", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("headers", JSONB, nullable=False),
        sa.Column("raw_body", sa.Text(), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("received_at", TS, nullable=False),
        sa.Column("processed_at", TS),
    )
    op.create_index("ix_webhook_events_provider_event_id", "webhook_events", ["provider_event_id"], unique=True)
    op.create_index("ix_webhook_events_resource_id", "webhook_events", ["resource_id"])
    op.create_index("ix_webhook_events_transaction_id", "webhook_events", ["transaction_id"])
    op.create_index("ix_webhook_events_received_at", "webhook_events", ["received_at"])

    op.create_table(
        "failure_injections",
        sa.Column("id", UUID, primary_key=True),
        _fk("payment_id", "payments.id"),
        sa.Column("scenario", sa.String(48), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("injected_at", TS),
        sa.Column("injected_by", sa.String(64), nullable=False),
        sa.Column("metadata", JSONB, nullable=False),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index("ix_failure_injections_payment_id", "failure_injections", ["payment_id"])
    op.create_index("ix_failure_injections_created_at", "failure_injections", ["created_at"])

    op.create_table(
        "reconciliation_runs",
        sa.Column("id", UUID, primary_key=True),
        _fk("payment_id", "payments.id", nullable=True),
        sa.Column("trigger", sa.String(48), nullable=False),
        sa.Column("checked", sa.Integer(), nullable=False),
        sa.Column("mismatches", sa.Integer(), nullable=False),
        sa.Column("result", JSONB, nullable=False),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("finished_at", TS),
    )
    op.create_index("ix_reconciliation_runs_payment_id", "reconciliation_runs", ["payment_id"])
    op.create_index("ix_reconciliation_runs_started_at", "reconciliation_runs", ["started_at"])

    op.create_table(
        "approvals",
        sa.Column("id", UUID, primary_key=True),
        _fk("action_id", "actions.id"),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("approver", sa.String(64), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("policy_recheck", JSONB),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index("ix_approvals_action_id", "approvals", ["action_id"])
    op.create_index("ix_approvals_created_at", "approvals", ["created_at"])


def downgrade() -> None:
    for table in ("approvals", "reconciliation_runs", "failure_injections", "webhook_events", "provider_transactions"):
        op.drop_table(table)
    for column in ("reference", "resolution_mode", "resolved_in_seconds"):
        op.drop_column("historical_incidents", column)
    for column in ("failure_source", "injected_scenario"):
        op.drop_column("incidents", column)
    op.drop_constraint("payments_provider_capture_id_key", "payments")
    op.drop_constraint("payments_provider_order_id_key", "payments")
    op.drop_index("ix_payments_provider", "payments")
    for column in ("provider_metadata", "payer", "reconciliation_status", "provider_status", "provider_capture_id",
                   "provider_order_id", "provider"):
        op.drop_column("payments", column)
