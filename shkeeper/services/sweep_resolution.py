import hashlib
import json
from decimal import Decimal, InvalidOperation

from sqlalchemy.exc import IntegrityError

from shkeeper import db
from shkeeper.models import AmlCheck, AmlStatus, AmlSweepResolution, DepositDecision
from shkeeper.services.sweep_guard_policy import (
    addresses_match,
    expected_sweep_network,
    normalize_sweep_network,
)


RESOLUTION_TYPES = {"approved", "refunded"}
REQUIRED_FIELDS = {
    "resolution_type",
    "deposit_id",
    "txid",
    "crypto",
    "network",
    "address",
    "reviewer",
    "reason",
    "external_review_id",
    "idempotency_key",
}
REFUND_REQUIRED_FIELDS = {"refund_txid", "refund_to_address", "refund_amount"}
OPTIONAL_FIELDS = {
    "refund_txid",
    "refund_to_address",
    "refund_amount",
    "refund_source_address",
    "refund_asset",
    "refund_network",
    "refund_notes",
}
ALLOWED_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS
TERMINAL_MANUAL_REVIEW_REASONS = {
    "risk_score_above_threshold",
    "aml_provider_error",
    "aml_pending_timeout",
    "incomplete_aml_result",
    "risk_profile_alert",
    "too_many_indirects",
}


class SweepResolutionError(Exception):
    def __init__(self, message, status_code=400, code="invalid_request"):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def _is_blank(value):
    return value is None or str(value).strip() == ""


def _text(payload, field):
    value = payload.get(field)
    if value is None:
        return None
    return str(value).strip()


def _normalize_crypto(value):
    return str(value or "").strip().upper()


def _validate_basic_payload(payload):
    if not isinstance(payload, dict):
        raise SweepResolutionError("JSON object is required")

    unknown = sorted(set(payload) - ALLOWED_FIELDS)
    if unknown:
        raise SweepResolutionError(
            f"Unsupported field(s): {', '.join(unknown)}",
            status_code=400,
            code="unsupported_fields",
        )

    missing = sorted(field for field in REQUIRED_FIELDS if _is_blank(payload.get(field)))
    if missing:
        raise SweepResolutionError(
            f"Missing required field(s): {', '.join(missing)}",
            status_code=400,
            code="missing_required_fields",
        )

    resolution_type = _text(payload, "resolution_type").lower()
    if resolution_type not in RESOLUTION_TYPES:
        raise SweepResolutionError(
            "resolution_type must be approved or refunded",
            status_code=400,
            code="invalid_resolution_type",
        )

    if resolution_type == "refunded":
        missing_refund = sorted(
            field for field in REFUND_REQUIRED_FIELDS if _is_blank(payload.get(field))
        )
        if missing_refund:
            raise SweepResolutionError(
                f"Missing refund evidence field(s): {', '.join(missing_refund)}",
                status_code=400,
                code="missing_refund_evidence",
            )
        _parse_refund_amount(payload.get("refund_amount"))
    else:
        refund_fields = sorted(
            field for field in OPTIONAL_FIELDS if not _is_blank(payload.get(field))
        )
        if refund_fields:
            raise SweepResolutionError(
                f"Refund evidence is only valid for refunded resolutions: {', '.join(refund_fields)}",
                status_code=400,
                code="unexpected_refund_evidence",
            )

    return resolution_type


def _parse_refund_amount(value):
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError):
        raise SweepResolutionError(
            "refund_amount must be a decimal value",
            status_code=400,
            code="invalid_refund_amount",
        )
    if not amount.is_finite() or amount <= 0:
        raise SweepResolutionError(
            "refund_amount must be a positive finite decimal value",
            status_code=400,
            code="invalid_refund_amount",
        )
    return amount


def _canonical_payload(payload, resolution_type):
    canonical = {}
    for field in sorted(ALLOWED_FIELDS):
        if field == "idempotency_key":
            continue
        if field not in payload or _is_blank(payload.get(field)):
            continue
        value = _text(payload, field)
        if field == "resolution_type":
            value = resolution_type
        elif field == "crypto":
            value = _normalize_crypto(value)
        elif field == "network":
            value = normalize_sweep_network(value)
        canonical[field] = value
    return canonical


def _request_digest(canonical):
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _network_matches(payload, check):
    check_network = check.network or expected_sweep_network(check.transaction.crypto)
    return normalize_sweep_network(payload.get("network")) == normalize_sweep_network(
        check_network
    )


def _validate_deposit(payload):
    check = AmlCheck.query.filter_by(deposit_id=_text(payload, "deposit_id")).first()
    if check is None:
        raise SweepResolutionError(
            "deposit_id was not found",
            status_code=404,
            code="deposit_not_found",
        )
    if not check.sweep_guard_required:
        raise SweepResolutionError(
            "AML check is not sweep guarded",
            status_code=409,
            code="not_guarded",
        )
    if check.status != AmlStatus.MANUAL_REVIEW:
        raise SweepResolutionError(
            "AML check is not in manual_review status",
            status_code=409,
            code="not_manual_review",
        )
    if (
        check.deposit_decision != DepositDecision.MANUAL_REVIEW
        or check.decision_reason not in TERMINAL_MANUAL_REVIEW_REASONS
    ):
        raise SweepResolutionError(
            "AML check is not a terminal manual_review decision",
            status_code=409,
            code="not_terminal_manual_review",
        )

    tx = check.transaction
    if tx is None:
        raise SweepResolutionError(
            "AML check has no transaction",
            status_code=409,
            code="transaction_missing",
        )

    mismatches = []
    if tx.txid != _text(payload, "txid"):
        mismatches.append("txid")
    if _normalize_crypto(tx.crypto) != _normalize_crypto(payload.get("crypto")):
        mismatches.append("crypto")
    if not addresses_match(tx.crypto, tx.addr, _text(payload, "address")):
        mismatches.append("address")
    if not _network_matches(payload, check):
        mismatches.append("network")

    if mismatches:
        raise SweepResolutionError(
            f"Resolution field mismatch: {', '.join(mismatches)}",
            status_code=409,
            code="mismatch",
        )

    existing = (
        AmlSweepResolution.query.filter(
            (AmlSweepResolution.transaction_id == tx.id)
            | (AmlSweepResolution.deposit_id == check.deposit_id)
        )
        .first()
    )

    return check, existing


def _resolution_response(resolution, idempotent=False):
    return {
        "status": "success",
        "idempotent": idempotent,
        "resolution": {
            "id": resolution.id,
            "transaction_id": resolution.transaction_id,
            "deposit_id": resolution.deposit_id,
            "txid": resolution.txid,
            "crypto": resolution.crypto,
            "network": resolution.network,
            "address": resolution.address,
            "resolution_type": resolution.resolution_type,
            "idempotency_key": resolution.idempotency_key,
            "refund_txid": resolution.refund_txid,
            "refund_to_address": resolution.refund_to_address,
            "refund_amount": (
                str(resolution.refund_amount)
                if resolution.refund_amount is not None
                else None
            ),
            "refund_source_address": resolution.refund_source_address,
            "refund_asset": resolution.refund_asset,
            "refund_network": resolution.refund_network,
            "refund_notes": resolution.refund_notes,
            "request_digest": resolution.request_digest,
        },
    }


def _idempotent_resolution_after_integrity_error(idempotency_key, digest):
    existing = AmlSweepResolution.query.filter_by(
        idempotency_key=idempotency_key
    ).first()
    if existing is None:
        return None
    if existing.request_digest != digest:
        raise SweepResolutionError(
            "idempotency_key was already used for a different request",
            status_code=409,
            code="idempotency_conflict",
        )
    return _resolution_response(existing, idempotent=True)


def record_sweep_resolution(payload):
    resolution_type = _validate_basic_payload(payload)
    canonical = _canonical_payload(payload, resolution_type)
    digest = _request_digest(canonical)
    idempotency_key = _text(payload, "idempotency_key")

    existing_idempotency = AmlSweepResolution.query.filter_by(
        idempotency_key=idempotency_key
    ).first()
    if existing_idempotency is not None:
        if existing_idempotency.request_digest != digest:
            raise SweepResolutionError(
                "idempotency_key was already used for a different request",
                status_code=409,
                code="idempotency_conflict",
            )
        return _resolution_response(existing_idempotency, idempotent=True)

    check, existing_resolution = _validate_deposit(payload)
    tx = check.transaction

    if existing_resolution is not None:
        if existing_resolution.request_digest == digest:
            return _resolution_response(existing_resolution, idempotent=True)
        raise SweepResolutionError(
            "Deposit already has a different sweep resolution",
            status_code=409,
            code="already_resolved",
        )

    resolution = AmlSweepResolution(
        transaction_id=tx.id,
        deposit_id=check.deposit_id,
        txid=tx.txid,
        crypto=_normalize_crypto(tx.crypto),
        network=normalize_sweep_network(payload.get("network")),
        address=tx.addr,
        resolution_type=resolution_type,
        reviewer=_text(payload, "reviewer"),
        reason=_text(payload, "reason"),
        external_review_id=_text(payload, "external_review_id"),
        idempotency_key=idempotency_key,
        refund_txid=_text(payload, "refund_txid"),
        refund_to_address=_text(payload, "refund_to_address"),
        refund_amount=(
            _parse_refund_amount(payload.get("refund_amount"))
            if not _is_blank(payload.get("refund_amount"))
            else None
        ),
        refund_source_address=_text(payload, "refund_source_address"),
        refund_asset=_text(payload, "refund_asset"),
        refund_network=(
            normalize_sweep_network(payload.get("refund_network"))
            if not _is_blank(payload.get("refund_network"))
            else None
        ),
        refund_notes=_text(payload, "refund_notes"),
        request_digest=digest,
    )
    db.session.add(resolution)
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        idempotent = _idempotent_resolution_after_integrity_error(
            idempotency_key, digest
        )
        if idempotent is not None:
            return idempotent
        raise SweepResolutionError(
            "Resolution conflicts with an existing audit row",
            status_code=409,
            code="resolution_conflict",
        ) from exc
    return _resolution_response(resolution)
