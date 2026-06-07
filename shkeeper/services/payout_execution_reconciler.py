from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_

from shkeeper import db
from shkeeper.models import PayoutExecution, PayoutExecutionState, PayoutFailureClass
from shkeeper.services.payout_errors import PayoutRequestError
from shkeeper.services.payout_execution_service import PayoutExecutionService
from shkeeper.services.payout_sidecar_client import (
    HttpPayoutSidecarClient,
    SidecarExecutionNotFound,
    SidecarStatusUnavailable,
    SidecarSubmitTimeout,
)


logger = logging.getLogger(__name__)


class PayoutExecutionReconciler:
    READY_STATES = (
        PayoutExecutionState.CREATED,
        PayoutExecutionState.PREFLIGHTED,
        PayoutExecutionState.ENQUEUEING,
    )
    POLL_STATES = (
        PayoutExecutionState.ENQUEUED,
        PayoutExecutionState.BROADCAST,
    )

    @classmethod
    def _utcnow(cls):
        return datetime.now(timezone.utc).replace(tzinfo=None)

    @classmethod
    def _preflight_retry_delay(cls, execution):
        attempts = execution.dispatch_attempts or 1
        return min(60 * (2 ** max(attempts - 1, 0)), 3600)

    @classmethod
    def dispatch_ready(cls, *, client=None, batch_size=50, lease_owner=None):
        client = client or HttpPayoutSidecarClient()
        lease_owner = lease_owner or f"payout-reconciler:{uuid.uuid4()}"
        now = cls._utcnow()
        dispatch_states = cls.READY_STATES + cls.POLL_STATES
        executions = (
            PayoutExecution.query.filter(PayoutExecution.state.in_(dispatch_states))
            .filter(
                or_(
                    PayoutExecution.lease_expires_at.is_(None),
                    PayoutExecution.lease_expires_at <= now,
                )
            )
            .filter(
                or_(
                    PayoutExecution.next_dispatch_at.is_(None),
                    PayoutExecution.next_dispatch_at <= now,
                )
            )
            .order_by(PayoutExecution.created_at, PayoutExecution.id)
            .limit(batch_size)
            .all()
        )

        processed = 0
        for execution in executions:
            claimed = cls.claim_execution(execution, lease_owner=lease_owner)
            if claimed is None:
                continue
            claimed_id = claimed.id
            lease_token = claimed.lease_token
            try:
                cls.dispatch_one(claimed, client=client)
            except Exception as exc:
                db.session.rollback()
                cls.release_execution_after_error(
                    claimed_id,
                    lease_owner=lease_owner,
                    lease_token=lease_token,
                    error_message=str(exc),
                )
                logger.exception(
                    "Payout execution dispatch failed",
                    extra={"payout_execution_id": claimed_id},
                )
            else:
                cls.release_execution_by_id(
                    claimed_id,
                    lease_owner=lease_owner,
                    lease_token=lease_token,
                )
            processed += 1
        return processed

    @classmethod
    def claim_execution(cls, execution, *, lease_owner, lease_seconds=300):
        now = cls._utcnow()
        lease_token = str(uuid.uuid4())
        rowcount = (
            PayoutExecution.query.filter(
                PayoutExecution.id == execution.id,
                PayoutExecution.state == execution.state,
            )
            .filter(
                or_(
                    PayoutExecution.lease_expires_at.is_(None),
                    PayoutExecution.lease_expires_at <= now,
                )
            )
            .update(
                {
                    "lease_owner": lease_owner,
                    "lease_token": lease_token,
                    "lease_expires_at": now + timedelta(seconds=lease_seconds),
                },
                synchronize_session=False,
            )
        )
        db.session.commit()
        if rowcount != 1:
            return None
        return PayoutExecution.query.get(execution.id)

    @staticmethod
    def release_execution_by_id(execution_id, *, lease_owner, lease_token):
        (
            PayoutExecution.query.filter_by(
                id=execution_id,
                lease_owner=lease_owner,
                lease_token=lease_token,
            ).update(
                {
                    "lease_owner": None,
                    "lease_token": None,
                    "lease_expires_at": None,
                },
                synchronize_session=False,
            )
        )
        db.session.commit()

    @classmethod
    def release_execution_after_error(
        cls,
        execution_id,
        *,
        lease_owner,
        lease_token,
        error_message,
    ):
        attempts = (
            db.session.query(PayoutExecution.dispatch_attempts)
            .filter(PayoutExecution.id == execution_id)
            .scalar()
            or 0
        )
        delay = min(60 * (2 ** max(attempts - 1, 0)), 3600)
        (
            PayoutExecution.query.filter_by(
                id=execution_id,
                lease_owner=lease_owner,
                lease_token=lease_token,
            ).update(
                {
                    "lease_owner": None,
                    "lease_token": None,
                    "lease_expires_at": None,
                    "next_dispatch_at": cls._utcnow() + timedelta(seconds=delay),
                    "error_code": "PAYOUT_DISPATCH_EXCEPTION",
                    "error_message": error_message,
                },
                synchronize_session=False,
            )
        )
        db.session.commit()

    @classmethod
    def release_execution(cls, execution, *, lease_owner, lease_token):
        cls.release_execution_by_id(
            execution.id,
            lease_owner=lease_owner,
            lease_token=lease_token,
        )

    @classmethod
    def dispatch_one(cls, execution, *, client=None):
        client = client or HttpPayoutSidecarClient()
        execution.dispatch_attempts = (execution.dispatch_attempts or 0) + 1
        db.session.add(execution)
        db.session.commit()

        if execution.state == PayoutExecutionState.CREATED:
            cls._preflight(execution, client)
            db.session.refresh(execution)
            if execution.state != PayoutExecutionState.PREFLIGHTED:
                return execution

        if execution.state == PayoutExecutionState.PREFLIGHTED:
            return cls._submit(execution, client)

        if execution.state == PayoutExecutionState.ENQUEUEING:
            return cls._recover_enqueueing(execution, client)

        if execution.state in cls.POLL_STATES:
            return cls._poll_sidecar_status(execution, client)

        return execution

    @classmethod
    def _preflight(cls, execution, client):
        try:
            response = client.preflight(execution)
        except SidecarStatusUnavailable as exc:
            payload = getattr(exc, "payload", None) or {}
            execution.error_code = (
                payload.get("code") or "SIDECAR_PREFLIGHT_UNAVAILABLE"
            )
            execution.error_message = payload.get("message") or str(exc)
            execution.next_dispatch_at = cls._utcnow() + timedelta(
                seconds=cls._preflight_retry_delay(execution)
            )
            db.session.add(execution)
            db.session.commit()
            return execution
        except PayoutRequestError as exc:
            return PayoutExecutionService.transition(
                execution,
                PayoutExecutionState.FAILED_PRE_BROADCAST,
                failure_class=PayoutFailureClass.PREFLIGHT,
                error_code=exc.code,
                error_message=str(exc),
                reconciliation_required=False,
            )

        if response.get("status") == "error" or response.get("error"):
            return PayoutExecutionService.transition(
                execution,
                PayoutExecutionState.FAILED_PRE_BROADCAST,
                failure_class=PayoutFailureClass.PREFLIGHT,
                error_code=response.get("code") or "SIDECAR_PREFLIGHT_FAILED",
                error_message=(
                    response.get("message")
                    or response.get("error")
                    or "Sidecar preflight failed"
                ),
                reconciliation_required=False,
            )

        return PayoutExecutionService.transition(
            execution,
            PayoutExecutionState.PREFLIGHTED,
            reconciliation_required=False,
        )

    @classmethod
    def _submit(cls, execution, client):
        if execution.state != PayoutExecutionState.ENQUEUEING:
            PayoutExecutionService.transition(
                execution,
                PayoutExecutionState.ENQUEUEING,
                reconciliation_required=False,
            )
            db.session.refresh(execution)

        try:
            response = client.submit(execution)
        except SidecarSubmitTimeout as exc:
            return PayoutExecutionService.transition(
                execution,
                PayoutExecutionState.RECONCILIATION_REQUIRED,
                failure_class=PayoutFailureClass.SIDECAR_TIMEOUT,
                error_code="SIDECAR_SUBMIT_TIMEOUT",
                error_message=str(exc),
                reconciliation_required=True,
            )
        except SidecarStatusUnavailable as exc:
            return PayoutExecutionService.transition(
                execution,
                PayoutExecutionState.RECONCILIATION_REQUIRED,
                failure_class=PayoutFailureClass.AMBIGUOUS,
                error_code="SIDECAR_SUBMIT_UNKNOWN",
                error_message=str(exc),
                reconciliation_required=True,
            )

        if response.get("status") == "error" or response.get("error"):
            return PayoutExecutionService.transition(
                execution,
                PayoutExecutionState.FAILED_PRE_BROADCAST,
                failure_class=PayoutFailureClass.WORKER_UNAVAILABLE,
                error_code=response.get("code") or "SIDECAR_SUBMIT_REJECTED",
                error_message=(
                    response.get("message")
                    or response.get("error")
                    or "Sidecar submit rejected"
                ),
                reconciliation_required=False,
            )

        return PayoutExecutionService.apply_sidecar_status(
            execution,
            cls._accepted_submit_status(response),
        )

    @classmethod
    def _recover_enqueueing(cls, execution, client):
        try:
            response = client.status(execution)
        except SidecarExecutionNotFound:
            return PayoutExecutionService.transition(
                execution,
                PayoutExecutionState.RECONCILIATION_REQUIRED,
                failure_class=PayoutFailureClass.AMBIGUOUS,
                error_code="SIDECAR_EXECUTION_NOT_FOUND_AFTER_SUBMIT_WINDOW",
                error_message=(
                    "Sidecar did not find an ENQUEUEING payout execution. "
                    "The submit window is ambiguous and must be resolved manually."
                ),
                reconciliation_required=True,
            )
        except SidecarStatusUnavailable as exc:
            return PayoutExecutionService.transition(
                execution,
                PayoutExecutionState.RECONCILIATION_REQUIRED,
                failure_class=PayoutFailureClass.AMBIGUOUS,
                error_code="SIDECAR_STATUS_UNAVAILABLE",
                error_message=str(exc),
                reconciliation_required=True,
            )

        return PayoutExecutionService.apply_sidecar_status(execution, response)

    @classmethod
    def _poll_sidecar_status(cls, execution, client):
        try:
            response = client.status(execution)
        except SidecarExecutionNotFound:
            return PayoutExecutionService.transition(
                execution,
                PayoutExecutionState.RECONCILIATION_REQUIRED,
                failure_class=PayoutFailureClass.AMBIGUOUS,
                error_code="SIDECAR_EXECUTION_LOST",
                error_message="Sidecar lost an accepted payout execution",
                reconciliation_required=True,
            )
        return PayoutExecutionService.apply_sidecar_status(execution, response)

    @staticmethod
    def _accepted_submit_status(response):
        accepted = dict(response)
        sidecar_state = response.get("sidecar_state") or response.get("state")
        if sidecar_state == "ACCEPTED":
            sidecar_state = None
        accepted["sidecar_state"] = sidecar_state or "RECEIVED"
        accepted["sidecar_state_version"] = (
            response.get("sidecar_state_version") or response.get("state_version")
        )
        accepted["sidecar_state_transition_id"] = (
            response.get("sidecar_state_transition_id")
            or response.get("state_transition_id")
        )
        accepted["sidecar_state_updated_at"] = (
            response.get("sidecar_state_updated_at") or response.get("state_updated_at")
        )
        return accepted
