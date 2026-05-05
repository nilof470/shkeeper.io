import unittest

from flask import Flask

import shkeeper.modules.cryptos  # noqa: F401
from shkeeper.modules.classes.crypto import Crypto
from shkeeper.services.aml_coverage import AML_COVERAGE, get_coverage_policy


class AmlCoverageTestCase(unittest.TestCase):
    def test_enabled_crypto_instances_have_explicit_coverage(self):
        missing = [symbol for symbol in Crypto.instances if symbol not in AML_COVERAGE]
        self.assertEqual(missing, [])

    def test_known_supported_and_unsupported_assets_are_explicit(self):
        for symbol in ("BTC", "LTC", "DOGE", "ETH-USDT", "BTC-LIGHTNING", "XMR"):
            self.assertIn(symbol, AML_COVERAGE)

    def test_supported_mapping_contains_provider_asset_and_network(self):
        policy = get_coverage_policy("ETH-USDT")
        self.assertEqual(policy["status"], "provider_supported")
        self.assertEqual(policy["provider"], "koinkyt")
        self.assertEqual(policy["asset"], "USDT")
        self.assertEqual(policy["network"], "ETHEREUM")

    def test_amlbot_provider_preserves_legacy_supported_assets(self):
        app = Flask(__name__)
        app.config["AML_PROVIDER"] = "amlbot"
        with app.app_context():
            policy = get_coverage_policy("DOGE")

        self.assertEqual(policy["status"], "provider_supported")
        self.assertEqual(policy["provider"], "amlbot")
        self.assertEqual(policy["asset"], "DOGE")
        self.assertEqual(policy["network"], "DOGE")

    def test_unsupported_mapping_fails_closed(self):
        policy = get_coverage_policy("BTC-LIGHTNING")
        self.assertEqual(policy["status"], "unsupported_manual_review")
        self.assertEqual(policy["reason"], "limited_analysis_requires_review")


if __name__ == "__main__":
    unittest.main()
