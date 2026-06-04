from datetime import datetime, timezone
import json
import unittest

from flask import Flask

from shkeeper import db
from shkeeper.models import (
    PayoutCallbackEvent,
    PayoutExecution,
    PayoutExecutionState,
    PayoutRail,
    PayoutRailHotWalletPolicy,
    PayoutRailLegacySpendPolicy,
)
from shkeeper.services.payout_contract import sha256_hex
from shkeeper.services.payout_errors import PayoutRequestError
from shkeeper.services.payout_execution_auth import signature_base, sign_request
from shkeeper.services.payout_execution_service import PayoutExecutionService


VALID_TRON_DESTINATION = "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb"


class PayoutCallbackOutboxTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            PAYOUT_CONSUMER_KEYS={"grither-pay": {"test-key": "secret"}},
            PAYOUT_CALLBACK_ENDPOINTS={
                "grither-pay": {
                    "grither-pay-payouts": "http://grither-pay/payout-callbacks",
                },
            },
            PAYOUT_AUTH_MAX_AGE_SECONDS=300,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.add_tron_rail()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def add_tron_rail(self):
        rail = PayoutRail(
            consumer="grither-pay",
            asset="USDT",
            network="TRON",
            crypto_id="USDT",
            sidecar_service="tron-shkeeper",
            sidecar_symbol="USDT",
            payout_queue="tron_usdt_fee_payouts",
            source_wallet_ref="fee_deposit",
            hot_wallet_policy=(
                PayoutRailHotWalletPolicy.CURRENT_SIDECAR_SOURCE_WALLET
            ),
            legacy_spend_policy=(
                PayoutRailLegacySpendPolicy.BLOCK_AUTOMATIC_BYPASS
            ),
            execution_enabled=True,
            decimals=6,
            callback_endpoint_id="grither-pay-payouts",
            contract_version="usdt-payout-execution-v1",
        )
        db.session.add(rail)
        db.session.commit()
        return rail

    def create_execution(self, external_id="WD-1"):
        PayoutExecutionService.submit(
            "grither-pay",
            {
                "external_id": external_id,
                "asset": "USDT",
                "network": "TRON",
                "amount": "25",
                "destination": VALID_TRON_DESTINATION,
            },
        )
        return PayoutExecution.query.filter_by(external_id=external_id).one()

    def sidecar_status(self, execution, **overrides):
        payload = {
            "consumer": execution.consumer,
            "execution_id": execution.id,
            "sidecar_execution_id": "sidecar-1",
            "external_id": execution.external_id,
            "contract_version": execution.contract_version,
            "asset": execution.asset,
            "network": execution.network,
            "request_hash": execution.request_hash,
            "sidecar_payload_hash": execution.sidecar_payload_hash,
            "sidecar_state": "RECEIVED",
            "sidecar_state_version": 1,
            "sidecar_state_transition_id": "sidecar-transition-1",
            "state_updated_at": "2026-06-03T10:00:00Z",
        }
        payload.update(overrides)
        return payload

    def test_submit_creates_initial_callback_event_atomically(self):
        execution = self.create_execution()

        event = PayoutCallbackEvent.query.one()
        payload = json.loads(event.raw_payload)
        headers = json.loads(event.signature_headers_json)

        self.assertEqual(event.consumer, "grither-pay")
        self.assertEqual(event.callback_endpoint_id, "grither-pay-payouts")
        self.assertEqual(event.execution_id, execution.id)
        self.assertEqual(event.event_version, 1)
        self.assertEqual(event.state_transition_id, execution.state_transition_id)
        self.assertEqual(event.occurred_at, execution.last_state_occurred_at)
        self.assertEqual(event.payload_hash, sha256_hex(event.raw_payload.encode()))
        self.assertEqual(event.dispatch_status, "PENDING")
        self.assertEqual(event.attempt_count, 0)
        self.assertEqual(payload["event_id"], event.event_id)
        self.assertEqual(payload["previous_state"], None)
        self.assertEqual(payload["state"], "CREATED")
        self.assertEqual(payload["sidecar_execution_id"], None)
        self.assertEqual(payload["amount"], "25.000000")
        self.assertEqual(payload["destination"], VALID_TRON_DESTINATION)
        self.assertEqual(headers["X-Payout-Consumer"], "grither-pay")
        self.assertEqual(headers["X-Payout-Key-Id"], "test-key")
        self.assertIn("X-Payout-Signature", headers)
        self.assertTrue(event.signature_base)

    def test_callback_signing_accepts_nested_key_config(self):
        self.app.config["PAYOUT_CALLBACK_KEYS"] = {
            "grither-pay": {
                "callback-key": {
                    "secret": "callback-secret",
                },
            },
        }

        self.create_execution("WD-NESTED-CALLBACK-KEY")

        event = PayoutCallbackEvent.query.one()
        headers = json.loads(event.signature_headers_json)
        self.assertEqual(headers["X-Payout-Key-Id"], "callback-key")
        self.assertIn("X-Payout-Signature", headers)

    def test_transition_creates_callback_event_for_state_change(self):
        execution = self.create_execution()

        PayoutExecutionService.transition(
            execution,
            PayoutExecutionState.PREFLIGHTED,
        )

        events = PayoutCallbackEvent.query.order_by(
            PayoutCallbackEvent.event_version
        ).all()
        self.assertEqual(len(events), 2)
        payload = json.loads(events[1].raw_payload)
        self.assertEqual(events[1].event_version, 2)
        self.assertEqual(payload["previous_state"], "CREATED")
        self.assertEqual(payload["state"], "PREFLIGHTED")

    def test_same_state_sidecar_progress_does_not_create_callback_event(self):
        execution = self.create_execution()
        PayoutExecutionService.apply_sidecar_status(
            execution,
            self.sidecar_status(execution),
        )
        PayoutExecutionService.apply_sidecar_status(
            execution,
            self.sidecar_status(
                execution,
                sidecar_state="VALIDATED",
                sidecar_state_version=2,
                sidecar_state_transition_id="sidecar-transition-2",
                state_updated_at="2026-06-03T10:01:00Z",
                source_wallet="TSourceWallet",
                token_contract="TRC20-USDT",
            ),
        )

        events = PayoutCallbackEvent.query.order_by(
            PayoutCallbackEvent.event_version
        ).all()
        self.assertEqual(len(events), 2)
        payload = json.loads(events[1].raw_payload)
        self.assertTrue(payload["sidecar_status_hash"])
        self.assertIsNone(payload["sidecar_evidence"]["source_wallet"])

        db.session.refresh(execution)
        status = PayoutExecutionService.status("grither-pay", execution.external_id)
        self.assertEqual(status["sidecar_evidence"]["source_wallet"], "TSourceWallet")
        self.assertEqual(status["sidecar_evidence"]["token_contract"], "TRC20-USDT")

    def test_outbox_insert_failure_rolls_back_state_transition(self):
        from shkeeper.services import payout_callback_outbox

        execution = self.create_execution()
        original = payout_callback_outbox.PayoutCallbackOutbox.add_transition_event

        def fail_after_insert(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("outbox insert failed")

        payout_callback_outbox.PayoutCallbackOutbox.add_transition_event = (
            fail_after_insert
        )
        try:
            with self.assertRaises(RuntimeError):
                PayoutExecutionService.transition(
                    execution,
                    PayoutExecutionState.PREFLIGHTED,
                )
        finally:
            payout_callback_outbox.PayoutCallbackOutbox.add_transition_event = (
                original
            )
            db.session.rollback()

        db.session.refresh(execution)
        self.assertEqual(execution.state, PayoutExecutionState.CREATED)
        self.assertEqual(PayoutCallbackEvent.query.count(), 1)

    def test_transition_requires_callback_endpoint_still_configured(self):
        execution = self.create_execution()
        self.app.config["PAYOUT_CALLBACK_ENDPOINTS"] = {"grither-pay": {}}

        with self.assertRaises(PayoutRequestError):
            PayoutExecutionService.transition(
                execution,
                PayoutExecutionState.PREFLIGHTED,
            )
        db.session.rollback()

        db.session.refresh(execution)
        self.assertEqual(execution.state, PayoutExecutionState.CREATED)
        self.assertEqual(PayoutCallbackEvent.query.count(), 1)

    def test_dispatch_failure_updates_retry_metadata_without_changing_payload(self):
        from shkeeper.services.payout_callback_outbox import PayoutCallbackOutbox

        self.create_execution()
        event = PayoutCallbackEvent.query.one()
        raw_payload = event.raw_payload

        def fail_delivery(_event):
            raise RuntimeError("network down")

        processed = PayoutCallbackOutbox.dispatch_due_events(
            batch_size=10,
            deliverer=fail_delivery,
            now=datetime(2026, 6, 3, 12, 0, 0),
        )

        db.session.refresh(event)
        self.assertEqual(processed, 1)
        self.assertEqual(event.dispatch_status, "RETRY")
        self.assertEqual(event.attempt_count, 1)
        self.assertEqual(event.last_error, "network down")
        self.assertIsNotNone(event.next_attempt_at)
        self.assertEqual(event.raw_payload, raw_payload)

    def test_dispatch_refreshes_signature_timestamp_for_delayed_event(self):
        from shkeeper.services import payout_callback_outbox
        from shkeeper.services.payout_callback_outbox import PayoutCallbackOutbox

        original_time = payout_callback_outbox.time.time
        try:
            payout_callback_outbox.time.time = lambda: 1780559400
            self.create_execution()
        finally:
            payout_callback_outbox.time.time = original_time

        event = PayoutCallbackEvent.query.one()
        initial_headers = json.loads(event.signature_headers_json)
        self.assertEqual(initial_headers["X-Payout-Timestamp"], "1780559400")

        dispatch_now = datetime(2026, 6, 4, 12, 10, 0)
        expected_timestamp = str(
            int(dispatch_now.replace(tzinfo=timezone.utc).timestamp())
        )
        delivered = {}

        class Response:
            status_code = 204

        def capture_delivery(delivery_event):
            headers = json.loads(delivery_event.signature_headers_json)
            delivered["headers"] = headers
            delivered["base"] = delivery_event.signature_base
            delivered["raw_payload"] = delivery_event.raw_payload
            return Response()

        processed = PayoutCallbackOutbox.dispatch_due_events(
            batch_size=10,
            deliverer=capture_delivery,
            now=dispatch_now,
        )

        db.session.refresh(event)
        expected_base = signature_base(
            expected_timestamp,
            event.event_id,
            "POST",
            "/payout-callbacks",
            "",
            event.raw_payload.encode("utf-8"),
        )
        self.assertEqual(processed, 1)
        self.assertEqual(delivered["headers"]["X-Payout-Nonce"], event.event_id)
        self.assertEqual(
            delivered["headers"]["X-Payout-Timestamp"],
            expected_timestamp,
        )
        self.assertEqual(delivered["base"], expected_base)
        self.assertEqual(
            delivered["headers"]["X-Payout-Signature"],
            sign_request("secret", expected_base),
        )
        self.assertEqual(event.signature_base, expected_base)
        self.assertEqual(event.raw_payload, delivered["raw_payload"])

    def test_dispatch_success_marks_event_delivered(self):
        from shkeeper.services.payout_callback_outbox import PayoutCallbackOutbox

        class Response:
            status_code = 204

        self.create_execution()
        event = PayoutCallbackEvent.query.one()

        processed = PayoutCallbackOutbox.dispatch_due_events(
            batch_size=10,
            deliverer=lambda _event: Response(),
            now=datetime(2026, 6, 3, 12, 0, 0),
        )

        db.session.refresh(event)
        self.assertEqual(processed, 1)
        self.assertEqual(event.dispatch_status, "DELIVERED")
        self.assertEqual(event.attempt_count, 1)
        self.assertIsNotNone(event.applied_at)

    def test_concurrent_dispatcher_cannot_double_deliver_same_event(self):
        from shkeeper.services.payout_callback_outbox import PayoutCallbackOutbox

        class Response:
            status_code = 204

        self.create_execution()
        delivered = []

        def first_deliverer(event):
            delivered.append(("first", event.event_id))
            nested_count = PayoutCallbackOutbox.dispatch_due_events(
                batch_size=10,
                deliverer=lambda nested_event: delivered.append(
                    ("nested", nested_event.event_id)
                )
                or Response(),
                now=datetime(2026, 6, 3, 12, 0, 0),
            )
            self.assertEqual(nested_count, 0)
            return Response()

        processed = PayoutCallbackOutbox.dispatch_due_events(
            batch_size=10,
            deliverer=first_deliverer,
            now=datetime(2026, 6, 3, 12, 0, 0),
        )

        self.assertEqual(processed, 1)
        self.assertEqual(delivered, [("first", PayoutCallbackEvent.query.one().event_id)])

    def test_later_event_does_not_overtake_retrying_earlier_event(self):
        from shkeeper.services.payout_callback_outbox import PayoutCallbackOutbox

        class Response:
            status_code = 204

        execution = self.create_execution()
        PayoutExecutionService.transition(
            execution,
            PayoutExecutionState.PREFLIGHTED,
        )
        first, second = PayoutCallbackEvent.query.order_by(
            PayoutCallbackEvent.event_version
        ).all()
        first.dispatch_status = "RETRY"
        first.next_attempt_at = datetime(2026, 6, 3, 13, 0, 0)
        db.session.commit()
        delivered = []

        processed = PayoutCallbackOutbox.dispatch_due_events(
            batch_size=10,
            deliverer=lambda event: delivered.append(event.event_version)
            or Response(),
            now=datetime(2026, 6, 3, 12, 0, 0),
        )

        db.session.refresh(second)
        self.assertEqual(processed, 0)
        self.assertEqual(delivered, [])
        self.assertEqual(second.dispatch_status, "PENDING")


if __name__ == "__main__":
    unittest.main()
