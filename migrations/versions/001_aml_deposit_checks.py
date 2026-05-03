"""Add AML deposit checks

Revision ID: 001_aml_deposit_checks
Revises: e4f8a9b2c1d8
Create Date: 2026-05-03 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "001_aml_deposit_checks"
down_revision = "e4f8a9b2c1d8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('aml_check',
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("deposit_id", sa.String(length=120), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=True),
        sa.Column("provider_status", sa.String(length=30), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("deposit_decision", sa.String(length=30), nullable=True),
        sa.Column("decision_reason", sa.String(length=80), nullable=True),
        sa.Column("score", sa.Numeric(precision=7, scale=5), nullable=True),
        sa.Column("threshold", sa.Numeric(precision=7, scale=5), nullable=True),
        sa.Column("uid", sa.String(length=128), nullable=True),
        sa.Column("asset", sa.String(length=30), nullable=True),
        sa.Column("network", sa.String(length=30), nullable=True),
        sa.Column("signals_json", sa.Text(), nullable=True),
        sa.Column("raw_response_json", sa.Text(), nullable=True),
        sa.Column("report_url", sa.String(length=512), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("skip_reason", sa.String(length=80), nullable=True),
        sa.Column("min_check_amount_fiat", sa.Numeric(), nullable=True),
        sa.Column("cumulative_window", sa.String(length=30), nullable=True),
        sa.Column("cumulative_amount_fiat", sa.Numeric(), nullable=True),
        sa.Column("cumulative_limit_fiat", sa.Numeric(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(), nullable=True),
        sa.Column("timeout_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["transaction_id"], ["transaction.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("transaction_id"),
        sa.UniqueConstraint("deposit_id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_aml_check_status", "aml_check", ["status"])
    op.create_index(
        "ix_aml_check_deposit_decision", "aml_check", ["deposit_decision"]
    )
    op.create_index("ix_aml_check_next_retry_at", "aml_check", ["next_retry_at"])
    op.create_index("ix_aml_check_created_at", "aml_check", ["created_at"])


def downgrade():
    op.drop_index("ix_aml_check_created_at", table_name="aml_check")
    op.drop_index("ix_aml_check_next_retry_at", table_name="aml_check")
    op.drop_index("ix_aml_check_deposit_decision", table_name="aml_check")
    op.drop_index("ix_aml_check_status", table_name="aml_check")
    op.drop_table("aml_check")
