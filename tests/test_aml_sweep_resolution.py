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
    DepositDecision,
    ExchangeRate,
    FeeCalculationPolicy,
    Invoice,
    InvoiceAddress,
    Transaction,
    Wallet,
)
from shkeeper.services.sweep_eligibility import decide_sweep_eligibility
from shkeeper.services.sweep_resolution import (
    SweepResolutionError,
    record_sweep_resolution,
)


class AmlSweepResolutionTestCase(unittest.TestCase):
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

    def make_manual_review_tx(
        self,
        guarded=True,
        status=AmlStatus.MANUAL_REVIEW,
        crypto="USDT",
        address="TADDR",
        network=None,
        decision_reason=None,
        callback_confirmed=True,
    ):
        self.sequence += 1
        network = network or {
            "BNB-USDT": "BSC",
            "ETH-USDT": "ETHEREUM",
            "TON-USDT": "TON",
        }.get(crypto, "TRON")
        decision_reason = decision_reason or (
            "risk_score_above_threshold"
            if status == AmlStatus.MANUAL_REVIEW
            else "risk_score_accepted"
        )
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
        db.session.add(InvoiceAddress(invoice_id=invoice.id, crypto=crypto, addr=address))
        db.session.commit()
        tx = Transaction(
            invoice_id=invoice.id,
            txid=f"manual-tx-{self.sequence}",
            crypto=crypto,
            amount_crypto=Decimal("100"),
            amount_fiat=Decimal("100"),
            need_more_confirmations=False,
            callback_confirmed=callback_confirmed,
        )
        db.session.add(tx)
        db.session.commit()
        db.session.add(
            AmlCheck(
                transaction_id=tx.id,
                deposit_id=f"shkeeper-tx-{tx.id}",
                idempotency_key=f"{crypto}:{tx.txid}:shkeeper-tx-{tx.id}",
                provider="koinkyt",
                provider_status=status,
                status=status,
                sweep_guard_required=guarded,
                deposit_decision=(
                    DepositDecision.MANUAL_REVIEW
                    if status == AmlStatus.MANUAL_REVIEW
                    else DepositDecision.CREDIT
                ),
                decision_reason=decision_reason,
                asset="USDT",
                network=network,
            )
        )
        db.session.commit()
        return tx

    def approved_payload(self, tx, **overrides):
        payload = {
            "resolution_type": "approved",
            "deposit_id": f"shkeeper-tx-{tx.id}",
            "crypto": "USDT",
            "network": "TRON",
            "address": "TADDR",
            "txid": tx.txid,
            "external_review_id": "gp-review-1",
            "reviewer": "admin@example.com",
            "reason": "Manual approval after compliance review",
            "idempotency_key": "gp-resolution-1",
        }
        payload.update(overrides)
        return payload

    def refunded_payload(self, tx, **overrides):
        payload = {
            "resolution_type": "refunded",
            "deposit_id": f"shkeeper-tx-{tx.id}",
            "crypto": "USDT",
            "network": "TRON",
            "address": "TADDR",
            "txid": tx.txid,
            "refund_txid": "refund-tx-1",
            "refund_to_address": "TSENDER",
            "refund_amount": "100.000000",
            "refund_source_address": "TREFUNDSOURCE",
            "refund_asset": "USDT",
            "refund_network": "TRON",
            "refund_notes": "operator note",
            "external_review_id": "gp-review-3",
            "reviewer": "admin@example.com",
            "reason": "Manual refund completed from VPS script",
            "idempotency_key": "gp-resolution-3",
        }
        payload.update(overrides)
        return payload

    def backend_headers(self):
        return {"X-Shkeeper-Backend-Key": "test-backend-key"}

    def test_approved_resolution_unblocks_manual_review_when_address_is_otherwise_safe(self):
        tx = self.make_manual_review_tx()

        result = record_sweep_resolution(self.approved_payload(tx))
        eligibility = decide_sweep_eligibility("USDT", "TRON", "TADDR")

        self.assertEqual(result["status"], "success")
        self.assertEqual(AmlSweepResolution.query.count(), 1)
        self.assertEqual(eligibility["decision"], "allow")
        self.assertEqual(eligibility["reason"], "manual_approved")

    def test_eth_approved_resolution_accepts_grither_erc20_network_alias(self):
        tx = self.make_manual_review_tx(
            crypto="ETH-USDT",
            address="0x000000000000000000000000000000000000dEaD",
        )

        result = record_sweep_resolution(
            self.approved_payload(
                tx,
                crypto="ETH-USDT",
                network="erc20",
                address="0x000000000000000000000000000000000000dEaD",
            )
        )
        eligibility = decide_sweep_eligibility(
            "ETH-USDT",
            "ETH",
            "0x000000000000000000000000000000000000dead",
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["resolution"]["network"], "ETHEREUM")
        self.assertEqual(eligibility["decision"], "allow")
        self.assertEqual(eligibility["reason"], "manual_approved")

    def test_eth_refunded_resolution_accepts_grither_erc20_network_alias(self):
        tx = self.make_manual_review_tx(
            crypto="ETH-USDT",
            address="0x000000000000000000000000000000000000dEaD",
        )

        result = record_sweep_resolution(
            self.refunded_payload(
                tx,
                crypto="ETH-USDT",
                network="erc20",
                address="0x000000000000000000000000000000000000dEaD",
            )
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["resolution"]["network"], "ETHEREUM")

    def test_eth_resolution_accepts_evm_address_case_difference(self):
        tx = self.make_manual_review_tx(
            crypto="ETH-USDT",
            address="0x000000000000000000000000000000000000dEaD",
        )

        result = record_sweep_resolution(
            self.approved_payload(
                tx,
                crypto="ETH-USDT",
                network="ETH",
                address="0x000000000000000000000000000000000000dead",
            )
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(AmlSweepResolution.query.count(), 1)

    def test_refunded_resolution_requires_refund_evidence(self):
        tx = self.make_manual_review_tx()
        payload = self.refunded_payload(tx)
        del payload["refund_txid"]

        with self.assertRaises(SweepResolutionError) as cm:
            record_sweep_resolution(payload)

        self.assertEqual(cm.exception.status_code, 400)
        self.assertEqual(AmlSweepResolution.query.count(), 0)

    def test_approved_resolution_rejects_refund_evidence(self):
        tx = self.make_manual_review_tx()

        with self.assertRaises(SweepResolutionError) as cm:
            record_sweep_resolution(
                self.approved_payload(tx, refund_notes="not valid for approval")
            )

        self.assertEqual(cm.exception.status_code, 400)
        self.assertEqual(cm.exception.code, "unexpected_refund_evidence")
        self.assertEqual(AmlSweepResolution.query.count(), 0)

    def test_refunded_resolution_rejects_non_positive_refund_amount(self):
        tx = self.make_manual_review_tx()

        for amount in ("0", "-1"):
            with self.subTest(amount=amount):
                with self.assertRaises(SweepResolutionError) as cm:
                    record_sweep_resolution(
                        self.refunded_payload(
                            tx,
                            refund_amount=amount,
                            idempotency_key=f"gp-resolution-{amount}",
                        )
                    )
                self.assertEqual(cm.exception.status_code, 400)
                self.assertEqual(cm.exception.code, "invalid_refund_amount")

        self.assertEqual(AmlSweepResolution.query.count(), 0)

    def test_refunded_resolution_rejects_non_finite_refund_amount(self):
        tx = self.make_manual_review_tx()

        for amount in ("NaN", "Infinity"):
            with self.subTest(amount=amount):
                with self.assertRaises(SweepResolutionError) as cm:
                    record_sweep_resolution(
                        self.refunded_payload(
                            tx,
                            refund_amount=amount,
                            idempotency_key=f"gp-resolution-{amount}",
                        )
                    )
                self.assertEqual(cm.exception.status_code, 400)
                self.assertEqual(cm.exception.code, "invalid_refund_amount")

        self.assertEqual(AmlSweepResolution.query.count(), 0)

    def test_refunded_resolution_with_operator_evidence_unblocks_when_address_is_otherwise_safe(self):
        tx = self.make_manual_review_tx()

        result = record_sweep_resolution(self.refunded_payload(tx))
        eligibility = decide_sweep_eligibility("USDT", "TRON", "TADDR")
        resolution = AmlSweepResolution.query.one()

        self.assertEqual(result["status"], "success")
        self.assertEqual(resolution.refund_txid, "refund-tx-1")
        self.assertEqual(resolution.refund_to_address, "TSENDER")
        self.assertEqual(resolution.refund_amount, Decimal("100.000000"))
        self.assertEqual(resolution.refund_source_address, "TREFUNDSOURCE")
        self.assertEqual(resolution.refund_asset, "USDT")
        self.assertEqual(resolution.refund_network, "TRON")
        self.assertEqual(resolution.refund_notes, "operator note")
        self.assertEqual(result["resolution"]["refund_source_address"], "TREFUNDSOURCE")
        self.assertEqual(result["resolution"]["refund_asset"], "USDT")
        self.assertEqual(result["resolution"]["refund_network"], "TRON")
        self.assertEqual(result["resolution"]["refund_notes"], "operator note")
        self.assertEqual(eligibility["decision"], "allow")
        self.assertEqual(eligibility["reason"], "manual_refund")

    def test_resolution_rejects_non_manual_review_deposit(self):
        tx = self.make_manual_review_tx(status=AmlStatus.APPROVED)

        with self.assertRaises(SweepResolutionError) as cm:
            record_sweep_resolution(self.approved_payload(tx))

        self.assertEqual(cm.exception.status_code, 409)
        self.assertEqual(AmlSweepResolution.query.count(), 0)

    def test_resolution_rejects_non_terminal_manual_review_deposit(self):
        tx = self.make_manual_review_tx()
        tx.aml_check.deposit_decision = None
        tx.aml_check.decision_reason = None
        db.session.commit()

        with self.assertRaises(SweepResolutionError) as cm:
            record_sweep_resolution(self.approved_payload(tx))

        self.assertEqual(cm.exception.status_code, 409)
        self.assertEqual(cm.exception.code, "not_terminal_manual_review")
        self.assertEqual(AmlSweepResolution.query.count(), 0)

    def test_resolution_rejects_legacy_non_guarded_deposit(self):
        tx = self.make_manual_review_tx(guarded=False)

        with self.assertRaises(SweepResolutionError) as cm:
            record_sweep_resolution(self.approved_payload(tx))

        self.assertEqual(cm.exception.status_code, 409)
        self.assertEqual(AmlSweepResolution.query.count(), 0)

    def test_resolution_is_idempotent_by_idempotency_key(self):
        tx = self.make_manual_review_tx()
        payload = self.approved_payload(tx)

        first = record_sweep_resolution(payload)
        second = record_sweep_resolution(dict(payload))

        self.assertEqual(first["resolution"]["id"], second["resolution"]["id"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(AmlSweepResolution.query.count(), 1)

    def test_conflicting_idempotency_key_is_rejected(self):
        tx = self.make_manual_review_tx()
        payload = self.approved_payload(tx)
        record_sweep_resolution(payload)

        conflicting = self.approved_payload(
            tx,
            reason="Different manual approval reason",
        )

        with self.assertRaises(SweepResolutionError) as cm:
            record_sweep_resolution(conflicting)

        self.assertEqual(cm.exception.status_code, 409)
        self.assertEqual(AmlSweepResolution.query.count(), 1)

    def test_same_deposit_resolution_with_new_key_and_same_evidence_is_idempotent(self):
        tx = self.make_manual_review_tx()
        payload = self.approved_payload(tx)
        first = record_sweep_resolution(payload)

        replay = self.approved_payload(tx, idempotency_key="gp-resolution-new")
        second = record_sweep_resolution(replay)

        self.assertEqual(first["resolution"]["id"], second["resolution"]["id"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(AmlSweepResolution.query.count(), 1)

    def test_second_resolution_for_deposit_with_different_evidence_is_rejected(self):
        tx = self.make_manual_review_tx()
        record_sweep_resolution(self.approved_payload(tx))

        with self.assertRaises(SweepResolutionError) as cm:
            record_sweep_resolution(
                self.refunded_payload(tx, idempotency_key="gp-resolution-new")
            )

        self.assertEqual(cm.exception.status_code, 409)
        self.assertEqual(AmlSweepResolution.query.count(), 1)

    def test_sweep_resolution_endpoint_rejects_missing_external_review_id(self):
        tx = self.make_manual_review_tx()
        payload = self.approved_payload(tx)
        del payload["external_review_id"]

        response = self.client.post(
            "/api/v1/sweep-resolution",
            json=payload,
            headers=self.backend_headers(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "missing_required_fields")

    def test_sweep_resolution_endpoint_records_resolution_with_valid_key(self):
        tx = self.make_manual_review_tx()

        response = self.client.post(
            "/api/v1/sweep-resolution",
            json=self.approved_payload(tx),
            headers=self.backend_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "success")
        self.assertEqual(AmlSweepResolution.query.count(), 1)

    def test_sweep_resolution_endpoint_rejects_missing_required_field(self):
        tx = self.make_manual_review_tx()
        payload = self.approved_payload(tx)
        del payload["reviewer"]

        response = self.client.post(
            "/api/v1/sweep-resolution",
            json=payload,
            headers=self.backend_headers(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "missing_required_fields")

    def test_sweep_resolution_endpoint_rejects_invalid_resolution_type(self):
        tx = self.make_manual_review_tx()

        response = self.client.post(
            "/api/v1/sweep-resolution",
            json=self.approved_payload(tx, resolution_type="rejected"),
            headers=self.backend_headers(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "invalid_resolution_type")

    def test_sweep_resolution_endpoint_rejects_refund_without_evidence(self):
        tx = self.make_manual_review_tx()
        payload = self.refunded_payload(tx)
        del payload["refund_txid"]

        response = self.client.post(
            "/api/v1/sweep-resolution",
            json=payload,
            headers=self.backend_headers(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "missing_refund_evidence")

    def test_sweep_resolution_endpoint_rejects_non_guarded_deposit(self):
        tx = self.make_manual_review_tx(guarded=False)

        response = self.client.post(
            "/api/v1/sweep-resolution",
            json=self.approved_payload(tx),
            headers=self.backend_headers(),
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["code"], "not_guarded")

    def test_sweep_resolution_endpoint_rejects_non_manual_review_deposit(self):
        tx = self.make_manual_review_tx(status=AmlStatus.APPROVED)

        response = self.client.post(
            "/api/v1/sweep-resolution",
            json=self.approved_payload(tx),
            headers=self.backend_headers(),
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["code"], "not_manual_review")

    def test_sweep_resolution_endpoint_maps_idempotency_conflict_to_409(self):
        tx = self.make_manual_review_tx()
        payload = self.approved_payload(tx)
        record_sweep_resolution(payload)

        response = self.client.post(
            "/api/v1/sweep-resolution",
            json=self.approved_payload(tx, reason="Different reason"),
            headers=self.backend_headers(),
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["code"], "idempotency_conflict")


if __name__ == "__main__":
    unittest.main()
