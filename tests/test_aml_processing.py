import unittest
from datetime import datetime, timedelta
from decimal import Decimal

from flask import Flask

from shkeeper import db
from shkeeper.models import (
    AmlCheck,
    AmlStatus,
    DepositDecision,
    ExchangeRate,
    FeeCalculationPolicy,
    Invoice,
    Transaction,
    Wallet,
)
from shkeeper.services import aml_processing


class AmlProcessingTestCase(unittest.TestCase):
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
        self.original_create_check = aml_processing.AmlShkeeperClient.create_check
        self.original_get_check = aml_processing.AmlShkeeperClient.get_check

    def tearDown(self):
        aml_processing.AmlShkeeperClient.create_check = self.original_create_check
        aml_processing.AmlShkeeperClient.get_check = self.original_get_check
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def make_tx(self, amount_fiat="150", crypto="BTC", txid=None):
        invoice = Invoice(
            external_id="user-1",
            fiat="USD",
            crypto=crypto,
            addr="addr-1",
            amount_fiat=Decimal("1000"),
            amount_crypto=Decimal("1"),
            exchange_rate=Decimal("1000"),
            balance_fiat=Decimal("0"),
            balance_crypto=Decimal("0"),
        )
        db.session.add(invoice)
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

    def test_unsupported_asset_terminal_manual_review(self):
        tx = self.make_tx("150", crypto="BTC-LIGHTNING")
        check = aml_processing.ensure_aml_for_transaction(tx)

        self.assertEqual(check.status, AmlStatus.MANUAL_REVIEW)
        self.assertEqual(check.deposit_decision, DepositDecision.MANUAL_REVIEW)
        self.assertEqual(check.decision_reason, "limited_analysis_requires_review")

    def test_skip_does_not_call_provider(self):
        tx = self.make_tx("50")

        def fail_create(*args, **kwargs):
            raise AssertionError("provider must not be called for skipped deposits")

        aml_processing.AmlShkeeperClient.create_check = fail_create

        check = aml_processing.ensure_aml_for_transaction(tx)

        self.assertEqual(check.status, AmlStatus.SKIPPED)
        self.assertEqual(check.deposit_decision, DepositDecision.CREDIT)

    def test_supported_above_threshold_creates_sidecar_check(self):
        calls = []

        def create_check(client, payload):
            calls.append(payload)
            return {"provider_status": "pending", "status": "pending"}

        aml_processing.AmlShkeeperClient.create_check = create_check
        tx = self.make_tx("150")

        check = aml_processing.ensure_aml_for_transaction(tx)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["deposit_id"], f"shkeeper-tx-{tx.id}")
        self.assertEqual(check.status, AmlStatus.CHECKING)

    def test_pending_check_blocks_callback(self):
        tx = self.make_tx("150")
        check = AmlCheck(
            transaction_id=tx.id,
            deposit_id=f"shkeeper-tx-{tx.id}",
            idempotency_key=f"BTC:{tx.txid}:shkeeper-tx-{tx.id}",
            provider="koinkyt",
            status=AmlStatus.CHECKING,
            provider_status="pending",
        )
        db.session.add(check)
        db.session.commit()

        self.assertFalse(aml_processing.is_callback_allowed(tx))

    def test_timeout_resolves_manual_review(self):
        tx = self.make_tx("150")
        check = AmlCheck(
            transaction_id=tx.id,
            deposit_id=f"shkeeper-tx-{tx.id}",
            idempotency_key=f"BTC:{tx.txid}:shkeeper-tx-{tx.id}",
            provider="koinkyt",
            status=AmlStatus.CHECKING,
            provider_status="pending",
            timeout_at=datetime.utcnow() - timedelta(seconds=1),
        )
        db.session.add(check)
        db.session.commit()

        aml_processing.process_pending_aml_checks()

        self.assertEqual(check.deposit_decision, DepositDecision.MANUAL_REVIEW)
        self.assertEqual(check.decision_reason, "aml_pending_timeout")
        self.assertEqual(check.provider_status, "timeout")

    def test_explicit_amlbot_provider_sends_legacy_asset_to_sidecar(self):
        self.app.config["AML_PROVIDER"] = "amlbot"
        calls = []

        def create_check(client, payload):
            calls.append(payload)
            return {
                "provider": "amlbot",
                "provider_status": "pending",
                "status": "pending",
            }

        aml_processing.AmlShkeeperClient.create_check = create_check
        tx = self.make_tx("150", crypto="DOGE")

        check = aml_processing.ensure_aml_for_transaction(tx)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["crypto"], "DOGE")
        self.assertEqual(check.provider, "amlbot")
        self.assertEqual(check.status, AmlStatus.CHECKING)

    def test_duplicate_ensure_reuses_same_aml_check(self):
        tx = self.make_tx("50")
        first = aml_processing.ensure_aml_for_transaction(tx)
        second = aml_processing.ensure_aml_for_transaction(tx)

        self.assertEqual(first.id, second.id)
        self.assertEqual(AmlCheck.query.count(), 1)


if __name__ == "__main__":
    unittest.main()
