"""Add unique payout external_id guard

Revision ID: 20260529_payout_external_id_unique
Revises: 001_aml_deposit_checks
Create Date: 2026-05-29 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "20260529_payout_external_id_unique"
down_revision = "001_aml_deposit_checks"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE payout
            SET external_id = NULL
            WHERE external_id IS NOT NULL AND TRIM(external_id) = ''
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE payout
            SET external_id = TRIM(external_id)
            WHERE external_id IS NOT NULL AND external_id <> TRIM(external_id)
            """
        )
    )
    duplicates = bind.execute(
        sa.text(
            """
            SELECT crypto, external_id, COUNT(*) AS cnt
            FROM payout
            WHERE external_id IS NOT NULL
            GROUP BY crypto, external_id
            HAVING COUNT(*) > 1
            """
        )
    ).fetchall()
    if duplicates:
        formatted = ", ".join(
            f"{row.crypto}:{row.external_id}({row.cnt})" for row in duplicates
        )
        raise RuntimeError(
            "Cannot add uq_payout_crypto_external_id; duplicate payout "
            f"external_id values exist: {formatted}"
        )
    op.create_index(
        "uq_payout_crypto_external_id",
        "payout",
        ["crypto", "external_id"],
        unique=True,
    )


def downgrade():
    op.drop_index("uq_payout_crypto_external_id", table_name="payout")
