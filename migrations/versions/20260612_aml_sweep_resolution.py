"""Add AML sweep manual resolution audit table

Revision ID: 20260612_aml_sweep_resolution
Revises: 20260612_aml_sweep_guard
Create Date: 2026-06-12 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "20260612_aml_sweep_resolution"
down_revision = "20260612_aml_sweep_guard"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "aml_sweep_resolution" not in table_names:
        op.create_table(
            "aml_sweep_resolution",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("transaction_id", sa.Integer(), nullable=False),
            sa.Column("deposit_id", sa.String(length=120), nullable=False),
            sa.Column("txid", sa.String(), nullable=False),
            sa.Column("crypto", sa.String(length=30), nullable=False),
            sa.Column("network", sa.String(length=30), nullable=False),
            sa.Column("address", sa.String(), nullable=False),
            sa.Column("resolution_type", sa.String(length=30), nullable=False),
            sa.Column("reviewer", sa.String(length=255), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("external_review_id", sa.String(length=255), nullable=False),
            sa.Column("idempotency_key", sa.String(length=255), nullable=False),
            sa.Column("refund_txid", sa.String(), nullable=True),
            sa.Column("refund_to_address", sa.String(), nullable=True),
            sa.Column(
                "refund_amount",
                sa.Numeric(precision=36, scale=18),
                nullable=True,
            ),
            sa.Column("refund_source_address", sa.String(), nullable=True),
            sa.Column("refund_asset", sa.String(length=80), nullable=True),
            sa.Column("refund_network", sa.String(length=40), nullable=True),
            sa.Column("refund_notes", sa.Text(), nullable=True),
            sa.Column("request_digest", sa.String(length=64), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(),
                server_default=sa.func.current_timestamp(),
                nullable=True,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                server_default=sa.func.current_timestamp(),
                nullable=True,
            ),
            sa.ForeignKeyConstraint(["transaction_id"], ["transaction.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.CheckConstraint(
                "resolution_type in ('approved', 'refunded')",
                name="ck_aml_sweep_resolution_resolution_type",
            ),
            sa.UniqueConstraint(
                "deposit_id", name="uq_aml_sweep_resolution_deposit_id"
            ),
            sa.UniqueConstraint(
                "idempotency_key",
                name="uq_aml_sweep_resolution_idempotency_key",
            ),
            sa.UniqueConstraint(
                "transaction_id",
                name="uq_aml_sweep_resolution_transaction_id",
            ),
        )

    _create_index_if_missing(
        bind,
        "aml_sweep_resolution",
        op.f("ix_aml_sweep_resolution_created_at"),
        ["created_at"],
    )
    _create_index_if_missing(
        bind,
        "aml_sweep_resolution",
        op.f("ix_aml_sweep_resolution_deposit_id"),
        ["deposit_id"],
    )
    _create_index_if_missing(
        bind,
        "aml_sweep_resolution",
        op.f("ix_aml_sweep_resolution_idempotency_key"),
        ["idempotency_key"],
    )
    _create_index_if_missing(
        bind,
        "invoice_address",
        "ix_invoice_address_crypto_addr",
        ["crypto", "addr"],
    )


def downgrade():
    bind = op.get_bind()
    _drop_index_if_exists(
        bind,
        "invoice_address",
        "ix_invoice_address_crypto_addr",
    )
    _drop_index_if_exists(
        bind,
        "aml_sweep_resolution",
        op.f("ix_aml_sweep_resolution_idempotency_key"),
    )
    _drop_index_if_exists(
        bind,
        "aml_sweep_resolution",
        op.f("ix_aml_sweep_resolution_deposit_id"),
    )
    _drop_index_if_exists(
        bind,
        "aml_sweep_resolution",
        op.f("ix_aml_sweep_resolution_created_at"),
    )
    if _table_exists(bind, "aml_sweep_resolution"):
        op.drop_table("aml_sweep_resolution")


def _table_exists(bind, table_name):
    return table_name in set(sa.inspect(bind).get_table_names())


def _create_index_if_missing(bind, table_name, index_name, columns):
    if not _table_exists(bind, table_name):
        return
    existing_indexes = {
        index["name"] for index in sa.inspect(bind).get_indexes(table_name)
    }
    if index_name not in existing_indexes:
        op.create_index(index_name, table_name, columns, unique=False)


def _drop_index_if_exists(bind, table_name, index_name):
    if not _table_exists(bind, table_name):
        return
    existing_indexes = {
        index["name"] for index in sa.inspect(bind).get_indexes(table_name)
    }
    if index_name in existing_indexes:
        op.drop_index(index_name, table_name=table_name)
