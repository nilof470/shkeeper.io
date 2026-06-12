from sqlalchemy import and_, false, func, or_

from shkeeper.models import (
    AmlCheck,
    AmlStatus,
    AmlSweepResolution,
    Invoice,
    InvoiceAddress,
    Transaction,
)
from shkeeper.services.sweep_guard_policy import (
    addresses_match,
    expected_sweep_network,
    is_sweep_guarded_crypto,
    network_matches_guarded_crypto,
    normalize_crypto,
    normalize_sweep_network,
    uses_case_insensitive_address,
)

MANUAL_RESOLUTION_TYPES = {"approved", "refunded"}
KNOWN_AML_STATUSES = {
    AmlStatus.PENDING,
    AmlStatus.CHECKING,
    AmlStatus.APPROVED,
    AmlStatus.DECLINED,
    AmlStatus.SKIPPED,
    AmlStatus.MANUAL_REVIEW,
}


def _address_filter(crypto, address):
    if address is None:
        return false()
    if uses_case_insensitive_address(crypto):
        normalized = str(address).lower()
        return or_(
            func.lower(InvoiceAddress.addr) == normalized,
            and_(InvoiceAddress.id == None, func.lower(Invoice.addr) == normalized),
        )
    return or_(
        InvoiceAddress.addr == address,
        and_(InvoiceAddress.id == None, Invoice.addr == address),
    )


def _network_matches(crypto, requested_network, check_network):
    requested = normalize_sweep_network(requested_network)
    actual = normalize_sweep_network(check_network or expected_sweep_network(crypto))
    return requested == actual


def _decision(decision, reason, checks=None):
    checks = checks or []
    return {
        "decision": decision,
        "reason": reason,
        "transaction_ids": [check.transaction_id for check in checks],
        "matched_transaction_count": len(checks),
        "aml_statuses": [check.status for check in checks],
    }


def _decision_for_transactions(decision, reason, transactions):
    return {
        "decision": decision,
        "reason": reason,
        "transaction_ids": [tx.id for tx in transactions],
        "matched_transaction_count": len(transactions),
        "aml_statuses": [],
    }


def _transactions_for_txid(crypto, txid):
    return (
        Transaction.query.filter(
            Transaction.crypto == normalize_crypto(crypto),
            Transaction.txid == txid,
        )
        .order_by(Transaction.id)
        .all()
    )


def _guarded_checks_for_address(crypto, address):
    return (
        AmlCheck.query.join(Transaction)
        .join(Invoice, Transaction.invoice_id == Invoice.id)
        .outerjoin(
            InvoiceAddress,
            and_(
                InvoiceAddress.invoice_id == Transaction.invoice_id,
                InvoiceAddress.crypto == Transaction.crypto,
            ),
        )
        .filter(
            Transaction.crypto == normalize_crypto(crypto),
            AmlCheck.sweep_guard_required == True,
        )
        .filter(_address_filter(crypto, address))
        .order_by(Transaction.id)
        .all()
    )


def _has_valid_manual_resolution(check):
    return _manual_resolution(check) is not None


def _has_confirmed_callback(check):
    return bool(getattr(check.transaction, "callback_confirmed", False))


def _manual_resolution(check):
    return (
        AmlSweepResolution.query.filter_by(
            transaction_id=check.transaction_id,
            deposit_id=check.deposit_id,
        )
        .filter(AmlSweepResolution.resolution_type.in_(MANUAL_RESOLUTION_TYPES))
        .first()
    )


def decide_sweep_eligibility(crypto, network, address, txid=None):
    if is_sweep_guarded_crypto(crypto) and not network_matches_guarded_crypto(
        crypto, network
    ):
        checks = _guarded_checks_for_address(crypto, address)
        return _decision("block", "network_mismatch", checks)

    if txid:
        recorded = _transactions_for_txid(crypto, txid)
        if not recorded:
            return _decision("wait", "transaction_not_found")
        if len(recorded) != 1:
            return _decision_for_transactions("block", "ambiguous_match", recorded)
        recorded_tx = recorded[0]
        if not addresses_match(crypto, recorded_tx.addr, address):
            return _decision("block", "mismatch")
        if recorded_tx.need_more_confirmations:
            return _decision_for_transactions(
                "wait", "confirmations_pending", recorded
            )

    checks = [
        check
        for check in _guarded_checks_for_address(crypto, address)
        if _network_matches(crypto, network, check.network)
    ]
    if not checks:
        if txid and is_sweep_guarded_crypto(crypto):
            recorded_tx = recorded[0]
            if recorded_tx.aml_check and recorded_tx.aml_check.status == AmlStatus.SKIPPED:
                return _decision_for_transactions(
                    "allow", "aml_skipped_small_amount", recorded
                )
            return _decision_for_transactions("wait", "aml_missing", recorded)
        return _decision("allow", "legacy_no_guarded_deposits")

    if any(check.transaction.need_more_confirmations for check in checks):
        return _decision("wait", "confirmations_pending", checks)

    if any(check.status not in KNOWN_AML_STATUSES for check in checks):
        return _decision("wait", "aml_missing", checks)

    if any(check.status == AmlStatus.PENDING for check in checks):
        return _decision("wait", "aml_pending", checks)

    if any(check.status == AmlStatus.CHECKING for check in checks):
        return _decision("wait", "aml_checking", checks)

    for check in checks:
        if check.status == AmlStatus.MANUAL_REVIEW and not _has_valid_manual_resolution(
            check
        ):
            return _decision("block", "manual_review", checks)

    if any(check.status == AmlStatus.DECLINED for check in checks):
        return _decision("block", "aml_declined", checks)

    if any(not _has_confirmed_callback(check) for check in checks):
        return _decision("wait", "callback_pending", checks)

    if all(
        check.status in {AmlStatus.APPROVED, AmlStatus.SKIPPED}
        or (
            check.status == AmlStatus.MANUAL_REVIEW
            and _has_valid_manual_resolution(check)
        )
        for check in checks
    ):
        resolutions = [
            _manual_resolution(check)
            for check in checks
            if check.status == AmlStatus.MANUAL_REVIEW
        ]
        resolution_types = {
            resolution.resolution_type for resolution in resolutions if resolution
        }
        if "refunded" in resolution_types:
            return _decision("allow", "manual_refund", checks)
        if "approved" in resolution_types:
            return _decision("allow", "manual_approved", checks)
        if any(check.status == AmlStatus.SKIPPED for check in checks):
            return _decision("allow", "aml_skipped_small_amount", checks)
        return _decision("allow", "aml_approved", checks)

    return _decision("wait", "aml_missing", checks)
