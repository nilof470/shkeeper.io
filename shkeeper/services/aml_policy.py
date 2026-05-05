import json
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from flask import current_app, has_app_context

from shkeeper.models import (
    AmlCheck,
    AmlStatus,
    DepositDecision,
    Invoice,
    Transaction,
)
from shkeeper.services.aml_coverage import SUPPORTED_STATUS, get_coverage_policy


AML_MIN_CHECK_AMOUNT_FIAT = Decimal("100")
AML_SKIP_CUMULATIVE_LIMIT_FIAT = Decimal("300")
AML_SKIP_CUMULATIVE_WINDOW_HOURS = 24
AML_MAX_ACCEPT_SCORE = Decimal("0.10")


def _config_value(name, default):
    if has_app_context():
        return current_app.config.get(name, default)
    return default


def _config_decimal(name, default):
    return Decimal(str(_config_value(name, default)))


def _config_int(name, default):
    return int(_config_value(name, default))


def _decimal(value):
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _signals_json(value):
    if not value:
        return "{}"
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True)


def _signals_dict(value):
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _has_alerts(signals):
    alerts = signals.get("alerts")
    if alerts in (None, "", [], {}):
        return False
    return True


def _is_true(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("1", "true", "yes")
    return bool(value)


def _datetime(value):
    if value is None or isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=None)


def build_deposit_id(tx):
    return f"shkeeper-tx-{tx.id}"


def build_idempotency_key(tx):
    return f"{tx.crypto}:{tx.txid}:{build_deposit_id(tx)}"


def _base_check(tx):
    policy = get_coverage_policy(tx.crypto)
    return AmlCheck(
        transaction_id=tx.id,
        deposit_id=build_deposit_id(tx),
        idempotency_key=build_idempotency_key(tx),
        provider=policy.get("provider") or "koinkyt",
        threshold=_config_decimal("AML_MAX_ACCEPT_SCORE", AML_MAX_ACCEPT_SCORE),
    )


def _tx_address(tx):
    return tx.addr


def _skipped_total_before(tx, window_start):
    current_addr = _tx_address(tx)
    checks = (
        AmlCheck.query.join(Transaction)
        .join(Invoice)
        .filter(
            AmlCheck.status == AmlStatus.SKIPPED,
            AmlCheck.skip_reason == "amount_below_threshold",
            Transaction.crypto == tx.crypto,
            Transaction.created_at >= window_start,
            Invoice.external_id == tx.invoice.external_id,
            Transaction.id != tx.id,
        )
        .all()
    )
    total = Decimal("0")
    for check in checks:
        if check.transaction.addr == current_addr:
            total += _decimal(check.transaction.amount_fiat) or Decimal("0")
    return total


def skipped_cumulative_amount(tx):
    window_hours = _config_int(
        "AML_SKIP_CUMULATIVE_WINDOW_HOURS", AML_SKIP_CUMULATIVE_WINDOW_HOURS
    )
    window_start = datetime.utcnow() - timedelta(hours=window_hours)
    return (_skipped_total_before(tx, window_start) + (_decimal(tx.amount_fiat) or 0))


def should_skip_aml(tx):
    amount_fiat = _decimal(tx.amount_fiat) or Decimal("0")
    min_amount = _config_decimal("AML_MIN_CHECK_AMOUNT_FIAT", AML_MIN_CHECK_AMOUNT_FIAT)
    limit = _config_decimal(
        "AML_SKIP_CUMULATIVE_LIMIT_FIAT", AML_SKIP_CUMULATIVE_LIMIT_FIAT
    )
    if amount_fiat >= min_amount:
        return False
    return skipped_cumulative_amount(tx) <= limit


def build_skipped_check(tx):
    window_hours = _config_int(
        "AML_SKIP_CUMULATIVE_WINDOW_HOURS", AML_SKIP_CUMULATIVE_WINDOW_HOURS
    )
    check = _base_check(tx)
    check.status = AmlStatus.SKIPPED
    check.provider_status = None
    check.deposit_decision = DepositDecision.CREDIT
    check.decision_reason = "amount_below_aml_threshold"
    check.score = None
    check.skip_reason = "amount_below_threshold"
    check.min_check_amount_fiat = _config_decimal(
        "AML_MIN_CHECK_AMOUNT_FIAT", AML_MIN_CHECK_AMOUNT_FIAT
    )
    check.cumulative_window = f"{window_hours}h"
    check.cumulative_amount_fiat = skipped_cumulative_amount(tx)
    check.cumulative_limit_fiat = _config_decimal(
        "AML_SKIP_CUMULATIVE_LIMIT_FIAT", AML_SKIP_CUMULATIVE_LIMIT_FIAT
    )
    return check


def unsupported_check(tx):
    policy = get_coverage_policy(tx.crypto)
    check = _base_check(tx)
    check.status = AmlStatus.MANUAL_REVIEW
    check.deposit_decision = DepositDecision.MANUAL_REVIEW
    check.decision_reason = policy.get("reason") or "unsupported_asset"
    check.provider = policy.get("provider") or check.provider
    check.asset = policy.get("asset")
    check.network = policy.get("network")
    return check


def decision_from_provider_result(tx, result):
    policy = get_coverage_policy(tx.crypto)
    if policy["status"] != SUPPORTED_STATUS:
        return unsupported_check(tx)

    check = _base_check(tx)
    check.provider = result.get("provider") or policy.get("provider") or "koinkyt"
    check.provider_status = result.get("provider_status")
    check.status = AmlStatus.CHECKING
    check.score = _decimal(result.get("score") or result.get("risk_score"))
    check.threshold = _config_decimal("AML_MAX_ACCEPT_SCORE", AML_MAX_ACCEPT_SCORE)
    check.uid = result.get("uid")
    check.asset = result.get("asset") or policy.get("asset")
    check.network = result.get("network") or policy.get("network")
    check.signals_json = _signals_json(result.get("signals"))
    check.raw_response_json = _signals_json(result.get("raw_response") or result)
    check.report_url = result.get("report_url")
    check.error_code = result.get("error_code")
    check.error_message = result.get("error_message")
    check.attempts = int(result.get("attempts") or 0)
    check.next_retry_at = _datetime(result.get("next_retry_at"))
    check.timeout_at = _datetime(result.get("timeout_at"))
    signals = _signals_dict(result.get("signals"))

    status = result.get("status")
    provider_status = result.get("provider_status")
    if status == "timeout" or provider_status == "timeout":
        check.status = AmlStatus.MANUAL_REVIEW
        check.deposit_decision = DepositDecision.MANUAL_REVIEW
        check.decision_reason = "aml_pending_timeout"
    elif check.error_code == "missing_risk_score":
        check.status = AmlStatus.MANUAL_REVIEW
        check.deposit_decision = DepositDecision.MANUAL_REVIEW
        check.decision_reason = "incomplete_aml_result"
    elif status == "failed" or provider_status == "error":
        check.status = AmlStatus.MANUAL_REVIEW
        check.deposit_decision = DepositDecision.MANUAL_REVIEW
        check.decision_reason = "aml_provider_error"
    elif provider_status in ("pending", "checking") or status in (
        "pending",
        "checking",
        "rechecking",
    ):
        check.status = AmlStatus.CHECKING
    elif check.score is None:
        check.status = AmlStatus.MANUAL_REVIEW
        check.deposit_decision = DepositDecision.MANUAL_REVIEW
        check.decision_reason = "incomplete_aml_result"
    elif _has_alerts(signals):
        check.status = AmlStatus.MANUAL_REVIEW
        check.deposit_decision = DepositDecision.MANUAL_REVIEW
        check.decision_reason = "risk_profile_alert"
    elif _is_true(signals.get("too_many_indirects")):
        check.status = AmlStatus.MANUAL_REVIEW
        check.deposit_decision = DepositDecision.MANUAL_REVIEW
        check.decision_reason = "too_many_indirects"
    elif check.score <= check.threshold:
        check.status = AmlStatus.APPROVED
        check.deposit_decision = DepositDecision.CREDIT
        check.decision_reason = "score_below_threshold"
    else:
        check.status = AmlStatus.MANUAL_REVIEW
        check.deposit_decision = DepositDecision.MANUAL_REVIEW
        check.decision_reason = "risk_score_above_threshold"

    return check


def is_terminal(aml_check):
    if aml_check is None:
        return False
    return aml_check.status in {
        AmlStatus.APPROVED,
        AmlStatus.DECLINED,
        AmlStatus.SKIPPED,
        AmlStatus.MANUAL_REVIEW,
    }
