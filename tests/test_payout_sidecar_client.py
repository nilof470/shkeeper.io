import json
from decimal import Decimal
from types import SimpleNamespace
import unittest

from flask import Flask

from shkeeper.modules.classes.crypto import Crypto
from shkeeper.services import payout_sidecar_client
from shkeeper.services.payout_execution_auth import signature_base, sign_request
from shkeeper.services.payout_sidecar_client import (
    HttpPayoutSidecarClient,
    SidecarExecutionNotFound,
    SidecarStatusUnavailable,
)


class FakeCrypto:
    def gethost(self):
        return "legacy-tron-shkeeper:6000"

    def get_auth_creds(self):
        return ("user", "pass")


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.payload = payload

    def json(self, *args, **kwargs):
        return dict(self.payload)


class NonJsonResponse:
    def __init__(self, status_code):
        self.status_code = status_code

    def json(self, *args, **kwargs):
        raise ValueError("not json")


class PayoutSidecarClientTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            PAYOUT_SIDECAR_REQUEST_TIMEOUT=7,
            PAYOUT_SIDECAR_KEYS={
                "grither-pay": {
                    "test-key": {
                        "secret": "secret",
                        "rails": ["TRON-USDT"],
                    }
                }
            }
        )
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.original_crypto_instances = dict(Crypto.instances)
        self.original_get = payout_sidecar_client.requests.get
        self.original_post = payout_sidecar_client.requests.post
        Crypto.instances.clear()
        Crypto.instances["USDT"] = FakeCrypto()
        self.execution = SimpleNamespace(
            id=123,
            crypto_id="USDT",
            sidecar_service="tron-shkeeper",
            sidecar_symbol="USDT",
            consumer="grither-pay",
            external_id="WD-1",
            asset="USDT",
            network="TRON",
            amount="25.000000",
            destination="TDEST",
            contract_version="usdt-payout-execution-v1",
            request_hash="request-hash",
            sidecar_payload_hash="payload-hash",
            source_wallet_ref="fee_deposit",
            payout_queue="tron_usdt_fee_payouts",
        )

    def tearDown(self):
        Crypto.instances.clear()
        Crypto.instances.update(self.original_crypto_instances)
        payout_sidecar_client.requests.get = self.original_get
        payout_sidecar_client.requests.post = self.original_post
        self.app_context.pop()

    def test_status_404_without_authenticated_not_found_is_unavailable(self):
        payout_sidecar_client.requests.get = lambda *args, **kwargs: FakeResponse(
            404,
            {"code": "ROUTE_NOT_FOUND"},
        )

        with self.assertRaises(SidecarStatusUnavailable):
            HttpPayoutSidecarClient().status(self.execution)

    def test_status_authenticated_no_execution_created_allows_safe_retry(self):
        payout_sidecar_client.requests.get = lambda *args, **kwargs: FakeResponse(
            404,
            {"code": "NO_EXECUTION_CREATED"},
        )

        with self.assertRaises(SidecarExecutionNotFound):
            HttpPayoutSidecarClient().status(self.execution)

    def test_status_non_json_404_is_unavailable(self):
        payout_sidecar_client.requests.get = lambda *args, **kwargs: NonJsonResponse(404)

        with self.assertRaises(SidecarStatusUnavailable):
            HttpPayoutSidecarClient().status(self.execution)

    def test_preflight_http_500_is_unavailable(self):
        payout_sidecar_client.requests.post = lambda *args, **kwargs: FakeResponse(
            500,
            {"message": "unavailable"},
        )

        with self.assertRaises(SidecarStatusUnavailable):
            HttpPayoutSidecarClient().preflight(self.execution)

    def test_preflight_payload_uses_canonical_six_decimal_amount(self):
        captured = {}
        self.execution.amount = Decimal("25")

        def post(*args, **kwargs):
            captured.update(json.loads(kwargs["data"].decode("utf-8")))
            return FakeResponse(200, {"status": "OK"})

        payout_sidecar_client.requests.post = post

        HttpPayoutSidecarClient().preflight(self.execution)

        self.assertEqual(captured["amount"], "25.000000")

    def test_preflight_signs_raw_sidecar_request(self):
        captured = {}

        def post(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return FakeResponse(200, {"status": "OK"})

        payout_sidecar_client.requests.post = post

        HttpPayoutSidecarClient().preflight(self.execution)

        headers = captured["headers"]
        body = captured["data"]
        self.assertEqual(captured["url"], "http://tron-shkeeper:6000/USDT/payout-executions/123/preflight")
        self.assertEqual(captured["auth"], ("user", "pass"))
        self.assertEqual(captured["timeout"], 7)
        self.assertEqual(headers["X-Payout-Consumer"], "grither-pay")
        self.assertEqual(headers["X-Payout-Key-Id"], "test-key")
        base = signature_base(
            headers["X-Payout-Timestamp"],
            headers["X-Payout-Nonce"],
            "POST",
            "/USDT/payout-executions/123/preflight",
            "",
            body,
        )
        self.assertEqual(headers["X-Payout-Signature"], sign_request("secret", base))

    def test_submit_uses_configured_timeout(self):
        captured = {}

        def post(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return FakeResponse(
                200,
                {
                    "status": "ACCEPTED",
                    "sidecar_execution_id": "sidecar-1",
                    "sidecar_state": "RECEIVED",
                    "sidecar_state_version": 1,
                    "sidecar_state_transition_id": "sidecar-transition-1",
                },
            )

        payout_sidecar_client.requests.post = post

        HttpPayoutSidecarClient().submit(self.execution)

        self.assertEqual(captured["url"], "http://tron-shkeeper:6000/USDT/payout-executions/123")
        self.assertEqual(captured["timeout"], 7)

    def test_status_signs_empty_body_sidecar_request(self):
        captured = {}

        def get(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return FakeResponse(200, {"status": "OK"})

        payout_sidecar_client.requests.get = get

        HttpPayoutSidecarClient().status(self.execution)

        headers = captured["headers"]
        self.assertEqual(captured["url"], "http://tron-shkeeper:6000/USDT/payout-executions/123")
        self.assertEqual(captured["timeout"], 7)
        base = signature_base(
            headers["X-Payout-Timestamp"],
            headers["X-Payout-Nonce"],
            "GET",
            "/USDT/payout-executions/123",
            "",
            b"",
        )
        self.assertEqual(headers["X-Payout-Signature"], sign_request("secret", base))

    def test_sidecar_signing_key_requires_explicit_rail_scope(self):
        self.app.config["PAYOUT_SIDECAR_KEYS"] = {
            "grither-pay": {"test-key": {"secret": "secret"}}
        }

        with self.assertRaises(SidecarStatusUnavailable):
            HttpPayoutSidecarClient().preflight(self.execution)

    def test_preflight_uses_sidecar_service_without_legacy_crypto(self):
        Crypto.instances.clear()
        captured = {}

        def post(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return FakeResponse(200, {"status": "OK"})

        payout_sidecar_client.requests.post = post

        HttpPayoutSidecarClient().preflight(self.execution)

        self.assertEqual(
            captured["url"],
            "http://tron-shkeeper:6000/USDT/payout-executions/123/preflight",
        )
        self.assertIsNone(captured["auth"])

    def test_preflight_keeps_legacy_crypto_host_fallback(self):
        del self.execution.sidecar_service
        captured = {}

        def post(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return FakeResponse(200, {"status": "OK"})

        payout_sidecar_client.requests.post = post

        HttpPayoutSidecarClient().preflight(self.execution)

        self.assertEqual(
            captured["url"],
            "http://legacy-tron-shkeeper:6000/USDT/payout-executions/123/preflight",
        )
        self.assertEqual(captured["auth"], ("user", "pass"))

    def test_preflight_accepts_explicit_sidecar_service_port(self):
        self.execution.sidecar_service = "tron-shkeeper:7000"
        captured = {}

        def post(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return FakeResponse(200, {"status": "OK"})

        payout_sidecar_client.requests.post = post

        HttpPayoutSidecarClient().preflight(self.execution)

        self.assertEqual(
            captured["url"],
            "http://tron-shkeeper:7000/USDT/payout-executions/123/preflight",
        )


if __name__ == "__main__":
    unittest.main()
