from __future__ import annotations

import hashlib
import hmac
import json
import time

from flask import current_app, g, request
from sqlalchemy.exc import IntegrityError

from shkeeper import db
from shkeeper.models import PayoutAuthNonce
from shkeeper.services.payout_contract import sha256_hex


PAYOUT_CONSUMER_HEADER = "X-Payout-Consumer"
PAYOUT_KEY_ID_HEADER = "X-Payout-Key-Id"
PAYOUT_TIMESTAMP_HEADER = "X-Payout-Timestamp"
PAYOUT_NONCE_HEADER = "X-Payout-Nonce"
PAYOUT_SIGNATURE_HEADER = "X-Payout-Signature"


def _configured_keys():
    keys = current_app.config.get("PAYOUT_CONSUMER_KEYS")
    if keys:
        return keys
    raw = current_app.config.get("PAYOUT_CONSUMER_KEYS_JSON")
    if not raw:
        return {}
    return json.loads(raw)


def get_consumer_secret(consumer, key_id):
    key_config = get_consumer_key_config(consumer, key_id)
    if isinstance(key_config, dict):
        return key_config.get("secret")
    return key_config


def get_consumer_key_config(consumer, key_id):
    consumer_keys = _configured_keys().get(consumer, {})
    if isinstance(consumer_keys, str):
        return consumer_keys if key_id == "default" else None
    return consumer_keys.get(key_id)


def is_consumer_key_allowed_for_rail(consumer, key_id, asset, network):
    key_config = get_consumer_key_config(consumer, key_id)
    if isinstance(key_config, str):
        return True
    if not isinstance(key_config, dict):
        return False
    rails = key_config.get("rails") or key_config.get("allowed_rails") or []
    allowed = {str(rail).upper() for rail in rails}
    return f"{network}-{asset}".upper() in allowed


def signature_base(timestamp, nonce, method, canonical_path, canonical_query, body):
    return "\n".join(
        [
            str(timestamp),
            nonce,
            method.upper(),
            canonical_path,
            canonical_query,
            sha256_hex(body),
        ]
    )


def sign_request(secret, base):
    return hmac.new(secret.encode("utf-8"), base.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_request_signature(*, body, now=None, max_age_seconds=None):
    consumer = request.headers.get(PAYOUT_CONSUMER_HEADER, "").strip()
    key_id = request.headers.get(PAYOUT_KEY_ID_HEADER, "").strip()
    timestamp = request.headers.get(PAYOUT_TIMESTAMP_HEADER, "").strip()
    nonce = request.headers.get(PAYOUT_NONCE_HEADER, "").strip()
    signature = request.headers.get(PAYOUT_SIGNATURE_HEADER, "").strip().lower()
    if not all([consumer, key_id, timestamp, nonce, signature]):
        return False, "PAYOUT_AUTH_MISSING", "Missing payout auth headers"
    if len(signature) != 64:
        return False, "PAYOUT_AUTH_INVALID", "Invalid payout signature"
    try:
        timestamp_int = int(timestamp)
    except ValueError:
        return False, "PAYOUT_AUTH_INVALID", "Invalid payout timestamp"

    clock = int(time.time()) if now is None else int(now)
    tolerance = max_age_seconds or current_app.config.get(
        "PAYOUT_AUTH_MAX_AGE_SECONDS",
        300,
    )
    if abs(clock - timestamp_int) > int(tolerance):
        return False, "PAYOUT_AUTH_EXPIRED", "Expired payout auth timestamp"

    secret = get_consumer_secret(consumer, key_id)
    if not secret:
        return False, "PAYOUT_AUTH_UNKNOWN_KEY", "Unknown payout auth key"

    base = signature_base(
        timestamp_int,
        nonce,
        request.method,
        request.path,
        request.query_string.decode("utf-8"),
        body,
    )
    expected = sign_request(secret, base)
    if not hmac.compare_digest(expected, signature):
        return False, "PAYOUT_AUTH_INVALID", "Invalid payout signature"

    db.session.add(
        PayoutAuthNonce(
            consumer=consumer,
            key_id=key_id,
            nonce=nonce,
            timestamp=timestamp_int,
        )
    )
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return False, "PAYOUT_AUTH_REPLAY", "Replayed payout auth nonce"

    g.payout_consumer = consumer
    g.payout_key_id = key_id
    return True, None, None


def payout_execution_auth_required(view):
    def wrapped(*args, **kwargs):
        body = request.get_data(cache=True) or b""
        ok, code, message = verify_request_signature(body=body)
        if not ok:
            status_code = 401
            if code in (
                "PAYOUT_AUTH_INVALID",
                "PAYOUT_AUTH_EXPIRED",
                "PAYOUT_AUTH_REPLAY",
            ):
                status_code = 403
            return {"status": "error", "code": code, "message": message}, status_code
        return view(*args, **kwargs)

    wrapped.__name__ = view.__name__
    wrapped.__doc__ = view.__doc__
    return wrapped
