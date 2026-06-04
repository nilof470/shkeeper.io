import base64
from datetime import datetime, timedelta
from decimal import Decimal
import unittest

import prometheus_client
from flask import Flask

from shkeeper import db
from shkeeper.modules.classes.crypto import Crypto
from shkeeper.models import (
    PayoutCallbackEvent,
    PayoutExecution,
    PayoutFailureClass,
    PayoutExecutionState,
    PayoutRail,
)
from shkeeper.services.payout_metrics import (
    _clear_payout_metrics,
    update_payout_metrics,
)
from shkeeper.wallet import bp as wallet_bp


class PayoutMetricsTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.app.register_blueprint(wallet_bp)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.client = self.app.test_client()
        self.original_crypto_instances = dict(Crypto.instances)
        Crypto.instances.clear()
        _clear_payout_metrics()

    def tearDown(self):
        _clear_payout_metrics()
        Crypto.instances.clear()
        Crypto.instances.update(self.original_crypto_instances)
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def make_execution(self, external_id, state, created_at, **overrides):
        values = {
            "consumer": "grither-pay",
            "external_id": external_id,
            "contract_version": "usdt-payout-execution-v1",
            "event_version": 1,
            "state_transition_id": f"transition-{external_id}",
            "asset": "USDT",
            "network": "TRON",
            "crypto_id": "USDT",
            "sidecar_service": "tron-shkeeper",
            "sidecar_symbol": "USDT",
            "payout_queue": "tron_usdt_fee_payouts",
            "source_wallet_ref": "fee_deposit",
            "amount": Decimal("25.000000"),
            "destination": "TDEST",
            "request_hash": f"request-hash-{external_id}",
            "sidecar_payload_hash": f"sidecar-hash-{external_id}",
            "state": state,
            "created_at": created_at,
            "updated_at": created_at,
            "txids_json": "[]",
            "message_hashes_json": "[]",
            "reconciliation_required": state
            == PayoutExecutionState.RECONCILIATION_REQUIRED,
        }
        values.update(overrides)
        execution = PayoutExecution(**values)
        db.session.add(execution)
        db.session.commit()
        return execution

    def make_callback_event(self, execution, created_at, dispatch_status="RETRY"):
        event = PayoutCallbackEvent(
            event_id=f"event-{execution.external_id}",
            payout_execution_id=execution.id,
            execution_id=execution.id,
            consumer=execution.consumer,
            external_id=execution.external_id,
            asset=execution.asset,
            network=execution.network,
            event_version=execution.event_version,
            state_transition_id=f"callback-{execution.external_id}",
            created_at=created_at,
            payload_hash=f"payload-hash-{execution.external_id}",
            raw_payload="{}",
            signature_key_id="test-key",
            dispatch_status=dispatch_status,
        )
        db.session.add(event)
        db.session.commit()
        return event

    def make_rail(self, **overrides):
        values = {
            "consumer": "grither-pay",
            "asset": "USDT",
            "network": "TRON",
            "crypto_id": "USDT",
            "sidecar_service": "tron-shkeeper",
            "sidecar_symbol": "USDT",
            "payout_queue": "tron_usdt_fee_payouts",
            "source_wallet_ref": "fee_deposit",
            "execution_enabled": True,
            "callback_endpoint_id": "grither-pay-payouts",
        }
        values.update(overrides)
        rail = PayoutRail(**values)
        db.session.add(rail)
        db.session.commit()
        return rail

    def metrics_text(self):
        return prometheus_client.generate_latest().decode()

    def auth_headers(self):
        token = base64.b64encode(b"shkeeper:shkeeper").decode()
        return {"Authorization": f"Basic {token}"}

    def test_payout_metrics_expose_execution_reconciliation_and_outbox_backlog(self):
        now = datetime(2026, 6, 4, 12, 0, 0)
        self.make_execution(
            "WD-CREATED-1",
            PayoutExecutionState.CREATED,
            now - timedelta(minutes=10),
        )
        self.make_execution(
            "WD-CREATED-2",
            PayoutExecutionState.CREATED,
            now - timedelta(minutes=5),
        )
        reconciliation = self.make_execution(
            "WD-RECON-1",
            PayoutExecutionState.RECONCILIATION_REQUIRED,
            now - timedelta(minutes=30),
        )
        self.make_execution(
            "WD-CONFIRMED-1",
            PayoutExecutionState.CONFIRMED,
            now - timedelta(hours=2),
            reconciliation_required=False,
        )
        self.make_callback_event(
            reconciliation,
            now - timedelta(minutes=20),
            dispatch_status="RETRY",
        )

        update_payout_metrics(now=now)

        text = self.metrics_text()
        self.assertIn(
            'shkeeper_payout_execution_count{asset="USDT",consumer="grither-pay",network="TRON",state="CREATED"} 2.0',
            text,
        )
        self.assertIn(
            'shkeeper_payout_non_terminal_oldest_age_seconds{asset="USDT",consumer="grither-pay",network="TRON",state="CREATED"} 600.0',
            text,
        )
        self.assertIn(
            'shkeeper_payout_reconciliation_required_count{asset="USDT",consumer="grither-pay",network="TRON"} 1.0',
            text,
        )
        self.assertIn(
            'shkeeper_payout_callback_outbox_backlog_count{asset="USDT",consumer="grither-pay",dispatch_status="RETRY",network="TRON"} 1.0',
            text,
        )
        self.assertIn(
            'shkeeper_payout_callback_outbox_oldest_age_seconds{asset="USDT",consumer="grither-pay",dispatch_status="RETRY",network="TRON"} 1200.0',
            text,
        )
        self.assertNotIn(
            'shkeeper_payout_non_terminal_oldest_age_seconds{asset="USDT",consumer="grither-pay",network="TRON",state="CONFIRMED"}',
            text,
        )

    def test_payout_metrics_expose_failure_dispatch_backlog_and_stuck_age(self):
        now = datetime(2026, 6, 4, 12, 0, 0)
        self.make_execution(
            "WD-BACKLOG-1",
            PayoutExecutionState.ENQUEUEING,
            now - timedelta(minutes=12),
            next_dispatch_at=now - timedelta(minutes=1),
        )
        self.make_execution(
            "WD-BACKLOG-FUTURE",
            PayoutExecutionState.ENQUEUEING,
            now - timedelta(minutes=30),
            next_dispatch_at=now + timedelta(minutes=1),
        )
        self.make_execution(
            "WD-FAIL-1",
            PayoutExecutionState.FAILED_PRE_BROADCAST,
            now - timedelta(minutes=3),
            failure_class=PayoutFailureClass.PREFLIGHT,
            error_code="SIDECAR_PREFLIGHT_FAILED",
            reconciliation_required=False,
        )
        self.make_execution(
            "WD-FAIL-RAW-1",
            PayoutExecutionState.FAILED_PRE_BROADCAST,
            now - timedelta(minutes=4),
            failure_class=PayoutFailureClass.PREFLIGHT,
            error_code="raw sidecar error for TDEST",
            reconciliation_required=False,
        )
        self.make_execution(
            "WD-FAIL-RAW-2",
            PayoutExecutionState.FAILED_PRE_BROADCAST,
            now - timedelta(minutes=5),
            failure_class=PayoutFailureClass.PREFLIGHT,
            error_code="provider timeout: request id 123",
            reconciliation_required=False,
        )

        update_payout_metrics(now=now)

        text = self.metrics_text()
        self.assertIn(
            'shkeeper_payout_failure_count{asset="USDT",consumer="grither-pay",error_code="SIDECAR_PREFLIGHT_FAILED",failure_class="PREFLIGHT",network="TRON",state="FAILED_PRE_BROADCAST"} 1.0',
            text,
        )
        self.assertIn(
            'shkeeper_payout_failure_count{asset="USDT",consumer="grither-pay",error_code="OTHER",failure_class="PREFLIGHT",network="TRON",state="FAILED_PRE_BROADCAST"} 2.0',
            text,
        )
        self.assertNotIn("raw sidecar error for TDEST", text)
        self.assertNotIn("provider timeout: request id 123", text)
        self.assertIn(
            'shkeeper_payout_dispatch_backlog_count{asset="USDT",consumer="grither-pay",network="TRON",payout_queue="tron_usdt_fee_payouts",state="ENQUEUEING"} 1.0',
            text,
        )
        self.assertIn(
            'shkeeper_payout_dispatch_backlog_oldest_age_seconds{asset="USDT",consumer="grither-pay",network="TRON",payout_queue="tron_usdt_fee_payouts",state="ENQUEUEING"} 720.0',
            text,
        )
        self.assertIn(
            'shkeeper_payout_stuck_execution_count{asset="USDT",consumer="grither-pay",network="TRON",state="ENQUEUEING",threshold_seconds="300"} 2.0',
            text,
        )
        self.assertIn(
            'shkeeper_payout_stuck_execution_oldest_age_seconds{asset="USDT",consumer="grither-pay",network="TRON",state="ENQUEUEING",threshold_seconds="300"} 1800.0',
            text,
        )

    def test_payout_metrics_expose_confirmation_sla_and_ordering_conflicts(self):
        now = datetime(2026, 6, 4, 12, 0, 0)
        self.make_execution(
            "WD-BROADCAST-SLA",
            PayoutExecutionState.BROADCAST,
            now - timedelta(hours=2),
            updated_at=now - timedelta(minutes=1),
            broadcasted_at=now - timedelta(hours=2),
        )
        self.make_execution(
            "WD-BROADCAST-FRESH",
            PayoutExecutionState.BROADCAST,
            now - timedelta(minutes=10),
            updated_at=now - timedelta(minutes=1),
            broadcasted_at=now - timedelta(minutes=10),
        )
        self.make_execution(
            "WD-ORDERING",
            PayoutExecutionState.RECONCILIATION_REQUIRED,
            now - timedelta(minutes=5),
            failure_class=PayoutFailureClass.AMBIGUOUS,
            error_code="SIDECAR_STATUS_AMBIGUOUS",
            reconciliation_required=True,
        )

        update_payout_metrics(now=now)

        text = self.metrics_text()
        self.assertIn(
            'shkeeper_payout_confirmation_sla_breach_count{asset="USDT",consumer="grither-pay",network="TRON",threshold_seconds="3600"} 1.0',
            text,
        )
        self.assertIn(
            'shkeeper_payout_confirmation_sla_breach_oldest_age_seconds{asset="USDT",consumer="grither-pay",network="TRON",threshold_seconds="3600"} 7200.0',
            text,
        )
        self.assertIn(
            'shkeeper_payout_ordering_conflict_count{asset="USDT",consumer="grither-pay",error_code="SIDECAR_STATUS_AMBIGUOUS",network="TRON"} 1.0',
            text,
        )
        self.assertNotIn(
            'shkeeper_payout_stuck_execution_count{asset="USDT",consumer="grither-pay",network="TRON",state="BROADCAST",threshold_seconds="3600"}',
            text,
        )

    def test_payout_metrics_expose_rail_enablement_only(self):
        self.make_rail()

        update_payout_metrics(now=datetime(2026, 6, 4, 12, 0, 0))

        text = self.metrics_text()
        self.assertIn(
            'shkeeper_payout_rail_enabled{asset="USDT",consumer="grither-pay",network="TRON",payout_queue="tron_usdt_fee_payouts"} 1.0',
            text,
        )
        self.assertNotIn("shkeeper_payout_rail_limit_amount{", text)

    def test_payout_metrics_preserve_last_successful_snapshot_when_collection_fails(self):
        now = datetime(2026, 6, 4, 12, 0, 0)
        self.make_execution(
            "WD-RECON-1",
            PayoutExecutionState.RECONCILIATION_REQUIRED,
            now - timedelta(minutes=30),
        )
        update_payout_metrics(now=now)

        original_query = db.session.query
        db.session.query = lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("db unavailable")
        )
        try:
            with self.assertRaises(RuntimeError):
                update_payout_metrics(now=now + timedelta(minutes=1))
        finally:
            db.session.query = original_query

        text = self.metrics_text()
        self.assertIn(
            'shkeeper_payout_execution_count{asset="USDT",consumer="grither-pay",network="TRON",state="RECONCILIATION_REQUIRED"} 1.0',
            text,
        )
        self.assertIn(
            'shkeeper_payout_reconciliation_required_count{asset="USDT",consumer="grither-pay",network="TRON"} 1.0',
            text,
        )

    def test_metrics_endpoint_fails_open_when_payout_metrics_collection_fails(self):
        import shkeeper.wallet as wallet_module

        original_update = wallet_module.update_payout_metrics
        wallet_module.update_payout_metrics = lambda: (_ for _ in ()).throw(
            RuntimeError("db unavailable")
        )
        try:
            response = self.client.get("/metrics", headers=self.auth_headers())
        finally:
            wallet_module.update_payout_metrics = original_update

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/plain", response.content_type)


if __name__ == "__main__":
    unittest.main()
