from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from shkeeper import db
from shkeeper.models import (
    PayoutRail,
    PayoutRailHotWalletPolicy,
    PayoutRailLegacySpendPolicy,
)


REQUIRED_FIELDS = (
    "consumer",
    "asset",
    "network",
    "crypto_id",
    "sidecar_service",
    "sidecar_symbol",
    "payout_queue",
    "source_wallet_ref",
)

OPTIONAL_FIELDS = (
    "hot_wallet_policy",
    "legacy_spend_policy",
    "wallet_guard_key",
    "execution_enabled",
    "token_contract",
    "chain_id_or_network_id",
    "decimals",
    "callback_endpoint_id",
    "contract_version",
)

ALLOWED_FIELDS = frozenset(REQUIRED_FIELDS + OPTIONAL_FIELDS)


class PayoutRailSyncError(ValueError):
    pass


def _load_rails(raw):
    if not raw:
        return None, []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise PayoutRailSyncError("PAYOUT_RAILS_JSON is not valid JSON") from exc
    if isinstance(raw, dict):
        consumer = raw.get("consumer")
        rails = raw.get("rails", [])
        if consumer in ("", None):
            consumer = None
        return consumer, rails
    if not isinstance(raw, list):
        raise PayoutRailSyncError("PAYOUT_RAILS_JSON must be a list or {'rails': [...]}")
    return None, raw


def _enum(enum_cls, value, default):
    if not value:
        return default
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls[str(value)]
    except KeyError as exc:
        allowed = ", ".join(item.name for item in enum_cls)
        raise PayoutRailSyncError(
            f"Invalid {enum_cls.__name__}: {value}. Allowed: {allowed}"
        ) from exc


def _bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    raise PayoutRailSyncError(f"Invalid boolean value: {value}")


def _decimals(value):
    if value in (None, ""):
        return 6
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PayoutRailSyncError(f"Invalid decimals value: {value}") from exc
    if (
        not decimal_value.is_finite()
        or decimal_value != decimal_value.to_integral_value()
    ):
        raise PayoutRailSyncError(f"Invalid decimals value: {value}")
    decimals = int(decimal_value)
    if decimals != 6:
        raise PayoutRailSyncError("USDT payout rails must use 6 decimals")
    return decimals


def _validate_enabled_rail(data, index):
    if not _bool(data.get("execution_enabled"), False):
        return
    missing = []
    for field in ("callback_endpoint_id",):
        if data.get(field) in (None, ""):
            missing.append(field)
    if missing:
        raise PayoutRailSyncError(
            "Enabled payout rail "
            f"#{index} missing required fields: {', '.join(missing)}"
        )


def _validate_known_fields(data, index):
    unknown = sorted(set(data) - ALLOWED_FIELDS)
    if unknown:
        raise PayoutRailSyncError(
            "Payout rail "
            f"#{index} has unknown fields: {', '.join(unknown)}. "
            "SHKeeper accepts only payout routing/execution configuration."
        )


def sync_payout_rails(raw):
    desired_consumer, rails = _load_rails(raw)
    synced = 0
    desired_keys = set()
    try:
        for index, data in enumerate(rails):
            if not isinstance(data, dict):
                raise PayoutRailSyncError(f"Payout rail #{index} must be an object")
            _validate_known_fields(data, index)
            missing = [field for field in REQUIRED_FIELDS if not data.get(field)]
            if missing:
                raise PayoutRailSyncError(
                    f"Payout rail #{index} missing required fields: "
                    f"{', '.join(missing)}"
                )
            _validate_enabled_rail(data, index)

            consumer = str(data["consumer"])
            asset = str(data["asset"]).upper()
            network = str(data["network"]).upper()
            if desired_consumer is not None and consumer != desired_consumer:
                raise PayoutRailSyncError(
                    f"Payout rail #{index} consumer must match catalog consumer"
                )
            if (consumer, asset, network) in desired_keys:
                raise PayoutRailSyncError(
                    f"Duplicate payout rail for {consumer}/{asset}/{network}"
                )
            desired_keys.add((consumer, asset, network))
            rail = PayoutRail.query.filter_by(
                consumer=consumer,
                asset=asset,
                network=network,
            ).first()
            if rail is None:
                rail = PayoutRail(consumer=consumer, asset=asset, network=network)
                db.session.add(rail)

            rail.crypto_id = str(data["crypto_id"])
            rail.sidecar_service = str(data["sidecar_service"])
            rail.sidecar_symbol = str(data["sidecar_symbol"])
            rail.payout_queue = str(data["payout_queue"])
            rail.source_wallet_ref = str(data["source_wallet_ref"])
            rail.hot_wallet_policy = _enum(
                PayoutRailHotWalletPolicy,
                data.get("hot_wallet_policy"),
                PayoutRailHotWalletPolicy.CURRENT_SIDECAR_SOURCE_WALLET,
            )
            rail.legacy_spend_policy = _enum(
                PayoutRailLegacySpendPolicy,
                data.get("legacy_spend_policy"),
                PayoutRailLegacySpendPolicy.BLOCK_AUTOMATIC_BYPASS,
            )
            rail.wallet_guard_key = data.get("wallet_guard_key") or None
            rail.execution_enabled = _bool(
                data.get("execution_enabled"),
                False,
            )
            rail.token_contract = data.get("token_contract") or None
            rail.chain_id_or_network_id = data.get("chain_id_or_network_id") or None
            rail.decimals = _decimals(data.get("decimals"))
            rail.callback_endpoint_id = data.get("callback_endpoint_id") or None
            rail.contract_version = data.get("contract_version") or (
                "usdt-payout-execution-v1"
            )
            synced += 1

        if desired_consumer is not None:
            stale_rails = PayoutRail.query.filter_by(consumer=desired_consumer).all()
            for stale in stale_rails:
                key = (stale.consumer, stale.asset, stale.network)
                if key not in desired_keys and stale.execution_enabled:
                    stale.execution_enabled = False

        db.session.commit()
        return synced
    except Exception:
        db.session.rollback()
        raise
