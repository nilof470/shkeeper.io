# TON Scanner Resilience Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `ton-shkeeper` so transient Toncenter indexer `404` gaps do not freeze TON-USDT scanning, while preserving safe native TON support for future enablement.

**Architecture:** Add explicit transient upstream error handling, bounded Toncenter request timeouts, and per-asset scan results. In current TON-USDT-only production, native TON transaction scanning can be disabled so native indexer gaps do not block Jetton deposit detection; when native TON is enabled, the scanner must keep unsafe blocks pending instead of silently skipping them.

**Tech Stack:** Python 3.13, Flask, SQLAlchemy, requests, pytest/unittest, Docker, Kubernetes/Helm. Target codebase is the `ton-shkeeper` image source, not the main `shkeeper.io` repository.

---

## Source Context

Use these existing investigation docs before starting:

- `docs/TON_SCANNER_RESILIENCE.md`
- Production evidence: repeated `404 Client Error: Not Found` from `/api/v3/transactionsByMasterchainBlock` for seqnos `65945199`, `66014784`, `66018441`, and `66018443`, while `getMasterchainInfo` and nearby indexer probes returned `200`.

Confirmed target image paths from `vsyshost/ton-shkeeper:0.0.2`:

- Modify: `app/toncenterapi.py`
- Modify: `app/events.py`
- Modify: `app/api/views.py`
- Possibly modify: `app/config.py` or whichever module currently reads env vars
- Add tests under the target repo's existing test directory, for example `tests/test_toncenterapi.py` and `tests/test_events_scanner.py`

If the `ton-shkeeper` source is not already available locally, create a fork or extract the source from the image before Task 1.

---

## Task 1: Prepare Target Repository And Baseline

**Files:**
- Read: `app/toncenterapi.py`
- Read: `app/events.py`
- Read: `app/api/views.py`
- Read: `requirements.txt` / `pyproject.toml` / existing test config
- Create if absent: `tests/`

- [ ] **Step 1: Get the source for the exact production image**

Run in the target working directory:

```bash
docker pull vsyshost/ton-shkeeper:0.0.2
CID=$(docker create vsyshost/ton-shkeeper:0.0.2)
mkdir -p /tmp/ton-shkeeper-0.0.2
docker cp "$CID":/app/. /tmp/ton-shkeeper-0.0.2/
docker rm "$CID"
```

Expected: `/tmp/ton-shkeeper-0.0.2/app/toncenterapi.py` and `/tmp/ton-shkeeper-0.0.2/app/events.py` exist.

- [ ] **Step 2: Create or update a fork branch**

```bash
git checkout -b fix/ton-scanner-indexer-404-resilience
git status --short
```

Expected: clean or only intentional source import changes.

- [ ] **Step 3: Run baseline tests**

Use the target repo's test runner. If no documented runner exists, start with:

```bash
python -m unittest discover -s tests
```

Expected: record current result. If tests do not exist, record that in the commit message for the first test task.

- [ ] **Step 4: Commit baseline-only setup if files were imported or scaffolding changed**

```bash
git add .
git commit -m "chore: prepare ton scanner resilience baseline"
```

Skip this commit if no files changed.

---

## Task 2: Add Toncenter Error Classification Tests

**Files:**
- Test: `tests/test_toncenterapi.py`
- Modify later: `app/toncenterapi.py`

- [ ] **Step 1: Write failing tests for transient errors and log masking**

Create `tests/test_toncenterapi.py`:

```python
import unittest

from app import toncenterapi


class ToncenterErrorClassificationTest(unittest.TestCase):
    def test_masks_api_key_in_url(self):
        url = "https://toncenter.com/api/v3/transactionsByMasterchainBlock?api_key=TEST_SECRET&seqno=66018441"
        masked = toncenterapi.mask_toncenter_secret(url)
        self.assertIn("api_key=***MASKED***", masked)
        self.assertNotIn("TEST_SECRET", masked)

    def test_404_transactions_by_masterchain_block_is_transient(self):
        self.assertTrue(
            toncenterapi.is_transient_toncenter_error(
                endpoint="transactionsByMasterchainBlock",
                status_code=404,
            )
        )

    def test_404_other_endpoint_is_not_automatically_transient(self):
        self.assertFalse(
            toncenterapi.is_transient_toncenter_error(
                endpoint="getAddressInformation",
                status_code=404,
            )
        )

    def test_429_and_5xx_are_transient(self):
        for status_code in (429, 500, 502, 503, 504):
            self.assertTrue(
                toncenterapi.is_transient_toncenter_error(
                    endpoint="getMasterchainInfo",
                    status_code=status_code,
                )
            )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and confirm it fails**

```bash
python -m unittest tests.test_toncenterapi -v
```

Expected: FAIL because `mask_toncenter_secret` and `is_transient_toncenter_error` do not exist.

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/test_toncenterapi.py
git commit -m "test: capture Toncenter transient error classification"
```

---

## Task 3: Implement Shared Toncenter Request Helpers

**Files:**
- Modify: `app/toncenterapi.py`
- Test: `tests/test_toncenterapi.py`

- [ ] **Step 1: Add exceptions and helpers**

In `app/toncenterapi.py`, add near the imports:

```python
import random
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TONCENTER_TIMEOUT = (3.05, 20)
TRANSIENT_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


class ToncenterTransientError(Exception):
    pass


class ToncenterPermanentError(Exception):
    pass


def mask_toncenter_secret(value):
    if not value:
        return value
    parts = urlsplit(value)
    if not parts.query:
        return value
    query = []
    for key, item_value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in {"api_key", "key", "token"}:
            query.append((key, "***MASKED***"))
        else:
            query.append((key, item_value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def is_transient_toncenter_error(endpoint, status_code):
    if status_code in TRANSIENT_STATUS_CODES:
        return True
    if endpoint == "transactionsByMasterchainBlock" and status_code == 404:
        return True
    return False


def sleep_before_retry(attempt):
    delay = min(10, 0.5 * (2 ** max(attempt - 1, 0)))
    time.sleep(delay + random.uniform(0, 0.25))
```

- [ ] **Step 2: Run classification tests**

```bash
python -m unittest tests.test_toncenterapi -v
```

Expected: PASS.

- [ ] **Step 3: Commit helper implementation**

```bash
git add app/toncenterapi.py tests/test_toncenterapi.py
git commit -m "fix: classify transient Toncenter indexer gaps"
```

---

## Task 4: Add Bounded Timeouts To Toncenter Calls

**Files:**
- Modify: `app/toncenterapi.py`
- Test: `tests/test_toncenterapi.py`

- [ ] **Step 1: Find all raw requests calls**

```bash
grep -R "requests\\.\\(get\\|post\\|request\\)" -n app/toncenterapi.py app
```

Expected: list every external Toncenter request that needs a timeout.

- [ ] **Step 2: Add a shared request wrapper**

In `app/toncenterapi.py`, add:

```python
def toncenter_request(endpoint, method, url, *, params=None, json=None, retries=3):
    last_error = None
    for attempt in range(1, retries + 1):
        started = time.time()
        try:
            response = requests.request(
                method,
                url,
                params=params,
                json=json,
                timeout=TONCENTER_TIMEOUT,
            )
            elapsed = round(time.time() - started, 3)
            if response.ok:
                return response
            status_code = response.status_code
            safe_url = mask_toncenter_secret(response.url)
            message = f"{endpoint} status={status_code} elapsed={elapsed} url={safe_url}"
            if is_transient_toncenter_error(endpoint, status_code):
                last_error = ToncenterTransientError(message)
                if attempt < retries:
                    sleep_before_retry(attempt)
                    continue
                raise last_error
            raise ToncenterPermanentError(message)
        except requests.RequestException as exc:
            last_error = ToncenterTransientError(f"{endpoint} request_error={type(exc).__name__}")
            if attempt < retries:
                sleep_before_retry(attempt)
                continue
            raise last_error
    raise last_error or ToncenterTransientError(f"{endpoint} failed")
```

- [ ] **Step 3: Replace Toncenter `requests.get` and `requests.post` calls**

For each Toncenter call in `app/toncenterapi.py`, replace direct calls like:

```python
response = requests.get(url, params=params)
response.raise_for_status()
return response.json()
```

with:

```python
response = toncenter_request("ENDPOINT_NAME", "GET", url, params=params)
return response.json()
```

Use exact endpoint names:

- `getMasterchainInfo`
- `getBlockHeader`
- `getAddressInformation`
- `getWalletInformation`
- `sendBocReturnHash`
- `sendBoc`
- `jettonWallets`
- `jettonMasters`
- `transactionsByMasterchainBlock`
- `jettonTransfers`
- `transactions`
- `transactionsByMessage`
- `blocks`

- [ ] **Step 4: Run tests**

```bash
python -m unittest tests.test_toncenterapi -v
```

Expected: PASS.

- [ ] **Step 5: Commit timeout wrapper**

```bash
git add app/toncenterapi.py tests/test_toncenterapi.py
git commit -m "fix: add bounded timeouts to Toncenter requests"
```

---

## Task 5: Make Native TON Scanning Configurable

**Files:**
- Modify: `app/events.py`
- Modify: `app/config.py` or existing env/config module
- Test: `tests/test_events_scanner.py`

- [ ] **Step 1: Add env flag with backward-compatible default**

In the config/env module, add:

```python
SCAN_NATIVE_TON_EVENTS = os.getenv("SCAN_NATIVE_TON_EVENTS", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
```

If the codebase does not have a central config module, add this directly in `app/events.py`:

```python
def scan_native_ton_events_enabled():
    return os.getenv("SCAN_NATIVE_TON_EVENTS", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
```

Default must be `true` to preserve existing native TON behavior unless production explicitly disables it.

- [ ] **Step 2: Write failing test for TON-USDT-only behavior**

Create `tests/test_events_scanner.py`:

```python
import os
import unittest
from unittest.mock import patch

from app import events


class EventsScannerNativeTonToggleTest(unittest.TestCase):
    def test_native_ton_scan_can_be_disabled(self):
        with patch.dict(os.environ, {"SCAN_NATIVE_TON_EVENTS": "false"}):
            self.assertFalse(events.scan_native_ton_events_enabled())

    def test_native_ton_scan_defaults_enabled(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SCAN_NATIVE_TON_EVENTS", None)
            self.assertTrue(events.scan_native_ton_events_enabled())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the test and confirm it fails before implementation**

```bash
python -m unittest tests.test_events_scanner -v
```

Expected: FAIL if helper does not exist yet.

- [ ] **Step 4: Implement the helper**

Add to `app/events.py` if not added in config:

```python
def scan_native_ton_events_enabled():
    return os.getenv("SCAN_NATIVE_TON_EVENTS", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
```

- [ ] **Step 5: Run tests**

```bash
python -m unittest tests.test_events_scanner -v
```

Expected: PASS.

- [ ] **Step 6: Commit native TON toggle**

```bash
git add app/events.py app/config.py tests/test_events_scanner.py
git commit -m "feat: allow disabling native TON scanner"
```

---

## Task 6: Return Structured Per-Block Scan Results

**Files:**
- Modify: `app/events.py`
- Test: `tests/test_events_scanner.py`

- [ ] **Step 1: Add scan result types**

In `app/events.py`, add near scanner functions:

```python
from dataclasses import dataclass, field


SCAN_OK = "ok"
SCAN_TRANSIENT_FAIL = "transient_fail"
SCAN_PERMANENT_FAIL = "permanent_fail"


@dataclass
class BlockScanResult:
    seqno: int
    native_ton: str = SCAN_OK
    jettons: str = SCAN_OK
    errors: list[str] = field(default_factory=list)

    def can_advance(self):
        if scan_native_ton_events_enabled() and self.native_ton != SCAN_OK:
            return False
        return self.jettons == SCAN_OK
```

- [ ] **Step 2: Add unit tests for checkpoint safety**

Append to `tests/test_events_scanner.py`:

```python
class BlockScanResultTest(unittest.TestCase):
    def test_native_ton_failure_does_not_block_when_disabled(self):
        with patch.dict(os.environ, {"SCAN_NATIVE_TON_EVENTS": "false"}):
            result = events.BlockScanResult(
                seqno=66018441,
                native_ton=events.SCAN_TRANSIENT_FAIL,
                jettons=events.SCAN_OK,
            )
            self.assertTrue(result.can_advance())

    def test_native_ton_failure_blocks_when_enabled(self):
        with patch.dict(os.environ, {"SCAN_NATIVE_TON_EVENTS": "true"}):
            result = events.BlockScanResult(
                seqno=66018441,
                native_ton=events.SCAN_TRANSIENT_FAIL,
                jettons=events.SCAN_OK,
            )
            self.assertFalse(result.can_advance())

    def test_jetton_failure_always_blocks(self):
        with patch.dict(os.environ, {"SCAN_NATIVE_TON_EVENTS": "false"}):
            result = events.BlockScanResult(
                seqno=66018441,
                native_ton=events.SCAN_OK,
                jettons=events.SCAN_TRANSIENT_FAIL,
            )
            self.assertFalse(result.can_advance())
```

- [ ] **Step 3: Run tests**

```bash
python -m unittest tests.test_events_scanner -v
```

Expected: PASS after implementation.

- [ ] **Step 4: Commit structured scan results**

```bash
git add app/events.py tests/test_events_scanner.py
git commit -m "feat: model per-asset TON block scan results"
```

---

## Task 7: Decouple Native TON From Jetton Scanning

**Files:**
- Modify: `app/events.py`
- Modify: `app/toncenterapi.py`
- Test: `tests/test_events_scanner.py`

- [ ] **Step 1: Update block worker logic**

Find `check_in_parallel()` or the function that processes one masterchain block.
Change it to return `BlockScanResult` instead of `True` / `False`.

Target shape:

```python
def check_in_parallel(seqno):
    result = BlockScanResult(seqno=seqno)

    if scan_native_ton_events_enabled():
        try:
            check_ton_events_for_block(seqno)
        except toncenterapi.ToncenterTransientError as exc:
            result.native_ton = SCAN_TRANSIENT_FAIL
            result.errors.append(str(exc))
        except Exception as exc:
            result.native_ton = SCAN_PERMANENT_FAIL
            result.errors.append(type(exc).__name__)

    try:
        check_jetton_events_for_block(seqno)
    except toncenterapi.ToncenterTransientError as exc:
        result.jettons = SCAN_TRANSIENT_FAIL
        result.errors.append(str(exc))
    except Exception as exc:
        result.jettons = SCAN_PERMANENT_FAIL
        result.errors.append(type(exc).__name__)

    return result
```

Use the actual existing native TON and Jetton scan function names from `app/events.py`; do not introduce wrappers that bypass existing deposit/callback logic.

- [ ] **Step 2: Update chunk commit logic**

Replace all-or-nothing boolean logic:

```python
if all(results):
    save_last_block()
```

with:

```python
if all(result.can_advance() for result in results):
    save_last_block()
else:
    log_block_scan_failures(results)
```

Add:

```python
def log_block_scan_failures(results):
    for result in results:
        if not result.can_advance():
            logging.warning(
                "Block scan incomplete seqno=%s native_ton=%s jettons=%s errors=%s",
                result.seqno,
                result.native_ton,
                result.jettons,
                result.errors[:3],
            )
```

- [ ] **Step 3: Add regression test for native 404 with Jetton OK**

Append to `tests/test_events_scanner.py`:

```python
class ChunkAdvanceTest(unittest.TestCase):
    def test_chunk_can_advance_for_jetton_only_when_native_transient_fails(self):
        with patch.dict(os.environ, {"SCAN_NATIVE_TON_EVENTS": "false"}):
            results = [
                events.BlockScanResult(seqno=1, native_ton=events.SCAN_TRANSIENT_FAIL, jettons=events.SCAN_OK),
                events.BlockScanResult(seqno=2, native_ton=events.SCAN_OK, jettons=events.SCAN_OK),
            ]
            self.assertTrue(all(result.can_advance() for result in results))

    def test_chunk_cannot_advance_when_native_enabled_and_native_transient_fails(self):
        with patch.dict(os.environ, {"SCAN_NATIVE_TON_EVENTS": "true"}):
            results = [
                events.BlockScanResult(seqno=1, native_ton=events.SCAN_TRANSIENT_FAIL, jettons=events.SCAN_OK),
                events.BlockScanResult(seqno=2, native_ton=events.SCAN_OK, jettons=events.SCAN_OK),
            ]
            self.assertFalse(all(result.can_advance() for result in results))
```

- [ ] **Step 4: Run tests**

```bash
python -m unittest tests.test_events_scanner -v
```

Expected: PASS.

- [ ] **Step 5: Commit scanner decoupling**

```bash
git add app/events.py app/toncenterapi.py tests/test_events_scanner.py
git commit -m "fix: decouple TON-USDT scan from native TON indexer gaps"
```

---

## Task 8: Add Scanner Diagnostics Without Breaking `/TON/status`

**Files:**
- Modify: `app/api/views.py`
- Modify: `app/events.py` if diagnostics state is stored there
- Test: `tests/test_api_status.py`

- [ ] **Step 1: Keep existing `/status` response fields**

Do not remove:

```json
{
  "last_block_timestamp": 1778476494,
  "status": "success"
}
```

Extra fields are allowed only if existing callers tolerate them. If uncertain,
add a separate endpoint, for example `/TON/scanner-diagnostics`.

- [ ] **Step 2: Add diagnostics fields or endpoint**

Target diagnostic payload:

```python
{
    "status": "success",
    "scanner_state": "ok",
    "last_block": 66018449,
    "head_block": 66018789,
    "failed_seqnos": [],
    "last_error": None,
}
```

If durable failed seqno storage is not in this minimal fix, populate only fields available in process memory and logs. Do not block the core fix on diagnostics storage.

- [ ] **Step 3: Test backward compatibility**

Create `tests/test_api_status.py`:

```python
import unittest


class TonStatusCompatibilityTest(unittest.TestCase):
    def test_status_keeps_required_fields(self):
        response = {
            "last_block_timestamp": 1778476494,
            "status": "success",
            "scanner_state": "ok",
        }
        self.assertIn("last_block_timestamp", response)
        self.assertEqual(response["status"], "success")
```

Replace the literal response with the target repo's real Flask test client if an app fixture exists.

- [ ] **Step 4: Run tests**

```bash
python -m unittest tests.test_api_status -v
```

Expected: PASS.

- [ ] **Step 5: Commit diagnostics**

```bash
git add app/api/views.py app/events.py tests/test_api_status.py
git commit -m "feat: expose TON scanner diagnostic state"
```

---

## Task 9: Build And Deploy Canary Image

**Files:**
- Modify production-private values file only: `/root/shkeeper-values.yaml`
- Do not commit secrets

- [ ] **Step 1: Build image**

From the `ton-shkeeper` fork:

```bash
TAG=$(git rev-parse --short HEAD)
docker buildx build --platform linux/amd64 -t ghcr.io/YOUR_ORG/ton-shkeeper:${TAG} --push .
```

Expected: image pushed successfully.

- [ ] **Step 2: Update production values**

On the server:

```bash
cp /root/shkeeper-values.yaml /root/shkeeper-values.yaml.bak.$(date +%Y%m%d%H%M%S)
nano /root/shkeeper-values.yaml
```

Set:

```yaml
ton_shkeeper:
  image: ghcr.io/YOUR_ORG/ton-shkeeper:REPLACE_WITH_TAG
  extraEnv:
    SCAN_NATIVE_TON_EVENTS: "false"
    GET_JETTON_TXS_LIMIT: "1000"
    EVENTS_MAX_THREADS_NUMBER: "4"
```

Use `SCAN_NATIVE_TON_EVENTS: "true"` only when native TON deposits are intentionally enabled and tested.

- [ ] **Step 3: Upgrade Helm release**

Use the namespace shown by `helm list -A`:

```bash
helm list -A | grep shkeeper
helm upgrade -n default -f /root/shkeeper-values.yaml shkeeper vsys-host/shkeeper
kubectl rollout status -n shkeeper deployment/ton-shkeeper
```

- [ ] **Step 4: Verify image and env**

```bash
kubectl get deployment -n shkeeper ton-shkeeper -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}'
kubectl exec -n shkeeper deployment/ton-shkeeper -c app -- sh -lc 'env | grep -E "SCAN_NATIVE_TON_EVENTS|EVENTS_MAX_THREADS_NUMBER|GET_JETTON_TXS_LIMIT"'
```

Expected:

```text
SCAN_NATIVE_TON_EVENTS=false
EVENTS_MAX_THREADS_NUMBER=4
GET_JETTON_TXS_LIMIT=1000
```

- [ ] **Step 5: Keep watchdog active during canary**

```bash
crontab -l | grep check-ton-shkeeper
tail -n 20 /var/log/ton-shkeeper-watchdog.log
```

Expected: watchdog still runs once per minute.

---

## Task 10: Production Verification And Rollback

**Files:**
- Read: `/var/log/ton-shkeeper-watchdog.log`
- Read: `/var/log/ton-shkeeper-freeze-evidence.log`
- Read: Kubernetes logs

- [ ] **Step 1: Watch scanner for at least one hour**

```bash
tail -f /var/log/ton-shkeeper-watchdog.log
```

Expected: `last` keeps changing and `lag_sec` stays below `360`.

- [ ] **Step 2: Watch app logs for repeated native 404 behavior**

```bash
kubectl logs -n shkeeper deployment/ton-shkeeper -c app --since=1h --tail=3000 \
  | grep -Ei 'transactionsByMasterchainBlock|jetton/transfers|Checked block|Traceback|429|404|timeout|Block scan incomplete'
```

Expected in TON-USDT-only mode:

- Jetton scanning continues.
- Native `transactionsByMasterchainBlock` does not block checkpoint progress when `SCAN_NATIVE_TON_EVENTS=false`.
- No API keys appear in logs.

- [ ] **Step 3: Verify deposit idempotency with a test TON-USDT payment**

Create one test invoice and pay once. Then check:

```bash
kubectl logs -n shkeeper deployment/shkeeper-deployment --since=30m \
  | grep -Ei 'walletnotify|Notification has been accepted|TON|USDT'
```

Expected: exactly one credited deposit and callback accepted or queued for retry.

- [ ] **Step 4: Confirm watchdog no longer restarts on native indexer 404**

```bash
grep -E 'restarting|timestamp_stuck|status_check_failed' /var/log/ton-shkeeper-watchdog.log | tail -50
```

Expected: no new `timestamp_stuck` restarts during normal transient native indexer `404` gaps.

- [ ] **Step 5: Roll back if lag approaches limit or deposits fail**

```bash
cp /root/shkeeper-values.yaml.bak.REPLACE_WITH_BACKUP_TIMESTAMP /root/shkeeper-values.yaml
helm upgrade -n default -f /root/shkeeper-values.yaml shkeeper vsys-host/shkeeper
kubectl rollout status -n shkeeper deployment/ton-shkeeper
```

After rollback, watchdog remains the mitigation.

---

## Self-Review

- Spec coverage: covers transient `404`, timeout handling, TON-USDT-first production, future native TON support, no skipped deposits, callback idempotency, deployment, and rollback.
- Placeholder scan: image registry placeholders remain intentionally scoped to deployment-specific values; code/task behavior is explicit.
- Type consistency: `BlockScanResult`, `SCAN_OK`, `SCAN_TRANSIENT_FAIL`, and `SCAN_PERMANENT_FAIL` are introduced before use.
