import pathlib
import unittest


class AmlContractDocsTestCase(unittest.TestCase):
    def test_aml_docs_include_grither_pay_credit_rule(self):
        content = pathlib.Path("docs/amlbot_deposit_gate.md").read_text()

        for required in (
            "deposit_decision",
            "manual_review",
            "amount_below_aml_threshold",
            "AML_MIN_CHECK_AMOUNT_FIAT=100",
            "AML_SKIP_CUMULATIVE_LIMIT_FIAT=300",
            "transactions[].trigger == true",
            "grither-pay",
        ):
            self.assertIn(required, content)


if __name__ == "__main__":
    unittest.main()
