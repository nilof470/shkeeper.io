import click
from apscheduler.schedulers import SchedulerNotRunningError

from shkeeper import requests

from flask import Blueprint, json
# from flask_smorest import Blueprint as SmorestBlueprint
from flask import current_app as app

from shkeeper.modules.classes.crypto import Crypto
from shkeeper.models import *
from shkeeper.services.aml_processing import (
    ensure_aml_for_transaction,
    is_callback_allowed,
)
from shkeeper.services.aml_coverage import SUPPORTED_STATUS, get_coverage_policy
from shkeeper.services.webhook_hmac import compact_json_bytes, shkeeper_webhook_auth_headers
from datetime import datetime, timedelta

bp = Blueprint("callback", __name__)
# bp_callback = SmorestBlueprint("callback", __name__)

DEFAULT_CURRENCY = 'USD'


def _json_object(value):
    if not value:
        return {}
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}


def _decimal_or_none(value):
    if value is None:
        return None
    return remove_exponent(value)


def _aml_checked(aml_check):
    return (
        aml_check.provider_status == "success"
        and aml_check.score is not None
        and aml_check.error_code is None
    )


def _aml_check_status(aml_check):
    if aml_check.skip_reason:
        return "skipped"
    if _aml_checked(aml_check):
        return "success"
    if aml_check.decision_reason == "aml_pending_timeout":
        return "timeout"
    if aml_check.decision_reason == "aml_provider_error":
        return "error"
    if aml_check.decision_reason == "incomplete_aml_result":
        return "incomplete"
    if aml_check.provider_status in ("timeout", "error"):
        return aml_check.provider_status
    if aml_check.error_code in ("missing_risk_score", "incomplete_aml_result"):
        return "incomplete"
    if aml_check.provider_status in ("pending", "checking"):
        return aml_check.provider_status
    return "incomplete"


def _aml_reason_code(aml_check):
    if aml_check.skip_reason == "amount_below_threshold":
        return "amount_below_shkeeper_threshold"
    if aml_check.decision_reason in (
        "aml_pending_timeout",
        "aml_provider_error",
        "incomplete_aml_result",
    ):
        return aml_check.decision_reason
    if aml_check.error_code:
        return aml_check.error_code
    if aml_check.provider_status in ("pending", "checking"):
        return None
    if not _aml_checked(aml_check):
        return "incomplete_aml_result"
    return None


def _aml_policy_payload(aml_check):
    policy = {}
    if aml_check.min_check_amount_fiat is not None:
        policy["min_check_amount_fiat"] = remove_exponent(
            aml_check.min_check_amount_fiat
        )
    if aml_check.cumulative_amount_fiat is not None:
        policy["cumulative_amount_fiat"] = remove_exponent(
            aml_check.cumulative_amount_fiat
        )
    if aml_check.cumulative_limit_fiat is not None:
        policy["cumulative_limit_fiat"] = remove_exponent(
            aml_check.cumulative_limit_fiat
        )
    if aml_check.cumulative_window is not None:
        policy["cumulative_window"] = aml_check.cumulative_window
    return policy


def _unsupported_aml_payload(tx):
    policy = get_coverage_policy(tx.crypto)
    reason = policy.get("reason") or "unsupported_asset"
    return {
        "supported": False,
        "checked": False,
        "check_status": "unsupported",
        "reason_code": reason,
        "provider": policy.get("provider"),
        "provider_status": "unsupported",
        "score": None,
        "uid": None,
        "asset": policy.get("asset"),
        "network": policy.get("network"),
        "signals": {},
        "report_url": None,
        "error_code": reason,
        "error_message": "AML provider does not support this asset",
        "policy": {},
    }


def _aml_payload(aml_check):
    payload = {
        "supported": True,
        "checked": _aml_checked(aml_check),
        "check_status": _aml_check_status(aml_check),
        "reason_code": _aml_reason_code(aml_check),
        "provider": aml_check.provider,
        "provider_status": aml_check.provider_status,
        "score": _decimal_or_none(aml_check.score),
        "uid": aml_check.uid,
        "asset": aml_check.asset,
        "network": aml_check.network,
        "signals": _json_object(aml_check.signals_json),
        "report_url": aml_check.report_url,
        "error_code": aml_check.error_code,
        "error_message": aml_check.error_message,
        "policy": _aml_policy_payload(aml_check),
    }
    return payload


def _add_aml_to_trigger_transaction(item, trigger_tx):
    aml_check = trigger_tx.aml_check
    if not aml_check:
        if get_coverage_policy(trigger_tx.crypto)["status"] != SUPPORTED_STATUS:
            item["aml"] = _unsupported_aml_payload(trigger_tx)
        return item
    item["deposit_id"] = aml_check.deposit_id
    item["idempotency_key"] = aml_check.idempotency_key
    item["aml"] = _aml_payload(aml_check)
    return item


def build_payment_notification(tx):
    transactions = []
    for t in tx.invoice.transactions:
        amount_fiat_without_fee = t.rate.get_orig_amount(t.amount_fiat)
        item = {
            "txid": t.txid,
            "date": str(t.created_at),
            "amount_crypto": remove_exponent(t.amount_crypto),
            "amount_fiat": remove_exponent(t.amount_fiat),
            "amount_fiat_without_fee": remove_exponent(amount_fiat_without_fee),
            "fee_fiat": remove_exponent(t.amount_fiat - amount_fiat_without_fee),
            "trigger": tx.id == t.id,
            "crypto": t.crypto,
        }
        if tx.id == t.id:
            _add_aml_to_trigger_transaction(item, t)
        transactions.append(item)

    notification = {
        "external_id": tx.invoice.external_id,
        "crypto": tx.invoice.crypto,
        "addr": tx.invoice.addr,
        "fiat": tx.invoice.fiat,
        "balance_fiat": remove_exponent(tx.invoice.balance_fiat),
        "balance_crypto": remove_exponent(tx.invoice.balance_crypto),
        "paid": tx.invoice.status in (InvoiceStatus.PAID, InvoiceStatus.OVERPAID),
        "status": tx.invoice.status.name,
        "transactions": transactions,
        "fee_percent": remove_exponent(tx.invoice.rate.fee),
        "fee_fixed": remove_exponent(tx.invoice.rate.fixed_fee),
        "fee_policy": (
            tx.invoice.rate.fee_policy.name
            if tx.invoice.rate.fee_policy
            else FeeCalculationPolicy.PERCENT_FEE.name
        ),
    }

    overpaid_fiat = tx.invoice.balance_fiat - (
        tx.invoice.amount_fiat * (tx.invoice.wallet.ulimit / 100)
    )
    notification["overpaid_fiat"] = (
        str(round(overpaid_fiat.normalize(), 2)) if overpaid_fiat > 0 else "0.00"
    )
    return notification


def send_unconfirmed_notification(utx: UnconfirmedTransaction):
    app.logger.info(
        f"send_unconfirmed_notification started for {utx.crypto} {utx.txid}, {utx.addr}, {utx.amount_crypto}"
    )

    invoice_address = InvoiceAddress.query.filter_by(
        crypto=utx.crypto, addr=utx.addr
    ).first()
    invoice = Invoice.query.filter_by(id=invoice_address.invoice_id).first()
    crypto = Crypto.instances[utx.crypto]
    apikey = crypto.wallet.apikey

    notification = {
        "status": "unconfirmed",
        "external_id": invoice.external_id,
        "crypto": utx.crypto,
        "addr": utx.addr,
        "txid": utx.txid,
        "amount": format_decimal(utx.amount_crypto, precision=crypto.precision),
    }

    app.logger.warning(
        f"[{utx.crypto}/{utx.txid}] Posting {notification} to {invoice.callback_url} with api key [REDACTED]"
    )
    body = compact_json_bytes(notification)
    try:
        r = requests.post(
            invoice.callback_url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Shkeeper-Api-Key": apikey,
                **shkeeper_webhook_auth_headers(apikey, body),
            },
            timeout=app.config.get("REQUESTS_NOTIFICATION_TIMEOUT"),
        )
    except Exception as e:
        app.logger.error(
            f"[{utx.crypto}/{utx.txid}] Unconfirmed TX notification failed: {e}"
        )
        return False

    if r.status_code != 202:
        app.logger.warning(
            f"[{utx.crypto}/{utx.txid}] Unconfirmed TX notification failed with HTTP code {r.status_code}"
        )
        return False

    utx.callback_confirmed = True
    db.session.commit()
    app.logger.info(
        f"[{utx.crypto}/{utx.txid}] Unconfirmed TX notification has been accepted"
    )

    return True


def send_notification(tx):
    app.logger.info(f"[{tx.crypto}/{tx.txid}] Notificator started")

    if tx.invoice.status != InvoiceStatus.OUTGOING and not is_callback_allowed(tx):
        app.logger.info(
            f"[{tx.crypto}/{tx.txid}] Final notification blocked until AML terminal state"
        )
        return False

    notification = build_payment_notification(tx)

    apikey = Crypto.instances[tx.crypto].wallet.apikey
    app.logger.warning(
        f"[{tx.crypto}/{tx.txid}] Posting {json.dumps(notification)} to {tx.invoice.callback_url} with api key [REDACTED]"
    )
    body = compact_json_bytes(notification)
    try:
        r = requests.post(
            tx.invoice.callback_url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Shkeeper-Api-Key": apikey,
                **shkeeper_webhook_auth_headers(apikey, body),
            },
            timeout=app.config.get("REQUESTS_NOTIFICATION_TIMEOUT"),
        )
    except Exception as e:
        app.logger.error(f"[{tx.crypto}/{tx.txid}] Notification failed: {e}")
        return False

    if r.status_code != 202:
        app.logger.warning(
            f"[{tx.crypto}/{tx.txid}] Notification failed by {tx.invoice.callback_url} with HTTP code {r.status_code}"
        )
        return False

    tx.callback_confirmed = True
    db.session.commit()
    app.logger.info(
        f"[{tx.crypto}/{tx.txid}] Notification has been accepted by {tx.invoice.callback_url}"
    )
    return True


def list_unconfirmed():
    for tx in Transaction.query.filter_by(callback_confirmed=False):
        print(tx)
    else:
        print("No unconfirmed transactions found!")


def send_callbacks():
    for utx in UnconfirmedTransaction.query.filter_by(callback_confirmed=False):
        try:
            send_unconfirmed_notification(utx)
        except Exception as e:
            app.logger.exception(
                f"Exception while sending callback for UTX {utx.crypto}/{utx.txid}"
            )

    for tx in Transaction.query.filter_by(
        callback_confirmed=False, need_more_confirmations=False
    ):
        try:
            delay_until_date = tx.created_at + timedelta(
                seconds=app.config.get("NOTIFICATION_TASK_DELAY")
            )
            if datetime.now() > delay_until_date:
                app.logger.info(
                    f"[{tx.crypto}/{tx.txid}] created at {tx.created_at}, delayed until {delay_until_date}"
                )
                if tx.invoice.status == InvoiceStatus.OUTGOING:
                    tx.callback_confirmed = True
                    db.session.commit()
                else:
                    ensure_aml_for_transaction(tx)
                    if not is_callback_allowed(tx):
                        app.logger.info(
                            f"[{tx.crypto}/{tx.txid}] Notification is blocked by AML"
                        )
                        continue
                    app.logger.info(f"[{tx.crypto}/{tx.txid}] Notification is pending")
                    send_notification(tx)
            else:
                app.logger.info(
                    f"[{tx.crypto}/{tx.txid}] delaying notification created at {tx.created_at} until {delay_until_date}"
                )
        except Exception as e:
            app.logger.exception(
                f"Exception while sending callback for TX {tx.crypto}/{tx.txid}"
            )

def poll_unconfirmed_payouts():
    app.logger.info("poll_unconfirmed_payouts start")
    cutoff = datetime.utcnow() - timedelta(days=1)
    payouts = (
        Payout.query
        .filter(
            Payout.status == PayoutStatus.IN_PROGRESS,
            Payout.created_at >= cutoff
        )
        .all()
    )
    app.logger.info(f"poll_unconfirmed_payouts finished {payouts}")
    for payout in payouts:
        app.logger.info(f"poll_unconfirmed_payouts payout {payout}")
        crypto = Crypto.instances.get(payout.crypto)
        if not crypto:
            continue
        all_confirmed = False
        tx_to_notify = None
        for tx in payout.transactions:
            if not tx or not getattr(tx, "txid", None):
                app.logger.warning(f"Skipping invalid transaction {tx}")
                continue
            try:
                app.logger.info(f"poll_unconfirmed_payouts get_confirmations_by_txid {tx.txid}")
                confirmations = crypto.get_confirmations_by_txid(tx.txid)
                app.logger.info(f"poll_unconfirmed_payouts confirmations {confirmations}")
            except Exception:
                continue
            if confirmations > int(app.config.get("MIN_CONFIRMATION_BLOCK_FOR_PAYOUT")):
                all_confirmed = True
                if not tx_to_notify:
                    tx_to_notify = tx
        if all_confirmed:
            app.logger.info(f"poll_unconfirmed_payouts all_confirmed {payout}")
            app.logger.info(f"poll_unconfirmed_payouts tx_to_notify {tx_to_notify}")
            payout.status = PayoutStatus.SUCCESS
            payout.success = "Yes"
            if payout.callback_url and tx_to_notify and app.config.get("ENABLE_PAYOUT_CALLBACK"):
                app.logger.info(f"Notification create {tx_to_notify}")
                notification = Notification(
                    txid=tx_to_notify.txid,
                    object_id=payout.id,
                    type='Payout',
                    crypto=payout.crypto,
                    amount_crypto=payout.amount,
                    callback_url=payout.callback_url,
                )
                db.session.add(notification)
    db.session.commit()

def send_payout_callback_notifier():
    max_retries = app.config.get("REQUESTS_NOTIFICATION_RETRIES", 10)
    now = datetime.utcnow()
    notifs = Notification.query.filter(
        Notification.retries < max_retries,
        Notification.callback_confirmed == False
    ).all()
    for notif in notifs:
        retries = notif.retries or 0
        delay_total = sum((i + 1) ** 2 for i in range(retries + 1))
        next_try_time = notif.created_at + timedelta(seconds=delay_total)
        if now < next_try_time:
            continue
        try:
            app.logger.info(f"[PAYOUT {notif.object_id}] Sending payout callback try={retries}")
            success = send_payout_notification(notif)
            if not success:
                notif.retries = retries + 1
                db.session.commit()
                app.logger.info(
                    f"[PAYOUT {notif.object_id}] Retry #{retries+1}. "
                    f"Next in {(retries+2)**2} sec"
                )
        except Exception:
            notif.retries = retries + 1
            db.session.commit()
            app.logger.exception(f"Exception while sending payout callback object_id={notif.object_id}")

def send_payout_notification(notif: Notification):
    payout = Payout.query.get(notif.object_id)
    if not payout:
        notif.message = "Payout not found"
        db.session.commit()
        return False

    tx = payout.transactions[0] if payout.transactions else None
    tx_hash = tx.txid if tx else None
    if not tx_hash:
        app.logger.info(f"[PAYOUT {payout.id}] No tx_hash yet — skipping callback")
        return False
    rate = ExchangeRate.get(DEFAULT_CURRENCY, payout.crypto).get_rate()
    amount_fiat = payout.amount * rate
    payload = {
        "payout_id": payout.id,
        "external_id": payout.external_id,
        "tx_hash": tx_hash,
        "status": "SUCCESS",
        "amount": str(payout.amount),
        "crypto": payout.crypto,
        "amount_fiat": str(amount_fiat),
        "currency_fiat": DEFAULT_CURRENCY,
        "timestamp": payout.created_at.isoformat(),
    }

    crypto = Crypto.instances.get(payout.crypto)
    apikey = crypto.wallet.apikey if crypto and crypto.wallet else ""

    retries = getattr(notif, "retries", 0)
    wait = (retries + 1) ** 2
    app.logger.info(f"[PAYOUT {payout.id}] Sending webhook try={retries}, wait={wait}s")

    body = compact_json_bytes(payload)
    headers = {"Content-Type": "application/json"}
    if apikey:
        headers.update(shkeeper_webhook_auth_headers(apikey, body))
    try:
        r = requests.post(
            payout.callback_url,
            data=body,
            headers=headers,
            timeout=app.config.get("REQUESTS_NOTIFICATION_TIMEOUT", 10),
        )
    except Exception as e:
        notif.message = str(e)
        notif.retries = retries + 1
        db.session.commit()
        return False

    if r.status_code != 202:
        notif.message = f"{r.status_code} {r.reason}"
        notif.retries = retries + 1
        db.session.commit()
        return False

    # Success
    notif.callback_confirmed = True
    db.session.commit()
    app.logger.info(f"[PAYOUT {payout.id}] Webhook delivered successfully")
    return True

def poll_all_pending_payouts():
    cutoff = datetime.utcnow() - timedelta(days=1)
    app.logger.info(f"poll_all_pending_payout start")
    pending_payouts = (
        Payout.query
        .filter(
            Payout.task_id.isnot(None),
            Payout.status == PayoutStatus.IN_PROGRESS,
            Payout.created_at >= cutoff
        )
        .all()
    )
    app.logger.info(f"poll_all_pending_payout pending_payouts {pending_payouts}")
    for payout in pending_payouts:
        app.logger.info(f"poll_all_pending_payout payout {payout}")
        crypto = Crypto.instances.get(payout.crypto)
        if not crypto:
            continue
        task_response = crypto.get_task(payout.task_id)
        status = task_response.get("status")
        app.logger.info(f"poll_all_pending_payout task_response {task_response}")
        if status in ("SUCCESS", "ERROR", "FAILED", "FAILURE"):
            app.logger.info(f"update_from_task")
            app.logger.info(f"update_from_task {task_response}")
            app.logger.info(f"update_from_task {payout.task_id}")
            Payout.update_from_task(task_response, payout.task_id)
    db.session.commit()

def update_confirmations():
    for tx in Transaction.query.filter_by(
        callback_confirmed=False, need_more_confirmations=True
    ):
        try:
            app.logger.info(f"[{tx.crypto}/{tx.txid}] Updating confirmations")
            if not tx.is_more_confirmations_needed():
                app.logger.info(f"[{tx.crypto}/{tx.txid}] Got enough confirmations")
            else:
                app.logger.info(f"[{tx.crypto}/{tx.txid}] Not enough confirmations yet")
        except Exception as e:
            app.logger.exception(
                f"Exception while updating tx confirmations for {tx.crypto}/{tx.txid}"
            )


@bp.cli.command()
def list():
    """Shows list of transaction notifications to be sent"""
    list_unconfirmed()


@bp.cli.command()
def send():
    """Send transaction notification"""
    send_callbacks()


@bp.cli.command()
def update():
    """Update number of confirmation"""
    update_confirmations()


@bp.cli.command()
@click.option("-c", "--confirmations", default=1)
def add(confirmations):
    import time

    crypto = Crypto.instances["BTC"]
    invoice = Invoice.add(
        crypto,
        {
            "external_id": str(time.time()),
            "fiat": "USD",
            "amount": 1000,
            "callback_url": "http://localhost:5000/api/v1/wp_callback",
        },
    )
    tx = Transaction.add(
        crypto,
        {
            "txid": invoice.id * 100,
            "addr": invoice.addr,
            "amount": invoice.amount_crypto,
            "confirmations": confirmations,
        },
    )
