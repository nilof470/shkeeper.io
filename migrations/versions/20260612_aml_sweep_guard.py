"""Add AML sweep guard marker

Revision ID: 20260612_aml_sweep_guard
Revises: 20260603_payout_execution_foundation
Create Date: 2026-06-12 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "20260612_aml_sweep_guard"
down_revision = "20260603_payout_execution_foundation"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    existing_columns = _existing_columns(bind)

    if "sweep_guard_required" not in existing_columns:
        op.add_column(
            "aml_check",
            sa.Column(
                "sweep_guard_required",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )

    aml_check = sa.table(
        "aml_check",
        sa.column("sweep_guard_required", sa.Boolean()),
    )
    op.execute(
        aml_check.update()
        .where(aml_check.c.sweep_guard_required.is_(None))
        .values(sweep_guard_required=False)
    )
    existing_columns = _existing_columns(bind)
    if "create_check_submitted" not in existing_columns:
        op.add_column(
            "aml_check",
            sa.Column(
                "create_check_submitted",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )

    aml_check = sa.table(
        "aml_check",
        sa.column("create_check_submitted", sa.Boolean()),
    )
    op.execute(
        aml_check.update()
        .where(aml_check.c.create_check_submitted.is_(None))
        .values(create_check_submitted=False)
    )


def downgrade():
    existing_columns = _existing_columns(op.get_bind())
    if "create_check_submitted" in existing_columns:
        op.drop_column("aml_check", "create_check_submitted")
    if "sweep_guard_required" in existing_columns:
        op.drop_column("aml_check", "sweep_guard_required")


def _existing_columns(bind):
    inspector = sa.inspect(bind)
    return {column["name"] for column in inspector.get_columns("aml_check")}
