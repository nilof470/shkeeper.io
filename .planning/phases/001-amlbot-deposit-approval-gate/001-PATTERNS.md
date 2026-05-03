# Phase 001: amlbot-deposit-approval-gate - Patterns

**Created:** 2026-05-03
**Status:** Complete

## Closest Existing Patterns

### SHKeeper model and migration changes

- Existing model location: `shkeeper/models.py`
- Existing migration location: `migrations/versions/*.py`
- Pattern: add SQLAlchemy model fields in `models.py`, then add Alembic migration under `migrations/versions/`.
- Required variation: add `AmlCheck` as a new table instead of adding AML lifecycle columns to `Transaction`.

### SHKeeper services

- Existing service directory: `shkeeper/services/`
- Existing examples: `payout_service.py`, `balance_service.py`, `crypto_cache.py`, `cache_service.py`
- Pattern: keep cross-cutting orchestration outside route handlers and models.
- Required new services:
  - `shkeeper/services/aml_coverage.py`
  - `shkeeper/services/aml_shkeeper_client.py`
  - `shkeeper/services/aml_policy.py`
  - `shkeeper/services/aml_processing.py`

### SHKeeper callback retry

- Existing callback sender: `shkeeper/callback.py:68`
- Existing retry selector: `Transaction.query.filter_by(callback_confirmed=False, need_more_confirmations=False)` in `send_callbacks`.
- Pattern: only set `callback_confirmed=True` after merchant returns HTTP 202.
- Required variation: non-outgoing confirmed transactions must also have terminal AML before `send_notification(tx)` posts.

### SHKeeper scheduler

- Existing scheduler job: `shkeeper/tasks.py:9`
- Pattern: every 60 seconds, run confirmation/callback maintenance inside `scheduler.app.app_context()`.
- Required variation: run AML processing between confirmation update and callback send.

### aml-shkeeper API and worker

- Existing API registration: `/Users/test/PycharmProjects/aml-shkeeper/app/api/__init__.py`
- Existing handlers: `/Users/test/PycharmProjects/aml-shkeeper/app/api/views.py`
- Existing Celery tasks: `/Users/test/PycharmProjects/aml-shkeeper/app/tasks.py`
- Pattern: API creates a DB row and Celery performs provider check/recheck.
- Required variation: new `/api/v1/checks` API is not symbol-prefixed and must be idempotent by `deposit_id`/`idempotency_key`.

## File Ownership Plan

| Plan | Write scope | Notes |
|------|-------------|-------|
| `001-01` | `/Users/test/PycharmProjects/aml-shkeeper` | External sidecar repo. Requires write permission outside current repo when executing. |
| `001-02` | `shkeeper/models.py`, `shkeeper/services/*`, `shkeeper/__init__.py`, `migrations/versions/*` | SHKeeper AML foundation. |
| `001-03` | `shkeeper/api_v1.py`, `shkeeper/callback.py`, `shkeeper/tasks.py`, `shkeeper/services/aml_processing.py` | Callback gate and lifecycle wiring. |
| `001-04` | `tests/*`, `/Users/test/PycharmProjects/aml-shkeeper/tests/*`, `docs/amlbot_deposit_gate.md` | Verification and docs. |

## Parallelism

- Wave 1: `001-01` and `001-02` can be implemented in parallel because they write separate repositories/scopes and share only the JSON contract agreed in CONTEXT.
- Wave 2: `001-03` depends on both sidecar contract and SHKeeper AML foundation.
- Wave 3: `001-04` depends on all implementation plans.

