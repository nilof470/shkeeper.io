import json
import unittest
from decimal import Decimal

from flask import Flask

from shkeeper import db
from shkeeper.callback import build_payment_notification, send_unconfirmed_notification
from shkeeper.modules.classes.crypto import Crypto
from shkeeper.models import (
    AmlCheck,
    AmlStatus,
    DepositDecision,
    ExchangeRate,
    FeeCalculationPolicy,
    Invoice,
    InvoiceAddress,
    InvoiceStatus,
    Transaction,
    UnconfirmedTransaction,
    Wallet,
)


class AmlCallbackPayloadTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            REQUESTS_NOTIFICATION_TIMEOUT=1,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        db.session.add(
            Wallet(
                crypto="BTC",
                apikey="api-key",
                llimit=Decimal("95"),
                ulimit=Decimal("105"),
            )
        )
        db.session.add(
            ExchangeRate(
                crypto="BTC",
                fiat="USD",
                rate=Decimal("1000"),
                fee=Decimal("0"),
                fixed_fee=Decimal("0"),
                fee_policy=FeeCalculationPolicy.PERCENT_FEE,
            )
        )
        db.session.commit()
        self.original_crypto_instances = dict(Crypto.instances)
        Crypto.instances["BTC"] = type(
            "FakeCrypto",
            (),
            {"precision": 8, "wallet": type("FakeWallet", (), {"apikey": "api-key"})()},
        )()

    def tearDown(self):
        Crypto.instances.clear()
        Crypto.instances.update(self.original_crypto_instances)
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def make_tx(self, status=InvoiceStatus.PARTIAL):
        invoice = Invoice(
            external_id="user-1",
            fiat="USD",
            crypto="BTC",
            addr="addr-1",
            callback_url="http://callback.local",
            amount_fiat=Decimal("1000"),
            amount_crypto=Decimal("1"),
            exchange_rate=Decimal("1000"),
            balance_fiat=Decimal("150"),
            balance_crypto=Decimal("0.15"),
            status=status,
        )
        db.session.add(invoice)
        db.session.commit()
        db.session.add(
            InvoiceAddress(invoice_id=invoice.id, crypto="BTC", addr=invoice.addr)
        )
        db.session.commit()
        tx = Transaction(
            invoice_id=invoice.id,
            txid="tx-1",
            crypto="BTC",
            amount_crypto=Decimal("0.15"),
            amount_fiat=Decimal("150"),
            need_more_confirmations=False,
        )
        db.session.add(tx)
        db.session.commit()
        return tx

    def add_check(self, tx, decision="credit", reason="score_below_threshold"):
        check = AmlCheck(
            transaction_id=tx.id,
            deposit_id=f"shkeeper-tx-{tx.id}",
            idempotency_key=f"BTC:{tx.txid}:shkeeper-tx-{tx.id}",
            provider="koinkyt",
            provider_status="success",
            status=AmlStatus.APPROVED
            if decision == "credit"
            else AmlStatus.MANUAL_REVIEW,
            deposit_decision=decision,
            decision_reason=reason,
            score=Decimal("0.04") if decision == "credit" else Decimal("0.72"),
            threshold=Decimal("0.10"),
            uid="koinkyt-check-id",
            asset="BTC",
            network="BTC",
            signals_json='{"mixer": 0.01}',
        )
        db.session.add(check)
        db.session.commit()
        return check

    def test_approved_callback_contains_aml_data_without_business_decision(self):
        tx = self.make_tx()
        self.add_check(tx)

        payload = build_payment_notification(tx)
        trigger = payload["transactions"][0]

        self.assertNotIn("deposit_decision", trigger)
        self.assertNotIn("decision_reason", trigger)
        self.assertEqual(trigger["aml"]["supported"], True)
        self.assertEqual(trigger["aml"]["checked"], True)
        self.assertEqual(trigger["aml"]["check_status"], "success")
        self.assertIsNone(trigger["aml"]["reason_code"])
        self.assertEqual(trigger["aml"]["provider"], "koinkyt")
        self.assertEqual(trigger["aml"]["score"], "0.04")
        self.assertEqual(trigger["aml"]["signals"], {"mixer": 0.01})
        self.assertNotIn("status", trigger["aml"])
        self.assertNotIn("threshold", trigger["aml"])

    def test_skipped_callback_contains_unchecked_aml_without_business_metadata(self):
        tx = self.make_tx()
        check = self.add_check(tx)
        check.status = AmlStatus.SKIPPED
        check.provider_status = None
        check.score = None
        check.decision_reason = "amount_below_aml_threshold"
        check.skip_reason = "amount_below_threshold"
        check.min_check_amount_fiat = Decimal("100")
        check.cumulative_window = "24h"
        check.cumulative_amount_fiat = Decimal("50")
        check.cumulative_limit_fiat = Decimal("300")
        db.session.commit()

        trigger = build_payment_notification(tx)["transactions"][0]

        self.assertEqual(trigger["aml"]["supported"], True)
        self.assertEqual(trigger["aml"]["checked"], False)
        self.assertEqual(trigger["aml"]["check_status"], "skipped")
        self.assertEqual(
            trigger["aml"]["reason_code"], "amount_below_shkeeper_threshold"
        )
        self.assertIsNone(trigger["aml"]["score"])
        self.assertNotIn("cumulative_limit_fiat", trigger["aml"])
        self.assertEqual(trigger["aml"]["policy"]["min_check_amount_fiat"], "100")
        self.assertEqual(trigger["aml"]["policy"]["cumulative_limit_fiat"], "300")
        self.assertEqual(trigger["aml"]["policy"]["cumulative_window"], "24h")
        self.assertNotIn("deposit_decision", trigger)
        self.assertNotIn("decision_reason", trigger)

    def test_manual_review_callback_contains_aml_data_without_business_decision(self):
        tx = self.make_tx()
        self.add_check(
            tx,
            decision=DepositDecision.MANUAL_REVIEW,
            reason="risk_score_above_threshold",
        )

        trigger = build_payment_notification(tx)["transactions"][0]

        self.assertNotIn("deposit_decision", trigger)
        self.assertNotIn("decision_reason", trigger)
        self.assertEqual(trigger["aml"]["supported"], True)
        self.assertEqual(trigger["aml"]["checked"], True)
        self.assertEqual(trigger["aml"]["check_status"], "success")
        self.assertEqual(trigger["aml"]["score"], "0.72")

    def test_unsupported_callback_contains_unchecked_aml_payload(self):
        tx = self.make_tx()
        db.session.add(
            ExchangeRate(
                crypto="BNB",
                fiat="USD",
                rate=Decimal("1000"),
                fee=Decimal("0"),
                fixed_fee=Decimal("0"),
                fee_policy=FeeCalculationPolicy.PERCENT_FEE,
            )
        )
        tx.crypto = "BNB"
        db.session.commit()

        trigger = build_payment_notification(tx)["transactions"][0]

        self.assertEqual(trigger["aml"]["supported"], False)
        self.assertEqual(trigger["aml"]["checked"], False)
        self.assertEqual(trigger["aml"]["check_status"], "unsupported")
        self.assertEqual(trigger["aml"]["reason_code"], "unsupported_asset")
        self.assertEqual(trigger["aml"]["provider_status"], "unsupported")
        self.assertEqual(trigger["aml"]["error_code"], "unsupported_asset")
        self.assertNotIn("deposit_decision", trigger)
        self.assertNotIn("decision_reason", trigger)

    def test_terminal_provider_error_is_not_reported_as_checking(self):
        tx = self.make_tx()
        check = self.add_check(
            tx,
            decision=DepositDecision.MANUAL_REVIEW,
            reason="aml_provider_error",
        )
        check.provider_status = "checking"
        check.score = None
        check.error_code = "http_429"
        db.session.commit()

        trigger = build_payment_notification(tx)["transactions"][0]

        self.assertEqual(trigger["aml"]["checked"], False)
        self.assertEqual(trigger["aml"]["check_status"], "error")
        self.assertEqual(trigger["aml"]["reason_code"], "aml_provider_error")
        self.assertEqual(trigger["aml"]["provider_status"], "checking")
        self.assertEqual(trigger["aml"]["error_code"], "http_429")

    def test_terminal_timeout_is_not_reported_as_pending(self):
        tx = self.make_tx()
        check = self.add_check(
            tx,
            decision=DepositDecision.MANUAL_REVIEW,
            reason="aml_pending_timeout",
        )
        check.provider_status = "pending"
        check.score = None
        check.error_code = "aml_pending_timeout"
        db.session.commit()

        trigger = build_payment_notification(tx)["transactions"][0]

        self.assertEqual(trigger["aml"]["checked"], False)
        self.assertEqual(trigger["aml"]["check_status"], "timeout")
        self.assertEqual(trigger["aml"]["reason_code"], "aml_pending_timeout")
        self.assertEqual(trigger["aml"]["provider_status"], "pending")
        self.assertEqual(trigger["aml"]["error_code"], "aml_pending_timeout")

    def test_incomplete_result_is_not_reported_as_checking(self):
        tx = self.make_tx()
        check = self.add_check(
            tx,
            decision=DepositDecision.MANUAL_REVIEW,
            reason="incomplete_aml_result",
        )
        check.provider_status = "checking"
        check.score = None
        check.error_code = "missing_risk_score"
        db.session.commit()

        trigger = build_payment_notification(tx)["transactions"][0]

        self.assertEqual(trigger["aml"]["checked"], False)
        self.assertEqual(trigger["aml"]["check_status"], "incomplete")
        self.assertEqual(trigger["aml"]["reason_code"], "incomplete_aml_result")
        self.assertEqual(trigger["aml"]["provider_status"], "checking")
        self.assertEqual(trigger["aml"]["error_code"], "missing_risk_score")

    def test_static_address_partial_invoice_can_credit_trigger_transaction(self):
        tx = self.make_tx(status=InvoiceStatus.PARTIAL)
        self.add_check(tx)

        payload = build_payment_notification(tx)

        self.assertFalse(payload["paid"])
        self.assertEqual(payload["status"], "PARTIAL")
        self.assertNotIn("deposit_decision", payload["transactions"][0])
        self.assertEqual(payload["transactions"][0]["aml"]["score"], "0.04")

    def test_retry_payload_is_stable(self):
        tx = self.make_tx()
        self.add_check(tx)

        first = build_payment_notification(tx)
        second = build_payment_notification(tx)

        self.assertEqual(first, second)

    def test_unconfirmed_callback_has_no_aml_decision_fields(self):
        tx = self.make_tx()
        utx = UnconfirmedTransaction(
            invoice_id=tx.invoice_id,
            addr="addr-1",
            txid="unconfirmed-tx",
            crypto="BTC",
            amount_crypto=Decimal("0.01"),
        )
        db.session.add(utx)
        db.session.commit()
        captured = {}

        class Response:
            status_code = 202

        import shkeeper.callback as callback_module

        original_post = callback_module.requests.post

        def capture_post(*args, **kwargs):
            captured.update(json.loads(kwargs["data"].decode()))
            return Response()

        callback_module.requests.post = capture_post
        try:
            self.assertTrue(send_unconfirmed_notification(utx))
        finally:
            callback_module.requests.post = original_post

        self.assertNotIn("deposit_decision", captured)
        self.assertNotIn("decision_reason", captured)
        self.assertNotIn("aml", captured)


if __name__ == "__main__":
    unittest.main()
