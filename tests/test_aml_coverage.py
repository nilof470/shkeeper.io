import unittest

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
        self.assertEqual(policy["status"], "amlbot_supported")
        self.assertEqual(policy["provider"], "amlbot")
        self.assertEqual(policy["asset"], "ETH")
        self.assertEqual(policy["network"], "ETHEREUM")

    def test_unsupported_mapping_fails_closed(self):
        policy = get_coverage_policy("BTC-LIGHTNING")
        self.assertEqual(policy["status"], "unsupported_manual_review")
        self.assertEqual(policy["reason"], "limited_analysis_requires_review")


if __name__ == "__main__":
    unittest.main()
