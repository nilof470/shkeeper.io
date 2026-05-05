from datetime import datetime, timedelta

from flask import current_app

from shkeeper import db
from shkeeper.models import AmlCheck, AmlStatus, DepositDecision, InvoiceStatus
from shkeeper.services.aml_coverage import SUPPORTED_STATUS, get_coverage_policy
from shkeeper.services.aml_policy import (
    build_deposit_id,
    build_idempotency_key,
    build_skipped_check,
    decision_from_provider_result,
    is_terminal,
    should_skip_aml,
    unsupported_check,
)
from shkeeper.services.aml_shkeeper_client import AmlShkeeperClient


SNAPSHOT_FIELDS = (
    "provider",
    "provider_status",
    "status",
    "deposit_decision",
    "decision_reason",
    "score",
    "threshold",
    "uid",
    "asset",
    "network",
    "signals_json",
    "raw_response_json",
    "report_url",
    "error_code",
    "error_message",
    "skip_reason",
    "min_check_amount_fiat",
    "cumulative_window",
    "cumulative_amount_fiat",
    "cumulative_limit_fiat",
    "attempts",
    "next_retry_at",
    "timeout_at",
)


def _now():
    return datetime.utcnow()


def _retry_delay():
    return timedelta(seconds=current_app.config.get("AML_RETRY_DELAY_SECONDS", 120))


def _timeout_at(now=None):
    now = now or _now()
    return now + timedelta(
        seconds=current_app.config.get("AML_PENDING_TIMEOUT_SECONDS", 1800)
    )


def _apply_snapshot(target, source):
    existing_timeout_at = target.timeout_at
    for field in SNAPSHOT_FIELDS:
        setattr(target, field, getattr(source, field))
    if target.timeout_at is None:
        target.timeout_at = existing_timeout_at
    return target


def _payload_for_sidecar(tx, check):
    return {
        "deposit_id": check.deposit_id,
        "idempotency_key": check.idempotency_key,
        "crypto": tx.crypto,
        "txid": tx.txid,
        "address": tx.addr,
        "amount_crypto": str(tx.amount_crypto),
        "asset": check.asset,
        "network": check.network,
        "direction": "deposit",
        "threshold": str(check.threshold),
    }


def _pending_check(tx, coverage):
    return AmlCheck(
        transaction=tx,
        transaction_id=tx.id,
        deposit_id=build_deposit_id(tx),
        idempotency_key=build_idempotency_key(tx),
        provider=coverage["provider"],
        provider_status="pending",
        status=AmlStatus.PENDING,
        asset=coverage["asset"],
        network=coverage["network"],
        threshold=current_app.config.get("AML_MAX_ACCEPT_SCORE", "0.10"),
        timeout_at=_timeout_at(),
    )


def _persist_new_check(check):
    db.session.add(check)
    db.session.commit()
    return check


def _resolve_from_result(check, result):
    decision = decision_from_provider_result(check.transaction, result)
    _apply_snapshot(check, decision)
    if is_terminal(check):
        check.next_retry_at = None
    elif check.next_retry_at is None:
        check.next_retry_at = _now() + _retry_delay()
    db.session.add(check)
    db.session.commit()
    return check


def _resolve_timeout(check):
    check.status = AmlStatus.MANUAL_REVIEW
    check.deposit_decision = DepositDecision.MANUAL_REVIEW
    check.decision_reason = "aml_pending_timeout"
    check.provider_status = "timeout"
    check.next_retry_at = None
    db.session.add(check)
    db.session.commit()
    return check


def ensure_aml_for_transaction(tx):
    if tx.invoice.status == InvoiceStatus.OUTGOING:
        return tx.aml_check
    if tx.aml_check:
        return tx.aml_check

    coverage = get_coverage_policy(tx.crypto)
    if coverage["status"] != SUPPORTED_STATUS:
        return _persist_new_check(unsupported_check(tx))

    if should_skip_aml(tx):
        return _persist_new_check(build_skipped_check(tx))

    check = _persist_new_check(_pending_check(tx, coverage))
    result = AmlShkeeperClient().create_check(_payload_for_sidecar(tx, check))
    return _resolve_from_result(check, result)


def refresh_aml_check(aml_check):
    now = _now()
    if aml_check.timeout_at and aml_check.timeout_at <= now:
        return _resolve_timeout(aml_check)
    result = AmlShkeeperClient().get_check(aml_check.deposit_id)
    return _resolve_from_result(aml_check, result)


def process_pending_aml_checks(now=None):
    now = now or _now()
    checks = (
        AmlCheck.query.filter(
            AmlCheck.status.in_([AmlStatus.PENDING, AmlStatus.CHECKING]),
            (AmlCheck.next_retry_at == None) | (AmlCheck.next_retry_at <= now),
        )
        .all()
    )
    processed = []
    for check in checks:
        if check.timeout_at and check.timeout_at <= now:
            processed.append(_resolve_timeout(check))
        else:
            processed.append(refresh_aml_check(check))
    return processed


def is_callback_allowed(tx):
    if tx.invoice.status == InvoiceStatus.OUTGOING:
        return True
    return is_terminal(tx.aml_check)
