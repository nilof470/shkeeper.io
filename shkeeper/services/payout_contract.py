from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation

from shkeeper.services.payout_errors import PayoutRequestError


USDT_DECIMALS = 6
PAYOUT_CONTRACT_VERSION = "usdt-payout-execution-v1"


def compact_json(data):
    return json.dumps(data, separators=(",", ":"), sort_keys=True)


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def normalize_external_id(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def canonical_usdt_amount(value) -> tuple[Decimal, str]:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PayoutRequestError("amount must be a decimal string", code="INVALID_AMOUNT") from exc
    if not amount.is_finite():
        raise PayoutRequestError("amount must be a finite decimal string", code="INVALID_AMOUNT")
    if amount <= 0:
        raise PayoutRequestError("amount must be positive", code="INVALID_AMOUNT")
    if amount.as_tuple().exponent < -USDT_DECIMALS:
        raise PayoutRequestError(
            "amount supports at most 6 decimal places",
            code="INVALID_AMOUNT_PRECISION",
        )
    quantized = amount.quantize(Decimal("0.000001"))
    return quantized, format(quantized, "f")


def canonical_request_payload(
    *,
    consumer,
    external_id,
    asset,
    network,
    amount,
    destination,
    callback_endpoint_id,
    contract_version,
):
    return {
        "consumer": consumer,
        "external_id": external_id,
        "asset": asset,
        "network": network,
        "amount": amount,
        "destination": destination,
        "callback_endpoint_id": callback_endpoint_id,
        "contract_version": contract_version,
    }


def canonical_sidecar_payload(
    *,
    consumer,
    execution_id,
    external_id,
    asset,
    network,
    amount,
    destination,
    contract_version,
):
    return {
        "consumer": consumer,
        "execution_id": str(execution_id),
        "external_id": external_id,
        "asset": asset,
        "network": network,
        "amount": amount,
        "destination": destination,
        "contract_version": contract_version,
    }


def hash_payload(payload) -> str:
    return sha256_hex(compact_json(payload))
