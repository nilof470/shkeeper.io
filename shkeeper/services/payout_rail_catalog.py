from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

from shkeeper.models import (
    PayoutRail,
    PayoutRailLegacySpendPolicy,
    PayoutRailHotWalletPolicy,
)
from shkeeper.services.payout_errors import PayoutRequestError

_legacy_spend_context = ContextVar("legacy_payout_spend_context", default=None)


class RouteThroughPayoutExecution(PayoutRequestError):
    def __init__(self, rail):
        super().__init__(
            "Automatic legacy payout must route through payout execution for "
            f"{rail.consumer}:{rail.asset}:{rail.network}",
            code="ROUTE_THROUGH_PAYOUT_EXECUTION",
            status_code=409,
        )
        self.rail = rail


class PayoutRailCatalog:
    @staticmethod
    def find_enabled_execution_rail(consumer, asset, network):
        return PayoutRail.query.filter_by(
            consumer=consumer,
            asset=asset,
            network=network,
            execution_enabled=True,
        ).first()

    @staticmethod
    def get_enabled_execution_rail(consumer, asset, network):
        rail = PayoutRailCatalog.find_enabled_execution_rail(
            consumer,
            asset,
            network,
        )
        if rail is None:
            raise PayoutRequestError(
                f"Payout rail is disabled or unsupported: {asset}/{network}",
                code="PAYOUT_RAIL_DISABLED",
                status_code=400,
            )
        return rail

    @staticmethod
    def find_enabled_execution_rail_for_spend_source(
        crypto_id,
        source_wallet_ref=None,
    ):
        query = PayoutRail.query.filter_by(
            crypto_id=crypto_id,
            execution_enabled=True,
        )
        if source_wallet_ref is not None:
            query = query.filter_by(source_wallet_ref=source_wallet_ref)
        return query.first()


def assert_operator_audit_context(operator_id, audit_reason):
    if not operator_id or not audit_reason:
        raise PayoutRequestError(
            "Manual admin payout requires operator_id and audit_reason",
            code="OPERATOR_AUDIT_REQUIRED",
        )


def acquire_or_verify_wallet_guard(rail, source_wallet_ref=None):
    if (
        rail.hot_wallet_policy
        == PayoutRailHotWalletPolicy.CURRENT_SIDECAR_SOURCE_WALLET_WITH_SHARED_GUARD
    ):
        if not rail.wallet_guard_key:
            raise PayoutRequestError(
                "Shared source wallet requires wallet_guard_key",
                code="WALLET_GUARD_REQUIRED",
            )
        raise PayoutRequestError(
            "Shared source wallet guard policy is unavailable for manual payout in this release",
            code="WALLET_GUARD_UNAVAILABLE",
            status_code=409,
        )
    return True


def assert_legacy_spend_allowed(
    crypto_id,
    source_wallet_ref=None,
    *,
    spend_origin,
    operator_id=None,
    audit_reason=None,
):
    rail = PayoutRailCatalog.find_enabled_execution_rail_for_spend_source(
        crypto_id=crypto_id,
        source_wallet_ref=source_wallet_ref,
    )
    if rail is None:
        return
    if spend_origin == "manual_admin":
        assert_operator_audit_context(operator_id, audit_reason)
        acquire_or_verify_wallet_guard(rail, source_wallet_ref)
        return
    if (
        rail.legacy_spend_policy
        == PayoutRailLegacySpendPolicy.ROUTE_AUTOMATIC_THROUGH_PAYOUT_EXECUTION
    ):
        raise RouteThroughPayoutExecution(rail)
    raise PayoutRequestError(
        "Automatic legacy payout is blocked for execution-enabled rail "
        f"{rail.consumer}:{rail.asset}:{rail.network}",
        code="AUTOMATIC_LEGACY_PAYOUT_BLOCKED",
        status_code=409,
    )


@contextmanager
def legacy_spend_guard_context(
    crypto_id,
    source_wallet_ref=None,
    *,
    spend_origin,
    operator_id=None,
    audit_reason=None,
):
    assert_legacy_spend_allowed(
        crypto_id,
        source_wallet_ref=source_wallet_ref,
        spend_origin=spend_origin,
        operator_id=operator_id,
        audit_reason=audit_reason,
    )
    token = _legacy_spend_context.set(
        {
            "crypto_id": crypto_id,
            "source_wallet_ref": source_wallet_ref,
            "spend_origin": spend_origin,
        }
    )
    try:
        yield
    finally:
        _legacy_spend_context.reset(token)


def assert_direct_crypto_legacy_spend_allowed(crypto_id, source_wallet_ref=None):
    context = _legacy_spend_context.get()
    if (
        context
        and context.get("crypto_id") == crypto_id
        and context.get("source_wallet_ref") == source_wallet_ref
    ):
        return
    assert_legacy_spend_allowed(
        crypto_id,
        source_wallet_ref=source_wallet_ref,
        spend_origin="service",
    )
