import pathlib
import json
import unittest


class AmlContractDocsTestCase(unittest.TestCase):
    def test_aml_docs_include_grither_pay_decision_boundary(self):
        content = pathlib.Path("docs/koinkyt_deposit_gate.md").read_text()

        for required in (
            "AML enrichment",
            "does not emit merchant-facing business",
            "aml.checked",
            "AML_MIN_CHECK_AMOUNT_FIAT=100",
            "AML_SKIP_CUMULATIVE_LIMIT_FIAT=300",
            "transactions[].trigger == true",
            "grither-pay",
            "Koinkyt Deposit Gate",
            "AMLBot is retained only",
            "AML_PROVIDER=koinkyt",
            "GET /openapi/v1/transaction",
            "X-API-Key",
            "AML_SHKEEPER_HOST",
            "KOINKYT_API_KEY",
            "BTC -> blockchain=btc, token=",
            "ETH-USDT -> blockchain=eth, token=USDT",
            "USDC -> blockchain=trx, token=USDC",
            "risk_score",
            "too_many_indirects",
            "/transfer",
        ):
            self.assertIn(required, content)

        for removed in (
            'deposit_decision="credit"',
            'deposit_decision="manual_review"',
            'decision_reason="risk_profile_alert"',
        ):
            self.assertNotIn(removed, content)

    def test_saved_koinkyt_openapi_matches_integration_contract(self):
        spec = json.loads(pathlib.Path("docs/koinkyt_openapi.json").read_text())

        self.assertEqual(
            spec["servers"][0]["url"], "https://explorer.coinkyt.com/openapi/"
        )
        self.assertIn("/v1/transaction", spec["paths"])
        self.assertIn("/v1/transfer", spec["paths"])
        transaction_params = {
            param["name"]
            for param in spec["paths"]["/v1/transaction"]["get"]["parameters"]
        }
        self.assertIn("risk_profile_ids", transaction_params)
        self.assertEqual(spec["components"]["schemas"]["Blockchain"]["enum"], [
            "btc",
            "eth",
            "trx",
        ])
        self.assertEqual(spec["components"]["schemas"]["Token"]["enum"], [
            "",
            "USDT",
            "USDC",
        ])


if __name__ == "__main__":
    unittest.main()
