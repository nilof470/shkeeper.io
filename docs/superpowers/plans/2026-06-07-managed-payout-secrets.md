# Managed Payout Secrets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make payout auth secrets Helm-managed from `/root/shkeeper-payout-values.yaml` so SHKeeper signer and sidecar verifier JSON cannot drift.

**Architecture:** Add an opt-in `payouts.managedSecrets.enabled` mode. In managed mode, the chart renders Kubernetes Secrets from `payouts.auth`, generates sidecar signer/verifier JSON from one helper, switches env refs to Helm-owned Secret names, and adds checksum annotations so pods roll when auth payload changes. Legacy external Secret refs remain the default.

**Rail Auth Semantics:** Generated sidecar auth rails are the capability set for `payouts.rails.*.enabled=true`. This intentionally includes rails that are staged with `paused=true` or `killSwitch=true`; execution enablement remains enforced by SHKeeper rail sync/catalog, while auth config stays stable through staged activation.

**Tech Stack:** Helm templates, Kubernetes Secret env refs, Python unittest-based Helm render tests, SHKeeper deployment documentation.

---

## File Structure

- Modify `shkeeper-helm-charts/charts/shkeeper/values.yaml`: add opt-in managed secret/auth values with safe empty defaults.
- Modify `shkeeper-helm-charts/charts/shkeeper/templates/_helpers.tpl`: add auth JSON helpers, managed-vs-legacy Secret ref helpers, validation, and checksum helper output.
- Create `shkeeper-helm-charts/charts/shkeeper/templates/secrets/payout-auth.yaml`: render Helm-owned payout auth Secrets only in managed mode.
- Modify payout-consuming templates:
  - `templates/shkeeper/deploy.yaml`
  - `templates/deployments/shkeeper-payout-workers.yaml`
  - `templates/jobs/shkeeper-payout-migrations.yaml`
  - `templates/jobs/shkeeper-payout-rail-sync.yaml`
  - `templates/deployments/tron-shkeeper.yaml`
  - `templates/deployments/tron-usdt-payout-worker.yaml`
  - `templates/deployments/ton-shkeeper.yaml`
  - `templates/deployments/ethereum-shkeeper.yaml`
  - `templates/jobs/payout-sidecar-migrations.yaml`
- Modify `shkeeper-helm-charts/tests/test_shkeeper_fork_chart.py`: cover managed Secret render, JSON parity, generated rails, checksum annotations, and legacy compatibility.
- Modify `shkeeper-helm-charts/charts/shkeeper/values-payouts-production-example.yaml` and environment examples only as non-secret guidance.
- Modify `shkeeper.io/docs/DEPLOYMENT.md`: replace manual payout Secret creation with managed values + one `helm upgrade` flow.
- Modify `shkeeper-helm-charts/docs/prod-tron-payout-runbook.md`: remove stale TRON-only sidecar Secret instructions or redirect to the managed flow.

## Task 1: Values And Validation Contract

**Files:**
- Modify: `shkeeper-helm-charts/charts/shkeeper/values.yaml`
- Modify: `shkeeper-helm-charts/charts/shkeeper/templates/_helpers.tpl`
- Test: `shkeeper-helm-charts/tests/test_shkeeper_fork_chart.py`

- [ ] **Step 1: Add failing tests for managed auth required fields**

Add tests that render with `payouts.enabled=true`, `payouts.managedSecrets.enabled=true`, and missing `payouts.auth.*.secret`; expect Helm failure messages naming the missing field.

Run:

```bash
cd /Users/test/PycharmProjects/shkeeper-helm-charts
python -m unittest tests.test_shkeeper_fork_chart.ShkeeperForkChartTests.test_managed_payout_requires_auth_values
```

Expected: fails because the test or validation does not exist yet.

- [ ] **Step 2: Add values defaults**

Add empty defaults:

```yaml
payouts:
  managedSecrets:
    enabled: false
  auth:
    gritherToShkeeper:
      keyId: ""
      secret: ""
    shkeeperToSidecars:
      keyId: ""
      secret: ""
    callbacks:
      keyId: ""
      secret: ""
    callbackEndpoints: {}
```

- [ ] **Step 3: Add validation helpers**

In `_helpers.tpl`, when `payouts.enabled=true` and `payouts.managedSecrets.enabled=true`, require:

```text
payouts.auth.gritherToShkeeper.keyId
payouts.auth.gritherToShkeeper.secret
payouts.auth.shkeeperToSidecars.keyId
payouts.auth.shkeeperToSidecars.secret
payouts.auth.callbacks.keyId
payouts.auth.callbacks.secret
payouts.auth.callbackEndpoints
```

Use existing placeholder rejection for key ids, secrets, and endpoint URLs.

- [ ] **Step 4: Keep legacy mode unchanged**

Ensure existing `payouts.secrets.*` Secret ref validation still runs when managed mode is disabled, so current users are not broken.

- [ ] **Step 5: Run focused tests**

```bash
cd /Users/test/PycharmProjects/shkeeper-helm-charts
python -m unittest tests.test_shkeeper_fork_chart.ShkeeperForkChartTests.test_managed_payout_requires_auth_values
```

Expected: pass.

## Task 2: Managed Secret Rendering And Env Ref Switching

**Files:**
- Create: `shkeeper-helm-charts/charts/shkeeper/templates/secrets/payout-auth.yaml`
- Modify: `shkeeper-helm-charts/charts/shkeeper/templates/_helpers.tpl`
- Test: `shkeeper-helm-charts/tests/test_shkeeper_fork_chart.py`

- [ ] **Step 1: Add failing tests for rendered managed Secrets**

Render with three enabled rails and managed auth values. Assert:

- a SHKeeper consumer Secret contains `PAYOUT_CONSUMER_KEYS_JSON`;
- a sidecar auth Secret contains both `PAYOUT_SIDECAR_KEYS_JSON` and `PAYOUT_CONSUMER_KEYS_JSON`;
- sidecar signing JSON and sidecar consumer JSON decode to the same object;
- decoded sidecar rails are exactly `TRON-USDT`, `TON-USDT`, `ETH-USDT`;
- env refs point to Helm-owned managed Secret names, not `grither-prod-*`.

- [ ] **Step 2: Add rail list helper**

Generate auth rails from enabled chart rails:

```text
tronUsdt.enabled -> TRON-USDT
tonUsdt.enabled -> TON-USDT
ethUsdt.enabled -> ETH-USDT
```

Do not gate auth rails on `paused` or `killSwitch`. This preserves the current `DEPLOYMENT.md` production pattern where all staged rails share one sidecar key and only the rail catalog execution flag changes during activation.

- [ ] **Step 3: Add JSON payload helpers**

Generate canonical nested JSON:

```json
{
  "grither-pay": {
    "shkeeper-to-sidecars-v1": {
      "secret": "secret",
      "rails": ["TRON-USDT", "TON-USDT", "ETH-USDT"]
    }
  }
}
```

Use the same helper for `PAYOUT_SIDECAR_KEYS_JSON` and sidecar `PAYOUT_CONSUMER_KEYS_JSON`.

- [ ] **Step 4: Render managed Kubernetes Secrets**

Create `payout-auth.yaml` with Helm-owned names such as:

```text
shkeeper-payout-consumer-keys
shkeeper-payout-sidecar-auth
shkeeper-payout-callback-keys
shkeeper-payout-callback-endpoints
```

Use `stringData` so templates stay readable and Kubernetes stores base64 data.

- [ ] **Step 5: Switch env helpers in managed mode**

Update `shkeeper.payoutShkeeperEnv` and `shkeeper.payoutSidecarConsumerEnv`:

- managed mode uses Helm-owned Secret refs;
- legacy mode uses existing `payouts.secrets.*` refs.

- [ ] **Step 6: Run focused render tests**

```bash
cd /Users/test/PycharmProjects/shkeeper-helm-charts
python -m unittest tests.test_shkeeper_fork_chart.ShkeeperForkChartTests.test_managed_payout_renders_auth_secrets_and_refs
```

Expected: pass.

## Task 3: Checksum Rollout Annotations

**Files:**
- Modify: `shkeeper-helm-charts/charts/shkeeper/templates/_helpers.tpl`
- Modify payout-consuming Deployment templates listed in File Structure.
- Test: `shkeeper-helm-charts/tests/test_shkeeper_fork_chart.py`

- [ ] **Step 1: Add failing checksum tests**

Render managed mode and assert pod templates that consume payout auth env include checksum annotations. Render twice with different `payouts.auth.shkeeperToSidecars.secret` and assert the sidecar checksum changes.

- [ ] **Step 2: Add checksum helper**

Add a helper that hashes the generated managed payloads. For legacy mode, hash the configured Secret ref names/keys so pod templates remain stable unless refs change.

- [ ] **Step 3: Add annotations to payout-consuming pod templates**

Add annotations under `.spec.template.metadata.annotations` for Deployments and under job template metadata where needed:

```yaml
checksum/payout-auth: {{ include "shkeeper.payoutAuthChecksum" . | quote }}
```

Preserve existing labels and annotations.

- [ ] **Step 4: Run focused checksum tests**

```bash
cd /Users/test/PycharmProjects/shkeeper-helm-charts
python -m unittest tests.test_shkeeper_fork_chart.ShkeeperForkChartTests.test_managed_payout_checksum_rolls_consumers
```

Expected: pass.
 
## Task 4: Documentation And Production Values Guidance

**Files:**
- Modify: `shkeeper.io/docs/DEPLOYMENT.md`
- Modify: `shkeeper-helm-charts/docs/prod-tron-payout-runbook.md`
- Modify: `shkeeper-helm-charts/charts/shkeeper/values-payouts-production-example.yaml`
- Modify: `shkeeper-helm-charts/charts/shkeeper/environments/values-prod-tron-payout.yaml`
- Modify: `shkeeper-helm-charts/charts/shkeeper/environments/values-prod-ton-payout.yaml`
- Modify: `shkeeper-helm-charts/charts/shkeeper/environments/values-prod-eth-payout.yaml`
- Test: `shkeeper-helm-charts/tests/test_shkeeper_fork_chart.py`

- [ ] **Step 1: Add documentation tests or existing fixture checks**

Ensure production example files do not contain real secret-looking values and do not enable managed mode with placeholders.

- [ ] **Step 2: Update deployment docs**

Replace manual `kubectl create secret generic ... --from-file ...` payout instructions with:

```yaml
payouts:
  managedSecrets:
    enabled: true
  auth:
    gritherToShkeeper:
      keyId: grither-pay-to-shkeeper-v1
      secret: "PASTE_ON_SERVER_ONLY"
    shkeeperToSidecars:
      keyId: shkeeper-to-sidecars-v1
      secret: "PASTE_ON_SERVER_ONLY"
    callbacks:
      keyId: shkeeper-callbacks-v1
      secret: "PASTE_ON_SERVER_ONLY"
    callbackEndpoints:
      grither-pay-main:
        url: "https://dev.api.grither.company/api/webhooks/shkeeper/payout-executions"
        path: "/api/webhooks/shkeeper/payout-executions"
```

Document that real values live only in `/root/shkeeper-payout-values.yaml`.

- [ ] **Step 3: Update runbook**

Remove the stale TRON-only sidecar consumer JSON flow. State that `shkeeperToSidecars` is one key for all enabled rails and is rendered into both signer and verifier payloads.

- [ ] **Step 4: Run docs-related tests**

```bash
cd /Users/test/PycharmProjects/shkeeper-helm-charts
python -m unittest tests.test_shkeeper_fork_chart.ShkeeperForkChartTests.test_production_payout_overlay_defaults_disabled_without_hot_wallet_material
```

Expected: pass.

## Task 5: Full Verification

**Files:**
- No new files.

- [ ] **Step 1: Run chart test suite**

```bash
cd /Users/test/PycharmProjects/shkeeper-helm-charts
python -m unittest tests.test_shkeeper_fork_chart
```

Expected: all tests pass.

- [ ] **Step 2: Render managed production shape**

Render with a temporary non-production values file containing fake secrets and enabled TRON, TON, ETH rails. Verify:

- Helm-owned payout auth Secrets render;
- sidecar auth JSON includes `TRON-USDT`, `TON-USDT`, `ETH-USDT`;
- `tron-shkeeper`, `ton-shkeeper`, `ethereum-shkeeper`, and payout worker templates reference managed Secrets;
- checksum annotations are present.

- [ ] **Step 3: Review final diff**

Run:

```bash
cd /Users/test/PycharmProjects/shkeeper-helm-charts
git diff -- charts/shkeeper tests docs
cd /Users/test/PycharmProjects/shkeeper.io
git diff -- docs/DEPLOYMENT.md docs/superpowers/plans/2026-06-07-managed-payout-secrets.md
```

Expected: diff only contains managed payout secret implementation, tests, and deployment docs.

## Deployment Command Shape

Final production deploy must use files only:

```bash
export HELM_NS=default
export APP_NS=shkeeper
export CHART_REF=oci://ghcr.io/nilof470/helm-charts/shkeeper
export CHART_VERSION=<new-chart-version>

helm -n "$HELM_NS" get values shkeeper -o yaml > /root/shkeeper-current-values.yaml

helm upgrade shkeeper "$CHART_REF" \
  --version "$CHART_VERSION" \
  -n "$HELM_NS" \
  -f /root/shkeeper-current-values.yaml \
  -f /root/shkeeper-payout-values.yaml \
  --timeout 15m \
  --history-max 2
```

No `kubectl create secret`, no `kubectl patch secret`, no `--set-string`.
