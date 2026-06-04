from decimal import Decimal
from pathlib import Path
import unittest

from flask import Flask
from sqlalchemy import UniqueConstraint
from sqlalchemy.exc import IntegrityError

from shkeeper import db
from shkeeper.models import (
    PayoutCallbackEvent,
    PayoutExecution,
    PayoutExecutionState,
    PayoutRail,
    PayoutRailHotWalletPolicy,
    PayoutRailLegacySpendPolicy,
)


class PayoutExecutionModelTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def make_execution(self, consumer="grither-pay", external_id="WD-1", **overrides):
        values = {
            "consumer": consumer,
            "external_id": external_id,
            "contract_version": "usdt-payout-execution-v1",
            "event_version": 1,
            "state_transition_id": f"transition-{consumer}-{external_id}",
            "asset": "USDT",
            "network": "TRON",
            "crypto_id": "USDT",
            "sidecar_service": "tron-shkeeper",
            "sidecar_symbol": "USDT",
            "payout_queue": "tron_usdt_fee_payouts",
            "source_wallet_ref": "fee_deposit",
            "amount": Decimal("25.000000"),
            "destination": "TDEST",
            "request_hash": f"request-hash-{consumer}-{external_id}",
            "sidecar_payload_hash": f"sidecar-hash-{consumer}-{external_id}",
            "state": PayoutExecutionState.CREATED,
            "txids_json": "[]",
            "message_hashes_json": "[]",
            "reconciliation_required": False,
        }
        values.update(overrides)
        execution = PayoutExecution(**values)
        db.session.add(execution)
        db.session.commit()
        return execution

    def make_rail(self, consumer="grither-pay", asset="USDT", network="TRON"):
        rail = PayoutRail(
            consumer=consumer,
            asset=asset,
            network=network,
            crypto_id="USDT",
            sidecar_service="tron-shkeeper",
            sidecar_symbol="USDT",
            payout_queue="tron_usdt_fee_payouts",
            source_wallet_ref="fee_deposit",
            hot_wallet_policy=(
                PayoutRailHotWalletPolicy.CURRENT_SIDECAR_SOURCE_WALLET
            ),
            legacy_spend_policy=(
                PayoutRailLegacySpendPolicy.BLOCK_AUTOMATIC_BYPASS
            ),
            execution_enabled=True,
            decimals=6,
            callback_endpoint_id="grither-pay-payouts",
            contract_version="usdt-payout-execution-v1",
        )
        db.session.add(rail)
        db.session.commit()
        return rail

    def make_callback_event(
        self,
        execution,
        event_id="event-1",
        event_version=1,
        state_transition_id="callback-transition-1",
    ):
        event = PayoutCallbackEvent(
            event_id=event_id,
            payout_execution_id=execution.id,
            execution_id=execution.id,
            external_id=execution.external_id,
            event_version=event_version,
            state_transition_id=state_transition_id,
            payload_hash=f"payload-hash-{event_id}",
            raw_payload="{}",
            signature_key_id="test-key",
        )
        db.session.add(event)
        db.session.commit()
        return event

    def test_payout_execution_unique_consumer_external_id(self):
        self.make_execution(consumer="grither-pay", external_id="WD-1")

        with self.assertRaises(IntegrityError):
            self.make_execution(
                consumer="grither-pay",
                external_id="WD-1",
                state_transition_id="transition-duplicate",
            )

    def test_payout_execution_allows_same_external_id_for_different_consumer(self):
        self.make_execution(consumer="grither-pay", external_id="WD-1")
        self.make_execution(consumer="other-consumer", external_id="WD-1")

        self.assertEqual(PayoutExecution.query.count(), 2)

    def test_payout_rail_catalog_unique_consumer_asset_network(self):
        self.make_rail(consumer="grither-pay", asset="USDT", network="TRON")

        with self.assertRaises(IntegrityError):
            self.make_rail(consumer="grither-pay", asset="USDT", network="TRON")

    def test_callback_event_unique_execution_event_version_and_transition_id(self):
        execution = self.make_execution()
        self.make_callback_event(
            execution,
            event_id="event-1",
            event_version=1,
            state_transition_id="transition-1",
        )

        with self.assertRaises(IntegrityError):
            self.make_callback_event(
                execution,
                event_id="event-1",
                event_version=2,
                state_transition_id="transition-2",
            )
        db.session.rollback()

        with self.assertRaises(IntegrityError):
            self.make_callback_event(
                execution,
                event_id="event-2",
                event_version=1,
                state_transition_id="transition-2",
            )
        db.session.rollback()

        with self.assertRaises(IntegrityError):
            self.make_callback_event(
                execution,
                event_id="event-3",
                event_version=3,
                state_transition_id="transition-1",
            )

    def test_constraint_names_match_execution_contract(self):
        expected_names = {
            PayoutExecution.__table__: {
                "uq_payout_execution_consumer_external_id",
                "uq_payout_execution_sidecar_execution_id",
                "uq_payout_execution_state_transition_id",
            },
            PayoutRail.__table__: {
                "uq_payout_rail_consumer_asset_network",
            },
            PayoutCallbackEvent.__table__: {
                "uq_payout_callback_event_id",
                "uq_payout_callback_execution_event_version",
                "uq_payout_callback_state_transition_id",
            },
        }

        for table, names in expected_names.items():
            actual = {
                constraint.name
                for constraint in table.constraints
                if isinstance(constraint, UniqueConstraint)
            }
            self.assertTrue(names.issubset(actual), table.name)

    def test_payout_execution_tables_do_not_own_product_policy_columns(self):
        forbidden_column_fragments = {
            "daily",
            "eligibility",
            "limit",
            "max",
            "min",
            "quota",
            "tier",
        }
        for table in (PayoutRail.__table__, PayoutExecution.__table__):
            for column in table.columns:
                normalized = column.name.lower()
                self.assertFalse(
                    forbidden_column_fragments.intersection(normalized.split("_")),
                    f"{table.name}.{column.name}",
                )

    def test_migration_uses_named_payout_constraints(self):
        migration_path = (
            Path(__file__).resolve().parents[1]
            / "migrations/versions/20260603_payout_execution_foundation.py"
        )
        migration = migration_path.read_text()

        for name in (
            "uq_payout_execution_consumer_external_id",
            "uq_payout_execution_sidecar_execution_id",
            "uq_payout_execution_state_transition_id",
            "uq_payout_rail_consumer_asset_network",
            "uq_payout_callback_event_id",
            "uq_payout_callback_execution_event_version",
            "uq_payout_callback_state_transition_id",
        ):
            self.assertIn(name, migration)

        self.assertIn('sa.Column("sidecar_state_updated_at"', migration)
        self.assertIn('sa.Column("last_sidecar_status_hash"', migration)
        self.assertIn('sa.Column("last_sidecar_status_json"', migration)
        self.assertIn('sa.Column("last_sidecar_status_observed_at"', migration)


if __name__ == "__main__":
    unittest.main()
