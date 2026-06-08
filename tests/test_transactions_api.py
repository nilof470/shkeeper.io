import unittest
from decimal import Decimal

from flask import Flask

from shkeeper import db
from shkeeper.api_v1 import bp
from shkeeper.models import (
    Invoice,
    InvoiceAddress,
    InvoiceStatus,
    Transaction,
    UnconfirmedTransaction,
    Wallet,
)


class TransactionsApiTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.app.register_blueprint(bp)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        db.session.add(Wallet(crypto="ETH-USDT", apikey="api-key"))
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_address_transactions_exclude_outgoing_sweeps(self):
        deposit_addr = "0x88C9DD4592Afbe26cdeCd967b629D6E3Da535735"
        incoming_invoice = Invoice(
            external_id="gpw-u2-usdt-erc20-007ead413c9e",
            fiat="USD",
            crypto="ETH-USDT",
            addr=deposit_addr,
            amount_fiat=Decimal("1000000"),
            amount_crypto=Decimal("1020000"),
            exchange_rate=Decimal("1"),
            status=InvoiceStatus.PARTIAL,
        )
        outgoing_invoice = Invoice(
            fiat="USD",
            addr=deposit_addr,
            status=InvoiceStatus.OUTGOING,
        )
        db.session.add_all([incoming_invoice, outgoing_invoice])
        db.session.commit()
        db.session.add(
            InvoiceAddress(
                invoice_id=incoming_invoice.id,
                crypto="ETH-USDT",
                addr=deposit_addr,
            )
        )
        db.session.add_all(
            [
                Transaction(
                    invoice_id=incoming_invoice.id,
                    txid="0xf192f6d5f3a2b2cb2f7cff7d934f39a14f670ec9fd6885f4800c722e7f2aa021",
                    crypto="ETH-USDT",
                    amount_crypto=Decimal("6"),
                    amount_fiat=Decimal("6"),
                    need_more_confirmations=False,
                ),
                Transaction(
                    invoice_id=outgoing_invoice.id,
                    txid="0x5d482f31421bee038ac592d8465f21f11fee7121b354bed5054ff109c5e2caa1",
                    crypto="ETH-USDT",
                    amount_crypto=Decimal("0"),
                    amount_fiat=Decimal("0"),
                    need_more_confirmations=False,
                    callback_confirmed=True,
                ),
            ]
        )
        db.session.commit()

        response = self.client.get(
            f"/api/v1/transactions/ETH-USDT/{deposit_addr}",
            headers={"X-Shkeeper-Api-Key": "api-key"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(
            [tx["txid"] for tx in payload["transactions"]],
            [
                "0xf192f6d5f3a2b2cb2f7cff7d934f39a14f670ec9fd6885f4800c722e7f2aa021",
            ],
        )

    def test_address_transactions_match_invoice_address_for_requested_crypto(self):
        eth_addr = "0x88C9DD4592Afbe26cdeCd967b629D6E3Da535735"
        tron_addr = "TKuG6z2Ewr6EPQHPAv2mQQ5rASX5KwiPuS"
        invoice = Invoice(
            external_id="gpw-u2-usdt-erc20-007ead413c9e",
            fiat="USD",
            crypto="ETH-USDT",
            addr=eth_addr,
            amount_fiat=Decimal("1000000"),
            amount_crypto=Decimal("1020000"),
            exchange_rate=Decimal("1"),
            status=InvoiceStatus.PARTIAL,
        )
        db.session.add(invoice)
        db.session.commit()
        db.session.add_all(
            [
                InvoiceAddress(invoice_id=invoice.id, crypto="ETH-USDT", addr=eth_addr),
                InvoiceAddress(invoice_id=invoice.id, crypto="USDT", addr=tron_addr),
                Transaction(
                    invoice_id=invoice.id,
                    txid="0xf192f6d5f3a2b2cb2f7cff7d934f39a14f670ec9fd6885f4800c722e7f2aa021",
                    crypto="ETH-USDT",
                    amount_crypto=Decimal("6"),
                    amount_fiat=Decimal("6"),
                    need_more_confirmations=False,
                ),
            ]
        )
        db.session.commit()

        response = self.client.get(
            f"/api/v1/transactions/ETH-USDT/{tron_addr}",
            headers={"X-Shkeeper-Api-Key": "api-key"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["transactions"], [])

    def test_address_transactions_match_invoice_address_for_token_crypto(self):
        eth_addr = "0x88C9DD4592Afbe26cdeCd967b629D6E3Da535735"
        token_addr = "0xTokenDepositAddress000000000000000000000001"
        invoice = Invoice(
            external_id="gpw-u2-usdt-erc20-007ead413c9e",
            fiat="USD",
            crypto="ETH",
            addr=eth_addr,
            amount_fiat=Decimal("1000000"),
            amount_crypto=Decimal("1020000"),
            exchange_rate=Decimal("1"),
            status=InvoiceStatus.PARTIAL,
        )
        db.session.add(invoice)
        db.session.commit()
        db.session.add_all(
            [
                InvoiceAddress(invoice_id=invoice.id, crypto="ETH-USDT", addr=token_addr),
                Transaction(
                    invoice_id=invoice.id,
                    txid="0xf192f6d5f3a2b2cb2f7cff7d934f39a14f670ec9fd6885f4800c722e7f2aa021",
                    crypto="ETH-USDT",
                    amount_crypto=Decimal("6"),
                    amount_fiat=Decimal("6"),
                    need_more_confirmations=False,
                ),
            ]
        )
        db.session.commit()

        response = self.client.get(
            f"/api/v1/transactions/ETH-USDT/{token_addr}",
            headers={"X-Shkeeper-Api-Key": "api-key"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(
            [tx["txid"] for tx in payload["transactions"]],
            [
                "0xf192f6d5f3a2b2cb2f7cff7d934f39a14f670ec9fd6885f4800c722e7f2aa021",
            ],
        )

    def test_address_transactions_keep_legacy_invoice_addr_match(self):
        deposit_addr = "0x88C9DD4592Afbe26cdeCd967b629D6E3Da535735"
        invoice = Invoice(
            external_id="gpw-u2-usdt-erc20-legacy",
            fiat="USD",
            crypto="ETH-USDT",
            addr=deposit_addr,
            amount_fiat=Decimal("1000000"),
            amount_crypto=Decimal("1020000"),
            exchange_rate=Decimal("1"),
            status=InvoiceStatus.PARTIAL,
        )
        db.session.add(invoice)
        db.session.commit()
        db.session.add(
            Transaction(
                invoice_id=invoice.id,
                txid="0xlegacyinvoiceaddr",
                crypto="ETH-USDT",
                amount_crypto=Decimal("6"),
                amount_fiat=Decimal("6"),
                need_more_confirmations=False,
            )
        )
        db.session.commit()

        response = self.client.get(
            f"/api/v1/transactions/ETH-USDT/{deposit_addr}",
            headers={"X-Shkeeper-Api-Key": "api-key"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [tx["txid"] for tx in response.get_json()["transactions"]],
            ["0xlegacyinvoiceaddr"],
        )

    def test_address_transactions_keep_unconfirmed_deposits(self):
        deposit_addr = "0x88C9DD4592Afbe26cdeCd967b629D6E3Da535735"
        invoice = Invoice(
            external_id="gpw-u2-usdt-erc20-007ead413c9e",
            fiat="USD",
            crypto="ETH-USDT",
            addr=deposit_addr,
            amount_fiat=Decimal("1000000"),
            amount_crypto=Decimal("1020000"),
            exchange_rate=Decimal("1"),
            status=InvoiceStatus.UNPAID,
        )
        db.session.add(invoice)
        db.session.commit()
        db.session.add(
            UnconfirmedTransaction(
                invoice_id=invoice.id,
                txid="0xunconfirmed",
                crypto="ETH-USDT",
                addr=deposit_addr,
                amount_crypto=Decimal("6"),
            )
        )
        db.session.commit()

        response = self.client.get(
            f"/api/v1/transactions/ETH-USDT/{deposit_addr}",
            headers={"X-Shkeeper-Api-Key": "api-key"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["transactions"],
            [
                {
                    "amount": "6",
                    "crypto": "ETH-USDT",
                    "addr": deposit_addr,
                    "txid": "0xunconfirmed",
                    "status": "UNCONFIRMED",
                }
            ],
        )

    def test_unscoped_transactions_keep_outgoing_rows(self):
        deposit_addr = "0x88C9DD4592Afbe26cdeCd967b629D6E3Da535735"
        incoming_invoice = Invoice(
            external_id="gpw-u2-usdt-erc20-007ead413c9e",
            fiat="USD",
            crypto="ETH-USDT",
            addr=deposit_addr,
            amount_fiat=Decimal("1000000"),
            amount_crypto=Decimal("1020000"),
            exchange_rate=Decimal("1"),
            status=InvoiceStatus.PARTIAL,
        )
        outgoing_invoice = Invoice(
            fiat="USD",
            addr=deposit_addr,
            status=InvoiceStatus.OUTGOING,
        )
        db.session.add_all([incoming_invoice, outgoing_invoice])
        db.session.commit()
        db.session.add_all(
            [
                Transaction(
                    invoice_id=incoming_invoice.id,
                    txid="0xf192f6d5f3a2b2cb2f7cff7d934f39a14f670ec9fd6885f4800c722e7f2aa021",
                    crypto="ETH-USDT",
                    amount_crypto=Decimal("6"),
                    amount_fiat=Decimal("6"),
                    need_more_confirmations=False,
                ),
                Transaction(
                    invoice_id=outgoing_invoice.id,
                    txid="0x5d482f31421bee038ac592d8465f21f11fee7121b354bed5054ff109c5e2caa1",
                    crypto="ETH-USDT",
                    amount_crypto=Decimal("0"),
                    amount_fiat=Decimal("0"),
                    need_more_confirmations=False,
                    callback_confirmed=True,
                ),
            ]
        )
        db.session.commit()

        response = self.client.get(
            "/api/v1/transactions",
            headers={"X-Shkeeper-Api-Key": "api-key"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertCountEqual(
            [tx["txid"] for tx in response.get_json()["transactions"]],
            [
                "0xf192f6d5f3a2b2cb2f7cff7d934f39a14f670ec9fd6885f4800c722e7f2aa021",
                "0x5d482f31421bee038ac592d8465f21f11fee7121b354bed5054ff109c5e2caa1",
            ],
        )


if __name__ == "__main__":
    unittest.main()
