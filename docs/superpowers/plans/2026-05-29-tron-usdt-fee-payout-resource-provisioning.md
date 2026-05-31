# TRON USDT Fee Payout Resource Provisioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add configured energy and bandwidth provider provisioning for USDT TRC-20 single payouts from the TRON `fee_deposit` wallet, while keeping the existing SHKeeper payout endpoint, Basic Auth behavior, admin browser flow, non-USDT payouts, and multipayout resource behavior unchanged.

**Architecture:** Keep SHKeeper as a generic payout API and technical payout record owner, not as an external consumer wallet/ledger service. Add a narrow TRON sidecar resource preflight and readiness helper for USDT single payouts only. The main app repeats destination-aware backend preflight before enqueue, reserves `external_id` defensively when present, and then lets the sidecar queue execute sequentially. The sidecar estimates USDT transfer energy through ProfeeX `/delegation/fee`, rechecks on-chain resources immediately before broadcast, and uses the configured `EnergyProvider`/`BandwidthProvider` only for the current payout resource deficit.

**Important safety rule:** This phase should prevent known TRX burn paths. If
ProfeeX reports the payout destination as a new/unactivated TRON address, block
the payout before enqueue with a controlled `DESTINATION_NOT_ACTIVATED` error.

**Tech Stack:** Flask, Flask-SQLAlchemy, Alembic, Celery, Redis, Jinja, TRON sidecar Flask app, tronpy, shared TRON resource provider interfaces, ProfeeX/re:Fee provider implementations, Python `unittest`.

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
- Sweep code in `transfer_trc20_from()` already estimates energy, checks bandwidth, calls the configured resource providers, waits for provider success, and rechecks resources before transfer.
- `../tron-shkeeper/app/resource_providers/base.py` defines shared `EnergyProvider` and `BandwidthProvider` protocols implemented by re:Fee, ProfeeX, and staking energy.
- `../tron-shkeeper/app/resource_providers/factory.py` selects providers from `ENERGY_PROVIDER` and `BANDWIDTH_PROVIDER`.
- Existing provider methods are sweep-oriented but already accept the receiver, resource deficit, account resources, and minimum readiness thresholds. The payout helper should reuse this layer instead of hardcoding ProfeeX.
- ProfeeX has documented `GET /delegation/fee` for USDT transfer energy estimation. The existing `tron_client.get_estimated_energy()` path can return invalid values for this flow and must not be used for payout resource sizing.

Observed external consumer behavior, using Grither Pay as the first known consumer:

- `/Users/test/IdeaProjects/grither-pay/apps/backend/src/main/java/com/grither/pay/wallet/application/WalletAltynWithdrawalCreationService.java` already generates `externalId = "WW-" + publicNumber` for USDT withdrawals.
- Grither Pay owns ledger holds, idempotency keys, active-withdrawal partial unique indexes, and user retry policy.
- In this SHKeeper phase, external consumers should call the existing SHKeeper payout endpoint with Basic Auth and `external_id`. SHKeeper must add defensive execution guards, not consumer-specific ledger, refund, or ambiguous-withdrawal state.

Important constraint:

- SHKeeper cannot reliably distinguish "external consumer over Basic Auth" from "manual API caller over Basic Auth" without adding a new endpoint, header, HMAC, or scoped credential. The user explicitly removed HMAC and endpoint/auth changes from this scope. Therefore SHKeeper must not globally reject payout requests missing `external_id`, because that would change existing admin/API behavior. The safe minimal design is:
  - External consumers that need idempotent payouts must always send `external_id`.
  - SHKeeper enforces race-safe duplicate protection whenever `external_id` is present.
  - Admin payouts without `external_id` remain legacy-compatible.

## Architecture Decisions

- [ ] Keep `POST /api/v1/<crypto_name>/payout` path and decorators unchanged.
- [ ] Keep Basic Auth and admin session auth unchanged.
- [ ] Do not add HMAC in this phase.
- [ ] Do not add an application IP allowlist in this phase.
- [ ] Do not change the admin payout template or add frontend resource quote UI in this phase.
- [ ] Do not add TRON USDT resource provisioning to multipayout. A minimal
  multipayout validation reorder is allowed only to avoid the new unique index
  creating a post-enqueue DB failure.
- [ ] Do not change TON, ETH, BTC-like, EVM, Lightning, Monero, TRX, or TRON USDC payout behavior.
- [ ] Add a DB unique constraint for `(crypto, external_id)` so duplicate external IDs cannot race through concurrent requests.
- [ ] Preserve multiple rows with `external_id IS NULL`; admin payouts and autopayouts without external IDs must keep working.
- [ ] For requests with `external_id`, create the SHKeeper `Payout` row before calling the sidecar, then update `task_id` when the sidecar returns it.
- [ ] On ambiguous sidecar enqueue errors after the request may have reached the sidecar, do not mark the payout as failed automatically. Keep it `IN_PROGRESS`, keep `task_id = NULL`, store the error text, and require manual reconciliation/status handling. This avoids telling an API consumer to retry while a sidecar task may still broadcast.
- [ ] For clear pre-enqueue validation failures, mark the reserved payout as `FAIL`.
- [ ] Use the configured resource provider only for deficient resources for the current payout. If resources are already sufficient, create no provider order/delegation.
- [ ] For this phase, fee-wallet payout energy acquisition supports external
  providers `refee` and `profeex`. Treat `ENERGY_PROVIDER=staking` as missing
  for fee-wallet payout deficits, while leaving existing sweep staking behavior
  unchanged.
- [ ] Use a dedicated Celery queue for USDT single payouts and deploy exactly one worker slot for that queue.
- [ ] Recompute resources at execution time, not only at backend preflight time.
- [ ] Do not add automatic SHKeeper retry for `DUPLICATE_REQUEST`,
  `RATE_LIMIT_EXCEEDED`, or provider outages. Return controlled failure/no
  broadcast and let the external consumer decide its business retry policy.

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
    bind.execute(
        sa.text(
            """
            UPDATE payout
            SET external_id = NULL
            WHERE external_id IS NOT NULL AND TRIM(external_id) = ''
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE payout
            SET external_id = TRIM(external_id)
            WHERE external_id IS NOT NULL AND external_id <> TRIM(external_id)
            """
        )
    )
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
- Existing blank or whitespace-only `external_id` values are normalized to `NULL`
  before the unique index is created.
- Existing non-empty `external_id` values are trimmed before duplicate detection.
- A second payout with the same non-empty `(crypto, external_id)` fails at DB level.
- Existing admin payouts without `external_id` are unaffected.

### 2. Make PayoutService Reserve External IDs Before Sidecar Enqueue

- [ ] Add `shkeeper/services/payout_errors.py`.

Keep payout API error types in a small standalone module so
`tron_token.py`, `payout_service.py`, and `api_v1.py` can import them without a
service/class import cycle:

```python
class PayoutRequestError(ValueError):
    status_code = 400
    code = "PAYOUT_REQUEST_ERROR"

    def __init__(self, message, *, code=None, status_code=None):
        super().__init__(message)
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code


class PayoutConflictError(PayoutRequestError):
    status_code = 409
    code = "PAYOUT_EXTERNAL_ID_CONFLICT"


class PayoutResourceUnavailableError(PayoutRequestError):
    status_code = 503
    code = "PAYOUT_RESOURCE_UNAVAILABLE"


class PayoutDestinationNotActivatedError(PayoutRequestError):
    status_code = 400
    code = "DESTINATION_NOT_ACTIVATED"
```

- [ ] Update `shkeeper/services/payout_service.py`.

- [ ] Add helpers to normalize external IDs and validate fee/amount before
  sidecar enqueue:

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

- [ ] Import payout request errors in `payout_service.py`:

```python
from shkeeper.services.payout_errors import PayoutConflictError, PayoutRequestError
```

- [ ] Add a preflight hook that is a no-op for cryptos without the hook. Let
  typed payout errors pass through unchanged:

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
            except PayoutRequestError:
                raise
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

    @staticmethod
    def validate_positive_amount(amount):
        if amount <= 0:
            raise PayoutRequestError(
                "Payout amount should be a positive number",
                code="INVALID_AMOUNT",
                status_code=400,
            )
```

- [ ] Add a reserved path for requests with normalized `external_id`.

Flow:

1. Validate `destination` presence, positive `amount`, `callback_url`, and
   fee/default fee before DB insert.
2. Run `preflight_payout()`. For TRON USDT this hook validates sidecar
   availability, token balance, destination activation, and resource readiness.
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
        amount = Decimal(req["amount"])
        cls.validate_positive_amount(amount)
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
            amount,
            fee,
        )
        task_id = res.get("task_id") if isinstance(res, dict) else None
        if not task_id:
            raise PayoutRequestError(f"Payout sidecar did not return task_id: {res}")
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

- [ ] Move multipayout validation before `crypto.multipayout()` without adding
  resource provisioning to multipayout:
  - validate each callback URL before sidecar enqueue;
  - normalize non-empty external IDs;
  - reject duplicate external IDs inside the same request body;
  - precheck existing DB duplicates before sidecar enqueue.

Reason: the new DB unique constraint must not create a new post-enqueue failure path for multipayout external IDs.

Acceptance:

- Admin/manual payout without `external_id` keeps the old sidecar-first flow.
- API payout with `external_id` creates a DB reservation before sidecar enqueue.
- Two concurrent same `(crypto, external_id)` requests create at most one sidecar enqueue.
- A clear sidecar response without `task_id` is rejected and does not create a
  misleading `IN_PROGRESS` payout row on the legacy no-`external_id` path.
- Positive amount is validated generically before sidecar enqueue.
- TRON USDT token balance is validated by the TRON USDT preflight hook; other
  crypto balance behavior remains unchanged.
- Multipayout has no resource provisioning change and only gets the minimal
  validation reorder needed for unique-index compatibility.

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

- [ ] Import `PayoutRequestError` from `shkeeper.services.payout_errors`.

Acceptance:

- Duplicate `external_id` returns HTTP 409.
- Invalid destination, invalid amount, insufficient token balance, and
  unactivated destination return HTTP 400 with a stable `code`.
- Resource estimator, sidecar, or provider availability failures return HTTP
  503 with `code="PAYOUT_RESOURCE_UNAVAILABLE"`.
- Unexpected exceptions still return HTTP 500 as before.

### 4. Extend Payout Status Response Without Breaking Existing Clients

- [ ] Update `shkeeper/api_v1.py` `payout_status()`.

Add fields:

```python
"task_id": payout.task_id,
"success": payout.success,
"error": payout.error,
"txids": [tx.txid for tx in payout.transactions],
"reconciliation_required": (
    payout.status.name == "IN_PROGRESS"
    and payout.task_id is None
    and bool(payout.error)
),
```

Keep existing fields unchanged.

Acceptance:

- Existing clients reading `id`, `external_id`, `crypto`, `status`, `amount`, `destination`, and `txid` still work.
- API consumers can detect `IN_PROGRESS` with `task_id = null`, non-empty
  `error`, and `reconciliation_required = true` as an ambiguous/manual-review
  state.

### 5. Add TRON USDT Quote and Backend Preflight Hook

- [ ] Update `shkeeper/modules/classes/tron_token.py`.

Import typed payout errors:

```python
from shkeeper.services.payout_errors import (
    PayoutDestinationNotActivatedError,
    PayoutRequestError,
    PayoutResourceUnavailableError,
)
```

Pass `address` into sidecar estimate and classify sidecar availability
failures as `503`:

```python
    def estimate_tx_fee(self, amount, **kwargs):
        params = {}
        if kwargs.get("address"):
            params["address"] = kwargs["address"]
        try:
            response = requests.post(
                f"http://{self.gethost()}/{self.crypto}/calc-tx-fee/{amount}",
                auth=self.get_auth_creds(),
                params=params,
                timeout=10,
            )
        except requests.exceptions.RequestException as exc:
            raise PayoutResourceUnavailableError("TRON sidecar fee estimate unavailable") from exc
        if response.status_code >= 500:
            raise PayoutResourceUnavailableError(
                f"TRON sidecar fee estimate returned HTTP {response.status_code}"
            )
        try:
            return response.json(parse_float=Decimal)
        except ValueError as exc:
            raise PayoutResourceUnavailableError("TRON sidecar fee estimate returned invalid JSON") from exc
```

- [ ] Add USDT-only fee omission:

```python
    def can_omit_fee_for_payout(self):
        return self.crypto == "USDT"
```

- [ ] Add USDT-only backend preflight:

```python
    def _usdt_payout_balance_for_preflight(self):
        try:
            response = requests.post(
                f"http://{self.gethost()}/{self.crypto}/balance",
                auth=self.get_auth_creds(),
                timeout=10,
            )
        except requests.exceptions.RequestException as exc:
            raise PayoutResourceUnavailableError("TRON USDT balance check unavailable") from exc
        if response.status_code >= 500:
            raise PayoutResourceUnavailableError(
                f"TRON USDT balance check returned HTTP {response.status_code}"
            )
        try:
            data = response.json(parse_float=Decimal)
        except ValueError as exc:
            raise PayoutResourceUnavailableError("TRON USDT balance check returned invalid JSON") from exc
        return Decimal(data["balance"])

    def preflight_payout(self, destination, amount):
        if self.crypto != "USDT":
            return
        balance = self._usdt_payout_balance_for_preflight()
        if amount > balance:
            raise PayoutRequestError(
                f"Payout amount exceeds wallet balance: {amount} > {balance}",
                code="INSUFFICIENT_BALANCE",
                status_code=400,
            )
        quote = self.estimate_tx_fee(amount, address=destination)
        if quote.get("status") == "error" or quote.get("error"):
            code = quote.get("code")
            message = quote.get("message") or quote.get("error") or "TRON USDT fee estimate failed"
            if code in {"PAYOUT_RESOURCE_UNAVAILABLE", "PROFEEX_ESTIMATE_UNAVAILABLE"}:
                raise PayoutResourceUnavailableError(message)
            raise PayoutRequestError(message, code=code or "TRON_USDT_PREFLIGHT_ERROR")
        if quote.get("code") == "DESTINATION_NOT_ACTIVATED":
            raise PayoutDestinationNotActivatedError(
                quote.get("message") or "TRON payout destination is not activated"
            )
        resource_quote = quote.get("resource_quote")
        if not resource_quote:
            if "fee" not in quote:
                raise PayoutResourceUnavailableError(
                    "TRON USDT fee estimate returned no fee or resource quote"
                )
            return
        if not resource_quote.get("submit_ready"):
            reason = resource_quote.get("blocking_reason") or "TRON USDT payout resources are not ready"
            code = resource_quote.get("blocking_code")
            if code == "DESTINATION_NOT_ACTIVATED":
                raise PayoutDestinationNotActivatedError(reason)
            if code in {"PROFEEX_ESTIMATE_UNAVAILABLE", "PROVIDER_UNAVAILABLE"}:
                raise PayoutResourceUnavailableError(reason)
            raise PayoutRequestError(reason, code=code or "TRON_USDT_PREFLIGHT_NOT_READY")
```

Acceptance:

- Feature-disabled sidecar returns legacy static fee and does not block.
- Sidecar error JSON or malformed estimate JSON blocks before enqueue.
- Feature-enabled sidecar returns `resource_quote`; API payout blocks when `submit_ready` is false.
- Temporary balance/estimate outage returns HTTP 503, not false
  `INSUFFICIENT_BALANCE`.
- `is_new_address=true` from ProfeeX blocks with
  `DESTINATION_NOT_ACTIVATED`.
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

Extend config validation:

```python
        if self.TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED and self.PROFEEX is None:
            raise ValueError(
                "PROFEEX must be configured when TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED=true"
            )
```

Reason: this payout path uses ProfeeX `GET /delegation/fee` as the USDT energy
estimator even when `ENERGY_PROVIDER=refee` is used for the actual rental.

Acceptance:

- Default deployment keeps old behavior.
- Enabling requires explicit env configuration.
- Enabling requires `PROFEEX`, because ProfeeX is the energy estimator for this
  payout flow.

### 7. Add ProfeeX USDT Energy Estimator

- [ ] Keep the existing provider selection as the runtime source of truth for
  actual resource acquisition:

```python
from app.resource_providers.factory import get_bandwidth_provider, get_energy_provider
```

- [ ] Do not hardcode ProfeeX for resource acquisition. The payout helper must
  still use:

```python
energy_provider = configured_energy_provider(client)
bandwidth_provider = get_bandwidth_provider(tron_client=client)
```

`configured_energy_provider()` should wrap the shared factory and return `None`
for `ENERGY_PROVIDER=staking` in this fee-wallet payout flow, while leaving the
factory itself unchanged for sweep.

- [ ] Keep existing sweep provider behavior compatible. Do not change the existing
  `EnergyProvider.acquire_energy()` and `BandwidthProvider.acquire_bandwidth()`
  signatures in a breaking way.

- [ ] Add a ProfeeX USDT transfer energy estimator. This is intentionally
  ProfeeX-specific because the existing local node estimate can return invalid
  values for this flow.

```python
    def estimate_usdt_transfer_fee(self, receiver_address: str) -> dict | None:
        settings = config.PROFEEX
        if settings is None:
            return None
        try:
            response = requests.get(
                self._url(settings, "/delegation/fee"),
                params={"receiver_address": receiver_address},
                headers=self._headers(settings),
                timeout=self.REQUEST_TIMEOUT_SEC,
            )
        except requests.RequestException:
            logger.exception("ProfeeX USDT fee estimate request failed")
            return None
        if response.status_code != 200:
            logger.warning(
                f"ProfeeX USDT fee estimate rejected with status "
                f"{response.status_code}: {response.text}"
            )
            return None
        try:
            data = response.json()
        except ValueError:
            logger.exception("ProfeeX USDT fee estimate response is not valid JSON")
            return None
        if not isinstance(data, dict):
            logger.warning(f"ProfeeX USDT fee estimate response is not an object: {data}")
            return None
        if not isinstance(data.get("energy_required"), int):
            logger.warning(f"ProfeeX USDT fee estimate has no energy_required: {data}")
            return None
        if not isinstance(data.get("is_new_address"), bool):
            logger.warning(f"ProfeeX USDT fee estimate has no is_new_address flag: {data}")
            return None
        if "trx_burned" not in data:
            logger.warning(f"ProfeeX USDT fee estimate has no trx_burned field: {data}")
            return None
        return data
```

- [ ] Add ProfeeX failure classification without changing existing sweep method
  return contracts. This is for clearer payout task errors and better logs.

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

Add a small exception and classifier:

```python
class ProfeeXOrderError(RuntimeError):
    def __init__(self, resource_name, message, error_code=None, temporary=False):
        super().__init__(message)
        self.resource_name = resource_name
        self.error_code = error_code
        self.temporary = temporary


    def _order_error_from_order(self, resource_name: str, order: dict) -> ProfeeXOrderError:
        error_code = order.get("error_code")
        details = order.get("details") or {}
        message = details.get("error_message") or f"ProfeeX {resource_name} order failed: {order}"
        temporary = error_code in TEMPORARY_ERROR_CODES
        return ProfeeXOrderError(resource_name, message, error_code, temporary)
```

Acceptance:

- Existing `acquire_energy()` and `acquire_bandwidth()` behavior for sweep remains compatible.
- Payout readiness uses the same configured external re:Fee or ProfeeX energy
  providers as sweep. Staking remains supported for existing sweep, but
  staking-based acquisition for fee-wallet payout deficits is intentionally
  blocked in this phase.
- Payout readiness can use the same configured re:Fee or ProfeeX bandwidth providers as sweep.
- USDT payout energy estimation uses ProfeeX `GET /delegation/fee`.
- USDT payout quote preserves `is_new_address` and `trx_burned`; new addresses
  are blocked before enqueue/broadcast.
- ProfeeX `precount` and admin provider cost display are not part of this phase.
- Logs include provider name, provider reference/task id when available, status, and `error_code`, never API keys.

### 8. Add Sidecar Payout Resource Helper

- [ ] Add `../tron-shkeeper/app/payout_resources.py`.

Imports:

```python
from dataclasses import dataclass
from decimal import Decimal
import time

import tronpy.keys

from app.connection_manager import ConnectionManager
from app.config import config
from app.resource_providers.profeex import ProfeeXProvider
from app.resource_providers.factory import get_bandwidth_provider, get_energy_provider
from app.schemas import KeyType
from app.utils import get_available_energy, get_key, has_free_bw
```

Core types:

```python
@dataclass
class ResourceReadiness:
    provider: str | None
    required: int
    available: int
    deficit: int


@dataclass
class PayoutResourceQuote:
    source_address: str
    destination: str
    amount: str
    activation_required: bool
    estimated_trx_burned: str | None
    energy: ResourceReadiness
    bandwidth: ResourceReadiness
    submit_ready: bool
    blocking_code: str | None
    blocking_reason: str | None


class PayoutResourceError(RuntimeError):
    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.code = code
```

Helper functions:

```python
def get_available_bandwidth(account_resource: dict) -> int:
    staked = max(account_resource.get("NetLimit", 0) - account_resource.get("NetUsed", 0), 0)
    daily = max(account_resource.get("freeNetLimit", 0) - account_resource.get("freeNetUsed", 0), 0)
    return max(staked, daily)


def provider_label(provider, configured_name: str) -> str | None:
    if provider is None:
        return None
    return configured_name


def configured_energy_provider(tron_client):
    if config.ENERGY_PROVIDER == "staking":
        return None
    return get_energy_provider(tron_client=tron_client)


def estimate_usdt_transfer_fee_via_profeex(destination: str) -> dict | None:
    estimate = ProfeeXProvider().estimate_usdt_transfer_fee(destination)
    if not estimate:
        return None
    return estimate
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
    fee_estimate = estimate_usdt_transfer_fee_via_profeex(destination)
    blocking_reason = None
    blocking_code = None
    activation_required = False
    estimated_trx_burned = None
    if fee_estimate is None:
        blocking_code = "PROFEEX_ESTIMATE_UNAVAILABLE"
        blocking_reason = "Unable to estimate TRON USDT transfer energy through ProfeeX"
        energy_required = 0
    else:
        energy_required = fee_estimate["energy_required"]
        activation_required = bool(fee_estimate.get("is_new_address"))
        trx_burned = fee_estimate.get("trx_burned")
        estimated_trx_burned = str(trx_burned) if trx_burned is not None else None
        if activation_required:
            blocking_code = "DESTINATION_NOT_ACTIVATED"
            blocking_reason = "TRON payout destination is not activated"
    energy_available = get_available_energy(account_resource)
    energy_deficit = max(energy_required - energy_available, 0)
    bandwidth_required = config.BANDWIDTH_PER_TRC20_TRANSFER_CALL
    bandwidth_available = get_available_bandwidth(account_resource)
    bandwidth_deficit = 0 if has_free_bw(
        fee_deposit_address,
        bandwidth_required,
        tron_client=client,
    ) else max(bandwidth_required - bandwidth_available, 0)

    energy_provider = configured_energy_provider(client)
    bandwidth_provider = get_bandwidth_provider(tron_client=client)

    if blocking_reason is None and energy_deficit and energy_provider is None:
        blocking_code = "PROVIDER_UNAVAILABLE"
        blocking_reason = "No energy provider is configured for TRON USDT payout resources"
    elif bandwidth_deficit and bandwidth_provider is None:
        blocking_code = "PROVIDER_UNAVAILABLE"
        blocking_reason = "No bandwidth provider is configured for TRON USDT payout resources"

    return PayoutResourceQuote(
        source_address=fee_deposit_address,
        destination=destination,
        amount=str(amount),
        activation_required=activation_required,
        estimated_trx_burned=estimated_trx_burned,
        energy=ResourceReadiness(
            provider=provider_label(energy_provider, config.ENERGY_PROVIDER),
            required=energy_required,
            available=energy_available,
            deficit=energy_deficit,
        ),
        bandwidth=ResourceReadiness(
            provider=provider_label(bandwidth_provider, config.BANDWIDTH_PROVIDER),
            required=bandwidth_required,
            available=bandwidth_available,
            deficit=bandwidth_deficit,
        ),
        submit_ready=blocking_reason is None,
        blocking_code=blocking_code,
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
        raise PayoutResourceError(
            quote.blocking_reason or "TRON USDT payout resources are not ready",
            code=quote.blocking_code,
        )

    client = tron_client or ConnectionManager.client()
    if quote.energy.deficit:
        energy_provider = configured_energy_provider(client)
        if energy_provider is None:
            raise PayoutResourceError("No energy provider is configured", code="PROVIDER_UNAVAILABLE")
        account_resource = client.get_account_resource(quote.source_address)
        if not energy_provider.acquire_energy(
            quote.source_address,
            quote.energy.deficit,
            account_resource,
            minimum_energy_required=quote.energy.required,
        ):
            raise PayoutResourceError("Energy provider failed to prepare resources", code="PROVIDER_FAILED")
    if quote.bandwidth.deficit:
        bandwidth_provider = get_bandwidth_provider(tron_client=client)
        if bandwidth_provider is None:
            raise PayoutResourceError("No bandwidth provider is configured", code="PROVIDER_UNAVAILABLE")
        if not bandwidth_provider.acquire_bandwidth(
            quote.source_address,
            quote.bandwidth.required,
        ):
            raise PayoutResourceError("Bandwidth provider failed to prepare resources", code="PROVIDER_FAILED")

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
        "TRON USDT payout resources are still insufficient after provider provisioning",
        code="RESOURCE_RECHECK_FAILED",
    )
```

- [ ] Convert dataclasses to JSON response in a small `to_dict()` method or with `dataclasses.asdict()`.

Acceptance:

- Existing sweep task code is not imported into this helper.
- Helper targets `fee_deposit`, not the destination wallet.
- Helper estimates USDT transfer energy through ProfeeX `/delegation/fee`, not
  through `tron_client.get_estimated_energy()`.
- Helper calls configured providers only for current deficits.
- Helper does not add custom splitting for re:Fee fixed `65_000` energy orders;
  the final on-chain recheck is the broadcast gate.
- Helper blocks broadcast when energy estimate, provider acquisition, or post-active
  recheck fails.
- Helper blocks broadcast when ProfeeX reports `is_new_address=true`.
- Helper treats `ENERGY_PROVIDER=staking` as unavailable for fee-wallet payout
  deficits in this phase.

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
        try:
            quote = estimate_fee_deposit_resources_for_usdt_payout(destination, amount)
        except PayoutResourceError as exc:
            return {
                "status": "error",
                "code": exc.code or "PAYOUT_RESOURCE_UNAVAILABLE",
                "message": str(exc),
            }, 503
        except Exception as exc:
            return {
                "status": "error",
                "code": "INVALID_DESTINATION",
                "message": str(exc),
            }, 400
        return {
            "fee": "0",
            "resource_quote": dataclasses.asdict(quote),
        }
    if (
        config.TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED
        and g.symbol == "USDT"
        and not destination
    ):
        return {"fee": config.TX_FEE}
    return {"fee": config.TX_FEE}
```

Acceptance:

- Existing callers without the feature flag still receive `{"fee": config.TX_FEE}`.
- Feature-enabled USDT estimates without destination still receive
  `{"fee": config.TX_FEE}` so the current admin template remains unchanged.
- Feature-enabled USDT backend preflight with destination includes a structured
  resource quote.
- Feature-enabled USDT backend preflight returns structured error JSON for
  invalid destination or helper failures instead of an unclassified 500.

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
                result = wallet.transfer(step["dst"], step["amount"])
                if result.get("status") != "success":
                    raise Exception(f"USDT payout transfer failed: {result}")
                payout_results.append(result)
            else:
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
- If `Wallet.transfer()` returns `status != "success"` in the USDT resource
  path, Celery task fails with a controlled error instead of leaving the payout
  indefinitely `IN_PROGRESS`.
- Multipayout steps do not have the marker and do not use this helper.
- Non-USDT payout task path remains byte-for-byte equivalent except surrounding branch.

## Documentation and Rollout Tasks

### 12. Update Deployment Notes

- [ ] Update `docs/DEPLOYMENT.md` in `shkeeper.io` only after implementation.

Document:

```text
TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED=true

# Required for USDT payout energy estimation via /delegation/fee.
PROFEEX='{"api_key":"example-secret","energy_duration_label":"1h","bandwidth_duration_label":"1h","currency":"TRX"}'

# Example: ProfeeX as the configured global TRON resource provider.
# ENERGY_PROVIDER=profeex
# BANDWIDTH_PROVIDER=profeex

# Example: re:Fee as the configured global TRON resource provider.
# ENERGY_PROVIDER=refee
# BANDWIDTH_PROVIDER=refee
# REFEE='{"api_key":"example-secret","rent_duration_label":"1h"}'

TRON_USDT_PAYOUT_QUEUE=tron_usdt_fee_payouts
```

Document that `ENERGY_PROVIDER` and `BANDWIDTH_PROVIDER` are global TRON
resource-provider settings in the sidecar. Changing them changes both existing
sweep resource provisioning and the new fee-wallet payout readiness flow. A
provider switch must run both sweep and single-payout smoke tests.

Document that `ENERGY_PROVIDER=staking` remains valid for existing sweep, but
is treated as unavailable for fee-wallet payout resource deficits in this phase.

Worker:

```bash
celery -A celery_worker.celery worker -E --loglevel=info -Q tron_usdt_fee_payouts --concurrency=1
```

Rollout order:

1. Deploy code with `TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED=false`.
2. Run DB migration.
3. Start dedicated queue worker.
4. Configure ProfeeX for energy estimation and configure the chosen resource provider.
5. Enable sidecar feature.
6. Confirm the existing admin estimate still shows the legacy static fee.
7. Test one small USDT TRC-20 single payout.
8. Point server-to-server consumers such as Grither Pay to the existing payout endpoint over private Yandex networking when available.

### 13. External API Consumer Contract

- [ ] Do not change external consumers such as Grither Pay in this SHKeeper implementation unless the user explicitly expands scope.
- [ ] Document the required request contract for idempotent external consumers:

```json
{
  "external_id": "WW-123456",
  "destination": "TXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
  "amount": "100.25",
  "callback_url": "https://consumer.example/internal/shkeeper/payout-callback"
}
```

- [ ] Document that SHKeeper accepts `fee` but idempotent USDT TRC-20 consumers do not need to send it when the resource-provisioning path can derive it.
- [ ] Document that TRON USDT payouts to unactivated destinations are rejected
  with `DESTINATION_NOT_ACTIVATED`; the consumer should ask the user for an
  already activated TRON address or handle activation outside this SHKeeper
  flow.
- [ ] Document that SHKeeper remains generic: consumer-side ledger, refund, ambiguous withdrawal status, and user retry policy stay outside SHKeeper.
- [ ] Document that duplicate `external_id` means the same consumer payout attempt already reached SHKeeper and must be handled by status lookup, not blind retry.
- [ ] Document auth clearly: payout creation continues to use the existing
  Basic Auth/admin-session path, while the existing payout status endpoint is
  API-key protected. External consumers that need status lookup must be
  configured with both credentials unless a separate auth change is explicitly
  scoped later.
- [ ] Document the temporary auth limitation: the existing API-key decorator is
  not crypto-scoped. Do not redesign it in this phase, but record it as a
  follow-up before broader third-party API exposure.
- [ ] Document that rapid sequential payouts may still fail with controlled
  provider cooldown/rate-limit errors and are not auto-retried by SHKeeper in
  this phase.

## Tests

### 14. Main App Tests

- [ ] Add `tests/test_payout_service_external_id.py`.

Test cases:

- `test_null_external_id_allows_multiple_payout_rows`
- `test_external_id_normalization_trims_whitespace_before_unique_check`
- `test_duplicate_external_id_is_rejected_before_sidecar_call`
- `test_concurrent_duplicate_external_id_hits_unique_constraint`
- `test_external_id_path_creates_payout_before_sidecar_task_id`
- `test_external_id_path_updates_task_id_after_sidecar_success`
- `test_sidecar_response_without_task_id_is_rejected_without_payout_record`
- `test_missing_fee_allowed_for_tron_usdt_only`
- `test_missing_fee_still_rejected_for_non_tron_usdt`
- `test_tron_usdt_payout_amount_exceeding_wallet_balance_is_rejected_before_sidecar_call`
- `test_tron_usdt_balance_unavailable_returns_resource_unavailable`
- `test_multipayout_validates_external_ids_before_sidecar_call`
- `test_multipayout_rejects_duplicate_external_ids_inside_same_request_before_sidecar_call`

- [ ] Add `tests/test_tron_token_payout_preflight.py`.

Test cases:

- `test_estimate_tx_fee_passes_destination_as_address_query_param`
- `test_preflight_allows_legacy_static_fee_response`
- `test_preflight_rejects_sidecar_error_json_without_resource_quote`
- `test_preflight_rejects_malformed_estimate_without_fee_or_resource_quote`
- `test_preflight_rejects_structured_quote_when_not_submit_ready`
- `test_preflight_rejects_destination_not_activated`
- `test_preflight_maps_sidecar_unavailable_to_503_error`
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

### 15. TRON Sidecar Tests

- [ ] Add `../tron-shkeeper/tests/test_payout_resources.py`.

Test cases:

- `test_quote_uses_profeex_fee_endpoint_for_energy_required`
- `test_quote_preserves_profeex_activation_and_trx_burn_fields`
- `test_quote_blocks_destination_not_activated`
- `test_quote_blocks_when_profeex_fee_estimate_fails`
- `test_quote_uses_configured_refee_energy_provider_for_acquisition`
- `test_quote_blocks_staking_provider_for_fee_wallet_energy_deficit`
- `test_quote_blocks_when_configured_provider_missing_and_resource_deficit_exists`
- `test_ensure_calls_configured_energy_provider_then_rechecks_before_return`
- `test_ensure_calls_configured_bandwidth_provider_then_rechecks_before_return`
- `test_ensure_raises_before_broadcast_when_resources_still_deficient`

- [ ] Update `../tron-shkeeper/tests/test_profeex_bandwidth_provider.py`.

Add cases:

- `test_estimate_usdt_transfer_fee_uses_receiver_address_and_api_key`
- `test_estimate_usdt_transfer_fee_rejects_response_without_energy_required`
- `test_order_error_marks_duplicate_request_temporary`
- `test_order_error_marks_rate_limit_temporary`

- [ ] Add `../tron-shkeeper/tests/test_payout_task_resource_provisioning.py`.

Test cases:

- `test_prepare_payout_marks_usdt_single_when_feature_enabled`
- `test_prepare_multipayout_does_not_mark_resource_provisioning`
- `test_payout_calls_resource_helper_before_wallet_transfer`
- `test_payout_raises_when_wallet_transfer_returns_error_status`
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

### 16. Manual Smoke Tests

- [ ] With feature disabled, confirm existing admin USDT payout form still shows static fee.
- [ ] With feature enabled, confirm existing admin USDT payout form still shows static fee and has no new provider UI.
- [ ] Confirm API payout with duplicate `external_id` returns HTTP 409 and does not call sidecar a second time.
- [ ] Confirm one small USDT payout reaches Celery task, provisions resources when deficient, and broadcasts only after resource recheck.
- [ ] Confirm payout to a known unactivated destination is rejected before enqueue/broadcast.
- [ ] Confirm two rapid USDT payouts enter `tron_usdt_fee_payouts` queue and execute sequentially. If the provider rejects the second request with cooldown/rate-limit, confirm the failure is controlled and unbroadcast.
- [ ] Confirm existing sweep still provisions resources with the currently configured provider.

## Review Checklist

- [ ] Auth decorators on `POST /api/v1/<crypto_name>/payout` are unchanged.
- [ ] Admin browser payout still uses the existing session path.
- [ ] Basic Auth payout still works.
- [ ] No HMAC code is added.
- [ ] No multipayout resource provisioning is added.
- [ ] No resource provisioning is added for TON, ETH, BTC-like, EVM, Lightning, Monero, TRX, or TRON USDC.
- [ ] New sidecar helper is isolated in `app/payout_resources.py`.
- [ ] Existing sweep behavior keeps using the shared provider layer.
- [ ] `ENERGY_PROVIDER=staking` is blocked only for fee-wallet payout deficits;
  sweep staking behavior is unchanged.
- [ ] Configured provider sweep regression tests still pass.
- [ ] ProfeeX fixed-order sweep and `/delegation/fee` estimator tests still pass when ProfeeX is configured.
- [ ] Admin payout template is unchanged in this phase.
- [ ] Payout task cannot call `Wallet.transfer()` after a resource helper failure.
- [ ] Payout task cannot leave failed USDT transfer receipts indefinitely
  `IN_PROGRESS`.
- [ ] Ambiguous sidecar enqueue failures do not produce an automatic duplicate retry path.
- [ ] The unique `(crypto, external_id)` guard has a migration precheck for existing duplicate data.
- [ ] All new behavior is disabled by default through sidecar config.

## Recommended Execution Order

1. Implement main app duplicate guard and tests first. This is independent and protects idempotent consumer retries.
2. Implement ProfeeX `/delegation/fee` estimator in the sidecar.
3. Implement `app/payout_resources.py` with unit tests.
4. Integrate the sidecar estimate endpoint and payout task marker.
5. Add main app TRON estimate/preflight pass-through.
6. Update docs and run smoke tests.

This order keeps each step small, reduces fork merge conflict risk, and makes the most dangerous behavior, duplicate payout creation, testable before touching broadcast logic.
