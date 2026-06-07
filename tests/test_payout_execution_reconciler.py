from decimal import Decimal
from datetime import datetime, timedelta, timezone
import json
import unittest

from flask import Flask

from shkeeper import db
from shkeeper.models import (
    PayoutCallbackEvent,
    PayoutExecution,
    PayoutExecutionState,
    PayoutFailureClass,
    PayoutRail,
    PayoutRailHotWalletPolicy,
    PayoutRailLegacySpendPolicy,
)
from shkeeper.services.payout_execution_reconciler import PayoutExecutionReconciler
from shkeeper.services.payout_execution_service import PayoutExecutionService
from shkeeper.services.payout_sidecar_client import (
    SidecarExecutionNotFound,
    SidecarStatusUnavailable,
    SidecarSubmitTimeout,
)


VALID_TRON_DESTINATION = "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb"


class FakeSidecarClient:
    def __init__(self):
        self.calls = []
        self.preflight_response = {"status": "OK"}
        self.submit_response = {
            "status": "ACCEPTED",
            "sidecar_execution_id": "sidecar-1",
            "sidecar_state": "RECEIVED",
            "sidecar_state_version": 1,
            "sidecar_state_transition_id": "sidecar-transition-1",
            "state_updated_at": "2026-06-03T10:00:00Z",
        }
        self.status_response = {
            "status": "OK",
            "sidecar_execution_id": "sidecar-1",
            "sidecar_state": "RECEIVED",
            "sidecar_state_version": 1,
            "sidecar_state_transition_id": "sidecar-transition-1",
            "state_updated_at": "2026-06-03T10:00:00Z",
        }
        self.raise_on_submit = None
        self.raise_on_status = None
        self.raise_on_preflight = None

    def preflight(self, execution):
        self.calls.append(("preflight", execution.id, execution.state.name))
        self.assert_execution_committed(execution.id)
        if self.raise_on_preflight:
            raise self.raise_on_preflight
        return dict(self.preflight_response)

    def submit(self, execution):
        self.calls.append(("submit", execution.id, execution.state.name))
        self.assert_execution_committed(execution.id)
        if self.raise_on_submit:
            raise self.raise_on_submit
        return self._with_execution_identity(execution, self.submit_response)

    def status(self, execution):
        self.calls.append(("status", execution.id, execution.state.name))
        if self.raise_on_status:
            raise self.raise_on_status
        return self._with_execution_identity(execution, self.status_response)

    @staticmethod
    def assert_execution_committed(execution_id):
        db.session.expire_all()
        assert PayoutExecution.query.get(execution_id) is not None

    @staticmethod
    def _with_execution_identity(execution, response):
        payload = {
            "consumer": execution.consumer,
            "execution_id": execution.id,
            "external_id": execution.external_id,
            "contract_version": execution.contract_version,
            "asset": execution.asset,
            "network": execution.network,
            "request_hash": execution.request_hash,
            "sidecar_payload_hash": execution.sidecar_payload_hash,
        }
        payload.update(response)
        return payload


class PayoutExecutionReconcilerTestCase(unittest.TestCase):
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

    def test_created_execution_is_dispatched_after_durable_commit(self):
        execution = self.create_execution()
        client = FakeSidecarClient()

        count = PayoutExecutionReconciler.dispatch_ready(client=client)

        db.session.refresh(execution)
        self.assertEqual(count, 1)
        self.assertEqual(execution.state, PayoutExecutionState.ENQUEUED)
        self.assertEqual(execution.sidecar_execution_id, "sidecar-1")
        self.assertEqual(execution.sidecar_state, "RECEIVED")
        self.assertEqual(execution.sidecar_state_version, 1)
        self.assertEqual(
            execution.sidecar_state_updated_at.isoformat(),
            "2026-06-03T10:00:00",
        )
        self.assertEqual(
            [call[0] for call in client.calls],
            ["preflight", "submit"],
        )
        self.assertIsNone(execution.lease_owner)
        self.assertIsNone(execution.lease_expires_at)

    def test_created_preflight_structured_503_records_retryable_diagnostic_without_transition(self):
        execution = self.create_execution()
        client = FakeSidecarClient()
        client.raise_on_preflight = SidecarStatusUnavailable(
            "Sidecar preflight endpoint returned HTTP 503",
            status_code=503,
            payload={
                "code": "PROFEEX_ESTIMATE_UNAVAILABLE",
                "message": "Unable to estimate resources",
            },
        )

        PayoutExecutionReconciler.dispatch_ready(client=client)

        db.session.refresh(execution)
        self.assertEqual(execution.state, PayoutExecutionState.CREATED)
        self.assertEqual(execution.event_version, 1)
        self.assertFalse(execution.reconciliation_required)
        self.assertEqual(execution.error_code, "PROFEEX_ESTIMATE_UNAVAILABLE")
        self.assertEqual(execution.error_message, "Unable to estimate resources")
        self.assertIsNotNone(execution.next_dispatch_at)

    def test_reconciler_polls_enqueued_and_broadcast_execution_until_terminal(self):
        execution = self.create_execution()
        client = FakeSidecarClient()

        PayoutExecutionReconciler.dispatch_ready(client=client)
        db.session.refresh(execution)
        self.assertEqual(execution.state, PayoutExecutionState.ENQUEUED)

        client.calls = []
        client.status_response = {
            "status": "OK",
            "sidecar_execution_id": "sidecar-1",
            "sidecar_state": "BROADCASTED",
            "sidecar_state_version": 2,
            "sidecar_state_transition_id": "sidecar-transition-2",
            "state_updated_at": "2026-06-03T10:01:00Z",
            "txids": ["tx-2"],
        }
        broadcast_count = PayoutExecutionReconciler.dispatch_ready(client=client)

        db.session.refresh(execution)
        self.assertEqual(broadcast_count, 1)
        self.assertEqual([call[0] for call in client.calls], ["status"])
        self.assertEqual(execution.state, PayoutExecutionState.BROADCAST)
        self.assertEqual(execution.txids_json, '["tx-2"]')

        client.calls = []
        client.status_response = {
            "status": "OK",
            "sidecar_execution_id": "sidecar-1",
            "sidecar_state": "CONFIRMED",
            "sidecar_state_version": 3,
            "sidecar_state_transition_id": "sidecar-transition-3",
            "state_updated_at": "2026-06-03T10:02:00Z",
            "txids": ["tx-2"],
        }
        confirmed_count = PayoutExecutionReconciler.dispatch_ready(client=client)

        db.session.refresh(execution)
        self.assertEqual(confirmed_count, 1)
        self.assertEqual([call[0] for call in client.calls], ["status"])
        self.assertEqual(execution.state, PayoutExecutionState.CONFIRMED)
        self.assertIsNotNone(execution.confirmed_at)

    def test_enqueued_status_unavailable_retries_without_reconciliation(self):
        execution = self.create_execution()
        client = FakeSidecarClient()
        PayoutExecutionReconciler.dispatch_ready(client=client)
        db.session.refresh(execution)
        self.assertEqual(execution.state, PayoutExecutionState.ENQUEUED)

        client.calls = []
        client.raise_on_status = SidecarStatusUnavailable("status timeout")
        count = PayoutExecutionReconciler.dispatch_ready(client=client)

        db.session.refresh(execution)
        self.assertEqual(count, 1)
        self.assertEqual([call[0] for call in client.calls], ["status"])
        self.assertEqual(execution.state, PayoutExecutionState.ENQUEUED)
        self.assertFalse(execution.reconciliation_required)
        self.assertEqual(execution.error_code, "PAYOUT_DISPATCH_EXCEPTION")
        self.assertIsNotNone(execution.next_dispatch_at)

    def test_successful_sidecar_progress_clears_previous_transient_error(self):
        execution = self.create_execution()
        client = FakeSidecarClient()
        PayoutExecutionReconciler.dispatch_ready(client=client)
        db.session.refresh(execution)
        self.assertEqual(execution.state, PayoutExecutionState.ENQUEUED)

        client.raise_on_status = SidecarStatusUnavailable("status timeout")
        PayoutExecutionReconciler.dispatch_ready(client=client)
        db.session.refresh(execution)
        self.assertEqual(execution.error_code, "PAYOUT_DISPATCH_EXCEPTION")
        execution.next_dispatch_at = None
        db.session.commit()

        client.raise_on_status = None
        client.status_response = {
            "status": "OK",
            "sidecar_execution_id": "sidecar-1",
            "sidecar_state": "BROADCASTED",
            "sidecar_state_version": 2,
            "sidecar_state_transition_id": "sidecar-transition-2",
            "state_updated_at": "2026-06-03T10:01:00Z",
            "txids": ["tx-2"],
        }
        PayoutExecutionReconciler.dispatch_ready(client=client)

        db.session.refresh(execution)
        self.assertEqual(execution.state, PayoutExecutionState.BROADCAST)
        self.assertIsNone(execution.error_code)
        self.assertIsNone(execution.error_message)
        response = PayoutExecutionService.status("grither-pay", execution.external_id)
        self.assertIsNone(response["error_code"])
        self.assertIsNone(response["error_message"])

    def test_outbox_failure_rolls_back_transition_before_lease_release(self):
        from shkeeper.services import payout_callback_outbox

        execution = self.create_execution()
        original_transition_id = execution.state_transition_id
        original_occurred_at = execution.last_state_occurred_at
        client = FakeSidecarClient()
        original = payout_callback_outbox.PayoutCallbackOutbox.add_transition_event

        def fail_after_insert(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("outbox insert failed")

        payout_callback_outbox.PayoutCallbackOutbox.add_transition_event = (
            fail_after_insert
        )
        try:
            count = PayoutExecutionReconciler.dispatch_ready(client=client)
        finally:
            payout_callback_outbox.PayoutCallbackOutbox.add_transition_event = (
                original
            )
            db.session.rollback()

        db.session.refresh(execution)
        self.assertEqual(count, 1)
        self.assertEqual(execution.state, PayoutExecutionState.CREATED)
        self.assertEqual(execution.event_version, 1)
        self.assertEqual(execution.state_transition_id, original_transition_id)
        self.assertEqual(execution.last_state_occurred_at, original_occurred_at)
        self.assertEqual(PayoutCallbackEvent.query.count(), 1)
        self.assertEqual(execution.error_code, "PAYOUT_DISPATCH_EXCEPTION")
        self.assertEqual(execution.error_message, "outbox insert failed")
        self.assertIsNotNone(execution.next_dispatch_at)
        self.assertIsNone(execution.lease_owner)
        self.assertIsNone(execution.lease_token)
        self.assertIsNone(execution.lease_expires_at)

    def test_poison_execution_does_not_block_later_ready_execution(self):
        from shkeeper.services import payout_callback_outbox

        first = self.create_execution("WD-1")
        second = self.create_execution("WD-2")
        client = FakeSidecarClient()
        original = payout_callback_outbox.PayoutCallbackOutbox.add_transition_event

        def fail_first(execution, *args, **kwargs):
            original(execution, *args, **kwargs)
            if execution.id == first.id:
                raise RuntimeError("outbox insert failed")

        payout_callback_outbox.PayoutCallbackOutbox.add_transition_event = (
            fail_first
        )
        try:
            count = PayoutExecutionReconciler.dispatch_ready(client=client)
        finally:
            payout_callback_outbox.PayoutCallbackOutbox.add_transition_event = (
                original
            )
            db.session.rollback()

        db.session.refresh(first)
        db.session.refresh(second)
        self.assertEqual(count, 2)
        self.assertEqual(first.state, PayoutExecutionState.CREATED)
        self.assertEqual(first.error_code, "PAYOUT_DISPATCH_EXCEPTION")
        self.assertEqual(second.state, PayoutExecutionState.ENQUEUED)
        self.assertEqual(second.sidecar_execution_id, "sidecar-1")

    def test_unexpired_lease_is_not_dispatched(self):
        execution = self.create_execution()
        execution.lease_owner = "worker-a"
        execution.lease_expires_at = (
            datetime.now(timezone.utc) + timedelta(minutes=5)
        ).replace(tzinfo=None)
        db.session.commit()
        client = FakeSidecarClient()

        count = PayoutExecutionReconciler.dispatch_ready(client=client)

        db.session.refresh(execution)
        self.assertEqual(count, 0)
        self.assertEqual(execution.state, PayoutExecutionState.CREATED)
        self.assertEqual(client.calls, [])

    def test_stale_release_does_not_clear_newer_lease(self):
        execution = self.create_execution()
        execution.lease_owner = "worker-new"
        execution.lease_token = "token-new"
        execution.lease_expires_at = (
            datetime.now(timezone.utc) + timedelta(minutes=5)
        ).replace(tzinfo=None)
        db.session.commit()

        PayoutExecutionReconciler.release_execution(
            execution,
            lease_owner="worker-old",
            lease_token="token-old",
        )

        db.session.refresh(execution)
        self.assertEqual(execution.lease_owner, "worker-new")
        self.assertEqual(execution.lease_token, "token-new")

    def test_prefighted_execution_retries_submit_safely(self):
        execution = self.create_execution()
        PayoutExecutionService.transition(
            execution,
            PayoutExecutionState.PREFLIGHTED,
        )
        client = FakeSidecarClient()

        PayoutExecutionReconciler.dispatch_ready(client=client)

        db.session.refresh(execution)
        self.assertEqual(execution.state, PayoutExecutionState.ENQUEUED)
        self.assertEqual([call[0] for call in client.calls], ["submit"])

    def test_enqueueing_status_not_found_requires_manual_reconciliation(self):
        execution = self.create_execution()
        PayoutExecutionService.transition(
            execution,
            PayoutExecutionState.ENQUEUEING,
        )
        client = FakeSidecarClient()
        client.raise_on_status = SidecarExecutionNotFound("NO_EXECUTION_CREATED")

        PayoutExecutionReconciler.dispatch_ready(client=client)

        db.session.refresh(execution)
        self.assertEqual(execution.state, PayoutExecutionState.RECONCILIATION_REQUIRED)
        self.assertEqual(execution.failure_class, PayoutFailureClass.AMBIGUOUS)
        self.assertEqual(
            execution.error_code,
            "SIDECAR_EXECUTION_NOT_FOUND_AFTER_SUBMIT_WINDOW",
        )
        self.assertTrue(execution.reconciliation_required)
        self.assertEqual([call[0] for call in client.calls], ["status"])

    def test_enqueueing_status_unavailable_moves_to_reconciliation_required(self):
        execution = self.create_execution()
        PayoutExecutionService.transition(
            execution,
            PayoutExecutionState.ENQUEUEING,
        )
        client = FakeSidecarClient()
        client.raise_on_status = SidecarStatusUnavailable("timeout")

        PayoutExecutionReconciler.dispatch_ready(client=client)

        db.session.refresh(execution)
        self.assertEqual(execution.state, PayoutExecutionState.RECONCILIATION_REQUIRED)
        self.assertEqual(execution.failure_class, PayoutFailureClass.AMBIGUOUS)
        self.assertTrue(execution.reconciliation_required)

    def test_submit_timeout_after_enqueueing_moves_to_reconciliation_required(self):
        execution = self.create_execution()
        client = FakeSidecarClient()
        client.raise_on_submit = SidecarSubmitTimeout("submit timeout")

        PayoutExecutionReconciler.dispatch_ready(client=client)

        db.session.refresh(execution)
        self.assertEqual(execution.state, PayoutExecutionState.RECONCILIATION_REQUIRED)
        self.assertEqual(execution.failure_class, PayoutFailureClass.SIDECAR_TIMEOUT)
        self.assertTrue(execution.reconciliation_required)

    def test_submit_response_missing_ordering_metadata_requires_reconciliation(self):
        execution = self.create_execution()
        client = FakeSidecarClient()
        client.submit_response = {
            "status": "ACCEPTED",
            "sidecar_execution_id": "sidecar-1",
            "sidecar_state": "RECEIVED",
        }

        PayoutExecutionReconciler.dispatch_ready(client=client)

        db.session.refresh(execution)
        self.assertEqual(execution.state, PayoutExecutionState.RECONCILIATION_REQUIRED)
        self.assertEqual(execution.failure_class, PayoutFailureClass.AMBIGUOUS)
        self.assertTrue(execution.reconciliation_required)
        self.assertEqual(execution.error_code, "SIDECAR_STATUS_AMBIGUOUS")

    def test_submit_response_missing_state_updated_at_requires_reconciliation(self):
        execution = self.create_execution()
        client = FakeSidecarClient()
        client.submit_response["state_updated_at"] = None

        PayoutExecutionReconciler.dispatch_ready(client=client)

        db.session.refresh(execution)
        self.assertEqual(execution.state, PayoutExecutionState.RECONCILIATION_REQUIRED)
        self.assertEqual(execution.failure_class, PayoutFailureClass.AMBIGUOUS)
        self.assertTrue(execution.reconciliation_required)
        self.assertEqual(execution.error_code, "SIDECAR_STATUS_AMBIGUOUS")

    def test_stale_sidecar_status_cannot_overwrite_newer_state(self):
        execution = self.create_execution()
        PayoutExecutionService.apply_sidecar_status(
            execution,
            self.sidecar_status(
                execution,
                sidecar_state="BROADCASTED",
                sidecar_state_version=2,
                sidecar_state_transition_id="sidecar-transition-2",
                state_updated_at="2026-06-03T10:01:00Z",
                txids=["tx-2"],
            ),
        )

        PayoutExecutionService.apply_sidecar_status(
            execution,
            self.sidecar_status(
                execution,
                sidecar_state="RECEIVED",
                sidecar_state_version=1,
                sidecar_state_transition_id="sidecar-transition-1",
            ),
        )

        db.session.refresh(execution)
        self.assertEqual(execution.sidecar_state, "BROADCASTED")
        self.assertEqual(execution.sidecar_state_version, 2)
        self.assertEqual(execution.state, PayoutExecutionState.BROADCAST)
        self.assertEqual(execution.txids_json, '["tx-2"]')

    def test_same_shkeeper_state_sidecar_progress_keeps_transition_metadata(self):
        execution = self.create_execution()
        original_utcnow = PayoutExecutionService._utcnow
        try:
            PayoutExecutionService._utcnow = staticmethod(
                lambda: datetime(2026, 6, 3, 10, 0, 0)
            )
            PayoutExecutionService.apply_sidecar_status(
                execution,
                self.sidecar_status(
                    execution,
                    sidecar_state="RECEIVED",
                    sidecar_state_version=1,
                    sidecar_state_transition_id="sidecar-transition-1",
                ),
            )
            first_event_version = execution.event_version
            first_transition_id = execution.state_transition_id
            first_occurred_at = execution.last_state_occurred_at

            PayoutExecutionService._utcnow = staticmethod(
                lambda: datetime(2026, 6, 3, 11, 0, 0)
            )
            PayoutExecutionService.apply_sidecar_status(
                execution,
                self.sidecar_status(
                    execution,
                    sidecar_state="VALIDATED",
                    sidecar_state_version=2,
                    sidecar_state_transition_id="sidecar-transition-2",
                    state_updated_at="2026-06-03T11:00:00Z",
                ),
            )
        finally:
            PayoutExecutionService._utcnow = staticmethod(original_utcnow)

        db.session.refresh(execution)
        self.assertEqual(execution.state, PayoutExecutionState.ENQUEUED)
        self.assertEqual(execution.sidecar_state, "VALIDATED")
        self.assertEqual(execution.sidecar_state_version, 2)
        self.assertEqual(execution.event_version, first_event_version)
        self.assertEqual(execution.state_transition_id, first_transition_id)
        self.assertEqual(execution.last_state_occurred_at, first_occurred_at)

    def test_same_version_conflicting_sidecar_status_requires_reconciliation(self):
        execution = self.create_execution()
        PayoutExecutionService.apply_sidecar_status(
            execution,
            self.sidecar_status(
                execution,
                sidecar_state="RECEIVED",
                sidecar_state_version=1,
                sidecar_state_transition_id="sidecar-transition-1",
            ),
        )

        PayoutExecutionService.apply_sidecar_status(
            execution,
            self.sidecar_status(
                execution,
                sidecar_state="BROADCASTED",
                sidecar_state_version=1,
                sidecar_state_transition_id="sidecar-transition-conflict",
                txids=["tx-conflict"],
            ),
        )

        db.session.refresh(execution)
        self.assertEqual(execution.state, PayoutExecutionState.RECONCILIATION_REQUIRED)
        self.assertEqual(execution.failure_class, PayoutFailureClass.AMBIGUOUS)
        self.assertTrue(execution.reconciliation_required)

    def test_same_version_sidecar_execution_id_change_requires_reconciliation(self):
        execution = self.create_execution()
        PayoutExecutionService.apply_sidecar_status(
            execution,
            self.sidecar_status(
                execution,
                sidecar_execution_id="sidecar-1",
                sidecar_state="RECEIVED",
                sidecar_state_version=1,
                sidecar_state_transition_id="sidecar-transition-1",
            ),
        )

        PayoutExecutionService.apply_sidecar_status(
            execution,
            self.sidecar_status(
                execution,
                sidecar_execution_id="sidecar-2",
                sidecar_state="RECEIVED",
                sidecar_state_version=1,
                sidecar_state_transition_id="sidecar-transition-1",
            ),
        )

        db.session.refresh(execution)
        self.assertEqual(execution.state, PayoutExecutionState.RECONCILIATION_REQUIRED)
        self.assertEqual(execution.failure_class, PayoutFailureClass.AMBIGUOUS)
        self.assertTrue(execution.reconciliation_required)

    def test_same_version_state_updated_at_change_requires_reconciliation(self):
        execution = self.create_execution()
        PayoutExecutionService.apply_sidecar_status(
            execution,
            self.sidecar_status(
                execution,
                sidecar_state="RECEIVED",
                sidecar_state_version=1,
                sidecar_state_transition_id="sidecar-transition-1",
                state_updated_at="2026-06-03T10:00:00Z",
            ),
        )

        PayoutExecutionService.apply_sidecar_status(
            execution,
            self.sidecar_status(
                execution,
                sidecar_state="RECEIVED",
                sidecar_state_version=1,
                sidecar_state_transition_id="sidecar-transition-1",
                state_updated_at="2026-06-03T10:01:00Z",
            ),
        )

        db.session.refresh(execution)
        self.assertEqual(execution.state, PayoutExecutionState.RECONCILIATION_REQUIRED)
        self.assertEqual(execution.failure_class, PayoutFailureClass.AMBIGUOUS)
        self.assertTrue(execution.reconciliation_required)

    def test_sidecar_status_evidence_snapshot_is_stored_with_allowlist(self):
        execution = self.create_execution()

        PayoutExecutionService.apply_sidecar_status(
            execution,
            self.sidecar_status(
                execution,
                sidecar_state="SIGNED",
                sidecar_state_version=3,
                sidecar_state_transition_id="sidecar-transition-signed",
                state_updated_at="2026-06-03T10:03:00Z",
                source_wallet="TSourceWallet",
                token_contract="TRC20-USDT",
                chain_id_or_network_id="tron-mainnet",
                nonce_or_seqno="42",
                signed_payload_hash="signed-hash-1",
                signed_payload_stored_at="2026-06-03T10:03:01Z",
                private_key="must-not-be-stored",
            ),
        )

        db.session.refresh(execution)
        evidence = json.loads(execution.last_sidecar_status_json)
        response = PayoutExecutionService.status("grither-pay", execution.external_id)
        self.assertIsNotNone(execution.last_sidecar_status_hash)
        self.assertIsNotNone(execution.last_sidecar_status_observed_at)
        self.assertEqual(evidence["source_wallet"], "TSourceWallet")
        self.assertEqual(evidence["token_contract"], "TRC20-USDT")
        self.assertEqual(evidence["chain_id_or_network_id"], "tron-mainnet")
        self.assertEqual(evidence["nonce_or_seqno"], "42")
        self.assertEqual(evidence["signed_payload_hash"], "signed-hash-1")
        self.assertNotIn("private_key", evidence)
        self.assertEqual(
            response["sidecar_status_hash"],
            execution.last_sidecar_status_hash,
        )
        self.assertEqual(
            response["sidecar_evidence"]["signed_payload_hash"],
            "signed-hash-1",
        )
        self.assertNotIn("private_key", response["sidecar_evidence"])

    def test_same_version_sidecar_evidence_change_requires_reconciliation(self):
        execution = self.create_execution()
        PayoutExecutionService.apply_sidecar_status(
            execution,
            self.sidecar_status(
                execution,
                sidecar_state="SIGNED",
                sidecar_state_version=3,
                sidecar_state_transition_id="sidecar-transition-signed",
                state_updated_at="2026-06-03T10:03:00Z",
                source_wallet="TSourceWallet",
                signed_payload_hash="signed-hash-1",
            ),
        )

        PayoutExecutionService.apply_sidecar_status(
            execution,
            self.sidecar_status(
                execution,
                sidecar_state="SIGNED",
                sidecar_state_version=3,
                sidecar_state_transition_id="sidecar-transition-signed",
                state_updated_at="2026-06-03T10:03:00Z",
                source_wallet="TChangedWallet",
                signed_payload_hash="signed-hash-1",
            ),
        )

        db.session.refresh(execution)
        self.assertEqual(execution.state, PayoutExecutionState.RECONCILIATION_REQUIRED)
        self.assertEqual(execution.failure_class, PayoutFailureClass.AMBIGUOUS)
        self.assertTrue(execution.reconciliation_required)

    def test_sidecar_identity_mismatch_requires_reconciliation(self):
        execution = self.create_execution()
        client = FakeSidecarClient()
        client.submit_response["execution_id"] = execution.id + 1

        PayoutExecutionReconciler.dispatch_ready(client=client)

        db.session.refresh(execution)
        self.assertEqual(execution.state, PayoutExecutionState.RECONCILIATION_REQUIRED)
        self.assertEqual(execution.failure_class, PayoutFailureClass.AMBIGUOUS)
        self.assertEqual(execution.error_code, "SIDECAR_STATUS_IDENTITY_MISMATCH")
        self.assertTrue(execution.reconciliation_required)

    def test_sidecar_missing_payload_hash_requires_reconciliation(self):
        execution = self.create_execution()
        PayoutExecutionService.apply_sidecar_status(
            execution,
            self.sidecar_status(
                execution,
                sidecar_execution_id="sidecar-1",
                sidecar_state="RECEIVED",
                sidecar_state_version=1,
                sidecar_state_transition_id="sidecar-transition-1",
                sidecar_payload_hash=None,
            ),
        )

        db.session.refresh(execution)
        self.assertEqual(execution.state, PayoutExecutionState.RECONCILIATION_REQUIRED)
        self.assertEqual(execution.failure_class, PayoutFailureClass.AMBIGUOUS)
        self.assertEqual(execution.error_code, "SIDECAR_STATUS_IDENTITY_MISMATCH")
        self.assertTrue(execution.reconciliation_required)


if __name__ == "__main__":
    unittest.main()
