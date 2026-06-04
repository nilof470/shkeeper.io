"""Add payout execution foundation

Revision ID: 20260603_payout_execution_foundation
Revises: 20260529_payout_external_id_unique
Create Date: 2026-06-03 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "20260603_payout_execution_foundation"
down_revision = "20260529_payout_external_id_unique"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payout_rail",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("consumer", sa.String(), nullable=False),
        sa.Column("asset", sa.String(), nullable=False),
        sa.Column("network", sa.String(), nullable=False),
        sa.Column("crypto_id", sa.String(), nullable=False),
        sa.Column("sidecar_service", sa.String(), nullable=False),
        sa.Column("sidecar_symbol", sa.String(), nullable=False),
        sa.Column("payout_queue", sa.String(), nullable=False),
        sa.Column("source_wallet_ref", sa.String(), nullable=False),
        sa.Column("hot_wallet_policy", sa.String(length=50), nullable=False),
        sa.Column("legacy_spend_policy", sa.String(length=50), nullable=False),
        sa.Column("wallet_guard_key", sa.String(), nullable=True),
        sa.Column("execution_enabled", sa.Boolean(), nullable=False),
        sa.Column("token_contract", sa.String(), nullable=True),
        sa.Column("chain_id_or_network_id", sa.String(), nullable=True),
        sa.Column("decimals", sa.Integer(), nullable=False),
        sa.Column("callback_endpoint_id", sa.String(), nullable=True),
        sa.Column("contract_version", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "consumer",
            "asset",
            "network",
            name="uq_payout_rail_consumer_asset_network",
        ),
    )
    op.create_index(
        "ix_payout_rail_created_at",
        "payout_rail",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_payout_rail_enabled_lookup",
        "payout_rail",
        ["consumer", "asset", "network"],
        unique=False,
    )

    op.create_table(
        "payout_execution",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("broadcasted_at", sa.DateTime(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("terminal_at", sa.DateTime(), nullable=True),
        sa.Column("last_state_occurred_at", sa.DateTime(), nullable=True),
        sa.Column("consumer", sa.String(), nullable=False),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("sidecar_execution_id", sa.String(), nullable=True),
        sa.Column("contract_version", sa.String(), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("state_transition_id", sa.String(), nullable=False),
        sa.Column("asset", sa.String(), nullable=False),
        sa.Column("network", sa.String(), nullable=False),
        sa.Column("crypto_id", sa.String(), nullable=False),
        sa.Column("sidecar_service", sa.String(), nullable=False),
        sa.Column("sidecar_symbol", sa.String(), nullable=False),
        sa.Column("payout_queue", sa.String(), nullable=False),
        sa.Column("source_wallet_ref", sa.String(), nullable=False),
        sa.Column("amount", sa.Numeric(), nullable=False),
        sa.Column("destination", sa.String(), nullable=False),
        sa.Column("request_hash", sa.String(), nullable=False),
        sa.Column("sidecar_payload_hash", sa.String(), nullable=False),
        sa.Column("callback_endpoint_id", sa.String(), nullable=True),
        sa.Column("state", sa.String(length=50), nullable=False),
        sa.Column("sidecar_state", sa.String(), nullable=True),
        sa.Column("sidecar_state_version", sa.Integer(), nullable=True),
        sa.Column("sidecar_state_transition_id", sa.String(), nullable=True),
        sa.Column("sidecar_state_updated_at", sa.DateTime(), nullable=True),
        sa.Column("last_sidecar_status_hash", sa.String(), nullable=True),
        sa.Column("last_sidecar_status_json", sa.Text(), nullable=True),
        sa.Column("last_sidecar_status_observed_at", sa.DateTime(), nullable=True),
        sa.Column("resolution_status", sa.String(length=50), nullable=False),
        sa.Column("resolution_evidence_json", sa.Text(), nullable=True),
        sa.Column("resolution_evidence_hash", sa.String(), nullable=True),
        sa.Column("resolution_operator_note", sa.Text(), nullable=True),
        sa.Column("resolved_by", sa.String(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("failure_class", sa.String(length=50), nullable=True),
        sa.Column("txids_json", sa.Text(), nullable=True),
        sa.Column("message_hashes_json", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("reconciliation_required", sa.Boolean(), nullable=False),
        sa.Column("dispatch_attempts", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(), nullable=True),
        sa.Column("lease_token", sa.String(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("next_dispatch_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "consumer",
            "external_id",
            name="uq_payout_execution_consumer_external_id",
        ),
        sa.UniqueConstraint(
            "sidecar_execution_id",
            name="uq_payout_execution_sidecar_execution_id",
        ),
        sa.UniqueConstraint(
            "state_transition_id",
            name="uq_payout_execution_state_transition_id",
        ),
    )
    op.create_index(
        "ix_payout_execution_created_at",
        "payout_execution",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_payout_execution_state",
        "payout_execution",
        ["state"],
        unique=False,
    )
    op.create_index(
        "ix_payout_execution_state_updated",
        "payout_execution",
        ["state", "updated_at"],
        unique=False,
    )
    op.create_index(
        "ix_payout_execution_reconciliation",
        "payout_execution",
        ["reconciliation_required", "updated_at"],
        unique=False,
    )

    op.create_table(
        "payout_execution_resolution_audit",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("payout_execution_id", sa.Integer(), nullable=False),
        sa.Column("execution_id", sa.Integer(), nullable=False),
        sa.Column("consumer", sa.String(), nullable=False),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("previous_state", sa.String(), nullable=True),
        sa.Column("new_state", sa.String(), nullable=False),
        sa.Column("previous_resolution_status", sa.String(), nullable=True),
        sa.Column("resolution_status", sa.String(), nullable=False),
        sa.Column("operator_id", sa.String(), nullable=False),
        sa.Column("operator_note", sa.Text(), nullable=True),
        sa.Column("evidence_hash", sa.String(), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("state_transition_id", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["payout_execution_id"], ["payout_execution.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_payout_execution_resolution_audit_created_at",
        "payout_execution_resolution_audit",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_payout_execution_resolution_audit_payout_execution_id",
        "payout_execution_resolution_audit",
        ["payout_execution_id"],
        unique=False,
    )
    op.create_index(
        "ix_payout_execution_resolution_audit_execution_id",
        "payout_execution_resolution_audit",
        ["execution_id"],
        unique=False,
    )
    op.create_index(
        "ix_payout_execution_resolution_audit_consumer",
        "payout_execution_resolution_audit",
        ["consumer"],
        unique=False,
    )
    op.create_index(
        "ix_payout_execution_resolution_audit_external_id",
        "payout_execution_resolution_audit",
        ["external_id"],
        unique=False,
    )
    op.create_index(
        "ix_payout_execution_resolution_audit_execution",
        "payout_execution_resolution_audit",
        ["payout_execution_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "payout_callback_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("payout_execution_id", sa.Integer(), nullable=False),
        sa.Column("execution_id", sa.Integer(), nullable=False),
        sa.Column("consumer", sa.String(), nullable=True),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("asset", sa.String(), nullable=True),
        sa.Column("network", sa.String(), nullable=True),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("state_transition_id", sa.String(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=True),
        sa.Column("callback_endpoint_id", sa.String(), nullable=True),
        sa.Column("payload_hash", sa.String(), nullable=False),
        sa.Column("raw_payload", sa.Text(), nullable=False),
        sa.Column("signature_key_id", sa.String(), nullable=False),
        sa.Column("signature_base", sa.Text(), nullable=True),
        sa.Column("signature_headers_json", sa.Text(), nullable=True),
        sa.Column("dispatch_status", sa.String(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=True),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
        sa.Column("apply_result", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["payout_execution_id"], ["payout_execution.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_payout_callback_event_id"),
        sa.UniqueConstraint(
            "execution_id",
            "event_version",
            name="uq_payout_callback_execution_event_version",
        ),
        sa.UniqueConstraint(
            "state_transition_id",
            name="uq_payout_callback_state_transition_id",
        ),
    )
    op.create_index(
        "ix_payout_callback_event_payout_execution_id",
        "payout_callback_event",
        ["payout_execution_id"],
        unique=False,
    )
    op.create_index(
        "ix_payout_callback_event_execution_id",
        "payout_callback_event",
        ["execution_id"],
        unique=False,
    )
    op.create_index(
        "ix_payout_callback_event_consumer",
        "payout_callback_event",
        ["consumer"],
        unique=False,
    )
    op.create_index(
        "ix_payout_callback_event_external_id",
        "payout_callback_event",
        ["external_id"],
        unique=False,
    )
    op.create_index(
        "ix_payout_callback_event_received_at",
        "payout_callback_event",
        ["received_at"],
        unique=False,
    )
    op.create_index(
        "ix_payout_callback_event_created_at",
        "payout_callback_event",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_payout_callback_event_dispatch_due",
        "payout_callback_event",
        ["dispatch_status", "next_attempt_at"],
        unique=False,
    )

    op.create_table(
        "payout_auth_nonce",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("consumer", sa.String(), nullable=False),
        sa.Column("key_id", sa.String(), nullable=False),
        sa.Column("nonce", sa.String(), nullable=False),
        sa.Column("timestamp", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "consumer",
            "key_id",
            "nonce",
            name="uq_payout_auth_nonce_consumer_key_nonce",
        ),
    )
    op.create_index(
        "ix_payout_auth_nonce_created_at",
        "payout_auth_nonce",
        ["created_at"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_payout_auth_nonce_created_at", table_name="payout_auth_nonce")
    op.drop_table("payout_auth_nonce")
    op.drop_index("ix_payout_callback_event_dispatch_due", table_name="payout_callback_event")
    op.drop_index("ix_payout_callback_event_created_at", table_name="payout_callback_event")
    op.drop_index("ix_payout_callback_event_received_at", table_name="payout_callback_event")
    op.drop_index("ix_payout_callback_event_external_id", table_name="payout_callback_event")
    op.drop_index("ix_payout_callback_event_consumer", table_name="payout_callback_event")
    op.drop_index("ix_payout_callback_event_execution_id", table_name="payout_callback_event")
    op.drop_index(
        "ix_payout_callback_event_payout_execution_id",
        table_name="payout_callback_event",
    )
    op.drop_table("payout_callback_event")
    op.drop_index(
        "ix_payout_execution_resolution_audit_execution",
        table_name="payout_execution_resolution_audit",
    )
    op.drop_index(
        "ix_payout_execution_resolution_audit_external_id",
        table_name="payout_execution_resolution_audit",
    )
    op.drop_index(
        "ix_payout_execution_resolution_audit_consumer",
        table_name="payout_execution_resolution_audit",
    )
    op.drop_index(
        "ix_payout_execution_resolution_audit_execution_id",
        table_name="payout_execution_resolution_audit",
    )
    op.drop_index(
        "ix_payout_execution_resolution_audit_payout_execution_id",
        table_name="payout_execution_resolution_audit",
    )
    op.drop_index(
        "ix_payout_execution_resolution_audit_created_at",
        table_name="payout_execution_resolution_audit",
    )
    op.drop_table("payout_execution_resolution_audit")
    op.drop_index("ix_payout_execution_reconciliation", table_name="payout_execution")
    op.drop_index("ix_payout_execution_state_updated", table_name="payout_execution")
    op.drop_index("ix_payout_execution_state", table_name="payout_execution")
    op.drop_index("ix_payout_execution_created_at", table_name="payout_execution")
    op.drop_table("payout_execution")
    op.drop_index("ix_payout_rail_enabled_lookup", table_name="payout_rail")
    op.drop_index("ix_payout_rail_created_at", table_name="payout_rail")
    op.drop_table("payout_rail")
