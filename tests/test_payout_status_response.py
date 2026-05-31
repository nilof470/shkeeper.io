from decimal import Decimal
import unittest

from flask import Flask

from shkeeper import db
from shkeeper.api_v1 import bp
from shkeeper.models import Payout, PayoutTx, Wallet


class PayoutStatusResponseTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.app.register_blueprint(bp)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        db.session.add(Wallet(crypto="USDT", apikey="api-key"))
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_payout_status_includes_task_error_success_and_txids(self):
        payout = Payout.add(
            {
                "dest": "TA",
                "amount": Decimal("1.25"),
                "txids": ["tx-1", "tx-2"],
            },
            "USDT",
            task_id="task-1",
            external_id="WW-1",
        )
        payout.success = "No"
        payout.error = "waiting for sidecar"
        db.session.commit()

        response = self.client.get(
            "/api/v1/USDT/payout/status?external_id=WW-1",
            headers={"X-Shkeeper-Api-Key": "api-key"},
        )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["task_id"], "task-1")
        self.assertEqual(data["success"], "No")
        self.assertEqual(data["error"], "waiting for sidecar")
        self.assertEqual(data["txids"], ["tx-1", "tx-2"])
        self.assertEqual(data["txid"], "tx-1")
        self.assertFalse(data["reconciliation_required"])

    def test_payout_status_exposes_ambiguous_enqueue_state(self):
        Payout.add(
            {"dest": "TA", "amount": Decimal("1.25")},
            "USDT",
            external_id="WW-1",
        )
        payout = Payout.query.filter_by(external_id="WW-1").one()
        payout.error = "Sidecar enqueue result is unknown: timeout"
        db.session.commit()

        response = self.client.get(
            "/api/v1/USDT/payout/status?external_id=WW-1",
            headers={"X-Shkeeper-Api-Key": "api-key"},
        )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIsNone(data["task_id"])
        self.assertEqual(data["txids"], [])
        self.assertTrue(data["reconciliation_required"])


if __name__ == "__main__":
    unittest.main()
