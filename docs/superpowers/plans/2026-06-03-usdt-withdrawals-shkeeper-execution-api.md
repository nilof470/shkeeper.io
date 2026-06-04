# USDT Withdrawals SHKeeper Execution API Implementation Plan

> **For agentic workers:** use concrete skills, not a generic Superpowers-style
> flow. Start with `investigate` to validate code reality, use `review` after
> each implementation block, and use `careful` before destructive production or
> Kubernetes operations. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** build the SHKeeper service-to-service payout execution API and durable state boundary for client USDT withdrawals.

**Architecture:** SHKeeper keeps legacy admin payout behavior intact and adds a separate client-withdrawal execution contract. `PayoutExecution` is the durable source of truth for service-consumer client payouts; sidecars are called through an idempotent dispatcher/reconciler boundary; callbacks are delivered from a durable outbox, not inline best-effort calls.

**Tech Stack:** Python, Flask, flask-smorest/Blueprint routes, SQLAlchemy, Alembic, pytest/unittest, HMAC-SHA256, Decimal canonical amount handling.

**Current Status, 2026-06-04:** implementation is present in the current
worktree and has passed focused verification. Commit steps are intentionally
left open until the whole SHKeeper block is reviewed and staged.

Verified:

- `PayoutExecution`, `PayoutRail`, `PayoutCallbackEvent`, `PayoutAuthNonce`, and
  additive migration are implemented.
- Service-consumer submit/status API uses HMAC auth, timestamp tolerance, nonce
  replay protection, idempotency by `(consumer, external_id)`, canonical request
  hashing, and configured callback endpoints.
- Reconciler covers pre-submit dispatch and active sidecar polling states:
  `CREATED`, `PREFLIGHTED`, `ENQUEUEING`, `ENQUEUED`, and `BROADCAST`.
- Submit timeout after `ENQUEUEING` fails closed into reconciliation; routine
  status polling outages after accepted submit use retry/backoff without blind
  resubmit.
- Durable callback outbox stores immutable signed callback events and dispatch
  retry metadata.
- Legacy `/payout` and `/multipayout` admin behavior remains available with
  operator audit context, while automatic legacy bypass can be blocked for
  execution-enabled rails.
- Production-code naming is consumer-generic; Grither Pay appears only as an
  example consumer in docs/tests/config examples.

Review fixes applied:

- Existing DB startup now runs migrations before `create_all` can create new
  payout tables; fresh DBs still use `create_all` plus Alembic `stamp head`.
- Non-finite decimal values are rejected at payout API, rail config, and legacy
  payout service boundaries.
- Malformed or non-object JSON request bodies return 400 instead of generic 500.
- SHKeeper-to-sidecar HTTP calls use bounded request timeouts.
- Reconciler now polls accepted async executions until `BROADCAST`/`CONFIRMED`
  or terminal failure instead of leaving them stuck in `ENQUEUED`.
- Nested consumer/callback key config is supported, and scoped consumer keys are
  enforced against the requested rail on both submit and status reads.
- Callback outbox dispatch now uses a claimed `DISPATCHING` lease and preserves
  per-execution callback version ordering across retries.
- Sidecar submit responses without ordering metadata are not accepted as
  successful ordered handoff evidence; they move the execution to reconciliation.
- Shared-wallet guard policy fails closed until a real allocator/guard is
  available, while legacy manual admin payout remains available on ordinary
  current-source-wallet rails.
- `payout-rail-sync` fails closed on invalid booleans, enabled rails without a
  callback endpoint, non-USDT decimals, duplicate desired rails, and partially
  invalid rail batches. Invalid batches roll back instead of leaving earlier
  rails committed. SHKeeper does not know or validate customer withdrawal policy.
- Direct TRON/ETH/TON sidecar adapter payout methods now require a service-layer
  legacy spend guard context. Direct `crypto.mkpayout`/`crypto.multipayout`
  calls are blocked for enabled client-withdrawal rails before HTTP sidecar
  submit, while `PayoutService` manual/admin flows still pass with operator
  audit context.
- Service auth response semantics now distinguish missing/unknown credentials
  from forbidden signed requests: missing auth remains 401, while tampered
  bodies, wrong method/path/query signatures, expired timestamps, and replayed
  nonces return 403. Regression coverage binds the signature base to method,
  canonical path, query string, and body hash.

Verification commands run:

```bash
PYTHONPATH=.venv/lib/python3.12/site-packages pytest tests/test_payout_callback_outbox.py tests/test_payout_execution_api.py tests/test_payout_execution_models.py tests/test_payout_execution_reconciler.py tests/test_payout_rail_sync.py tests/test_payout_sidecar_client.py tests/test_payout_status_response.py tests/test_payout_service_external_id.py tests/test_payout_tron_template.py tests/test_tron_token_payout_preflight.py tests/test_healthz.py -q
```

Result: 109 passed.

```bash
PYTHONPATH=.venv/lib/python3.12/site-packages .venv/bin/python -m py_compile shkeeper/services/payout_contract.py shkeeper/services/payout_execution_auth.py shkeeper/services/payout_execution_service.py shkeeper/services/payout_rail_catalog.py shkeeper/services/payout_rail_sync.py shkeeper/services/payout_execution_reconciler.py shkeeper/services/payout_callback_outbox.py shkeeper/services/payout_sidecar_client.py shkeeper/api_v1.py shkeeper/models.py migrations/versions/20260603_payout_execution_foundation.py
git diff --check
```

Result: both clean.

```bash
PYTHONPATH=.venv/lib/python3.12/site-packages .venv/bin/flask --app 'shkeeper:create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:////private/tmp/shkeeper-payout-migration-smoke-20260604.sqlite", "SQLALCHEMY_TRACK_MODIFICATIONS": False, "SESSION_TYPE": "filesystem"})' db upgrade
```

Result: command completed successfully against an isolated temporary SQLite
database; existing database migration-before-`create_all` behavior is covered by
`tests/test_healthz.py::HealthzTestCase::test_existing_database_runs_migrations_before_create_all`.

Additional rail-sync hardening review on 2026-06-04:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_rail_sync tests.test_payout_execution_api tests.test_payout_execution_models tests.test_payout_execution_reconciler tests.test_payout_callback_outbox tests.test_payout_sidecar_client tests.test_payout_service_external_id tests.test_healthz -v
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m py_compile shkeeper/services/payout_rail_sync.py tests/test_payout_rail_sync.py
git diff --check
```

Results: focused payout execution suite passed 95 tests; full SHKeeper unittest
discovery passed 181 tests; `py_compile` and `git diff --check` were clean.

Additional SHKeeper product-policy boundary review on 2026-06-04:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_rail_sync tests.test_payout_execution_models tests.test_payout_execution_api -v
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v
jq empty docs/openapi-3.json
git diff --check
```

Results: SHKeeper runtime, OpenAPI, and migrations contain no client payout
amount-limit fields and no Grither-specific production names. `payout-rail-sync`
rejects unknown config fields with a strict routing/execution contract error.
The service-consumer `/api/v1/payout-executions` request also rejects unsupported
fields instead of silently accepting payload data outside the signed execution
contract. Focused boundary/API tests passed 60 tests; full SHKeeper unittest
discovery passed 215 tests; OpenAPI JSON and `git diff --check` were clean.

Revalidated after the 2026-06-04 architecture clarification: there is still no
SHKeeper-side client payout limit implementation to remove. The runtime boundary
is intentionally narrow: accept only execution fields, route through an enabled
rail, and record durable state/evidence. Product withdrawal limits remain
exclusively upstream in the consuming product ledger.

Additional Helm/SHKeeper rail-only integration review on 2026-06-04:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_sidecar_client -v
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v
PYTHONPYCACHEPREFIX=/private/tmp/shkeeper-pycache .venv/bin/python -m py_compile shkeeper/services/payout_sidecar_client.py tests/test_payout_sidecar_client.py
git diff --check
```

Results: `HttpPayoutSidecarClient` now routes by
`PayoutExecution.sidecar_service`, defaults bare Kubernetes service names to
port 6000, and treats legacy `Crypto.instances` as a compatibility fallback
only. Focused sidecar-client tests passed 12 tests; full SHKeeper unittest
discovery passed 184 tests; `py_compile` and `git diff --check` were clean.

Additional direct legacy adapter guard review on 2026-06-04:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_execution_api -v
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_callback_outbox tests.test_payout_execution_api tests.test_payout_execution_models tests.test_payout_execution_reconciler tests.test_payout_rail_sync tests.test_payout_sidecar_client tests.test_payout_status_response tests.test_payout_service_external_id tests.test_payout_tron_template tests.test_tron_token_payout_preflight tests.test_healthz -v
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v
PYTHONPYCACHEPREFIX=/private/tmp/shkeeper-pycache .venv/bin/python -m py_compile shkeeper/services/payout_rail_catalog.py shkeeper/services/payout_service.py shkeeper/models.py shkeeper/modules/classes/tron_token.py shkeeper/modules/classes/ethereum.py shkeeper/modules/classes/ton.py tests/test_payout_execution_api.py
git diff --check
```

Results: direct TRON `crypto.mkpayout` and `crypto.multipayout` are now blocked
for enabled client-withdrawal rails unless they run under the `PayoutService`
guard context. API-level regressions also verify that legacy admin `/payout`
and `/multipayout` remain available through the authenticated admin context.
The focused API test passed 31 tests; the focused payout suite passed 125
tests; full SHKeeper unittest discovery passed 191 tests; `py_compile` and
`git diff --check` were clean.

Additional service-auth boundary review on 2026-06-04:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_execution_api -v
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_callback_outbox tests.test_payout_execution_api tests.test_payout_execution_models tests.test_payout_execution_reconciler tests.test_payout_rail_sync tests.test_payout_sidecar_client tests.test_payout_status_response tests.test_payout_service_external_id tests.test_payout_tron_template tests.test_tron_token_payout_preflight tests.test_healthz -v
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v
PYTHONPYCACHEPREFIX=/private/tmp/shkeeper-pycache .venv/bin/python -m py_compile shkeeper/services/payout_execution_auth.py tests/test_payout_execution_api.py
git diff --check
```

Results: payout API auth regressions now verify missing signature 401, tampered
body 403, expired timestamp 403, replay nonce 403, and method/path/query-bound
signatures. Focused API tests passed 35 tests; focused payout suite passed 129
tests; full SHKeeper unittest discovery passed 195 tests; `py_compile` and
`git diff --check` were clean.

Additional SHKeeper model/import review on 2026-06-04:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_callback_outbox tests.test_payout_execution_api tests.test_payout_execution_models tests.test_payout_execution_reconciler tests.test_payout_metrics tests.test_payout_rail_sync tests.test_payout_sidecar_client tests.test_payout_service_external_id tests.test_payout_status_response tests.test_payout_tron_template tests.test_tron_token_payout_preflight tests.test_healthz -v
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v
git diff --check
```

Results: import-time review found that payout execution states had been placed
under the legacy `PayoutStatus` enum instead of a dedicated
`PayoutExecutionState` enum. The model now declares `PayoutExecutionState`
explicitly before `PayoutExecution`, while legacy `PayoutStatus` remains
`IN_PROGRESS`/`SUCCESS`/`FAIL`. Focused SHKeeper payout/core verification
passed 143 tests; full SHKeeper unittest discovery passed 209 tests;
`git diff --check` was clean.

Additional SHKeeper sidecar-status error-field review on 2026-06-04:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_execution_reconciler -v
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_execution_api tests.test_payout_execution_reconciler tests.test_payout_callback_outbox tests.test_payout_metrics tests.test_payout_sidecar_client tests.test_payout_rail_sync tests.test_payout_execution_models -v
PYTHONPYCACHEPREFIX=/private/tmp/shkeeper-pycache .venv/bin/python -m py_compile shkeeper/services/payout_execution_service.py tests/test_payout_execution_reconciler.py
git diff --check
```

Results: review found that a transient dispatcher/status error such as
`PAYOUT_DISPATCH_EXCEPTION` could remain on an execution after later successful
sidecar progress, leaking a stale error into status responses and callbacks.
`apply_sidecar_status()` now clears stale error fields for active/successful
sidecar states unless the current sidecar status explicitly provides new error
fields, while preserving error fields for terminal failure and reconciliation
states. Reconciler focused tests passed 23 tests; broader payout suite passed
111 tests; full SHKeeper unittest discovery passed 216 tests; `py_compile` and
`git diff --check` were clean.

Additional SHKeeper product-policy and callback HMAC freshness review on
2026-06-04:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_rail_sync tests.test_payout_execution_models tests.test_payout_execution_api tests.test_payout_callback_outbox -v
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_execution_api tests.test_payout_execution_reconciler tests.test_payout_callback_outbox tests.test_payout_metrics tests.test_payout_sidecar_client tests.test_payout_rail_sync tests.test_payout_execution_models -v
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v
jq empty docs/openapi-3.json
jq empty docs/openapi-grither-pay-payouts.json
git diff --check
```

Results: SHKeeper still has no customer withdrawal-limit implementation.
Runtime/API/rail sync now use generic strict execution/routing contract errors
for unsupported fields and do not name product policy fields. OpenAPI runtime and
Grither handoff specs were regenerated from `api_docs.py`. Callback outbox
retries keep the raw payload and `event_id` nonce stable, but refresh
`X-Payout-Timestamp` and `X-Payout-Signature` per delivery attempt so delayed
callbacks do not fail consumer timestamp skew checks. Focused tests passed 71
tests; broad payout suite passed 112 tests; full SHKeeper unittest discovery
passed 217 tests.

---

## Source Material

- Design spec:
  `docs/superpowers/specs/2026-06-03-usdt-withdrawals-production-readiness-design.md`
- Master plan:
  `docs/superpowers/plans/2026-06-03-usdt-withdrawals-production-readiness.md`
- Current legacy payout tests:
  `tests/test_payout_service_external_id.py`
  `tests/test_payout_status_response.py`
  `tests/test_webhook_hmac.py`

## File Structure

Create:

- `shkeeper/services/payout_contract.py`
  Canonical request models, 6-decimal USDT normalization, request hash, sidecar
  payload hash, status/callback payload builders.
- `shkeeper/services/payout_execution_service.py`
  Creation, idempotency, state transitions, sidecar submission dispatch, status
  projection, and sidecar status reconciliation.
- `shkeeper/services/payout_rail_catalog.py`
  Explicit `(consumer, asset, network)` rail lookup and legacy spend guards.
- `shkeeper/services/payout_execution_auth.py`
  Scoped service auth, HMAC verification, timestamp tolerance, replay nonce check,
  callback signing helpers.
- `shkeeper/services/payout_callback_outbox.py`
  Durable callback event creation and bounded dispatch loop.
- `shkeeper/services/payout_execution_reconciler.py`
  DB-backed recovery for stale `CREATED`, `PREFLIGHTED`, `ENQUEUEING`, and sidecar
  polling states.
- `tests/test_payout_contract.py`
- `tests/test_payout_execution_models.py`
- `tests/test_payout_execution_api.py`
- `tests/test_payout_execution_reconciler.py`
- `tests/test_payout_callback_outbox.py`
- `tests/test_payout_legacy_spend_guards.py`

Modify:

- `shkeeper/models.py`
  Add `PayoutExecution`, `PayoutRail`, `PayoutCallbackEvent`, and auth/replay
  storage if the existing models file remains the project pattern.
- `shkeeper/api_v1.py`
  Register new service-to-service endpoints without changing the legacy payout
  endpoints.
- `shkeeper/tasks.py`
  Register worker-callable helpers for callback dispatch and reconciliation.
- `shkeeper/__init__.py`
  Register the first production worker commands
  `flask payout-execution-reconciler`, `flask payout-callback-dispatcher`, and
  `flask payout-rail-sync`, and register a new blueprint or smorest blueprint if
  routes are split from `api_v1.py`.
- `migrations/versions/20260603_payout_execution_contract.py`
  Add additive schema only.

Do not modify:

- Deposit/invoice processing.
- `walletnotify` behavior.
- Legacy admin payout response contract, except for guards that stop legacy paths
  from spending execution-enabled payout wallets.

## Task 1: Canonical Contract And Hashing

**Files:**

- Create: `shkeeper/services/payout_contract.py`
- Test: `tests/test_payout_contract.py`

- [x] **Step 1: Write failing contract tests**

Create `tests/test_payout_contract.py` with these test names and assertions:

```python
import pytest

from shkeeper.services.payout_contract import (
    PayoutExecutionRequest,
    canonical_usdt_amount,
    request_hash,
    sidecar_payload_hash,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("25", "25.000000"), ("25.0", "25.000000"), ("25.000000", "25.000000")],
)
def test_canonical_usdt_amount_normalizes_equivalent_strings(raw, expected):
    assert canonical_usdt_amount(raw) == expected


def test_canonical_usdt_amount_rejects_more_than_six_decimals():
    with pytest.raises(ValueError, match="USDT amount supports at most 6 decimals"):
        canonical_usdt_amount("25.0000001")


def test_request_hash_includes_callback_endpoint_and_contract_version():
    first = PayoutExecutionRequest(
        consumer="wallet-client",
        external_id="wd-1",
        asset="USDT",
        network="TRON",
        amount="25",
        destination="TA",
        callback_endpoint_id="cb-prod",
        contract_version="usdt-payout-execution-v1",
    )
    second = first.with_changes(callback_endpoint_id="cb-staging")

    assert request_hash(first) != request_hash(second)
    assert request_hash(first) == request_hash(first.with_changes(amount="25.000000"))


def test_sidecar_payload_hash_excludes_callback_endpoint():
    first = PayoutExecutionRequest(
        consumer="wallet-client",
        external_id="wd-1",
        asset="USDT",
        network="TRON",
        amount="25",
        destination="TA",
        callback_endpoint_id="cb-prod",
        contract_version="usdt-payout-execution-v1",
    )
    second = first.with_changes(callback_endpoint_id="cb-staging")

    assert sidecar_payload_hash(first, execution_id=123) == sidecar_payload_hash(
        second,
        execution_id=123,
    )
```

- [x] **Step 2: Run the failing tests**

Run:

```bash
pytest tests/test_payout_contract.py -q
```

Expected: tests fail with `ModuleNotFoundError` for `shkeeper.services.payout_contract`.

- [x] **Step 3: Implement the contract module**

Create `shkeeper/services/payout_contract.py` with these public names:

```python
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation, ROUND_DOWN
import hashlib
import json


CONTRACT_VERSION = "usdt-payout-execution-v1"
USDT_SCALE = Decimal("0.000001")


def canonical_usdt_amount(raw_amount):
    try:
        value = Decimal(str(raw_amount))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("USDT amount must be a decimal string") from exc

    if value <= 0:
        raise ValueError("USDT amount must be positive")
    if value != value.quantize(USDT_SCALE, rounding=ROUND_DOWN):
        raise ValueError("USDT amount supports at most 6 decimals")
    return format(value.quantize(USDT_SCALE), "f")


@dataclass(frozen=True)
class PayoutExecutionRequest:
    consumer: str
    external_id: str
    asset: str
    network: str
    amount: str
    destination: str
    callback_endpoint_id: str
    contract_version: str = CONTRACT_VERSION

    def canonical_amount(self):
        return canonical_usdt_amount(self.amount)

    def with_changes(self, **changes):
        return replace(self, **changes)


def _stable_hash(payload):
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def request_hash(request):
    return _stable_hash(
        {
            "consumer": request.consumer,
            "external_id": request.external_id,
            "asset": request.asset,
            "network": request.network,
            "amount": request.canonical_amount(),
            "destination": request.destination,
            "callback_endpoint_id": request.callback_endpoint_id,
            "contract_version": request.contract_version,
        }
    )


def sidecar_payload_hash(request, execution_id):
    return _stable_hash(
        {
            "consumer": request.consumer,
            "execution_id": execution_id,
            "external_id": request.external_id,
            "asset": request.asset,
            "network": request.network,
            "amount": request.canonical_amount(),
            "destination": request.destination,
            "contract_version": request.contract_version,
        }
    )
```

- [x] **Step 4: Run contract tests**

Run:

```bash
pytest tests/test_payout_contract.py -q
```

Expected: all tests in `tests/test_payout_contract.py` pass.

- [ ] **Step 5: Commit the contract slice**

Run:

```bash
git add shkeeper/services/payout_contract.py tests/test_payout_contract.py
git commit -m "feat: add payout execution contract hashing"
```

## Task 2: Execution, Rail, And Callback Storage

**Files:**

- Modify: `shkeeper/models.py`
- Create: `migrations/versions/20260603_payout_execution_contract.py`
- Test: `tests/test_payout_execution_models.py`

- [x] **Step 1: Write failing model tests**

Create `tests/test_payout_execution_models.py` using the existing in-memory SQLite
pattern from `tests/test_payout_status_response.py`. Required test names:

- `test_payout_execution_unique_consumer_external_id`
- `test_payout_execution_allows_same_external_id_for_different_consumer`
- `test_payout_rail_catalog_unique_consumer_asset_network`
- `test_callback_event_unique_execution_event_version_and_transition_id`

Assertions:

- duplicate `(consumer, external_id)` on `PayoutExecution` raises
  `sqlalchemy.exc.IntegrityError`;
- same `external_id` with a different consumer is allowed;
- duplicate `(consumer, asset, network)` on `PayoutRail` raises `IntegrityError`;
- duplicate callback `event_id`, `(execution_id, event_version)`, or
  `state_transition_id` raises `IntegrityError`.

- [x] **Step 2: Run the failing model tests**

Run:

```bash
pytest tests/test_payout_execution_models.py -q
```

Expected: tests fail because `PayoutExecution`, `PayoutRail`, and
`PayoutCallbackEvent` do not exist.

- [x] **Step 3: Add additive models**

In `shkeeper/models.py`, add:

- `PayoutExecutionState` enum values: `CREATED`, `PREFLIGHTED`, `ENQUEUEING`,
  `ENQUEUED`, `BROADCAST`, `CONFIRMED`, `FAILED_PRE_BROADCAST`,
  `FAILED_CHAIN_TERMINAL`, `RECONCILIATION_REQUIRED`;
- `PayoutFailureClass` enum values: `VALIDATION`, `PREFLIGHT`, `WORKER_UNAVAILABLE`,
  `SIDECAR_TIMEOUT`, `CHAIN_TERMINAL`, `AMBIGUOUS`, `OPERATOR_RESOLVED`;
- `PayoutExecution` table with the fields from the design spec storage section;
- `PayoutRail` table with explicit `crypto_id`, `sidecar_service`,
  `sidecar_symbol`, `payout_queue`, token metadata, wallet policy, and
  enabled flags;
- `PayoutCallbackEvent` table with immutable payload fields, refreshed delivery
  signature metadata, and dispatch tracking.

`PayoutRail` must use generic client-withdrawal terminology. Required fields:

- `consumer`
- `asset`
- `network`
- `crypto_id`
- `sidecar_service`
- `sidecar_symbol`
- `payout_queue`
- `source_wallet_ref`: literal existing sidecar source-wallet reference, such as
  TRON `fee_deposit` or TON `fee_deposit`
- `hot_wallet_policy`: enum with `CURRENT_SIDECAR_SOURCE_WALLET`,
  `CURRENT_SIDECAR_SOURCE_WALLET_WITH_SHARED_GUARD`
- `execution_enabled`
- `legacy_spend_policy`: enum with `BLOCK_AUTOMATIC_BYPASS`,
  `ROUTE_AUTOMATIC_THROUGH_PAYOUT_EXECUTION`
- optional `wallet_guard_key` only when the sidecar already has a named
  lock/nonce/seqno/resource guard that must be shared by same-wallet spend paths

Do not add per-rail min/max amount or daily limit fields to SHKeeper.
Per-withdrawal, daily, tier, wallet, and compliance limits are upstream product
ledger policy and must be enforced before a client calls the SHKeeper execution
API.

Do not build a generic wallet registry in Phase 1. `source_wallet_ref` records
the current sidecar source wallet exactly as the sidecar uses it, and
`wallet_guard_key` is only a pointer to an existing or locally implemented
per-wallet guard, not a new inventory system.

Phase 1 source-wallet policy:

- TRON `USDT` uses the existing `tron-shkeeper` `fee_deposit` key, matching the
  current sidecar `/USDT/payout/<to>/<amount>` transfer source.
- TON `TON-USDT` uses the existing `ton-shkeeper` `fee_deposit` account, matching
  the current sidecar `/TON-USDT/payout/<to>/<amount>` transfer source.
- ETH `ETH-USDT` stays disabled until the owned fork is checked out and its
  current `/ETH-USDT/payout/<to>/<amount>` source wallet, nonce storage, and
  broadcast evidence can be proven from code.
- Do not rename `fee_deposit`, do not create a dedicated payout wallet migration,
  and do not remove manual admin payouts in this release.
- The new SHKeeper execution API is the client-withdrawal API boundary. It may
  wrap the current sidecar payout transfer primitive, but legacy `/payout` and
  `/multipayout` must not become an automatic Grither Pay client-withdrawal
  bypass.

Do not add service-specific rail fields or lookup helpers. Grither Pay is only
one configured `consumer` value.

Add a generic `payout-rail-sync` command that upserts `PayoutRail` rows from
`PAYOUT_RAILS_JSON`. Helm uses this command to make rail catalog configuration
chart-owned instead of requiring manual SQL/runbook creation.
When `PAYOUT_RAILS_JSON` is an object with a top-level `consumer`, sync is
desired-state for that consumer: rails present in the payload are upserted and
rails absent from the payload are kept for history/audit but have
`execution_enabled=false`. This prevents a removed Helm rail from
remaining active only because an older release inserted it into the DB.

The sync command must be atomic and fail closed: invalid booleans, enabled rails
without callback endpoints, non-USDT decimals, duplicate desired rails, and
partially invalid rail batches must raise `PayoutRailSyncError` and roll back
the whole batch. SHKeeper must not store or apply product amount policy fields;
unknown rail config fields must be rejected so unsupported controls stay
upstream in the consumer product.

Use SQLAlchemy unique constraints:

```python
db.UniqueConstraint("consumer", "external_id", name="uq_payout_execution_consumer_external_id")
db.UniqueConstraint("consumer", "asset", "network", name="uq_payout_rail_consumer_asset_network")
db.UniqueConstraint("event_id", name="uq_payout_callback_event_id")
db.UniqueConstraint("execution_id", "event_version", name="uq_payout_callback_execution_event_version")
db.UniqueConstraint("state_transition_id", name="uq_payout_callback_state_transition_id")
```

- [x] **Step 4: Add migration**

Create `migrations/versions/20260603_payout_execution_contract.py` with additive
`create_table` operations for `payout_execution`, `payout_rail`, and
`payout_callback_event`. The migration must not alter existing `payout`,
`invoice`, `transaction`, or `wallet` tables except for indexes required by the
new foreign keys.

- [x] **Step 5: Run model tests**

Run:

```bash
pytest tests/test_payout_execution_models.py -q
```

Expected: all model tests pass.

- [ ] **Step 6: Commit the storage slice**

Run:

```bash
git add shkeeper/models.py migrations/versions/20260603_payout_execution_contract.py tests/test_payout_execution_models.py
git commit -m "feat: add payout execution storage"
```

## Task 3: Rail Catalog And Routing

**Files:**

- Create: `shkeeper/services/payout_rail_catalog.py`
- Test: `tests/test_payout_rail_catalog.py`

- [x] **Step 1: Write failing catalog tests**

Create tests for:

- TRON `("wallet-client", "USDT", "TRON")` maps to `crypto_id="USDT"`,
  `sidecar_service="tron-shkeeper"`, `sidecar_symbol="USDT"`,
  `payout_queue="tron_usdt_fee_payouts"`;
- TON maps to `crypto_id="TON-USDT"`, `sidecar_service="ton-shkeeper"`,
  `sidecar_symbol="TON-USDT"`, `payout_queue="ton_usdt_payouts"`;
- ETH maps to `crypto_id="ETH-USDT"`, `sidecar_service="ethereum-shkeeper"`,
  `sidecar_symbol="ETH-USDT"`, `payout_queue="eth_usdt_payouts"`;
- disabled rails are rejected before `PayoutExecution` creation;
- SHKeeper does not own product amount policy; catalog tests must prove routing,
  enablement, and fail-closed rejection of unknown fields only.

- [x] **Step 2: Run the failing catalog tests**

Run:

```bash
pytest tests/test_payout_rail_catalog.py -q
```

Expected: tests fail because `payout_rail_catalog.py` does not exist.

- [x] **Step 3: Implement catalog lookup**

Implement `PayoutRailCatalog` with:

- `resolve(consumer, asset, network)`;
- `RailDisabledError`;
- `RailUnsupportedError`;
- explicit DB-backed lookup from `PayoutRail`;
- no routing by string concatenation.

- [x] **Step 4: Run catalog tests**

Run:

```bash
pytest tests/test_payout_rail_catalog.py -q
```

Expected: all catalog tests pass.

- [ ] **Step 5: Commit the catalog slice**

Run:

```bash
git add shkeeper/services/payout_rail_catalog.py tests/test_payout_rail_catalog.py
git commit -m "feat: add payout rail catalog"
```

## Task 4: Service Auth And Callback Signing

**Files:**

- Create: `shkeeper/services/payout_execution_auth.py`
- Modify: `shkeeper/models.py`
- Test: `tests/test_payout_execution_auth.py`

- [x] **Step 1: Write failing auth tests**

Required tests:

- valid `X-Payout-Consumer`, `X-Payout-Key-Id`, `X-Payout-Timestamp`,
  `X-Payout-Nonce`, and `X-Payout-Signature` authenticates the consumer;
- missing signature returns 401;
- tampered body returns 403;
- signature for `POST /api/v1/payout-executions` fails when replayed against
  `GET /api/v1/payout-executions/{external_id}`;
- changed canonical path or query string fails verification;
- timestamp outside tolerance returns 403;
- repeated nonce for the same key ID returns 403;
- callback signing produces stable headers over the stored payload bytes.

- [x] **Step 2: Run failing auth tests**

Run:

```bash
pytest tests/test_payout_execution_auth.py -q
```

Expected: tests fail because `payout_execution_auth.py` does not exist.

- [x] **Step 3: Implement HMAC auth**

Implement:

- `verify_payout_request(headers, method, canonical_path, canonical_query, body_bytes)`;
- `sign_callback_payload(key_id, secret, method, canonical_path, canonical_query, body_bytes, timestamp, nonce)`;
- timestamp tolerance from app config with a default of 300 seconds;
- replay storage using a table or existing cache-backed storage with durable test
  coverage for process restart behavior.

Signature base:

```text
<timestamp>\n<nonce>\n<method>\n<canonical_path>\n<canonical_query>\n<body_sha256>
```

Use the same canonical signature base for consumer submit/status requests and
SHKeeper-to-consumer callbacks. A callback signature must be bound to the
callback method and path, not only to the payload bytes.

- [x] **Step 4: Run auth tests**

Run:

```bash
pytest tests/test_payout_execution_auth.py -q
```

Expected: all auth tests pass.

- [ ] **Step 5: Commit the auth slice**

Run:

```bash
git add shkeeper/services/payout_execution_auth.py shkeeper/models.py tests/test_payout_execution_auth.py
git commit -m "feat: add payout execution service auth"
```

## Task 5: Submit And Status API

**Files:**

- Modify: `shkeeper/api_v1.py`
- Create: `shkeeper/services/payout_execution_service.py`
- Test: `tests/test_payout_execution_api.py`

- [x] **Step 1: Write failing API tests**

Create tests with Flask test client for:

- `POST /api/v1/payout-executions` creates `PayoutExecution` before sidecar submit;
- equivalent amounts `25`, `25.0`, `25.000000` return the same execution;
- `25.0000001` returns 400;
- duplicate same payload returns the existing execution;
- duplicate changed payload returns `409 IDEMPOTENCY_CONFLICT`;
- disabled/unsupported rails return 400 before execution creation;
- SHKeeper accepts technically valid positive 6-decimal USDT amounts without
  applying consumer product policy;
- arbitrary request callback URL is ignored or rejected unless allowlisted;
- `GET /api/v1/payout-executions/{external_id}` returns `consumer`,
  `execution_id`, nullable `sidecar_execution_id`, `contract_version`,
  `event_version`, `state_transition_id`, timestamps, canonical amount,
  destination, request hashes, failure class, txids/message hashes, sidecar
  evidence, error fields, and reconciliation flag;
- missed callback followed by status polling has enough ordering metadata for
  any API consumer to apply state monotonically.

- [x] **Step 2: Run failing API tests**

Run:

```bash
pytest tests/test_payout_execution_api.py -q
```

Expected: tests fail because routes and service do not exist.

- [x] **Step 3: Implement service creation path**

Implement `PayoutExecutionService.submit()`:

- authenticate consumer before processing;
- resolve rail before execution creation;
- canonicalize amount and compute hashes;
- create `PayoutExecution` with `CREATED`;
- return durable `CREATED` status from the API without requiring synchronous
  sidecar preflight or submit inside the HTTP request;
- leave sidecar preflight, `PREFLIGHTED`, `ENQUEUEING`, `ENQUEUED`, timeout, and
  recovery transitions to the DB-backed payout execution worker/reconciler in
  Task 6;
- duplicate `(consumer, external_id)` with same hash returns existing status;
- duplicate with different hash returns conflict.

- [x] **Step 4: Implement routes**

Add routes without changing the legacy `/<crypto>/payout` endpoints:

```text
POST /api/v1/payout-executions
GET  /api/v1/payout-executions/{external_id}
```

Return normalized states and never emit legacy generic `FAIL` for client payouts.

- [x] **Step 5: Run API tests**

Run:

```bash
pytest tests/test_payout_execution_api.py -q
```

Expected: all API tests pass.

- [x] **Step 6: Run legacy payout compatibility tests**

Run:

```bash
pytest tests/test_payout_service_external_id.py tests/test_payout_status_response.py -q
```

Expected: existing legacy payout behavior remains passing.

- [ ] **Step 7: Commit the API slice**

Run:

```bash
git add shkeeper/api_v1.py shkeeper/services/payout_execution_service.py tests/test_payout_execution_api.py
git commit -m "feat: add payout execution API"
```

## Task 6: Dispatcher And Reconciler Recovery

**Files:**

- Create: `shkeeper/services/payout_execution_reconciler.py`
- Modify: `shkeeper/services/payout_execution_service.py`
- Modify: `shkeeper/tasks.py`
- Test: `tests/test_payout_execution_reconciler.py`

- [x] **Step 1: Write failing recovery tests**

Required tests:

- crash after `PayoutExecution` creation but before sidecar submit is recovered by
  a DB-backed dispatcher/reconciler;
- stale `CREATED` and `PREFLIGHTED` are retried safely;
- stale `ENQUEUEING` first performs authenticated sidecar status lookup by
  SHKeeper `execution_id`;
- stale `ENQUEUEING` retries submit only when sidecar status returns a durable,
  authenticated `NOT_FOUND`/`NO_EXECUTION_CREATED` result from the v1 idempotent
  sidecar API;
- stale `ENQUEUEING` moves to `RECONCILIATION_REQUIRED` when sidecar status is
  unavailable, conflicting, ambiguous, or cannot prove non-existence;
- sidecar timeout after accepted work moves to `RECONCILIATION_REQUIRED`;
- status lookup after submit timeout returns authoritative state or
  `RECONCILIATION_REQUIRED`, never a new execution;
- stale sidecar status cannot overwrite newer SHKeeper state;
- same-version conflicting sidecar data moves execution to
  `RECONCILIATION_REQUIRED`.

- [x] **Step 2: Run failing recovery tests**

Run:

```bash
pytest tests/test_payout_execution_reconciler.py -q
```

Expected: tests fail because reconciler does not exist.

- [x] **Step 3: Implement DB-backed reconciliation**

Implement a reconciler that:

- leases non-terminal executions with compare-and-set state/version checks;
- retries only safe pre-sidecar-submit states;
- calls sidecar status by SHKeeper `execution_id`;
- never treats missing SHKeeper-local sidecar evidence as proof that sidecar work
  was not accepted;
- applies sidecar status monotonically by sidecar `state_version` and
  `state_transition_id`;
- requires and stores sidecar `state_updated_at` as part of the sidecar ordering
  evidence, and treats same-version timestamp drift as ambiguous;
- rejects missing or mismatched sidecar identity/evidence fields
  (`consumer`, `execution_id`, `external_id`, `contract_version`, `asset`,
  `network`, `request_hash`, `sidecar_payload_hash`) by moving the execution to
  `RECONCILIATION_REQUIRED`;
- stores an allowlisted sidecar evidence snapshot plus `sidecar_status_hash` and
  `sidecar_status_observed_at`; unsafe fields such as private keys must not be
  persisted or exposed through status/callback payloads;
- moves ambiguous same-version conflicts to `RECONCILIATION_REQUIRED`;
- emits a `PayoutCallbackEvent` for every SHKeeper state transition.

- [x] **Step 4: Register worker entrypoint**

In `shkeeper/tasks.py`, add an existing worker-compatible reconciliation
function, and register a production worker command:

```bash
flask payout-execution-reconciler
```

Helm must run that command in a dedicated worker container when payout execution
is enabled. APScheduler in the web process must not be the production withdrawal
reconciler.

- [x] **Step 5: Run recovery tests**

Run:

```bash
pytest tests/test_payout_execution_reconciler.py -q
```

Expected: all recovery tests pass.

2026-06-04 follow-up verification: sidecar submit/status identity mismatch and
missing `sidecar_payload_hash` now fail closed with
`SIDECAR_STATUS_IDENTITY_MISMATCH`; sidecar `state_updated_at` is now required,
stored, exposed as `sidecar_state_updated_at`, and same-version timestamp drift
is ambiguous. Focused payout suite:
`.venv/bin/python -m unittest tests.test_payout_callback_outbox tests.test_payout_execution_api tests.test_payout_execution_models tests.test_payout_execution_reconciler tests.test_payout_metrics tests.test_payout_rail_sync tests.test_payout_sidecar_client tests.test_payout_service_external_id tests.test_healthz -v`
-> `Ran 120 tests in 2.349s OK`.

2026-06-04 follow-up implementation: SHKeeper now persists only an allowlisted
sidecar evidence snapshot and hash. The snapshot covers technical execution
evidence such as source wallet, token/jetton metadata, nonce/seqno, signed
payload hash/ref, broadcast provider, txids/message hashes, and chain-check
timestamps. It explicitly does not persist arbitrary sidecar fields. Same-version
sidecar evidence drift moves the execution to `RECONCILIATION_REQUIRED`.
Status/callback schemas expose `sidecar_status_hash`,
`sidecar_status_observed_at`, and `sidecar_evidence`; status always shows the
latest evidence, while callbacks remain transition events and are not re-emitted
for same-SHKeeper-state sidecar progress.

Validation: `.venv/bin/python -m unittest tests.test_payout_callback_outbox
tests.test_payout_execution_api tests.test_payout_execution_models
tests.test_payout_execution_reconciler tests.test_payout_metrics
tests.test_payout_rail_sync tests.test_payout_sidecar_client
tests.test_payout_service_external_id tests.test_healthz -v` -> `Ran 123 tests
in 2.875s OK`. `git diff --check`, runtime payout amount/day cap search, and
runtime/OpenAPI Grither-specific payout identifier search passed.

Product-policy invariant: `PayoutRail`/`PayoutExecution` do not contain product
policy columns. SHKeeper validates only execution invariants: auth, rail
enablement, idempotency, supported asset/network, positive canonical USDT
precision, destination, sidecar routing, callback endpoint, ordering, and audit
state. Customer withdrawal policy stays in the upstream consumer product.

- [ ] **Step 6: Commit the reconciler slice**

Run:

```bash
git add shkeeper/services/payout_execution_reconciler.py shkeeper/services/payout_execution_service.py shkeeper/tasks.py tests/test_payout_execution_reconciler.py
git commit -m "feat: recover payout execution dispatch"
```

## Task 7: Durable Callback Outbox

**Files:**

- Create: `shkeeper/services/payout_callback_outbox.py`
- Modify: `shkeeper/services/payout_execution_service.py`
- Modify: `shkeeper/tasks.py`
- Test: `tests/test_payout_callback_outbox.py`

- [x] **Step 1: Write failing callback tests**

Required tests:

- callback event has unique `event_id`;
- duplicate `(execution_id, event_version)` is rejected;
- duplicate `state_transition_id` is rejected;
- state transition and `PayoutCallbackEvent` creation commit in one DB
  transaction;
- rollback after callback event insert rolls back the state transition too;
- no committed state transition exists without a callback event, unless a tested
  reconciler path deterministically backfills the missing event;
- callback payload includes nullable `sidecar_execution_id`;
- payload includes previous state, current state, `event_version`,
  `state_transition_id`, `occurred_at`, rail, amount, destination, hashes,
  failure class, txids/message hashes, error fields, and reconciliation flag;
- retries resend the stored payload while refreshing timestamp/signature metadata
  per delivery attempt;
- bounded retries update attempt count, next attempt, and last error.

- [x] **Step 2: Run failing callback tests**

Run:

```bash
pytest tests/test_payout_callback_outbox.py -q
```

Expected: tests fail because callback outbox service does not exist.

- [x] **Step 3: Implement outbox service**

Implement:

- a single transactional transition function that increments `state_version`,
  writes `last_state_transition_id`, updates execution state, and inserts the
  `PayoutCallbackEvent` atomically;
- `create_event_for_transition(execution, previous_state, current_state)` only as
  an internal helper called by that transition function;
- immutable payload JSON stored at creation time;
- delivery signature metadata stored for audit and refreshed for each dispatch
  attempt;
- `dispatch_due_events(limit)` with bounded retry policy and observable failure
  fields;
- dispatcher uses the stored payload bytes for every retry.
- register `flask payout-callback-dispatcher` as the first production callback
  outbox worker command. Helm must run it in a dedicated worker container when
  payout execution callbacks are enabled.

- [x] **Step 4: Run callback tests**

Run:

```bash
pytest tests/test_payout_callback_outbox.py -q
```

Expected: all callback tests pass.

- [ ] **Step 5: Commit the callback slice**

Run:

```bash
git add shkeeper/services/payout_callback_outbox.py shkeeper/services/payout_execution_service.py shkeeper/tasks.py tests/test_payout_callback_outbox.py
git commit -m "feat: add payout callback outbox"
```

## Task 8: Legacy Spend Guards

**Files:**

- Modify: `shkeeper/models.py`
- Modify: `shkeeper/services/payout_service.py`
- Test: `tests/test_payout_execution_api.py`

- [x] **Step 1: Write failing guard tests**

Required tests:

- automatic/service client-withdrawal traffic cannot use legacy payout,
  `/multipayout`, `PayoutService.multiple_payout`, legacy autopayout, direct
  `crypto.mkpayout`, or direct `crypto.multipayout` to bypass
  `PayoutExecution`;
- manual/admin payout still works for the current `fee_deposit` source wallet;
- same-wallet manual/admin spend requires explicit operator context, audit
  metadata, and the same wallet lock/nonce/seqno/resource guard;
- legacy admin payout still works for wallets not assigned to a
  execution-enabled payout rail.

- [x] **Step 2: Run failing guard tests**

Run the guard-focused tests in `tests/test_payout_execution_api.py`.

- [x] **Step 3: Implement guard check**

Add a single guard function used by legacy payout, multipayout, autopayout, and
direct `crypto.mkpayout`/`crypto.multipayout` paths:

```python
def assert_legacy_spend_allowed(
    crypto_id,
    source_wallet_ref=None,
    *,
    spend_origin,
    operator_id=None,
    audit_reason=None,
):
    rail = PayoutRail.find_enabled_execution_rail_for_spend_source(
        crypto_id=crypto_id,
        source_wallet_ref=source_wallet_ref,
    )
    if rail is None:
        return
    if spend_origin == "manual_admin":
        assert_operator_audit_context(operator_id, audit_reason)
        acquire_or_verify_wallet_guard(rail, source_wallet_ref)
        return
    if rail.legacy_spend_policy == "ROUTE_AUTOMATIC_THROUGH_PAYOUT_EXECUTION":
        raise RouteThroughPayoutExecution(rail)
    raise PayoutRequestError(
        "Automatic legacy payout is blocked for execution-enabled rail "
        f"{rail.consumer}:{rail.asset}:{rail.network}"
    )
```

Rules:

- if a `PayoutRail` has `execution_enabled=true`, automatic/service
  legacy single payout, multipayout, autopayout, and direct crypto spend are
  rejected or routed through `PayoutExecution`;
- manual/admin payout is allowed only with explicit operator context, audit
  metadata, and the same wallet guard when it spends from the current
  client-withdrawal source wallet;
- rejection error message names the rail and required operator action.

Implementation note: direct adapter protection is enforced through a scoped
`legacy_spend_guard_context`. `PayoutService` establishes the context after
validating operator/audit metadata; TRON/ETH/TON adapter payout methods fail
closed without that context when a client-withdrawal rail uses the same
`crypto_id`.

- [x] **Step 4: Run guard and legacy tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_payout_execution_api tests.test_payout_service_external_id -v
```

Expected: new guard tests and legacy payout tests pass.

- [ ] **Step 5: Commit the guard slice**

Run:

```bash
git add shkeeper/models.py shkeeper/services/payout_rail_catalog.py shkeeper/services/payout_service.py shkeeper/modules/classes/tron_token.py shkeeper/modules/classes/ethereum.py shkeeper/modules/classes/ton.py tests/test_payout_execution_api.py
git commit -m "feat: guard payout hot wallets from legacy spend"
```

## Task 9: SHKeeper Verification And Review Gate

**Files:**

- Review: all files changed by Tasks 1-8.

- [x] **Step 1: Run focused payout suite**

Run:

```bash
pytest \
  tests/test_payout_contract.py \
  tests/test_payout_execution_models.py \
  tests/test_payout_rail_catalog.py \
  tests/test_payout_execution_auth.py \
  tests/test_payout_execution_api.py \
  tests/test_payout_execution_reconciler.py \
  tests/test_payout_callback_outbox.py \
  tests/test_payout_legacy_spend_guards.py \
  tests/test_payout_service_external_id.py \
  tests/test_payout_status_response.py \
  tests/test_webhook_hmac.py -q
```

Expected: all selected tests pass.

- [x] **Step 2: Run migration smoke**

Run:

```bash
FLASK_APP=shkeeper:create_app flask db upgrade
```

Expected: migration applies without altering legacy payout rows.

Current evidence: isolated CLI smoke completed successfully against
`/private/tmp/shkeeper-payout-migration-smoke-20260604.sqlite`.
`tests/test_healthz.py::HealthzTestCase::test_existing_database_runs_migrations_before_create_all`
passes and proves existing DB startup runs Alembic before `create_all`.

- [x] **Step 3: Request independent review**

Use `Superpowers:requesting-code-review` with:

- Description: SHKeeper service-to-service payout execution API for client USDT
  withdrawals.
- Requirements: this task plan and the source design spec.
- Focus: idempotency, failure classes, callback/status ordering, sidecar timeout
  recovery, legacy spend guards, and compatibility with legacy admin payout.

- [x] **Step 4: Apply review feedback through receiving-code-review**

For every Critical or Important finding:

- restate the technical issue;
- validate it against code/spec;
- fix one issue at a time;
- run the focused test that proves the fix;
- rerun Step 1 before moving to sidecar plans.

- [ ] **Step 5: Commit review fixes**

Run:

```bash
git status --short
git add shkeeper tests migrations
git commit -m "fix: harden payout execution API review findings"
```

## Acceptance Gate

SHKeeper is ready for sidecar implementation only when:

- `POST /api/v1/payout-executions` and
  `GET /api/v1/payout-executions/{external_id}` pass all API tests;
- status payload exposes callback-critical fields and ordering metadata;
- duplicate submit behavior is idempotent by `(consumer, external_id)`;
- generic `FAILED` is not emitted for client payouts;
- sidecar timeout after `ENQUEUEING` cannot create a second execution;
- callback outbox stores immutable signed payloads;
- reconciler can recover pre-submit crashes;
- automatic/service legacy spend cannot bypass `PayoutExecution`, while
  manual/admin payout remains available with operator audit context and the same
  wallet guard;
- independent review has no unresolved Critical or Important findings.
