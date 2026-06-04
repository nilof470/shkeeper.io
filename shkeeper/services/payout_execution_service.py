from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.exc import IntegrityError

from shkeeper import db
from shkeeper.models import (
    PayoutExecution,
    PayoutExecutionResolutionAudit,
    PayoutExecutionState,
    PayoutFailureClass,
    PayoutResolutionStatus,
)
from shkeeper.services.payout_contract import (
    PAYOUT_CONTRACT_VERSION,
    canonical_request_payload,
    canonical_sidecar_payload,
    canonical_usdt_amount,
    hash_payload,
    normalize_external_id,
)
from shkeeper.services.payout_address_validation import validate_payout_destination
from shkeeper.services.payout_errors import PayoutConflictError, PayoutRequestError
from shkeeper.services.payout_execution_auth import is_consumer_key_allowed_for_rail
from shkeeper.services.payout_rail_catalog import PayoutRailCatalog


class PayoutExecutionService:
    REQUEST_FIELDS = frozenset(
        (
            "external_id",
            "asset",
            "network",
            "amount",
            "destination",
        )
    )
    SIDECAR_EVIDENCE_FIELDS = (
        "consumer",
        "execution_id",
        "sidecar_execution_id",
        "external_id",
        "contract_version",
        "asset",
        "network",
        "amount",
        "destination",
        "state",
        "state_version",
        "state_transition_id",
        "state_updated_at",
        "request_hash",
        "sidecar_payload_hash",
        "source_wallet",
        "token_contract",
        "jetton_master",
        "jetton_wallet",
        "chain_id_or_network_id",
        "reference_block_or_masterchain_seqno",
        "transaction_expiration_or_valid_until",
        "lease_owner",
        "lease_expires_at",
        "attempt_id",
        "nonce_or_seqno",
        "nonce_seqno_reserved_at",
        "resource_reservation_id",
        "resource_reservation_status",
        "signed_payload_storage_ref",
        "signed_payload_hash",
        "signed_payload_stored_at",
        "txid_or_message_hash",
        "broadcast_attempted_at",
        "broadcast_provider",
        "last_chain_check_at",
        "last_chain_check_source",
        "failure_class",
        "error_code",
        "error_message",
        "txids",
        "message_hashes",
    )
    SIDECAR_EVIDENCE_DATETIME_FIELDS = {
        "state_updated_at",
        "transaction_expiration_or_valid_until",
        "lease_expires_at",
        "nonce_seqno_reserved_at",
        "signed_payload_stored_at",
        "broadcast_attempted_at",
        "last_chain_check_at",
    }

    SIDECAR_STATE_MAP = {
        "RECEIVED": PayoutExecutionState.ENQUEUED,
        "VALIDATED": PayoutExecutionState.ENQUEUED,
        "SIGNING": PayoutExecutionState.ENQUEUED,
        "SIGNED": PayoutExecutionState.ENQUEUED,
        "BROADCASTING": PayoutExecutionState.BROADCAST,
        "BROADCASTED": PayoutExecutionState.BROADCAST,
        "CONFIRMING": PayoutExecutionState.BROADCAST,
        "CONFIRMED": PayoutExecutionState.CONFIRMED,
        "FAILED_PRE_BROADCAST": PayoutExecutionState.FAILED_PRE_BROADCAST,
        "FAILED_CHAIN_TERMINAL": PayoutExecutionState.FAILED_CHAIN_TERMINAL,
        "RECONCILIATION_REQUIRED": PayoutExecutionState.RECONCILIATION_REQUIRED,
    }

    @staticmethod
    def _normalize_asset_network(payload):
        asset = str(payload.get("asset", "")).strip().upper()
        network = str(payload.get("network", "")).strip().upper()
        if asset != "USDT":
            raise PayoutRequestError(
                "Only USDT payout execution is supported",
                code="UNSUPPORTED_ASSET",
            )
        if network not in ("TRON", "TON", "ETH"):
            raise PayoutRequestError(
                "Unsupported payout network",
                code="UNSUPPORTED_NETWORK",
            )
        return asset, network

    @staticmethod
    def _normalize_destination(payload, network):
        destination = str(payload.get("destination", "")).strip()
        if not destination:
            raise PayoutRequestError(
                "destination is required",
                code="INVALID_DESTINATION",
            )
        return validate_payout_destination(network, destination)

    @staticmethod
    def _request_identity(payload):
        external_id = normalize_external_id(payload.get("external_id"))
        if not external_id:
            raise PayoutRequestError(
                "external_id is required",
                code="EXTERNAL_ID_REQUIRED",
            )
        return external_id

    @staticmethod
    def _validate_payload_object(payload):
        if not isinstance(payload, dict):
            raise PayoutRequestError(
                "Payout execution request body must be a JSON object",
                code="INVALID_PAYOUT_REQUEST",
            )
        unknown_fields = sorted(set(payload) - PayoutExecutionService.REQUEST_FIELDS)
        if unknown_fields:
            raise PayoutRequestError(
                "Payout execution request contains unsupported fields: "
                f"{', '.join(unknown_fields)}. SHKeeper accepts only execution "
                "contract fields.",
                code="INVALID_PAYOUT_REQUEST",
            )

    @staticmethod
    def _validate_key_scope(consumer, key_id, asset, network):
        if key_id is None:
            return
        if not is_consumer_key_allowed_for_rail(consumer, key_id, asset, network):
            raise PayoutRequestError(
                "Payout auth key is not allowed for this rail",
                code="PAYOUT_AUTH_RAIL_FORBIDDEN",
                status_code=403,
            )

    @classmethod
    def _execution_amount_string(cls, execution):
        return format(Decimal(execution.amount).quantize(Decimal("0.000001")), "f")

    @classmethod
    def _resolution_status(cls, value):
        try:
            return PayoutResolutionStatus[str(value)]
        except (KeyError, TypeError):
            raise PayoutRequestError(
                "Unsupported payout manual resolution status",
                code="PAYOUT_MANUAL_RESOLUTION_STATUS_INVALID",
            )

    @staticmethod
    def _require_evidence_object(evidence):
        if not isinstance(evidence, dict) or not evidence:
            raise PayoutRequestError(
                "Manual resolution evidence is required",
                code="PAYOUT_MANUAL_RESOLUTION_EVIDENCE_REQUIRED",
            )

    @classmethod
    def _require_resolution_field(cls, evidence, field):
        if field not in evidence or evidence.get(field) in ("", [], {}):
            raise PayoutRequestError(
                f"Manual resolution evidence missing field: {field}",
                code="PAYOUT_MANUAL_RESOLUTION_EVIDENCE_REQUIRED",
            )

    @classmethod
    def _require_resolution_match(cls, evidence, field, expected):
        cls._require_resolution_field(evidence, field)
        actual = evidence.get(field)
        if actual != expected and str(actual) != str(expected):
            raise PayoutRequestError(
                f"Manual resolution evidence field mismatch: {field}",
                code="PAYOUT_MANUAL_RESOLUTION_EVIDENCE_MISMATCH",
            )

    @classmethod
    def _validate_common_resolution_evidence(cls, execution, evidence):
        cls._require_evidence_object(evidence)
        cls._require_resolution_match(evidence, "network", execution.network)
        cls._require_resolution_match(evidence, "asset", execution.asset)
        cls._require_resolution_match(evidence, "execution_id", execution.id)
        cls._require_resolution_match(evidence, "external_id", execution.external_id)
        cls._require_resolution_match(evidence, "destination", execution.destination)
        cls._require_resolution_match(
            evidence,
            "amount",
            cls._execution_amount_string(execution),
        )
        cls._require_resolution_match(evidence, "last_state", execution.state.name)
        if "last_sidecar_state" not in evidence:
            raise PayoutRequestError(
                "Manual resolution evidence missing field: last_sidecar_state",
                code="PAYOUT_MANUAL_RESOLUTION_EVIDENCE_REQUIRED",
            )
        if execution.sidecar_state is not None:
            cls._require_resolution_match(
                evidence,
                "last_sidecar_state",
                execution.sidecar_state,
            )
        if execution.sidecar_execution_id is not None:
            cls._require_resolution_match(
                evidence,
                "sidecar_execution_id",
                execution.sidecar_execution_id,
            )
        for field in ("source_wallet", "checked_sources"):
            cls._require_resolution_field(evidence, field)
        if not (
            evidence.get("token_contract")
            or evidence.get("jetton_master")
            or evidence.get("token_or_jetton_contract")
        ):
            raise PayoutRequestError(
                "Manual resolution evidence missing token contract or jetton master",
                code="PAYOUT_MANUAL_RESOLUTION_EVIDENCE_REQUIRED",
            )

    @staticmethod
    def _has_search_range(evidence):
        return any(
            evidence.get(field)
            for field in (
                "searched_block_range",
                "searched_masterchain_range",
                "searched_time_range",
            )
        )

    @classmethod
    def _sidecar_evidence_object(cls, execution):
        if not execution.last_sidecar_status_json:
            return {}
        return json.loads(execution.last_sidecar_status_json)

    @classmethod
    def _validate_manual_resolution_evidence(cls, execution, status, evidence):
        cls._validate_common_resolution_evidence(execution, evidence)
        if status == PayoutResolutionStatus.SAFE_FOR_MANUAL_PAYOUT:
            if evidence.get("matching_transfer_found") is not False:
                raise PayoutRequestError(
                    "Manual payout requires negative transfer evidence",
                    code="PAYOUT_MANUAL_RESOLUTION_NEGATIVE_EVIDENCE_REQUIRED",
                )
            if evidence.get("pending_original_artifact") is not False:
                raise PayoutRequestError(
                    "Manual payout requires no pending original artifact",
                    code="PAYOUT_MANUAL_RESOLUTION_NEGATIVE_EVIDENCE_REQUIRED",
                )
            if not cls._has_search_range(evidence):
                raise PayoutRequestError(
                    "Manual payout requires searched block, masterchain, or time range",
                    code="PAYOUT_MANUAL_RESOLUTION_EVIDENCE_REQUIRED",
                )
            sidecar_evidence = cls._sidecar_evidence_object(execution)
            if (
                sidecar_evidence.get("signed_payload_hash")
                and evidence.get("signed_artifact_finality_checked") is not True
            ):
                raise PayoutRequestError(
                    "Manual payout requires signed artifact finality evidence",
                    code="PAYOUT_MANUAL_RESOLUTION_NEGATIVE_EVIDENCE_REQUIRED",
                )
        elif status == PayoutResolutionStatus.CHAIN_BROADCAST_FOUND:
            if evidence.get("matching_transfer_found") is not True:
                raise PayoutRequestError(
                    "Broadcast-found resolution requires matching transfer evidence",
                    code="PAYOUT_MANUAL_RESOLUTION_EVIDENCE_REQUIRED",
                )
            if not evidence.get("txids") and not evidence.get("message_hashes"):
                raise PayoutRequestError(
                    "Broadcast-found resolution requires txid or message hash evidence",
                    code="PAYOUT_MANUAL_RESOLUTION_EVIDENCE_REQUIRED",
                )
        elif status == PayoutResolutionStatus.MANUAL_PAYOUT_PENDING:
            if evidence.get("manual_payout_prepared") is not True:
                raise PayoutRequestError(
                    "Manual payout pending requires prepared evidence",
                    code="PAYOUT_MANUAL_RESOLUTION_EVIDENCE_REQUIRED",
                )
        elif status == PayoutResolutionStatus.MANUAL_PAYOUT_COMPLETED:
            for field in ("manual_txid_or_message_hash", "manual_payout_source_wallet"):
                cls._require_resolution_field(evidence, field)
        elif status == PayoutResolutionStatus.CANCELLED_PRE_BROADCAST:
            if evidence.get("pre_broadcast_failure_confirmed") is not True:
                raise PayoutRequestError(
                    "Pre-broadcast cancellation requires confirmation evidence",
                    code="PAYOUT_MANUAL_RESOLUTION_EVIDENCE_REQUIRED",
                )

    @classmethod
    def _manual_resolution_target_state(cls, execution, status):
        current = execution.state
        if status == PayoutResolutionStatus.SAFE_FOR_MANUAL_PAYOUT:
            allowed = {
                PayoutExecutionState.RECONCILIATION_REQUIRED,
                PayoutExecutionState.MANUAL_REVIEW,
                PayoutExecutionState.FAILED_CHAIN_TERMINAL,
            }
            target = PayoutExecutionState.SAFE_FOR_MANUAL_PAYOUT
        elif status == PayoutResolutionStatus.CHAIN_BROADCAST_FOUND:
            allowed = {
                PayoutExecutionState.RECONCILIATION_REQUIRED,
                PayoutExecutionState.MANUAL_REVIEW,
                PayoutExecutionState.SAFE_FOR_MANUAL_PAYOUT,
            }
            target = PayoutExecutionState.BROADCAST
        elif status == PayoutResolutionStatus.MANUAL_PAYOUT_PENDING:
            allowed = {PayoutExecutionState.SAFE_FOR_MANUAL_PAYOUT}
            target = PayoutExecutionState.MANUAL_PAYOUT_PENDING
        elif status == PayoutResolutionStatus.MANUAL_PAYOUT_COMPLETED:
            allowed = {PayoutExecutionState.MANUAL_PAYOUT_PENDING}
            target = PayoutExecutionState.MANUAL_PAYOUT_COMPLETED
        elif status == PayoutResolutionStatus.CANCELLED_PRE_BROADCAST:
            allowed = {
                PayoutExecutionState.FAILED_PRE_BROADCAST,
                PayoutExecutionState.RECONCILIATION_REQUIRED,
            }
            target = PayoutExecutionState.FAILED_PRE_BROADCAST
        else:
            raise PayoutRequestError(
                "UNRESOLVED is not an operator resolution action",
                code="PAYOUT_MANUAL_RESOLUTION_STATUS_INVALID",
            )
        if current not in allowed:
            raise PayoutConflictError(
                f"Manual resolution {status.name} is not allowed from {current.name}",
                code="PAYOUT_MANUAL_RESOLUTION_INVALID_STATE",
            )
        return target

    @classmethod
    def _canonicalize(cls, consumer, payload):
        external_id = cls._request_identity(payload)
        asset, network = cls._normalize_asset_network(payload)
        destination = cls._normalize_destination(payload, network)
        amount_decimal, amount_string = canonical_usdt_amount(payload.get("amount"))
        rail = PayoutRailCatalog.get_enabled_execution_rail(
            consumer,
            asset,
            network,
        )
        if not rail.callback_endpoint_id:
            raise PayoutRequestError(
                "Payout rail callback endpoint is not configured",
                code="PAYOUT_CALLBACK_ENDPOINT_REQUIRED",
                status_code=503,
            )
        from shkeeper.services.payout_callback_outbox import PayoutCallbackOutbox

        PayoutCallbackOutbox.require_endpoint_configured(
            consumer,
            rail.callback_endpoint_id,
        )
        contract_version = rail.contract_version or PAYOUT_CONTRACT_VERSION
        request_payload = canonical_request_payload(
            consumer=consumer,
            external_id=external_id,
            asset=asset,
            network=network,
            amount=amount_string,
            destination=destination,
            callback_endpoint_id=rail.callback_endpoint_id,
            contract_version=contract_version,
        )
        return {
            "external_id": external_id,
            "asset": asset,
            "network": network,
            "destination": destination,
            "amount_decimal": amount_decimal,
            "amount_string": amount_string,
            "rail": rail,
            "contract_version": contract_version,
            "request_hash": hash_payload(request_payload),
        }

    @classmethod
    def _canonicalize_existing_request(cls, consumer, payload, execution):
        external_id = cls._request_identity(payload)
        asset, network = cls._normalize_asset_network(payload)
        destination = cls._normalize_destination(payload, network)
        _, amount_string = canonical_usdt_amount(payload.get("amount"))
        request_payload = canonical_request_payload(
            consumer=consumer,
            external_id=external_id,
            asset=asset,
            network=network,
            amount=amount_string,
            destination=destination,
            callback_endpoint_id=execution.callback_endpoint_id,
            contract_version=execution.contract_version,
        )
        return {
            "external_id": external_id,
            "request_hash": hash_payload(request_payload),
        }

    @staticmethod
    def _sidecar_payload_hash(execution):
        amount = format(
            Decimal(execution.amount).quantize(Decimal("0.000001")),
            "f",
        )
        return hash_payload(
            canonical_sidecar_payload(
                consumer=execution.consumer,
                execution_id=execution.id,
                external_id=execution.external_id,
                asset=execution.asset,
                network=execution.network,
                amount=amount,
                destination=execution.destination,
                contract_version=execution.contract_version,
            )
        )

    @staticmethod
    def _format_datetime(value):
        if value is None:
            return None
        if value.tzinfo is not None:
            return value.isoformat()
        return f"{value.isoformat()}Z"

    @staticmethod
    def _parse_sidecar_datetime(value):
        if isinstance(value, datetime):
            if value.tzinfo is not None:
                return value.astimezone(timezone.utc).replace(tzinfo=None)
            return value
        if not value:
            return None
        try:
            normalized = str(value).strip()
            if normalized.endswith("Z"):
                normalized = f"{normalized[:-1]}+00:00"
            parsed = datetime.fromisoformat(normalized)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed

    @classmethod
    def _state_occurred_at(cls, execution):
        if execution.last_state_occurred_at is not None:
            return execution.last_state_occurred_at
        if (
            execution.state == PayoutExecutionState.CONFIRMED
            and execution.confirmed_at is not None
        ):
            return execution.confirmed_at
        if (
            execution.state == PayoutExecutionState.BROADCAST
            and execution.broadcasted_at is not None
        ):
            return execution.broadcasted_at
        if (
            execution.state
            in (
                PayoutExecutionState.FAILED_PRE_BROADCAST,
                PayoutExecutionState.FAILED_CHAIN_TERMINAL,
            )
            and execution.terminal_at is not None
        ):
            return execution.terminal_at
        if execution.submitted_at is not None:
            return execution.submitted_at
        return execution.created_at

    @staticmethod
    def _utcnow():
        return datetime.now(timezone.utc).replace(tzinfo=None)

    @staticmethod
    def _json_list(values):
        return json.dumps(list(values or []), separators=(",", ":"))

    @classmethod
    def _json_safe_value(cls, value):
        if isinstance(value, Decimal):
            return format(value, "f")
        if isinstance(value, datetime):
            return cls._format_datetime(value)
        if isinstance(value, (list, tuple)):
            return [cls._json_safe_value(item) for item in value]
        if isinstance(value, dict):
            return {
                str(key): cls._json_safe_value(val)
                for key, val in sorted(value.items())
            }
        return value

    @classmethod
    def _normalize_evidence_field(cls, field, value):
        if field in cls.SIDECAR_EVIDENCE_DATETIME_FIELDS:
            parsed = cls._parse_sidecar_datetime(value)
            return cls._format_datetime(parsed) if parsed is not None else None
        return cls._json_safe_value(value)

    @classmethod
    def _sidecar_status_snapshot(
        cls,
        execution,
        status,
        sidecar_state,
        sidecar_version,
        transition_id,
        state_updated_at,
    ):
        snapshot = {field: None for field in cls.SIDECAR_EVIDENCE_FIELDS}
        snapshot.update(
            {
                "consumer": execution.consumer,
                "execution_id": execution.id,
                "sidecar_execution_id": status.get("sidecar_execution_id"),
                "external_id": execution.external_id,
                "contract_version": execution.contract_version,
                "asset": execution.asset,
                "network": execution.network,
                "amount": format(
                    Decimal(execution.amount).quantize(Decimal("0.000001")),
                    "f",
                ),
                "destination": execution.destination,
                "state": sidecar_state,
                "state_version": sidecar_version,
                "state_transition_id": transition_id,
                "state_updated_at": cls._format_datetime(state_updated_at),
                "request_hash": execution.request_hash,
                "sidecar_payload_hash": execution.sidecar_payload_hash,
                "txids": cls._json_safe_value(status.get("txids")),
                "message_hashes": cls._json_safe_value(status.get("message_hashes")),
            }
        )
        for field in cls.SIDECAR_EVIDENCE_FIELDS:
            if field in snapshot and snapshot[field] is not None:
                continue
            if field in status:
                snapshot[field] = cls._normalize_evidence_field(field, status.get(field))
        return snapshot

    @staticmethod
    def _compact_json(data):
        return json.dumps(data, sort_keys=True, separators=(",", ":"))

    @classmethod
    def _sidecar_status_hash(cls, snapshot):
        return hash_payload(snapshot)

    @classmethod
    def transition(
        cls,
        execution,
        state,
        *,
        failure_class=None,
        error_code=None,
        error_message=None,
        reconciliation_required=None,
        occurred_at=None,
        before_commit=None,
        force_event=False,
    ):
        occurred_at = occurred_at or cls._utcnow()
        if isinstance(state, str):
            state = PayoutExecutionState[state]
        if isinstance(failure_class, str):
            failure_class = PayoutFailureClass[failure_class]

        previous_state = execution.state
        state_changed = previous_state != state
        event_required = state_changed or force_event
        if event_required:
            execution.event_version = (execution.event_version or 0) + 1
            execution.state_transition_id = str(uuid.uuid4())
            execution.last_state_occurred_at = occurred_at
        execution.state = state
        execution.failure_class = failure_class
        execution.error_code = error_code
        execution.error_message = error_message

        if reconciliation_required is None:
            reconciliation_required = state == PayoutExecutionState.RECONCILIATION_REQUIRED
        execution.reconciliation_required = reconciliation_required

        if state in (PayoutExecutionState.ENQUEUEING, PayoutExecutionState.ENQUEUED):
            execution.submitted_at = execution.submitted_at or occurred_at
        elif state == PayoutExecutionState.BROADCAST:
            execution.broadcasted_at = execution.broadcasted_at or occurred_at
        elif state == PayoutExecutionState.CONFIRMED:
            execution.confirmed_at = execution.confirmed_at or occurred_at
        elif state in (
            PayoutExecutionState.FAILED_PRE_BROADCAST,
            PayoutExecutionState.FAILED_CHAIN_TERMINAL,
            PayoutExecutionState.MANUAL_PAYOUT_COMPLETED,
        ):
            execution.terminal_at = execution.terminal_at or occurred_at

        try:
            db.session.add(execution)
            if event_required:
                from shkeeper.services.payout_callback_outbox import (
                    PayoutCallbackOutbox,
                )

                PayoutCallbackOutbox.add_transition_event(
                    execution,
                    previous_state=previous_state,
                    current_state=state,
                    occurred_at=occurred_at,
                )
            if before_commit is not None:
                before_commit(
                    execution=execution,
                    previous_state=previous_state,
                    current_state=state,
                    state_changed=state_changed,
                    event_required=event_required,
                    occurred_at=occurred_at,
                )
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return execution

    @classmethod
    def _move_to_reconciliation(
        cls,
        execution,
        message,
        failure_class=None,
        error_code="SIDECAR_STATUS_AMBIGUOUS",
    ):
        return cls.transition(
            execution,
            PayoutExecutionState.RECONCILIATION_REQUIRED,
            failure_class=failure_class or PayoutFailureClass.AMBIGUOUS,
            error_code=error_code,
            error_message=str(message),
            reconciliation_required=True,
        )

    @staticmethod
    def _status_value_matches(expected, actual):
        return actual is not None and str(actual) == str(expected)

    @classmethod
    def _validate_sidecar_identity(cls, execution, status):
        expected_fields = {
            "consumer": execution.consumer,
            "execution_id": execution.id,
            "external_id": execution.external_id,
            "contract_version": execution.contract_version,
            "asset": execution.asset,
            "network": execution.network,
            "request_hash": execution.request_hash,
            "sidecar_payload_hash": execution.sidecar_payload_hash,
        }
        for field, expected in expected_fields.items():
            if not cls._status_value_matches(expected, status.get(field)):
                return field
        return None

    @classmethod
    def _sidecar_status_conflicts(cls, execution, status, status_hash=None):
        sidecar_state = status.get("sidecar_state") or status.get("state")
        transition_id = (
            status.get("sidecar_state_transition_id")
            or status.get("state_transition_id")
        )
        incoming_sidecar_execution_id = status.get("sidecar_execution_id")
        if (
            execution.sidecar_execution_id
            and incoming_sidecar_execution_id
            and execution.sidecar_execution_id != incoming_sidecar_execution_id
        ):
            return True
        if execution.sidecar_state != sidecar_state:
            return True
        if execution.sidecar_state_transition_id != transition_id:
            return True
        incoming_state_updated_at = cls._parse_sidecar_datetime(
            status.get("sidecar_state_updated_at") or status.get("state_updated_at")
        )
        if (
            incoming_state_updated_at is not None
            and execution.sidecar_state_updated_at is not None
            and execution.sidecar_state_updated_at != incoming_state_updated_at
        ):
            return True
        if (
            status_hash
            and execution.last_sidecar_status_hash
            and execution.last_sidecar_status_hash != status_hash
        ):
            return True
        incoming_txids = status.get("txids")
        if (
            incoming_txids is not None
            and execution.txids_json != cls._json_list(incoming_txids)
        ):
            return True
        incoming_hashes = status.get("message_hashes")
        if (
            incoming_hashes is not None
            and execution.message_hashes_json != cls._json_list(incoming_hashes)
        ):
            return True
        return False

    @classmethod
    def apply_sidecar_status(cls, execution, status):
        identity_mismatch = cls._validate_sidecar_identity(execution, status)
        if identity_mismatch:
            return cls._move_to_reconciliation(
                execution,
                f"Sidecar status identity mismatch: {identity_mismatch}",
                error_code="SIDECAR_STATUS_IDENTITY_MISMATCH",
            )

        sidecar_state = status.get("sidecar_state") or status.get("state")
        if not sidecar_state:
            return cls._move_to_reconciliation(
                execution,
                "Sidecar status is missing sidecar_state",
            )

        try:
            sidecar_version = int(
                status.get("sidecar_state_version")
                if status.get("sidecar_state_version") is not None
                else status.get("state_version")
            )
        except (TypeError, ValueError):
            return cls._move_to_reconciliation(
                execution,
                "Sidecar status is missing state_version",
            )

        transition_id = (
            status.get("sidecar_state_transition_id")
            or status.get("state_transition_id")
        )
        if not transition_id:
            return cls._move_to_reconciliation(
                execution,
                "Sidecar status is missing state_transition_id",
            )

        sidecar_state_updated_at = cls._parse_sidecar_datetime(
            status.get("sidecar_state_updated_at") or status.get("state_updated_at")
        )
        if sidecar_state_updated_at is None:
            return cls._move_to_reconciliation(
                execution,
                "Sidecar status is missing or invalid state_updated_at",
            )

        sidecar_status_snapshot = cls._sidecar_status_snapshot(
            execution,
            status,
            sidecar_state,
            sidecar_version,
            transition_id,
            sidecar_state_updated_at,
        )
        sidecar_status_hash = cls._sidecar_status_hash(sidecar_status_snapshot)

        existing_version = execution.sidecar_state_version
        if existing_version is not None:
            if sidecar_version < existing_version:
                return execution
            if sidecar_version == existing_version:
                if cls._sidecar_status_conflicts(
                    execution,
                    status,
                    sidecar_status_hash,
                ):
                    return cls._move_to_reconciliation(
                        execution,
                        "Sidecar returned conflicting same-version status",
                    )
                return execution

        sidecar_execution_id = status.get("sidecar_execution_id")
        if (
            execution.sidecar_execution_id
            and sidecar_execution_id
            and execution.sidecar_execution_id != sidecar_execution_id
        ):
            return cls._move_to_reconciliation(
                execution,
                "Sidecar execution id changed",
            )
        if sidecar_execution_id:
            execution.sidecar_execution_id = sidecar_execution_id

        execution.sidecar_state = sidecar_state
        execution.sidecar_state_version = sidecar_version
        execution.sidecar_state_transition_id = transition_id
        execution.sidecar_state_updated_at = sidecar_state_updated_at
        execution.last_sidecar_status_hash = sidecar_status_hash
        execution.last_sidecar_status_json = cls._compact_json(sidecar_status_snapshot)
        execution.last_sidecar_status_observed_at = cls._utcnow()
        if status.get("txids") is not None:
            execution.txids_json = cls._json_list(status.get("txids"))
        if status.get("message_hashes") is not None:
            execution.message_hashes_json = cls._json_list(status.get("message_hashes"))
        status_has_error_code = "error_code" in status
        status_has_error_message = "error_message" in status
        status_error_code = status.get("error_code") if status_has_error_code else None
        status_error_message = (
            status.get("error_message") if status_has_error_message else None
        )

        state = cls.SIDECAR_STATE_MAP.get(sidecar_state)
        if state is None:
            return cls._move_to_reconciliation(
                execution,
                f"Unknown sidecar state: {sidecar_state}",
            )

        failure_class = None
        reconciliation_required = False
        if state == PayoutExecutionState.FAILED_PRE_BROADCAST:
            failure_class = PayoutFailureClass.PREFLIGHT
        elif state == PayoutExecutionState.FAILED_CHAIN_TERMINAL:
            failure_class = PayoutFailureClass.CHAIN_TERMINAL
        elif state == PayoutExecutionState.RECONCILIATION_REQUIRED:
            failure_class = PayoutFailureClass.AMBIGUOUS
            reconciliation_required = True

        if state in (
            PayoutExecutionState.FAILED_PRE_BROADCAST,
            PayoutExecutionState.FAILED_CHAIN_TERMINAL,
            PayoutExecutionState.RECONCILIATION_REQUIRED,
        ):
            error_code = status_error_code if status_has_error_code else execution.error_code
            error_message = (
                status_error_message
                if status_has_error_message
                else execution.error_message
            )
        else:
            error_code = status_error_code if status_has_error_code else None
            error_message = status_error_message if status_has_error_message else None

        return cls.transition(
            execution,
            state,
            failure_class=failure_class,
            error_code=error_code,
            error_message=error_message,
            reconciliation_required=reconciliation_required,
        )

    @classmethod
    def _execution_response(cls, execution, status="ACCEPTED"):
        return {
            "status": status,
            "consumer": execution.consumer,
            "execution_id": execution.id,
            "sidecar_execution_id": execution.sidecar_execution_id,
            "external_id": execution.external_id,
            "contract_version": execution.contract_version,
            "event_version": execution.event_version,
            "state_transition_id": execution.state_transition_id,
            "occurred_at": cls._format_datetime(cls._state_occurred_at(execution)),
            "updated_at": cls._format_datetime(execution.updated_at),
            "asset": execution.asset,
            "network": execution.network,
            "crypto_id": execution.crypto_id,
            "sidecar_symbol": execution.sidecar_symbol,
            "payout_queue": execution.payout_queue,
            "source_wallet_ref": execution.source_wallet_ref,
            "state": execution.state.name,
            "failure_class": (
                execution.failure_class.name if execution.failure_class else None
            ),
            "amount": format(
                Decimal(execution.amount).quantize(Decimal("0.000001")),
                "f",
            ),
            "destination": execution.destination,
            "callback_endpoint_id": execution.callback_endpoint_id,
            "request_hash": execution.request_hash,
            "sidecar_payload_hash": execution.sidecar_payload_hash,
            "sidecar_state": execution.sidecar_state,
            "sidecar_state_version": execution.sidecar_state_version,
            "txids": json.loads(execution.txids_json or "[]"),
            "sidecar_state_updated_at": cls._format_datetime(
                execution.sidecar_state_updated_at
            ),
            "sidecar_status_hash": execution.last_sidecar_status_hash,
            "sidecar_status_observed_at": cls._format_datetime(
                execution.last_sidecar_status_observed_at
            ),
            "sidecar_evidence": json.loads(
                execution.last_sidecar_status_json or "{}"
            ),
            "message_hashes": json.loads(execution.message_hashes_json or "[]"),
            "error_code": execution.error_code,
            "error_message": execution.error_message,
            "reconciliation_required": execution.reconciliation_required,
            "resolution_status": (
                execution.resolution_status.name
                if execution.resolution_status
                else PayoutResolutionStatus.UNRESOLVED.name
            ),
            "resolution_evidence": json.loads(
                execution.resolution_evidence_json or "{}"
            ),
            "resolution_evidence_hash": execution.resolution_evidence_hash,
            "resolution_operator_note": execution.resolution_operator_note,
            "resolved_by": execution.resolved_by,
            "resolved_at": cls._format_datetime(execution.resolved_at),
        }

    @classmethod
    def record_manual_resolution(
        cls,
        execution_id,
        payload,
        *,
        operator_id,
    ):
        if not operator_id:
            raise PayoutRequestError(
                "Manual resolution requires an authenticated operator",
                code="PAYOUT_MANUAL_RESOLUTION_OPERATOR_REQUIRED",
                status_code=403,
            )
        if not isinstance(payload, dict):
            raise PayoutRequestError(
                "Manual resolution request body must be a JSON object",
                code="PAYOUT_MANUAL_RESOLUTION_REQUEST_INVALID",
            )
        execution = PayoutExecution.query.get(execution_id)
        if execution is None:
            raise PayoutRequestError(
                "Payout execution not found",
                code="PAYOUT_EXECUTION_NOT_FOUND",
                status_code=404,
            )
        resolution_status = cls._resolution_status(payload.get("resolution_status"))
        evidence = payload.get("evidence")
        cls._validate_manual_resolution_evidence(
            execution,
            resolution_status,
            evidence,
        )
        target_state = cls._manual_resolution_target_state(
            execution,
            resolution_status,
        )
        evidence_snapshot = cls._json_safe_value(evidence)
        evidence_hash = hash_payload(evidence_snapshot)
        evidence_json = cls._compact_json(evidence_snapshot)
        operator_note = payload.get("operator_note")
        previous_state = execution.state
        previous_resolution_status = execution.resolution_status

        execution.resolution_status = resolution_status
        execution.resolution_evidence_json = evidence_json
        execution.resolution_evidence_hash = evidence_hash
        execution.resolution_operator_note = operator_note
        execution.resolved_by = operator_id
        execution.resolved_at = cls._utcnow()
        if resolution_status == PayoutResolutionStatus.CHAIN_BROADCAST_FOUND:
            if evidence_snapshot.get("txids"):
                execution.txids_json = cls._json_list(evidence_snapshot.get("txids"))
            if evidence_snapshot.get("message_hashes"):
                execution.message_hashes_json = cls._json_list(
                    evidence_snapshot.get("message_hashes")
                )

        audit = PayoutExecutionResolutionAudit(
            payout_execution_id=execution.id,
            execution_id=execution.id,
            consumer=execution.consumer,
            external_id=execution.external_id,
            action=resolution_status.name,
            previous_state=previous_state.name if previous_state else None,
            new_state=target_state.name,
            previous_resolution_status=(
                previous_resolution_status.name if previous_resolution_status else None
            ),
            resolution_status=resolution_status.name,
            operator_id=operator_id,
            operator_note=operator_note,
            evidence_hash=evidence_hash,
            evidence_json=evidence_json,
            state_transition_id=None,
        )

        def add_resolution_audit(**kwargs):
            audit.state_transition_id = kwargs["execution"].state_transition_id
            db.session.add(audit)

        cls.transition(
            execution,
            target_state,
            failure_class=PayoutFailureClass.OPERATOR_RESOLVED,
            error_code=None,
            error_message=operator_note,
            reconciliation_required=False,
            before_commit=add_resolution_audit,
            force_event=True,
        )
        db.session.refresh(execution)
        return cls._execution_response(execution, status="OK")

    @classmethod
    def submit(cls, consumer, payload, key_id=None):
        cls._validate_payload_object(payload)
        external_id = cls._request_identity(payload)
        existing = PayoutExecution.query.filter_by(
            consumer=consumer,
            external_id=external_id,
        ).first()
        if existing:
            cls._validate_key_scope(
                consumer,
                key_id,
                existing.asset,
                existing.network,
            )
            data = cls._canonicalize_existing_request(consumer, payload, existing)
            if existing.request_hash != data["request_hash"]:
                raise PayoutConflictError(
                    "Payout execution external_id already exists with different request",
                    code="PAYOUT_EXECUTION_CONFLICT",
                )
            return cls._execution_response(existing)

        data = cls._canonicalize(consumer, payload)
        cls._validate_key_scope(consumer, key_id, data["asset"], data["network"])
        rail = data["rail"]
        execution = PayoutExecution(
            consumer=consumer,
            external_id=data["external_id"],
            contract_version=data["contract_version"],
            event_version=1,
            state_transition_id=str(uuid.uuid4()),
            asset=data["asset"],
            network=data["network"],
            crypto_id=rail.crypto_id,
            sidecar_service=rail.sidecar_service,
            sidecar_symbol=rail.sidecar_symbol,
            payout_queue=rail.payout_queue,
            source_wallet_ref=rail.source_wallet_ref,
            amount=data["amount_decimal"],
            destination=data["destination"],
            request_hash=data["request_hash"],
            sidecar_payload_hash="pending",
            callback_endpoint_id=rail.callback_endpoint_id,
            state=PayoutExecutionState.CREATED,
            last_state_occurred_at=cls._utcnow(),
            txids_json="[]",
            message_hashes_json="[]",
            reconciliation_required=False,
        )
        db.session.add(execution)
        try:
            db.session.flush()
            execution.sidecar_payload_hash = cls._sidecar_payload_hash(execution)
            from shkeeper.services.payout_callback_outbox import PayoutCallbackOutbox

            PayoutCallbackOutbox.add_transition_event(
                execution,
                previous_state=None,
                current_state=PayoutExecutionState.CREATED,
                occurred_at=execution.last_state_occurred_at,
            )
            db.session.commit()
        except IntegrityError as exc:
            db.session.rollback()
            existing = PayoutExecution.query.filter_by(
                consumer=consumer,
                external_id=data["external_id"],
            ).first()
            if existing and existing.request_hash == data["request_hash"]:
                return cls._execution_response(existing)
            raise PayoutConflictError(
                "Payout execution external_id already exists",
                code="PAYOUT_EXECUTION_CONFLICT",
            ) from exc
        except Exception:
            db.session.rollback()
            raise
        return cls._execution_response(execution)

    @classmethod
    def status(cls, consumer, external_id, key_id=None):
        normalized = normalize_external_id(external_id)
        if not normalized:
            raise PayoutRequestError(
                "external_id is required",
                code="EXTERNAL_ID_REQUIRED",
            )
        execution = PayoutExecution.query.filter_by(
            consumer=consumer,
            external_id=normalized,
        ).first()
        if not execution:
            raise PayoutRequestError(
                "Payout execution not found",
                code="PAYOUT_EXECUTION_NOT_FOUND",
                status_code=404,
            )
        cls._validate_key_scope(
            consumer,
            key_id,
            execution.asset,
            execution.network,
        )
        return cls._execution_response(execution, status="OK")
