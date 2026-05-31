# TON Scanner Resilience Fix Plan

This document records the production failure mode observed in
`vsyshost/ton-shkeeper:0.0.2` and the concrete fix direction for making TON and
TON-USDT scanning resilient without risking missed deposits.

Do not paste real Toncenter keys, wallet passwords, or customer callback URLs
into this file or into diagnostic logs.

## Current Production Issue

`ton-shkeeper` can stop advancing its scanner checkpoint while the pod remains
healthy:

- Kubernetes shows `ton-shkeeper` as `3/3 Running`.
- `/TON/status` returns HTTP `200`.
- `last_block_timestamp` stops changing.
- The app logs stop printing regular `Checked block ...` progress lines.
- A rollout restart makes scanning continue.

The confirmed 2026-05-11 incident showed repeated Toncenter indexer `404`
responses for specific masterchain seqnos:

```text
Cannot get all transactions from 66018441 block, 404 Client Error: Not Found
Cannot get all transactions from 66018443 block, 404 Client Error: Not Found
```

During the same evidence window, `getMasterchainInfo` returned HTTP `200` and a
later indexer probe returned HTTP `200`. That means the failure was not a full
Toncenter outage. It was a transient or per-block indexer gap for specific
masterchain blocks.

Additional evidence collected by the watchdog confirms the same pattern across
multiple incidents:

| Time UTC | Repeated failing seqno(s) | Evidence summary |
| --- | --- | --- |
| 2026-05-10 21:00 | `65945199` | Repeated `404` from `20:57:51` through `20:59:53`; `getMasterchainInfo` and a nearby indexer probe returned `200`. |
| 2026-05-11 04:52 | `66014784` | Repeated `404` from `04:49:50` through `04:51:52`; `getMasterchainInfo` and a nearby indexer probe returned `200`. |
| 2026-05-11 05:17 | `66018441`, `66018443` | Repeated `404` from `05:14:55` through `05:16:57`; `getMasterchainInfo` and a nearby indexer probe returned `200`. |

These are evidence-backed scanner stalls, not just single slow checks. Earlier
watchdog restarts before evidence collection show the same operational symptom,
but do not have enough preserved app logs to classify with the same confidence.

## Why Restart Helps

Restarting the pod does not intentionally skip the problem block.

The scanner checkpoint is stored in `ton-shkeeper` state, for example
`Settings.last_block`. On restart, the new pod reads the saved checkpoint and
starts scanning from that point again. The restart helps because the external
indexer often starts returning data for the previously failing seqno by the time
the new pod retries it.

Expected behavior during recovery:

```text
before restart: block 66018441 -> indexer 404 -> chunk fails -> checkpoint stuck
after restart:  block 66018441 -> indexer 200 -> processed -> checkpoint moves
```

If the upstream indexer continues returning `404`, the scanner can loop on the
same checkpoint after each restart. That was observed on 2026-05-10, where
multiple restarts happened before the scanner eventually advanced.

## Root Cause

The production investigation confirmed this code-level failure mode from the
`vsyshost/ton-shkeeper:0.0.2` image:

- `/app/app/toncenterapi.py`
  - `get_all_transactions_by_masterchain_seqno()` calls
    `/api/v3/transactionsByMasterchainBlock`.
  - It uses `raise_for_status()`.
  - It retries three times with 10 second sleeps.
  - After repeated failure, it does not produce a successful block result.
- `/app/app/events.py`
  - `check_in_parallel()` returns `False` for a block whose native TON scan
    fails.
  - The scanner commits `Settings.last_block` only when `all(results)` for the
    parallel chunk is true.
- `/app/app/api/views.py`
  - `/status` reads the stored checkpoint timestamp, so the HTTP endpoint can
    stay healthy while the scanner is stuck.

The simplified current behavior is:

```python
results = scan_blocks_in_parallel(blocks)

if all(results):
    save_last_block()
else:
    retry_same_chunk_again()
```

This is safe against silently skipping blocks, but it lets one transient indexer
gap stop the whole scanner.

## Product Constraints

- Production maximum acceptable TON lag is `360` seconds.
- TON-USDT is the primary production asset.
- Native TON deposits are not required right now, but the design must allow
  enabling native TON later.
- Deposit detection is more important than payout convenience.
- A fix must not silently skip blocks that may contain deposits.
- Customer callbacks must remain idempotent. Restarts may delay callbacks, but
  must not create duplicate credited deposits.
- Watchdog restart remains an emergency mitigation until the scanner itself is
  resilient.

## Fix Principles

1. Never advance the global checkpoint past a block whose enabled asset scans
   are incomplete.
2. Treat Toncenter `404` for a recent existing masterchain block as a transient
   indexer gap, not as proof that the block is empty.
3. Decouple native TON and Jetton scans. Native TON failure must not block
   TON-USDT deposit processing when native TON support is disabled.
4. Keep deposit processing idempotent. Reprocessing a block after restart or
   retry must not double-credit deposits.
5. Sanitize all logs. Never log `api_key=...` or `TONCENTER_*_KEY`.
6. Add bounded HTTP timeouts everywhere. No external HTTP request should be able
   to block a scanner worker indefinitely.

## Proposed Scanner Model

Introduce explicit per-block and per-asset scan results.

```python
class ScanStatus:
    OK = "ok"
    TRANSIENT_FAIL = "transient_fail"
    PERMANENT_FAIL = "permanent_fail"

class BlockScanResult:
    seqno: int
    native_ton: ScanStatus
    jettons: ScanStatus
    errors: list[str]
```

For current TON-USDT-only production:

- `jettons=OK` is required before TON-USDT events for the block are considered
  processed.
- `native_ton` should be `OK` without calling
  `transactionsByMasterchainBlock` when native TON scanning is disabled.
- A native TON indexer `404` must not block Jetton deposits if native TON is
  disabled.

For future native TON support:

- `native_ton=OK` must be required before advancing the global checkpoint.
- If native TON scan returns transient `404`, the scanner must keep the block
  pending and retry it.
- If Jetton scan succeeds while native TON is pending, Jetton events can still
  be processed idempotently, but the checkpoint must not advance past the
  pending native block unless replay state is persisted.

## Minimal Safe Code Fix

### 1. Add a shared Toncenter HTTP client

All Toncenter calls should go through one wrapper:

```python
def toncenter_request(method, url, *, params=None, json=None, key_name=None):
    response = session.request(
        method,
        url,
        params=params,
        json=json,
        timeout=(3.05, 20),
    )
    return response
```

Required behavior:

- connect timeout: about `3` seconds
- read timeout: about `20` seconds
- sanitized error logs
- retry with jittered backoff for transient statuses
- metrics/log fields: endpoint name, seqno, attempt, status code, elapsed time

Transient statuses:

- `404` for recent `transactionsByMasterchainBlock` seqnos
- `408`
- `409`
- `425`
- `429`
- `500`
- `502`
- `503`
- `504`
- connection reset
- read/connect timeout

### 2. Disable native TON scan when native TON is not enabled

Current production mainly needs TON-USDT. The scanner should not require native
TON transaction reads unless native TON deposits are enabled.

Required behavior:

```python
if not native_ton_enabled:
    native_result = ScanStatus.OK
else:
    native_result = scan_native_ton_block(seqno)
```

The implementation must verify the real config flag used by the chart/app. Do
not infer native TON enablement from wallet existence alone.

### 3. Keep Jetton scan independent

TON-USDT scanning should use `/api/v3/jetton/transfers` with the configured LT
range and `GET_JETTON_TXS_LIMIT=1000`.

If native TON scan is disabled or transiently failing:

- still scan Jetton transfers for the block;
- process matching TON-USDT deposits idempotently;
- write enough state/logging so reprocessing the same block is safe.

### 4. Do not treat indexer 404 as empty without verification

For native TON enabled mode, a `404` from
`transactionsByMasterchainBlock` should become `TRANSIENT_FAIL`.

The scanner may only mark the native block as empty if another verified source
confirms there are no relevant transactions for that masterchain block. If no
verified source is available, keep the block pending and alert.

### 5. Advance checkpoint only when safe

For TON-USDT-only production:

```python
can_advance = all(block.jettons == OK for block in chunk)
```

For native TON plus TON-USDT:

```python
can_advance = all(
    block.native_ton == OK and block.jettons == OK
    for block in chunk
)
```

If future implementation stores durable per-block replay state, this can be
relaxed, but only after proving replay cannot miss deposits.

## Preferred Long-Term Fix

The robust architecture is a cursor plus gap queue:

- Keep the main checkpoint as the lowest unclosed block.
- Store transiently failed seqnos in a durable `scanner_gaps` table.
- Continue processing newer blocks for enabled assets where safe.
- Retry gaps with backoff.
- Alert if the oldest gap age approaches the 360 second business limit.

This avoids full scanner stalls while preserving no-skip semantics.

Suggested state:

```text
seqno
asset_scope        native_ton | jetton:<master_address>
status             pending | processing | done | transient_failed
attempt_count
last_error_code
last_error_message_sanitized
next_retry_at
created_at
updated_at
```

Checkpoint rule:

```text
global checkpoint = highest contiguous seqno where all enabled asset scopes are done
```

## Acceptance Criteria

- A transient `404` from `/api/v3/transactionsByMasterchainBlock` no longer
  freezes TON-USDT deposit scanning when native TON is disabled.
- When native TON is enabled, transient native TON indexer gaps do not silently
  skip blocks.
- No Toncenter request can hang indefinitely.
- Logs never include `api_key` values or `TONCENTER_*_KEY` values.
- Reprocessing the same block does not double-credit deposits.
- `/TON/status` or a new diagnostics endpoint exposes enough scanner state to
  distinguish:
  - healthy scanner,
  - catching up,
  - transient upstream gap,
  - stuck scanner.
- Watchdog should become a last-resort recovery mechanism, not the normal way
  to clear indexer gaps.

## Verification Plan

Unit tests:

- `transactionsByMasterchainBlock` returns `404` once, then `200`: scanner
  retries and advances.
- `transactionsByMasterchainBlock` returns repeated `404` with native TON
  disabled: TON-USDT block scan still succeeds.
- `transactionsByMasterchainBlock` returns repeated `404` with native TON
  enabled: scanner does not advance past the block.
- Toncenter request timeout raises a transient scanner error, not an unbounded
  hang.
- Logs mask API keys.

Integration tests:

- Simulate a chunk with blocks `[A, B, C]` where native scan for `B` returns
  `404` and Jetton scan returns valid TON-USDT deposit data.
- Confirm TON-USDT callback is sent exactly once.
- Confirm restart and replay do not duplicate the deposit.
- Confirm checkpoint behavior matches enabled asset scopes.

Production verification:

```bash
tail -n 50 /var/log/ton-shkeeper-watchdog.log
tail -n 300 /var/log/ton-shkeeper-freeze-evidence.log
kubectl logs -n shkeeper deployment/ton-shkeeper -c app --since=1h --tail=3000 \
  | grep -Ei 'transactionsByMasterchainBlock|jetton/transfers|Checked block|Traceback|429|404|timeout'
```

Expected post-fix behavior:

- `last_block_timestamp` continues moving for TON-USDT-only production even if
  native TON indexer reads have a transient gap.
- Any native TON gap is visible as degraded state if native TON is enabled.
- `lag_sec` remains below the 360 second business threshold without watchdog
  restarts during normal transient upstream indexer gaps.

## Operational Mitigation Until Fixed

Keep the watchdog from `docs/DEPLOYMENT.md` installed:

- cron interval: once per minute
- `MAX_STUCK=2`
- restart target: only `deployment/ton-shkeeper`
- evidence log: `/var/log/ton-shkeeper-freeze-evidence.log`

The watchdog protects production by restarting before lag reaches the 360 second
limit, but it is not the root fix. It works because restart retries the same
checkpoint after the upstream indexer gap often resolves.

## Security Note

The production evidence pasted during diagnosis included a Toncenter key in raw
URLs. Rotate any exposed Toncenter keys before production hardening, and keep
watchdog evidence masking enabled.
