---
spike: 003
name: refee-rent-order-lifecycle
type: standard
validates: "Given a topped-up re:Fee account, when we POST /api/rent_resource/orders with resource=energy, duration_label=1h, amount=65000 for a real TRX address, then we observe order status transitions, measure delegation latency, and confirm energy arrives on-chain"
verdict: VALIDATED
related: [001, 002]
tags: [tron, refee, integration]
---

# Spike 003: re:Fee rent order lifecycle

## What This Validates

**Given** a topped-up re:Fee account and an activated TRON address,
**When** we create a `rent_resource` order for `energy` with `duration_label=1h`,
**Then** we can confirm the live response shape, status transitions, delegation latency,
rate-limit headers, error bodies, and whether rented energy is visible on-chain before
the sidecar broadcasts a TRC-20 transfer.

## Current Status

**VALIDATED - live `rent_resource` order reached `delegated`.**

The live run used an operator-provided test API key and a topped-up re:Fee
balance. The script does not print or store the API key. The JSON report for the
run was written to `/tmp/refee-rent-lifecycle-live.json` and is intentionally not
committed because it contains public order metadata.

## Research

Source used: committed OpenAPI snapshot at `docs/openapi-refeebot.json`.

Relevant contract from the OpenAPI snapshot:

| Question | Finding |
|---|---|
| Create endpoint | `POST /api/rent_resource/orders` |
| Success code | `202` |
| Request fields | `address`, `amount`, `resource`, `duration_label` |
| Resource value | `energy` or `bandwidth`; this spike uses `energy` |
| Poll endpoint | `GET /api/rent_resource/orders/{order_id}` |
| Order id field | `id` |
| Status field | `status` |
| Status values | `pending`, `delegated`, `completed`, `failed`, `insufficient_funds`, `canceled` |
| Delegation transaction field | `txn_hash` |
| Error field | `error` |
| Insufficient funds response | HTTP `402` on order creation |
| Validation response | HTTP `422` |
| Auth errors | HTTP `401` invalid/missing API key, HTTP `403` no access/IP whitelist |
| Idempotency/external id | No `external_id` or idempotency field in `RentResourceSchema` |

Implementation signal for Phase 2 before the live run:

- `RefeeEnergyProvider` should read the created order id from `response["id"]`.
- It should poll `GET /api/rent_resource/orders/{id}` and read lowercase
  `response["status"]`.
- It should treat `delegated` as the earliest safe point to broadcast the TRC-20
  transfer, then still cross-check `tron_client.get_account_resource(receiver)`.
- It should treat `failed`, `insufficient_funds`, and `canceled` as terminal failures.
- It cannot rely on re:Fee-side idempotency unless the live API exposes an undocumented
  field. Phase 2 should remain stateless and rely on the existing on-chain
  `EnergyLimit >= energy_needed` pre-check to avoid duplicate rent orders.

## How to Run

From `/Users/test/PycharmProjects/shkeeper.io`:

```bash
export REFEE_API_KEY="..."
export REFEE_TEST_TRON_ADDRESS="T..."

python3 .planning/spikes/003-refee-rent-order-lifecycle/refee_rent_lifecycle.py
```

Optional knobs:

```bash
export REFEE_RENT_AMOUNT=65000
export REFEE_RENT_DURATION_LABEL=1h
export REFEE_RENT_POLL_INTERVAL_SEC=2
export REFEE_RENT_TIMEOUT_SEC=90
export TRON_FULLNODE_URL=https://api.trongrid.io
export TRONGRID_API_KEY="..."        # optional, avoids public TronGrid throttling
```

Dry/offline checks:

```bash
python3 .planning/spikes/003-refee-rent-order-lifecycle/refee_rent_lifecycle.py --self-test
```

To intentionally record the insufficient-balance response body, run with:

```bash
python3 .planning/spikes/003-refee-rent-order-lifecycle/refee_rent_lifecycle.py --allow-insufficient-balance-probe
```

The script writes a JSON report under:

```text
.planning/spikes/003-refee-rent-order-lifecycle/artifacts/
```

The artifact directory ignores JSON reports by default because they can contain public
addresses, order ids, transaction hashes, and timestamps. It never stores the API key.

## What to Expect

Expected happy path:

1. `GET /api/users/me` returns `balance_sun` greater than the estimated cost.
2. `GET /api/rent_resource/tariffs` confirms the selected `duration_label=1h`.
3. Optional TRON fullnode resource snapshot records energy before the order.
4. `POST /api/rent_resource/orders` returns HTTP `202` with fields `id`, `status`,
   `cost`, `txn_hash`, `expiration_at`, and `created_at`.
5. Polling `GET /api/rent_resource/orders/{id}` reaches `status=delegated`.
6. Optional TRON fullnode snapshot shows `EnergyLimit - EnergyUsed` increased by
   approximately the rented amount.
7. The report includes `delegation_latency_sec` and any rate-limit headers observed.

Expected blocked path:

- If balance is below the estimated cost, the script exits before creating an order
  unless `--allow-insufficient-balance-probe` is set.
- If the API returns `402`, the response body is captured as the canonical
  insufficient-funds error shape for Phase 2.

## Observability

`refee_rent_lifecycle.py` records an event log with ISO timestamps:

- profile and tariff response status
- estimated cost in sun
- rate-limit headers from each re:Fee response
- initial and latest order payloads
- `pending -> delegated` elapsed seconds
- optional on-chain `EnergyLimit`, `EnergyUsed`, and available-energy deltas
- terminal errors or timeout cause

## Investigation Trail

### Iteration 1 - OpenAPI grounding

The local OpenAPI snapshot confirms that the assumptions from spike 002 are mostly
correct:

- status field name is `status`, not `check_status`;
- status values are lowercase;
- order id is `id`;
- a direct poll endpoint exists at `/api/rent_resource/orders/{order_id}`;
- the response has an `error` field, but non-2xx error bodies are not schema-defined;
- the create schema has no `external_id`, idempotency key, or caller-supplied metadata.

This closes the schema-level unknowns, but not the live behavior unknowns. We still need
a real run to measure latency, rate-limit headers, 402 body shape, and on-chain resource
arrival timing.

### Iteration 2 - Probe/runbook preparation

Created `refee_rent_lifecycle.py`, a stdlib-only Python probe that can be run by the
operator with their own API key and topped-up balance. It refuses to create an order when
the visible balance is below the estimated tariff cost unless explicitly asked to probe
the insufficient-funds path.

## Results

**Verdict: VALIDATED.**

Live run summary:

- `GET /api/users/me`: HTTP 200.
- `GET /api/rent_resource/tariffs`: HTTP 200.
- Visible test balance before order: `30,000,000` sun (30 TRX).
- Selected `duration_label=1h`, `amount=65000`.
- Test tariff price: `62.07` sun per energy, estimated cost `4,034,550` sun.
- `POST /api/rent_resource/orders`: HTTP 202 with initial `status=pending`.
- Poll sequence: `pending -> delegated`.
- Delegation latency: `4.933s`.
- Chain verification: before available energy `0`, after available energy `64999`.
- Rate-limit headers observed: none.

Operational conclusions:

- The live API uses `id` for order id, `status` for lifecycle state, lowercase
  statuses, and `txn_hash` for the delegation transaction.
- `REFEE.poll_interval_sec=2.0` and `REFEE.timeout_sec=60` remain conservative
  defaults for the sidecar implementation: the observed delegation was under 5s,
  but production should keep margin for network variance.
- The stdlib probe needed a browser-like `User-Agent`; otherwise Cloudflare
  returned `403 Error 1010 browser_signature_banned` for Python urllib. The
  sidecar's production `requests` client was separately checked against
  `/api/users/me` and returned HTTP 200 with the same key.
- Refund behavior on failed/insufficient-funds orders was not tested in this run
  because the account was funded and the order succeeded.
