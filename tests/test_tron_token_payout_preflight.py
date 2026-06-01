from decimal import Decimal
import os
import unittest
from unittest.mock import patch

from shkeeper.modules.cryptos.usdc import usdc
from shkeeper.modules.cryptos.usdt import usdt
from shkeeper.modules.classes import tron_token
from shkeeper.services.payout_errors import (
    PayoutDestinationNotActivatedError,
    PayoutRequestError,
    PayoutResourceUnavailableError,
)


DESTINATION = "TY4ZLVFpNhpozeWYSqWpcQjv6vntfHnjA7"


class FakeResponse:
    def __init__(self, data=None, *, status_code=200, json_error=None):
        self.data = data
        self.status_code = status_code
        self.json_error = json_error

    def json(self, parse_float=None):
        if self.json_error:
            raise self.json_error
        return self.data


class TronTokenPayoutPreflightTestCase(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {
                "TRON_API_SERVER_HOST": "tron-sidecar",
                "TRON_API_SERVER_PORT": "6001",
            },
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_estimate_tx_fee_passes_destination_to_sidecar(self):
        crypto = usdt()

        with patch.object(
            tron_token.requests,
            "post",
            return_value=FakeResponse({"fee": "0"}),
        ) as post:
            result = crypto.estimate_tx_fee(Decimal("1.25"), address=DESTINATION)

        self.assertEqual(result, {"fee": "0"})
        self.assertEqual(
            post.call_args.kwargs["params"],
            {"address": DESTINATION},
        )
        self.assertEqual(post.call_args.kwargs["timeout"], 10)

    def test_estimate_tx_fee_maps_sidecar_4xx_error(self):
        crypto = usdt()

        with patch.object(
            tron_token.requests,
            "post",
            return_value=FakeResponse(
                {
                    "status": "error",
                    "code": "INVALID_DESTINATION",
                    "message": "Bad destination address",
                },
                status_code=400,
            ),
        ):
            with self.assertRaises(PayoutRequestError) as cm:
                crypto.estimate_tx_fee(Decimal("1.25"), address=DESTINATION)

        self.assertEqual(cm.exception.code, "INVALID_DESTINATION")
        self.assertEqual(cm.exception.status_code, 400)

    def test_estimate_tx_fee_maps_sidecar_error_body(self):
        crypto = usdt()

        with patch.object(
            tron_token.requests,
            "post",
            return_value=FakeResponse(
                {
                    "status": "error",
                    "code": "PROVIDER_UNAVAILABLE",
                    "message": "No energy provider is configured",
                },
            ),
        ):
            with self.assertRaises(PayoutResourceUnavailableError):
                crypto.estimate_tx_fee(Decimal("1.25"), address=DESTINATION)

    def test_can_omit_fee_only_for_usdt(self):
        self.assertTrue(usdt().can_omit_fee_for_payout())
        self.assertFalse(usdc().can_omit_fee_for_payout())

    def test_preflight_blocks_when_usdt_balance_is_too_low(self):
        crypto = usdt()

        with patch.object(
            tron_token.requests,
            "post",
            return_value=FakeResponse({"balance": "1.00"}),
        ) as post:
            with self.assertRaises(PayoutRequestError) as cm:
                crypto.preflight_payout(DESTINATION, Decimal("1.25"))

        self.assertEqual(cm.exception.code, "INSUFFICIENT_BALANCE")
        self.assertEqual(post.call_count, 1)

    def test_preflight_blocks_destination_not_activated_quote(self):
        crypto = usdt()
        quote = {
            "fee": "0",
            "resource_quote": {
                "submit_ready": False,
                "blocking_code": "DESTINATION_NOT_ACTIVATED",
                "blocking_reason": "TRON payout destination is not activated",
            },
        }

        with patch.object(
            tron_token.requests,
            "post",
            side_effect=[
                FakeResponse({"balance": "10.00"}),
                FakeResponse(quote),
            ],
        ):
            with self.assertRaises(PayoutDestinationNotActivatedError):
                crypto.preflight_payout(DESTINATION, Decimal("1.25"))

    def test_preflight_blocks_provider_unavailable_quote_as_503(self):
        crypto = usdt()
        quote = {
            "fee": "0",
            "resource_quote": {
                "submit_ready": False,
                "blocking_code": "PROVIDER_UNAVAILABLE",
                "blocking_reason": "No energy provider is configured",
            },
        }

        with patch.object(
            tron_token.requests,
            "post",
            side_effect=[
                FakeResponse({"balance": "10.00"}),
                FakeResponse(quote),
            ],
        ):
            with self.assertRaises(PayoutResourceUnavailableError) as cm:
                crypto.preflight_payout(DESTINATION, Decimal("1.25"))

        self.assertEqual(cm.exception.status_code, 503)

    def test_preflight_blocks_missing_payout_worker_as_503(self):
        crypto = usdt()
        quote = {
            "fee": "0",
            "resource_quote": {
                "submit_ready": False,
                "blocking_code": "PAYOUT_WORKER_UNAVAILABLE",
                "blocking_reason": "TRON USDT payout worker is not ready",
            },
        }

        with patch.object(
            tron_token.requests,
            "post",
            side_effect=[
                FakeResponse({"balance": "10.00"}),
                FakeResponse(quote),
            ],
        ):
            with self.assertRaises(PayoutResourceUnavailableError) as cm:
                crypto.preflight_payout(DESTINATION, Decimal("1.25"))

        self.assertEqual(cm.exception.code, "PAYOUT_RESOURCE_UNAVAILABLE")
        self.assertEqual(cm.exception.status_code, 503)

    def test_preflight_allows_ready_resource_quote(self):
        crypto = usdt()
        quote = {
            "fee": "0",
            "resource_quote": {
                "submit_ready": True,
            },
        }

        with patch.object(
            tron_token.requests,
            "post",
            side_effect=[
                FakeResponse({"balance": "10.00"}),
                FakeResponse(quote),
            ],
        ) as post:
            crypto.preflight_payout(DESTINATION, Decimal("1.25"))

        self.assertEqual(post.call_count, 2)
        self.assertEqual(
            post.call_args_list[1].kwargs["params"],
            {"address": DESTINATION},
        )

    def test_preflight_blocks_legacy_static_fee_response(self):
        crypto = usdt()

        with patch.object(
            tron_token.requests,
            "post",
            side_effect=[
                FakeResponse({"balance": "10.00"}),
                FakeResponse({"fee": "40"}),
            ],
        ):
            with self.assertRaises(PayoutResourceUnavailableError):
                crypto.preflight_payout(DESTINATION, Decimal("1.25"))

    def test_preflight_ignores_non_usdt_tokens(self):
        crypto = usdc()

        with patch.object(tron_token.requests, "post") as post:
            crypto.preflight_payout(DESTINATION, Decimal("1.25"))

        post.assert_not_called()

    def test_preflight_maps_sidecar_estimate_outage_to_resource_error(self):
        crypto = usdt()

        with patch.object(
            tron_token.requests,
            "post",
            side_effect=[
                FakeResponse({"balance": "10.00"}),
                FakeResponse({"status": "error"}, status_code=503),
            ],
        ):
            with self.assertRaises(PayoutResourceUnavailableError):
                crypto.preflight_payout(DESTINATION, Decimal("1.25"))

    def test_preflight_maps_sidecar_invalid_destination_error(self):
        crypto = usdt()

        with patch.object(
            tron_token.requests,
            "post",
            side_effect=[
                FakeResponse({"balance": "10.00"}),
                FakeResponse(
                    {
                        "status": "error",
                        "code": "INVALID_DESTINATION",
                        "message": "Bad destination address",
                    },
                    status_code=400,
                ),
            ],
        ):
            with self.assertRaises(PayoutRequestError) as cm:
                crypto.preflight_payout(DESTINATION, Decimal("1.25"))

        self.assertEqual(cm.exception.code, "INVALID_DESTINATION")


if __name__ == "__main__":
    unittest.main()
