import unittest
from datetime import datetime
from decimal import Decimal

from flask import Flask

from shkeeper import db
from shkeeper.models import DepositDecision, Invoice, Transaction
from shkeeper.services.aml_policy import (
    build_skipped_check,
    decision_from_provider_result,
    should_skip_aml,
)


class AmlPolicyTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            AML_MAX_ACCEPT_SCORE="0.10",
            AML_MIN_CHECK_AMOUNT_FIAT="100",
            AML_SKIP_CUMULATIVE_LIMIT_FIAT="300",
            AML_SKIP_CUMULATIVE_WINDOW_HOURS=24,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def make_tx(self, amount_fiat, crypto="BTC", external_id="user-1", addr="addr-1"):
        invoice = Invoice(
            external_id=external_id,
            fiat="USD",
            crypto=crypto,
            addr=addr,
            amount_fiat=Decimal("1000"),
            amount_crypto=Decimal("1"),
            exchange_rate=Decimal("1000"),
        )
        db.session.add(invoice)
        db.session.commit()
        tx = Transaction(
            invoice_id=invoice.id,
            txid=f"tx-{crypto}-{amount_fiat}-{datetime.utcnow().timestamp()}",
            crypto=crypto,
            amount_crypto=Decimal("0.1"),
            amount_fiat=Decimal(str(amount_fiat)),
            need_more_confirmations=False,
        )
        db.session.add(tx)
        db.session.commit()
        return tx

    def persist_skipped(self, amount_fiat):
        tx = self.make_tx(amount_fiat)
        check = build_skipped_check(tx)
        db.session.add(check)
        db.session.commit()
        return check

    def test_skip_under_threshold(self):
        tx = self.make_tx("50")
        self.assertTrue(should_skip_aml(tx))

    def test_repeated_skipped_deposits_under_cumulative_limit(self):
        self.persist_skipped("80")
        self.persist_skipped("90")
        tx = self.make_tx("70")
        self.assertTrue(should_skip_aml(tx))

    def test_cumulative_limit_exceeded_requires_check(self):
        self.persist_skipped("90")
        self.persist_skipped("95")
        self.persist_skipped("90")
        tx = self.make_tx("50")
        self.assertFalse(should_skip_aml(tx))

    def test_skipped_check_score_is_none(self):
        tx = self.make_tx("25")
        check = build_skipped_check(tx)
        self.assertIsNone(check.score)
        self.assertEqual(check.deposit_decision, DepositDecision.CREDIT)
        self.assertEqual(check.decision_reason, "amount_below_aml_threshold")

    def test_score_equal_threshold_credits(self):
        tx = self.make_tx("150")
        check = decision_from_provider_result(
            tx, {"provider_status": "success", "score": "0.10"}
        )
        self.assertEqual(check.deposit_decision, "credit")
        self.assertEqual(check.decision_reason, "score_below_threshold")

    def test_score_above_threshold_manual_review(self):
        tx = self.make_tx("150")
        check = decision_from_provider_result(
            tx, {"provider_status": "success", "score": "0.72"}
        )
        self.assertEqual(check.deposit_decision, "manual_review")
        self.assertEqual(check.decision_reason, "risk_score_above_threshold")

    def test_missing_score_manual_review(self):
        tx = self.make_tx("150")
        check = decision_from_provider_result(tx, {"provider_status": "success"})
        self.assertEqual(check.deposit_decision, "manual_review")
        self.assertEqual(check.decision_reason, "incomplete_aml_result")

    def test_unsupported_asset_manual_review(self):
        tx = self.make_tx("150", crypto="BTC-LIGHTNING")
        check = decision_from_provider_result(
            tx, {"provider_status": "success", "score": "0.01"}
        )
        self.assertEqual(check.deposit_decision, "manual_review")
        self.assertEqual(check.decision_reason, "limited_analysis_requires_review")


if __name__ == "__main__":
    unittest.main()
