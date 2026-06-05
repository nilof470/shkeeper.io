from datetime import datetime
from decimal import Decimal
from pathlib import Path
import unittest

from flask import Flask, g

from shkeeper import db
from shkeeper.models import (
    Payout,
    PayoutExecution,
    PayoutExecutionState,
    PayoutStatus,
)
from shkeeper.modules.classes.crypto import Crypto
from shkeeper.utils import format_decimal
from shkeeper.wallet import bp as wallet_bp


class FakeCrypto:
    crypto = "USDT"
    _display_name = "TRC20 USDT"

    def getname(self):
        return "USDT"


class WalletPayoutsPageTestCase(unittest.TestCase):
    def setUp(self):
        template_folder = Path(__file__).resolve().parents[1] / "shkeeper" / "templates"
        self.app = Flask(__name__, template_folder=str(template_folder))
        self.app.secret_key = "test-secret"
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        self.app.jinja_env.filters["format_decimal"] = format_decimal

        @self.app.before_request
        def set_user():
            g.user = object()

        db.init_app(self.app)
        self.app.register_blueprint(wallet_bp)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.client = self.app.test_client()
        self.original_crypto_instances = dict(Crypto.instances)
        Crypto.instances.clear()
        Crypto.instances["USDT"] = FakeCrypto()

    def tearDown(self):
        Crypto.instances.clear()
        Crypto.instances.update(self.original_crypto_instances)
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def make_execution(self, **overrides):
        values = {
            "created_at": datetime(2026, 6, 5, 12, 35),
            "updated_at": datetime(2026, 6, 5, 12, 36),
            "consumer": "grither-pay",
            "external_id": "WD-NEW",
            "contract_version": "usdt-payout-execution-v1",
            "event_version": 1,
            "state_transition_id": "transition-WD-NEW",
            "asset": "USDT",
            "network": "TRON",
            "crypto_id": "USDT",
            "sidecar_service": "tron-shkeeper",
            "sidecar_symbol": "USDT",
            "payout_queue": "tron_usdt_fee_payouts",
            "source_wallet_ref": "fee_deposit",
            "amount": Decimal("1.000000"),
            "destination": "TGxRskKm45DJ7h1ZaEdLswN2C6ncKAQnZJ",
            "request_hash": "request-hash-WD-NEW",
            "sidecar_payload_hash": "sidecar-hash-WD-NEW",
            "state": PayoutExecutionState.CONFIRMED,
            "txids_json": '["tx-new-123"]',
            "message_hashes_json": "[]",
            "reconciliation_required": False,
        }
        values.update(overrides)
        execution = PayoutExecution(**values)
        db.session.add(execution)
        db.session.commit()
        return execution

    def test_payouts_table_includes_payout_execution_rows(self):
        Payout.add(
            {
                "dest": "TLEGACY",
                "amount": Decimal("2.000000"),
                "txids": ["tx-legacy-123"],
            },
            "USDT",
            external_id="LEGACY-1",
        )
        legacy = Payout.query.filter_by(external_id="LEGACY-1").one()
        legacy.created_at = datetime(2026, 6, 1, 10, 6)
        legacy.status = PayoutStatus.SUCCESS
        legacy.success = "Yes"
        self.make_execution()
        db.session.commit()

        response = self.client.get("/parts/payouts")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("WD-NEW", body)
        self.assertIn("tx-new-123", body)
        self.assertIn("TGxRskKm45DJ7h1ZaEdLswN2C6ncKAQnZJ", body)
        self.assertIn("LEGACY-1", body)

    def test_payouts_table_filters_payout_execution_by_txid(self):
        self.make_execution()

        response = self.client.get("/parts/payouts?txid=tx-new")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("WD-NEW", body)
        self.assertIn("tx-new-123", body)

    def test_payouts_table_filters_payout_execution_by_crypto(self):
        self.make_execution()

        response = self.client.get("/parts/payouts?crypto=USDT")

        self.assertEqual(response.status_code, 200)
        self.assertIn("WD-NEW", response.get_data(as_text=True))

    def test_payouts_table_prefers_execution_when_legacy_external_id_overlaps(self):
        Payout.add(
            {
                "dest": "TLEGACY",
                "amount": Decimal("1.000000"),
                "txids": ["tx-legacy-duplicate"],
            },
            "USDT",
            external_id="WD-NEW",
        )
        self.make_execution()

        response = self.client.get("/parts/payouts")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertEqual(body.count("WD-NEW"), 1)
        self.assertIn("tx-new-123", body)
        self.assertNotIn("tx-legacy-duplicate", body)

    def test_payouts_table_paginates_union_rows(self):
        for i in range(51):
            self.make_execution(
                external_id=f"WD-{i:02}",
                state_transition_id=f"transition-WD-{i:02}",
                request_hash=f"request-hash-WD-{i:02}",
                sidecar_payload_hash=f"sidecar-hash-WD-{i:02}",
                created_at=datetime(2026, 6, 5, 12, i),
                updated_at=datetime(2026, 6, 5, 12, i),
                txids_json=f'["tx-{i:02}"]',
            )

        first_page = self.client.get("/parts/payouts?page=1").get_data(as_text=True)
        second_page = self.client.get("/parts/payouts?page=2").get_data(as_text=True)

        self.assertIn("WD-50", first_page)
        self.assertNotIn("WD-00", first_page)
        self.assertIn("WD-00", second_page)

    def test_payouts_csv_includes_execution_external_id_and_success(self):
        self.make_execution()

        response = self.client.get("/parts/payouts?download=csv")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("ExternalId", body)
        self.assertIn("Success", body)
        self.assertIn("WD-NEW", body)
        self.assertIn("Yes", body)
