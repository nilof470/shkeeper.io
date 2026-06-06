from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlsplit

from flask import current_app

from shkeeper import db, requests
from shkeeper.models import PayoutCallbackEvent
from shkeeper.services.payout_contract import sha256_hex
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


class PayoutCallbackOutbox:
    PENDING = "PENDING"
    RETRY = "RETRY"
    DISPATCHING = "DISPATCHING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    ACTIVE_UNDELIVERED = (PENDING, RETRY, DISPATCHING)

    @staticmethod
    def _utcnow():
        return datetime.now(timezone.utc).replace(tzinfo=None)

    @staticmethod
    def _format_datetime(value):
        if value is None:
            return None
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.isoformat(timespec="microseconds") + "Z"

    @staticmethod
    def _timestamp_seconds(value=None):
        if value is None:
            return int(time.time())
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)
        return int(value.timestamp())

    @staticmethod
    def _state_name(state):
        if state is None:
            return None
        return state.name if hasattr(state, "name") else str(state)

    @staticmethod
    def _json_list(raw_value):
        if not raw_value:
            return []
        return json.loads(raw_value)

    @staticmethod
    def _json_object(raw_value):
        if not raw_value:
            return {}
        return json.loads(raw_value)

    @staticmethod
    def _load_mapping(config_key):
        value = current_app.config.get(config_key)
        if isinstance(value, dict):
            return value
        if value:
            return json.loads(value)
        return {}

    @classmethod
    def _configured_keys(cls):
        keys = cls._load_mapping("PAYOUT_CALLBACK_KEYS")
        if keys:
            return keys
        keys = cls._load_mapping("PAYOUT_CALLBACK_KEYS_JSON")
        if keys:
            return keys
        keys = cls._load_mapping("PAYOUT_CONSUMER_KEYS")
        if keys:
            return keys
        return cls._load_mapping("PAYOUT_CONSUMER_KEYS_JSON")

    @classmethod
    def _signing_key(cls, consumer):
        consumer_keys = cls._configured_keys().get(consumer, {})
        if isinstance(consumer_keys, str):
            return "default", consumer_keys
        if consumer_keys:
            key_id = sorted(consumer_keys.keys())[0]
            key_config = consumer_keys[key_id]
            if isinstance(key_config, dict):
                return key_id, key_config.get("secret")
            return key_id, key_config
        raise PayoutRequestError(
            f"Missing payout callback signing key for consumer: {consumer}",
            code="PAYOUT_CALLBACK_SIGNING_KEY_REQUIRED",
            status_code=500,
        )

    @classmethod
    def _configured_endpoints(cls):
        endpoints = cls._load_mapping("PAYOUT_CALLBACK_ENDPOINTS")
        if endpoints:
            return endpoints
        return cls._load_mapping("PAYOUT_CALLBACK_ENDPOINTS_JSON")

    @classmethod
    def _endpoint_config(cls, consumer, callback_endpoint_id):
        consumer_endpoints = cls._configured_endpoints().get(consumer, {})
        if isinstance(consumer_endpoints, str):
            if callback_endpoint_id == "default":
                return {"url": consumer_endpoints}
            return {}
        if not isinstance(consumer_endpoints, dict):
            return {}
        endpoint = consumer_endpoints.get(callback_endpoint_id)
        if isinstance(endpoint, str):
            return {"url": endpoint}
        return endpoint or {}

    @classmethod
    def require_endpoint_configured(cls, consumer, callback_endpoint_id):
        endpoint = cls._endpoint_config(consumer, callback_endpoint_id)
        url = endpoint.get("url")
        if not url:
            raise PayoutRequestError(
                "Payout callback endpoint is not configured",
                code="PAYOUT_CALLBACK_ENDPOINT_UNCONFIGURED",
                status_code=503,
            )
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise PayoutRequestError(
                "Payout callback endpoint URL is invalid",
                code="PAYOUT_CALLBACK_ENDPOINT_INVALID",
                status_code=503,
            )
        return endpoint

    @classmethod
    def _signature_path_query(cls, consumer, callback_endpoint_id, endpoint=None):
        endpoint = endpoint or cls.require_endpoint_configured(
            consumer,
            callback_endpoint_id,
        )
        if endpoint.get("path"):
            return endpoint["path"], endpoint.get("query", "")
        if endpoint.get("url"):
            parsed = urlsplit(endpoint["url"])
            return parsed.path or "/", parsed.query
        return f"/api/v1/payout-callbacks/{callback_endpoint_id}", ""

    @classmethod
    def _callback_url(cls, event):
        endpoint = cls.require_endpoint_configured(
            event.consumer,
            event.callback_endpoint_id,
        )
        return endpoint.get("url")

    @classmethod
    def _payload(cls, execution, previous_state, current_state, event_id, occurred_at):
        return {
            "event_id": event_id,
            "event_version": execution.event_version,
            "state_transition_id": execution.state_transition_id,
            "occurred_at": cls._format_datetime(occurred_at),
            "consumer": execution.consumer,
            "execution_id": execution.id,
            "sidecar_execution_id": execution.sidecar_execution_id,
            "external_id": execution.external_id,
            "asset": execution.asset,
            "network": execution.network,
            "amount": format(
                Decimal(execution.amount).quantize(Decimal("0.000001")),
                "f",
            ),
            "destination": execution.destination,
            "previous_state": cls._state_name(previous_state),
            "state": cls._state_name(current_state),
            "failure_class": (
                execution.failure_class.name if execution.failure_class else None
            ),
            "txids": cls._json_list(execution.txids_json),
            "message_hashes": cls._json_list(execution.message_hashes_json),
            "error_code": execution.error_code,
            "error_message": execution.error_message,
            "reconciliation_required": execution.reconciliation_required,
            "callback_endpoint_id": execution.callback_endpoint_id,
            "request_hash": execution.request_hash,
            "sidecar_payload_hash": execution.sidecar_payload_hash,
            "sidecar_status_hash": execution.last_sidecar_status_hash,
            "sidecar_status_observed_at": cls._format_datetime(
                execution.last_sidecar_status_observed_at
            ),
            "sidecar_evidence": cls._json_object(execution.last_sidecar_status_json),
            "resolution_status": (
                execution.resolution_status.name
                if execution.resolution_status
                else None
            ),
            "resolution_evidence": cls._json_object(
                execution.resolution_evidence_json
            ),
            "resolution_evidence_hash": execution.resolution_evidence_hash,
            "resolution_operator_note": execution.resolution_operator_note,
            "resolved_by": execution.resolved_by,
            "resolved_at": cls._format_datetime(execution.resolved_at),
        }

    @classmethod
    def add_transition_event(
        cls,
        execution,
        *,
        previous_state,
        current_state,
        occurred_at,
    ):
        endpoint = cls.require_endpoint_configured(
            execution.consumer,
            execution.callback_endpoint_id,
        )
        event_id = str(uuid.uuid4())
        payload = cls._payload(
            execution,
            previous_state,
            current_state,
            event_id,
            occurred_at,
        )
        raw_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        body = raw_payload.encode("utf-8")
        timestamp = cls._timestamp_seconds()
        nonce = event_id
        key_id, base, headers = cls._signed_callback_metadata(
            execution.consumer,
            execution.callback_endpoint_id,
            body,
            timestamp,
            nonce,
            endpoint=endpoint,
        )
        event = PayoutCallbackEvent(
            event_id=event_id,
            payout_execution_id=execution.id,
            execution_id=execution.id,
            consumer=execution.consumer,
            external_id=execution.external_id,
            asset=execution.asset,
            network=execution.network,
            event_version=execution.event_version,
            state_transition_id=execution.state_transition_id,
            occurred_at=occurred_at,
            callback_endpoint_id=execution.callback_endpoint_id,
            payload_hash=sha256_hex(body),
            raw_payload=raw_payload,
            signature_key_id=key_id,
            signature_base=base,
            signature_headers_json=json.dumps(
                headers,
                sort_keys=True,
                separators=(",", ":"),
            ),
            dispatch_status=cls.PENDING,
            attempt_count=0,
        )
        db.session.add(event)
        return event

    @classmethod
    def _signed_callback_metadata(
        cls,
        consumer,
        callback_endpoint_id,
        body,
        timestamp,
        nonce,
        *,
        endpoint=None,
    ):
        key_id, secret = cls._signing_key(consumer)
        canonical_path, canonical_query = cls._signature_path_query(
            consumer,
            callback_endpoint_id,
            endpoint=endpoint,
        )
        base = signature_base(
            timestamp,
            nonce,
            "POST",
            canonical_path,
            canonical_query,
            body,
        )
        headers = {
            "Content-Type": "application/json",
            PAYOUT_CONSUMER_HEADER: consumer,
            PAYOUT_KEY_ID_HEADER: key_id,
            PAYOUT_TIMESTAMP_HEADER: str(timestamp),
            PAYOUT_NONCE_HEADER: nonce,
            PAYOUT_SIGNATURE_HEADER: sign_request(secret, base),
        }
        return key_id, base, headers

    @classmethod
    def _refresh_delivery_signature(cls, event, now):
        body = event.raw_payload.encode("utf-8")
        timestamp = cls._timestamp_seconds(now)
        key_id, base, headers = cls._signed_callback_metadata(
            event.consumer,
            event.callback_endpoint_id,
            body,
            timestamp,
            event.event_id,
        )
        event.signature_key_id = key_id
        event.signature_base = base
        event.signature_headers_json = json.dumps(
            headers,
            sort_keys=True,
            separators=(",", ":"),
        )
        return event

    @classmethod
    def _deliver_http(cls, event):
        callback_url = cls._callback_url(event)
        if not callback_url:
            raise RuntimeError("Callback endpoint URL is not configured")
        headers = json.loads(event.signature_headers_json or "{}")
        return requests.post(
            callback_url,
            data=event.raw_payload.encode("utf-8"),
            headers=headers,
            timeout=current_app.config.get("REQUESTS_NOTIFICATION_TIMEOUT", 30),
        )

    @staticmethod
    def _status_code(response):
        if response is True:
            return 200
        return getattr(response, "status_code", 0)

    @staticmethod
    def _next_attempt_at(now, attempt_count):
        delay = min(60 * (2 ** max(attempt_count - 1, 0)), 3600)
        return now + timedelta(seconds=delay)

    @classmethod
    def _stale_dispatching_before(cls, now, lease_seconds):
        return now - timedelta(seconds=lease_seconds)

    @classmethod
    def _due_filter(cls, now, lease_seconds):
        return db.or_(
            db.and_(
                PayoutCallbackEvent.dispatch_status.in_([cls.PENDING, cls.RETRY]),
                db.or_(
                    PayoutCallbackEvent.next_attempt_at.is_(None),
                    PayoutCallbackEvent.next_attempt_at <= now,
                ),
            ),
            db.and_(
                PayoutCallbackEvent.dispatch_status == cls.DISPATCHING,
                PayoutCallbackEvent.last_attempt_at
                <= cls._stale_dispatching_before(now, lease_seconds),
            ),
        )

    @classmethod
    def _has_undelivered_predecessor(cls, event):
        return (
            PayoutCallbackEvent.query.filter(
                PayoutCallbackEvent.execution_id == event.execution_id,
                PayoutCallbackEvent.event_version < event.event_version,
                PayoutCallbackEvent.dispatch_status.in_(cls.ACTIVE_UNDELIVERED),
            )
            .with_entities(PayoutCallbackEvent.id)
            .first()
            is not None
        )

    @classmethod
    def _claim_event(cls, event, now, lease_seconds):
        if cls._has_undelivered_predecessor(event):
            return None
        rowcount = (
            PayoutCallbackEvent.query.filter(
                PayoutCallbackEvent.id == event.id,
                cls._due_filter(now, lease_seconds),
            ).update(
                {
                    "dispatch_status": cls.DISPATCHING,
                    "attempt_count": (event.attempt_count or 0) + 1,
                    "last_attempt_at": now,
                    "next_attempt_at": None,
                },
                synchronize_session=False,
            )
        )
        db.session.commit()
        if rowcount != 1:
            return None
        return PayoutCallbackEvent.query.get(event.id)

    @classmethod
    def dispatch_due_events(
        cls,
        *,
        batch_size=50,
        deliverer=None,
        now=None,
        max_attempts=10,
        lease_seconds=None,
    ):
        now = now or cls._utcnow()
        deliverer = deliverer or cls._deliver_http
        lease_seconds = lease_seconds or current_app.config.get(
            "PAYOUT_CALLBACK_DISPATCH_LEASE_SECONDS",
            120,
        )
        events = (
            PayoutCallbackEvent.query.filter(cls._due_filter(now, lease_seconds))
            .order_by(PayoutCallbackEvent.id)
            .limit(batch_size)
            .all()
        )

        processed = 0
        for event in events:
            event = cls._claim_event(event, now, lease_seconds)
            if event is None:
                continue
            try:
                cls._refresh_delivery_signature(event, now)
                response = deliverer(event)
                status_code = cls._status_code(response)
                if not 200 <= status_code < 300:
                    raise RuntimeError(f"Callback endpoint returned HTTP {status_code}")
            except Exception as exc:
                event.last_error = str(exc)
                event.dispatch_status = (
                    cls.FAILED if event.attempt_count >= max_attempts else cls.RETRY
                )
                event.next_attempt_at = cls._next_attempt_at(now, event.attempt_count)
                event.apply_result = event.dispatch_status
            else:
                event.dispatch_status = cls.DELIVERED
                event.applied_at = now
                event.apply_result = cls.DELIVERED
                event.next_attempt_at = None
                event.last_error = None
            db.session.add(event)
            processed += 1

        db.session.commit()
        return processed
