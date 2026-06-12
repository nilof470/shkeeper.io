import json
import unittest
from datetime import datetime
from decimal import Decimal

from flask import Flask

from shkeeper import db
import shkeeper.callback as callback_module
from shkeeper.modules.classes.crypto import Crypto
from shkeeper.models import (
    AmlCheck,
    AmlStatus,
    DepositDecision,
    ExchangeRate,
    FeeCalculationPolicy,
    Invoice,
    InvoiceAddress,
    Transaction,
    Wallet,
)
from shkeeper.services import aml_processing
from shkeeper.services.sweep_eligibility import decide_sweep_eligibility


class AmlEndToEndTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            AML_SHKEEPER_HOST="http://aml-shkeeper",
            AML_SHKEEPER_USERNAME="shkeeper",
            AML_SHKEEPER_PASSWORD="shkeeper",
            REQUESTS_TIMEOUT=1,
            REQUESTS_NOTIFICATION_TIMEOUT=1,
            AML_MAX_ACCEPT_SCORE="0.10",
            AML_MIN_CHECK_AMOUNT_FIAT="100",
            AML_SKIP_CUMULATIVE_LIMIT_FIAT="300",
            AML_SKIP_CUMULATIVE_WINDOW_HOURS=24,
            AML_PENDING_TIMEOUT_SECONDS=1800,
            AML_RETRY_DELAY_SECONDS=120,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        db.session.add(Wallet(crypto="BTC", apikey="api-key", llimit=95, ulimit=105))
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
        import shkeeper.api_v1 as api_v1_module

        self.app.register_blueprint(api_v1_module.bp)
        Crypto.instances["BTC"] = type(
            "FakeCrypto",
            (),
            {"precision": 8, "wallet": type("FakeWallet", (), {"apikey": "api-key"})()},
        )()
        self.original_create_check = aml_processing.AmlShkeeperClient.create_check
        self.original_get_check = aml_processing.AmlShkeeperClient.get_check
        self.original_post = callback_module.requests.post

    def tearDown(self):
        aml_processing.AmlShkeeperClient.create_check = self.original_create_check
        aml_processing.AmlShkeeperClient.get_check = self.original_get_check
        callback_module.requests.post = self.original_post
        Crypto.instances.clear()
        Crypto.instances.update(self.original_crypto_instances)
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def make_tx(self, amount_fiat="150", crypto="BTC", txid=None):
        invoice = Invoice(
            external_id="user-1",
            fiat="USD",
            crypto=crypto,
            addr="addr-1",
            callback_url="http://callback.local",
            amount_fiat=Decimal("1000"),
            amount_crypto=Decimal("1"),
            exchange_rate=Decimal("1000"),
            balance_fiat=Decimal(str(amount_fiat)),
            balance_crypto=Decimal("0.1"),
        )
        db.session.add(invoice)
        db.session.commit()
        db.session.add(
            InvoiceAddress(invoice_id=invoice.id, crypto=crypto, addr=invoice.addr)
        )
        db.session.commit()
        tx = Transaction(
            invoice_id=invoice.id,
            txid=txid or f"tx-{crypto}-{datetime.utcnow().timestamp()}",
            crypto=crypto,
            amount_crypto=Decimal("0.1"),
            amount_fiat=Decimal(str(amount_fiat)),
            need_more_confirmations=False,
        )
        db.session.add(tx)
        db.session.commit()
        return tx

    def response(self, code):
        return type("Response", (), {"status_code": code, "reason": "OK"})()

    def test_pending_aml_blocks_final_callback(self):
        tx = self.make_tx("150")
        aml_processing.AmlShkeeperClient.create_check = lambda client, payload: {
            "provider_status": "pending",
            "status": "pending",
        }
        posted = []
        callback_module.requests.post = lambda *args, **kwargs: posted.append(kwargs)

        aml_processing.ensure_aml_for_transaction(tx)
        sent = callback_module.send_notification(tx)

        self.assertFalse(sent)
        self.assertEqual(posted, [])
        self.assertFalse(tx.callback_confirmed)

    def test_provider_success_credits_and_sends_after_accepted_callback(self):
        tx = self.make_tx("150")
        check = AmlCheck(
            transaction_id=tx.id,
            deposit_id=f"shkeeper-tx-{tx.id}",
            idempotency_key=f"BTC:{tx.txid}:shkeeper-tx-{tx.id}",
            provider="koinkyt",
            status=AmlStatus.CHECKING,
            provider_status="pending",
            create_check_submitted=True,
        )
        db.session.add(check)
        db.session.commit()
        aml_processing.AmlShkeeperClient.get_check = lambda client, deposit_id: {
            "provider_status": "success",
            "status": "ready",
            "score": "0.04",
            "uid": "koinkyt-check-id",
        }
        captured = {}
        callback_module.requests.post = lambda *args, **kwargs: captured.update(
            json.loads(kwargs["data"].decode())
        ) or self.response(202)

        aml_processing.process_pending_aml_checks()
        self.assertTrue(callback_module.send_notification(tx))

        self.assertTrue(tx.callback_confirmed)
        self.assertNotIn("deposit_decision", captured["transactions"][0])
        self.assertNotIn("decision_reason", captured["transactions"][0])
        self.assertEqual(captured["transactions"][0]["aml"]["checked"], True)
        self.assertEqual(captured["transactions"][0]["aml"]["score"], "0.04")

    def test_score_above_threshold_manual_review(self):
        tx = self.make_tx("150")
        check = AmlCheck(
            transaction_id=tx.id,
            deposit_id=f"shkeeper-tx-{tx.id}",
            idempotency_key=f"BTC:{tx.txid}:shkeeper-tx-{tx.id}",
            provider="koinkyt",
            status=AmlStatus.CHECKING,
            provider_status="pending",
            create_check_submitted=True,
        )
        db.session.add(check)
        db.session.commit()
        aml_processing.AmlShkeeperClient.get_check = lambda client, deposit_id: {
            "provider_status": "success",
            "status": "ready",
            "score": "0.72",
        }

        aml_processing.process_pending_aml_checks()

        self.assertEqual(check.deposit_decision, DepositDecision.MANUAL_REVIEW)
        self.assertEqual(check.decision_reason, "risk_score_above_threshold")

    def test_below_threshold_deposit_is_skipped_with_null_score(self):
        tx = self.make_tx("50")
        check = aml_processing.ensure_aml_for_transaction(tx)

        self.assertEqual(check.status, AmlStatus.SKIPPED)
        self.assertIsNone(check.score)

    def test_repeated_small_deposits_exceeding_cumulative_limit_call_sidecar(self):
        for amount in ("90", "95", "90"):
            aml_processing.ensure_aml_for_transaction(self.make_tx(amount))

        calls = []
        aml_processing.AmlShkeeperClient.create_check = lambda client, payload: calls.append(
            payload
        ) or {"provider_status": "pending", "status": "pending"}

        aml_processing.ensure_aml_for_transaction(self.make_tx("50"))

        self.assertEqual(len(calls), 1)

    def test_unsupported_crypto_bypasses_aml_and_allows_callback(self):
        tx = self.make_tx("150", crypto="BNB")
        check = aml_processing.ensure_aml_for_transaction(tx)

        self.assertIsNone(check)
        self.assertTrue(aml_processing.is_callback_allowed(tx))

    def test_unsupported_ton_usdt_bypasses_aml_and_allows_live_sweep(self):
        tx = self.make_tx("150", crypto="TON-USDT", txid="ton-usdt-unsupported-tx")

        check = aml_processing.ensure_aml_for_transaction(tx)
        sweep = decide_sweep_eligibility(
            "TON-USDT",
            "TON",
            tx.addr,
            txid="ton-usdt-unsupported-tx",
        )

        self.assertIsNone(check)
        self.assertTrue(aml_processing.is_callback_allowed(tx))
        self.assertEqual(sweep["decision"], "allow")
        self.assertEqual(sweep["reason"], "legacy_no_guarded_deposits")

    def test_replayed_walletnotify_reuses_aml_check(self):
        tx = self.make_tx("50", txid="same-tx")
        first = aml_processing.ensure_aml_for_transaction(tx)
        second = aml_processing.ensure_aml_for_transaction(tx)

        self.assertEqual(first.id, second.id)
        self.assertEqual(AmlCheck.query.count(), 1)

    def test_replayed_walletnotify_creates_missing_aml_check_for_existing_tx(self):
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
        )
        db.session.add(invoice)
        db.session.commit()
        db.session.add(InvoiceAddress(invoice_id=invoice.id, crypto="BTC", addr="addr-1"))
        db.session.commit()
        tx = Transaction(
            invoice_id=invoice.id,
            txid="same-walletnotify-tx",
            crypto="BTC",
            amount_crypto=Decimal("0.15"),
            amount_fiat=Decimal("150"),
            need_more_confirmations=False,
        )
        db.session.add(tx)
        db.session.commit()
        Crypto.instances["BTC"] = type(
            "FakeCrypto",
            (),
            {
                "crypto": "BTC",
                "precision": 8,
                "wallet": type(
                    "FakeWallet",
                    (),
                    {"apikey": "api-key", "confirmations": 1},
                )(),
                "getaddrbytx": lambda self, txid: [
                    ("addr-1", Decimal("0.15"), 1, "receive")
                ],
            },
        )()
        calls = []
        aml_processing.AmlShkeeperClient.create_check = lambda client, payload: calls.append(
            payload
        ) or {"provider_status": "pending", "status": "pending"}
        client = self.app.test_client()

        response = client.post(
            "/api/v1/walletnotify/BTC/same-walletnotify-tx",
            headers={"X-Shkeeper-Backend-Key": "shkeeper"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(calls), 1)
        self.assertEqual(AmlCheck.query.count(), 1)


if __name__ == "__main__":
    unittest.main()
