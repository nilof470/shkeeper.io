import json
import unittest

from flask import Flask

from shkeeper import db
from shkeeper.models import (
    PayoutRail,
    PayoutRailHotWalletPolicy,
    PayoutRailLegacySpendPolicy,
)
from shkeeper.services.payout_rail_sync import (
    PayoutRailSyncError,
    sync_payout_rails,
)


class PayoutRailSyncTestCase(unittest.TestCase):
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

    def rail_payload(self, **overrides):
        payload = {
            "consumer": "grither-pay",
            "asset": "USDT",
            "network": "TRON",
            "crypto_id": "USDT",
            "sidecar_service": "tron-shkeeper",
            "sidecar_symbol": "USDT",
            "payout_queue": "tron_usdt_fee_payouts",
            "source_wallet_ref": "fee_deposit",
            "execution_enabled": True,
            "callback_endpoint_id": "grither-pay-payouts",
            "hot_wallet_policy": "CURRENT_SIDECAR_SOURCE_WALLET",
            "legacy_spend_policy": "BLOCK_AUTOMATIC_BYPASS",
        }
        payload.update(overrides)
        return payload

    def test_sync_creates_enabled_payout_rail(self):
        synced = sync_payout_rails(json.dumps([self.rail_payload()]))

        rail = PayoutRail.query.one()
        self.assertEqual(synced, 1)
        self.assertEqual(rail.consumer, "grither-pay")
        self.assertEqual(rail.asset, "USDT")
        self.assertEqual(rail.network, "TRON")
        self.assertEqual(rail.payout_queue, "tron_usdt_fee_payouts")
        self.assertEqual(rail.source_wallet_ref, "fee_deposit")
        self.assertTrue(rail.execution_enabled)
        self.assertEqual(rail.callback_endpoint_id, "grither-pay-payouts")
        self.assertEqual(
            rail.hot_wallet_policy,
            PayoutRailHotWalletPolicy.CURRENT_SIDECAR_SOURCE_WALLET,
        )
        self.assertEqual(
            rail.legacy_spend_policy,
            PayoutRailLegacySpendPolicy.BLOCK_AUTOMATIC_BYPASS,
        )

    def test_sync_updates_existing_payout_rail(self):
        sync_payout_rails([self.rail_payload()])

        synced = sync_payout_rails(
            {
                "consumer": "grither-pay",
                "rails": [
                    self.rail_payload(
                        payout_queue="tron_usdt_priority_payouts",
                    )
                ]
            }
        )

        rail = PayoutRail.query.one()
        self.assertEqual(synced, 1)
        self.assertEqual(PayoutRail.query.count(), 1)
        self.assertEqual(rail.payout_queue, "tron_usdt_priority_payouts")

    def test_consumer_catalog_disables_stale_enabled_rails(self):
        sync_payout_rails([self.rail_payload(network="TRON")])
        sync_payout_rails([self.rail_payload(network="TON", crypto_id="TON-USDT")])

        synced = sync_payout_rails(
            {
                "consumer": "grither-pay",
                "rails": [self.rail_payload(network="TRON")],
            }
        )

        tron = PayoutRail.query.filter_by(network="TRON").one()
        ton = PayoutRail.query.filter_by(network="TON").one()
        self.assertEqual(synced, 1)
        self.assertTrue(tron.execution_enabled)
        self.assertFalse(ton.execution_enabled)

    def test_empty_consumer_catalog_disables_existing_enabled_rails(self):
        sync_payout_rails([self.rail_payload()])

        synced = sync_payout_rails({"consumer": "grither-pay", "rails": []})

        rail = PayoutRail.query.one()
        self.assertEqual(synced, 0)
        self.assertFalse(rail.execution_enabled)

    def test_consumer_catalog_rejects_mismatched_rail_consumer(self):
        with self.assertRaises(PayoutRailSyncError):
            sync_payout_rails(
                {
                    "consumer": "grither-pay",
                    "rails": [self.rail_payload(consumer="other-consumer")],
                }
            )

        self.assertEqual(PayoutRail.query.count(), 0)

    def test_sync_rejects_duplicate_desired_rail(self):
        with self.assertRaises(PayoutRailSyncError):
            sync_payout_rails(
                {
                    "consumer": "grither-pay",
                    "rails": [
                        self.rail_payload(),
                        self.rail_payload(payout_queue="tron_usdt_priority_payouts"),
                    ],
                }
            )

        self.assertEqual(PayoutRail.query.count(), 0)

    def test_legacy_list_sync_does_not_disable_absent_rails(self):
        sync_payout_rails([self.rail_payload(network="TRON")])
        sync_payout_rails([self.rail_payload(network="TON", crypto_id="TON-USDT")])

        sync_payout_rails([self.rail_payload(network="TRON")])

        ton = PayoutRail.query.filter_by(network="TON").one()
        self.assertTrue(ton.execution_enabled)

    def test_sync_rejects_missing_required_field(self):
        payload = self.rail_payload()
        del payload["source_wallet_ref"]

        with self.assertRaises(PayoutRailSyncError):
            sync_payout_rails([payload])

    def test_sync_rejects_invalid_enum(self):
        with self.assertRaises(PayoutRailSyncError):
            sync_payout_rails(
                [self.rail_payload(hot_wallet_policy="DEDICATED_PAYOUT_WALLET")]
            )

    def test_sync_rejects_invalid_boolean_value(self):
        with self.assertRaises(PayoutRailSyncError):
            sync_payout_rails(
                [self.rail_payload(execution_enabled="enabled")]
            )

    def test_sync_rejects_unknown_fields(self):
        with self.assertRaisesRegex(
            PayoutRailSyncError,
            "unknown fields: unexpected_field",
        ):
            sync_payout_rails(
                [
                    self.rail_payload(
                        unexpected_field="not part of the rail contract",
                    )
                ]
            )

        self.assertEqual(PayoutRail.query.count(), 0)

    def test_sync_rejects_multiple_unknown_fields(self):
        with self.assertRaises(PayoutRailSyncError) as raised:
            sync_payout_rails(
                [
                    self.rail_payload(
                        unsupported_alpha="not part of the rail contract",
                        unsupported_beta="not part of the rail contract",
                    )
                ]
            )

        message = str(raised.exception)
        self.assertIn("unsupported_alpha", message)
        self.assertIn("unsupported_beta", message)
        self.assertEqual(PayoutRail.query.count(), 0)

    def test_enabled_sync_rejects_missing_callback_endpoint(self):
        with self.assertRaises(PayoutRailSyncError):
            sync_payout_rails([self.rail_payload(callback_endpoint_id="")])

    def test_sync_rejects_non_usdt_decimals(self):
        for decimals in (0, 18, "6.5"):
            with self.subTest(decimals=decimals):
                with self.assertRaises(PayoutRailSyncError):
                    sync_payout_rails([self.rail_payload(decimals=decimals)])

    def test_sync_rolls_back_when_later_rail_is_invalid(self):
        invalid = self.rail_payload(network="TON")
        invalid["execution_enabled"] = "enabled"

        with self.assertRaises(PayoutRailSyncError):
            sync_payout_rails([self.rail_payload(), invalid])

        self.assertEqual(PayoutRail.query.count(), 0)


if __name__ == "__main__":
    unittest.main()
