from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import prometheus_client

from shkeeper import db
from shkeeper.models import (
    PayoutCallbackEvent,
    PayoutExecution,
    PayoutFailureClass,
    PayoutExecutionState,
    PayoutRail,
)


PAYOUT_EXECUTION_COUNT = prometheus_client.Gauge(
    "shkeeper_payout_execution_count",
    "Payout executions by consumer, rail, and state.",
    ["consumer", "asset", "network", "state"],
)

PAYOUT_NON_TERMINAL_OLDEST_AGE_SECONDS = prometheus_client.Gauge(
    "shkeeper_payout_non_terminal_oldest_age_seconds",
    "Age in seconds of the oldest non-terminal payout execution by state.",
    ["consumer", "asset", "network", "state"],
)

PAYOUT_RECONCILIATION_REQUIRED_COUNT = prometheus_client.Gauge(
    "shkeeper_payout_reconciliation_required_count",
    "Payout executions currently requiring operator reconciliation.",
    ["consumer", "asset", "network"],
)

PAYOUT_CALLBACK_OUTBOX_BACKLOG_COUNT = prometheus_client.Gauge(
    "shkeeper_payout_callback_outbox_backlog_count",
    "Undelivered payout callback outbox events by dispatch status.",
    ["consumer", "asset", "network", "dispatch_status"],
)

PAYOUT_CALLBACK_OUTBOX_OLDEST_AGE_SECONDS = prometheus_client.Gauge(
    "shkeeper_payout_callback_outbox_oldest_age_seconds",
    "Age in seconds of the oldest undelivered payout callback outbox event.",
    ["consumer", "asset", "network", "dispatch_status"],
)

PAYOUT_FAILURE_COUNT = prometheus_client.Gauge(
    "shkeeper_payout_failure_count",
    "Payout executions with failure metadata by failure class and error code.",
    ["consumer", "asset", "network", "state", "failure_class", "error_code"],
)

PAYOUT_DISPATCH_BACKLOG_COUNT = prometheus_client.Gauge(
    "shkeeper_payout_dispatch_backlog_count",
    "DB-backed SHKeeper payout dispatch backlog by queue and state.",
    ["consumer", "asset", "network", "payout_queue", "state"],
)

PAYOUT_DISPATCH_BACKLOG_OLDEST_AGE_SECONDS = prometheus_client.Gauge(
    "shkeeper_payout_dispatch_backlog_oldest_age_seconds",
    "Age in seconds of the oldest due SHKeeper payout dispatch item.",
    ["consumer", "asset", "network", "payout_queue", "state"],
)

PAYOUT_STUCK_EXECUTION_COUNT = prometheus_client.Gauge(
    "shkeeper_payout_stuck_execution_count",
    "Payout executions older than the first-release per-state operational threshold.",
    ["consumer", "asset", "network", "state", "threshold_seconds"],
)

PAYOUT_STUCK_EXECUTION_OLDEST_AGE_SECONDS = prometheus_client.Gauge(
    "shkeeper_payout_stuck_execution_oldest_age_seconds",
    "Age in seconds of the oldest stuck payout execution by state.",
    ["consumer", "asset", "network", "state", "threshold_seconds"],
)

PAYOUT_CONFIRMATION_SLA_BREACH_COUNT = prometheus_client.Gauge(
    "shkeeper_payout_confirmation_sla_breach_count",
    "Broadcast payout executions that have not confirmed within the SLA.",
    ["consumer", "asset", "network", "threshold_seconds"],
)

PAYOUT_CONFIRMATION_SLA_BREACH_OLDEST_AGE_SECONDS = prometheus_client.Gauge(
    "shkeeper_payout_confirmation_sla_breach_oldest_age_seconds",
    "Age in seconds of the oldest broadcast payout execution still awaiting confirmation.",
    ["consumer", "asset", "network", "threshold_seconds"],
)

PAYOUT_ORDERING_CONFLICT_COUNT = prometheus_client.Gauge(
    "shkeeper_payout_ordering_conflict_count",
    "Payout executions in reconciliation because callback/status ordering metadata conflicted.",
    ["consumer", "asset", "network", "error_code"],
)

PAYOUT_RAIL_ENABLED = prometheus_client.Gauge(
    "shkeeper_payout_rail_enabled",
    "Whether payout execution is enabled for the SHKeeper payout rail.",
    ["consumer", "asset", "network", "payout_queue"],
)


NON_TERMINAL_STATES = (
    PayoutExecutionState.CREATED,
    PayoutExecutionState.PREFLIGHTED,
    PayoutExecutionState.ENQUEUEING,
    PayoutExecutionState.ENQUEUED,
    PayoutExecutionState.BROADCAST,
    PayoutExecutionState.RECONCILIATION_REQUIRED,
    PayoutExecutionState.MANUAL_REVIEW,
    PayoutExecutionState.SAFE_FOR_MANUAL_PAYOUT,
    PayoutExecutionState.MANUAL_PAYOUT_PENDING,
)

TERMINAL_STATES = (
    PayoutExecutionState.CONFIRMED,
    PayoutExecutionState.FAILED_PRE_BROADCAST,
    PayoutExecutionState.FAILED_CHAIN_TERMINAL,
    PayoutExecutionState.MANUAL_PAYOUT_COMPLETED,
)

UNDELIVERED_CALLBACK_STATUSES = ("PENDING", "RETRY", "DISPATCHING", "FAILED")
METRIC_ERROR_CODE_RE = re.compile(r"^[A-Z0-9_:-]{1,80}$")
DISPATCH_BACKLOG_STATES = (
    PayoutExecutionState.CREATED,
    PayoutExecutionState.PREFLIGHTED,
    PayoutExecutionState.ENQUEUEING,
    PayoutExecutionState.ENQUEUED,
    PayoutExecutionState.BROADCAST,
)
STUCK_STATE_THRESHOLDS_SECONDS = {
    PayoutExecutionState.CREATED: 300,
    PayoutExecutionState.PREFLIGHTED: 300,
    PayoutExecutionState.ENQUEUEING: 300,
    PayoutExecutionState.ENQUEUED: 900,
    PayoutExecutionState.BROADCAST: 3600,
    PayoutExecutionState.RECONCILIATION_REQUIRED: 1,
    PayoutExecutionState.MANUAL_REVIEW: 1,
}
CONFIRMATION_SLA_SECONDS = 3600
ORDERING_CONFLICT_ERROR_CODES = {
    "SIDECAR_STATUS_AMBIGUOUS",
    "SIDECAR_EXECUTION_LOST",
}

def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _state_name(state):
    return state.name if hasattr(state, "name") else str(state)


def _failure_class_name(failure_class):
    if failure_class is None:
        return ""
    return failure_class.name if hasattr(failure_class, "name") else str(failure_class)


def _metric_error_code(error_code):
    if not error_code:
        return ""
    error_code = str(error_code).strip()
    if METRIC_ERROR_CODE_RE.match(error_code):
        return error_code
    return "OTHER"


def _age_seconds(now, value):
    if value is None:
        return 0
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return max(0, int((now - value).total_seconds()))


def _clear_payout_metrics(clear_process_metrics=True):
    for metric in (
        PAYOUT_EXECUTION_COUNT,
        PAYOUT_NON_TERMINAL_OLDEST_AGE_SECONDS,
        PAYOUT_RECONCILIATION_REQUIRED_COUNT,
        PAYOUT_CALLBACK_OUTBOX_BACKLOG_COUNT,
        PAYOUT_CALLBACK_OUTBOX_OLDEST_AGE_SECONDS,
        PAYOUT_FAILURE_COUNT,
        PAYOUT_DISPATCH_BACKLOG_COUNT,
        PAYOUT_DISPATCH_BACKLOG_OLDEST_AGE_SECONDS,
        PAYOUT_STUCK_EXECUTION_COUNT,
        PAYOUT_STUCK_EXECUTION_OLDEST_AGE_SECONDS,
        PAYOUT_CONFIRMATION_SLA_BREACH_COUNT,
        PAYOUT_CONFIRMATION_SLA_BREACH_OLDEST_AGE_SECONDS,
        PAYOUT_ORDERING_CONFLICT_COUNT,
        PAYOUT_RAIL_ENABLED,
    ):
        metric.clear()


def update_payout_metrics(now=None):
    """Refresh SHKeeper payout metrics from durable DB state."""
    now = now or _utcnow()

    execution_rows = (
        db.session.query(
            PayoutExecution.consumer,
            PayoutExecution.asset,
            PayoutExecution.network,
            PayoutExecution.state,
            db.func.count(PayoutExecution.id),
            db.func.min(PayoutExecution.created_at),
        )
        .group_by(
            PayoutExecution.consumer,
            PayoutExecution.asset,
            PayoutExecution.network,
            PayoutExecution.state,
        )
        .all()
    )

    reconciliation_rows = (
        db.session.query(
            PayoutExecution.consumer,
            PayoutExecution.asset,
            PayoutExecution.network,
            db.func.count(PayoutExecution.id),
        )
        .filter(PayoutExecution.reconciliation_required.is_(True))
        .group_by(
            PayoutExecution.consumer,
            PayoutExecution.asset,
            PayoutExecution.network,
        )
        .all()
    )

    callback_rows = (
        db.session.query(
            PayoutCallbackEvent.consumer,
            PayoutCallbackEvent.asset,
            PayoutCallbackEvent.network,
            PayoutCallbackEvent.dispatch_status,
            db.func.count(PayoutCallbackEvent.id),
            db.func.min(PayoutCallbackEvent.created_at),
        )
        .filter(PayoutCallbackEvent.dispatch_status.in_(UNDELIVERED_CALLBACK_STATUSES))
        .group_by(
            PayoutCallbackEvent.consumer,
            PayoutCallbackEvent.asset,
            PayoutCallbackEvent.network,
            PayoutCallbackEvent.dispatch_status,
        )
        .all()
    )

    failure_rows = (
        db.session.query(
            PayoutExecution.consumer,
            PayoutExecution.asset,
            PayoutExecution.network,
            PayoutExecution.state,
            PayoutExecution.failure_class,
            PayoutExecution.error_code,
            db.func.count(PayoutExecution.id),
        )
        .filter(
            db.or_(
                PayoutExecution.failure_class.isnot(None),
                PayoutExecution.error_code.isnot(None),
            )
        )
        .group_by(
            PayoutExecution.consumer,
            PayoutExecution.asset,
            PayoutExecution.network,
            PayoutExecution.state,
            PayoutExecution.failure_class,
            PayoutExecution.error_code,
        )
        .all()
    )

    backlog_rows = (
        db.session.query(
            PayoutExecution.consumer,
            PayoutExecution.asset,
            PayoutExecution.network,
            PayoutExecution.payout_queue,
            PayoutExecution.state,
            db.func.count(PayoutExecution.id),
            db.func.min(PayoutExecution.updated_at),
        )
        .filter(PayoutExecution.state.in_(DISPATCH_BACKLOG_STATES))
        .filter(
            db.or_(
                PayoutExecution.next_dispatch_at.is_(None),
                PayoutExecution.next_dispatch_at <= now,
            )
        )
        .group_by(
            PayoutExecution.consumer,
            PayoutExecution.asset,
            PayoutExecution.network,
            PayoutExecution.payout_queue,
            PayoutExecution.state,
        )
        .all()
    )

    stuck_rows_by_state = []
    for state, threshold_seconds in STUCK_STATE_THRESHOLDS_SECONDS.items():
        threshold_at = now - timedelta(seconds=threshold_seconds)
        stuck_rows = (
            db.session.query(
                PayoutExecution.consumer,
                PayoutExecution.asset,
                PayoutExecution.network,
                db.func.count(PayoutExecution.id),
                db.func.min(PayoutExecution.updated_at),
            )
            .filter(PayoutExecution.state == state)
            .filter(PayoutExecution.updated_at <= threshold_at)
            .group_by(
                PayoutExecution.consumer,
                PayoutExecution.asset,
                PayoutExecution.network,
            )
            .all()
        )
        stuck_rows_by_state.append((state, threshold_seconds, stuck_rows))

    confirmation_sla_threshold_at = now - timedelta(seconds=CONFIRMATION_SLA_SECONDS)
    confirmation_sla_rows = (
        db.session.query(
            PayoutExecution.consumer,
            PayoutExecution.asset,
            PayoutExecution.network,
            db.func.count(PayoutExecution.id),
            db.func.min(PayoutExecution.broadcasted_at),
        )
        .filter(PayoutExecution.state == PayoutExecutionState.BROADCAST)
        .filter(PayoutExecution.broadcasted_at.isnot(None))
        .filter(PayoutExecution.broadcasted_at <= confirmation_sla_threshold_at)
        .filter(PayoutExecution.confirmed_at.is_(None))
        .group_by(
            PayoutExecution.consumer,
            PayoutExecution.asset,
            PayoutExecution.network,
        )
        .all()
    )

    ordering_conflict_rows = (
        db.session.query(
            PayoutExecution.consumer,
            PayoutExecution.asset,
            PayoutExecution.network,
            PayoutExecution.error_code,
            db.func.count(PayoutExecution.id),
        )
        .filter(PayoutExecution.reconciliation_required.is_(True))
        .filter(PayoutExecution.failure_class == PayoutFailureClass.AMBIGUOUS)
        .filter(PayoutExecution.error_code.in_(ORDERING_CONFLICT_ERROR_CODES))
        .group_by(
            PayoutExecution.consumer,
            PayoutExecution.asset,
            PayoutExecution.network,
            PayoutExecution.error_code,
        )
        .all()
    )

    rail_rows = PayoutRail.query.all()

    _clear_payout_metrics(clear_process_metrics=False)

    for consumer, asset, network, state, count, oldest_created_at in execution_rows:
        state_name = _state_name(state)
        PAYOUT_EXECUTION_COUNT.labels(
            consumer=consumer,
            asset=asset,
            network=network,
            state=state_name,
        ).set(count)
        if state in NON_TERMINAL_STATES:
            PAYOUT_NON_TERMINAL_OLDEST_AGE_SECONDS.labels(
                consumer=consumer,
                asset=asset,
                network=network,
                state=state_name,
            ).set(_age_seconds(now, oldest_created_at))
        elif state not in TERMINAL_STATES:
            PAYOUT_NON_TERMINAL_OLDEST_AGE_SECONDS.labels(
                consumer=consumer,
                asset=asset,
                network=network,
                state=state_name,
            ).set(_age_seconds(now, oldest_created_at))

    for consumer, asset, network, count in reconciliation_rows:
        PAYOUT_RECONCILIATION_REQUIRED_COUNT.labels(
            consumer=consumer,
            asset=asset,
            network=network,
        ).set(count)

    for consumer, asset, network, dispatch_status, count, oldest_created_at in callback_rows:
        PAYOUT_CALLBACK_OUTBOX_BACKLOG_COUNT.labels(
            consumer=consumer or "",
            asset=asset or "",
            network=network or "",
            dispatch_status=dispatch_status,
        ).set(count)
        PAYOUT_CALLBACK_OUTBOX_OLDEST_AGE_SECONDS.labels(
            consumer=consumer or "",
            asset=asset or "",
            network=network or "",
            dispatch_status=dispatch_status,
        ).set(_age_seconds(now, oldest_created_at))

    failure_counts = defaultdict(int)
    for (
        consumer,
        asset,
        network,
        state,
        failure_class,
        error_code,
        count,
    ) in failure_rows:
        failure_counts[
            (
                consumer,
                asset,
                network,
                _state_name(state),
                _failure_class_name(failure_class),
                _metric_error_code(error_code),
            )
        ] += int(count)

    for (
        consumer,
        asset,
        network,
        state_name,
        failure_class,
        error_code,
    ), count in failure_counts.items():
        PAYOUT_FAILURE_COUNT.labels(
            consumer=consumer,
            asset=asset,
            network=network,
            state=state_name,
            failure_class=failure_class,
            error_code=error_code,
        ).set(count)

    for consumer, asset, network, payout_queue, state, count, oldest_updated_at in backlog_rows:
        state_name = _state_name(state)
        labels = {
            "consumer": consumer,
            "asset": asset,
            "network": network,
            "payout_queue": payout_queue,
            "state": state_name,
        }
        PAYOUT_DISPATCH_BACKLOG_COUNT.labels(**labels).set(count)
        PAYOUT_DISPATCH_BACKLOG_OLDEST_AGE_SECONDS.labels(**labels).set(
            _age_seconds(now, oldest_updated_at)
        )

    for state, threshold_seconds, stuck_rows in stuck_rows_by_state:
        for consumer, asset, network, count, oldest_updated_at in stuck_rows:
            state_name = _state_name(state)
            threshold_label = str(threshold_seconds)
            labels = {
                "consumer": consumer,
                "asset": asset,
                "network": network,
                "state": state_name,
                "threshold_seconds": threshold_label,
            }
            PAYOUT_STUCK_EXECUTION_COUNT.labels(**labels).set(count)
            PAYOUT_STUCK_EXECUTION_OLDEST_AGE_SECONDS.labels(**labels).set(
                _age_seconds(now, oldest_updated_at)
            )

    for consumer, asset, network, count, oldest_broadcasted_at in confirmation_sla_rows:
        threshold_label = str(CONFIRMATION_SLA_SECONDS)
        labels = {
            "consumer": consumer,
            "asset": asset,
            "network": network,
            "threshold_seconds": threshold_label,
        }
        PAYOUT_CONFIRMATION_SLA_BREACH_COUNT.labels(**labels).set(count)
        PAYOUT_CONFIRMATION_SLA_BREACH_OLDEST_AGE_SECONDS.labels(**labels).set(
            _age_seconds(now, oldest_broadcasted_at)
        )

    for consumer, asset, network, error_code, count in ordering_conflict_rows:
        PAYOUT_ORDERING_CONFLICT_COUNT.labels(
            consumer=consumer,
            asset=asset,
            network=network,
            error_code=_metric_error_code(error_code),
        ).set(count)

    for rail in rail_rows:
        rail_labels = {
            "consumer": rail.consumer,
            "asset": rail.asset,
            "network": rail.network,
            "payout_queue": rail.payout_queue,
        }
        PAYOUT_RAIL_ENABLED.labels(**rail_labels).set(
            1 if rail.execution_enabled else 0
        )
