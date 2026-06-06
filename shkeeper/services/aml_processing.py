from datetime import datetime, timedelta
import json

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


def _is_retryable_sidecar_error(result):
    return (
        result.get("error_source") == "aml-shkeeper"
        and result.get("provider_status") == "error"
    )


def _resolve_retryable_sidecar_error(check, result):
    check.status = AmlStatus.CHECKING
    check.provider_status = "checking"
    check.deposit_decision = None
    check.decision_reason = None
    check.error_code = result.get("error_code") or "aml_shkeeper_error"
    check.error_message = result.get("error_message")
    check.raw_response_json = json.dumps(result, sort_keys=True, default=str)
    check.attempts = (check.attempts or 0) + 1
    check.next_retry_at = _now() + _retry_delay()
    if check.timeout_at is None:
        check.timeout_at = _timeout_at()
    db.session.add(check)
    db.session.commit()
    return check


def _resolve_client_result(check, result):
    if _is_retryable_sidecar_error(result):
        return _resolve_retryable_sidecar_error(check, result)
    return _resolve_from_result(check, result)


def _resolve_timeout(check):
    check.status = AmlStatus.MANUAL_REVIEW
    check.deposit_decision = DepositDecision.MANUAL_REVIEW
    check.decision_reason = "aml_pending_timeout"
    check.provider_status = "timeout"
    check.error_code = check.error_code or "aml_pending_timeout"
    check.error_message = check.error_message or "AML provider result timed out"
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
        current_app.logger.info(
            "[%s/%s] AML provider does not support asset, sending callback without AML enrichment",
            tx.crypto,
            tx.txid,
        )
        return None

    if should_skip_aml(tx):
        return _persist_new_check(build_skipped_check(tx))

    check = _persist_new_check(_pending_check(tx, coverage))
    result = AmlShkeeperClient().create_check(_payload_for_sidecar(tx, check))
    return _resolve_client_result(check, result)


def refresh_aml_check(aml_check):
    now = _now()
    if aml_check.timeout_at and aml_check.timeout_at <= now:
        return _resolve_timeout(aml_check)
    result = AmlShkeeperClient().get_check(aml_check.deposit_id)
    return _resolve_client_result(aml_check, result)


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
    if tx.aml_check is None:
        return get_coverage_policy(tx.crypto)["status"] != SUPPORTED_STATUS
    return is_terminal(tx.aml_check)
