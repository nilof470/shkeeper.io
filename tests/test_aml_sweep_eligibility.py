import os
import unittest
from decimal import Decimal

from flask import Flask

from shkeeper import db
from shkeeper.api_v1 import bp
from shkeeper.models import (
    AmlCheck,
    AmlStatus,
    AmlSweepResolution,
    ExchangeRate,
    FeeCalculationPolicy,
    Invoice,
    InvoiceAddress,
    Transaction,
    Wallet,
)
from shkeeper.services.sweep_eligibility import decide_sweep_eligibility


class AmlSweepEligibilityTestCase(unittest.TestCase):
    def setUp(self):
        self.original_sweep_backend_key = os.environ.get("SHKEEPER_SWEEP_BACKEND_KEY")
        self.original_backend_key = os.environ.get("SHKEEPER_BACKEND_KEY")
        self.original_btc_backend_key = os.environ.get("SHKEEPER_BTC_BACKEND_KEY")
        os.environ.pop("SHKEEPER_SWEEP_BACKEND_KEY", None)
        os.environ["SHKEEPER_BACKEND_KEY"] = "test-backend-key"
        os.environ.pop("SHKEEPER_BTC_BACKEND_KEY", None)
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
        self.client = self.app.test_client()
        self.sequence = 0
        self.add_wallet_and_rate("USDT")
        self.add_wallet_and_rate("BNB-USDT")
        self.add_wallet_and_rate("ETH-USDT")
        self.add_wallet_and_rate("TON-USDT")

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        if self.original_sweep_backend_key is None:
            os.environ.pop("SHKEEPER_SWEEP_BACKEND_KEY", None)
        else:
            os.environ["SHKEEPER_SWEEP_BACKEND_KEY"] = self.original_sweep_backend_key
        if self.original_backend_key is None:
            os.environ.pop("SHKEEPER_BACKEND_KEY", None)
        else:
            os.environ["SHKEEPER_BACKEND_KEY"] = self.original_backend_key
        if self.original_btc_backend_key is None:
            os.environ.pop("SHKEEPER_BTC_BACKEND_KEY", None)
        else:
            os.environ["SHKEEPER_BTC_BACKEND_KEY"] = self.original_btc_backend_key

    def add_wallet_and_rate(self, crypto):
        db.session.add(
            Wallet(
                crypto=crypto,
                apikey="api-key",
                llimit=Decimal("95"),
                ulimit=Decimal("105"),
            )
        )
        db.session.add(
            ExchangeRate(
                crypto=crypto,
                fiat="USD",
                rate=Decimal("1"),
                fee=Decimal("0"),
                fixed_fee=Decimal("0"),
                fee_policy=FeeCalculationPolicy.PERCENT_FEE,
            )
        )
        db.session.commit()

    def make_tx(
        self,
        crypto="USDT",
        address="TADDR",
        txid=None,
        need_more_confirmations=False,
        callback_confirmed=True,
    ):
        self.sequence += 1
        txid = txid or f"tx-{self.sequence}"
        invoice = Invoice(
            external_id=f"user-{self.sequence}",
            fiat="USD",
            crypto=crypto,
            addr=address,
            amount_fiat=Decimal("100"),
            amount_crypto=Decimal("100"),
            exchange_rate=Decimal("1"),
            balance_fiat=Decimal("0"),
            balance_crypto=Decimal("0"),
        )
        db.session.add(invoice)
        db.session.commit()
        db.session.add(
            InvoiceAddress(invoice_id=invoice.id, crypto=crypto, addr=address)
        )
        db.session.commit()
        tx = Transaction(
            invoice_id=invoice.id,
            txid=txid,
            crypto=crypto,
            amount_crypto=Decimal("100"),
            amount_fiat=Decimal("100"),
            need_more_confirmations=need_more_confirmations,
            callback_confirmed=callback_confirmed,
        )
        db.session.add(tx)
        db.session.commit()
        return tx

    def add_check(self, tx, status, guarded=True, network=None):
        network = network or {
            "BNB-USDT": "BSC",
            "ETH-USDT": "ETHEREUM",
            "TON-USDT": "TON",
        }.get(tx.crypto, "TRON")
        check = AmlCheck(
            transaction_id=tx.id,
            deposit_id=f"shkeeper-tx-{tx.id}",
            idempotency_key=f"{tx.crypto}:{tx.txid}:shkeeper-tx-{tx.id}",
            provider="koinkyt",
            provider_status=status,
            status=status,
            sweep_guard_required=guarded,
            asset="USDT",
            network=network,
        )
        db.session.add(check)
        db.session.commit()
        return check

    def add_resolution(self, tx, resolution_type="approved"):
        network = {
            "BNB-USDT": "BSC",
            "ETH-USDT": "ETHEREUM",
            "TON-USDT": "TON",
        }.get(tx.crypto, "TRON")
        resolution = AmlSweepResolution(
            transaction_id=tx.id,
            deposit_id=f"shkeeper-tx-{tx.id}",
            txid=tx.txid,
            crypto=tx.crypto,
            network=network,
            address=tx.addr,
            resolution_type=resolution_type,
            reviewer="admin@example.com",
            reason="Manual resolution",
            external_review_id="gp-review-1",
            idempotency_key=f"resolution-{tx.id}",
            request_digest=f"digest-{tx.id}",
        )
        db.session.add(resolution)
        db.session.commit()
        return resolution

    def assertDecision(self, result, decision, reason):
        self.assertEqual(result["decision"], decision)
        self.assertEqual(result["reason"], reason)

    def backend_headers(self):
        return {"X-Shkeeper-Backend-Key": "test-backend-key"}

    def test_legacy_address_without_guarded_checks_allows_sweep(self):
        self.make_tx(address="TADDR", txid="legacy-tx")

        result = decide_sweep_eligibility("USDT", "TRON", "TADDR")

        self.assertDecision(result, "allow", "legacy_no_guarded_deposits")
        self.assertEqual(result["matched_transaction_count"], 0)
        self.assertEqual(result["transaction_ids"], [])
        self.assertEqual(result["aml_statuses"], [])

    def test_pending_guarded_deposit_returns_wait(self):
        tx = self.make_tx(address="TADDR")
        self.add_check(tx, AmlStatus.PENDING)

        result = decide_sweep_eligibility("USDT", "TRON", "TADDR")

        self.assertDecision(result, "wait", "aml_pending")
        self.assertEqual(result["transaction_ids"], [tx.id])
        self.assertEqual(result["matched_transaction_count"], 1)
        self.assertEqual(result["aml_statuses"], [AmlStatus.PENDING])

    def test_manual_review_guarded_deposit_returns_block(self):
        tx = self.make_tx(address="TADDR")
        self.add_check(tx, AmlStatus.MANUAL_REVIEW)

        result = decide_sweep_eligibility("USDT", "TRON", "TADDR")

        self.assertDecision(result, "block", "manual_review")

    def test_approved_guarded_deposit_returns_allow(self):
        tx = self.make_tx(address="TADDR")
        self.add_check(tx, AmlStatus.APPROVED)

        result = decide_sweep_eligibility("USDT", "TRON", "TADDR")

        self.assertDecision(result, "allow", "aml_approved")
        self.assertEqual(result["transaction_ids"], [tx.id])

    def test_approved_guarded_deposit_waits_until_callback_is_confirmed(self):
        tx = self.make_tx(address="TADDR", callback_confirmed=False)
        self.add_check(tx, AmlStatus.APPROVED)

        result = decide_sweep_eligibility("USDT", "TRON", "TADDR")

        self.assertDecision(result, "wait", "callback_pending")
        self.assertEqual(result["transaction_ids"], [tx.id])

    def test_approved_eth_usdt_guarded_deposit_accepts_sidecar_eth_network_alias(self):
        tx = self.make_tx(crypto="ETH-USDT", address="0xabc", txid="approved-eth-tx")
        self.add_check(tx, AmlStatus.APPROVED)

        result = decide_sweep_eligibility("ETH-USDT", "ETH", "0xabc")

        self.assertDecision(result, "allow", "aml_approved")
        self.assertEqual(result["transaction_ids"], [tx.id])

    def test_ton_usdt_unsupported_rail_is_not_strictly_guarded_until_coverage_exists(self):
        self.make_tx(
            crypto="TON-USDT",
            address="UQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            txid="ton-usdt-recorded",
        )

        result = decide_sweep_eligibility(
            "TON-USDT",
            "TON",
            "UQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            txid="ton-usdt-recorded",
        )

        self.assertDecision(result, "allow", "legacy_no_guarded_deposits")

    def test_bep20_usdt_unsupported_rail_is_not_strictly_guarded_until_coverage_exists(self):
        self.make_tx(
            crypto="BNB-USDT",
            address="0x1111111111111111111111111111111111111111",
            txid="bnb-usdt-recorded",
        )

        result = decide_sweep_eligibility(
            "BNB-USDT",
            "bep20",
            "0x1111111111111111111111111111111111111111",
            txid="bnb-usdt-recorded",
        )

        self.assertDecision(result, "allow", "legacy_no_guarded_deposits")

    def test_eth_usdt_address_only_matches_evm_address_case_insensitively(self):
        tx = self.make_tx(
            crypto="ETH-USDT",
            address="0x000000000000000000000000000000000000dEaD",
            txid="pending-eth-tx",
        )
        self.add_check(tx, AmlStatus.CHECKING)

        result = decide_sweep_eligibility(
            "ETH-USDT", "ETH", "0x000000000000000000000000000000000000dead"
        )

        self.assertDecision(result, "wait", "aml_checking")
        self.assertEqual(result["transaction_ids"], [tx.id])

    def test_eth_usdt_txid_match_accepts_evm_address_case_difference(self):
        tx = self.make_tx(
            crypto="ETH-USDT",
            address="0x000000000000000000000000000000000000dEaD",
            txid="pending-eth-tx",
        )
        self.add_check(tx, AmlStatus.CHECKING)

        result = decide_sweep_eligibility(
            "ETH-USDT",
            "ETH",
            "0x000000000000000000000000000000000000dead",
            txid="pending-eth-tx",
        )

        self.assertDecision(result, "wait", "aml_checking")
        self.assertEqual(result["transaction_ids"], [tx.id])

    def test_guarded_deposit_with_wrong_network_returns_block(self):
        tx = self.make_tx(address="TADDR", txid="network-mismatch-tx")
        self.add_check(tx, AmlStatus.MANUAL_REVIEW)

        result = decide_sweep_eligibility("USDT", "ETH", "TADDR")

        self.assertDecision(result, "block", "network_mismatch")
        self.assertEqual(result["transaction_ids"], [tx.id])

    def test_skipped_small_amount_without_guard_marker_returns_allow(self):
        tx = self.make_tx(address="TADDR")
        self.add_check(tx, AmlStatus.SKIPPED, guarded=False)

        result = decide_sweep_eligibility("USDT", "TRON", "TADDR")

        self.assertDecision(result, "allow", "legacy_no_guarded_deposits")
        self.assertEqual(result["matched_transaction_count"], 0)

    def test_live_unknown_txid_returns_wait(self):
        result = decide_sweep_eligibility(
            "USDT", "TRON", "TADDR", txid="not-recorded-yet"
        )

        self.assertDecision(result, "wait", "transaction_not_found")
        self.assertEqual(result["matched_transaction_count"], 0)

    def test_mismatched_address_returns_block(self):
        self.make_tx(address="TOTHER", txid="known-tx")

        result = decide_sweep_eligibility(
            "USDT", "TRON", "TADDR", txid="known-tx"
        )

        self.assertDecision(result, "block", "mismatch")

    def test_ambiguous_txid_returns_block(self):
        self.make_tx(address="TADDR", txid="ambiguous-tx")
        self.make_tx(address="TADDR", txid="ambiguous-tx")

        result = decide_sweep_eligibility(
            "USDT", "TRON", "TADDR", txid="ambiguous-tx"
        )

        self.assertDecision(result, "block", "ambiguous_match")

    def test_one_pending_guarded_deposit_blocks_full_address_allow(self):
        approved = self.make_tx(address="TADDR", txid="approved-tx")
        pending = self.make_tx(address="TADDR", txid="pending-tx")
        self.add_check(approved, AmlStatus.APPROVED)
        self.add_check(pending, AmlStatus.CHECKING)

        result = decide_sweep_eligibility("USDT", "TRON", "TADDR")

        self.assertDecision(result, "wait", "aml_checking")
        self.assertEqual(result["matched_transaction_count"], 2)
        self.assertEqual(result["transaction_ids"], [approved.id, pending.id])

    def test_txid_approved_guarded_deposit_still_waits_for_pending_same_address(self):
        approved = self.make_tx(address="TADDR", txid="approved-tx")
        pending = self.make_tx(address="TADDR", txid="pending-tx")
        self.add_check(approved, AmlStatus.MANUAL_REVIEW)
        self.add_resolution(approved)
        self.add_check(pending, AmlStatus.CHECKING)

        result = decide_sweep_eligibility(
            "USDT", "TRON", "TADDR", txid="approved-tx"
        )

        self.assertDecision(result, "wait", "aml_checking")
        self.assertEqual(result["matched_transaction_count"], 2)
        self.assertEqual(result["transaction_ids"], [approved.id, pending.id])

    def test_manual_resolution_does_not_allow_when_another_guarded_deposit_is_pending(self):
        manual = self.make_tx(address="TADDR", txid="manual-tx")
        pending = self.make_tx(address="TADDR", txid="pending-tx")
        self.add_check(manual, AmlStatus.MANUAL_REVIEW)
        self.add_check(pending, AmlStatus.PENDING)
        self.add_resolution(manual)

        result = decide_sweep_eligibility("USDT", "TRON", "TADDR")

        self.assertDecision(result, "wait", "aml_pending")
        self.assertEqual(result["matched_transaction_count"], 2)

    def test_persisted_guarded_rail_transaction_without_guarded_check_waits_for_aml(self):
        self.make_tx(address="TADDR", txid="guarded-rail-no-check")

        result = decide_sweep_eligibility(
            "USDT", "TRON", "TADDR", txid="guarded-rail-no-check"
        )

        self.assertDecision(result, "wait", "aml_missing")
        self.assertEqual(result["matched_transaction_count"], 1)

    def test_guarded_check_with_unknown_status_returns_wait(self):
        tx = self.make_tx(address="TADDR")
        self.add_check(tx, "provider_glitch")

        result = decide_sweep_eligibility("USDT", "TRON", "TADDR")

        self.assertDecision(result, "wait", "aml_missing")

    def test_confirmations_pending_returns_wait(self):
        tx = self.make_tx(address="TADDR", need_more_confirmations=True)
        self.add_check(tx, AmlStatus.APPROVED)

        result = decide_sweep_eligibility("USDT", "TRON", "TADDR")

        self.assertDecision(result, "wait", "confirmations_pending")

    def test_eth_usdt_accepts_sidecar_eth_network_alias(self):
        self.make_tx(crypto="ETH-USDT", address="0xabc", txid="eth-usdt-legacy")

        result = decide_sweep_eligibility("ETH-USDT", "ETH", "0xabc")

        self.assertDecision(result, "allow", "legacy_no_guarded_deposits")

    def test_sweep_eligibility_endpoint_requires_backend_key(self):
        payload = {"crypto": "USDT", "network": "TRON", "address": "TADDR"}

        missing = self.client.post("/api/v1/sweep-eligibility", json=payload)
        wrong = self.client.post(
            "/api/v1/sweep-eligibility",
            json=payload,
            headers={"X-Shkeeper-Backend-Key": "wrong"},
        )

        self.assertEqual(missing.status_code, 403)
        self.assertEqual(wrong.status_code, 403)

    def test_sweep_eligibility_endpoint_fails_closed_without_configured_backend_key(self):
        os.environ.pop("SHKEEPER_BACKEND_KEY", None)
        os.environ.pop("SHKEEPER_SWEEP_BACKEND_KEY", None)
        os.environ.pop("SHKEEPER_BTC_BACKEND_KEY", None)
        payload = {"crypto": "USDT", "network": "TRON", "address": "TADDR"}

        response = self.client.post(
            "/api/v1/sweep-eligibility",
            json=payload,
            headers={"X-Shkeeper-Backend-Key": "test-backend-key"},
        )

        self.assertEqual(response.status_code, 503)

    def test_sweep_eligibility_endpoint_ignores_legacy_sweep_backend_key_env(self):
        original_backend_key = os.environ.get("SHKEEPER_BACKEND_KEY")
        original_btc_backend_key = os.environ.get("SHKEEPER_BTC_BACKEND_KEY")
        os.environ.pop("SHKEEPER_BACKEND_KEY", None)
        os.environ.pop("SHKEEPER_BTC_BACKEND_KEY", None)
        os.environ["SHKEEPER_SWEEP_BACKEND_KEY"] = "legacy-sweep-key"
        payload = {"crypto": "USDT", "network": "TRON", "address": "TADDR"}

        try:
            response = self.client.post(
                "/api/v1/sweep-eligibility",
                json=payload,
                headers={"X-Shkeeper-Backend-Key": "legacy-sweep-key"},
            )
        finally:
            if original_backend_key is None:
                os.environ.pop("SHKEEPER_BACKEND_KEY", None)
            else:
                os.environ["SHKEEPER_BACKEND_KEY"] = original_backend_key
            if original_btc_backend_key is None:
                os.environ.pop("SHKEEPER_BTC_BACKEND_KEY", None)
            else:
                os.environ["SHKEEPER_BTC_BACKEND_KEY"] = original_btc_backend_key

        self.assertEqual(response.status_code, 503)

    def test_sweep_eligibility_endpoint_returns_service_decision_with_valid_key(self):
        tx = self.make_tx(address="TADDR")
        self.add_check(tx, AmlStatus.APPROVED)

        response = self.client.post(
            "/api/v1/sweep-eligibility",
            json={"crypto": "USDT", "network": "TRON", "address": "TADDR"},
            headers=self.backend_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["decision"], "allow")
        self.assertEqual(response.get_json()["reason"], "aml_approved")


if __name__ == "__main__":
    unittest.main()
