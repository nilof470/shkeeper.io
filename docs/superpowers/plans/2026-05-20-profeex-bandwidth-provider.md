# ProfeeX Bandwidth Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add independent TRON bandwidth provider selection and implement ProfeeX ordinary bandwidth rental while keeping energy rental on staking or re:Fee.

**Architecture:** Split resource provisioning into energy and bandwidth capabilities under `app/resource_providers/`. `transfer_trc20_from` checks wallet bandwidth first, optionally rents bandwidth through the configured bandwidth provider, then estimates and provisions energy through the configured energy provider.

**Tech Stack:** Python 3.12, pydantic v2 settings, requests, tronpy, unittest, existing Celery task flow.

---

## Scope Check

This is one subsystem: TRON resource provisioning for the TRC-20 sweep path in `../tron-shkeeper`. The plan does not implement ProfeeX energy, ProfeeX activation, webhooks, provider fallback chains, or flash bandwidth.

## Target Repo

Run implementation commands from:

```bash
cd /Users/test/PycharmProjects/tron-shkeeper
```

## File Structure

- Create: `app/profeex.py`
  - Owns `ProfeeXConfig` and validation for ProfeeX API settings.
- Create: `app/resource_providers/__init__.py`
  - Exports public provider interfaces and factories.
- Create: `app/resource_providers/base.py`
  - Defines `EnergyProvider` and `BandwidthProvider` protocols.
- Create: `app/resource_providers/staking.py`
  - Moves current `StakingEnergyProvider` implementation.
- Create: `app/resource_providers/refee.py`
  - Moves current re:Fee energy and bandwidth code into one `RefeeProvider`.
- Create: `app/resource_providers/profeex.py`
  - Implements `ProfeeXBandwidthProvider`.
- Create: `app/resource_providers/factory.py`
  - Selects providers from `config.ENERGY_PROVIDER` and `config.BANDWIDTH_PROVIDER`.
- Modify: `app/config.py`
  - Replace `ENERGY_SOURCE` and `REFEE_RENT_BANDWIDTH`.
  - Add `ENERGY_PROVIDER`, `BANDWIDTH_PROVIDER`, and `PROFEEX`.
- Modify: `app/tasks.py`
  - Import the new factories.
  - Add `ensure_onetime_bandwidth`.
  - Use independent energy and bandwidth providers.
- Modify: `app/energy_provider.py`
  - Replace with a small compatibility re-export during this refactor.
- Modify: `docs/DEPLOYMENT.md`
  - Update Helm env examples and explain `BANDWIDTH_PROVIDER=disabled|refee|profeex`.
- Modify tests:
  - `tests/test_phase2_review_fixes.py`
  - `tests/test_refee_bandwidth_guard.py`
  - `tests/test_refee_energy_accounting.py`
  - Create: `tests/test_profeex_bandwidth_provider.py`
  - Create: `tests/test_resource_provider_config.py`

## Task 1: Config Rename and ProfeeX Settings

**Files:**
- Create: `app/profeex.py`
- Modify: `app/config.py`
- Test: `tests/test_resource_provider_config.py`
- Update existing tests that instantiate `Settings` with `ENERGY_SOURCE`.

- [ ] **Step 1: Write failing config tests**

Create `tests/test_resource_provider_config.py`:

```python
import unittest

from pydantic import ValidationError

from app.config import Settings
from app.profeex import ProfeeXConfig


class ResourceProviderConfigTests(unittest.TestCase):
    def test_refee_required_for_refee_energy_provider(self):
        with self.assertRaisesRegex(
            ValidationError,
            "REFEE must be configured when ENERGY_PROVIDER='refee'",
        ):
            Settings(ENERGY_PROVIDER="refee", BANDWIDTH_PROVIDER="disabled")

    def test_refee_required_for_refee_bandwidth_provider(self):
        with self.assertRaisesRegex(
            ValidationError,
            "REFEE must be configured when BANDWIDTH_PROVIDER='refee'",
        ):
            Settings(ENERGY_PROVIDER="staking", BANDWIDTH_PROVIDER="refee")

    def test_profeex_required_for_profeex_bandwidth_provider(self):
        with self.assertRaisesRegex(
            ValidationError,
            "PROFEEX must be configured when BANDWIDTH_PROVIDER='profeex'",
        ):
            Settings(ENERGY_PROVIDER="staking", BANDWIDTH_PROVIDER="profeex")

    def test_profeex_is_not_valid_energy_provider_yet(self):
        with self.assertRaises(ValidationError):
            Settings(ENERGY_PROVIDER="profeex")

    def test_profeex_config_rejects_non_https_api_base_url(self):
        with self.assertRaises(ValidationError):
            ProfeeXConfig(
                api_key="secret",
                api_base_url="http://api.profeex.test/api/v1",
            )

    def test_profeex_config_rejects_empty_api_key(self):
        with self.assertRaises(ValidationError):
            ProfeeXConfig(api_key="")

    def test_profeex_config_rejects_bandwidth_min_below_api_minimum(self):
        with self.assertRaises(ValidationError):
            ProfeeXConfig(api_key="secret", min_bandwidth_order_amount=349)

    def test_profeex_config_rejects_bandwidth_max_above_api_maximum(self):
        with self.assertRaises(ValidationError):
            ProfeeXConfig(api_key="secret", max_bandwidth_order_amount=10_001)


if __name__ == "__main__":
    unittest.main()
```

Update `tests/test_phase2_review_fixes.py::test_refee_fixed_energy_amount_must_not_be_below_order_minimum` to use:

```python
Settings(
    ENERGY_PROVIDER="refee",
    REFEE='{"api_key":"secret","min_energy_order_amount":30000}',
    REFEE_FIXED_ENERGY_ORDER_AMOUNT=20_000,
)
```

- [ ] **Step 2: Run config tests and verify failure**

Run:

```bash
uv run --python 3.12 --with-requirements requirements.txt python -m unittest \
  tests.test_resource_provider_config \
  tests.test_phase2_review_fixes.Phase2ReviewFixTests.test_refee_fixed_energy_amount_must_not_be_below_order_minimum \
  -v
```

Expected: fails because `app.profeex` and new config fields do not exist yet.

- [ ] **Step 3: Add `ProfeeXConfig`**

Create `app/profeex.py`:

```python
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator


class ProfeeXConfig(BaseModel):
    api_base_url: str = Field(default="https://api.profeex.io/api/v1", min_length=1)
    api_key: SecretStr
    currency: Literal["TRX", "USDT"] = "TRX"
    bandwidth_duration_label: Literal["1h", "1d", "3d", "7d", "14d"] = "1h"
    min_bandwidth_order_amount: int = Field(default=350, ge=350)
    max_bandwidth_order_amount: int = Field(default=10_000, le=10_000)
    poll_interval_sec: float = Field(default=2.0, gt=0)
    timeout_sec: int = Field(default=60, gt=0)

    @field_validator("api_base_url")
    @classmethod
    def validate_api_base_url(cls, value: str) -> str:
        value = value.strip()
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("api_base_url must be an HTTPS URL")
        return value

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            raise ValueError("api_key must not be empty")
        return value

    @model_validator(mode="after")
    def validate_bandwidth_order_range(self):
        if self.min_bandwidth_order_amount > self.max_bandwidth_order_amount:
            raise ValueError(
                "min_bandwidth_order_amount must be less than or equal to "
                "max_bandwidth_order_amount"
            )
        return self
```

- [ ] **Step 4: Update `Settings` fields and validation**

In `app/config.py`, add:

```python
from .profeex import ProfeeXConfig
```

Replace:

```python
ENERGY_SOURCE: Literal["staking", "refee"] = "staking"
REFEE: Json[RefeeConfig] | None = None
REFEE_FIXED_ENERGY_ORDER_AMOUNT: int = Field(65_000, ge=0)
REFEE_RENT_BANDWIDTH: bool = True
```

with:

```python
ENERGY_PROVIDER: Literal["staking", "refee"] = "staking"
BANDWIDTH_PROVIDER: Literal["disabled", "refee", "profeex"] = "disabled"
REFEE: Json[RefeeConfig] | None = None
PROFEEX: Json[ProfeeXConfig] | None = None
REFEE_FIXED_ENERGY_ORDER_AMOUNT: int = Field(65_000, ge=0)
```

Replace `validate_refee_config_state` with:

```python
@model_validator(mode="after")
def validate_resource_provider_config_state(self):
    if self.ENERGY_PROVIDER == "refee" and self.REFEE is None:
        raise ValueError("REFEE must be configured when ENERGY_PROVIDER='refee'")
    if self.BANDWIDTH_PROVIDER == "refee" and self.REFEE is None:
        raise ValueError("REFEE must be configured when BANDWIDTH_PROVIDER='refee'")
    if self.BANDWIDTH_PROVIDER == "profeex" and self.PROFEEX is None:
        raise ValueError("PROFEEX must be configured when BANDWIDTH_PROVIDER='profeex'")
    if (
        self.ENERGY_PROVIDER == "refee"
        and self.REFEE is not None
        and self.REFEE_FIXED_ENERGY_ORDER_AMOUNT > 0
        and self.REFEE_FIXED_ENERGY_ORDER_AMOUNT < self.REFEE.min_energy_order_amount
    ):
        raise ValueError(
            "REFEE_FIXED_ENERGY_ORDER_AMOUNT must be 0 or greater than or "
            "equal to REFEE.min_energy_order_amount"
        )
    return self
```

- [ ] **Step 5: Run config tests and verify pass**

Run:

```bash
uv run --python 3.12 --with-requirements requirements.txt python -m unittest \
  tests.test_resource_provider_config \
  tests.test_phase2_review_fixes.Phase2ReviewFixTests.test_refee_fixed_energy_amount_must_not_be_below_order_minimum \
  -v
```

Expected: all listed tests pass.

- [ ] **Step 6: Commit config changes**

```bash
git add app/config.py app/profeex.py tests/test_resource_provider_config.py tests/test_phase2_review_fixes.py
git commit -m "feat: configure independent resource providers"
```

## Task 2: Resource Provider Package and re:Fee Refactor

**Files:**
- Create: `app/resource_providers/__init__.py`
- Create: `app/resource_providers/base.py`
- Create: `app/resource_providers/staking.py`
- Create: `app/resource_providers/refee.py`
- Create: `app/resource_providers/factory.py`
- Modify: `app/energy_provider.py`
- Modify: `app/tasks.py`
- Modify tests that import `app.energy_provider`

- [ ] **Step 1: Write failing factory tests**

Create `tests/test_resource_provider_factory.py`:

```python
from types import SimpleNamespace
import unittest


class ResourceProviderFactoryTests(unittest.TestCase):
    def test_energy_factory_returns_refee_provider(self):
        from app.resource_providers import factory
        from app.resource_providers.refee import RefeeProvider

        original_config = factory.config
        try:
            factory.config = SimpleNamespace(ENERGY_PROVIDER="refee")
            provider = factory.get_energy_provider(tron_client=object())
        finally:
            factory.config = original_config

        self.assertIsInstance(provider, RefeeProvider)

    def test_bandwidth_factory_returns_none_when_disabled(self):
        from app.resource_providers import factory

        original_config = factory.config
        try:
            factory.config = SimpleNamespace(BANDWIDTH_PROVIDER="disabled")
            provider = factory.get_bandwidth_provider(tron_client=object())
        finally:
            factory.config = original_config

        self.assertIsNone(provider)

    def test_bandwidth_factory_returns_refee_provider(self):
        from app.resource_providers import factory
        from app.resource_providers.refee import RefeeProvider

        original_config = factory.config
        try:
            factory.config = SimpleNamespace(BANDWIDTH_PROVIDER="refee")
            provider = factory.get_bandwidth_provider(tron_client=object())
        finally:
            factory.config = original_config

        self.assertIsInstance(provider, RefeeProvider)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run factory tests and verify failure**

Run:

```bash
uv run --python 3.12 --with-requirements requirements.txt python -m unittest \
  tests.test_resource_provider_factory -v
```

Expected: fails because `app.resource_providers` does not exist.

- [ ] **Step 3: Add provider protocols**

Create `app/resource_providers/base.py`:

```python
from typing import Protocol


class EnergyProvider(Protocol):
    def acquire_energy(
        self,
        receiver: str,
        energy_to_provision: int,
        account_resource: dict,
        *,
        minimum_energy_required: int | None = None,
    ) -> bool:
        """Make enough TRON energy available for receiver."""

    def release_energy(self, receiver: str) -> None:
        """Release provider-owned energy resources when the provider requires it."""


class BandwidthProvider(Protocol):
    def acquire_bandwidth(self, receiver: str, bandwidth_required: int) -> bool:
        """Make enough TRON bandwidth available for receiver."""
```

- [ ] **Step 4: Move staking provider**

Create `app/resource_providers/staking.py` by moving the existing
`StakingEnergyProvider` code from `app/energy_provider.py`.

Make exactly these method-name changes while keeping the existing method bodies:

- The existing `acquire` method becomes `acquire_energy`; parameters and body
  stay the same.
- `def release(self, receiver: str) -> None` becomes
  `def release_energy(self, receiver: str) -> None`.
- `_calc_sun_for_energy_delegation` remains unchanged.

- [ ] **Step 5: Move re:Fee provider**

Create `app/resource_providers/refee.py` by moving the existing
`RefeeEnergyProvider` implementation from `app/energy_provider.py` into a class
named `RefeeProvider`.

Make exactly these method-name and class-name changes while keeping the existing
method bodies:

- `class RefeeEnergyProvider` becomes `class RefeeProvider`.
- The existing `acquire` method becomes `acquire_energy`; parameters and body
  stay the same.
- `def release(self, receiver: str) -> None` becomes
  `def release_energy(self, receiver: str) -> None`.
- `def acquire_bandwidth(self, receiver: str, bandwidth_required: int) -> bool`
  keeps the same name and body.
- `_create_order`, `_wait_until_delegated`, `_headers`, `_url`, and
  `_get_available_energy` keep the same names and behavior.

- [ ] **Step 6: Add factories and exports**

Create `app/resource_providers/factory.py`:

```python
from .base import BandwidthProvider, EnergyProvider
from .profeex import ProfeeXBandwidthProvider
from .refee import RefeeProvider
from .staking import StakingEnergyProvider
from ..config import config


def get_energy_provider(tron_client=None) -> EnergyProvider:
    if config.ENERGY_PROVIDER == "refee":
        return RefeeProvider(tron_client=tron_client)
    return StakingEnergyProvider(tron_client=tron_client)


def get_bandwidth_provider(tron_client=None) -> BandwidthProvider | None:
    if config.BANDWIDTH_PROVIDER == "disabled":
        return None
    if config.BANDWIDTH_PROVIDER == "refee":
        return RefeeProvider(tron_client=tron_client)
    if config.BANDWIDTH_PROVIDER == "profeex":
        return ProfeeXBandwidthProvider(tron_client=tron_client)
    raise ValueError(f"Unknown BANDWIDTH_PROVIDER={config.BANDWIDTH_PROVIDER!r}")
```

Temporarily create `app/resource_providers/profeex.py` with a stub so Task 2
can pass before Task 3:

```python
class ProfeeXBandwidthProvider:
    def __init__(self, tron_client=None):
        self.tron_client = tron_client

    def acquire_bandwidth(self, receiver: str, bandwidth_required: int) -> bool:
        raise NotImplementedError("ProfeeX bandwidth provider is implemented in Task 3")
```

Create `app/resource_providers/__init__.py`:

```python
from .base import BandwidthProvider, EnergyProvider
from .factory import get_bandwidth_provider, get_energy_provider
from .profeex import ProfeeXBandwidthProvider
from .refee import RefeeProvider
from .staking import StakingEnergyProvider

__all__ = [
    "BandwidthProvider",
    "EnergyProvider",
    "ProfeeXBandwidthProvider",
    "RefeeProvider",
    "StakingEnergyProvider",
    "get_bandwidth_provider",
    "get_energy_provider",
]
```

- [ ] **Step 7: Replace `app/energy_provider.py` with re-exports**

Replace `app/energy_provider.py` with:

```python
from .resource_providers import (
    BandwidthProvider,
    EnergyProvider,
    ProfeeXBandwidthProvider,
    RefeeProvider,
    StakingEnergyProvider,
    get_bandwidth_provider,
    get_energy_provider,
)

RefeeEnergyProvider = RefeeProvider

__all__ = [
    "BandwidthProvider",
    "EnergyProvider",
    "ProfeeXBandwidthProvider",
    "RefeeEnergyProvider",
    "RefeeProvider",
    "StakingEnergyProvider",
    "get_bandwidth_provider",
    "get_energy_provider",
]
```

This keeps existing tests importable while the internal code moves to the new
package.

- [ ] **Step 8: Rename energy provider calls in `tasks.py`**

In `app/tasks.py`, replace:

```python
if not provider.acquire(
    onetime_publ_key,
    energy_to_provision,
    onetime_address_resources,
    minimum_energy_required=energy_needed,
):
```

with:

```python
if not provider.acquire_energy(
    onetime_publ_key,
    energy_to_provision,
    onetime_address_resources,
    minimum_energy_required=energy_needed,
):
```

Replace:

```python
provider.release(onetime_publ_key)
```

with:

```python
provider.release_energy(onetime_publ_key)
```

- [ ] **Step 9: Update re:Fee tests for renamed methods and moved module**

In tests that use fake providers, replace `acquire` with `acquire_energy` and
`release` with `release_energy`. Keep `RefeeEnergyProvider` imports working
through the re-export for this implementation.

Example for `tests/test_refee_energy_accounting.py`:

```python
class RecordingProvider:
    def __init__(self, acquire_result=False):
        self.acquire_result = acquire_result
        self.acquire_calls = []
        self.release_calls = []

    def acquire_energy(self, *args, **kwargs):
        self.acquire_calls.append((args, kwargs))
        return self.acquire_result

    def release_energy(self, receiver):
        self.release_calls.append(receiver)
```

In `tests/test_phase2_review_fixes.py`, update request patching tests to patch
the moved module:

```python
from app.resource_providers import refee

provider = refee.RefeeProvider()
original_get = refee.requests.get
refee.requests.get = MockRequestGet()
try:
    order = provider._wait_until_delegated(
        RefeeSettings(), "order-1", {"id": "order-1", "status": "custom-success"}
    )
finally:
    refee.requests.get = original_get
```

Use the same `refee.requests.get` patch target for
`test_refee_poll_continues_after_transient_request_failure`.

- [ ] **Step 10: Run factory and existing re:Fee provider tests**

Run:

```bash
uv run --python 3.12 --with-requirements requirements.txt python -m unittest \
  tests.test_resource_provider_factory \
  tests.test_phase2_review_fixes \
  tests.test_refee_energy_accounting \
  tests.test_refee_bandwidth_guard \
  -v
```

Expected: tests pass after moved code and method rename are complete.

- [ ] **Step 11: Commit provider package refactor**

```bash
git add app/resource_providers app/energy_provider.py app/tasks.py tests/test_resource_provider_factory.py tests/test_phase2_review_fixes.py tests/test_refee_energy_accounting.py tests/test_refee_bandwidth_guard.py
git commit -m "refactor: split resource provider capabilities"
```

## Task 3: ProfeeX Bandwidth Provider

**Files:**
- Modify: `app/resource_providers/profeex.py`
- Test: `tests/test_profeex_bandwidth_provider.py`

- [ ] **Step 1: Write failing ProfeeX provider tests**

Create `tests/test_profeex_bandwidth_provider.py`:

```python
from types import SimpleNamespace
import unittest

from requests import RequestException


class FakeSecret:
    def get_secret_value(self):
        return "profeex-secret"


class FakeSettings:
    api_base_url = "https://api.profeex.test/api/v1"
    api_key = FakeSecret()
    currency = "TRX"
    bandwidth_duration_label = "1h"
    min_bandwidth_order_amount = 350
    max_bandwidth_order_amount = 10_000
    poll_interval_sec = 0.01
    timeout_sec = 0.05


class SequencedBandwidthTronClient:
    def __init__(self, resources):
        self.resources = list(resources)

    def get_account_resource(self, _address):
        if len(self.resources) > 1:
            return self.resources.pop(0)
        return self.resources[0]


class MockJsonResponse:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data
        self.text = str(data)

    def json(self):
        return self._data


class ProfeeXBandwidthProviderTests(unittest.TestCase):
    def patch_config(self, module):
        original_config = module.config
        module.config = SimpleNamespace(PROFEEX=FakeSettings())

        def restore():
            module.config = original_config

        return restore

    def test_rents_minimum_bandwidth_with_query_params_and_api_key(self):
        from app.resource_providers import profeex
        from app.resource_providers.profeex import ProfeeXBandwidthProvider

        client = SequencedBandwidthTronClient(
            [
                {"freeNetLimit": 600, "freeNetUsed": 600, "NetLimit": 0, "NetUsed": 0},
                {"freeNetLimit": 600, "freeNetUsed": 600, "NetLimit": 350, "NetUsed": 0},
            ]
        )
        provider = ProfeeXBandwidthProvider(tron_client=client)
        posts = []
        gets = []

        def fake_post(url, params, headers, timeout):
            posts.append((url, params, headers, timeout))
            return MockJsonResponse(202, {"task_id": "task-1", "status": "QUEUED"})

        def fake_get(url, headers, timeout):
            gets.append((url, headers, timeout))
            return MockJsonResponse(200, {"task_id": "task-1", "status": "ACTIVE"})

        original_post = profeex.requests.post
        original_get = profeex.requests.get
        restore_config = self.patch_config(profeex)
        try:
            profeex.requests.post = fake_post
            profeex.requests.get = fake_get
            acquired = provider.acquire_bandwidth(
                "TY4ZLVFpNhpozeWYSqWpcQjv6vntfHnjA7",
                346,
            )
        finally:
            profeex.requests.post = original_post
            profeex.requests.get = original_get
            restore_config()

        self.assertTrue(acquired)
        self.assertEqual(
            posts,
            [
                (
                    "https://api.profeex.test/api/v1/delegation/buybandwidth",
                    {
                        "target": "TY4ZLVFpNhpozeWYSqWpcQjv6vntfHnjA7",
                        "volume": 350,
                        "days": "1h",
                        "currency": "TRX",
                    },
                    {"X-API-Key": "profeex-secret"},
                    10,
                )
            ],
        )
        self.assertEqual(
            gets,
            [
                (
                    "https://api.profeex.test/api/v1/delegation/status/task-1",
                    {"X-API-Key": "profeex-secret"},
                    10,
                )
            ],
        )

    def test_skips_order_when_bandwidth_is_already_available(self):
        from app.resource_providers import profeex
        from app.resource_providers.profeex import ProfeeXBandwidthProvider

        client = SequencedBandwidthTronClient(
            [{"freeNetLimit": 600, "freeNetUsed": 0, "NetLimit": 0, "NetUsed": 0}]
        )
        provider = ProfeeXBandwidthProvider(tron_client=client)
        original_post = profeex.requests.post
        restore_config = self.patch_config(profeex)
        try:
            profeex.requests.post = lambda *args, **kwargs: self.fail("post not expected")
            self.assertTrue(provider.acquire_bandwidth("TADDR", 346))
        finally:
            profeex.requests.post = original_post
            restore_config()

    def test_fails_when_requested_bandwidth_exceeds_provider_maximum(self):
        from app.resource_providers import profeex
        from app.resource_providers.profeex import ProfeeXBandwidthProvider

        client = SequencedBandwidthTronClient(
            [{"freeNetLimit": 0, "freeNetUsed": 0, "NetLimit": 0, "NetUsed": 0}]
        )
        provider = ProfeeXBandwidthProvider(tron_client=client)
        original_post = profeex.requests.post
        restore_config = self.patch_config(profeex)
        try:
            profeex.requests.post = lambda *args, **kwargs: self.fail("post not expected")
            self.assertFalse(provider.acquire_bandwidth("TADDR", 10_001))
        finally:
            profeex.requests.post = original_post
            restore_config()

    def test_failed_status_returns_false(self):
        from app.resource_providers import profeex
        from app.resource_providers.profeex import ProfeeXBandwidthProvider

        client = SequencedBandwidthTronClient(
            [{"freeNetLimit": 0, "freeNetUsed": 0, "NetLimit": 0, "NetUsed": 0}]
        )
        provider = ProfeeXBandwidthProvider(tron_client=client)

        original_post = profeex.requests.post
        original_get = profeex.requests.get
        restore_config = self.patch_config(profeex)
        try:
            profeex.requests.post = lambda *args, **kwargs: MockJsonResponse(
                202, {"task_id": "task-1", "status": "QUEUED"}
            )
            profeex.requests.get = lambda *args, **kwargs: MockJsonResponse(
                200,
                {
                    "task_id": "task-1",
                    "status": "FAILED",
                    "error_code": "INSUFFICIENT_BALANCE",
                    "details": {"error_message": "not enough balance"},
                },
            )
            self.assertFalse(provider.acquire_bandwidth("TADDR", 350))
        finally:
            profeex.requests.post = original_post
            profeex.requests.get = original_get
            restore_config()

    def test_timeout_returns_false_after_transient_poll_failures(self):
        from app.resource_providers import profeex
        from app.resource_providers.profeex import ProfeeXBandwidthProvider

        client = SequencedBandwidthTronClient(
            [{"freeNetLimit": 0, "freeNetUsed": 0, "NetLimit": 0, "NetUsed": 0}]
        )
        provider = ProfeeXBandwidthProvider(tron_client=client)

        original_post = profeex.requests.post
        original_get = profeex.requests.get
        restore_config = self.patch_config(profeex)
        try:
            profeex.requests.post = lambda *args, **kwargs: MockJsonResponse(
                202, {"task_id": "task-1", "status": "QUEUED"}
            )
            profeex.requests.get = lambda *args, **kwargs: (_ for _ in ()).throw(
                RequestException("temporary")
            )
            self.assertFalse(provider.acquire_bandwidth("TADDR", 350))
        finally:
            profeex.requests.post = original_post
            profeex.requests.get = original_get
            restore_config()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run ProfeeX tests and verify failure**

Run:

```bash
uv run --python 3.12 --with-requirements requirements.txt python -m unittest \
  tests.test_profeex_bandwidth_provider -v
```

Expected: fails because `ProfeeXBandwidthProvider` is still a stub.

- [ ] **Step 3: Implement ProfeeX provider**

Replace `app/resource_providers/profeex.py` with:

```python
import time

import requests

from ..config import config
from ..connection_manager import ConnectionManager
from ..logging import logger
from ..utils import has_free_bw


class ProfeeXBandwidthProvider:
    REQUEST_TIMEOUT_SEC = 10
    PENDING_STATUSES = {"QUEUED", "PENDING", "PROCESSING"}
    SUCCESS_STATUSES = {"ACTIVE"}
    FAILURE_STATUSES = {"FAILED", "CANCELLED", "COMPLETED", "unknown"}

    def __init__(self, tron_client=None):
        self.tron_client = tron_client

    def acquire_bandwidth(self, receiver: str, bandwidth_required: int) -> bool:
        settings = config.PROFEEX
        if settings is None:
            logger.warning("PROFEEX config is missing. Terminating transfer.")
            return False

        tron_client = self.tron_client or ConnectionManager.client()
        if has_free_bw(receiver, bandwidth_required, tron_client=tron_client):
            logger.info(
                f"ProfeeX bandwidth order not needed for {receiver}: "
                f"{bandwidth_required=} already available"
            )
            return True

        amount = max(bandwidth_required, settings.min_bandwidth_order_amount)
        if amount > settings.max_bandwidth_order_amount:
            logger.warning(
                "ProfeeX bandwidth request exceeds provider maximum: "
                f"{amount=} max={settings.max_bandwidth_order_amount}"
            )
            return False

        order = self._create_bandwidth_order(settings, receiver, amount)
        if order is None:
            return False

        task_id = order.get("task_id")
        if not task_id:
            logger.warning(f"ProfeeX bandwidth order response has no task_id: {order}")
            return False

        active_order = self._wait_until_active(settings, task_id, order)
        if active_order is None:
            return False

        if not has_free_bw(receiver, bandwidth_required, tron_client=tron_client):
            logger.warning(
                "Onetime account has not enough bandwidth after ProfeeX delegation. "
                "Terminating transfer."
            )
            return False

        logger.info(f"ProfeeX bandwidth successfully delegated: {active_order}")
        return True

    def _create_bandwidth_order(self, settings, receiver: str, amount: int) -> dict | None:
        try:
            response = requests.post(
                self._url(settings, "/delegation/buybandwidth"),
                params={
                    "target": receiver,
                    "volume": amount,
                    "days": settings.bandwidth_duration_label,
                    "currency": settings.currency,
                },
                headers=self._headers(settings),
                timeout=self.REQUEST_TIMEOUT_SEC,
            )
        except requests.RequestException:
            logger.exception("ProfeeX create bandwidth order request failed")
            return None

        if response.status_code != 202:
            logger.warning(
                "ProfeeX create bandwidth order rejected with status "
                f"{response.status_code}: {response.text}"
            )
            return None

        try:
            data = response.json()
        except ValueError:
            logger.exception("ProfeeX create bandwidth order response is not valid JSON")
            return None
        if not isinstance(data, dict):
            logger.warning(f"ProfeeX create bandwidth order response is not an object: {data}")
            return None

        logger.info(f"ProfeeX bandwidth order accepted: {data}")
        return data

    def _wait_until_active(self, settings, task_id: str, initial_order: dict) -> dict | None:
        deadline = time.monotonic() + settings.timeout_sec
        order = initial_order
        last_status = None

        while time.monotonic() <= deadline:
            status = order.get("status")
            if status != last_status:
                logger.info(f"ProfeeX bandwidth order {task_id} status: {status}")
                last_status = status

            if status in self.SUCCESS_STATUSES:
                return order
            if status in self.FAILURE_STATUSES:
                logger.warning(f"ProfeeX bandwidth order {task_id} failed: {order}")
                return None

            time.sleep(settings.poll_interval_sec)

            try:
                response = requests.get(
                    self._url(settings, f"/delegation/status/{task_id}"),
                    headers=self._headers(settings),
                    timeout=self.REQUEST_TIMEOUT_SEC,
                )
            except requests.RequestException:
                logger.warning(f"ProfeeX poll request failed for bandwidth order {task_id}")
                continue

            if response.status_code != 200:
                logger.warning(
                    "ProfeeX poll for bandwidth order "
                    f"{task_id} returned status {response.status_code}: {response.text}"
                )
                continue

            try:
                order = response.json()
            except ValueError:
                logger.exception(
                    f"ProfeeX poll response is not valid JSON for bandwidth order {task_id}"
                )
                return None
            if not isinstance(order, dict):
                logger.warning(
                    f"ProfeeX poll response is not an object for bandwidth order {task_id}: {order}"
                )
                return None

        logger.warning(
            "ProfeeX bandwidth order "
            f"{task_id} did not reach ACTIVE status within {settings.timeout_sec} seconds"
        )
        return None

    @staticmethod
    def _headers(settings) -> dict:
        return {"X-API-Key": settings.api_key.get_secret_value()}

    @staticmethod
    def _url(settings, path: str) -> str:
        return f"{settings.api_base_url.rstrip('/')}{path}"
```

- [ ] **Step 4: Run ProfeeX tests and verify pass**

Run:

```bash
uv run --python 3.12 --with-requirements requirements.txt python -m unittest \
  tests.test_profeex_bandwidth_provider -v
```

Expected: all ProfeeX provider tests pass.

- [ ] **Step 5: Commit ProfeeX provider**

```bash
git add app/resource_providers/profeex.py tests/test_profeex_bandwidth_provider.py
git commit -m "feat: add ProfeeX bandwidth provider"
```

## Task 4: Independent Bandwidth Flow in `transfer_trc20_from`

**Files:**
- Modify: `app/tasks.py`
- Modify: `tests/test_refee_bandwidth_guard.py`
- Modify: `tests/test_refee_energy_accounting.py`

- [ ] **Step 1: Write failing task-flow tests**

Update `tests/test_refee_bandwidth_guard.py` fake config and provider patching:

```python
class FakeConfig:
    ENERGY_PROVIDER = "refee"
    BANDWIDTH_PROVIDER = "refee"
    ENERGY_DELEGATION_MODE = False
    ENERGY_DELEGATION_MODE_ALLOW_BURN_TRX_ON_PAYOUT = False
    ENERGY_DELEGATION_MODE_ALLOW_ADDITIONAL_ENERGY_DELEGATION = False
    BANDWIDTH_PER_TRC20_TRANSFER_CALL = 346

    def get_contract_address(self, symbol):
        self.last_contract_symbol = symbol
        return "TCONTRACT"

    def get_min_transfer_threshold(self, symbol):
        self.last_threshold_symbol = symbol
        return Decimal("1")


class DisabledBandwidthProviderConfig(FakeConfig):
    BANDWIDTH_PROVIDER = "disabled"
```

Update `FakeProvider` in the same file:

```python
class FakeProvider:
    def __init__(self):
        self.acquire_calls = 0
        self.acquire_bandwidth_calls = []

    def acquire_energy(self, *_args, **_kwargs):
        self.acquire_calls += 1
        return False

    def acquire_bandwidth(self, receiver, bandwidth_required):
        self.acquire_bandwidth_calls.append((receiver, bandwidth_required))
        return True
```

Patch both factories in each test:

```python
original_get_energy_provider = tasks.get_energy_provider
original_get_bandwidth_provider = tasks.get_bandwidth_provider
try:
    tasks.get_energy_provider = lambda tron_client=None: provider
    tasks.get_bandwidth_provider = lambda tron_client=None: provider
    result = tasks.transfer_trc20_from.run(onetime, "USDT")
finally:
    tasks.get_energy_provider = original_get_energy_provider
    tasks.get_bandwidth_provider = original_get_bandwidth_provider
```

Rename `test_refee_sweep_stops_without_renting_bandwidth_when_flag_is_disabled`
to `test_sweep_uses_existing_bandwidth_only_when_bandwidth_provider_disabled`.

Add a mixed-provider test:

```python
def test_sweep_can_use_different_energy_and_bandwidth_providers(self):
    from app import tasks
    from app.schemas import KeyType

    class MixedProviderConfig(FakeConfig):
        ENERGY_PROVIDER = "refee"
        BANDWIDTH_PROVIDER = "profeex"

    fee_deposit = "TRfonfrf1AqFzXqJTpad8Tz4EzvCBhZe5k"
    onetime = "TY4ZLVFpNhpozeWYSqWpcQjv6vntfHnjA7"
    client = FakeTronClient()
    energy_provider = FakeProvider()
    bandwidth_provider = FakeProvider()

    original_config = tasks.config
    original_connection_manager = tasks.ConnectionManager
    original_get_key = tasks.get_key
    original_get_energy_provider = tasks.get_energy_provider
    original_get_bandwidth_provider = tasks.get_bandwidth_provider
    try:
        tasks.config = MixedProviderConfig()
        tasks.ConnectionManager = SimpleNamespace(client=lambda: client)

        def fake_get_key(key_type, pub=None):
            if key_type == KeyType.fee_deposit:
                return object(), fee_deposit
            if key_type == KeyType.onetime:
                return object(), pub
            raise AssertionError(f"unexpected key type {key_type}")

        tasks.get_key = fake_get_key
        tasks.get_energy_provider = lambda tron_client=None: energy_provider
        tasks.get_bandwidth_provider = lambda tron_client=None: bandwidth_provider

        result = tasks.transfer_trc20_from.run(onetime, "USDT")
    finally:
        tasks.config = original_config
        tasks.ConnectionManager = original_connection_manager
        tasks.get_key = original_get_key
        tasks.get_energy_provider = original_get_energy_provider
        tasks.get_bandwidth_provider = original_get_bandwidth_provider

    self.assertIsNone(result)
    self.assertEqual(
        bandwidth_provider.acquire_bandwidth_calls,
        [(onetime, MixedProviderConfig.BANDWIDTH_PER_TRC20_TRANSFER_CALL)],
    )
    self.assertEqual(energy_provider.acquire_calls, 1)
```

- [ ] **Step 2: Run task-flow tests and verify failure**

Run:

```bash
uv run --python 3.12 --with-requirements requirements.txt python -m unittest \
  tests.test_refee_bandwidth_guard -v
```

Expected: fails because `tasks.py` still uses `ENERGY_SOURCE`, `REFEE_RENT_BANDWIDTH`, and gets bandwidth through the energy provider.

- [ ] **Step 3: Update task imports**

In `app/tasks.py`, replace:

```python
from .energy_provider import get_energy_provider
```

with:

```python
from .resource_providers import get_bandwidth_provider, get_energy_provider
```

- [ ] **Step 4: Add `ensure_onetime_bandwidth` helper**

Add near `_trc20_transfer_succeeded`:

```python
def ensure_onetime_bandwidth(onetime_publ_key: str, tron_client) -> bool:
    required_bandwidth = config.BANDWIDTH_PER_TRC20_TRANSFER_CALL
    logger.info("Check onetime account bandwidth before energy provisioning")
    if has_free_bw(onetime_publ_key, required_bandwidth, tron_client=tron_client):
        logger.info("Onetime account has enough bandwidth")
        return True

    if config.BANDWIDTH_PROVIDER == "disabled":
        logger.warning(
            "One-time account has no bandwidth and external bandwidth rental "
            "is disabled. Leaving sweep for a later retry after TRON bandwidth recovery."
        )
        return False

    bandwidth_provider = get_bandwidth_provider(tron_client=tron_client)
    if bandwidth_provider is None:
        logger.warning(
            "One-time account has no bandwidth and no bandwidth provider is configured."
        )
        return False

    logger.info(
        "One-time account has no bandwidth. "
        f"Requesting {config.BANDWIDTH_PROVIDER} bandwidth before energy provisioning."
    )
    if not bandwidth_provider.acquire_bandwidth(onetime_publ_key, required_bandwidth):
        logger.warning(
            "One-time account has no bandwidth after provider rental. "
            "Terminating transfer before energy provisioning."
        )
        return False

    return True
```

- [ ] **Step 5: Replace energy provider selection in `transfer_trc20_from`**

Replace:

```python
use_refee_energy_provider = config.ENERGY_SOURCE == "refee"
use_staking_energy_provider = (
    config.ENERGY_SOURCE == "staking" and config.ENERGY_DELEGATION_MODE
)
use_energy_provider = use_refee_energy_provider or use_staking_energy_provider
```

with:

```python
use_refee_energy_provider = config.ENERGY_PROVIDER == "refee"
use_staking_energy_provider = (
    config.ENERGY_PROVIDER == "staking" and config.ENERGY_DELEGATION_MODE
)
use_energy_provider = use_refee_energy_provider or use_staking_energy_provider
```

Replace the provider log:

```python
logger.info(f"Using energy provider source: {config.ENERGY_SOURCE}")
```

with:

```python
logger.info(f"Using energy provider: {config.ENERGY_PROVIDER}")
```

- [ ] **Step 6: Replace inline bandwidth rental branch**

Replace the current block that checks onetime bandwidth and calls
`provider.acquire_bandwidth(onetime_publ_key, config.BANDWIDTH_PER_TRC20_TRANSFER_CALL)`
with:

```python
if not ensure_onetime_bandwidth(onetime_publ_key, tron_client):
    return
```

Keep the later pre-broadcast bandwidth recheck in place. It protects against
provider success responses that do not actually leave enough on-chain bandwidth.

- [ ] **Step 7: Verify energy provider method calls use the new names**

Run:

```bash
rg -n "provider\\.acquire\\(|provider\\.release\\(" app/tasks.py tests
```

Expected: no matches.

- [ ] **Step 8: Update energy accounting test fakes**

In `tests/test_refee_energy_accounting.py`, update `FakeConfig`:

```python
class FakeConfig:
    ENERGY_PROVIDER = "refee"
    BANDWIDTH_PROVIDER = "disabled"
    ENERGY_DELEGATION_MODE = False
    ENERGY_DELEGATION_MODE_ALLOW_BURN_TRX_ON_PAYOUT = False
    ENERGY_DELEGATION_MODE_ALLOW_ADDITIONAL_ENERGY_DELEGATION = False
    BANDWIDTH_PER_TRC20_TRANSFER_CALL = 346
    TX_FEE_LIMIT = Decimal("50")
```

Update `FakeStakingConfig`:

```python
class FakeStakingConfig(FakeConfig):
    ENERGY_PROVIDER = "staking"
    ENERGY_DELEGATION_MODE = True
    BANDWIDTH_PER_DELEGE_CALL = 1
    BANDWIDTH_PER_UNDELEGATE_CALL = 1
    BANDWIDTH_PER_TRX_TRANSFER = 1
    ENERGY_DELEGATION_MODE_ALLOW_BURN_TRX_FOR_BANDWITH = False
```

Patch and restore `tasks.get_bandwidth_provider` in `patch_tasks`:

```python
original_get_bandwidth_provider = tasks.get_bandwidth_provider

tasks.get_bandwidth_provider = lambda tron_client=None: None

def restore():
    tasks.config = original_config
    tasks.ConnectionManager = original_connection_manager
    tasks.get_key = original_get_key
    tasks.get_energy_delegator = original_get_energy_delegator
    tasks.get_energy_provider = original_get_energy_provider
    tasks.get_bandwidth_provider = original_get_bandwidth_provider
```

- [ ] **Step 9: Run task-flow and accounting tests**

Run:

```bash
uv run --python 3.12 --with-requirements requirements.txt python -m unittest \
  tests.test_refee_bandwidth_guard \
  tests.test_refee_energy_accounting \
  -v
```

Expected: all listed tests pass.

- [ ] **Step 10: Commit task flow changes**

```bash
git add app/tasks.py tests/test_refee_bandwidth_guard.py tests/test_refee_energy_accounting.py
git commit -m "feat: route bandwidth through independent provider"
```

## Task 5: Documentation and Full Verification

**Files:**
- Modify: `docs/DEPLOYMENT.md`

- [ ] **Step 1: Update deployment env example**

In `docs/DEPLOYMENT.md`, replace the Helm env block:

```yaml
tron_shkeeper:
  image: ghcr.io/nilof470/tron-shkeeper:REPLACE_WITH_TAG
  extraEnv:
    ENERGY_SOURCE: refee
    REFEE: '{"api_key":"REPLACE_WITH_REFEE_API_KEY","rent_duration_label":"1h"}'
    REFEE_FIXED_ENERGY_ORDER_AMOUNT: "65000"
    ENERGY_DELEGATION_MODE_ALLOW_BURN_TRX_ON_PAYOUT: "false"
    ENERGY_DELEGATION_MODE_ALLOW_BURN_TRX_FOR_BANDWITH: "true"
    USDT_MIN_TRANSFER_THRESHOLD: "0.5"
    TRX_MIN_TRANSFER_THRESHOLD: "1.01"
```

with:

```yaml
tron_shkeeper:
  image: ghcr.io/nilof470/tron-shkeeper:REPLACE_WITH_TAG
  extraEnv:
    ENERGY_PROVIDER: refee
    BANDWIDTH_PROVIDER: profeex
    REFEE: '{"api_key":"REPLACE_WITH_REFEE_API_KEY","rent_duration_label":"1h"}'
    PROFEEX: '{"api_key":"REPLACE_WITH_PROFEEX_API_KEY","bandwidth_duration_label":"1h"}'
    REFEE_FIXED_ENERGY_ORDER_AMOUNT: "65000"
    ENERGY_DELEGATION_MODE_ALLOW_BURN_TRX_ON_PAYOUT: "false"
    ENERGY_DELEGATION_MODE_ALLOW_BURN_TRX_FOR_BANDWITH: "true"
    USDT_MIN_TRANSFER_THRESHOLD: "0.5"
    TRX_MIN_TRANSFER_THRESHOLD: "1.01"
```

- [ ] **Step 2: Update deployment notes**

Add these notes below the env block:

```markdown
- `ENERGY_PROVIDER=refee` rents TRC20 transfer energy from re:Fee.
- `BANDWIDTH_PROVIDER=profeex` rents onetime-wallet bandwidth from ProfeeX only
  when the wallet does not already have enough bandwidth for the TRC20 transfer.
- `BANDWIDTH_PROVIDER=disabled` preserves the old behavior: the sweep uses only
  bandwidth already available on the onetime wallet and retries naturally after
  TRON restores daily bandwidth.
- ProfeeX ordinary bandwidth rental uses `/api/v1/delegation/buybandwidth`.
  Flash bandwidth is not used because it requires the target address to have
  its own consumed staked bandwidth.
```

Replace later `ENERGY_SOURCE` mentions with `ENERGY_PROVIDER`. Replace
`REFEE_RENT_BANDWIDTH` mentions if any appear after the implementation.

- [ ] **Step 3: Run focused tests**

Run:

```bash
uv run --python 3.12 --with-requirements requirements.txt python -m unittest \
  tests.test_resource_provider_config \
  tests.test_resource_provider_factory \
  tests.test_profeex_bandwidth_provider \
  tests.test_refee_bandwidth_guard \
  tests.test_refee_energy_accounting \
  tests.test_phase2_review_fixes \
  -v
```

Expected: all focused tests pass.

- [ ] **Step 4: Run full test suite**

Run:

```bash
uv run --python 3.12 --with-requirements requirements.txt python -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 5: Run compile check**

Run:

```bash
uv run --python 3.12 --with-requirements requirements.txt python -m compileall app tests
```

Expected: compileall completes without syntax errors.

- [ ] **Step 6: Commit documentation and verification cleanup**

```bash
git add docs/DEPLOYMENT.md
git commit -m "docs: document ProfeeX bandwidth configuration"
```

## Final Manual Review Checklist

- [ ] `rg -n "ENERGY_SOURCE|REFEE_RENT_BANDWIDTH" app tests docs` returns no active config references.
- [ ] `rg -n "flashbandwidth" app tests docs` shows no implementation call; documentation may mention that it is intentionally unused.
- [ ] `BANDWIDTH_PROVIDER=disabled` path checks existing wallet bandwidth and makes no external API call.
- [ ] `BANDWIDTH_PROVIDER=profeex` path does not call ProfeeX when wallet bandwidth is already sufficient.
- [ ] ProfeeX create-order request uses a `params` dict with `target`, `volume`, `days`, and `currency`; it does not use `json=`.
- [ ] ProfeeX success requires `ACTIVE` plus an on-chain `has_free_bw` recheck.
- [ ] Energy provider selection still supports `staking` and `refee`.
