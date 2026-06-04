# USDT Withdrawals Helm Kubernetes Implementation Plan

> **For agentic workers:** use concrete skills, not a generic Superpowers-style
> flow. Start with `investigate` to validate code reality, use `review` after
> each implementation block, and use `careful` before destructive production or
> Kubernetes operations. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** render the production payout topology for TRON, TON, and ETH through Helm without wrapper-script correctness assumptions.

**Architecture:** The chart renders first-class payout workers, queues, storage/migration readiness, backup/restore posture, NetworkPolicy or equivalent ingress restrictions, Secret/external-secret references, and per-rail kill switches. Helm validation must fail for incomplete enabled rails.

**Tech Stack:** Helm templates, `values.yaml`, optional `values.schema.json`, Python unittest chart tests.

---

## Files

Repository: `/Users/test/PycharmProjects/shkeeper-helm-charts`

Modify:

- `charts/shkeeper/values.yaml`
- `charts/shkeeper/templates/deployments/tron-shkeeper.yaml`
- `charts/shkeeper/templates/deployments/ton-shkeeper.yaml`
- `charts/shkeeper/templates/deployments/ethereum-shkeeper.yaml`
- `charts/shkeeper/templates/shkeeper/deploy.yaml`
- `tests/test_shkeeper_fork_chart.py`

Create when required by implementation:

- `charts/shkeeper/values.schema.json` if Helm `fail` checks stop being enough
- `charts/shkeeper/templates/networkpolicies/payout-sidecars.yaml`
- `charts/shkeeper/templates/jobs/payout-sidecar-migrations.yaml`
- `charts/shkeeper/templates/jobs/shkeeper-payout-migrations.yaml`
- `charts/shkeeper/templates/deployments/shkeeper-payout-workers.yaml`
- `charts/shkeeper/templates/monitoring/payout-prometheusrule.yaml`
- `charts/shkeeper/templates/secrets/payout-secret-refs.yaml`

## Current Implementation Status, 2026-06-04

Implemented in `/Users/test/PycharmProjects/shkeeper-helm-charts`:

- `payouts.*` values contract for generic consumer, request timeout, auth max
  age, Secret refs, NetworkPolicy, storage mode, migration hooks, resources,
  SHKeeper worker settings, and TRON/TON/ETH rail settings.
- Per-rail enablement, pause/kill switch, callback endpoint id,
  backup/restore evidence, source wallet ref, queue, sidecar service/symbol, and
  owned image repository gates. The chart must not render SHKeeper-side
  amount/day cap fields; business limits are enforced upstream.
- Helm `fail` validation for incomplete enabled payout topology. A
  `values.schema.json` was not added because template-level fail checks cover the
  production invariants and can validate cross-field queue/image relationships.
- Enabled payout topology also fails closed for non-positive or non-numeric
  operational timing/batch values: SHKeeper-to-sidecar request timeout, sidecar
  HMAC max-age, SHKeeper payout worker interval, and SHKeeper payout worker batch
  limit must be positive integers.
- SHKeeper payout execution reconciler and callback dispatcher Deployments.
  Grither Pay owns the submit outbox/dispatcher; SHKeeper submit is the signed
  API/web path, not a separate Helm worker.
- Chart-owned `shkeeper-payout-rail-sync` Job with generated
  `PAYOUT_RAILS_JSON`.
- SHKeeper and sidecar migration/init Jobs ordered before rail sync by Helm hook
  weights.
- Dedicated `tron-usdt-payouts`, `ton-usdt-payouts`, and `eth-usdt-payouts`
  containers with queue isolation, concurrency 1, prefetch 1, probes, resources,
  and `preStop`.
- Sidecar pod-local Redis safety posture: `replicas: 1`, `Recreate`, AOF,
  Redis PVC, readiness/liveness, `preStop`, and termination grace.
- NetworkPolicies restricting sidecar HTTP ingress to SHKeeper-labeled pods.
- Payout-critical `extraEnv` bypasses are rejected for enabled rails. The chart
  owns queue/provisioning/auth env through `payouts.rails.*`, not through wrapper
  scripts or ad hoc values.
- Enabled payout topology also rejects literal secret/hot-wallet-looking
  `extraEnv` keys for SHKeeper and TRON/TON/ETH sidecars, including private key,
  mnemonic, seed, password, API key, auth token, `FEE_DEPOSIT_*`,
  `HOT_WALLET_*`, and payout auth override attempts. Secret material must come
  from Kubernetes Secret or external-secret references.
- Direct `*.usdtPayoutWorker.enabled=true` is rejected unless the matching
  `payouts.rails.*.enabled=true` rail is also enabled. Dedicated payout workers
  cannot be started through legacy worker values without the payout rail contract.
- Enabled payout rails render their sidecar Kubernetes Service even when the
  legacy wallet asset flag is disabled. SHKeeper runtime routing must use the
  synced `PayoutRail.sidecar_service` / `PayoutExecution.sidecar_service`, not
  `Crypto.instances`, so a rail-only payout deployment is not coupled to legacy
  wallet module loading.
- Enabled rails require exact owned image repository prefixes (`repo:` tag or
  `repo@` digest), not substring matches inside arbitrary image names.
- Enabled rails reject `sourceWalletRef` values other than `fee_deposit` for the
  Phase 1 sidecar source-wallet model. Dedicated payout wallets require sidecar
  source override support before this guard can be relaxed.
- Enabled payout topology rejects placeholder text such as `REPLACE-*`,
  `TODO`, `TBD`, and `PLACEHOLDER` in required production values.
- Optional chart-owned `PrometheusRule` renders first-release payout alerts for
  SHKeeper reconciliation/stuck/dispatch/callback backlog, enabled-rail catalog
  disabled/missing, enabled sidecar worker/broker queue depth/age health,
  wallet-balance metric availability, and optional low hot-wallet/fee-wallet
  thresholds. It is disabled by default so clusters without Prometheus Operator
  CRDs can still render the base chart. Low-balance thresholds are empty by
  default and render only when an operator explicitly sets
  `payouts.rails.*.hotWalletMinimumBalance` or
  `payouts.rails.*.feeWalletMinimumBalance`.
- SHKeeper payout reconciler/dispatcher workers fail closed when
  `payouts.shkeeperWorkers.enabled=true` is set without `payouts.enabled=true`.
  They cannot be rendered as orphan workers without payout secrets/env.
- `values-payouts-production-example.yaml` provides a disabled TRON/TON/ETH
  production overlay scaffold with Secret refs, owned fork image repositories,
  queues, source-wallet references, and backup/restore evidence placeholders. It
  intentionally contains no hot-wallet signing material or secret values, and it
  fails rendering if an operator enables payout topology before replacing the
  placeholders with concrete production evidence.

Remaining production inputs before enabling a rail:

- publish and reference concrete owned image tags or immutable digests for
  SHKeeper and the target sidecar fork, built from the final clean reviewed
  commits. Current environment overlay image refs are render/staging evidence,
  not proof that registry images contain the uncommitted payout WIP;
- update the final production environment values only after release images are
  built from those reviewed commits. Current local release-image audit:
  `shkeeper.io` HEAD `54fe764` vs overlay `0e4c415`, `tron-shkeeper` HEAD
  `7298151` vs overlay `5a6133b`, `ton-shkeeper` HEAD `f433e03` vs overlay
  `d8f5c77`; ETH overlay matches `ethereum-shkeeper` HEAD `977f920`. Prefer
  immutable `image@sha256:...` refs for the final enablement values;
- create real Kubernetes Secrets or external-secret bindings matching the chart
  Secret refs;
- record restore-drill evidence in values for SHKeeper and the enabled rail;
- verify hot-wallet material handling for that rail stays out of ConfigMaps and
  matches the current sidecar `fee_deposit` source-wallet model;
- run a low-value staging/testnet smoke payout and then one rail at a time in
  production. Wallet-balance alert thresholds should be chosen explicitly before
  enablement only if the operator wants those controls; the chart does not
  hardcode business limits or render SHKeeper amount/day caps.

## Task 1: Values Contract And Validation

- [x] Write failing chart tests proving enabled rails fail validation when
  missing: owned image tag, queue match, sidecar execution storage, migration
  config, backup posture, NetworkPolicy/ingress restriction, Secret refs,
  pause/kill switch, and safe rollout strategy. SHKeeper amount/day caps are not
  part of the values contract for an enabled rail.
- [x] Write failing chart tests proving a pod-local Redis payout rail cannot be
  enabled unless the rail deployment is `replicas: 1`, uses `Recreate` strategy
  with no surge/second broker window, has Redis AOF or a documented durable
  execution recovery path, has `preStop`, startup/readiness probes,
  `terminationGracePeriodSeconds`, and a readiness gate that fails closed during
  startup, migration, and shutdown.
- [x] Run:

```bash
cd /Users/test/PycharmProjects/shkeeper-helm-charts
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

Result: tests pass after implementation.

- [x] Add `values.schema.json` or Helm `required`/`fail` checks for all required
  production values.
- [x] Render a SHKeeper payout rail sync Job that calls
  `flask payout-rail-sync` with chart-generated `PAYOUT_RAILS_JSON`, so enabled
  rails do not require manual SQL/runbook creation of `PayoutRail` rows.
- [x] Render bounded SHKeeper-to-sidecar request timeout configuration
  (`PAYOUT_SIDECAR_REQUEST_TIMEOUT`) for preflight, submit, and status calls.
- [x] Fail rendering when `payouts.sidecarRequestTimeoutSeconds`,
  `payouts.authMaxAgeSeconds`, `payouts.shkeeperWorkers.intervalSeconds`, or
  `payouts.shkeeperWorkers.limit` is missing, non-numeric, zero, or negative.
  These are operational bounds and batch sizes, not payout amount limits.
- [x] Validate that TRON resource-provisioning `extraEnv` cannot implicitly
  enable payout topology. Payout worker topology must come from
  `payouts.rails.*.enabled` plus production gate values.
- [x] Reject direct `*.usdtPayoutWorker.enabled=true` unless the matching
  `payouts.rails.*.enabled=true` rail is enabled. This closes the legacy worker
  bypass without removing SHKeeper's runtime/admin `/payout` feature.
- [x] Ensure enabled rails default to disabled in production examples until every
  subsystem acceptance gate passes.
- [x] Scope rendered `PAYOUT_RAILS_JSON` to the configured consumer so SHKeeper
  rail sync can disable stale DB rails removed from Helm values. This makes
  disabled/removed rails a desired-state operation instead of requiring manual
  DB cleanup.
- [x] Reject placeholder values in enabled production topology, so
  `REPLACE-WITH-PUBLISHED-TAG` or restore-drill placeholders cannot render as an
  enabled payout deployment.
- [x] Run chart tests and require all to pass.
- [ ] Commit:

```bash
git add charts/shkeeper/values.yaml charts/shkeeper/values.schema.json tests/test_shkeeper_fork_chart.py
git commit -m "feat: validate payout rail helm values"
```

## Task 2: Dedicated Workers And Queue Isolation

- [x] Validate SHKeeper core storage before choosing worker topology. If the
  chart still runs SHKeeper core on SQLite through `shkeeper-db-claim`, separate
  payout reconciler/callback worker pods are only acceptable for a
  single-VPS/single-node Phase 1 deployment. HA, zero-downtime, or multi-node
  workers require moving SHKeeper payout execution state to a shared DB or
  keeping those workers in the same pod as the web process.
- [x] Write failing tests proving:
  `tron-usdt-payouts`, `ton-usdt-payouts`, and `eth-usdt-payouts` render when
  enabled; normal `tasks` worker does not consume payout queues; worker queue,
  sidecar queue env/config, readiness check, and SHKeeper rail catalog queue match;
  workers default to concurrency 1 and prefetch 1.
- [x] Harden payout worker concurrency/prefetch from default to render-time
  invariant: enabled TRON/TON/ETH payout workers now fail `helm template` if
  `usdtPayoutWorker.concurrency != 1` or `usdtPayoutWorker.prefetchMultiplier != 1`.
  This keeps first-release payout workers sequential until a rail-specific wallet
  allocator proves higher parallelism safe.
- [x] Write failing tests proving SHKeeper renders first-class payout execution
  reconciler and callback-outbox dispatcher workers when payout API is enabled.
  Grither Pay owns the submit outbox/dispatcher. The SHKeeper worker commands
  are `flask payout-execution-reconciler` and
  `flask payout-callback-dispatcher`. APScheduler in the web process is not a
  production payout reconciler or callback dispatcher.
- [x] Write a negative test proving TRON `extraEnv`
  `TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED=true` cannot silently enable the
  payout worker without an explicitly enabled TRON payout rail and production
  gate config.
- [x] Preserve TRON queue `tron_usdt_fee_payouts`.
- [x] Add TON queue `ton_usdt_payouts`.
- [x] Add ETH queue `eth_usdt_payouts`.
- [x] Render SHKeeper payout worker command/queue values separately from the web
  container: execution reconciler and callback outbox dispatcher have readiness,
  resources, logs, and bounded batch config.
- [x] Fail rendering if `payouts.shkeeperWorkers.enabled=true` is set without
  `payouts.enabled=true`; payout workers must not run without the payout API
  contract, secrets, and rail configuration.
- [x] Render the payout rail sync Job before SHKeeper payout workers start, and
  fail rendering when `payouts.enabled=true` lacks `payouts.consumer` or an
  enabled rail lacks `callbackEndpointId`.
- [x] Render payout workers from values and fail if a rail is enabled without an
  owned image tag.
- [x] Clean up TRON worker enablement so resource provisioning env config can
  require worker readiness, but cannot be the only chart API that changes payout
  worker topology.
- [x] Clean up direct payout worker enablement so `tron_shkeeper`,
  `ton_shkeeper`, and `ethereum_shkeeper` cannot start a dedicated payout worker
  without the matching enabled payout rail.
- [x] Run chart tests and require all to pass.
  Verification, 2026-06-04: `python3 -m unittest tests/test_shkeeper_fork_chart.py -v`
  passed 21 tests; `helm lint charts/shkeeper` passed with only the standard icon
  recommendation; `git diff --check` was clean.
- [ ] Commit:

```bash
git add charts/shkeeper/values.yaml charts/shkeeper/templates/deployments tests/test_shkeeper_fork_chart.py
git commit -m "feat: render dedicated payout workers"
```

## Task 3: Storage, Migrations, Backup, And Redis Recovery

Pod-local Redis is allowed only as a Phase 1 compromise. It is transport, not the
source of truth. Enabled payout rails must be recoverable from sidecar execution
state plus SHKeeper execution state, or Redis must be externalized before the rail
is enabled.

- [x] Write failing tests proving each enabled rail renders sidecar execution DB
  persistence or external DB configuration, migration job/init step, backup/restore
  posture, and restore drill evidence gate.
- [x] Write failing tests proving SHKeeper payout execution migrations and sidecar
  payout execution migrations are rendered and complete before payout
  submit/status readiness becomes true.
- [x] If Redis remains pod-local, test that persistence/AOF parity exists or queued
  payout work is fully recoverable from sidecar execution state.
- [x] If Redis remains pod-local, render Redis with PVC-backed AOF, `preStop`
  save/drain behavior, termination grace, and readiness that becomes false before
  termination so payout submit stops before the broker shuts down.
- [x] If a sidecar deployment contains pod-local Redis, force `replicas: 1` and
  `strategy.type: Recreate` for that rail unless values select an external Redis
  with a proven HA/recovery posture. Do not use RollingUpdate/surge for pod-local
  payout brokers.
- [x] Document that zero-downtime or HA sidecar rollout requires an external
  broker and separate Deployments; pod-local Redis must fail validation for that
  mode.
- [x] Render migration readiness before payout submit readiness.
- [x] Render restore-drill evidence config for enabled production rails.
- [x] Render resources/limits and liveness/readiness/startup probes for app,
  tasks, payout worker, migration job, and Redis containers.
- [x] Run chart tests and require all to pass.
- [ ] Commit:

```bash
git add charts/shkeeper/values.yaml charts/shkeeper/templates tests/test_shkeeper_fork_chart.py
git commit -m "feat: render payout state persistence gates"
```

## Task 4: Network And Secret Boundaries

- [x] Write failing tests proving sidecar payout endpoints are reachable only from
  SHKeeper service traffic through NetworkPolicy or an equivalent ingress
  restriction.
- [x] Write failing tests proving rendered manifests do not place payout credentials,
  callback signing keys, RPC credentials, or hot-wallet material in ConfigMaps.
- [x] Add Secret/external-secret references for payout credentials and callback
  keys, and preserve existing RPC/backend Secret references.
- [x] Verify chart-level hot-wallet material handling per rail before enabling
  production payouts: TRON, TON, and ETH use the current `fee_deposit`
  source-wallet reference; hot-wallet material stays out of committed values,
  ConfigMaps, and rendered manifests. Real Kubernetes Secret creation and
  sidecar-specific key custody remain production inputs.
- [x] Add a disabled TRON/TON/ETH production overlay scaffold proving the chart
  can configure rails without committing hot-wallet material or secret values.
  Concrete published tags, real Secret bindings, and restore-drill evidence must
  replace placeholders before an enabled render is allowed.
- [x] Fail rendering if payout credential or callback key refs are missing.
- [x] Run chart tests and require all to pass.
- [ ] Commit:

```bash
git add charts/shkeeper/templates charts/shkeeper/values.yaml tests/test_shkeeper_fork_chart.py
git commit -m "feat: restrict payout sidecar ingress"
```

## Verification Gate

- [x] Run:

```bash
cd /Users/test/PycharmProjects/shkeeper-helm-charts
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

- [x] Request independent review focused on queue isolation, pod-local Redis
  rollout, missing production values, secret rendering, NetworkPolicy, and
  backup/restore posture.
- [x] Do not deploy an enabled rail until the rendered manifest has no missing
  required values and cannot run two active pod-local Redis brokers serving payout
  submit traffic for the same rail. The chart now rejects missing required
  values, placeholder production gates, direct worker bypasses, non-owned images,
  queue mismatches, and non-sequential sidecar payout workers.
- [x] Verify rendered manifests include SHKeeper payout dispatcher/reconciler and
  callback-outbox workers, sidecar migration readiness, pod-local Redis
  single-replica/Recreate controls, and fail-closed payout readiness before a rail
  is enabled.

2026-06-04 verification evidence after the Helm/SHKeeper rail-only review pass:

- `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`: 32 tests
  passed after the direct worker bypass, placeholder-gate, alerting, positive
  timing/batch validation fixes, consumer-scoped empty desired catalog
  validation, and literal secret/hot-wallet `extraEnv` rejection.
- `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_shkeeper_fork_chart -v`:
  32 tests passed.
- `helm lint charts/shkeeper`: passed, 0 failed.
- `git diff --check`: passed.
- `helm template shkeeper charts/shkeeper --output-dir /private/tmp/shkeeper-helm-render-default-20260604-postfix`:
  default render passed.
- `helm template shkeeper charts/shkeeper` and
  `helm template shkeeper charts/shkeeper -f charts/shkeeper/values-payouts-production-example.yaml`
  rendered successfully in the current review pass.
- `helm template shkeeper charts/shkeeper -f charts/shkeeper/environments/values-prod-tron-payout.yaml`,
  `helm template shkeeper charts/shkeeper -f charts/shkeeper/environments/values-prod-ton-payout.yaml`,
  and `helm template shkeeper charts/shkeeper -f charts/shkeeper/environments/values-prod-eth-payout.yaml`
  rendered successfully in the current review pass; rendered manifests include
  the dedicated `tron-usdt-payouts`, `ton-usdt-payouts`, or `eth-usdt-payouts`
  worker for exactly one rail, with `--concurrency=1`,
  `--prefetch-multiplier=1`, kill-switched
  `execution_enabled=false` catalog rows, and no `PrometheusRule`
  manifests by default.
- `helm template shkeeper charts/shkeeper --output-dir /private/tmp/shkeeper-helm-render-all-payouts-20260604-postfix`
  with TRON, TON, and ETH rails enabled passed.
- TRON, TON, and ETH positive rail renders passed with required Secret refs,
  backup evidence, owned image repository values, rail sync job, sidecar
  migration/init jobs, dedicated payout worker, NetworkPolicy, probes, resources,
  and `execution_enabled=true`.
- Positive rail renders proved sidecar Services are rendered even when the
  corresponding legacy asset flags are disabled.
- Negative render failed as expected when enabled TRON payout tried to set
  `tron_shkeeper.extraEnv.TRON_USDT_PAYOUT_QUEUE`.
- Negative render failed as expected when enabled payout topology tried to set
  literal secret/hot-wallet-looking `extraEnv` values on SHKeeper or
  TRON/TON/ETH sidecars.
- Negative render failed as expected when an enabled rail image used an owned
  repository only as a substring of an untrusted image name.
- Negative render failed as expected when `payouts.shkeeperWorkers.enabled=true`
  was set while `payouts.enabled=false`.
- Negative render failed as expected when direct `*.usdtPayoutWorker.enabled=true`
  was set without the matching enabled rail.
- Negative render failed as expected when the production overlay was enabled with
  `REPLACE-*` placeholder image tags or restore-drill evidence.
- Disabled-rail render proved an enabled legacy TRON sidecar does not receive
  payout auth/auto-enqueue env when only the ETH rail is enabled.
- Positive TRON/TON/ETH render proved rail sync JSON contains
  `source_wallet_ref=fee_deposit`, `hot_wallet_policy=CURRENT_SIDECAR_SOURCE_WALLET`,
  `legacy_spend_policy=BLOCK_AUTOMATIC_BYPASS`, and
  `execution_enabled=true` only for enabled rails.
- Production overlay default render proved no payout worker, payout migration,
  or payout Secret env is rendered until the operator explicitly enables
  `payouts.enabled` and one rail after replacing placeholder gates.
- Empty desired catalog render proved `PAYOUT_RAILS_JSON` includes the configured
  consumer (`{"consumer":"grither-pay","rails":[]}`), allowing SHKeeper sync to
  disable stale enabled rails for that consumer instead of leaving old DB state
  active.
