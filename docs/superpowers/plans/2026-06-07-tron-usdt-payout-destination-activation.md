# TRON USDT Payout Destination Activation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-activate unactivated TRON USDT payout destinations through ProfeeX for the payout-execution API only, while keeping quote/preflight read-only and keeping transient provider failures retryable.

**Architecture:** The TRON sidecar owns TRON-specific activation and resource readiness. Payout-execution preflight becomes submit-eligible for `DESTINATION_NOT_ACTIVATED` under a feature flag, while legacy `/payout` and `/multipayout` keep blocking behavior. Destination activation uses ProfeeX plus Redis-backed destination idempotency; SHKeeper core and Grither receive narrow guards so pre-broadcast transient diagnostics do not become reconciliation incidents.

**Tech Stack:** Python Flask sidecar, Celery, Redis locks/JSON keys, tronpy, ProfeeX HTTP API, Flask-SQLAlchemy SHKeeper core, Java Spring Boot Grither Pay, JUnit/AssertJ, Python `unittest`/pytest-compatible tests, Helm values.

---

## Scope

This plan implements the approved spec:

- `docs/superpowers/specs/2026-06-07-tron-usdt-payout-destination-activation-design.md`

Code changes span four worktrees:

- TRON sidecar: `/Users/test/PycharmProjects/tron-shkeeper`
- SHKeeper core: `/Users/test/PycharmProjects/shkeeper.io`
- Grither Pay: `/Users/test/IdeaProjects/grither-pay`
- Helm chart: `/Users/test/PycharmProjects/shkeeper-helm-charts`

Commit after each task in the repo that changed. If one task changes multiple repos, commit separately in each repo with the same task number in the message.

## File Structure

TRON sidecar:

- Modify `app/config.py`: add auto-activation flag and Redis activation lock/record settings.
- Reference `app/profeex.py`: existing `ProfeeXConfig` already owns `currency`, `api_base_url`, polling timeout, and API key.
- Modify `app/resource_providers/profeex.py`: add activation API methods and activation-specific status polling.
- Create `app/payout_destination_activation.py`: destination-scoped Redis lock/record orchestration around ProfeeX activation.
- Modify `app/payout_resources.py`: add `allow_destination_activation` opt-in and invoke activation only for payout-execution callers.
- Modify `app/payout_status.py`: make payout-execution preflight submit-eligible for `DESTINATION_NOT_ACTIVATED` under the feature flag.
- Modify `app/payout_execution.py`: classify activation transient errors as retryable pre-broadcast and clear resource reservation before re-enqueue.
- Keep `app/tasks.py` legacy calls unchanged; `PayoutExecutionStore.execute()` is the payout-execution-only opt-in boundary.
- Test `tests/test_payout_resources.py`: quote and helper activation behavior.
- Test `tests/test_payout_status_confirmation.py`: preflight eligibility under flag.
- Test `tests/test_payout_execution_boundaries.py`: retryable activation error returns to re-enqueueable state.
- Test `tests/test_payout_task_resource_provisioning.py`: legacy `/payout` and `/multipayout` remain unchanged.
- Create `tests/test_profeex_activation_provider.py`: ProfeeX activation HTTP handling.
- Create `tests/test_payout_destination_activation.py`: Redis/idempotency orchestration.
- Modify `tests/test_resource_provider_config.py`: config validation.

SHKeeper core:

- Modify `shkeeper/services/payout_sidecar_client.py`: preserve structured sidecar error payload/status on `SidecarStatusUnavailable`.
- Modify `shkeeper/services/payout_execution_reconciler.py`: keep preflight transient diagnostics retryable and backoff safely.
- Test `tests/test_payout_sidecar_client.py`: structured 5xx payload is carried.
- Test `tests/test_payout_execution_reconciler.py`: preflight transient does not create ambiguity and structured diagnostics are preserved.

Grither Pay:

- Modify `apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutStateTransactionService.java`: add narrow same-version transient diagnostic predicate.
- Modify `docs/grither-pay-payout-integration.md`: document the same-version transient diagnostic exception.
- Test `apps/backend/src/test/java/com/grither/pay/providers/shkeeper/web/ShKeeperPayoutWebhookControllerTest.java`: transient same-version diagnostic stays non-reconciliation; real same-version conflicts still reconcile.

Helm:

- Modify `/Users/test/PycharmProjects/shkeeper-helm-charts/charts/shkeeper/values.yaml`: add `tron_shkeeper.payoutDestinationActivation` defaults.
- Modify `/Users/test/PycharmProjects/shkeeper-helm-charts/charts/shkeeper/templates/deployments/tron-shkeeper.yaml`: add destination activation env ownership and rendered env vars.
- Modify `/Users/test/PycharmProjects/shkeeper-helm-charts/charts/shkeeper/templates/deployments/tron-usdt-payout-worker.yaml`: add the same destination activation env ownership and rendered env vars.
- Modify `/Users/test/PycharmProjects/shkeeper-helm-charts/tests/test_shkeeper_fork_chart.py`: assert env ownership and rendered defaults.

## Task 1: TRON Config and ProfeeX Activation Client

**Files:**
- Modify: `/Users/test/PycharmProjects/tron-shkeeper/app/config.py`
- Modify: `/Users/test/PycharmProjects/tron-shkeeper/app/resource_providers/profeex.py`
- Test: `/Users/test/PycharmProjects/tron-shkeeper/tests/test_resource_provider_config.py`
- Test: `/Users/test/PycharmProjects/tron-shkeeper/tests/test_profeex_activation_provider.py`

- [ ] **Step 1: Write config validation tests**

Add tests to `tests/test_resource_provider_config.py`:

```python
def test_auto_activate_destination_requires_resource_provisioning_enabled(self):
    with self.assertRaisesRegex(
        ValueError,
        "TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED must be true",
    ):
        Settings(
            TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION=True,
            PROFEEX='{"api_key":"secret"}',
        )


def test_auto_activate_destination_requires_profeex_config(self):
    with self.assertRaisesRegex(
        ValueError,
        "PROFEEX must be configured when TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION=true",
    ):
        Settings(
            TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED=True,
            TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION=True,
        )


def test_auto_activate_destination_config_defaults(self):
    settings = Settings(
        TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED=True,
        TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION=True,
        PROFEEX='{"api_key":"secret"}',
    )

    self.assertTrue(settings.TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION)
    self.assertEqual(
        settings.TRON_USDT_DESTINATION_ACTIVATION_LOCK_TTL_SEC,
        300,
    )
    self.assertEqual(
        settings.TRON_USDT_DESTINATION_ACTIVATION_LOCK_WAIT_SEC,
        60,
    )
    self.assertEqual(
        settings.TRON_USDT_DESTINATION_ACTIVATION_RECORD_TTL_SEC,
        86400,
    )
```

- [ ] **Step 2: Run config tests and verify they fail**

Run:

```bash
cd /Users/test/PycharmProjects/tron-shkeeper
pytest tests/test_resource_provider_config.py -q
```

Expected: fails because `TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION` and activation TTL fields do not exist.

- [ ] **Step 3: Implement config fields and validation**

In `app/config.py`, add fields near the payout resource settings:

```python
    TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION: bool = False
    TRON_USDT_DESTINATION_ACTIVATION_LOCK_TTL_SEC: int = Field(300, ge=1)
    TRON_USDT_DESTINATION_ACTIVATION_LOCK_WAIT_SEC: int = Field(60, ge=0)
    TRON_USDT_DESTINATION_ACTIVATION_RECORD_TTL_SEC: int = Field(86400, ge=60)
```

Extend the existing `@model_validator(mode="after")` block with:

```python
        if self.TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION:
            if not self.TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED:
                raise ValueError(
                    "TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED must be true "
                    "when TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION=true"
                )
            if self.PROFEEX is None:
                raise ValueError(
                    "PROFEEX must be configured when "
                    "TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION=true"
                )
```

- [ ] **Step 4: Write ProfeeX activation client tests**

Create `tests/test_profeex_activation_provider.py`:

```python
from types import SimpleNamespace
import unittest

from app.resource_providers import profeex


class FakeSettings:
    api_base_url = "https://api.profeex.test/api/v1"
    api_key = SimpleNamespace(get_secret_value=lambda: "secret")
    currency = "TRX"
    timeout_sec = 1
    poll_interval_sec = 0.01


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class ProfeeXActivationProviderTests(unittest.TestCase):
    def setUp(self):
        self.original_config = profeex.config
        self.original_post = profeex.requests.post
        self.original_get = profeex.requests.get
        profeex.config = SimpleNamespace(PROFEEX=FakeSettings())

    def tearDown(self):
        profeex.config = self.original_config
        profeex.requests.post = self.original_post
        profeex.requests.get = self.original_get

    def test_activate_address_posts_expected_query_params(self):
        captured = {}

        def post(url, params, headers, timeout):
            captured.update(
                {
                    "url": url,
                    "params": params,
                    "headers": headers,
                    "timeout": timeout,
                }
            )
            return FakeResponse(
                202,
                {
                    "task_id": "task-1",
                    "target": "TTMqzSAwwcM1UqMy7Up2eQuNXZ6uUZ9AN5",
                    "status": "QUEUED",
                },
            )

        profeex.requests.post = post

        result = profeex.ProfeeXProvider().activate_address(
            "TTMqzSAwwcM1UqMy7Up2eQuNXZ6uUZ9AN5"
        )

        self.assertEqual(result["task_id"], "task-1")
        self.assertEqual(
            captured["url"],
            "https://api.profeex.test/api/v1/activation/activate",
        )
        self.assertEqual(
            captured["params"],
            {
                "address": "TTMqzSAwwcM1UqMy7Up2eQuNXZ6uUZ9AN5",
                "currency": "TRX",
            },
        )
        self.assertEqual(captured["headers"], {"X-API-Key": "secret"})

    def test_activation_409_is_retryable_duplicate(self):
        profeex.requests.post = lambda *args, **kwargs: FakeResponse(
            409,
            {"message": "duplicate request"},
        )

        with self.assertRaises(profeex.ProfeeXOrderError) as ctx:
            profeex.ProfeeXProvider().activate_address(
                "TTMqzSAwwcM1UqMy7Up2eQuNXZ6uUZ9AN5"
            )

        self.assertEqual(ctx.exception.resource_name, "activation")
        self.assertEqual(ctx.exception.error_code, "DUPLICATE_REQUEST")
        self.assertTrue(ctx.exception.temporary)

    def test_activation_503_is_retryable_unavailable(self):
        profeex.requests.post = lambda *args, **kwargs: FakeResponse(
            503,
            {"message": "service unavailable"},
        )

        with self.assertRaises(profeex.ProfeeXOrderError) as ctx:
            profeex.ProfeeXProvider().activate_address(
                "TTMqzSAwwcM1UqMy7Up2eQuNXZ6uUZ9AN5"
            )

        self.assertEqual(ctx.exception.resource_name, "activation")
        self.assertEqual(ctx.exception.error_code, "SERVICE_UNAVAILABLE")
        self.assertTrue(ctx.exception.temporary)

    def test_activation_request_exception_is_retryable_unavailable(self):
        def post(*args, **kwargs):
            raise profeex.requests.RequestException("timeout")

        profeex.requests.post = post

        with self.assertRaises(profeex.ProfeeXOrderError) as ctx:
            profeex.ProfeeXProvider().activate_address(
                "TTMqzSAwwcM1UqMy7Up2eQuNXZ6uUZ9AN5"
            )

        self.assertEqual(ctx.exception.resource_name, "activation")
        self.assertEqual(ctx.exception.error_code, "SERVICE_UNAVAILABLE")
        self.assertTrue(ctx.exception.temporary)

    def test_activation_422_invalid_address_is_terminal(self):
        profeex.requests.post = lambda *args, **kwargs: FakeResponse(
            422,
            {"error_code": "INVALID_ADDRESS", "message": "invalid address"},
        )

        with self.assertRaises(profeex.ProfeeXOrderError) as ctx:
            profeex.ProfeeXProvider().activate_address(
                "not-a-tron-address"
            )

        self.assertEqual(ctx.exception.resource_name, "activation")
        self.assertEqual(ctx.exception.error_code, "INVALID_ADDRESS")
        self.assertFalse(ctx.exception.temporary)

    def test_wait_for_activation_treats_completed_as_success(self):
        polls = iter(
            [
                FakeResponse(200, {"task_id": "task-1", "status": "PROCESSING"}),
                FakeResponse(200, {"task_id": "task-1", "status": "COMPLETED"}),
            ]
        )
        profeex.requests.get = lambda *args, **kwargs: next(polls)

        result = profeex.ProfeeXProvider().wait_for_activation(
            FakeSettings(),
            "task-1",
            {"task_id": "task-1", "status": "QUEUED"},
        )

        self.assertEqual(result["status"], "COMPLETED")
```

- [ ] **Step 5: Run ProfeeX activation tests and verify they fail**

Run:

```bash
cd /Users/test/PycharmProjects/tron-shkeeper
pytest tests/test_profeex_activation_provider.py -q
```

Expected: fails because `activate_address()` and `wait_for_activation()` do not exist.

- [ ] **Step 6: Implement ProfeeX activation methods**

In `app/resource_providers/profeex.py`, add constants near status sets:

```python
    ACTIVATION_SUCCESS_STATUSES = {"ACTIVE", "COMPLETED"}
    ACTIVATION_FAILURE_STATUSES = {"FAILED", "CANCELLED", "unknown"}
```

Add methods to `ProfeeXProvider`:

```python
    def activate_address(self, receiver: str) -> dict:
        settings = config.PROFEEX
        if settings is None:
            raise ProfeeXOrderError(
                "activation",
                "PROFEEX config is missing. Cannot activate destination.",
                "CONFIGURATION_ERROR",
                temporary=False,
            )
        try:
            response = requests.post(
                self._url(settings, "/activation/activate"),
                params={
                    "address": receiver,
                    "currency": settings.currency,
                },
                headers=self._headers(settings),
                timeout=self.REQUEST_TIMEOUT_SEC,
            )
        except requests.RequestException as exc:
            raise ProfeeXOrderError(
                "activation",
                f"ProfeeX activation request failed: {exc}",
                "SERVICE_UNAVAILABLE",
                temporary=True,
            ) from exc

        if response.status_code == 202:
            data = self._json_response(response, "activation")
            task_id = self._extract_task_id(data, "activation")
            if task_id is None:
                raise ProfeeXOrderError(
                    "activation",
                    f"ProfeeX activation response has no task_id: {data}",
                    "INVALID_PARAMETERS",
                    temporary=False,
                )
            logger.info(f"ProfeeX activation accepted: {data}")
            return data

        if response.status_code == 409:
            raise ProfeeXOrderError(
                "activation",
                f"ProfeeX activation duplicate or already active: {response.text}",
                "DUPLICATE_REQUEST",
                temporary=True,
            )
        if response.status_code == 503:
            raise ProfeeXOrderError(
                "activation",
                f"ProfeeX activation unavailable: {response.text}",
                "SERVICE_UNAVAILABLE",
                temporary=True,
            )

        data = self._safe_json(response)
        code = self._error_code_from_payload(data)
        temporary = code in TEMPORARY_ERROR_CODES or code in OPERATIONAL_ERROR_CODES
        raise ProfeeXOrderError(
            "activation",
            f"ProfeeX activation rejected with status {response.status_code}: {response.text}",
            code or "UNKNOWN_ERROR",
            temporary=temporary,
        )

    def wait_for_activation(self, settings, task_id: str, initial_order: dict) -> dict:
        return self._wait_for_status(
            settings,
            task_id,
            initial_order,
            "activation",
            success_statuses=self.ACTIVATION_SUCCESS_STATUSES,
            failure_statuses=self.ACTIVATION_FAILURE_STATUSES,
        )
```

Refactor existing `_wait_until_active()` to call a shared helper:

```python
    def _wait_until_active(
        self, settings, task_id: str, initial_order: dict, resource_name: str
    ) -> dict | None:
        try:
            return self._wait_for_status(
                settings,
                task_id,
                initial_order,
                resource_name,
                success_statuses=self.SUCCESS_STATUSES,
                failure_statuses=self.FAILURE_STATUSES,
            )
        except ProfeeXOrderError:
            return None
```

Add helpers:

```python
    def _wait_for_status(
        self,
        settings,
        task_id: str,
        initial_order: dict,
        resource_name: str,
        *,
        success_statuses,
        failure_statuses,
    ) -> dict:
        deadline = time.monotonic() + settings.timeout_sec
        order = initial_order
        last_status = None
        should_sleep_before_poll = False

        while True:
            status = order.get("status")
            if status != last_status:
                logger.info(f"ProfeeX {resource_name} order {task_id} status: {status}")
                last_status = status
            if status in success_statuses:
                return order
            if status in failure_statuses:
                raise self._order_error_from_order(resource_name, order)
            if status not in self.PENDING_STATUSES:
                raise ProfeeXOrderError(
                    resource_name,
                    f"ProfeeX {resource_name} order {task_id} returned unexpected status: {status}",
                    "UNKNOWN_ERROR",
                    temporary=False,
                )

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if should_sleep_before_poll:
                sleep_for = min(settings.poll_interval_sec, remaining)
                if sleep_for > 0:
                    time.sleep(sleep_for)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break

            try:
                response = requests.get(
                    self._url(settings, f"/delegation/status/{task_id}"),
                    headers=self._headers(settings),
                    timeout=min(self.REQUEST_TIMEOUT_SEC, remaining),
                )
            except requests.RequestException as exc:
                logger.warning(
                    f"ProfeeX poll request failed for {resource_name} order {task_id}: {exc}"
                )
                should_sleep_before_poll = True
                continue

            if response.status_code != 200:
                logger.warning(
                    f"ProfeeX poll for {resource_name} order {task_id} returned "
                    f"status {response.status_code}: {response.text}"
                )
                should_sleep_before_poll = True
                continue
            order = self._json_response(response, f"{resource_name} poll")
            should_sleep_before_poll = True

        raise ProfeeXOrderError(
            resource_name,
            f"ProfeeX {resource_name} order {task_id} did not reach success within "
            f"{settings.timeout_sec} seconds",
            "REQUEST_TIMEOUT",
            temporary=True,
        )

    @staticmethod
    def _safe_json(response):
        try:
            data = response.json()
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def _json_response(self, response, resource_name: str) -> dict:
        data = self._safe_json(response)
        if data is None:
            raise ProfeeXOrderError(
                resource_name,
                f"ProfeeX {resource_name} response is not a JSON object",
                "UNKNOWN_ERROR",
                temporary=False,
            )
        return data

    @staticmethod
    def _error_code_from_payload(data):
        if not isinstance(data, dict):
            return None
        if isinstance(data.get("error_code"), str):
            return data["error_code"]
        detail = data.get("detail")
        if isinstance(detail, dict) and isinstance(detail.get("error_code"), str):
            return detail["error_code"]
        return None
```

- [ ] **Step 7: Run Task 1 tests**

Run:

```bash
cd /Users/test/PycharmProjects/tron-shkeeper
pytest tests/test_resource_provider_config.py tests/test_profeex_activation_provider.py -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit Task 1**

Run:

```bash
cd /Users/test/PycharmProjects/tron-shkeeper
git add app/config.py app/resource_providers/profeex.py tests/test_resource_provider_config.py tests/test_profeex_activation_provider.py
git commit -m "feat: add profeex destination activation client"
```

## Task 2: Redis Destination Activation Orchestrator

**Files:**
- Create: `/Users/test/PycharmProjects/tron-shkeeper/app/payout_destination_activation.py`
- Modify: `/Users/test/PycharmProjects/tron-shkeeper/app/payout_observability.py`
- Test: `/Users/test/PycharmProjects/tron-shkeeper/tests/test_payout_destination_activation.py`

- [ ] **Step 1: Write activation orchestrator tests**

Create `tests/test_payout_destination_activation.py`:

```python
from decimal import Decimal
from types import SimpleNamespace
import json
import unittest

import prometheus_client

from app import payout_destination_activation as activation
from app.resource_providers.profeex import ProfeeXOrderError


DESTINATION = "TTMqzSAwwcM1UqMy7Up2eQuNXZ6uUZ9AN5"


class FakeLock:
    def __init__(self, events):
        self.events = events

    def acquire(self, blocking=True):
        self.events.append(("lock_acquire", blocking))
        return True

    def release(self):
        self.events.append(("lock_release",))


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.events = []

    def lock(self, name, timeout, blocking_timeout, thread_local):
        self.events.append(("lock", name, timeout, blocking_timeout, thread_local))
        return FakeLock(self.events)

    def get(self, key):
        self.events.append(("get", key))
        return self.values.get(key)

    def setex(self, key, ttl, value):
        self.events.append(("setex", key, ttl, json.loads(value)))
        self.values[key] = value.encode("utf-8") if isinstance(value, str) else value

    def delete(self, key):
        self.events.append(("delete", key))
        self.values.pop(key, None)


class FakeProvider:
    def __init__(self):
        self.calls = []

    def activate_address(self, destination):
        self.calls.append(("activate", destination))
        return {"task_id": "task-1", "target": destination, "status": "QUEUED"}

    def wait_for_activation(self, settings, task_id, order):
        self.calls.append(("wait", task_id, order["status"]))
        return {"task_id": task_id, "target": DESTINATION, "status": "COMPLETED"}


class FailingProvider(FakeProvider):
    def wait_for_activation(self, settings, task_id, order):
        self.calls.append(("wait", task_id, order["status"]))
        raise ProfeeXOrderError(
            "activation",
            "provider unavailable",
            "SERVICE_UNAVAILABLE",
            temporary=True,
        )


class DuplicateProvider(FakeProvider):
    def activate_address(self, destination):
        self.calls.append(("activate", destination))
        raise ProfeeXOrderError(
            "activation",
            "duplicate request",
            "DUPLICATE_REQUEST",
            temporary=True,
        )


class DestinationActivationTests(unittest.TestCase):
    def setUp(self):
        self.original_config = activation.config
        activation.config = SimpleNamespace(
            REDIS_HOST="localhost",
            PROFEEX=SimpleNamespace(timeout_sec=1, poll_interval_sec=0.01),
            TRON_USDT_DESTINATION_ACTIVATION_LOCK_TTL_SEC=300,
            TRON_USDT_DESTINATION_ACTIVATION_LOCK_WAIT_SEC=60,
            TRON_USDT_DESTINATION_ACTIVATION_RECORD_TTL_SEC=86400,
        )
        from app.payout_observability import clear_destination_activation_metrics

        clear_destination_activation_metrics()

    def tearDown(self):
        activation.config = self.original_config
        from app.payout_observability import clear_destination_activation_metrics

        clear_destination_activation_metrics()

    def quote_sequence(self, *is_new_values):
        values = list(is_new_values)

        def quote(destination):
            return {"is_new_address": values.pop(0), "energy_required": 65000, "trx_burned": Decimal("1.1")}

        return quote

    def test_activates_once_and_persists_task_id(self):
        redis_client = FakeRedis()
        provider = FakeProvider()

        result = activation.ensure_destination_activated(
            DESTINATION,
            quote_fn=self.quote_sequence(True, True, False),
            provider=provider,
            redis_client=redis_client,
        )

        self.assertTrue(result.activated)
        self.assertEqual(provider.calls, [("activate", DESTINATION), ("wait", "task-1", "QUEUED")])
        self.assertTrue(any(event[0] == "setex" and event[3]["task_id"] == "task-1" for event in redis_client.events))
        text = prometheus_client.generate_latest().decode()
        self.assertIn(
            'tron_payout_destination_activation_total{result="success"} 1.0',
            text,
        )
        self.assertIn(
            "tron_payout_destination_activation_duration_seconds_count 1.0",
            text,
        )

    def test_active_destination_skips_lock_and_provider(self):
        redis_client = FakeRedis()
        provider = FakeProvider()

        result = activation.ensure_destination_activated(
            DESTINATION,
            quote_fn=self.quote_sequence(False),
            provider=provider,
            redis_client=redis_client,
        )

        self.assertFalse(result.activated)
        self.assertEqual(provider.calls, [])
        self.assertFalse(any(event[0] == "lock" for event in redis_client.events))

    def test_existing_task_record_is_resumed(self):
        redis_client = FakeRedis()
        redis_client.values[
            activation.activation_record_key(DESTINATION)
        ] = json.dumps({"destination": DESTINATION, "task_id": "task-existing", "status": "PROCESSING"}).encode("utf-8")
        provider = FakeProvider()

        result = activation.ensure_destination_activated(
            DESTINATION,
            quote_fn=self.quote_sequence(True, True, False),
            provider=provider,
            redis_client=redis_client,
        )

        self.assertTrue(result.activated)
        self.assertEqual(provider.calls, [("wait", "task-existing", "PROCESSING")])

    def test_duplicate_activation_rechecks_destination_before_retrying(self):
        redis_client = FakeRedis()
        provider = DuplicateProvider()

        result = activation.ensure_destination_activated(
            DESTINATION,
            quote_fn=self.quote_sequence(True, True, False),
            provider=provider,
            redis_client=redis_client,
        )

        self.assertFalse(result.activated)
        self.assertEqual(result.status, "ALREADY_ACTIVE")
        self.assertEqual(provider.calls, [("activate", DESTINATION)])

    def test_retryable_provider_failure_records_metric(self):
        redis_client = FakeRedis()
        provider = FailingProvider()

        with self.assertRaises(activation.DestinationActivationError) as ctx:
            activation.ensure_destination_activated(
                DESTINATION,
                quote_fn=self.quote_sequence(True, True),
                provider=provider,
                redis_client=redis_client,
            )

        self.assertTrue(ctx.exception.temporary)
        self.assertEqual(ctx.exception.code, "PAYOUT_DESTINATION_ACTIVATION_UNAVAILABLE")
        text = prometheus_client.generate_latest().decode()
        self.assertIn(
            'tron_payout_destination_activation_total{result="retryable_error"} 1.0',
            text,
        )
```

- [ ] **Step 2: Run orchestrator tests and verify they fail**

Run:

```bash
cd /Users/test/PycharmProjects/tron-shkeeper
pytest tests/test_payout_destination_activation.py -q
```

Expected: fails because `app/payout_destination_activation.py` and destination activation metric helpers do not exist.

- [ ] **Step 3: Implement destination activation metrics**

In `app/payout_observability.py`, extend the import:

```python
from prometheus_client import Counter, Histogram
```

Add metrics near `tron_payout_request_failed`:

```python
DESTINATION_ACTIVATION_RESULTS = {"success", "retryable_error", "terminal_error"}

tron_payout_destination_activation_total = Counter(
    "tron_payout_destination_activation",
    "TRON payout destination activation attempts by result.",
    ("result",),
)

tron_payout_destination_activation_duration_seconds = Histogram(
    "tron_payout_destination_activation_duration_seconds",
    "TRON payout destination activation duration in seconds.",
)
```

Add helpers after `record_payout_request_failed()`:

```python
def _metric_activation_result(result):
    result = str(result or "").strip().lower()
    return result if result in DESTINATION_ACTIVATION_RESULTS else "terminal_error"


def record_destination_activation(result, duration_seconds):
    tron_payout_destination_activation_total.labels(
        result=_metric_activation_result(result),
    ).inc()
    tron_payout_destination_activation_duration_seconds.observe(
        max(0.0, float(duration_seconds or 0.0))
    )


def clear_destination_activation_metrics():
    tron_payout_destination_activation_total.clear()
    tron_payout_destination_activation_duration_seconds.clear()
```

Update `clear_payout_request_metrics()` so existing payout API tests keep clearing all payout observability:

```python
def clear_payout_request_metrics():
    tron_payout_request_failed.clear()
    clear_destination_activation_metrics()
```

- [ ] **Step 4: Implement Redis activation orchestrator**

Create `app/payout_destination_activation.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
import json
import time
from datetime import datetime, timezone

import redis

from .config import config
from .logging import logger
from .payout_observability import record_destination_activation
from .resource_providers.profeex import ProfeeXOrderError, ProfeeXProvider


@dataclass
class DestinationActivationResult:
    activated: bool
    task_id: str | None = None
    status: str | None = None


class DestinationActivationError(RuntimeError):
    def __init__(self, message, *, code, temporary):
        super().__init__(message)
        self.code = code
        self.temporary = temporary


def activation_lock_key(destination: str) -> str:
    return f"tron_usdt_destination_activation_lock:{destination}"


def activation_record_key(destination: str) -> str:
    return f"tron_usdt_destination_activation:{destination}"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redis_client():
    return redis.Redis.from_url(f"redis://{config.REDIS_HOST}")


def _is_active_quote(quote: dict | None) -> bool:
    return bool(quote) and quote.get("is_new_address") is False


def _load_record(redis_client, destination: str) -> dict | None:
    raw = redis_client.get(activation_record_key(destination))
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        record = json.loads(raw)
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def _store_record(redis_client, destination: str, record: dict) -> None:
    record = dict(record)
    record["destination"] = destination
    record["updated_at"] = _utcnow()
    record.setdefault("created_at", record["updated_at"])
    redis_client.setex(
        activation_record_key(destination),
        config.TRON_USDT_DESTINATION_ACTIVATION_RECORD_TTL_SEC,
        json.dumps(record, sort_keys=True, separators=(",", ":")),
    )


def _raise_activation_error(exc: ProfeeXOrderError) -> None:
    code = exc.error_code or "PAYOUT_DESTINATION_ACTIVATION_UNAVAILABLE"
    mapped = {
        "DUPLICATE_REQUEST": "PAYOUT_DESTINATION_ACTIVATION_DUPLICATE",
        "REQUEST_TIMEOUT": "PAYOUT_DESTINATION_ACTIVATION_TIMEOUT",
        "SERVICE_UNAVAILABLE": "PAYOUT_DESTINATION_ACTIVATION_UNAVAILABLE",
        "RATE_LIMIT_EXCEEDED": "PAYOUT_DESTINATION_ACTIVATION_UNAVAILABLE",
        "INSUFFICIENT_BALANCE": "PAYOUT_DESTINATION_ACTIVATION_UNAVAILABLE",
    }.get(code, code)
    raise DestinationActivationError(str(exc), code=mapped, temporary=exc.temporary) from exc


def ensure_destination_activated(
    destination: str,
    *,
    quote_fn,
    provider: ProfeeXProvider | None = None,
    redis_client=None,
) -> DestinationActivationResult:
    quote = quote_fn(destination)
    if _is_active_quote(quote):
        return DestinationActivationResult(activated=False, status="ALREADY_ACTIVE")

    started_at = time.monotonic()
    metric_result = "success"
    lock = None
    try:
        provider = provider or ProfeeXProvider()
        redis_client = redis_client or _redis_client()
        lock = redis_client.lock(
            activation_lock_key(destination),
            timeout=config.TRON_USDT_DESTINATION_ACTIVATION_LOCK_TTL_SEC,
            blocking_timeout=config.TRON_USDT_DESTINATION_ACTIVATION_LOCK_WAIT_SEC,
            thread_local=False,
        )
        try:
            acquired = lock.acquire(blocking=True)
        except redis.exceptions.RedisError as exc:
            raise DestinationActivationError(
                "Unable to acquire TRON destination activation lock",
                code="PAYOUT_DESTINATION_ACTIVATION_UNAVAILABLE",
                temporary=True,
            ) from exc
        if not acquired:
            raise DestinationActivationError(
                "Timed out waiting for TRON destination activation lock",
                code="PAYOUT_DESTINATION_ACTIVATION_PENDING",
                temporary=True,
            )

        quote = quote_fn(destination)
        if _is_active_quote(quote):
            return DestinationActivationResult(activated=False, status="ALREADY_ACTIVE")

        record = _load_record(redis_client, destination)
        order = None
        task_id = None
        if record and record.get("task_id") and record.get("status") in (
            "QUEUED",
            "PENDING",
            "PROCESSING",
        ):
            task_id = record["task_id"]
            order = {"task_id": task_id, "status": record["status"], "target": destination}
        if order is None:
            try:
                order = provider.activate_address(destination)
            except ProfeeXOrderError as exc:
                if exc.error_code == "DUPLICATE_REQUEST":
                    quote = quote_fn(destination)
                    if _is_active_quote(quote):
                        return DestinationActivationResult(activated=False, status="ALREADY_ACTIVE")
                _raise_activation_error(exc)
            task_id = order["task_id"]
            _store_record(redis_client, destination, order)

        try:
            active_order = provider.wait_for_activation(config.PROFEEX, task_id, order)
        except ProfeeXOrderError as exc:
            _store_record(
                redis_client,
                destination,
                {
                    "task_id": task_id,
                    "status": "PROCESSING" if exc.temporary else "FAILED",
                    "error_code": exc.error_code,
                    "error_message": str(exc),
                },
            )
            _raise_activation_error(exc)

        _store_record(redis_client, destination, active_order)
        quote = quote_fn(destination)
        if not _is_active_quote(quote):
            raise DestinationActivationError(
                "TRON destination is still not active after ProfeeX activation",
                code="PAYOUT_DESTINATION_ACTIVATION_PENDING",
                temporary=True,
            )
        logger.info(
            "TRON destination activation complete: destination=%s task_id=%s status=%s",
            destination,
            task_id,
            active_order.get("status"),
        )
        return DestinationActivationResult(
            activated=True,
            task_id=task_id,
            status=active_order.get("status"),
        )
    except DestinationActivationError as exc:
        metric_result = "retryable_error" if exc.temporary else "terminal_error"
        raise
    finally:
        if lock is not None:
            try:
                lock.release()
            except redis.exceptions.RedisError:
                logger.warning("TRON destination activation lock release failed: %s", destination)
        record_destination_activation(metric_result, time.monotonic() - started_at)
```

- [ ] **Step 5: Run orchestrator tests**

Run:

```bash
cd /Users/test/PycharmProjects/tron-shkeeper
pytest tests/test_payout_destination_activation.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit Task 2**

Run:

```bash
cd /Users/test/PycharmProjects/tron-shkeeper
git add app/payout_destination_activation.py app/payout_observability.py tests/test_payout_destination_activation.py
git commit -m "feat: add tron destination activation idempotency"
```

## Task 3: TRON Payout-Execution Preflight Eligibility

**Files:**
- Modify: `/Users/test/PycharmProjects/tron-shkeeper/app/payout_status.py`
- Modify: `/Users/test/PycharmProjects/tron-shkeeper/app/payout_execution.py`
- Test: `/Users/test/PycharmProjects/tron-shkeeper/tests/test_payout_status_confirmation.py`

- [ ] **Step 1: Write payout-execution preflight eligibility tests**

In `tests/test_payout_status_confirmation.py`, add:

```python
def test_execution_preflight_allows_unactivated_destination_when_auto_activation_enabled(self):
    original_flag = self.store_module.config.TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION
    self.store_module.config.TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION = True
    try:
        quote = FakeQuote(
            submit_ready=False,
            code="DESTINATION_NOT_ACTIVATED",
            reason="TRON payout destination is not activated",
        )
        result = self.preflight_with_runtime(quote=quote, execution_id="1")
    finally:
        self.store_module.config.TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION = original_flag

    self.assertEqual(result["resource_quote"]["blocking_code"], "DESTINATION_NOT_ACTIVATED")
    self.assertTrue(result["destination_activation_submit_eligible"])


def test_legacy_preflight_still_rejects_unactivated_destination(self):
    original_flag = self.store_module.config.TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION
    self.store_module.config.TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION = True
    try:
        quote = FakeQuote(
            submit_ready=False,
            code="DESTINATION_NOT_ACTIVATED",
            reason="TRON payout destination is not activated",
        )
        with self.assertRaises(self.store_module.PayoutExecutionError) as ctx:
            self.preflight_with_runtime(quote=quote, execution_id=None)
    finally:
        self.store_module.config.TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION = original_flag

    self.assertEqual(ctx.exception.code, "DESTINATION_NOT_ACTIVATED")
```

Update the existing `preflight_with_runtime()` helper in this test class:

```python
    def preflight_with_runtime(self, *, wallet_balance=Decimal("100"), quote=None, worker_ready=True, execution_id="1"):
        payout_status = importlib.import_module("app.payout_status")
        quote = quote or FakeQuote()
        with patch.object(payout_status, "Wallet", lambda symbol: FakeWallet(symbol, wallet_balance)):
            with patch.object(
                payout_status,
                "estimate_fee_deposit_resources_for_usdt_payout",
                lambda destination, amount: quote,
            ):
                with patch.object(
                    payout_status,
                    "usdt_payout_worker_ready",
                    lambda: worker_ready,
                ):
                    payload = self.body(**({"execution_id": execution_id} if execution_id is not None else {}))
                    return self.store_module.PayoutExecutionStore.preflight(
                        payload,
                        authenticated_consumer="grither-pay",
                        execution_id=execution_id,
                    )
```

- [ ] **Step 2: Run preflight tests and verify they fail**

Run:

```bash
cd /Users/test/PycharmProjects/tron-shkeeper
pytest tests/test_payout_status_confirmation.py -q
```

Expected: fails because preflight still rejects `DESTINATION_NOT_ACTIVATED`.

- [ ] **Step 3: Implement submit-eligible diagnostic in preflight**

In `app/payout_status.py`, change the signature:

```python
def run_tron_usdt_preflight_checks(canonical, *, allow_destination_auto_activation=False):
```

Replace the `if not quote.submit_ready` block with:

```python
    if not quote.submit_ready:
        if (
            allow_destination_auto_activation
            and config.TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION
            and quote.blocking_code == "DESTINATION_NOT_ACTIVATED"
        ):
            return {
                "resource_quote": quote.to_dict(),
                "destination_activation_submit_eligible": True,
            }
        raise PayoutStatusError(
            quote.blocking_reason or "TRON USDT payout resources are not ready",
            code=quote.blocking_code or "PAYOUT_RESOURCE_UNAVAILABLE",
            status_code=503,
        )
```

In `app/payout_execution.py`, update `preflight()`:

```python
                runtime = run_tron_usdt_preflight_checks(
                    canonical,
                    allow_destination_auto_activation=execution_id is not None,
                )
```

This keeps `/USDT/payout/preflight` legacy behavior unchanged because `execution_id` is `None`.

- [ ] **Step 4: Run preflight tests**

Run:

```bash
cd /Users/test/PycharmProjects/tron-shkeeper
pytest tests/test_payout_status_confirmation.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 3**

Run:

```bash
cd /Users/test/PycharmProjects/tron-shkeeper
git add app/payout_status.py app/payout_execution.py tests/test_payout_status_confirmation.py
git commit -m "feat: allow activation-eligible tron payout preflight"
```

## Task 4: TRON Worker Activation and Retryable State Handling

**Files:**
- Modify: `/Users/test/PycharmProjects/tron-shkeeper/app/payout_resources.py`
- Modify: `/Users/test/PycharmProjects/tron-shkeeper/app/payout_execution.py`
- Test: `/Users/test/PycharmProjects/tron-shkeeper/tests/test_payout_resources.py`
- Test: `/Users/test/PycharmProjects/tron-shkeeper/tests/test_payout_execution_boundaries.py`
- Test: `/Users/test/PycharmProjects/tron-shkeeper/tests/test_payout_task_resource_provisioning.py`

- [ ] **Step 1: Write resource helper activation tests**

In `tests/test_payout_resources.py`, add:

```python
def test_ensure_activates_destination_when_allowed(self):
    from app import payout_resources

    calls = []
    quotes = [
        payout_resources.PayoutResourceQuote(
            source_address=FEE_DEPOSIT,
            destination=DESTINATION,
            amount="1.25",
            activation_required=True,
            estimated_trx_burned="1.1",
            energy=payout_resources.ResourceReadiness("profeex", 65000, 0, 65000),
            bandwidth=payout_resources.ResourceReadiness("profeex", 346, 0, 346),
            submit_ready=False,
            blocking_code="DESTINATION_NOT_ACTIVATED",
            blocking_reason="TRON payout destination is not activated",
        ),
        payout_resources.PayoutResourceQuote(
            source_address=FEE_DEPOSIT,
            destination=DESTINATION,
            amount="1.25",
            activation_required=False,
            estimated_trx_burned="6.5",
            energy=payout_resources.ResourceReadiness("profeex", 65000, 65000, 0),
            bandwidth=payout_resources.ResourceReadiness("profeex", 346, 346, 0),
            submit_ready=True,
            blocking_code=None,
            blocking_reason=None,
        ),
    ]

    def estimate(destination, amount, tron_client=None):
        return quotes.pop(0)

    original_estimate = payout_resources.estimate_fee_deposit_resources_for_usdt_payout
    original_activation = payout_resources.ensure_destination_activated
    original_flag = payout_resources.config.TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION
    payout_resources.estimate_fee_deposit_resources_for_usdt_payout = estimate
    payout_resources.ensure_destination_activated = lambda destination, *, quote_fn: calls.append(destination)
    payout_resources.config.TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION = True
    try:
        result = payout_resources.ensure_fee_deposit_resources_for_usdt_payout(
            DESTINATION,
            Decimal("1.25"),
            tron_client=object(),
            allow_destination_activation=True,
        )
    finally:
        payout_resources.estimate_fee_deposit_resources_for_usdt_payout = original_estimate
        payout_resources.ensure_destination_activated = original_activation
        payout_resources.config.TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION = original_flag

    self.assertEqual(calls, [DESTINATION])
    self.assertTrue(result.submit_ready)


def test_ensure_maps_retryable_activation_error_to_resource_error(self):
    from app import payout_resources
    from app.payout_destination_activation import DestinationActivationError

    quote = payout_resources.PayoutResourceQuote(
        source_address=FEE_DEPOSIT,
        destination=DESTINATION,
        amount="1.25",
        activation_required=True,
        estimated_trx_burned="1.1",
        energy=payout_resources.ResourceReadiness("profeex", 65000, 0, 65000),
        bandwidth=payout_resources.ResourceReadiness("profeex", 346, 0, 346),
        submit_ready=False,
        blocking_code="DESTINATION_NOT_ACTIVATED",
        blocking_reason="TRON payout destination is not activated",
    )

    def estimate(destination, amount, tron_client=None):
        return quote

    def activate(destination, *, quote_fn):
        raise DestinationActivationError(
            "ProfeeX activation unavailable",
            code="PAYOUT_DESTINATION_ACTIVATION_UNAVAILABLE",
            temporary=True,
        )

    original_estimate = payout_resources.estimate_fee_deposit_resources_for_usdt_payout
    original_activation = payout_resources.ensure_destination_activated
    original_flag = payout_resources.config.TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION
    payout_resources.estimate_fee_deposit_resources_for_usdt_payout = estimate
    payout_resources.ensure_destination_activated = activate
    payout_resources.config.TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION = True
    try:
        with self.assertRaises(payout_resources.PayoutResourceError) as ctx:
            payout_resources.ensure_fee_deposit_resources_for_usdt_payout(
                DESTINATION,
                Decimal("1.25"),
                tron_client=object(),
                allow_destination_activation=True,
            )
    finally:
        payout_resources.estimate_fee_deposit_resources_for_usdt_payout = original_estimate
        payout_resources.ensure_destination_activated = original_activation
        payout_resources.config.TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION = original_flag

    self.assertEqual(ctx.exception.code, "PAYOUT_DESTINATION_ACTIVATION_UNAVAILABLE")
```

- [ ] **Step 2: Write retryable execution boundary test**

In `tests/test_payout_execution_boundaries.py`, add:

```python
def test_retryable_activation_resource_error_returns_to_received_without_refund_state(self):
    from app import payout_resources
    from app.payout_destination_activation import DestinationActivationError

    self.submit()
    original_flag = payout_resources.config.TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION
    payout_resources.config.TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION = True
    quote = payout_resources.PayoutResourceQuote(
        source_address="fee-deposit",
        destination=DESTINATION,
        amount="25.000000",
        activation_required=True,
        estimated_trx_burned="1.1",
        energy=payout_resources.ResourceReadiness("profeex", 65000, 0, 65000),
        bandwidth=payout_resources.ResourceReadiness("profeex", 346, 0, 346),
        submit_ready=False,
        blocking_code="DESTINATION_NOT_ACTIVATED",
        blocking_reason="TRON payout destination is not activated",
    )

    def estimate(destination, amount, tron_client=None):
        return quote

    def activate(destination, *, quote_fn):
        raise DestinationActivationError(
            "ProfeeX activation unavailable",
            code="PAYOUT_DESTINATION_ACTIVATION_UNAVAILABLE",
            temporary=True,
        )

    original_estimate = payout_resources.estimate_fee_deposit_resources_for_usdt_payout
    original_activation = payout_resources.ensure_destination_activated
    payout_resources.estimate_fee_deposit_resources_for_usdt_payout = estimate
    payout_resources.ensure_destination_activated = activate
    events = []
    try:
        status = self.store_module.PayoutExecutionStore.execute(
            "1",
            wallet=BoundaryWallet(events, self.row),
            resource_ensurer=payout_resources.ensure_fee_deposit_resources_for_usdt_payout,
            lease_owner="worker-1",
        )
    finally:
        payout_resources.config.TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION = original_flag
        payout_resources.estimate_fee_deposit_resources_for_usdt_payout = original_estimate
        payout_resources.ensure_destination_activated = original_activation

    self.assertEqual(status["state"], "RECEIVED")
    self.assertEqual(status["failure_class"], "TRANSIENT")
    self.assertEqual(status["error_code"], "PAYOUT_DESTINATION_ACTIVATION_UNAVAILABLE")
    self.assertFalse(status["reconciliation_required"])
    row = self.row()
    self.assertIsNone(row["resource_reservation_id"])
    self.assertIsNone(row["signed_raw_tx_hash"])
    self.assertIsNone(row["broadcast_attempted_at"])
```

- [ ] **Step 3: Write terminal activation boundary test**

In `tests/test_payout_execution_boundaries.py`, add:

```python
def test_terminal_activation_failure_stays_failed_pre_broadcast_without_reconciliation(self):
    self.submit()
    events = []

    def resource_ensurer(destination, amount, tron_client=None, allow_destination_activation=False):
        events.append((destination, amount, allow_destination_activation))
        raise self.store_module.PayoutExecutionError(
            "Invalid TRON destination",
            code="INVALID_ADDRESS",
            status_code=422,
        )

    status = self.store_module.PayoutExecutionStore.execute(
        "1",
        wallet=BoundaryWallet(events, self.row),
        resource_ensurer=resource_ensurer,
        lease_owner="worker-1",
    )

    self.assertEqual(status["state"], "FAILED_PRE_BROADCAST")
    self.assertEqual(status["failure_class"], "PREFLIGHT")
    self.assertEqual(status["error_code"], "INVALID_ADDRESS")
    self.assertFalse(status["reconciliation_required"])
    row = self.row()
    self.assertIsNone(row["broadcast_attempted_at"])
    self.assertEqual(events[0][2], True)
```

- [ ] **Step 4: Write legacy no-auto-activation test**

In `tests/test_payout_task_resource_provisioning.py`, add:

```python
def test_legacy_payout_does_not_allow_destination_activation(self):
    tasks = load_tasks()
    calls = []

    def resource_ensurer(destination, amount, tron_client=None, allow_destination_activation=False):
        calls.append(allow_destination_activation)
        raise RuntimeError("stop before transfer")

    original = tasks.ensure_fee_deposit_resources_for_usdt_payout
    tasks.ensure_fee_deposit_resources_for_usdt_payout = resource_ensurer
    events, posted, restore = self.patch_tasks(tasks, enabled=True)
    try:
        with self.assertRaises(RuntimeError):
            tasks.payout.run(
                [{"dst": DESTINATION, "amount": Decimal("1.25"), "ensure_usdt_payout_resources": True}],
                "USDT",
            )
    finally:
        tasks.ensure_fee_deposit_resources_for_usdt_payout = original
        restore()

    self.assertEqual(calls, [False])
```

- [ ] **Step 5: Update existing boundary test doubles for the new opt-in argument**

In `tests/test_payout_execution_boundaries.py`, update `test_execute_persists_markers_before_external_side_effects()`:

```python
        def resource_ensurer(destination, amount, tron_client=None, allow_destination_activation=False):
            row = self.row()
            events.append(
                (
                    "resource_ensurer",
                    destination,
                    amount,
                    tron_client,
                    allow_destination_activation,
                )
            )
            self.assertEqual(row["state"], "SIGNING")
            self.assertTrue(row["resource_reservation_id"])
            self.assertIsNone(row["signed_raw_tx_hash"])
```

Update that test's expected events list:

```python
            [
                ("lock_enter",),
                ("resource_ensurer", DESTINATION, Decimal("25.000000"), "tron-client", True),
                ("build_signed_transfer", DESTINATION, Decimal("25.000000")),
                ("broadcast", "tx-1"),
                ("lock_exit",),
            ],
```

In `test_resource_ensurer_failure_after_marker_is_pre_broadcast_failure()`, update the helper:

```python
        def resource_ensurer(destination, amount, tron_client=None, allow_destination_activation=False):
            row = self.row()
            events.append(
                (
                    "resource_ensurer",
                    destination,
                    amount,
                    tron_client,
                    allow_destination_activation,
                )
            )
            self.assertEqual(row["state"], "SIGNING")
            self.assertTrue(row["resource_reservation_id"])
            self.assertIsNone(row["signed_raw_tx_hash"])
            raise self.store_module.PayoutExecutionError(
                "Unable to verify TRON USDT payout resources",
                code="PAYOUT_RESOURCES_UNAVAILABLE",
                status_code=503,
            )
```

Update that test's expected events list:

```python
        self.assertEqual(
            events,
            [("resource_ensurer", DESTINATION, Decimal("25.000000"), "tron-client", True)],
        )
```

- [ ] **Step 6: Run tests and verify they fail**

Run:

```bash
cd /Users/test/PycharmProjects/tron-shkeeper
pytest tests/test_payout_resources.py tests/test_payout_execution_boundaries.py tests/test_payout_task_resource_provisioning.py -q
```

Expected: fails because helper opt-in and retryable activation codes do not exist.

- [ ] **Step 7: Implement helper opt-in and activation call**

In `app/payout_resources.py`, import the orchestrator:

```python
from app.payout_destination_activation import (
    DestinationActivationError,
    ensure_destination_activated,
)
```

Change the helper signature:

```python
def ensure_fee_deposit_resources_for_usdt_payout(
    destination: str,
    amount: Decimal,
    *,
    tron_client=None,
    allow_destination_activation: bool = False,
) -> PayoutResourceQuote:
```

Immediately after the first quote:

```python
    if (
        not quote.submit_ready
        and quote.blocking_code == "DESTINATION_NOT_ACTIVATED"
        and allow_destination_activation
        and config.TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION
    ):
        try:
            ensure_destination_activated(
                destination,
                quote_fn=lambda receiver: estimate_usdt_transfer_fee_via_profeex(receiver),
            )
        except DestinationActivationError as exc:
            raise PayoutResourceError(str(exc), code=exc.code) from exc
        quote = estimate_fee_deposit_resources_for_usdt_payout(
            destination,
            amount,
            tron_client=client,
        )
```

Keep the existing `if not quote.submit_ready: raise PayoutResourceError(...)` block after this new activation branch.

- [ ] **Step 8: Implement retryable activation codes in sidecar execution**

In `app/payout_execution.py`, add near constants:

```python
RETRYABLE_PRE_BROADCAST_ERROR_CODES = {
    "PAYOUT_RESOURCE_LOCK_UNAVAILABLE",
    "PAYOUT_DESTINATION_ACTIVATION_PENDING",
    "PAYOUT_DESTINATION_ACTIVATION_UNAVAILABLE",
    "PAYOUT_DESTINATION_ACTIVATION_DUPLICATE",
    "PAYOUT_DESTINATION_ACTIVATION_TIMEOUT",
    "PAYOUT_RESOURCE_PROVIDER_UNAVAILABLE",
}
```

Replace the special-case `PAYOUT_RESOURCE_LOCK_UNAVAILABLE` block in `_mark_failed_or_reconciliation()` with:

```python
        error_code = getattr(exc, "code", None)
        if (
            error_code in RETRYABLE_PRE_BROADCAST_ERROR_CODES
            and not cls._has_unsafe_side_effect(row)
        ):
            row = cls._transition(
                row,
                STATE_RECEIVED,
                lease_owner=None,
                lease_expires_at=None,
                attempt_id=None,
                resource_reservation_id=None,
                failure_class="TRANSIENT",
                error_code=error_code,
                error_message=str(exc),
                reconciliation_required=0,
            )
            return cls._row_to_status(row)
```

Change the resource ensurer call in `execute()`:

```python
                resource_ensurer(
                    destination,
                    amount,
                    tron_client=wallet.client,
                    allow_destination_activation=True,
                )
```

- [ ] **Step 9: Keep legacy calls defaulted off**

No code change is needed in `app/tasks.py` legacy `payout()` if `ensure_fee_deposit_resources_for_usdt_payout()` defaults `allow_destination_activation=False`. Ensure `execute_payout_execution()` remains the only caller that reaches `allow_destination_activation=True` through `PayoutExecutionStore.execute()`.

- [ ] **Step 10: Run Task 4 tests**

Run:

```bash
cd /Users/test/PycharmProjects/tron-shkeeper
pytest tests/test_payout_resources.py tests/test_payout_execution_boundaries.py tests/test_payout_task_resource_provisioning.py -q
```

Expected: all tests pass.

- [ ] **Step 11: Commit Task 4**

Run:

```bash
cd /Users/test/PycharmProjects/tron-shkeeper
git add app/payout_resources.py app/payout_execution.py tests/test_payout_resources.py tests/test_payout_execution_boundaries.py tests/test_payout_task_resource_provisioning.py
git commit -m "feat: activate tron payout destinations during execution"
```

## Task 5: SHKeeper Core Structured Preflight Diagnostics

**Files:**
- Modify: `/Users/test/PycharmProjects/shkeeper.io/shkeeper/services/payout_sidecar_client.py`
- Modify: `/Users/test/PycharmProjects/shkeeper.io/shkeeper/services/payout_execution_reconciler.py`
- Test: `/Users/test/PycharmProjects/shkeeper.io/tests/test_payout_sidecar_client.py`
- Test: `/Users/test/PycharmProjects/shkeeper.io/tests/test_payout_execution_reconciler.py`

- [ ] **Step 1: Write sidecar client structured error test**

In `tests/test_payout_sidecar_client.py`, add:

```python
def test_preflight_http_503_carries_structured_payload(self):
    payout_sidecar_client.requests.post = lambda *args, **kwargs: FakeResponse(
        503,
        {
            "status": "error",
            "code": "PROFEEX_ESTIMATE_UNAVAILABLE",
            "message": "Unable to estimate resources",
        },
    )

    with self.assertRaises(SidecarStatusUnavailable) as ctx:
        HttpPayoutSidecarClient().preflight(self.execution)

    self.assertEqual(ctx.exception.status_code, 503)
    self.assertEqual(ctx.exception.payload["code"], "PROFEEX_ESTIMATE_UNAVAILABLE")
```

- [ ] **Step 2: Write reconciler transient preflight test**

In `tests/test_payout_execution_reconciler.py`, add:

```python
def test_created_preflight_structured_503_records_retryable_diagnostic_without_transition(self):
    execution = self.create_execution()
    client = FakeSidecarClient()
    client.raise_on_preflight = SidecarStatusUnavailable(
        "Sidecar preflight endpoint returned HTTP 503",
        status_code=503,
        payload={
            "code": "PROFEEX_ESTIMATE_UNAVAILABLE",
            "message": "Unable to estimate resources",
        },
    )

    PayoutExecutionReconciler.dispatch_ready(client=client)

    db.session.refresh(execution)
    self.assertEqual(execution.state, PayoutExecutionState.CREATED)
    self.assertEqual(execution.event_version, 1)
    self.assertFalse(execution.reconciliation_required)
    self.assertEqual(execution.error_code, "PROFEEX_ESTIMATE_UNAVAILABLE")
    self.assertEqual(execution.error_message, "Unable to estimate resources")
    self.assertIsNotNone(execution.next_dispatch_at)
```

Update `FakeSidecarClient.__init__()` in the test file to initialize the injected exception:

```python
        self.raise_on_preflight = None
```

Update `FakeSidecarClient.preflight()` to support the injected exception:

```python
    def preflight(self, execution):
        self.calls.append(("preflight", execution.id, execution.state.name))
        self.assert_execution_committed(execution.id)
        if self.raise_on_preflight:
            raise self.raise_on_preflight
        return dict(self.preflight_response)
```

- [ ] **Step 3: Run SHKeeper core tests and verify they fail**

Run:

```bash
cd /Users/test/PycharmProjects/shkeeper.io
pytest tests/test_payout_sidecar_client.py tests/test_payout_execution_reconciler.py -q
```

Expected: fails because `SidecarStatusUnavailable` has no `status_code`/`payload`, and preflight diagnostics do not back off.

- [ ] **Step 4: Implement structured sidecar exception**

In `shkeeper/services/payout_sidecar_client.py`, change the exception:

```python
class SidecarStatusUnavailable(SidecarClientError):
    def __init__(self, message, *, status_code=None, payload=None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload if isinstance(payload, dict) else None
```

In `preflight()`, replace the `response.status_code >= 500` branch:

```python
        if response.status_code >= 500:
            raise SidecarStatusUnavailable(
                f"Sidecar preflight endpoint returned HTTP {response.status_code}",
                status_code=response.status_code,
                payload=payload,
            )
```

Use the same shape for `response.status_code >= 400 and not self._is_error_payload(payload)`.

- [ ] **Step 5: Implement preflight diagnostic backoff**

In `shkeeper/services/payout_execution_reconciler.py`, add helper:

```python
    @classmethod
    def _preflight_retry_delay(cls, execution):
        attempts = execution.dispatch_attempts or 1
        return min(60 * (2 ** max(attempts - 1, 0)), 3600)
```

Change the `except SidecarStatusUnavailable as exc` branch in `_preflight()`:

```python
        except SidecarStatusUnavailable as exc:
            payload = getattr(exc, "payload", None) or {}
            execution.error_code = payload.get("code") or "SIDECAR_PREFLIGHT_UNAVAILABLE"
            execution.error_message = payload.get("message") or str(exc)
            execution.next_dispatch_at = cls._utcnow() + timedelta(
                seconds=cls._preflight_retry_delay(execution)
            )
            db.session.add(execution)
            db.session.commit()
            return execution
```

Do not call `PayoutExecutionService.transition()` in this branch.

- [ ] **Step 6: Run SHKeeper core tests**

Run:

```bash
cd /Users/test/PycharmProjects/shkeeper.io
pytest tests/test_payout_sidecar_client.py tests/test_payout_execution_reconciler.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit Task 5**

Run:

```bash
cd /Users/test/PycharmProjects/shkeeper.io
git add shkeeper/services/payout_sidecar_client.py shkeeper/services/payout_execution_reconciler.py tests/test_payout_sidecar_client.py tests/test_payout_execution_reconciler.py
git commit -m "fix: keep sidecar preflight diagnostics retryable"
```

## Task 6: Grither Same-Version Transient Diagnostic Guard

**Files:**
- Modify: `/Users/test/IdeaProjects/grither-pay/apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutStateTransactionService.java`
- Modify: `/Users/test/IdeaProjects/grither-pay/docs/grither-pay-payout-integration.md`
- Test: `/Users/test/IdeaProjects/grither-pay/apps/backend/src/test/java/com/grither/pay/providers/shkeeper/web/ShKeeperPayoutWebhookControllerTest.java`

- [ ] **Step 1: Write Grither transient diagnostic test**

In `ShKeeperPayoutWebhookControllerTest.java`, add helper methods near the existing `callback()` helper:

```java
    private static ShKeeperPayoutCallbackPayload createdCallback(
            String eventId,
            String externalId,
            String transitionId
    ) {
        return createdCallback(eventId, externalId, transitionId, null, null);
    }

    private static ShKeeperPayoutCallbackPayload createdCallback(
            String eventId,
            String externalId,
            String transitionId,
            String errorCode,
            String errorMessage
    ) {
        return new ShKeeperPayoutCallbackPayload(
                eventId,
                "grither-pay",
                9701L,
                null,
                externalId,
                "usdt-payout-execution-v1",
                "TRON",
                "USDT",
                ShKeeperPayoutState.CREATED,
                1,
                transitionId,
                Instant.parse("2026-06-03T10:15:00Z"),
                Instant.parse("2026-06-03T10:15:01Z"),
                REQUEST_HASH,
                SIDECAR_HASH,
                null,
                List.of(),
                List.of(),
                errorCode,
                errorMessage,
                false,
                null);
    }
```

Add the transient diagnostic test:

```java
@Test
void sameVersionCreatedTransientDiagnosticDoesNotRequireReconciliation() throws Exception {
    seedExecution("PWEB-DIAG");
    ShKeeperPayoutCallbackPayload first = createdCallback("evt-diag-1", "PWEB-DIAG", "transition-diag");
    byte[] firstBody = objectMapper.writeValueAsBytes(first);
    restTemplate.postForEntity(
            ShKeeperPayoutWebhookController.PATH,
            new HttpEntity<>(firstBody, signedHeaders(firstBody, "evt-diag-1")),
            Map.class);

    ShKeeperPayoutCallbackPayload diagnostic = createdCallback(
            "evt-diag-2",
            "PWEB-DIAG",
            "transition-diag",
            "SIDECAR_PREFLIGHT_UNAVAILABLE",
            "Sidecar preflight endpoint returned HTTP 503");
    byte[] body = objectMapper.writeValueAsBytes(diagnostic);

    ResponseEntity<Map> response = restTemplate.postForEntity(
            ShKeeperPayoutWebhookController.PATH,
            new HttpEntity<>(body, signedHeaders(body, "evt-diag-2")),
            Map.class);

    assertThat(response.getStatusCode()).isEqualTo(HttpStatus.ACCEPTED);
    assertThat(response.getBody()).containsEntry("result", "IDEMPOTENT");
    ShKeeperPayoutExecution execution = executionRepository.findByExternalId("PWEB-DIAG").orElseThrow();
    assertThat(execution.getState()).isEqualTo(ShKeeperPayoutState.CREATED.name());
    assertThat(execution.isReconciliationRequired()).isFalse();
    assertThat(execution.getErrorCode()).isEqualTo("SIDECAR_PREFLIGHT_UNAVAILABLE");
    assertThat(execution.getErrorMessage()).isEqualTo("Sidecar preflight endpoint returned HTTP 503");
}
```

- [ ] **Step 2: Write same-version real-conflict regression test**

Add:

```java
@Test
void sameVersionCreatedChangedTransitionStillRequiresReconciliation() throws Exception {
    seedExecution("PWEB-CONFLICT");
    ShKeeperPayoutCallbackPayload first = createdCallback("evt-conflict-1", "PWEB-CONFLICT", "transition-a");
    byte[] firstBody = objectMapper.writeValueAsBytes(first);
    restTemplate.postForEntity(
            ShKeeperPayoutWebhookController.PATH,
            new HttpEntity<>(firstBody, signedHeaders(firstBody, "evt-conflict-1")),
            Map.class);

    ShKeeperPayoutCallbackPayload conflicting = createdCallback("evt-conflict-2", "PWEB-CONFLICT", "transition-b");
    byte[] body = objectMapper.writeValueAsBytes(conflicting);

    ResponseEntity<Map> response = restTemplate.postForEntity(
            ShKeeperPayoutWebhookController.PATH,
            new HttpEntity<>(body, signedHeaders(body, "evt-conflict-2")),
            Map.class);

    assertThat(response.getStatusCode()).isEqualTo(HttpStatus.ACCEPTED);
    assertThat(response.getBody()).containsEntry("result", "RECONCILIATION_REQUIRED");
    ShKeeperPayoutExecution execution = executionRepository.findByExternalId("PWEB-CONFLICT").orElseThrow();
    assertThat(execution.getState()).isEqualTo(ShKeeperPayoutState.RECONCILIATION_REQUIRED.name());
}
```

- [ ] **Step 3: Run Grither tests and verify they fail**

Run:

```bash
cd /Users/test/IdeaProjects/grither-pay
./gradlew :apps:backend:test --tests '*ShKeeperPayoutWebhookControllerTest'
```

Expected: transient diagnostic test fails because same-version error changes currently become reconciliation.

- [ ] **Step 4: Implement narrow same-version diagnostic predicate**

In `ShKeeperPayoutStateTransactionService.java`, add import:

```java
import java.util.Set;
```

Add constants:

```java
    private static final Set<String> SAME_VERSION_TRANSIENT_DIAGNOSTIC_CODES = Set.of(
            "SIDECAR_PREFLIGHT_UNAVAILABLE",
            "DESTINATION_NOT_ACTIVATED",
            "PROFEEX_ESTIMATE_UNAVAILABLE",
            "PAYOUT_DESTINATION_ACTIVATION_PENDING",
            "PAYOUT_DESTINATION_ACTIVATION_UNAVAILABLE",
            "PAYOUT_RESOURCE_PROVIDER_UNAVAILABLE"
    );
```

In `applyObservation()`, before `sameObservation()` reconciliation in the equal-version branch:

```java
        if (incoming.eventVersion() == currentVersion) {
            if (sameObservation(execution, incoming)) {
                return result(ShKeeperPayoutStateApplyResult.IDEMPOTENT, execution, null, null);
            }
            if (sameVersionTransientCreatedDiagnostic(execution, incoming)) {
                applyTransientDiagnostic(execution, incoming, receivedAt);
                return result(ShKeeperPayoutStateApplyResult.IDEMPOTENT, execution, null, null);
            }
            return markReconciliationRequired(
```

Add helpers:

```java
    private boolean sameVersionTransientCreatedDiagnostic(
            ShKeeperPayoutExecution execution,
            ShKeeperPayoutObservation incoming
    ) {
        return ShKeeperPayoutState.CREATED.name().equals(execution.getState())
                && storedState(incoming.state()) == ShKeeperPayoutState.CREATED
                && Objects.equals(execution.getStateTransitionId(), incoming.stateTransitionId())
                && Objects.equals(execution.getRequestHash(), incoming.requestHash())
                && Objects.equals(execution.getSidecarPayloadHash(), incoming.sidecarPayloadHash())
                && execution.getSidecarExecutionId() == null
                && incoming.sidecarExecutionId() == null
                && !hasBroadcastEvidence(execution, ShKeeperPayoutState.CREATED)
                && isEmptyJsonArray(execution.getTxidsJson())
                && isEmptyJsonArray(execution.getMessageHashesJson())
                && isEmptyList(incoming.txids())
                && isEmptyList(incoming.messageHashes())
                && incoming.failureClass() == null
                && SAME_VERSION_TRANSIENT_DIAGNOSTIC_CODES.contains(incoming.errorCode());
    }

    private void applyTransientDiagnostic(
            ShKeeperPayoutExecution execution,
            ShKeeperPayoutObservation incoming,
            Instant receivedAt
    ) {
        execution.setErrorCode(incoming.errorCode());
        execution.setErrorMessage(incoming.errorMessage());
        execution.setUpdatedAt(receivedAt);
    }

    private static boolean isEmptyJsonArray(String json) {
        return json == null || "[]".equals(json);
    }

    private static boolean isEmptyList(List<String> values) {
        return values == null || values.isEmpty();
    }
```

Use existing local helper names if this class already has equivalents; preserve the predicate semantics exactly.

- [ ] **Step 5: Update Grither integration docs**

In `docs/grither-pay-payout-integration.md`, add a note near same-version conflict handling:

```markdown
Same-version observations normally reconcile when state or evidence differs. The only exception is a narrow pre-broadcast diagnostic update: `CREATED` with the same transition/request/sidecar payload identity, no sidecar execution id, no tx/message evidence, and an allowlisted transient diagnostic code. These observations are idempotent diagnostics and must not move the withdrawal into manual reconciliation.
```

- [ ] **Step 6: Run Grither tests**

Run:

```bash
cd /Users/test/IdeaProjects/grither-pay
./gradlew :apps:backend:test --tests '*ShKeeperPayoutWebhookControllerTest'
```

Expected: all tests pass.

- [ ] **Step 7: Commit Task 6**

Run:

```bash
cd /Users/test/IdeaProjects/grither-pay
git add apps/backend/src/main/java/com/grither/pay/providers/shkeeper/payout/ShKeeperPayoutStateTransactionService.java apps/backend/src/test/java/com/grither/pay/providers/shkeeper/web/ShKeeperPayoutWebhookControllerTest.java docs/grither-pay-payout-integration.md
git commit -m "fix: ignore shkeeper transient preflight diagnostics"
```

## Task 7: Helm Rollout Flags

**Files:**
- Modify: `/Users/test/PycharmProjects/shkeeper-helm-charts/charts/shkeeper/values.yaml`
- Modify: `/Users/test/PycharmProjects/shkeeper-helm-charts/charts/shkeeper/templates/deployments/tron-shkeeper.yaml`
- Modify: `/Users/test/PycharmProjects/shkeeper-helm-charts/charts/shkeeper/templates/deployments/tron-usdt-payout-worker.yaml`
- Modify: `/Users/test/PycharmProjects/shkeeper-helm-charts/tests/test_shkeeper_fork_chart.py`

- [ ] **Step 1: Locate existing TRON env values**

Run:

```bash
cd /Users/test/PycharmProjects/shkeeper-helm-charts
rg -n "TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED|PROFEEX|tron-usdt-payouts|tron-shkeeper" .
```

Expected: paths include `charts/shkeeper/values.yaml`, `charts/shkeeper/templates/deployments/tron-shkeeper.yaml`, and `charts/shkeeper/templates/deployments/tron-usdt-payout-worker.yaml`.

- [ ] **Step 2: Add Helm values**

In `charts/shkeeper/values.yaml`, under `tron_shkeeper`, add:

```yaml
payoutDestinationActivation:
  autoActivateDestination: false
  lockTtlSec: 300
  lockWaitSec: 60
  recordTtlSec: 86400
```

This belongs under `tron_shkeeper`, not under `tron_shkeeper.usdtPayoutWorker`, because both the API sidecar and the worker need the same runtime settings.

- [ ] **Step 3: Protect the new env vars from extraEnv overrides**

In both TRON deployment templates, extend `$tronPayoutOwnedExtraEnvKeys`:

```gotemplate
{{- $tronDestinationActivationEnvKeys := list "TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION" "TRON_USDT_DESTINATION_ACTIVATION_LOCK_TTL_SEC" "TRON_USDT_DESTINATION_ACTIVATION_LOCK_WAIT_SEC" "TRON_USDT_DESTINATION_ACTIVATION_RECORD_TTL_SEC" }}
{{- $tronPayoutOwnedExtraEnvKeys := concat (list "TRON_USDT_PAYOUT_QUEUE" "TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED") $tronDestinationActivationEnvKeys (include "shkeeper.payoutSidecarOwnedEnvKeys" . | fromJsonArray) }}
```

- [ ] **Step 4: Add env vars to the TRON sidecar and payout worker containers**

Add these env vars next to existing `TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED`:

```yaml
- name: TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION
  value: {{ ternary "true" "false" (.Values.tron_shkeeper.payoutDestinationActivation.autoActivateDestination | default false) | quote }}
- name: TRON_USDT_DESTINATION_ACTIVATION_LOCK_TTL_SEC
  value: {{ .Values.tron_shkeeper.payoutDestinationActivation.lockTtlSec | quote }}
- name: TRON_USDT_DESTINATION_ACTIVATION_LOCK_WAIT_SEC
  value: {{ .Values.tron_shkeeper.payoutDestinationActivation.lockWaitSec | quote }}
- name: TRON_USDT_DESTINATION_ACTIVATION_RECORD_TTL_SEC
  value: {{ .Values.tron_shkeeper.payoutDestinationActivation.recordTtlSec | quote }}
```

- [ ] **Step 5: Add helm unit coverage**

In `tests/test_shkeeper_fork_chart.py`, extend the TRON template assertions:

```python
def test_tron_destination_activation_env_is_owned_and_disabled_by_default(self):
    sidecar_template = (CHART / "templates" / "deployments" / "tron-shkeeper.yaml").read_text()
    worker_template = (CHART / "templates" / "deployments" / "tron-usdt-payout-worker.yaml").read_text()

    for template in (sidecar_template, worker_template):
        self.assertIn("TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION", template)
        self.assertIn("TRON_USDT_DESTINATION_ACTIVATION_LOCK_TTL_SEC", template)
        self.assertIn("TRON_USDT_DESTINATION_ACTIVATION_LOCK_WAIT_SEC", template)
        self.assertIn("TRON_USDT_DESTINATION_ACTIVATION_RECORD_TTL_SEC", template)
        self.assertIn("$tronDestinationActivationEnvKeys", template)
```

- [ ] **Step 6: Render chart**

Run:

```bash
cd /Users/test/PycharmProjects/shkeeper-helm-charts
helm template shkeeper charts/shkeeper >/tmp/shkeeper-rendered.yaml
rg -n "TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION|TRON_USDT_DESTINATION_ACTIVATION" /tmp/shkeeper-rendered.yaml
```

Expected: all four new env vars render in the TRON sidecar and payout worker containers.

- [ ] **Step 7: Run helm chart tests**

Run:

```bash
cd /Users/test/PycharmProjects/shkeeper-helm-charts
pytest tests/test_shkeeper_fork_chart.py -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit Task 7**

Run:

```bash
cd /Users/test/PycharmProjects/shkeeper-helm-charts
git status --short
git add charts/shkeeper/values.yaml charts/shkeeper/templates/deployments/tron-shkeeper.yaml charts/shkeeper/templates/deployments/tron-usdt-payout-worker.yaml tests/test_shkeeper_fork_chart.py
git commit -m "chore: add tron payout destination activation flags"
```

## Task 8: End-to-End Verification

**Files:**
- No code files. Use deployed dev environment after Tasks 1-7 are merged/deployed.

- [ ] **Step 1: Run focused TRON sidecar suite**

Run:

```bash
cd /Users/test/PycharmProjects/tron-shkeeper
pytest \
  tests/test_profeex_activation_provider.py \
  tests/test_payout_destination_activation.py \
  tests/test_payout_resources.py \
  tests/test_payout_status_confirmation.py \
  tests/test_payout_execution_boundaries.py \
  tests/test_payout_task_resource_provisioning.py \
  -q
```

Expected: all tests pass.

- [ ] **Step 2: Run focused SHKeeper core suite**

Run:

```bash
cd /Users/test/PycharmProjects/shkeeper.io
pytest tests/test_payout_sidecar_client.py tests/test_payout_execution_reconciler.py -q
```

Expected: all tests pass.

- [ ] **Step 3: Run focused Grither suite**

Run:

```bash
cd /Users/test/IdeaProjects/grither-pay
./gradlew :apps:backend:test --tests '*ShKeeperPayoutWebhookControllerTest'
```

Expected: all tests pass.

- [ ] **Step 4: Deploy to dev with flag enabled**

Set:

```text
TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED=true
TRON_USDT_PAYOUT_AUTO_ACTIVATE_DESTINATION=true
```

Keep legacy `/payout` callers unchanged.

- [ ] **Step 5: Execute one payout to a fresh TRON destination**

Create a Grither withdrawal through the existing admin/user flow with:

```text
network: USDT TRC-20
destination: a fresh unactivated TRON address
amount: a small dev amount
```

Expected:

- SHKeeper core payout execution moves `CREATED -> PREFLIGHTED -> ENQUEUED`.
- TRON sidecar logs one ProfeeX activation task id for the destination.
- Redis has one destination activation record.
- USDT transfer is broadcast only after ProfeeX activation succeeds.
- Grither withdrawal reaches `COMPLETED` without admin recovery.

- [ ] **Step 6: Verify transient failure behavior in dev**

Temporarily point ProfeeX base URL to an unavailable dev value or block activation API in a controlled dev deployment. Create a Grither withdrawal to a fresh destination.

Expected:

- no USDT txid is created;
- no fallback TRX activation from our own fee/main wallet happens;
- TRON sidecar execution remains retryable pre-broadcast;
- SHKeeper core does not move to `RECONCILIATION_REQUIRED`;
- Grither does not move to `RECONCILIATION_REQUIRED`.

- [ ] **Step 7: Commit verification notes**

If the repo uses deployment notes, add the observed dev payout id, activation task id, and txid to the release/deploy note. If no deployment note file exists, record the result in the PR description instead of adding a new document.

## Review and Handoff

- [ ] Request review of this plan and the spec before implementation.
- [ ] Fix Critical and Important review findings before writing production code.
- [ ] After review approval, choose execution mode:
  - Subagent-driven: use `superpowers:subagent-driven-development`.
  - Inline: use `superpowers:executing-plans`.
