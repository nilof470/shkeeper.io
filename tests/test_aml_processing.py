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
        db.session.add_all(
            [
                Wallet(
                    crypto="BTC",
                    apikey="api-key",
                    llimit=Decimal("95"),
                    ulimit=Decimal("105"),
                ),
                Wallet(
                    crypto="USDT",
                    apikey="api-key",
                    llimit=Decimal("95"),
                    ulimit=Decimal("105"),
                ),
                Wallet(
                    crypto="ETH-USDT",
                    apikey="api-key",
                    llimit=Decimal("95"),
                    ulimit=Decimal("105"),
                ),
                Wallet(
                    crypto="BNB-USDT",
                    apikey="api-key",
                    llimit=Decimal("95"),
                    ulimit=Decimal("105"),
                ),
                Wallet(
                    crypto="TON-USDT",
                    apikey="api-key",
                    llimit=Decimal("95"),
                    ulimit=Decimal("105"),
                ),
            ]
        )
        db.session.add_all(
            [
                ExchangeRate(
                    crypto="BTC",
                    fiat="USD",
                    rate=Decimal("1000"),
                    fee=Decimal("0"),
                    fixed_fee=Decimal("0"),
                    fee_policy=FeeCalculationPolicy.PERCENT_FEE,
                ),
                ExchangeRate(
                    crypto="USDT",
                    fiat="USD",
                    rate=Decimal("1"),
                    fee=Decimal("0"),
                    fixed_fee=Decimal("0"),
                    fee_policy=FeeCalculationPolicy.PERCENT_FEE,
                ),
                ExchangeRate(
                    crypto="ETH-USDT",
                    fiat="USD",
                    rate=Decimal("1"),
                    fee=Decimal("0"),
                    fixed_fee=Decimal("0"),
                    fee_policy=FeeCalculationPolicy.PERCENT_FEE,
                ),
                ExchangeRate(
                    crypto="BNB-USDT",
                    fiat="USD",
                    rate=Decimal("1"),
                    fee=Decimal("0"),
                    fixed_fee=Decimal("0"),
                    fee_policy=FeeCalculationPolicy.PERCENT_FEE,
                ),
                ExchangeRate(
                    crypto="TON-USDT",
                    fiat="USD",
                    rate=Decimal("1"),
                    fee=Decimal("0"),
                    fixed_fee=Decimal("0"),
                    fee_policy=FeeCalculationPolicy.PERCENT_FEE,
                ),
            ]
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

    def stub_sidecar_pending_result(self):
        def create_check(client, payload):
            return {"provider_status": "pending", "status": "pending"}

        aml_processing.AmlShkeeperClient.create_check = create_check

    def test_default_threshold_sent_to_sidecar_is_zero_point_thirty_when_not_configured(self):
        self.app.config.pop("AML_MAX_ACCEPT_SCORE", None)
        payloads = []

        def create_check(client, payload):
            payloads.append(payload)
            return {"provider_status": "pending", "status": "pending"}

        aml_processing.AmlShkeeperClient.create_check = create_check
        tx = self.make_tx("150", crypto="USDT")

        check = aml_processing.ensure_aml_for_transaction(tx)

        self.assertEqual(Decimal(payloads[0]["threshold"]), Decimal("0.30"))
        self.assertEqual(check.threshold, Decimal("0.30"))

    def test_unsupported_asset_bypasses_aml_check(self):
        tx = self.make_tx("150", crypto="BTC-LIGHTNING")
        check = aml_processing.ensure_aml_for_transaction(tx)

        self.assertIsNone(check)
        self.assertTrue(aml_processing.is_callback_allowed(tx))
        self.assertEqual(AmlCheck.query.count(), 0)

    def test_skip_does_not_call_provider(self):
        tx = self.make_tx("50")

        def fail_create(*args, **kwargs):
            raise AssertionError("provider must not be called for skipped deposits")

        aml_processing.AmlShkeeperClient.create_check = fail_create

        check = aml_processing.ensure_aml_for_transaction(tx)

        self.assertEqual(check.status, AmlStatus.SKIPPED)
        self.assertEqual(check.deposit_decision, DepositDecision.CREDIT)

    def test_new_tron_usdt_above_skip_threshold_sets_sweep_guard_required(self):
        self.stub_sidecar_pending_result()
        tx = self.make_tx("150", crypto="USDT")
        check = aml_processing.ensure_aml_for_transaction(tx)
        self.assertTrue(check.sweep_guard_required)

    def test_new_eth_usdt_above_skip_threshold_sets_sweep_guard_required(self):
        self.stub_sidecar_pending_result()
        tx = self.make_tx("150", crypto="ETH-USDT")
        check = aml_processing.ensure_aml_for_transaction(tx)
        self.assertTrue(check.sweep_guard_required)

    def test_unsupported_ton_usdt_bypasses_aml_check_until_provider_coverage_exists(self):
        def fail_create(*args, **kwargs):
            raise AssertionError("unsupported TON-USDT must not call AML sidecar")

        aml_processing.AmlShkeeperClient.create_check = fail_create
        tx = self.make_tx("150", crypto="TON-USDT")

        check = aml_processing.ensure_aml_for_transaction(tx)

        self.assertIsNone(check)
        self.assertTrue(aml_processing.is_callback_allowed(tx))

    def test_unsupported_bep20_usdt_bypasses_aml_check_until_provider_coverage_exists(self):
        tx = self.make_tx("150", crypto="BNB-USDT")

        check = aml_processing.ensure_aml_for_transaction(tx)

        self.assertIsNone(check)
        self.assertTrue(aml_processing.is_callback_allowed(tx))

    def test_new_tron_usdt_skipped_small_amount_does_not_need_sweep_guard(self):
        tx = self.make_tx("50", crypto="USDT")
        check = aml_processing.ensure_aml_for_transaction(tx)
        self.assertEqual(check.status, AmlStatus.SKIPPED)
        self.assertFalse(check.sweep_guard_required)

    def test_legacy_or_non_guarded_check_defaults_to_not_guarded(self):
        self.stub_sidecar_pending_result()
        tx = self.make_tx("150", crypto="BTC")
        check = aml_processing.ensure_aml_for_transaction(tx)
        self.assertFalse(check.sweep_guard_required)

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
        self.assertTrue(check.create_check_submitted)

    def test_sidecar_auth_error_remains_retryable_until_timeout(self):
        def create_check(client, payload):
            return {
                "provider_status": "error",
                "error_source": "aml-shkeeper",
                "error_code": "http_401",
                "error_message": '{"msg":"authorization requred","status":"error"}',
            }

        aml_processing.AmlShkeeperClient.create_check = create_check
        tx = self.make_tx("150")

        check = aml_processing.ensure_aml_for_transaction(tx)

        self.assertEqual(check.status, AmlStatus.CHECKING)
        self.assertEqual(check.provider_status, "checking")
        self.assertIsNone(check.deposit_decision)
        self.assertIsNone(check.decision_reason)
        self.assertEqual(check.error_code, "http_401")
        self.assertIsNotNone(check.next_retry_at)
        self.assertFalse(check.create_check_submitted)
        self.assertFalse(aml_processing.is_callback_allowed(tx))

    def test_returned_sidecar_transport_error_retries_create_before_polling(self):
        calls = []

        def create_check(client, payload):
            calls.append(("create", payload["deposit_id"]))
            if len(calls) == 1:
                return {
                    "provider_status": "error",
                    "error_source": "aml-shkeeper",
                    "error_code": "transport_error",
                    "error_message": "connection timeout",
                }
            return {
                "provider_status": "success",
                "status": "ready",
                "score": "0.04",
                "uid": "koinkyt-check-id",
            }

        def get_check(client, deposit_id):
            calls.append(("get", deposit_id))
            return {
                "provider_status": "error",
                "status": "failed",
                "error_code": "not_found",
                "error_message": "deposit does not exist",
            }

        aml_processing.AmlShkeeperClient.create_check = create_check
        aml_processing.AmlShkeeperClient.get_check = get_check
        tx = self.make_tx("150")

        initial = aml_processing.ensure_aml_for_transaction(tx)
        self.assertFalse(initial.create_check_submitted)
        refreshed = aml_processing.refresh_aml_check(initial)

        self.assertEqual(
            calls,
            [
                ("create", f"shkeeper-tx-{tx.id}"),
                ("create", f"shkeeper-tx-{tx.id}"),
            ],
        )
        self.assertEqual(refreshed.status, AmlStatus.APPROVED)
        self.assertEqual(refreshed.deposit_decision, DepositDecision.CREDIT)

    def test_sidecar_create_exception_remains_retryable_until_timeout(self):
        def create_check(client, payload):
            raise RuntimeError("connection reset by peer")

        aml_processing.AmlShkeeperClient.create_check = create_check
        tx = self.make_tx("150")

        check = aml_processing.ensure_aml_for_transaction(tx)

        self.assertEqual(check.status, AmlStatus.CHECKING)
        self.assertEqual(check.provider_status, "checking")
        self.assertIsNone(check.deposit_decision)
        self.assertIsNone(check.decision_reason)
        self.assertEqual(check.error_code, "aml_shkeeper_exception")
        self.assertIn("connection reset by peer", check.error_message)
        self.assertIsNotNone(check.next_retry_at)
        self.assertFalse(check.create_check_submitted)
        self.assertFalse(aml_processing.is_callback_allowed(tx))

    def test_create_exception_retries_create_before_polling_missing_sidecar_check(self):
        calls = []

        def create_check(client, payload):
            calls.append(("create", payload["deposit_id"]))
            if len(calls) == 1:
                raise RuntimeError("connection reset by peer")
            return {
                "provider_status": "success",
                "status": "ready",
                "score": "0.04",
                "uid": "koinkyt-check-id",
            }

        def get_check(client, deposit_id):
            calls.append(("get", deposit_id))
            return {
                "provider_status": "error",
                "status": "failed",
                "error_code": "not_found",
                "error_message": "deposit does not exist",
            }

        aml_processing.AmlShkeeperClient.create_check = create_check
        aml_processing.AmlShkeeperClient.get_check = get_check
        tx = self.make_tx("150")

        initial = aml_processing.ensure_aml_for_transaction(tx)
        refreshed = aml_processing.refresh_aml_check(initial)

        self.assertEqual(
            calls,
            [
                ("create", f"shkeeper-tx-{tx.id}"),
                ("create", f"shkeeper-tx-{tx.id}"),
            ],
        )
        self.assertEqual(refreshed.status, AmlStatus.APPROVED)
        self.assertEqual(refreshed.deposit_decision, DepositDecision.CREDIT)

    def test_polling_exception_remains_retryable_until_timeout(self):
        def get_check(client, deposit_id):
            raise RuntimeError("connection reset by peer")

        aml_processing.AmlShkeeperClient.get_check = get_check
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

        refreshed = aml_processing.refresh_aml_check(check)

        self.assertEqual(refreshed.status, AmlStatus.CHECKING)
        self.assertEqual(refreshed.provider_status, "checking")
        self.assertIsNone(refreshed.deposit_decision)
        self.assertEqual(refreshed.error_code, "aml_shkeeper_exception")
        self.assertIn("connection reset by peer", refreshed.error_message)
        self.assertIsNotNone(refreshed.next_retry_at)

    def test_provider_error_still_goes_to_manual_review(self):
        def create_check(client, payload):
            return {
                "provider_status": "error",
                "error_code": "aml_provider_error",
                "error_message": "provider rejected request",
            }

        aml_processing.AmlShkeeperClient.create_check = create_check
        tx = self.make_tx("150")

        check = aml_processing.ensure_aml_for_transaction(tx)

        self.assertEqual(check.status, AmlStatus.MANUAL_REVIEW)
        self.assertEqual(check.deposit_decision, DepositDecision.MANUAL_REVIEW)
        self.assertEqual(check.decision_reason, "aml_provider_error")

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
