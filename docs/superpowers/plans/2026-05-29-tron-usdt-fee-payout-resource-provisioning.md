# TRON USDT Fee Payout Resource Provisioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add ProfeeX-backed energy and bandwidth provisioning for USDT TRC-20 single payouts from the TRON `fee_deposit` wallet, while keeping the existing SHKeeper payout endpoint, Basic Auth behavior, admin browser flow, non-USDT payouts, and multipayout resource behavior unchanged.

**Architecture:** Keep SHKeeper as the payout API and record owner. Add a narrow TRON sidecar resource preflight and readiness helper for USDT single payouts only. The main app repeats destination-aware quote/preflight before enqueue, reserves `external_id` defensively when present, and then lets the sidecar queue execute sequentially. The sidecar rechecks on-chain resources immediately before broadcast and creates ProfeeX orders only for the current payout resource deficit.

**Tech Stack:** Flask, Flask-SQLAlchemy, Alembic, Celery, Redis, Jinja, TRON sidecar Flask app, tronpy, ProfeeX HTTP API, Python `unittest`.

---

## Current Code Review

- [ ] Confirm no implementation starts before this plan is reviewed. This plan intentionally does not change product code.

Observed SHKeeper main app behavior:

- `shkeeper/api_v1.py` exposes `POST /api/v1/<crypto_name>/payout` with `@basic_auth_optional`, `@login_required`, and `@handle_request_error`.
- `shkeeper/auth.py` Basic Auth sets `g.user` from username/password and does not require TOTP. Browser login still requires 2FA when enabled.
- `shkeeper/services/payout_service.py` checks duplicate `external_id` before calling `crypto.mkpayout`, but the check is not race-safe and the `Payout` record is currently created after the sidecar enqueue.
- `shkeeper/models.py` has `Payout.external_id` but no unique DB constraint.
- `shkeeper/api_v1.py` already has `GET /api/v1/<crypto_name>/payout/status?external_id=<value>`, protected by API key, but it returns a narrow payload.
- `shkeeper/modules/classes/tron_token.py` ignores the `address` kwarg in `estimate_tx_fee()` and sends TRON single payouts through `POST /<symbol>/payout/<destination>/<amount>`.
- `shkeeper/templates/wallet/payout_tron.j2` estimates only by amount and compares static `fee` with TRX fee-deposit balance.

Observed TRON sidecar behavior:

- `../tron-shkeeper/app/api/payout.py` has static `POST /calc-tx-fee/<amount>` returning `{"fee": config.TX_FEE}`.
- `../tron-shkeeper/app/api/payout.py` enqueues single payout as `prepare_payout.s(to, amount, g.symbol) | payout_task.s(g.symbol)`.
- `../tron-shkeeper/app/tasks.py` executes `Wallet.transfer()` without any resource-provider check for normal single payouts.
- Sweep code in `transfer_trc20_from()` already estimates energy, checks bandwidth, calls resource providers, waits for ProfeeX `ACTIVE`, and rechecks resources before transfer.
- `../tron-shkeeper/app/resource_providers/profeex.py` already creates ProfeeX energy and bandwidth orders, polls order status, and validates resources on chain after `ACTIVE`.
- Existing ProfeeX provider methods use fixed order amounts for sweep. The payout path needs per-payout deficit order sizing without changing sweep semantics.

Observed Grither Pay behavior:

- `/Users/test/IdeaProjects/grither-pay/apps/backend/src/main/java/com/grither/pay/wallet/application/WalletAltynWithdrawalCreationService.java` already generates `externalId = "WW-" + publicNumber` for USDT withdrawals.
- Grither Pay owns ledger holds, idempotency keys, active-withdrawal partial unique indexes, and user retry policy.
- In this SHKeeper phase, Grither Pay should call the existing SHKeeper payout endpoint with Basic Auth and `external_id`. SHKeeper must add defensive execution guards, not a second wallet ledger.

Important constraint:

- SHKeeper cannot reliably distinguish "Grither Pay over Basic Auth" from "manual API caller over Basic Auth" without adding a new endpoint, header, HMAC, or scoped credential. The user explicitly removed HMAC and endpoint/auth changes from this scope. Therefore SHKeeper must not globally reject payout requests missing `external_id`, because that would change existing admin/API behavior. The safe minimal design is:
  - Grither Pay must always send `external_id`.
  - SHKeeper enforces race-safe duplicate protection whenever `external_id` is present.
  - Admin payouts without `external_id` remain legacy-compatible.

## Architecture Decisions

- [ ] Keep `POST /api/v1/<crypto_name>/payout` path and decorators unchanged.
- [ ] Keep Basic Auth and admin session auth unchanged.
- [ ] Do not add HMAC in this phase.
- [ ] Do not add an application IP allowlist in this phase.
- [ ] Do not add TRON USDT resource provisioning to multipayout.
- [ ] Do not change TON, ETH, BTC-like, EVM, Lightning, Monero, TRX, or TRON USDC payout behavior.
- [ ] Add a DB unique constraint for `(crypto, external_id)` so duplicate external IDs cannot race through concurrent requests.
- [ ] Preserve multiple rows with `external_id IS NULL`; admin payouts and autopayouts without external IDs must keep working.
- [ ] For requests with `external_id`, create the SHKeeper `Payout` row before calling the sidecar, then update `task_id` when the sidecar returns it.
- [ ] On ambiguous sidecar enqueue errors after the request may have reached the sidecar, do not mark the payout as failed automatically. Keep it `IN_PROGRESS`, keep `task_id = NULL`, store the error text, and require manual reconciliation/status handling. This avoids telling Grither Pay to retry while a sidecar task may still broadcast.
- [ ] For clear pre-enqueue validation failures, mark the reserved payout as `FAIL`.
- [ ] Use one ProfeeX order per deficient resource for the current payout only. If resources are already sufficient, create no ProfeeX order.
- [ ] Use a dedicated Celery queue for USDT single payouts and deploy exactly one worker slot for that queue.
- [ ] Recompute resources at execution time, not only at admin estimate time.

## Main App Tasks

### 1. Add Race-Safe Duplicate Guard

- [ ] Add migration `migrations/versions/20260529_payout_external_id_unique.py`.

Use `down_revision = "001_aml_deposit_checks"`.

Migration content:

```python
"""Add unique payout external_id guard

Revision ID: 20260529_payout_external_id_unique
Revises: 001_aml_deposit_checks
Create Date: 2026-05-29 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "20260529_payout_external_id_unique"
down_revision = "001_aml_deposit_checks"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE payout SET external_id = NULL WHERE external_id = ''"))
    duplicates = bind.execute(
        sa.text(
            """
            SELECT crypto, external_id, COUNT(*) AS cnt
            FROM payout
            WHERE external_id IS NOT NULL
            GROUP BY crypto, external_id
            HAVING COUNT(*) > 1
            """
        )
    ).fetchall()
    if duplicates:
        formatted = ", ".join(
            f"{row.crypto}:{row.external_id}({row.cnt})" for row in duplicates
        )
        raise RuntimeError(
            "Cannot add uq_payout_crypto_external_id; duplicate payout "
            f"external_id values exist: {formatted}"
        )
    op.create_index(
        "uq_payout_crypto_external_id",
        "payout",
        ["crypto", "external_id"],
        unique=True,
    )


def downgrade():
    op.drop_index(
        "uq_payout_crypto_external_id",
        "payout",
    )
```

- [ ] Update `shkeeper/models.py` `Payout` with matching metadata:

```python
    __table_args__ = (
        db.Index("uq_payout_crypto_external_id", "crypto", "external_id", unique=True),
    )
```

- [ ] Keep `Payout.add()` compatible with `external_id=None` and autopayouts.

Acceptance:

- Multiple payouts with `external_id = NULL` can still be inserted.
- A second payout with the same non-empty `(crypto, external_id)` fails at DB level.
- Existing admin payouts without `external_id` are unaffected.

### 2. Make PayoutService Reserve External IDs Before Sidecar Enqueue

- [ ] Update `shkeeper/services/payout_service.py`.

Add small typed request errors near the top of the file:

```python
class PayoutRequestError(ValueError):
    status_code = 400
    code = "PAYOUT_REQUEST_ERROR"


class PayoutConflictError(PayoutRequestError):
    status_code = 409
    code = "PAYOUT_EXTERNAL_ID_CONFLICT"
```

- [ ] Add helpers to normalize external IDs and validate fees without changing legacy behavior:

```python
    @staticmethod
    def normalize_external_id(value):
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    @staticmethod
    def get_request_fee(crypto, req):
        if "fee" in req:
            return req["fee"]
        can_omit = getattr(crypto, "can_omit_fee_for_payout", False)
        if callable(can_omit):
            can_omit = can_omit()
        if can_omit:
            return "0"
        raise PayoutRequestError("fee is required")
```

- [ ] Change `check_external_id_unique()` to use normalized value and raise `PayoutConflictError`.

```python
    @staticmethod
    def check_external_id_unique(req, crypto_name):
        external_id = PayoutService.normalize_external_id(req.get("external_id"))
        if external_id:
            existing = Payout.query.filter_by(
                crypto=crypto_name,
                external_id=external_id,
            ).first()
            if existing:
                raise PayoutConflictError(
                    f"Payout with this external_id already exists: {external_id}"
                )
        return external_id
```

- [ ] Add a preflight hook that is a no-op for cryptos without the hook:

```python
    @staticmethod
    def preflight_payout(crypto, req):
        preflight = getattr(crypto, "preflight_payout", None)
        if callable(preflight):
            try:
                preflight(
                    destination=req["destination"],
                    amount=Decimal(req["amount"]),
                )
            except ValueError as exc:
                raise PayoutRequestError(str(exc)) from exc
```

- [ ] Add helpers to mark clear failures and ambiguous enqueue states:

```python
    @staticmethod
    def mark_payout_failed(payout, message):
        from shkeeper.models import PayoutStatus

        payout.status = PayoutStatus.FAIL
        payout.success = "No"
        payout.error = str(message)
        db.session.commit()

    @staticmethod
    def mark_payout_enqueue_unknown(payout, message):
        payout.error = f"Sidecar enqueue result is unknown: {message}"
        db.session.commit()
```

- [ ] Add a reserved path for requests with normalized `external_id`.

Flow:

1. Validate `destination`, `amount`, `callback_url`, and fee/default fee before DB insert.
2. Run `preflight_payout()`.
3. Create `Payout` row with `task_id=None` and normalized `external_id`.
4. Call `crypto.mkpayout(req["destination"], Decimal(req["amount"]), fee)`.
5. If response has `task_id`, save it on the reserved row.
6. If response clearly has no `task_id`, mark row failed and raise a 400 error.
7. If `crypto.mkpayout()` raises a network/request exception after the request may have left the process, save unknown enqueue text and re-raise.
8. Let the unique constraint turn races into `PayoutConflictError`.

Implementation shape:

```python
    @classmethod
    def single_payout(cls, crypto_name, req):
        crypto = cls.get_crypto(crypto_name)
        external_id = cls.check_external_id_unique(req, crypto_name)
        cls.validate_callback_url(req.get("callback_url"))
        cls.preflight_payout(crypto, req)
        fee = cls.get_request_fee(crypto, req)

        if external_id:
            return cls._single_payout_with_reserved_external_id(
                crypto_name,
                crypto,
                req,
                fee,
                external_id,
            )

        res = crypto.mkpayout(
            req["destination"],
            Decimal(req["amount"]),
            fee,
        )
        task_id = res.get("task_id")
        cls.create_payout_record(
            req,
            crypto_name,
            task_id=task_id,
            txids=res.get("result", []),
        )
        return res
```

Reserved helper:

```python
    @classmethod
    def _single_payout_with_reserved_external_id(
        cls,
        crypto_name,
        crypto,
        req,
        fee,
        external_id,
    ):
        req = dict(req)
        req["external_id"] = external_id
        try:
            payout = cls.create_payout_record(req, crypto_name, task_id=None)
        except IntegrityError as exc:
            db.session.rollback()
            raise PayoutConflictError(
                f"Payout with this external_id already exists: {external_id}"
            ) from exc

        try:
            res = crypto.mkpayout(
                req["destination"],
                Decimal(req["amount"]),
                fee,
            )
        except Exception as exc:
            cls.mark_payout_enqueue_unknown(payout, exc)
            raise

        task_id = res.get("task_id") if isinstance(res, dict) else None
        if not task_id:
            cls.mark_payout_failed(payout, res)
            raise PayoutRequestError(f"Payout sidecar did not return task_id: {res}")

        payout.task_id = task_id
        db.session.commit()
        res["external_id"] = external_id
        return res
```

- [ ] Import `IntegrityError` in `payout_service.py`.

```python
from sqlalchemy.exc import IntegrityError
```

- [ ] Adjust `PayoutService.create_payout_record()` to write normalized external IDs:

```python
external_id=PayoutService.normalize_external_id(req.get("external_id"))
```

- [ ] Move multipayout duplicate checks before `crypto.multipayout()` without adding resource provisioning to multipayout.

Reason: the new DB unique constraint must not create a new post-enqueue failure path for multipayout external IDs.

Acceptance:

- Admin/manual payout without `external_id` keeps the old sidecar-first flow.
- Grither-style payout with `external_id` creates a DB reservation before sidecar enqueue.
- Two concurrent same `(crypto, external_id)` requests create at most one sidecar enqueue.
- Multipayout has no resource provisioning change.

### 3. Return Proper HTTP Status for Payout Request Errors

- [ ] Update `shkeeper/api_v1.py` `handle_request_error()` to respect `PayoutRequestError.status_code`.

```python
def handle_request_error(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except PayoutRequestError as e:
            app.logger.exception("Payout request rejected")
            return {
                "status": "error",
                "code": e.code,
                "message": str(e),
            }, e.status_code
        except Exception as e:
            app.logger.exception("Payout error")
            return {"status": "error", "message": str(e)}, 500

    return wrapper
```

- [ ] Import `PayoutRequestError` from `shkeeper.services.payout_service`.

Acceptance:

- Duplicate `external_id` returns HTTP 409.
- Bad resource preflight returns HTTP 400 or 503 depending on the typed error chosen in implementation.
- Unexpected exceptions still return HTTP 500 as before.

### 4. Extend Payout Status Response Without Breaking Existing Clients

- [ ] Update `shkeeper/api_v1.py` `payout_status()`.

Add fields:

```python
"task_id": payout.task_id,
"success": payout.success,
"error": payout.error,
"txids": [tx.txid for tx in payout.transactions],
```

Keep existing fields unchanged.

Acceptance:

- Existing clients reading `id`, `external_id`, `crypto`, `status`, `amount`, `destination`, and `txid` still work.
- Grither Pay can detect `IN_PROGRESS` with `task_id = null` and non-empty `error` as an ambiguous/manual-review state.

### 5. Add TRON USDT Quote and Backend Preflight Hook

- [ ] Update `shkeeper/modules/classes/tron_token.py`.

Pass `address` into sidecar estimate:

```python
    def estimate_tx_fee(self, amount, **kwargs):
        params = {}
        if kwargs.get("address"):
            params["address"] = kwargs["address"]
        response = requests.post(
            f"http://{self.gethost()}/{self.crypto}/calc-tx-fee/{amount}",
            auth=self.get_auth_creds(),
            params=params,
        ).json(parse_float=Decimal)
        return response
```

- [ ] Add USDT-only fee omission:

```python
    def can_omit_fee_for_payout(self):
        return self.crypto == "USDT"
```

- [ ] Add USDT-only backend preflight:

```python
    def preflight_payout(self, destination, amount):
        if self.crypto != "USDT":
            return
        quote = self.estimate_tx_fee(amount, address=destination)
        resource_quote = quote.get("resource_quote")
        if not resource_quote:
            return
        if not resource_quote.get("submit_ready"):
            reason = resource_quote.get("blocking_reason") or "TRON USDT payout resources are not ready"
            raise ValueError(reason)
```

Acceptance:

- Feature-disabled sidecar returns legacy static fee and does not block.
- Feature-enabled sidecar returns `resource_quote`; API payout blocks when `submit_ready` is false.
- The worker still recomputes readiness before broadcast.

## TRON Sidecar Tasks

### 6. Add Payout Resource Configuration

- [ ] Update `../tron-shkeeper/app/config.py`.

Add settings:

```python
    TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED: bool = False
    TRON_USDT_PAYOUT_QUEUE: str = "tron_usdt_fee_payouts"
    PAYOUT_RESOURCE_POST_ACTIVE_RECHECK_ATTEMPTS: int = 3
    PAYOUT_RESOURCE_POST_ACTIVE_RECHECK_SLEEP_SEC: float = 1.0
```

Acceptance:

- Default deployment keeps old behavior.
- Enabling requires explicit env configuration.

### 7. Add ProfeeX Precount and Payout-Sized Order Methods

- [ ] Update `../tron-shkeeper/app/profeex.py` only if new min/max constants are needed from code. Existing constants are already:

```python
PROFEEX_MIN_ENERGY_ORDER_AMOUNT = 64_285
PROFEEX_MAX_ENERGY_ORDER_AMOUNT = 3_000_000
PROFEEX_MIN_BANDWIDTH_ORDER_AMOUNT = 350
PROFEEX_MAX_BANDWIDTH_ORDER_AMOUNT = 10_000
```

- [ ] Update `../tron-shkeeper/app/resource_providers/profeex.py`.

Add failure classification constants:

```python
TEMPORARY_ERROR_CODES = {
    "DUPLICATE_REQUEST",
    "RATE_LIMIT_EXCEEDED",
    "SERVICE_UNAVAILABLE",
    "REQUEST_TIMEOUT",
}
OPERATIONAL_ERROR_CODES = {
    "INSUFFICIENT_BALANCE",
    "PROCESSING_FAILED",
    "CONFIGURATION_ERROR",
    "UNKNOWN_ERROR",
}
VALIDATION_ERROR_CODES = {
    "INVALID_ADDRESS",
    "INVALID_PARAMETERS",
}
```

Add a small exception:

```python
from dataclasses import dataclass


@dataclass
class ProfeeXWaitResult:
    active_order: dict | None
    failure_order: dict | None


class ProfeeXOrderError(RuntimeError):
    def __init__(self, resource_name, message, error_code=None, temporary=False):
        super().__init__(message)
        self.resource_name = resource_name
        self.error_code = error_code
        self.temporary = temporary
```

Add pricing methods that use ProfeeX docs endpoints:

```python
    def precount_energy(self, volume: int) -> dict | None:
        settings = config.PROFEEX
        if settings is None:
            return None
        return self._precount(
            settings,
            "/delegation/precount/energy",
            volume,
            settings.energy_duration_label,
        )

    def precount_bandwidth(self, volume: int) -> dict | None:
        settings = config.PROFEEX
        if settings is None:
            return None
        return self._precount(
            settings,
            "/delegation/precount/bandwidth",
            volume,
            settings.bandwidth_duration_label,
        )
```

Precount helper:

```python
    def _precount(self, settings, path: str, volume: int, duration_label: str) -> dict | None:
        try:
            response = requests.get(
                self._url(settings, path),
                params={
                    "volume": volume,
                    "days": duration_label,
                    "currency": settings.currency,
                },
                headers=self._headers(settings),
                timeout=self.REQUEST_TIMEOUT_SEC,
            )
        except requests.RequestException:
            logger.exception("ProfeeX precount request failed")
            return None
        if response.status_code != 200:
            logger.warning(
                f"ProfeeX precount rejected with status {response.status_code}: {response.text}"
            )
            return None
        try:
            data = response.json()
        except ValueError:
            logger.exception("ProfeeX precount response is not valid JSON")
            return None
        return data if isinstance(data, dict) else None
```

- [ ] Add new public acquisition methods without changing existing sweep methods:

```python
    def acquire_energy_order(
        self,
        receiver: str,
        order_amount: int,
        *,
        minimum_energy_required: int,
    ) -> bool:
        settings = config.PROFEEX
        if settings is None:
            raise ProfeeXOrderError("energy", "PROFEEX config is missing")
        order = self._create_order(
            settings,
            receiver,
            order_amount,
            resource_name="energy",
            path="/delegation/buyenergy",
            duration_label=settings.energy_duration_label,
        )
        if order is None:
            raise ProfeeXOrderError("energy", "ProfeeX energy order was not accepted")
        task_id = self._extract_task_id(order, "energy")
        if task_id is None:
            raise ProfeeXOrderError("energy", f"ProfeeX energy order has no task_id: {order}")
        wait_result = self._wait_for_order(settings, task_id, order, "energy")
        if wait_result.active_order is None:
            raise self._order_error_from_order(
                "energy",
                wait_result.failure_order or order,
            )
        tron_client = self.tron_client or ConnectionManager.client()
        energy_available = self._get_available_energy(tron_client, receiver, "payout-post-delegation")
        if energy_available is None or energy_available < minimum_energy_required:
            raise ProfeeXOrderError(
                "energy",
                "ProfeeX energy order is ACTIVE but on-chain energy is still insufficient",
            )
        return True

    def acquire_bandwidth_order(
        self,
        receiver: str,
        order_amount: int,
        *,
        bandwidth_required: int,
    ) -> bool:
        settings = config.PROFEEX
        if settings is None:
            raise ProfeeXOrderError("bandwidth", "PROFEEX config is missing")
        order = self._create_order(
            settings,
            receiver,
            order_amount,
            resource_name="bandwidth",
            path="/delegation/buybandwidth",
            duration_label=settings.bandwidth_duration_label,
        )
        if order is None:
            raise ProfeeXOrderError("bandwidth", "ProfeeX bandwidth order was not accepted")
        task_id = self._extract_task_id(order, "bandwidth")
        if task_id is None:
            raise ProfeeXOrderError("bandwidth", f"ProfeeX bandwidth order has no task_id: {order}")
        wait_result = self._wait_for_order(settings, task_id, order, "bandwidth")
        if wait_result.active_order is None:
            raise self._order_error_from_order(
                "bandwidth",
                wait_result.failure_order or order,
            )
        tron_client = self.tron_client or ConnectionManager.client()
        if not has_free_bw(receiver, bandwidth_required, tron_client=tron_client):
            raise ProfeeXOrderError(
                "bandwidth",
                "ProfeeX bandwidth order is ACTIVE but on-chain bandwidth is still insufficient",
            )
        return True
```

Add classifier:

```python
    def _order_error_from_order(self, resource_name: str, order: dict) -> ProfeeXOrderError:
        error_code = order.get("error_code")
        details = order.get("details") or {}
        message = details.get("error_message") or f"ProfeeX {resource_name} order failed: {order}"
        temporary = error_code in TEMPORARY_ERROR_CODES
        return ProfeeXOrderError(resource_name, message, error_code, temporary)
```

Add a new wait helper and keep the existing public behavior:

```python
    def _wait_for_order(
        self,
        settings,
        task_id: str,
        initial_order: dict,
        resource_name: str,
    ) -> ProfeeXWaitResult:
        # Move the existing _wait_until_active loop here.
        # Return ProfeeXWaitResult(active_order=order, failure_order=None)
        # on ACTIVE.
        # Return ProfeeXWaitResult(active_order=None, failure_order=order)
        # on FAILED, CANCELLED, COMPLETED, unknown, unexpected status, timeout,
        # invalid JSON, or invalid object response.

    def _wait_until_active(
        self,
        settings,
        task_id: str,
        initial_order: dict,
        resource_name: str,
    ) -> dict | None:
        return self._wait_for_order(
            settings,
            task_id,
            initial_order,
            resource_name,
        ).active_order
```

Existing tests must keep passing because `_wait_until_active()` still returns `dict | None`.

Acceptance:

- Existing `acquire_energy()` and `acquire_bandwidth()` behavior for sweep remains compatible.
- New payout methods can order exact payout-sized volume.
- Precount uses `GET /delegation/precount/energy` and `GET /delegation/precount/bandwidth`.
- Logs include ProfeeX `task_id`, status, and `error_code`, never API keys.

### 8. Add Sidecar Payout Resource Helper

- [ ] Add `../tron-shkeeper/app/payout_resources.py`.

Imports:

```python
from dataclasses import dataclass
from decimal import Decimal
import time

import tronpy.keys
from tronpy.abi import trx_abi

from app.connection_manager import ConnectionManager
from app.config import config
from app.profeex import (
    PROFEEX_MAX_BANDWIDTH_ORDER_AMOUNT,
    PROFEEX_MAX_ENERGY_ORDER_AMOUNT,
    PROFEEX_MIN_BANDWIDTH_ORDER_AMOUNT,
    PROFEEX_MIN_ENERGY_ORDER_AMOUNT,
)
from app.resource_providers.profeex import ProfeeXProvider
from app.schemas import KeyType
from app.utils import get_available_energy, get_key, has_free_bw
```

Core types:

```python
@dataclass
class ResourceQuote:
    required: int
    available: int
    deficit: int
    order_volume: int
    cost: str | None
    currency: str | None


@dataclass
class PayoutResourceQuote:
    provider: str
    source_address: str
    destination: str
    amount: str
    energy: ResourceQuote
    bandwidth: ResourceQuote
    submit_ready: bool
    blocking_reason: str | None


class PayoutResourceError(RuntimeError):
    pass
```

Helper functions:

```python
def get_available_bandwidth(account_resource: dict) -> int:
    staked = max(account_resource.get("NetLimit", 0) - account_resource.get("NetUsed", 0), 0)
    daily = max(account_resource.get("freeNetLimit", 0) - account_resource.get("freeNetUsed", 0), 0)
    return max(staked, daily)


def clamp_order_volume(value: int, minimum: int, maximum: int) -> int:
    if value <= 0:
        return 0
    if value < minimum:
        return minimum
    if value > maximum:
        raise PayoutResourceError(
            f"Required resource volume {value} exceeds ProfeeX maximum {maximum}"
        )
    return value
```

Energy estimate:

```python
def estimate_usdt_transfer_energy(tron_client, source: str, destination: str, amount: Decimal) -> int:
    contract_address = config.get_contract_address("USDT")
    precision = config.get_decimal("USDT")
    parameter = trx_abi.encode_single(
        "(address,uint256)",
        (destination, int(amount * (10 ** precision))),
    ).hex()
    return tron_client.get_estimated_energy(
        source,
        contract_address,
        "transfer(address,uint256)",
        parameter,
    )
```

Quote function:

```python
def estimate_fee_deposit_resources_for_usdt_payout(
    destination: str,
    amount: Decimal,
    *,
    tron_client=None,
) -> PayoutResourceQuote:
    tronpy.keys.to_base58check_address(destination)
    client = tron_client or ConnectionManager.client()
    _, fee_deposit_address = get_key(KeyType.fee_deposit)
    account_resource = client.get_account_resource(fee_deposit_address)
    energy_required = estimate_usdt_transfer_energy(
        client,
        fee_deposit_address,
        destination,
        amount,
    )
    energy_available = get_available_energy(account_resource)
    energy_deficit = max(energy_required - energy_available, 0)
    bandwidth_required = config.BANDWIDTH_PER_TRC20_TRANSFER_CALL
    bandwidth_available = get_available_bandwidth(account_resource)
    bandwidth_deficit = 0 if has_free_bw(
        fee_deposit_address,
        bandwidth_required,
        tron_client=client,
    ) else max(bandwidth_required - bandwidth_available, 0)

    energy_order_volume = clamp_order_volume(
        energy_deficit,
        PROFEEX_MIN_ENERGY_ORDER_AMOUNT,
        PROFEEX_MAX_ENERGY_ORDER_AMOUNT,
    )
    bandwidth_order_volume = clamp_order_volume(
        bandwidth_deficit,
        PROFEEX_MIN_BANDWIDTH_ORDER_AMOUNT,
        PROFEEX_MAX_BANDWIDTH_ORDER_AMOUNT,
    )

    provider = ProfeeXProvider(tron_client=client)
    energy_price = provider.precount_energy(energy_order_volume) if energy_order_volume else None
    bandwidth_price = provider.precount_bandwidth(bandwidth_order_volume) if bandwidth_order_volume else None

    blocking_reason = None
    if config.PROFEEX is None and (energy_order_volume or bandwidth_order_volume):
        blocking_reason = "PROFEEX config is missing"
    elif energy_order_volume and energy_price is None:
        blocking_reason = "Unable to quote ProfeeX energy price"
    elif bandwidth_order_volume and bandwidth_price is None:
        blocking_reason = "Unable to quote ProfeeX bandwidth price"

    return PayoutResourceQuote(
        provider="profeex",
        source_address=fee_deposit_address,
        destination=destination,
        amount=str(amount),
        energy=ResourceQuote(
            required=energy_required,
            available=energy_available,
            deficit=energy_deficit,
            order_volume=energy_order_volume,
            cost=str(energy_price.get("summa")) if energy_price else None,
            currency=str(energy_price.get("currency")) if energy_price else None,
        ),
        bandwidth=ResourceQuote(
            required=bandwidth_required,
            available=bandwidth_available,
            deficit=bandwidth_deficit,
            order_volume=bandwidth_order_volume,
            cost=str(bandwidth_price.get("summa")) if bandwidth_price else None,
            currency=str(bandwidth_price.get("currency")) if bandwidth_price else None,
        ),
        submit_ready=blocking_reason is None,
        blocking_reason=blocking_reason,
    )
```

Readiness function:

```python
def ensure_fee_deposit_resources_for_usdt_payout(
    destination: str,
    amount: Decimal,
    *,
    tron_client=None,
) -> PayoutResourceQuote:
    quote = estimate_fee_deposit_resources_for_usdt_payout(
        destination,
        amount,
        tron_client=tron_client,
    )
    if not quote.submit_ready:
        raise PayoutResourceError(quote.blocking_reason)

    client = tron_client or ConnectionManager.client()
    provider = ProfeeXProvider(tron_client=client)
    if quote.energy.order_volume:
        provider.acquire_energy_order(
            quote.source_address,
            quote.energy.order_volume,
            minimum_energy_required=quote.energy.required,
        )
    if quote.bandwidth.order_volume:
        provider.acquire_bandwidth_order(
            quote.source_address,
            quote.bandwidth.order_volume,
            bandwidth_required=quote.bandwidth.required,
        )

    for attempt in range(config.PAYOUT_RESOURCE_POST_ACTIVE_RECHECK_ATTEMPTS):
        refreshed = estimate_fee_deposit_resources_for_usdt_payout(
            destination,
            amount,
            tron_client=client,
        )
        if refreshed.energy.deficit == 0 and refreshed.bandwidth.deficit == 0:
            return refreshed
        if attempt + 1 < config.PAYOUT_RESOURCE_POST_ACTIVE_RECHECK_ATTEMPTS:
            time.sleep(config.PAYOUT_RESOURCE_POST_ACTIVE_RECHECK_SLEEP_SEC)

    raise PayoutResourceError(
        "TRON USDT payout resources are still insufficient after ProfeeX provisioning"
    )
```

- [ ] Convert dataclasses to JSON response in a small `to_dict()` method or with `dataclasses.asdict()`.

Acceptance:

- Existing sweep code is not imported into this helper.
- Helper targets `fee_deposit`, not the destination wallet.
- Helper estimates exact transfer `fee_deposit -> destination, amount`.
- Helper creates orders only for current deficits.
- Helper blocks broadcast when quote, order, or post-active recheck fails.

### 9. Extend Sidecar Estimate Endpoint

- [ ] Update `../tron-shkeeper/app/api/payout.py` `calc_tx_fee()`.

Behavior:

```python
@api.post("/calc-tx-fee/<decimal:amount>")
def calc_tx_fee(amount):
    destination = request.args.get("address")
    if (
        config.TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED
        and g.symbol == "USDT"
        and destination
    ):
        quote = estimate_fee_deposit_resources_for_usdt_payout(destination, amount)
        return {
            "fee": "0",
            "resource_quote": dataclasses.asdict(quote),
        }
    if (
        config.TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED
        and g.symbol == "USDT"
        and not destination
    ):
        return {
            "fee": "0",
            "resource_quote": {
                "provider": "profeex",
                "submit_ready": False,
                "blocking_reason": "Destination address is required for TRON USDT resource quote",
            },
        }
    return {"fee": config.TX_FEE}
```

Acceptance:

- Existing callers without the feature flag still receive `{"fee": config.TX_FEE}`.
- Feature-enabled USDT estimates require destination and include provider cost.

### 10. Route USDT Single Payouts to Dedicated Queue

- [ ] Update `../tron-shkeeper/app/api/payout.py` single `payout()` route.

Validate destination and amount before enqueue:

```python
tronpy.keys.to_base58check_address(to)
if amount <= 0:
    raise Exception("Payout amount should be a positive number")
```

Build queue-aware chain:

```python
prepare_sig = prepare_payout.s(to, amount, g.symbol)
execute_sig = payout_task.s(g.symbol)
if config.TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED and g.symbol == "USDT":
    prepare_sig = prepare_sig.set(queue=config.TRON_USDT_PAYOUT_QUEUE)
    execute_sig = execute_sig.set(queue=config.TRON_USDT_PAYOUT_QUEUE)
task = (prepare_sig | execute_sig).apply_async()
return {"task_id": task.id}
```

Acceptance:

- Multipayout route remains unchanged.
- Single USDT payout tasks can be consumed by `celery -A celery_worker.celery worker -Q tron_usdt_fee_payouts --concurrency=1`.
- Non-USDT single payouts keep default queue behavior.

### 11. Run Resource Helper Before Broadcast

- [ ] Update `../tron-shkeeper/app/tasks.py`.

In `prepare_payout()`, add a marker only for feature-enabled USDT single payout:

```python
        {
            "dst": dest,
            "amount": decimal.Decimal(amount),
            "ensure_usdt_payout_resources": (
                config.TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED
                and symbol == "USDT"
            ),
        }
```

In `payout()`, preserve current ThreadPoolExecutor path when no step needs resource provisioning. Use sequential execution when any step has the marker:

```python
@celery.task()
def payout(steps, symbol):
    wallet = Wallet(symbol)
    if any(step.get("ensure_usdt_payout_resources") for step in steps):
        payout_results = []
        for step in steps:
            if step.get("ensure_usdt_payout_resources"):
                ensure_fee_deposit_resources_for_usdt_payout(
                    step["dst"],
                    step["amount"],
                    tron_client=wallet.client,
                )
            payout_results.append(wallet.transfer(step["dst"], step["amount"]))
    else:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=config.CONCURRENT_MAX_WORKERS
        ) as executor:
            payout_results = list(
                executor.map(lambda x: wallet.transfer(x["dst"], x["amount"]), steps)
            )
    post_payout_results.delay(payout_results, symbol)
    return payout_results
```

- [ ] Import `ensure_fee_deposit_resources_for_usdt_payout`.

Acceptance:

- Single USDT payout recomputes resources immediately before `Wallet.transfer()`.
- If resource readiness fails, Celery task fails before broadcast.
- Multipayout steps do not have the marker and do not use this helper.
- Non-USDT payout task path remains byte-for-byte equivalent except surrounding branch.

## Admin UI Tasks

### 12. Show Provider Quote and Block Stale Submissions

- [ ] Update `shkeeper/templates/wallet/payout_tron.j2` only.

Keep existing layout. Add rows after `Estimated fee`:

```html
<div><p>Provider:</p></div>
<div><p><span id="resource_provider">-</span></p></div>
<div><p>Provider cost:</p></div>
<div><p><span id="resource_cost">-</span></p></div>
<div><p>Energy:</p></div>
<div><p><span id="resource_energy">-</span></p></div>
<div><p>Bandwidth:</p></div>
<div><p><span id="resource_bandwidth">-</span></p></div>
```

Add state:

```javascript
let latestEstimateLoaded = false;
let latestEstimateKey = null;
let latestResourceQuote = null;
let latestResourceQuoteKey = null;
```

Update `show_est_fee()`:

```javascript
function quoteKey(addr, amount) {
  return `${addr}|${amount}`;
}

function show_est_fee() {
  let amount = document.querySelector(".fee-input").value;
  let addr = document.querySelector("#paddress").value.trim();
  latestEstimateLoaded = false;
  latestEstimateKey = null;
  latestResourceQuote = null;
  latestResourceQuoteKey = null;
  if (!amount || !(addr.startsWith('T') && addr.length == 34)) return;
  fetch("/api/v1/{{crypto.crypto}}/estimate-tx-fee/" + amount + "?address=" + encodeURIComponent(addr))
    .then(response => response.json())
    .then(data => {
      if (data.fee !== undefined) {
        document.querySelector("#est_fee").innerText = data.fee;
      }
      latestEstimateLoaded = true;
      latestEstimateKey = quoteKey(addr, amount);
      if (data.resource_quote) {
        latestResourceQuote = data.resource_quote;
        latestResourceQuoteKey = quoteKey(addr, amount);
        renderResourceQuote(data.resource_quote);
      } else {
        clearResourceQuote();
      }
      check_fee();
    })
}
```

Add rendering functions:

```javascript
function clearResourceQuote() {
  document.querySelector("#resource_provider").innerText = "-";
  document.querySelector("#resource_cost").innerText = "-";
  document.querySelector("#resource_energy").innerText = "-";
  document.querySelector("#resource_bandwidth").innerText = "-";
}

function renderResourceQuote(quote) {
  document.querySelector("#resource_provider").innerText = quote.provider || "-";
  let energyCost = quote.energy && quote.energy.cost ? parseFloat(quote.energy.cost) : 0;
  let bandwidthCost = quote.bandwidth && quote.bandwidth.cost ? parseFloat(quote.bandwidth.cost) : 0;
  let currency = (quote.energy && quote.energy.currency) || (quote.bandwidth && quote.bandwidth.currency) || "";
  let total = energyCost + bandwidthCost;
  document.querySelector("#resource_cost").innerText = total ? `${total.toFixed(6)} ${currency}` : `0 ${currency}`;
  document.querySelector("#resource_energy").innerText = quote.energy
    ? `${quote.energy.available}/${quote.energy.required}, order ${quote.energy.order_volume}`
    : "-";
  document.querySelector("#resource_bandwidth").innerText = quote.bandwidth
    ? `${quote.bandwidth.available}/${quote.bandwidth.required}, order ${quote.bandwidth.order_volume}`
    : "-";
}
```

Update `check_fee()`:

```javascript
function check_fee() {
  let fee_err = document.querySelector("#fee_err");
  let amount = document.querySelector(".fee-input").value;
  let addr = document.querySelector("#paddress").value.trim();
  if (!latestEstimateLoaded || latestEstimateKey !== quoteKey(addr, amount)) {
    fee_err.innerText = "Estimate is missing or stale. Recalculate before sending.";
    fee_err.style.display = "block";
    return false;
  }
  if (latestResourceQuote) {
    if (latestResourceQuoteKey !== quoteKey(addr, amount)) {
      fee_err.innerText = "Resource quote is stale. Recalculate before sending.";
      fee_err.style.display = "block";
      return false;
    }
    if (!latestResourceQuote.submit_ready) {
      fee_err.innerText = latestResourceQuote.blocking_reason || "TRON USDT payout resources are not ready.";
      fee_err.style.display = "block";
      return false;
    }
    fee_err.style.display = "none";
    return true;
  }

  let est_fee = parseFloat(document.querySelector("#est_fee").innerText);
  let fee_depos_bal = parseFloat(document.querySelector("#fee_depos_bal").innerText);
  if (fee_depos_bal < est_fee) {
    fee_err.innerText = not_en_trx_msg;
    fee_err.style.display = "block";
    return false;
  }
  fee_err.style.display = "none";
  return true;
}
```

- [ ] Add destination input listener:

```javascript
document.querySelector("#paddress").addEventListener("input", delay((e) => show_est_fee(), 200));
```

- [ ] Keep the existing submit payload shape with `fee`.

Acceptance:

- Admin sees ProfeeX provider name and cost when sidecar feature returns `resource_quote`.
- Submit is blocked when quote is missing, stale, or `submit_ready=false`.
- Feature-disabled sidecar keeps legacy static `fee` behavior.
- Text stays inside existing form rows on desktop and mobile.

## Documentation and Rollout Tasks

### 13. Update Deployment Notes

- [ ] Update `docs/DEPLOYMENT.md` in `shkeeper.io` only after implementation.

Document:

```text
TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED=true
ENERGY_PROVIDER=profeex
BANDWIDTH_PROVIDER=profeex
PROFEEX='{"api_key":"example-secret","energy_duration_label":"1h","bandwidth_duration_label":"1h","currency":"TRX"}'
TRON_USDT_PAYOUT_QUEUE=tron_usdt_fee_payouts
```

Worker:

```bash
celery -A celery_worker.celery worker -E --loglevel=info -Q tron_usdt_fee_payouts --concurrency=1
```

Rollout order:

1. Deploy code with `TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED=false`.
2. Run DB migration.
3. Start dedicated queue worker.
4. Configure ProfeeX.
5. Enable sidecar feature.
6. Test admin estimate.
7. Test one small USDT TRC-20 single payout.
8. Point Grither Pay to the existing payout endpoint over private Yandex networking when available.

### 14. Grither Pay Contract

- [ ] Do not change Grither Pay in this SHKeeper implementation unless the user explicitly expands scope.
- [ ] Document the required request contract for the future Grither Pay adapter:

```json
{
  "external_id": "WW-123456",
  "destination": "TXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
  "amount": "100.25",
  "callback_url": "https://grither-pay.example/internal/shkeeper/payout-callback"
}
```

- [ ] Document that SHKeeper accepts `fee` but Grither Pay does not need to send it for USDT TRC-20.
- [ ] Document that duplicate `external_id` means the same withdrawal attempt already reached SHKeeper and must be handled by status lookup, not blind retry.

## Tests

### 15. Main App Tests

- [ ] Add `tests/test_payout_service_external_id.py`.

Test cases:

- `test_null_external_id_allows_multiple_payout_rows`
- `test_duplicate_external_id_is_rejected_before_sidecar_call`
- `test_concurrent_duplicate_external_id_hits_unique_constraint`
- `test_external_id_path_creates_payout_before_sidecar_task_id`
- `test_external_id_path_updates_task_id_after_sidecar_success`
- `test_missing_fee_allowed_for_tron_usdt_only`
- `test_missing_fee_still_rejected_for_non_tron_usdt`
- `test_multipayout_validates_external_ids_before_sidecar_call`

- [ ] Add `tests/test_tron_token_payout_preflight.py`.

Test cases:

- `test_estimate_tx_fee_passes_destination_as_address_query_param`
- `test_preflight_allows_legacy_static_fee_response`
- `test_preflight_rejects_structured_quote_when_not_submit_ready`
- `test_preflight_allows_structured_quote_when_submit_ready`

- [ ] Add `tests/test_payout_status_response.py`.

Test case:

- `test_payout_status_includes_task_id_error_success_and_txids`

Run:

```bash
python -m unittest \
  tests.test_payout_service_external_id \
  tests.test_tron_token_payout_preflight \
  tests.test_payout_status_response \
  tests.test_aml_callback_payload \
  tests.test_aml_processing
```

Expected output:

```text
OK
```

### 16. TRON Sidecar Tests

- [ ] Add `../tron-shkeeper/tests/test_payout_resources.py`.

Test cases:

- `test_quote_skips_profeex_prices_when_resources_are_sufficient`
- `test_quote_requests_energy_precount_when_energy_is_deficient`
- `test_quote_requests_bandwidth_precount_when_bandwidth_is_deficient`
- `test_quote_blocks_when_profeex_config_missing_and_resource_deficit_exists`
- `test_quote_blocks_when_precount_fails_and_resource_deficit_exists`
- `test_ensure_orders_energy_then_rechecks_before_return`
- `test_ensure_orders_bandwidth_then_rechecks_before_return`
- `test_ensure_raises_before_broadcast_when_resources_still_deficient`

- [ ] Update `../tron-shkeeper/tests/test_profeex_bandwidth_provider.py`.

Add cases:

- `test_precount_energy_uses_get_query_params_and_api_key`
- `test_precount_bandwidth_uses_get_query_params_and_api_key`
- `test_acquire_energy_order_uses_requested_order_amount`
- `test_acquire_bandwidth_order_uses_requested_order_amount`
- `test_order_error_marks_duplicate_request_temporary`
- `test_order_error_marks_rate_limit_temporary`

- [ ] Add `../tron-shkeeper/tests/test_payout_task_resource_provisioning.py`.

Test cases:

- `test_prepare_payout_marks_usdt_single_when_feature_enabled`
- `test_prepare_multipayout_does_not_mark_resource_provisioning`
- `test_payout_calls_resource_helper_before_wallet_transfer`
- `test_payout_does_not_call_resource_helper_for_non_usdt`
- `test_api_routes_usdt_single_chain_to_dedicated_queue_when_enabled`

Run:

```bash
cd /Users/test/PycharmProjects/tron-shkeeper
python -m unittest \
  tests.test_payout_resources \
  tests.test_profeex_bandwidth_provider \
  tests.test_resource_provider_config \
  tests.test_resource_provider_factory \
  tests.test_payout_task_resource_provisioning
```

Expected output:

```text
OK
```

### 17. Manual Smoke Tests

- [ ] With feature disabled, confirm existing admin USDT payout form still shows static fee.
- [ ] With feature enabled and ProfeeX configured, enter destination and amount in admin payout form.
- [ ] Confirm provider rows render `profeex`, energy order volume, bandwidth order volume, and total cost.
- [ ] Confirm Send is blocked if quote endpoint returns `submit_ready=false`.
- [ ] Confirm API payout with duplicate `external_id` returns HTTP 409 and does not call sidecar a second time.
- [ ] Confirm one small USDT payout reaches Celery task, provisions resources when deficient, and broadcasts only after resource recheck.
- [ ] Confirm two rapid USDT payouts enter `tron_usdt_fee_payouts` queue and execute sequentially.

## Review Checklist

- [ ] Auth decorators on `POST /api/v1/<crypto_name>/payout` are unchanged.
- [ ] Admin browser payout still uses the existing session path.
- [ ] Basic Auth payout still works.
- [ ] No HMAC code is added.
- [ ] No multipayout resource provisioning is added.
- [ ] No resource provisioning is added for TON, ETH, BTC-like, EVM, Lightning, Monero, TRX, or TRON USDC.
- [ ] New sidecar helper is isolated in `app/payout_resources.py`.
- [ ] Existing sweep behavior keeps using existing provider methods.
- [ ] ProfeeX existing fixed-order sweep tests still pass.
- [ ] Payout task cannot call `Wallet.transfer()` after a resource helper failure.
- [ ] Ambiguous sidecar enqueue failures do not produce an automatic duplicate retry path.
- [ ] The unique `(crypto, external_id)` guard has a migration precheck for existing duplicate data.
- [ ] All new behavior is disabled by default through sidecar config.

## Recommended Execution Order

1. Implement main app duplicate guard and tests first. This is independent and protects Grither Pay retries.
2. Implement ProfeeX precount and payout-sized order methods in the sidecar.
3. Implement `app/payout_resources.py` with unit tests.
4. Integrate the sidecar estimate endpoint and payout task marker.
5. Add main app TRON estimate/preflight pass-through.
6. Update admin UI.
7. Update docs and run smoke tests.

This order keeps each step small, reduces fork merge conflict risk, and makes the most dangerous behavior, duplicate payout creation, testable before touching broadcast logic.
