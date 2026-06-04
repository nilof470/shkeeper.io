import base64
import importlib.util
import json
import time
import unittest
from pathlib import Path

import prometheus_client
from flask import Flask, g

from shkeeper import db
from shkeeper.api_v1 import bp
from shkeeper.modules.classes import ethereum as ethereum_module
from shkeeper.modules.classes import ton as ton_module
from shkeeper.modules.classes import tron_token as tron_token_module
from shkeeper.models import (
    PayoutCallbackEvent,
    PayoutExecution,
    PayoutExecutionResolutionAudit,
    PayoutExecutionState,
    PayoutFailureClass,
    PayoutResolutionStatus,
    PayoutPolicy,
    PayoutRail,
    PayoutRailHotWalletPolicy,
    PayoutRailLegacySpendPolicy,
    User,
    Wallet,
)
from shkeeper.modules.classes.crypto import Crypto
from shkeeper.modules.cryptos.usdt import usdt
from shkeeper.services.payout_contract import canonical_sidecar_payload, hash_payload
from shkeeper.services.payout_execution_auth import (
    PAYOUT_CONSUMER_HEADER,
    PAYOUT_KEY_ID_HEADER,
    PAYOUT_NONCE_HEADER,
    PAYOUT_SIGNATURE_HEADER,
    PAYOUT_TIMESTAMP_HEADER,
    sign_request,
    signature_base,
)
from shkeeper.services.payout_errors import PayoutRequestError
from shkeeper.services.payout_metrics import _clear_payout_metrics
from shkeeper.services.payout_service import PayoutService


VALID_TRON_DESTINATION = "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb"
VALID_TON_DESTINATION = "UQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAJKZ"
VALID_ETH_DESTINATION = "0x0000000000000000000000000000000000000001"


class FakeCrypto:
    def __init__(self):
        self.calls = []

    def mkpayout(self, destination, amount, fee, subtract_fee_from_amount=False):
        self.calls.append(("mkpayout", destination, amount, fee))
        return {"task_id": "task-1"}

    def multipayout(self, payout_list):
        self.calls.append(("multipayout", list(payout_list)))
        return {"task_id": "task-1"}


class FakeAutopayoutCrypto:
    crypto = "USDT"

    def __init__(self, wallet):
        self.wallet = wallet
        self.calls = []

    def balance(self):
        return 10

    def mkpayout(self, destination, amount, fee, subtract_fee_from_amount=False):
        self.calls.append((destination, amount, fee, subtract_fee_from_amount))
        return {"task_id": "task-1"}


class FakeSidecarResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self, parse_float=None):
        return dict(self.payload)


def load_crypto_class(filename, class_name):
    path = (
        Path(__file__).resolve().parents[1]
        / "shkeeper"
        / "modules"
        / "cryptos"
        / filename
    )
    spec = importlib.util.spec_from_file_location(f"test_{class_name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, class_name)


class PayoutExecutionApiTestCase(unittest.TestCase):
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
        self.app.register_blueprint(bp)

        @self.app.before_request
        def set_test_user_context():
            if not hasattr(g, "user"):
                g.user = None

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

    def metrics_text(self):
        return prometheus_client.generate_latest().decode()

    def add_admin_user(self):
        user = User(
            username="admin",
            passhash=User.get_password_hash("secret"),
        )
        db.session.add(user)
        db.session.commit()
        return user

    def admin_basic_auth_headers(self):
        token = base64.b64encode(b"admin:secret").decode("ascii")
        return {"Authorization": f"Basic {token}"}

    def add_tron_rail(self, **overrides):
        values = {
            "consumer": "grither-pay",
            "asset": "USDT",
            "network": "TRON",
            "crypto_id": "USDT",
            "sidecar_service": "tron-shkeeper",
            "sidecar_symbol": "USDT",
            "payout_queue": "tron_usdt_fee_payouts",
            "source_wallet_ref": "fee_deposit",
            "hot_wallet_policy": (
                PayoutRailHotWalletPolicy.CURRENT_SIDECAR_SOURCE_WALLET
            ),
            "legacy_spend_policy": (
                PayoutRailLegacySpendPolicy.BLOCK_AUTOMATIC_BYPASS
            ),
            "execution_enabled": True,
            "decimals": 6,
            "callback_endpoint_id": "grither-pay-payouts",
            "contract_version": "usdt-payout-execution-v1",
        }
        values.update(overrides)
        rail = PayoutRail(
            **values,
        )
        db.session.add(rail)
        db.session.commit()
        return rail

    def add_eth_rail(self, **overrides):
        return self.add_tron_rail(
            network="ETH",
            crypto_id="ETH-USDT",
            sidecar_service="ethereum-shkeeper",
            sidecar_symbol="ETH-USDT",
            payout_queue="eth_usdt_payouts",
            source_wallet_ref="fee_deposit",
            **overrides,
        )

    def add_ton_rail(self, **overrides):
        return self.add_tron_rail(
            network="TON",
            crypto_id="TON-USDT",
            sidecar_service="ton-shkeeper",
            sidecar_symbol="TON-USDT",
            payout_queue="ton_usdt_payouts",
            source_wallet_ref="fee_deposit",
            **overrides,
        )

    def signed_headers(
        self,
        method,
        path,
        body=b"",
        nonce="nonce-1",
        timestamp=None,
        query="",
    ):
        timestamp = int(time.time()) if timestamp is None else timestamp
        base = signature_base(timestamp, nonce, method, path, query, body)
        return {
            PAYOUT_CONSUMER_HEADER: "grither-pay",
            PAYOUT_KEY_ID_HEADER: "test-key",
            PAYOUT_TIMESTAMP_HEADER: str(timestamp),
            PAYOUT_NONCE_HEADER: nonce,
            PAYOUT_SIGNATURE_HEADER: sign_request("secret", base),
        }

    def post_execution(self, payload, nonce="nonce-1"):
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        path = "/api/v1/payout-executions"
        return self.client.post(
            path,
            data=body,
            headers=self.signed_headers("POST", path, body, nonce=nonce),
            content_type="application/json",
        )

    def post_raw_execution(self, body, nonce="nonce-raw"):
        path = "/api/v1/payout-executions"
        return self.client.post(
            path,
            data=body,
            headers=self.signed_headers("POST", path, body, nonce=nonce),
            content_type="application/json",
        )

    def get_execution(self, external_id, nonce="nonce-status"):
        path = f"/api/v1/payout-executions/{external_id}"
        return self.client.get(
            path,
            headers=self.signed_headers("GET", path, b"", nonce=nonce),
        )

    def create_reconciliation_execution(self, external_id="WD-MANUAL-1"):
        self.add_tron_rail()
        response = self.post_execution(
            {
                "external_id": external_id,
                "asset": "USDT",
                "network": "TRON",
                "amount": "25.000000",
                "destination": VALID_TRON_DESTINATION,
            }
        )
        self.assertEqual(response.status_code, 202)
        execution = PayoutExecution.query.filter_by(external_id=external_id).one()
        from shkeeper.services.payout_execution_service import PayoutExecutionService

        PayoutExecutionService.transition(
            execution,
            PayoutExecutionState.RECONCILIATION_REQUIRED,
            failure_class=PayoutFailureClass.AMBIGUOUS,
            error_code="SIDECAR_STATUS_AMBIGUOUS",
            error_message="manual resolution required",
            reconciliation_required=True,
        )
        db.session.refresh(execution)
        return execution

    def manual_resolution_payload(self, execution, **overrides):
        evidence = {
            "network": execution.network,
            "asset": execution.asset,
            "execution_id": execution.id,
            "external_id": execution.external_id,
            "destination": execution.destination,
            "amount": "25.000000",
            "last_state": execution.state.name,
            "last_sidecar_state": execution.sidecar_state,
            "source_wallet": execution.source_wallet_ref,
            "token_contract": "TRC20-USDT",
            "checked_sources": ["tron-fullnode", "tron-indexer"],
            "searched_block_range": {"from": 100, "to": 200},
            "searched_time_range": {
                "from": "2026-06-03T10:00:00Z",
                "to": "2026-06-03T10:10:00Z",
            },
            "matching_transfer_found": False,
            "pending_original_artifact": False,
        }
        evidence.update(overrides.pop("evidence", {}))
        payload = {
            "resolution_status": "SAFE_FOR_MANUAL_PAYOUT",
            "operator_note": "negative chain evidence checked",
            "evidence": evidence,
        }
        payload.update(overrides)
        return payload

    def test_submit_creates_idempotent_execution_before_sidecar_submit(self):
        self.add_tron_rail()

        response = self.post_execution(
            {
                "external_id": "WD-1",
                "asset": "USDT",
                "network": "TRON",
                "amount": "25",
                "destination": VALID_TRON_DESTINATION,
            }
        )

        self.assertEqual(response.status_code, 202)
        data = response.get_json()
        self.assertEqual(data["status"], "ACCEPTED")
        self.assertEqual(data["state"], "CREATED")
        self.assertEqual(data["amount"], "25.000000")
        self.assertEqual(data["source_wallet_ref"], "fee_deposit")
        self.assertEqual(PayoutExecution.query.count(), 1)

    def test_malformed_json_is_rejected_as_client_error(self):
        self.add_tron_rail()

        response = self.post_raw_execution(b'{"external_id":')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "INVALID_JSON")
        self.assertEqual(PayoutExecution.query.count(), 0)

    def test_non_object_json_is_rejected_as_client_error(self):
        self.add_tron_rail()

        response = self.post_raw_execution(b"[]")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "INVALID_PAYOUT_REQUEST")
        self.assertEqual(PayoutExecution.query.count(), 0)

    def test_submit_rejects_network_invalid_destination_before_creation(self):
        invalid_cases = [
            (
                "tron-with-eth-address",
                "TRON",
                self.add_tron_rail,
                "0x0000000000000000000000000000000000000001",
            ),
            (
                "ton-with-tron-address",
                "TON",
                self.add_ton_rail,
                "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb",
            ),
            (
                "ton-bad-checksum",
                "TON",
                self.add_ton_rail,
                "UQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAJKA",
            ),
            ("eth-invalid-address", "ETH", self.add_eth_rail, "not-an-eth-address"),
        ]

        for index, (label, network, add_rail, destination) in enumerate(invalid_cases):
            with self.subTest(label=label):
                add_rail()
                response = self.post_execution(
                    {
                        "external_id": f"WD-BAD-DEST-{label}",
                        "asset": "USDT",
                        "network": network,
                        "amount": "25",
                        "destination": destination,
                    },
                    nonce=f"nonce-bad-dest-{index}",
                )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json()["code"], "INVALID_DESTINATION")
                self.assertEqual(PayoutExecution.query.count(), 0)
                PayoutRail.query.delete()
                db.session.commit()

    def test_missing_hmac_signature_is_unauthorized(self):
        self.add_tron_rail()

        response = self.client.post(
            "/api/v1/payout-executions",
            data=b"{}",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["code"], "PAYOUT_AUTH_MISSING")
        self.assertEqual(PayoutExecution.query.count(), 0)

    def test_tampered_hmac_body_is_forbidden(self):
        self.add_tron_rail()
        signed_body = json.dumps(
            {
                "external_id": "WD-TAMPER",
                "asset": "USDT",
                "network": "TRON",
                "amount": "25",
                "destination": VALID_TRON_DESTINATION,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        tampered_body = json.dumps(
            {
                "external_id": "WD-TAMPER",
                "asset": "USDT",
                "network": "TRON",
                "amount": "26",
                "destination": VALID_TRON_DESTINATION,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        path = "/api/v1/payout-executions"

        response = self.client.post(
            path,
            data=tampered_body,
            headers=self.signed_headers(
                "POST",
                path,
                signed_body,
                nonce="nonce-tampered-body",
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["code"], "PAYOUT_AUTH_INVALID")
        self.assertEqual(PayoutExecution.query.count(), 0)

    def test_hmac_signature_is_bound_to_method_path_and_query(self):
        self.add_tron_rail()
        self.post_execution(
            {
                "external_id": "WD-AUTH-BINDING",
                "asset": "USDT",
                "network": "TRON",
                "amount": "25",
                "destination": VALID_TRON_DESTINATION,
            },
            nonce="nonce-auth-binding-create",
        )

        get_path = "/api/v1/payout-executions/WD-AUTH-BINDING"
        wrong_method_response = self.client.get(
            get_path,
            headers=self.signed_headers(
                "POST",
                "/api/v1/payout-executions",
                b"",
                nonce="nonce-wrong-method-path",
            ),
        )
        wrong_query_response = self.client.get(
            f"{get_path}?unexpected=1",
            headers=self.signed_headers(
                "GET",
                get_path,
                b"",
                nonce="nonce-wrong-query",
            ),
        )

        self.assertEqual(wrong_method_response.status_code, 403)
        self.assertEqual(wrong_method_response.get_json()["code"], "PAYOUT_AUTH_INVALID")
        self.assertEqual(wrong_query_response.status_code, 403)
        self.assertEqual(wrong_query_response.get_json()["code"], "PAYOUT_AUTH_INVALID")
        self.assertEqual(PayoutExecution.query.count(), 1)

    def test_expired_hmac_timestamp_is_forbidden(self):
        self.add_tron_rail()
        body = json.dumps(
            {
                "external_id": "WD-EXPIRED",
                "asset": "USDT",
                "network": "TRON",
                "amount": "25",
                "destination": VALID_TRON_DESTINATION,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        path = "/api/v1/payout-executions"

        response = self.client.post(
            path,
            data=body,
            headers=self.signed_headers(
                "POST",
                path,
                body,
                nonce="nonce-expired",
                timestamp=int(time.time()) - 301,
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["code"], "PAYOUT_AUTH_EXPIRED")
        self.assertEqual(PayoutExecution.query.count(), 0)

    def test_nested_hmac_key_config_allows_configured_rail(self):
        self.app.config["PAYOUT_CONSUMER_KEYS"] = {
            "grither-pay": {
                "test-key": {
                    "secret": "secret",
                    "rails": ["TRON-USDT"],
                },
            },
        }
        self.add_tron_rail()

        response = self.post_execution(
            {
                "external_id": "WD-SCOPED-1",
                "asset": "USDT",
                "network": "TRON",
                "amount": "25",
                "destination": VALID_TRON_DESTINATION,
            }
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(PayoutExecution.query.count(), 1)

    def test_hmac_key_config_rejects_unconfigured_rail(self):
        self.app.config["PAYOUT_CONSUMER_KEYS"] = {
            "grither-pay": {
                "test-key": {
                    "secret": "secret",
                    "rails": ["TRON-USDT"],
                },
            },
        }
        self.add_tron_rail(
            network="TON",
            crypto_id="TON-USDT",
            sidecar_service="ton-shkeeper",
            sidecar_symbol="TON-USDT",
            payout_queue="ton_usdt_payouts",
        )

        response = self.post_execution(
            {
                "external_id": "WD-SCOPED-2",
                "asset": "USDT",
                "network": "TON",
                "amount": "25",
                "destination": VALID_TON_DESTINATION,
            }
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["code"], "PAYOUT_AUTH_RAIL_FORBIDDEN")
        self.assertEqual(PayoutExecution.query.count(), 0)

    def test_sidecar_payload_hash_matches_v1_submit_contract(self):
        self.add_tron_rail()

        self.post_execution(
            {
                "external_id": "WD-1",
                "asset": "USDT",
                "network": "TRON",
                "amount": "25",
                "destination": VALID_TRON_DESTINATION,
            }
        )

        execution = PayoutExecution.query.one()
        expected_hash = hash_payload(
            canonical_sidecar_payload(
                consumer="grither-pay",
                execution_id=execution.id,
                external_id="WD-1",
                asset="USDT",
                network="TRON",
                amount="25.000000",
                destination=VALID_TRON_DESTINATION,
                contract_version="usdt-payout-execution-v1",
            )
        )
        self.assertEqual(execution.sidecar_payload_hash, expected_hash)

    def test_enabled_rail_without_callback_endpoint_is_rejected_before_creation(self):
        self.add_tron_rail(callback_endpoint_id=None)

        response = self.post_execution(
            {
                "external_id": "WD-1",
                "asset": "USDT",
                "network": "TRON",
                "amount": "25",
                "destination": VALID_TRON_DESTINATION,
            }
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.get_json()["code"],
            "PAYOUT_CALLBACK_ENDPOINT_REQUIRED",
        )
        self.assertEqual(PayoutExecution.query.count(), 0)

    def test_unconfigured_callback_endpoint_is_rejected_before_creation(self):
        self.app.config["PAYOUT_CALLBACK_ENDPOINTS"] = {"grither-pay": {}}
        self.add_tron_rail(callback_endpoint_id="missing-endpoint")

        response = self.post_execution(
            {
                "external_id": "WD-1",
                "asset": "USDT",
                "network": "TRON",
                "amount": "25",
                "destination": VALID_TRON_DESTINATION,
            }
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.get_json()["code"],
            "PAYOUT_CALLBACK_ENDPOINT_UNCONFIGURED",
        )
        self.assertEqual(PayoutExecution.query.count(), 0)

    def test_callback_endpoint_does_not_silently_fallback_to_default(self):
        self.app.config["PAYOUT_CALLBACK_ENDPOINTS"] = {
            "grither-pay": {
                "default": "http://grither-pay/default-payout-callbacks",
            },
        }
        self.add_tron_rail(callback_endpoint_id="missing-endpoint")

        response = self.post_execution(
            {
                "external_id": "WD-1",
                "asset": "USDT",
                "network": "TRON",
                "amount": "25",
                "destination": VALID_TRON_DESTINATION,
            }
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.get_json()["code"],
            "PAYOUT_CALLBACK_ENDPOINT_UNCONFIGURED",
        )
        self.assertEqual(PayoutExecution.query.count(), 0)

    def test_invalid_callback_endpoint_url_is_rejected_before_creation(self):
        self.app.config["PAYOUT_CALLBACK_ENDPOINTS"] = {
            "grither-pay": {
                "grither-pay-payouts": "ftp://grither-pay/payout-callbacks",
            },
        }
        self.add_tron_rail()

        response = self.post_execution(
            {
                "external_id": "WD-1",
                "asset": "USDT",
                "network": "TRON",
                "amount": "25",
                "destination": VALID_TRON_DESTINATION,
            }
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.get_json()["code"],
            "PAYOUT_CALLBACK_ENDPOINT_INVALID",
        )
        self.assertEqual(PayoutExecution.query.count(), 0)

    def test_duplicate_submit_same_payload_returns_existing_execution(self):
        self.add_tron_rail()
        payload = {
            "external_id": "WD-1",
            "asset": "USDT",
            "network": "TRON",
            "amount": "25.000000",
            "destination": VALID_TRON_DESTINATION,
        }

        first = self.post_execution(payload, nonce="nonce-1")
        second = self.post_execution(payload, nonce="nonce-2")

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(
            first.get_json()["execution_id"],
            second.get_json()["execution_id"],
        )
        self.assertEqual(PayoutExecution.query.count(), 1)

    def test_duplicate_submit_returns_existing_when_callback_config_is_broken(self):
        self.add_tron_rail()
        payload = {
            "external_id": "WD-1",
            "asset": "USDT",
            "network": "TRON",
            "amount": "25.000000",
            "destination": VALID_TRON_DESTINATION,
        }

        first = self.post_execution(payload, nonce="nonce-1")
        self.app.config["PAYOUT_CALLBACK_ENDPOINTS"] = {"grither-pay": {}}
        second = self.post_execution(payload, nonce="nonce-2")

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(
            first.get_json()["execution_id"],
            second.get_json()["execution_id"],
        )
        self.assertEqual(PayoutExecution.query.count(), 1)

    def test_duplicate_submit_changed_payload_is_rejected(self):
        self.add_tron_rail()
        first = self.post_execution(
            {
                "external_id": "WD-1",
                "asset": "USDT",
                "network": "TRON",
                "amount": "25",
                "destination": VALID_TRON_DESTINATION,
            },
            nonce="nonce-1",
        )
        second = self.post_execution(
            {
                "external_id": "WD-1",
                "asset": "USDT",
                "network": "TRON",
                "amount": "26",
                "destination": VALID_TRON_DESTINATION,
            },
            nonce="nonce-2",
        )

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.get_json()["code"], "PAYOUT_EXECUTION_CONFLICT")
        self.assertEqual(PayoutExecution.query.count(), 1)

    def test_status_is_scoped_by_hmac_consumer(self):
        self.add_tron_rail()
        self.post_execution(
            {
                "external_id": "WD-1",
                "asset": "USDT",
                "network": "TRON",
                "amount": "25",
                "destination": VALID_TRON_DESTINATION,
            },
            nonce="nonce-submit",
        )

        response = self.get_execution("WD-1")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["status"], "OK")
        self.assertEqual(data["external_id"], "WD-1")
        self.assertEqual(data["event_version"], 1)
        self.assertIsNotNone(data["occurred_at"])
        self.assertIsNotNone(data["updated_at"])
        self.assertIsNone(data["sidecar_execution_id"])
        self.assertIsNone(data["sidecar_state_updated_at"])
        self.assertIsNone(data["sidecar_status_hash"])
        self.assertIsNone(data["sidecar_status_observed_at"])
        self.assertEqual(data["sidecar_evidence"], {})

    def test_submit_accepts_large_valid_amount_inside_execution_contract(self):
        self.add_tron_rail()

        first = self.post_execution(
            {
                "external_id": "WD-NO-SHKEEPER-CAP-1",
                "asset": "USDT",
                "network": "TRON",
                "amount": "1000000.000000",
                "destination": VALID_TRON_DESTINATION,
            },
            nonce="nonce-large-execution-amount-1",
        )
        second = self.post_execution(
            {
                "external_id": "WD-NO-SHKEEPER-CAP-2",
                "asset": "USDT",
                "network": "TRON",
                "amount": "1000000.000000",
                "destination": VALID_TRON_DESTINATION,
            },
            nonce="nonce-large-execution-amount-2",
        )

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(PayoutExecution.query.count(), 2)

    def test_submit_rejects_unknown_fields(self):
        self.add_tron_rail()

        response = self.post_execution(
            {
                "external_id": "WD-UNKNOWN-FIELD",
                "asset": "USDT",
                "network": "TRON",
                "amount": "25.000000",
                "destination": VALID_TRON_DESTINATION,
                "unexpected_field": "not part of the execution contract",
            },
            nonce="nonce-unknown-fields",
        )

        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertEqual(data["code"], "INVALID_PAYOUT_REQUEST")
        self.assertIn("unsupported fields: unexpected_field", data["message"])
        self.assertEqual(PayoutExecution.query.count(), 0)

    def test_submit_rejects_multiple_unknown_fields(self):
        self.add_tron_rail()

        response = self.post_execution(
            {
                "external_id": "WD-UNKNOWN-FIELDS",
                "asset": "USDT",
                "network": "TRON",
                "amount": "25.000000",
                "destination": VALID_TRON_DESTINATION,
                "unsupported_alpha": "not part of the execution contract",
                "unsupported_beta": "not part of the execution contract",
            },
            nonce="nonce-multiple-unknown-fields",
        )

        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertEqual(data["code"], "INVALID_PAYOUT_REQUEST")
        self.assertIn("unsupported_alpha", data["message"])
        self.assertIn("unsupported_beta", data["message"])
        self.assertEqual(PayoutExecution.query.count(), 0)

    def test_submit_rejects_non_finite_amounts(self):
        self.add_tron_rail()

        for index, amount in enumerate(("NaN", "Infinity", "-Infinity")):
            response = self.post_execution(
                {
                    "external_id": f"WD-NON-FINITE-{index}",
                    "asset": "USDT",
                    "network": "TRON",
                    "amount": amount,
                    "destination": VALID_TRON_DESTINATION,
                },
                nonce=f"nonce-non-finite-{index}",
            )

            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.get_json()["code"], "INVALID_AMOUNT")

        self.assertEqual(PayoutExecution.query.count(), 0)

    def test_replayed_hmac_nonce_is_rejected(self):
        self.add_tron_rail()
        payload = {
            "external_id": "WD-1",
            "asset": "USDT",
            "network": "TRON",
            "amount": "25",
            "destination": VALID_TRON_DESTINATION,
        }

        first = self.post_execution(payload, nonce="same-nonce")
        second = self.post_execution(payload, nonce="same-nonce")

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 403)
        self.assertEqual(second.get_json()["code"], "PAYOUT_AUTH_REPLAY")
        self.assertEqual(PayoutExecution.query.count(), 1)

    def test_eth_rail_is_disabled_until_owned_fork_is_enabled(self):
        response = self.post_execution(
            {
                "external_id": "WD-ETH-1",
                "asset": "USDT",
                "network": "ETH",
                "amount": "25",
                "destination": VALID_ETH_DESTINATION,
            }
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "PAYOUT_RAIL_DISABLED")
        self.assertEqual(PayoutExecution.query.count(), 0)

    def test_service_origin_legacy_multipayout_is_blocked_for_enabled_rail(self):
        self.add_tron_rail()
        crypto = FakeCrypto()
        Crypto.instances["USDT"] = crypto

        with self.assertRaises(PayoutRequestError) as cm:
            PayoutService.multiple_payout(
                "USDT",
                [{"dest": "TA", "amount": "1"}],
                spend_origin="service",
            )

        self.assertEqual(cm.exception.code, "AUTOMATIC_LEGACY_PAYOUT_BLOCKED")
        self.assertEqual(crypto.calls, [])

    def test_manual_admin_legacy_multipayout_still_works_for_enabled_rail(self):
        self.add_tron_rail()
        crypto = FakeCrypto()
        Crypto.instances["USDT"] = crypto

        response = PayoutService.multiple_payout(
            "USDT",
            [{"dest": "TA", "amount": "1"}],
            spend_origin="manual_admin",
            operator_id="admin",
            audit_reason="manual payout from admin UI",
        )

        self.assertEqual(response["task_id"], "task-1")
        self.assertEqual(crypto.calls[0][0], "multipayout")

    def test_legacy_admin_payout_endpoint_still_works_for_enabled_rail(self):
        self.add_tron_rail()
        self.add_admin_user()
        crypto = FakeCrypto()
        Crypto.instances["USDT"] = crypto

        response = self.client.post(
            "/api/v1/USDT/payout",
            json={"dest": "TA", "amount": "1", "fee": "0"},
            headers=self.admin_basic_auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["task_id"], "task-1")
        self.assertEqual(crypto.calls[0][0], "mkpayout")

    def test_legacy_admin_multipayout_endpoint_still_works_for_enabled_rail(self):
        self.add_tron_rail()
        self.add_admin_user()
        crypto = FakeCrypto()
        Crypto.instances["USDT"] = crypto

        response = self.client.post(
            "/api/v1/USDT/multipayout",
            json=[{"dest": "TA", "amount": "1"}],
            headers=self.admin_basic_auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["task_id"], "task-1")
        self.assertEqual(crypto.calls[0][0], "multipayout")

    def test_manual_admin_shared_wallet_guard_policy_fails_closed(self):
        self.add_tron_rail(
            hot_wallet_policy=(
                PayoutRailHotWalletPolicy
                .CURRENT_SIDECAR_SOURCE_WALLET_WITH_SHARED_GUARD
            ),
            wallet_guard_key="tron-fee-deposit",
        )
        crypto = FakeCrypto()
        Crypto.instances["USDT"] = crypto

        with self.assertRaises(PayoutRequestError) as cm:
            PayoutService.multiple_payout(
                "USDT",
                [{"dest": "TA", "amount": "1"}],
                spend_origin="manual_admin",
                operator_id="admin",
                audit_reason="manual payout from admin UI",
            )

        self.assertEqual(cm.exception.code, "WALLET_GUARD_UNAVAILABLE")
        self.assertEqual(crypto.calls, [])

    def test_direct_tron_crypto_mkpayout_is_blocked_for_enabled_rail(self):
        self.add_tron_rail()
        calls = []
        original_post = tron_token_module.requests.post
        tron_token_module.requests.post = lambda *args, **kwargs: calls.append(args)
        try:
            with self.assertRaises(PayoutRequestError) as cm:
                usdt().mkpayout("TA", "1", "0")
        finally:
            tron_token_module.requests.post = original_post

        self.assertEqual(cm.exception.code, "AUTOMATIC_LEGACY_PAYOUT_BLOCKED")
        self.assertEqual(calls, [])

    def test_direct_tron_crypto_multipayout_is_blocked_for_enabled_rail(self):
        self.add_tron_rail()
        calls = []
        original_post = tron_token_module.requests.post
        tron_token_module.requests.post = lambda *args, **kwargs: calls.append(args)
        try:
            with self.assertRaises(PayoutRequestError) as cm:
                usdt().multipayout([{"dest": "TA", "amount": "1"}])
        finally:
            tron_token_module.requests.post = original_post

        self.assertEqual(cm.exception.code, "AUTOMATIC_LEGACY_PAYOUT_BLOCKED")
        self.assertEqual(calls, [])

    def test_direct_eth_crypto_payouts_are_blocked_for_enabled_rail(self):
        self.add_eth_rail()
        eth_usdt = load_crypto_class("eth-usdt.py", "eth_usdt")
        calls = []
        original_post = ethereum_module.requests.post
        ethereum_module.requests.post = lambda *args, **kwargs: calls.append(args)
        try:
            with self.assertRaises(PayoutRequestError) as single_cm:
                eth_usdt().mkpayout(VALID_ETH_DESTINATION, "1", "0")
            with self.assertRaises(PayoutRequestError) as multi_cm:
                eth_usdt().multipayout(
                    [{"dest": VALID_ETH_DESTINATION, "amount": "1"}]
                )
        finally:
            ethereum_module.requests.post = original_post

        self.assertEqual(single_cm.exception.code, "AUTOMATIC_LEGACY_PAYOUT_BLOCKED")
        self.assertEqual(multi_cm.exception.code, "AUTOMATIC_LEGACY_PAYOUT_BLOCKED")
        self.assertEqual(calls, [])

    def test_direct_ton_crypto_payouts_are_blocked_for_enabled_rail(self):
        self.add_ton_rail()
        ton_usdt = load_crypto_class("ton-usdt.py", "ton_usdt")
        calls = []
        original_ton_post = ton_module.requests.post
        original_eth_post = ethereum_module.requests.post
        ton_module.requests.post = lambda *args, **kwargs: calls.append(args)
        ethereum_module.requests.post = lambda *args, **kwargs: calls.append(args)
        try:
            with self.assertRaises(PayoutRequestError) as single_cm:
                ton_usdt().mkpayout("UQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "1", "0")
            with self.assertRaises(PayoutRequestError) as multi_cm:
                ton_usdt().multipayout(
                    [{"dest": "UQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "amount": "1"}]
                )
        finally:
            ton_module.requests.post = original_ton_post
            ethereum_module.requests.post = original_eth_post

        self.assertEqual(single_cm.exception.code, "AUTOMATIC_LEGACY_PAYOUT_BLOCKED")
        self.assertEqual(multi_cm.exception.code, "AUTOMATIC_LEGACY_PAYOUT_BLOCKED")
        self.assertEqual(calls, [])

    def test_manual_admin_service_context_allows_tron_adapter_multipayout(self):
        self.add_tron_rail()
        Crypto.instances["USDT"] = usdt()
        calls = []
        original_post = tron_token_module.requests.post

        def fake_post(url, *args, **kwargs):
            calls.append((url, kwargs))
            return FakeSidecarResponse({"task_id": "task-1"})

        tron_token_module.requests.post = fake_post
        try:
            response = PayoutService.multiple_payout(
                "USDT",
                [{"dest": "TA", "amount": "1"}],
                spend_origin="manual_admin",
                operator_id="admin",
                audit_reason="manual payout from admin UI",
            )
        finally:
            tron_token_module.requests.post = original_post

        self.assertEqual(response["task_id"], "task-1")
        self.assertEqual(len(calls), 1)

    def test_autopayout_is_blocked_for_enabled_rail(self):
        self.add_tron_rail()
        wallet = Wallet(
            crypto="USDT",
            payout=True,
            pdest="TA",
            pfee="0",
            ppolicy=PayoutPolicy.LIMIT,
            pcond="1",
        )
        db.session.add(wallet)
        db.session.commit()
        crypto = FakeAutopayoutCrypto(wallet)
        Crypto.instances["USDT"] = crypto

        with self.assertRaises(PayoutRequestError) as cm:
            wallet.do_payout()

        self.assertEqual(cm.exception.code, "AUTOMATIC_LEGACY_PAYOUT_BLOCKED")
        self.assertEqual(crypto.calls, [])

    def test_manual_resolution_requires_structured_evidence(self):
        self.add_admin_user()
        execution = self.create_reconciliation_execution()

        response = self.client.post(
            f"/api/v1/payout-executions/{execution.id}/manual-resolution",
            json={"resolution_status": "SAFE_FOR_MANUAL_PAYOUT"},
            headers=self.admin_basic_auth_headers(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["code"],
            "PAYOUT_MANUAL_RESOLUTION_EVIDENCE_REQUIRED",
        )
        db.session.refresh(execution)
        self.assertEqual(execution.state, PayoutExecutionState.RECONCILIATION_REQUIRED)
        self.assertEqual(PayoutExecutionResolutionAudit.query.count(), 0)

    def test_manual_resolution_marks_safe_only_with_negative_evidence_and_audit(self):
        self.add_admin_user()
        execution = self.create_reconciliation_execution()

        response = self.client.post(
            f"/api/v1/payout-executions/{execution.id}/manual-resolution",
            json=self.manual_resolution_payload(execution),
            headers=self.admin_basic_auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        db.session.refresh(execution)
        audit = PayoutExecutionResolutionAudit.query.one()
        self.assertEqual(execution.state, PayoutExecutionState.SAFE_FOR_MANUAL_PAYOUT)
        self.assertEqual(
            execution.resolution_status,
            PayoutResolutionStatus.SAFE_FOR_MANUAL_PAYOUT,
        )
        self.assertEqual(execution.resolved_by, "admin")
        self.assertIsNotNone(execution.resolved_at)
        self.assertFalse(execution.reconciliation_required)
        self.assertEqual(audit.operator_id, "admin")
        self.assertEqual(audit.action, "SAFE_FOR_MANUAL_PAYOUT")
        self.assertEqual(audit.evidence_hash, execution.resolution_evidence_hash)
        self.assertEqual(audit.state_transition_id, execution.state_transition_id)
        callback_event = PayoutCallbackEvent.query.filter_by(
            state_transition_id=audit.state_transition_id
        ).one()
        callback_payload = json.loads(callback_event.raw_payload)
        self.assertEqual(callback_payload["state_transition_id"], audit.state_transition_id)
        self.assertEqual(callback_payload["resolution_status"], "SAFE_FOR_MANUAL_PAYOUT")
        self.assertEqual(data["state"], "SAFE_FOR_MANUAL_PAYOUT")
        self.assertEqual(data["state_transition_id"], audit.state_transition_id)
        self.assertEqual(data["resolution_status"], "SAFE_FOR_MANUAL_PAYOUT")
        self.assertFalse(data["resolution_evidence"]["matching_transfer_found"])

        status_response = self.get_execution(execution.external_id, nonce="manual-status")
        self.assertEqual(status_response.status_code, 200)
        status_data = status_response.get_json()
        self.assertEqual(status_data["state_transition_id"], audit.state_transition_id)
        self.assertEqual(status_data["resolution_status"], "SAFE_FOR_MANUAL_PAYOUT")
        self.assertEqual(status_data["resolved_by"], "admin")

    def test_manual_payout_completion_requires_pending_state_and_tx_evidence(self):
        self.add_admin_user()
        execution = self.create_reconciliation_execution()

        blocked = self.client.post(
            f"/api/v1/payout-executions/{execution.id}/manual-resolution",
            json=self.manual_resolution_payload(
                execution,
                resolution_status="MANUAL_PAYOUT_COMPLETED",
                evidence={
                    "manual_txid_or_message_hash": "manual-tx-1",
                    "manual_payout_source_wallet": "fee_deposit",
                },
            ),
            headers=self.admin_basic_auth_headers(),
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(
            blocked.get_json()["code"],
            "PAYOUT_MANUAL_RESOLUTION_INVALID_STATE",
        )

        safe = self.client.post(
            f"/api/v1/payout-executions/{execution.id}/manual-resolution",
            json=self.manual_resolution_payload(execution),
            headers=self.admin_basic_auth_headers(),
        )
        self.assertEqual(safe.status_code, 200)
        db.session.refresh(execution)
        pending = self.client.post(
            f"/api/v1/payout-executions/{execution.id}/manual-resolution",
            json=self.manual_resolution_payload(
                execution,
                resolution_status="MANUAL_PAYOUT_PENDING",
                evidence={"manual_payout_prepared": True},
            ),
            headers=self.admin_basic_auth_headers(),
        )
        self.assertEqual(pending.status_code, 200)
        db.session.refresh(execution)
        completed = self.client.post(
            f"/api/v1/payout-executions/{execution.id}/manual-resolution",
            json=self.manual_resolution_payload(
                execution,
                resolution_status="MANUAL_PAYOUT_COMPLETED",
                evidence={
                    "manual_txid_or_message_hash": "manual-tx-1",
                    "manual_payout_source_wallet": "fee_deposit",
                },
            ),
            headers=self.admin_basic_auth_headers(),
        )

        self.assertEqual(completed.status_code, 200)
        db.session.refresh(execution)
        self.assertEqual(execution.state, PayoutExecutionState.MANUAL_PAYOUT_COMPLETED)
        self.assertEqual(
            execution.resolution_status,
            PayoutResolutionStatus.MANUAL_PAYOUT_COMPLETED,
        )
        self.assertIsNotNone(execution.terminal_at)
        self.assertEqual(PayoutExecutionResolutionAudit.query.count(), 3)

    def test_manual_resolution_same_state_still_emits_resolution_callback(self):
        self.add_admin_user()
        execution = self.create_reconciliation_execution("WD-MANUAL-CANCEL")
        from shkeeper.services.payout_execution_service import PayoutExecutionService

        PayoutExecutionService.transition(
            execution,
            PayoutExecutionState.FAILED_PRE_BROADCAST,
            failure_class=PayoutFailureClass.PREFLIGHT,
            error_code="PRE_BROADCAST_FAILED",
            error_message="pre-broadcast failure",
            reconciliation_required=False,
        )
        db.session.refresh(execution)
        previous_transition_id = execution.state_transition_id
        previous_event_version = execution.event_version

        response = self.client.post(
            f"/api/v1/payout-executions/{execution.id}/manual-resolution",
            json=self.manual_resolution_payload(
                execution,
                resolution_status="CANCELLED_PRE_BROADCAST",
                evidence={"pre_broadcast_failure_confirmed": True},
            ),
            headers=self.admin_basic_auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        db.session.refresh(execution)
        audit = PayoutExecutionResolutionAudit.query.filter_by(
            action="CANCELLED_PRE_BROADCAST"
        ).one()
        self.assertEqual(execution.state, PayoutExecutionState.FAILED_PRE_BROADCAST)
        self.assertEqual(
            execution.resolution_status,
            PayoutResolutionStatus.CANCELLED_PRE_BROADCAST,
        )
        self.assertNotEqual(execution.state_transition_id, previous_transition_id)
        self.assertEqual(execution.event_version, previous_event_version + 1)
        self.assertEqual(audit.state_transition_id, execution.state_transition_id)
        callback_event = PayoutCallbackEvent.query.filter_by(
            state_transition_id=audit.state_transition_id
        ).one()
        callback_payload = json.loads(callback_event.raw_payload)
        self.assertEqual(callback_payload["previous_state"], "FAILED_PRE_BROADCAST")
        self.assertEqual(callback_payload["state"], "FAILED_PRE_BROADCAST")
        self.assertEqual(callback_payload["resolution_status"], "CANCELLED_PRE_BROADCAST")


if __name__ == "__main__":
    unittest.main()
