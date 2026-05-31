from abc import abstractmethod
from decimal import Decimal
from os import environ
import datetime
import json
from collections import namedtuple
from typing import Annotated, Union

from shkeeper import requests
from flask import current_app as app

from shkeeper.modules.classes.crypto import Crypto
from shkeeper.schemas import TronAccountResponse, TronError
from shkeeper.services.payout_errors import (
    PayoutDestinationNotActivatedError,
    PayoutRequestError,
    PayoutResourceUnavailableError,
)
from pydantic import TypeAdapter


class TronToken(Crypto):
    can_set_tx_fee = False
    network_currency = "TRX"
    account_activation_fee = (
        1.1  # https://developers.tron.network/docs/account#account-activation
    )

    def gethost(self):
        host = environ.get("TRON_API_SERVER_HOST", "localhost")
        port = environ.get("TRON_API_SERVER_PORT", "6000")
        return f"{host}:{port}"

    def get_auth_creds(self):
        username = environ.get(f"{self.crypto}_USERNAME", "shkeeper")
        password = environ.get(f"{self.crypto}_PASSWORD", "shkeeper")
        return (username, password)

    def balance(self):
        try:
            response = requests.post(
                f"http://{self.gethost()}/{self.crypto}/balance",
                auth=self.get_auth_creds(),
            ).json(parse_float=Decimal)
            balance = response["balance"]
        except Exception as e:
            app.logger.exception("balance error")
            balance = False

        return Decimal(balance)

    def getstatus(self):
        try:
            response = requests.post(
                f"http://{self.gethost()}/{self.crypto}/status",
                auth=self.get_auth_creds(),
            ).json(parse_float=Decimal)

            block_ts = response["last_block_timestamp"]
            now_ts = int(datetime.datetime.now().timestamp())

            delta = abs(now_ts - block_ts)
            block_interval = 3
            if delta < block_interval * 10:
                return "Synced"
            else:
                return "Sync In Progress (%d blocks behind)" % (delta // block_interval)

        except Exception as e:
            return "Offline"

    def mkaddr(self, **kwargs):
        response = requests.post(
            f"http://{self.gethost()}/{self.crypto}/generate-address",
            auth=self.get_auth_creds(),
        ).json(parse_float=Decimal)
        addr = response["base58check_address"]
        return addr

    def getaddrbytx(self, txid):
        txs = requests.post(
            f"http://{self.gethost()}/{self.crypto}/transaction/{txid}",
            auth=self.get_auth_creds(),
        ).json(parse_float=Decimal)
        return [
            [
                tx["address"],
                Decimal(tx["amount"]),
                tx["confirmations"],
                tx["category"],
            ]
            for tx in txs
        ]

    def get_confirmations_by_txid(self, txid):
        transactions = self.getaddrbytx(txid)
        _, _, confirmations, _ = transactions[0]
        return confirmations

    def dump_wallet(self):
        response = requests.post(
            f"http://{self.gethost()}/{self.crypto}/dump",
            auth=self.get_auth_creds(),
        ).json(parse_float=Decimal)

        now = datetime.datetime.now().strftime("%F_%T")
        filename = f"{now}_{self.crypto}_shkeeper_wallet.json"
        content = json.dumps(response["accounts"], indent=4)
        return filename, content

    def create_wallet(self, *args, **kwargs):
        return {"error": None}

    @property
    def fee_deposit_account(self):
        response = requests.post(
            f"http://{self.gethost()}/{self.crypto}/fee-deposit-account",
            auth=self.get_auth_creds(),
        ).json(parse_float=Decimal)

        FeeDepositAccount = namedtuple("FeeDepositAccount", "addr balance")
        return FeeDepositAccount(response["account"], Decimal(response["balance"]))

    def estimate_tx_fee(self, amount, **kwargs):
        params = {}
        if kwargs.get("address"):
            params["address"] = kwargs["address"]
        try:
            response = requests.post(
                f"http://{self.gethost()}/{self.crypto}/calc-tx-fee/{amount}",
                auth=self.get_auth_creds(),
                params=params or None,
                timeout=10,
            )
        except requests.exceptions.RequestException as exc:
            raise PayoutResourceUnavailableError(
                "TRON sidecar fee estimate unavailable"
            ) from exc
        try:
            data = response.json(parse_float=Decimal)
        except ValueError as exc:
            raise PayoutResourceUnavailableError(
                "TRON sidecar fee estimate returned invalid JSON"
            ) from exc
        if response.status_code >= 400:
            if isinstance(data, dict):
                self._raise_payout_preflight_error(
                    data,
                    status_code=response.status_code,
                )
            raise PayoutResourceUnavailableError(
                f"TRON sidecar fee estimate returned HTTP {response.status_code}"
            )
        if isinstance(data, dict) and (
            data.get("status") == "error" or data.get("error")
        ):
            self._raise_payout_preflight_error(data)
        return data

    def can_omit_fee_for_payout(self):
        return self.crypto == "USDT"

    def _usdt_payout_balance_for_preflight(self):
        try:
            response = requests.post(
                f"http://{self.gethost()}/{self.crypto}/balance",
                auth=self.get_auth_creds(),
                timeout=10,
            )
        except requests.exceptions.RequestException as exc:
            raise PayoutResourceUnavailableError(
                "TRON USDT balance check unavailable"
            ) from exc
        if response.status_code >= 400:
            raise PayoutResourceUnavailableError(
                f"TRON USDT balance check returned HTTP {response.status_code}"
            )
        try:
            data = response.json(parse_float=Decimal)
        except ValueError as exc:
            raise PayoutResourceUnavailableError(
                "TRON USDT balance check returned invalid JSON"
            ) from exc
        try:
            return Decimal(data["balance"])
        except Exception as exc:
            raise PayoutResourceUnavailableError(
                "TRON USDT balance check returned invalid balance"
            ) from exc

    def _raise_payout_preflight_error(self, payload, status_code=None):
        code = payload.get("code")
        message = (
            payload.get("message")
            or payload.get("error")
            or "TRON USDT payout preflight failed"
        )
        if status_code is not None and status_code >= 500:
            raise PayoutResourceUnavailableError(message)
        if code == "DESTINATION_NOT_ACTIVATED":
            raise PayoutDestinationNotActivatedError(message)
        if code in {
            "PAYOUT_RESOURCE_UNAVAILABLE",
            "PROFEEX_ESTIMATE_UNAVAILABLE",
            "PROVIDER_UNAVAILABLE",
            "PROVIDER_FAILED",
            "RESOURCE_READ_FAILED",
            "RESOURCE_RECHECK_FAILED",
        }:
            raise PayoutResourceUnavailableError(message)
        raise PayoutRequestError(
            message,
            code=code or "TRON_USDT_PREFLIGHT_ERROR",
            status_code=status_code if status_code is not None else None,
        )

    def preflight_payout(self, destination, amount):
        if self.crypto != "USDT":
            return
        balance = self._usdt_payout_balance_for_preflight()
        if amount > balance:
            raise PayoutRequestError(
                f"Payout amount exceeds wallet balance: {amount} > {balance}",
                code="INSUFFICIENT_BALANCE",
            )

        quote = self.estimate_tx_fee(amount, address=destination)
        if not isinstance(quote, dict):
            raise PayoutResourceUnavailableError(
                "TRON USDT fee estimate returned invalid response"
            )
        if quote.get("status") == "error" or quote.get("error"):
            self._raise_payout_preflight_error(quote)

        resource_quote = quote.get("resource_quote")
        if not resource_quote:
            raise PayoutResourceUnavailableError(
                "TRON USDT payout resource quote is unavailable"
            )

        if not resource_quote.get("submit_ready"):
            self._raise_payout_preflight_error(
                {
                    "code": resource_quote.get("blocking_code"),
                    "message": resource_quote.get("blocking_reason")
                    or "TRON USDT payout resources are not ready",
                }
            )

    def mkpayout(self, destination, amount, fee, subtract_fee_from_amount=False):
        if self.crypto == self.network_currency and subtract_fee_from_amount:
            fee = Decimal(self.estimate_tx_fee(amount)["fee"])
            if fee >= amount:
                return f"Payout failed: not enought TRX to pay for transaction. Need {fee}, balance {amount}"
            else:
                amount -= fee
        response = requests.post(
            f"http://{self.gethost()}/{self.crypto}/payout/{destination}/{amount}",
            auth=self.get_auth_creds(),
        ).json(parse_float=Decimal)
        return response

    def get_task(self, id):
        response = requests.post(
            f"http://{self.gethost()}/{self.crypto}/task/{id}",
            auth=self.get_auth_creds(),
        ).json(parse_float=Decimal)
        return response

    def multipayout(self, payout_list):
        response = requests.post(
            f"http://{self.gethost()}/{self.crypto}/multipayout",
            auth=self.get_auth_creds(),
            json=payout_list,
        ).json(parse_float=Decimal)
        return response

    def servers_status(self):
        response = requests.get(
            f"http://{self.gethost()}/{self.crypto}/multiserver/status",
            auth=self.get_auth_creds(),
        ).json(parse_float=Decimal)
        return response["statuses"]

    def multiserver_set_server(self, server_id):
        response = requests.post(
            f"http://{self.gethost()}/{self.crypto}/multiserver/change/{server_id}",
            auth=self.get_auth_creds(),
        ).json(parse_float=Decimal)
        return response

    def metrics(self):
        host = str(self.gethost())
        host = host.split(":")[0].replace("-", "_")
        try:
            success_text = f"# HELP {host}_status Connection status to {host}\n# TYPE {host}_status gauge\n{host}_status 1.0\n"
            response = requests.get(
                f"http://{self.gethost()}/metrics",
                auth=self.get_auth_creds(),
                timeout=10,
            )
            response.raise_for_status()
            return response.text + success_text
        except Exception as e:
            error_text = f"# HELP {host}_status Connection status to {host}\n# TYPE {host}_status gauge\n{host}_status 0.0\n"
            return error_text

    def get_all_addresses(self):
        response = requests.get(
            f"http://{self.gethost()}/{self.crypto}/addresses",
            auth=self.get_auth_creds(),
        ).json(parse_float=Decimal)
        return response["accounts"]

    def get_account_info(self) -> TronAccountResponse | TronError:
        response = requests.get(
            f"http://{self.gethost()}/staking",
            auth=self.get_auth_creds(),
        )
        # adaptor = TypeAdapter(Annotated[Union[TronAccountResponse, TronError]])
        adaptor = TypeAdapter(Union[TronAccountResponse, TronError])
        return adaptor.validate_json(response.text)

    def get_staking_config(self):
        response = requests.get(
            f"http://{self.gethost()}/staking/info",
            auth=self.get_auth_creds(),
        )
        return response.json(parse_float=Decimal)

    def stake_trx(self, amount, resource):
        response = requests.post(
            f"http://{self.gethost()}/staking/freeze/{amount}/{resource}",
            auth=self.get_auth_creds(),
        )
        return response.json(parse_float=Decimal)
