from pathlib import Path
import unittest


class PayoutTronTemplateTests(unittest.TestCase):
    def test_fee_estimate_includes_destination_address(self):
        template = Path("shkeeper/templates/wallet/payout_tron.j2").read_text()

        self.assertIn('document.querySelector("#paddress").value.trim()', template)
        self.assertIn('"?address="', template)
        self.assertIn("encodeURIComponent(address)", template)

    def test_dropdown_address_selection_recalculates_fee(self):
        template = Path("shkeeper/templates/wallet/payout_tron.j2").read_text()

        self.assertIn(
            'document.querySelector(".dropdown__body").addEventListener',
            template,
        )
        self.assertIn('e.target.closest(".dropdown__text")', template)
        self.assertIn("show_est_fee();", template)

    def test_resource_quote_can_block_send_button_check(self):
        template = Path("shkeeper/templates/wallet/payout_tron.j2").read_text()

        self.assertIn("let quote = data.resource_quote;", template)
        self.assertIn("!quote || quote.submit_ready", template)
        self.assertIn("quote?.blocking_reason", template)
        self.assertIn("if (!payout_resource_ready)", template)
        self.assertIn('alert(document.querySelector("#fee_err").innerText', template)


if __name__ == "__main__":
    unittest.main()
