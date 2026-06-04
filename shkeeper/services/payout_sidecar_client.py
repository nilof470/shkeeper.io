from __future__ import annotations

import json
import time
import uuid
from decimal import Decimal

from flask import current_app

from shkeeper import requests
from shkeeper.modules.classes.crypto import Crypto
from shkeeper.services.payout_errors import PayoutRequestError
from shkeeper.services.payout_execution_auth import (
    PAYOUT_CONSUMER_HEADER,
    PAYOUT_KEY_ID_HEADER,
    PAYOUT_NONCE_HEADER,
    PAYOUT_SIGNATURE_HEADER,
    PAYOUT_TIMESTAMP_HEADER,
    sign_request,
    signature_base,
)


class SidecarClientError(RuntimeError):
    pass


class SidecarSubmitTimeout(SidecarClientError):
    pass


class SidecarStatusUnavailable(SidecarClientError):
    pass


class SidecarExecutionNotFound(SidecarClientError):
    pass


class HttpPayoutSidecarClient:
    DEFAULT_SIDECAR_PORT = "6000"

    def _crypto(self, execution):
        try:
            return Crypto.instances[execution.crypto_id]
        except KeyError as exc:
            raise PayoutRequestError(
                f"Unknown sidecar crypto: {execution.crypto_id}",
                code="UNKNOWN_CRYPTO",
                status_code=404,
            ) from exc

    @staticmethod
    def _crypto_or_none(execution):
        return Crypto.instances.get(execution.crypto_id)

    def _base_url(self, execution):
        service = (getattr(execution, "sidecar_service", "") or "").strip()
        if service:
            if service.startswith(("http://", "https://")):
                return service.rstrip("/")
            if ":" not in service:
                service = f"{service}:{self.DEFAULT_SIDECAR_PORT}"
            return f"http://{service}".rstrip("/")

        crypto = self._crypto(execution)
        return f"http://{crypto.gethost()}".rstrip("/")

    def _url(self, execution, suffix):
        return f"{self._base_url(execution)}/{execution.sidecar_symbol}{suffix}"

    def _auth(self, execution):
        crypto = self._crypto_or_none(execution)
        if crypto is None:
            return None
        return crypto.get_auth_creds()

    @staticmethod
    def _load_mapping(config_key):
        value = current_app.config.get(config_key)
        if isinstance(value, dict):
            return value
        if value:
            return json.loads(value)
        return {}

    @classmethod
    def _configured_sidecar_keys(cls):
        keys = cls._load_mapping("PAYOUT_SIDECAR_KEYS")
        if keys:
            return keys
        return cls._load_mapping("PAYOUT_SIDECAR_KEYS_JSON")

    @staticmethod
    def _rail(execution):
        return f"{execution.network}-{execution.asset}".upper()

    @staticmethod
    def _timeout():
        return current_app.config.get("PAYOUT_SIDECAR_REQUEST_TIMEOUT", 10)

    @classmethod
    def _signing_key(cls, execution):
        consumer_keys = cls._configured_sidecar_keys().get(execution.consumer, {})
        if isinstance(consumer_keys, str):
            raise SidecarStatusUnavailable(
                "Payout sidecar signing key must declare allowed rails"
            )
        for key_id, key_config in sorted(consumer_keys.items()):
            if isinstance(key_config, str):
                raise SidecarStatusUnavailable(
                    "Payout sidecar signing key must declare allowed rails"
                )
            rails = key_config.get("rails") or key_config.get("allowed_rails") or []
            if cls._rail(execution) in rails:
                return key_id, key_config.get("secret")
        raise SidecarStatusUnavailable(
            f"Missing payout sidecar signing key for {execution.consumer}:{cls._rail(execution)}"
        )

    @staticmethod
    def _compact_body(payload):
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    @classmethod
    def _signed_headers(cls, execution, method, path, body):
        key_id, secret = cls._signing_key(execution)
        if not secret:
            raise SidecarStatusUnavailable("Payout sidecar signing secret is empty")
        timestamp = int(time.time())
        nonce = str(uuid.uuid4())
        base = signature_base(timestamp, nonce, method, path, "", body)
        return {
            "Content-Type": "application/json",
            PAYOUT_CONSUMER_HEADER: execution.consumer,
            PAYOUT_KEY_ID_HEADER: key_id,
            PAYOUT_TIMESTAMP_HEADER: str(timestamp),
            PAYOUT_NONCE_HEADER: nonce,
            PAYOUT_SIGNATURE_HEADER: sign_request(secret, base),
        }

    @staticmethod
    def _json(response, action):
        try:
            return response.json(parse_float=Decimal)
        except TypeError:
            try:
                return response.json()
            except ValueError as exc:
                raise SidecarStatusUnavailable(
                    f"Sidecar {action} endpoint returned non-JSON response"
                ) from exc
        except ValueError as exc:
            raise SidecarStatusUnavailable(
                f"Sidecar {action} endpoint returned non-JSON response"
            ) from exc

    @staticmethod
    def _is_error_payload(payload):
        return payload.get("status") == "error" or bool(payload.get("error"))

    @staticmethod
    def _amount(execution):
        return format(
            Decimal(execution.amount).quantize(Decimal("0.000001")),
            "f",
        )

    def _payload(self, execution):
        return {
            "consumer": execution.consumer,
            "execution_id": str(execution.id),
            "external_id": execution.external_id,
            "asset": execution.asset,
            "network": execution.network,
            "amount": self._amount(execution),
            "destination": execution.destination,
            "contract_version": execution.contract_version,
            "request_hash": execution.request_hash,
            "sidecar_payload_hash": execution.sidecar_payload_hash,
            "source_wallet_ref": execution.source_wallet_ref,
            "payout_queue": execution.payout_queue,
        }

    def preflight(self, execution):
        suffix = f"/payout-executions/{execution.id}/preflight"
        path = f"/{execution.sidecar_symbol}{suffix}"
        body = self._compact_body(self._payload(execution))
        try:
            response = requests.post(
                self._url(execution, suffix),
                auth=self._auth(execution),
                data=body,
                headers=self._signed_headers(execution, "POST", path, body),
                timeout=self._timeout(),
            )
        except requests.exceptions.Timeout as exc:
            raise SidecarStatusUnavailable(str(exc)) from exc
        except requests.exceptions.RequestException as exc:
            raise SidecarStatusUnavailable(str(exc)) from exc
        payload = self._json(response, "preflight")
        if response.status_code >= 500:
            raise SidecarStatusUnavailable(
                f"Sidecar preflight endpoint returned HTTP {response.status_code}"
            )
        if response.status_code >= 400 and not self._is_error_payload(payload):
            raise SidecarStatusUnavailable(
                f"Sidecar preflight endpoint returned HTTP {response.status_code}"
            )
        return payload

    def submit(self, execution):
        suffix = f"/payout-executions/{execution.id}"
        path = f"/{execution.sidecar_symbol}{suffix}"
        body = self._compact_body(self._payload(execution))
        try:
            response = requests.post(
                self._url(execution, suffix),
                auth=self._auth(execution),
                data=body,
                headers=self._signed_headers(execution, "POST", path, body),
                timeout=self._timeout(),
            )
        except requests.exceptions.Timeout as exc:
            raise SidecarSubmitTimeout(str(exc)) from exc
        except requests.exceptions.RequestException as exc:
            raise SidecarSubmitTimeout(str(exc)) from exc
        payload = self._json(response, "submit")
        if response.status_code >= 500:
            raise SidecarStatusUnavailable(
                f"Sidecar submit endpoint returned HTTP {response.status_code}"
            )
        if response.status_code >= 400 and not self._is_error_payload(payload):
            raise SidecarStatusUnavailable(
                f"Sidecar submit endpoint returned HTTP {response.status_code}"
            )
        return payload

    def status(self, execution):
        suffix = f"/payout-executions/{execution.id}"
        path = f"/{execution.sidecar_symbol}{suffix}"
        body = b""
        try:
            response = requests.get(
                self._url(execution, suffix),
                auth=self._auth(execution),
                headers=self._signed_headers(execution, "GET", path, body),
                timeout=self._timeout(),
            )
        except requests.exceptions.Timeout as exc:
            raise SidecarStatusUnavailable(str(exc)) from exc
        except requests.exceptions.RequestException as exc:
            raise SidecarStatusUnavailable(str(exc)) from exc
        payload = self._json(response, "status")
        if response.status_code == 404 and payload.get("code") in (
            "NOT_FOUND",
            "NO_EXECUTION_CREATED",
        ):
            raise SidecarExecutionNotFound(payload.get("code"))
        if response.status_code == 404:
            raise SidecarStatusUnavailable(
                "Sidecar status endpoint returned 404 without NO_EXECUTION_CREATED"
            )
        if response.status_code >= 400:
            raise SidecarStatusUnavailable(
                f"Sidecar status endpoint returned HTTP {response.status_code}"
            )
        return payload
