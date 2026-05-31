# TON Scanner Review Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the production-safety gaps found in the TON scanner resilience review before using the forked TON image in production.

**Architecture:** Split Toncenter HTTP behavior by operation class, keep scanner reads retryable, make broadcast writes non-retried, and keep deposit scanning bounded and observable. TON-USDT remains the production path; native TON is kept safe for future enablement by adding pagination and by making the native-scan mode explicit in deployment.

**Tech Stack:** Python 3.13, Flask, Celery, requests, unittest, Helm/Kubernetes runbook docs.

---

## Validation Summary

| Issue | Verdict | Evidence | Required fix |
| --- | --- | --- | --- |
| Native TON `404` can still block TON-USDT when native scan is enabled | Confirmed, conditional on `SCAN_NATIVE_TON_EVENTS=true` | `SCAN_NATIVE_TON_EVENTS` defaults to true in `app/config.py`; `check_in_parallel()` returns before Jetton scan on native `ToncenterTransientError`; checkpoint only advances when all results can advance | Keep production config explicit with `SCAN_NATIVE_TON_EVENTS=false`; also stop early-returning before Jetton scan so future native mode can process Jettons idempotently even while native is pending |
| Broadcast POSTs inherit scanner retry policy | Confirmed | `send_message()` and `send_message_with_hash()` call `toncenter_request()` with default retries | Make broadcast calls single-attempt/non-retry by default; document ambiguous submission behavior |
| Native TON block transaction scan lacks pagination | Confirmed against Toncenter docs | `/api/v3/transactionsByMasterchainBlock` has `limit` default `10`, max `1000`; code calls without `limit` or `offset` | Add `GET_NATIVE_TON_TXS_LIMIT=1000` and paginate until a short page |
| `walletnotify_shkeeper()` can block scanner forever | Confirmed | Inline `while True` has no request timeout and no bounded attempt count | Add timeout and bounded attempts; return failure to mark block scan incomplete instead of hanging a worker forever |
| `CURRENT_TON_NETWORK=main` missing from production runbook | Confirmed | code defaults to `testnet`; docs set mainnet Toncenter URLs but not network | Add `CURRENT_TON_NETWORK: "main"` to values and verification commands |
| Error body can leak `api_key` | Confirmed | `_response_error_message()` masks URL but not response body | Mask response body before logging and add regression test |
| Tests do not cover scanner flow | Confirmed | Current tests cover helper decisions only | Add unit tests for no POST retries, native pagination, masked body, bounded walletnotify, and native-disabled Jetton path |

## Files

- Modify: `/Users/test/PycharmProjects/ton-shkeeper/app/config.py`
- Modify: `/Users/test/PycharmProjects/ton-shkeeper/app/toncenterapi.py`
- Modify: `/Users/test/PycharmProjects/ton-shkeeper/app/events.py`
- Modify: `/Users/test/PycharmProjects/ton-shkeeper/tests/test_toncenterapi.py`
- Modify: `/Users/test/PycharmProjects/ton-shkeeper/tests/test_events_scanner.py`
- Modify: `/Users/test/PycharmProjects/shkeeper.io/docs/DEPLOYMENT.md`
- Modify: `/Users/test/PycharmProjects/shkeeper.io/docs/TON_SCANNER_RESILIENCE.md`

## Task 1: Make Toncenter HTTP Policy Safe By Operation Type

**Files:**
- Modify: `app/toncenterapi.py`
- Modify: `tests/test_toncenterapi.py`

- [ ] **Step 1: Add failing tests for non-retried broadcast POSTs and masked response bodies**

Add tests equivalent to:

```python
def test_send_boc_timeout_is_not_retried(self):
    calls = []

    def fake_request(*args, **kwargs):
        calls.append((args, kwargs))
        raise toncenterapi.rq.Timeout("accepted but response lost")

    old_request = toncenterapi.rq.request
    toncenterapi.rq.request = fake_request
    try:
        with self.assertRaises(toncenterapi.ToncenterTransientError):
            toncenterapi.toncenter_request(
                "sendBoc",
                "POST",
                "https://toncenter.com/api/v2/sendBoc",
                json={"boc": "abc"},
                params={"api_key": "SECRET"},
                retry_transient=False,
            )
    finally:
        toncenterapi.rq.request = old_request

    self.assertEqual(len(calls), 1)


def test_response_error_message_masks_api_key_in_body(self):
    response = FakeResponse(
        status_code=500,
        ok=False,
        url="https://toncenter.com/api/v3/x?api_key=URL_SECRET",
        text="proxy echoed https://toncenter.com/api/v3/x?api_key=BODY_SECRET",
    )

    message = toncenterapi._response_error_message("x", response)

    self.assertNotIn("URL_SECRET", message)
    self.assertNotIn("BODY_SECRET", message)
    self.assertIn("***MASKED***", message)
```

- [ ] **Step 2: Run targeted tests and confirm they fail**

Run:

```bash
cd /Users/test/PycharmProjects/ton-shkeeper
.venv/bin/python -m unittest tests.test_toncenterapi -v
```

Expected: the two new tests fail because `toncenter_request()` has no `retry_transient` parameter and the body is not masked.

- [ ] **Step 3: Implement endpoint-aware retry behavior**

Change `toncenter_request()` signature and internals to:

```python
def toncenter_request(
    endpoint,
    method,
    url,
    *,
    params=None,
    json=None,
    headers=None,
    retries=None,
    retry_transient=True,
    timeout=TONCENTER_TIMEOUT,
):
    if retries is None:
        retries = TONCENTER_RETRIES if retry_transient else 1

    last_error = None

    for attempt in range(1, retries + 1):
        try:
            response = rq.request(
                method,
                url,
                params=params,
                json=json,
                headers=headers,
                timeout=timeout,
            )
        except rq.RequestException as e:
            message = (
                f"Toncenter {endpoint} request failed on attempt "
                f"{attempt}/{retries}: {mask_toncenter_secret(str(e))}"
            )
            last_error = ToncenterTransientError(message)
            logger.warning(message)
        else:
            if response.ok:
                return response

            message = _response_error_message(endpoint, response)
            if retry_transient and is_transient_toncenter_error(endpoint, response.status_code):
                last_error = ToncenterTransientError(message)
                logger.warning(
                    f"Toncenter transient error on attempt {attempt}/{retries}: {message}"
                )
            else:
                raise ToncenterPermanentError(message)

        if retry_transient and attempt < retries:
            sleep_before_retry(attempt)

    if last_error is not None:
        raise last_error
    raise ToncenterTransientError(f"Toncenter {endpoint} failed without response")
```

Also change `_response_error_message()` body handling:

```python
body = mask_toncenter_secret(body[:300].replace('\n', ' '))
```

- [ ] **Step 4: Make broadcast methods non-retry**

In `send_message()` and `send_message_with_hash()`, call:

```python
response = toncenter_request(
    'sendBocReturnHash',
    'POST',
    f'{self.api_url}/api/v2/sendBocReturnHash',
    json={"boc": signed_boc},
    headers={'accept': 'application/json', 'Content-Type': 'application/json'},
    params={'api_key': self.api_key},
    retry_transient=False,
)
```

Use the same `retry_transient=False` pattern for `sendBoc`.

- [ ] **Step 5: Verify**

Run:

```bash
cd /Users/test/PycharmProjects/ton-shkeeper
.venv/bin/python -m unittest tests.test_toncenterapi -v
```

Expected: all `test_toncenterapi` tests pass.

## Task 2: Add Native TON Pagination

**Files:**
- Modify: `app/config.py`
- Modify: `app/toncenterapi.py`
- Modify: `tests/test_toncenterapi.py`

- [ ] **Step 1: Add failing pagination test**

Add a test for `get_all_transactions_by_masterchain_seqno()` that returns exactly one full page and then one short page:

```python
def test_transactions_by_masterchain_block_paginates(self):
    calls = []

    class Response:
        ok = True
        status_code = 200
        url = "https://toncenter.com/api/v3/transactionsByMasterchainBlock"
        text = "{}"

        def __init__(self, transactions):
            self._transactions = transactions

        def json(self):
            return {"transactions": self._transactions}

    def fake_request(method, url, params=None, **kwargs):
        calls.append(params.copy())
        if params["offset"] == 0:
            return Response([{"hash": "a"}, {"hash": "b"}])
        return Response([{"hash": "c"}])

    old_limit = toncenterapi.config["GET_NATIVE_TON_TXS_LIMIT"]
    old_request = toncenterapi.rq.request
    toncenterapi.config["GET_NATIVE_TON_TXS_LIMIT"] = 2
    toncenterapi.rq.request = fake_request
    try:
        api = toncenterapi.Toncenterapi()
        result = api.get_all_transactions_by_masterchain_seqno(123)
    finally:
        toncenterapi.config["GET_NATIVE_TON_TXS_LIMIT"] = old_limit
        toncenterapi.rq.request = old_request

    self.assertEqual(result, [{"hash": "a"}, {"hash": "b"}, {"hash": "c"}])
    self.assertEqual(calls[0]["limit"], 2)
    self.assertEqual(calls[0]["offset"], 0)
    self.assertEqual(calls[1]["offset"], 2)
```

- [ ] **Step 2: Run and confirm failure**

Run:

```bash
cd /Users/test/PycharmProjects/ton-shkeeper
.venv/bin/python -m unittest tests.test_toncenterapi -v
```

Expected: pagination test fails because current native scan only performs one request.

- [ ] **Step 3: Add config**

In `app/config.py`, add:

```python
'GET_NATIVE_TON_TXS_LIMIT': int(os.environ.get('GET_NATIVE_TON_TXS_LIMIT', '1000')),
```

- [ ] **Step 4: Implement pagination**

Replace `get_all_transactions_by_masterchain_seqno()` with:

```python
def get_all_transactions_by_masterchain_seqno(self, seqno):
    end_transactions = False
    request_counter = 0
    all_transactions = []
    limit = int(config['GET_NATIVE_TON_TXS_LIMIT'])

    while not end_transactions:
        response = toncenter_request(
            'transactionsByMasterchainBlock',
            'GET',
            f'{self.indexer_url}/api/v3/transactionsByMasterchainBlock',
            params={
                'api_key': self.indexer_key,
                'seqno': seqno,
                'limit': limit,
                'offset': request_counter * limit,
            },
            headers=self.headers,
        )
        request_counter += 1
        transactions = response.json()['transactions']
        all_transactions.extend(transactions)
        if len(transactions) < limit:
            end_transactions = True

    return all_transactions
```

- [ ] **Step 5: Verify**

Run:

```bash
cd /Users/test/PycharmProjects/ton-shkeeper
.venv/bin/python -m unittest tests.test_toncenterapi -v
```

Expected: all `test_toncenterapi` tests pass.

## Task 3: Bound SHKeeper Walletnotify Without Losing Deposit Safety

**Files:**
- Modify: `app/config.py`
- Modify: `app/events.py`
- Modify: `tests/test_events_scanner.py`

- [ ] **Step 1: Add failing tests for timeout and bounded retry**

Add tests that monkeypatch `events.rq.post` and `events.time.sleep`:

```python
def test_walletnotify_uses_timeout_and_stops_after_configured_attempts(self):
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(kwargs)
        raise events.rq.Timeout("slow shkeeper")

    old_post = events.rq.post
    old_sleep = events.time.sleep
    old_attempts = events.config.get("WALLETNOTIFY_MAX_ATTEMPTS")
    old_timeout = events.config.get("WALLETNOTIFY_TIMEOUT_SECONDS")
    events.rq.post = fake_post
    events.time.sleep = lambda seconds: None
    events.config["WALLETNOTIFY_MAX_ATTEMPTS"] = 2
    events.config["WALLETNOTIFY_TIMEOUT_SECONDS"] = 3
    try:
        result = events.walletnotify_shkeeper("TON-USDT", "abc")
    finally:
        events.rq.post = old_post
        events.time.sleep = old_sleep
        events.config["WALLETNOTIFY_MAX_ATTEMPTS"] = old_attempts
        events.config["WALLETNOTIFY_TIMEOUT_SECONDS"] = old_timeout

    self.assertFalse(result)
    self.assertEqual(len(calls), 2)
    self.assertEqual(calls[0]["timeout"], 3)
```

- [ ] **Step 2: Run and confirm failure**

Run:

```bash
cd /Users/test/PycharmProjects/ton-shkeeper
.venv/bin/python -m unittest tests.test_events_scanner -v
```

Expected: test fails because `walletnotify_shkeeper()` loops forever and does not pass a timeout.

- [ ] **Step 3: Add config**

In `app/config.py`, add:

```python
'WALLETNOTIFY_TIMEOUT_SECONDS': int(os.environ.get('WALLETNOTIFY_TIMEOUT_SECONDS', '10')),
'WALLETNOTIFY_MAX_ATTEMPTS': int(os.environ.get('WALLETNOTIFY_MAX_ATTEMPTS', '3')),
```

- [ ] **Step 4: Implement bounded notification**

Replace `walletnotify_shkeeper()` with:

```python
def walletnotify_shkeeper(symbol, txid) -> bool:
    """Notify SHKeeper about transaction."""
    logger.warning(f"Notifying about {symbol}/{txid}")
    max_attempts = int(config.get('WALLETNOTIFY_MAX_ATTEMPTS', 3))
    timeout = int(config.get('WALLETNOTIFY_TIMEOUT_SECONDS', 10))

    for attempt in range(1, max_attempts + 1):
        try:
            r = rq.post(
                f'http://{config["SHKEEPER_HOST"]}/api/v1/walletnotify/{symbol}/{txid}',
                headers={'X-Shkeeper-Backend-Key': config['SHKEEPER_KEY']},
                timeout=timeout,
            ).json()
            if r["status"] == "success":
                logger.warning(f"The notification about {symbol}/{txid} was successful")
                return True
            logger.warning(
                f"Failed to notify SHKeeper about {symbol}/{txid} "
                f"on attempt {attempt}/{max_attempts}, received response: {r}"
            )
            time.sleep(5)
        except Exception as e:
            logger.warning(
                f'Shkeeper notification failed for {symbol}/{txid} '
                f'on attempt {attempt}/{max_attempts}: {e}'
            )
            time.sleep(10)

    logger.warning(f"Giving up notification about {symbol}/{txid} after {max_attempts} attempts")
    return False
```

- [ ] **Step 5: Propagate notification failure to block scan result**

In both native and Jetton scan branches, replace bare calls like:

```python
walletnotify_shkeeper(token, base64.b64decode(transaction['transaction_hash']).hex())
```

with:

```python
notified = walletnotify_shkeeper(token, base64.b64decode(transaction['transaction_hash']).hex())
if not notified:
    raise ToncenterTransientError(f"walletnotify failed for {token}/{transaction['transaction_hash']}")
```

This keeps deposit safety: if SHKeeper cannot be notified, the block is not checkpointed as processed.

- [ ] **Step 6: Verify**

Run:

```bash
cd /Users/test/PycharmProjects/ton-shkeeper
.venv/bin/python -m unittest tests.test_events_scanner -v
```

Expected: all `test_events_scanner` tests pass.

## Task 4: Keep Jetton Scan From Being Skipped By Native Failure

**Files:**
- Modify: `app/events.py`
- Modify: `tests/test_events_scanner.py`

- [ ] **Step 1: Add a focused scanner helper test**

Before changing the loop, extract block scanning into a helper so it can be tested:

```python
def scan_block(block, toncenterapi, list_accounts, drain_account_task):
    result = BlockScanResult(block=block)
    ...
    return result
```

Then add a test where native scan raises `ToncenterTransientError`, Jetton scan returns one transfer, and `walletnotify_shkeeper()` is called once.

- [ ] **Step 2: Run and confirm failure**

Run:

```bash
cd /Users/test/PycharmProjects/ton-shkeeper
.venv/bin/python -m unittest tests.test_events_scanner -v
```

Expected: test fails before extraction/behavior change.

- [ ] **Step 3: Extract `scan_block()` from nested `check_in_parallel()`**

Move the body of `check_in_parallel()` into module-level `scan_block()`. Keep behavior identical except for the next step.

- [ ] **Step 4: Do not return immediately after native scan failure**

Change native failure handling from:

```python
except ToncenterTransientError as e:
    result.native_ton = SCAN_TRANSIENT_FAILURE
    result.native_error = str(e)
    logger.warning(...)
    return result
```

to:

```python
except ToncenterTransientError as e:
    result.native_ton = SCAN_TRANSIENT_FAILURE
    result.native_error = str(e)
    logger.warning(f'Block {block}: transient native TON scan failure: {e}')
```

Do the same for generic native exceptions. The Jetton scan should still run. `can_advance_checkpoint()` will still prevent checkpoint advancement when native scan is enabled and failed.

- [ ] **Step 5: Verify behavior**

Run:

```bash
cd /Users/test/PycharmProjects/ton-shkeeper
.venv/bin/python -m unittest tests.test_events_scanner -v
```

Expected: Jetton scan/callback occurs even when native scan fails, but `result.can_advance_checkpoint()` is still false when `SCAN_NATIVE_TON_EVENTS=true`.

## Task 5: Fix Production Runbook Defaults And Verification

**Files:**
- Modify: `/Users/test/PycharmProjects/shkeeper.io/docs/DEPLOYMENT.md`
- Modify: `/Users/test/PycharmProjects/shkeeper.io/docs/TON_SCANNER_RESILIENCE.md`

- [ ] **Step 1: Add mainnet network env to every TON production values snippet**

Add:

```yaml
CURRENT_TON_NETWORK: "main"
GET_NATIVE_TON_TXS_LIMIT: "1000"
WALLETNOTIFY_TIMEOUT_SECONDS: "10"
WALLETNOTIFY_MAX_ATTEMPTS: "3"
```

near the existing TON env block that contains `SCAN_NATIVE_TON_EVENTS`, `EVENTS_MAX_THREADS_NUMBER`, and `GET_JETTON_TXS_LIMIT`.

- [ ] **Step 2: Clarify native TON mode**

Add this text near the TON operational notes:

```markdown
For TON-USDT-only production, keep `SCAN_NATIVE_TON_EVENTS=false`.
This means native TON invoices must not be offered to customers in that mode.
If native TON deposits are enabled later, set `SCAN_NATIVE_TON_EVENTS=true`
only after native scan pagination and stuck-block handling are deployed and tested.
```

- [ ] **Step 3: Fix log grep for Cyrillic/ASCII checked-block spelling**

Replace grep pattern:

```text
Checked block
```

with:

```text
[СC]hecked block
```

This matches both Cyrillic `С` and ASCII `C`.

- [ ] **Step 4: Add pull-secret preflight to TON GHCR rollout**

Add:

```bash
kubectl get secret ghcr-nilof470 -n shkeeper
helm get values shkeeper -n shkeeper -o yaml | grep -A5 imagePullSecrets
```

before the `helm upgrade` command.

- [ ] **Step 5: Verify docs formatting**

Run:

```bash
cd /Users/test/PycharmProjects/shkeeper.io
git diff --check docs/DEPLOYMENT.md docs/TON_SCANNER_RESILIENCE.md
```

Expected: no output.

## Task 6: Full Verification And Release Gate

**Files:**
- No code edits expected.

- [ ] **Step 1: Run all unit tests**

Run:

```bash
cd /Users/test/PycharmProjects/ton-shkeeper
.venv/bin/python -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 2: Compile Python files**

Run:

```bash
cd /Users/test/PycharmProjects/ton-shkeeper
.venv/bin/python -m compileall app tests
```

Expected: no syntax errors.

- [ ] **Step 3: Check whitespace**

Run:

```bash
cd /Users/test/PycharmProjects/ton-shkeeper
git diff --check
```

Expected: no output.

- [ ] **Step 4: Build amd64 image**

Run:

```bash
cd /Users/test/PycharmProjects/ton-shkeeper
docker buildx build --platform linux/amd64 -t ghcr.io/nilof470/ton-shkeeper:review-remediation-amd64 --load .
```

Expected: build succeeds.

- [ ] **Step 5: Production canary checks after deploy**

Run on VPS after Helm upgrade:

```bash
kubectl exec -n shkeeper deployment/ton-shkeeper -c app -- sh -c \
  'env | egrep "^(CURRENT_TON_NETWORK|SCAN_NATIVE_TON_EVENTS|EVENTS_MAX_THREADS_NUMBER|GET_JETTON_TXS_LIMIT|GET_NATIVE_TON_TXS_LIMIT|WALLETNOTIFY_TIMEOUT_SECONDS|WALLETNOTIFY_MAX_ATTEMPTS)="'

tail -n 30 /var/log/ton-shkeeper-watchdog.log
grep -E 'timestamp_stuck|status_check_failed|restarting' /var/log/ton-shkeeper-watchdog.log | tail -20
```

Expected for TON-USDT-only prod:

```text
CURRENT_TON_NETWORK=main
SCAN_NATIVE_TON_EVENTS=false
GET_JETTON_TXS_LIMIT=1000
GET_NATIVE_TON_TXS_LIMIT=1000
```

No watchdog restart during the first one-hour canary window.

---

## Recommended Execution Order

1. Task 1 first because broadcast retry can affect funds movement.
2. Task 5 runbook env fix before any production rollout.
3. Task 3 to remove hidden infinite scanner thread hangs.
4. Task 2 native pagination before enabling native TON deposits.
5. Task 4 if native TON support is needed soon; otherwise keep `SCAN_NATIVE_TON_EVENTS=false` and treat Task 4 as hardening.
6. Task 6 before building and pushing a new production image.
