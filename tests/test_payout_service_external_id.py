from decimal import Decimal
import unittest

from flask import Flask
from sqlalchemy.exc import IntegrityError

from shkeeper import db
from shkeeper.models import Payout, PayoutStatus
from shkeeper.modules.classes.crypto import Crypto
from shkeeper.services.payout_errors import PayoutConflictError, PayoutRequestError
from shkeeper.services.payout_service import PayoutService


class FakeCrypto:
    def __init__(self, response=None):
        self.response = response or {"task_id": "task-1"}
        self.calls = []
        self.on_mkpayout = None

    def mkpayout(self, destination, amount, fee):
        self.calls.append((destination, amount, fee))
        if self.on_mkpayout:
            return self.on_mkpayout(destination, amount, fee)
        return dict(self.response)

    def multipayout(self, payout_list):
        self.calls.append(("multipayout", list(payout_list)))
        return dict(self.response)


class PayoutServiceExternalIdTestCase(unittest.TestCase):
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
        self.original_crypto_instances = dict(Crypto.instances)
        Crypto.instances.clear()

    def tearDown(self):
        Crypto.instances.clear()
        Crypto.instances.update(self.original_crypto_instances)
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def register_crypto(self, crypto):
        Crypto.instances["USDT"] = crypto
        return crypto

    def test_null_external_id_allows_multiple_payout_rows(self):
        Payout.add({"dest": "TA", "amount": Decimal("1")}, "USDT")
        Payout.add({"dest": "TB", "amount": Decimal("2")}, "USDT")

        self.assertEqual(Payout.query.count(), 2)

    def test_external_id_normalization_trims_before_record_create(self):
        crypto = self.register_crypto(FakeCrypto())

        result = PayoutService.single_payout(
            "USDT",
            {
                "external_id": "  WW-1  ",
                "destination": "TA",
                "amount": "1.25",
                "fee": "0",
            },
        )

        payout = Payout.query.one()
        self.assertEqual(payout.external_id, "WW-1")
        self.assertEqual(result["external_id"], "WW-1")
        self.assertEqual(crypto.calls, [("TA", Decimal("1.25"), "0")])

    def test_duplicate_external_id_is_rejected_before_sidecar_call(self):
        Payout.add({"dest": "TA", "amount": Decimal("1")}, "USDT", external_id="WW-1")
        crypto = self.register_crypto(FakeCrypto())

        with self.assertRaises(PayoutConflictError):
            PayoutService.single_payout(
                "USDT",
                {
                    "external_id": "WW-1",
                    "destination": "TB",
                    "amount": "1",
                    "fee": "0",
                },
            )

        self.assertEqual(crypto.calls, [])
        self.assertEqual(Payout.query.count(), 1)

    def test_unique_constraint_rejects_concurrent_duplicate_external_id(self):
        Payout.add({"dest": "TA", "amount": Decimal("1")}, "USDT", external_id="WW-1")

        with self.assertRaises(IntegrityError):
            Payout.add(
                {"dest": "TB", "amount": Decimal("1")},
                "USDT",
                external_id="WW-1",
            )

        db.session.rollback()
        self.assertEqual(Payout.query.count(), 1)

    def test_external_id_path_creates_payout_before_sidecar_task_id(self):
        crypto = self.register_crypto(FakeCrypto())

        def assert_reserved_then_return(destination, amount, fee):
            payout = Payout.query.filter_by(external_id="WW-1").one()
            self.assertIsNone(payout.task_id)
            self.assertEqual(payout.status, PayoutStatus.IN_PROGRESS)
            return {"task_id": "task-1"}

        crypto.on_mkpayout = assert_reserved_then_return

        PayoutService.single_payout(
            "USDT",
            {
                "external_id": "WW-1",
                "destination": "TA",
                "amount": "1",
                "fee": "0",
            },
        )

        payout = Payout.query.one()
        self.assertEqual(payout.task_id, "task-1")
        self.assertIsNone(payout.error)

    def test_external_id_path_marks_enqueue_pending_before_sidecar_task_id(self):
        crypto = self.register_crypto(FakeCrypto())

        def assert_pending_then_return(destination, amount, fee):
            payout = Payout.query.filter_by(external_id="WW-1").one()
            self.assertEqual(payout.error, "Payout enqueue pending")
            return {"task_id": "task-1"}

        crypto.on_mkpayout = assert_pending_then_return

        PayoutService.single_payout(
            "USDT",
            {
                "external_id": "WW-1",
                "destination": "TA",
                "amount": "1",
                "fee": "0",
            },
        )

    def test_external_id_path_marks_clear_no_task_response_failed(self):
        crypto = self.register_crypto(FakeCrypto(response={"status": "error"}))

        with self.assertRaises(PayoutRequestError):
            PayoutService.single_payout(
                "USDT",
                {
                    "external_id": "WW-1",
                    "destination": "TA",
                    "amount": "1",
                    "fee": "0",
                },
            )

        payout = Payout.query.one()
        self.assertEqual(payout.status, PayoutStatus.FAIL)
        self.assertEqual(payout.success, "No")

    def test_sidecar_response_without_task_id_is_rejected_without_legacy_record(self):
        self.register_crypto(FakeCrypto(response={"status": "error"}))

        with self.assertRaises(PayoutRequestError):
            PayoutService.single_payout(
                "USDT",
                {
                    "destination": "TA",
                    "amount": "1",
                    "fee": "0",
                },
            )

        self.assertEqual(Payout.query.count(), 0)

    def test_non_finite_amount_is_rejected_before_sidecar_call(self):
        crypto = self.register_crypto(FakeCrypto())

        with self.assertRaises(PayoutRequestError) as cm:
            PayoutService.single_payout(
                "USDT",
                {
                    "destination": "TA",
                    "amount": "NaN",
                    "fee": "0",
                },
            )

        self.assertEqual(cm.exception.code, "INVALID_AMOUNT")
        self.assertEqual(crypto.calls, [])
        self.assertEqual(Payout.query.count(), 0)

    def test_direct_payout_response_without_task_id_is_accepted(self):
        self.register_crypto(FakeCrypto(response={"result": "tx-1", "error": None}))

        result = PayoutService.single_payout(
            "USDT",
            {
                "destination": "TA",
                "amount": "1",
                "fee": "0",
            },
        )

        payout = Payout.query.one()
        self.assertEqual(result["result"], "tx-1")
        self.assertIsNone(payout.task_id)
        self.assertEqual([tx.txid for tx in payout.transactions], ["tx-1"])

    def test_direct_payout_error_without_external_id_marks_record_failed(self):
        self.register_crypto(
            FakeCrypto(response={"result": None, "error": {"message": "rejected"}})
        )

        result = PayoutService.single_payout(
            "USDT",
            {
                "destination": "TA",
                "amount": "1",
                "fee": "0",
            },
        )

        payout = Payout.query.one()
        self.assertEqual(result["error"], {"message": "rejected"})
        self.assertEqual(payout.status, PayoutStatus.FAIL)
        self.assertEqual(payout.success, "No")
        self.assertIn("rejected", payout.error)

    def test_external_id_direct_payout_response_without_task_id_is_accepted(self):
        self.register_crypto(FakeCrypto(response={"result": "tx-1", "error": None}))

        result = PayoutService.single_payout(
            "USDT",
            {
                "external_id": "WW-1",
                "destination": "TA",
                "amount": "1",
                "fee": "0",
            },
        )

        payout = Payout.query.one()
        self.assertEqual(result["external_id"], "WW-1")
        self.assertIsNone(payout.task_id)
        self.assertIsNone(payout.error)
        self.assertEqual([tx.txid for tx in payout.transactions], ["tx-1"])

    def test_external_id_direct_payout_error_marks_reserved_row_failed(self):
        self.register_crypto(
            FakeCrypto(response={"result": None, "error": {"message": "rejected"}})
        )

        result = PayoutService.single_payout(
            "USDT",
            {
                "external_id": "WW-1",
                "destination": "TA",
                "amount": "1",
                "fee": "0",
            },
        )

        payout = Payout.query.one()
        self.assertEqual(result["external_id"], "WW-1")
        self.assertEqual(payout.status, PayoutStatus.FAIL)
        self.assertEqual(payout.success, "No")
        self.assertIn("rejected", payout.error)

    def test_multipayout_validates_external_ids_before_sidecar_call(self):
        Payout.add({"dest": "TA", "amount": Decimal("1")}, "USDT", external_id="WW-1")
        crypto = self.register_crypto(FakeCrypto())

        with self.assertRaises(PayoutConflictError):
            PayoutService.multiple_payout(
                "USDT",
                [{"external_id": " WW-1 ", "dest": "TB", "amount": "1"}],
            )

        self.assertEqual(crypto.calls, [])

    def test_multipayout_rejects_external_id_before_sidecar_call(self):
        crypto = self.register_crypto(FakeCrypto())

        with self.assertRaises(PayoutRequestError) as cm:
            PayoutService.multiple_payout(
                "USDT",
                [
                    {"external_id": "WW-1", "dest": "TA", "amount": "1"},
                    {"external_id": " WW-1 ", "dest": "TB", "amount": "2"},
                ],
            )

        self.assertEqual(cm.exception.code, "MULTIPAYOUT_EXTERNAL_ID_UNSUPPORTED")
        self.assertEqual(crypto.calls, [])

    def test_update_from_task_marks_error_result_failed(self):
        payout = Payout.add(
            {"dest": "TA", "amount": Decimal("1")},
            "USDT",
            task_id="task-1",
        )

        Payout.update_from_task(
            {
                "status": "SUCCESS",
                "result": [
                    {
                        "dest": "TA",
                        "status": "error",
                        "message": "contractResult",
                    }
                ],
            },
            "task-1",
        )

        db.session.refresh(payout)
        self.assertEqual(payout.status, PayoutStatus.FAIL)
        self.assertEqual(payout.success, "No")
        self.assertIn("contractResult", payout.error)


if __name__ == "__main__":
    unittest.main()
