"""Risk score, human resolution, LangGraph trace, notifications outbox, audit sealing, ops controls

Revision ID: 0003_ops_controls
Revises: 0002_paypal_realtime
Create Date: 2026-10-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_ops_controls"
down_revision: Union[str, None] = "0002_paypal_realtime"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("incidents", sa.Column("risk_score", sa.Integer()))
    op.add_column("incidents", sa.Column("risk_factors", JSONB))
    op.add_column("incidents", sa.Column("acknowledged_by", sa.String(64)))
    op.add_column("incidents", sa.Column("acknowledged_at", TS))
    op.add_column("incidents", sa.Column("resolution", sa.String(32)))
    op.add_column("incidents", sa.Column("resolution_note", sa.Text()))
    op.add_column("incidents", sa.Column("agent_trace", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")))

    op.add_column("audit_logs", sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False))
    op.create_unique_constraint("audit_logs_seq_key", "audit_logs", ["seq"])
    op.add_column("audit_logs", sa.Column("chain_index", sa.BigInteger()))
    op.create_unique_constraint("audit_logs_chain_index_key", "audit_logs", ["chain_index"])
    op.add_column("audit_logs", sa.Column("prev_hash", sa.String(64)))
    op.add_column("audit_logs", sa.Column("hash", sa.String(64)))
    op.add_column("audit_logs", sa.Column("sealed_at", TS))

    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", JSONB, nullable=False),
        sa.Column("updated_by", sa.String(64)),
        sa.Column("updated_at", TS, nullable=False),
    )

    op.create_table(
        "notifications",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("dedup_key", sa.String(160), nullable=False, unique=True),
        sa.Column("incident_id", UUID, sa.ForeignKey("incidents.id", ondelete="CASCADE")),
        sa.Column("transaction_id", sa.String(64)),
        sa.Column("severity", sa.String(4), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("link", sa.String(200)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("escalation_level", sa.Integer(), nullable=False),
        sa.Column("acknowledged_by", sa.String(64)),
        sa.Column("acknowledged_at", TS),
        sa.Column("resolved_at", TS),
        sa.Column("last_alerted_at", TS),
        sa.Column("created_at", TS, nullable=False),
    )
    for col in ("incident_id", "transaction_id", "severity", "status", "created_at"):
        op.create_index(f"ix_notifications_{col}", "notifications", [col])

    op.create_table(
        "notification_deliveries",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("notification_id", UUID, sa.ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.String(24), nullable=False),
        sa.Column("target", sa.String(120), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("escalation_level", sa.Integer(), nullable=False),
        sa.Column("provider_message_id", sa.String(120)),
        sa.Column("error", sa.String(400)),
        sa.Column("sent_at", TS),
        sa.Column("created_at", TS, nullable=False),
    )
    for col in ("notification_id", "status", "created_at"):
        op.create_index(f"ix_notification_deliveries_{col}", "notification_deliveries", [col])


def downgrade() -> None:
    op.drop_table("notification_deliveries")
    op.drop_table("notifications")
    op.drop_table("app_settings")
    for col in ("sealed_at", "hash", "prev_hash"):
        op.drop_column("audit_logs", col)
    op.drop_constraint("audit_logs_chain_index_key", "audit_logs")
    op.drop_column("audit_logs", "chain_index")
    op.drop_constraint("audit_logs_seq_key", "audit_logs")
    op.drop_column("audit_logs", "seq")
    for col in ("agent_trace", "resolution_note", "resolution", "acknowledged_at", "acknowledged_by", "risk_factors", "risk_score"):
        op.drop_column("incidents", col)
