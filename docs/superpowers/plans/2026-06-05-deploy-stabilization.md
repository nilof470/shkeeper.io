# Deploy Stabilization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align the production deploy path with the current payout chart and document the accepted single-node SQLite operating mode.

**Architecture:** Keep the runtime payout architecture unchanged. Treat the Helm chart fork as the Kubernetes source of truth, make the deploy wrapper verify the current separate TRON payout worker Deployment, and add a small release-gate check so stale chart versions do not return.

**Tech Stack:** POSIX shell deploy wrapper, Python verification/release-gate scripts, unittest, Markdown documentation, Helm chart metadata.

---

### Task 1: Align Deploy Wrapper With Current Chart

**Files:**
- Modify: `deploy/shkeeper/upgrade.sh`
- Modify: `deploy/shkeeper/verify-tron-usdt-payout-worker.py`
- Test: `tests/test_shkeeper_deploy_scripts.py`

- [x] **Step 1: Update default chart version**

Set `DEFAULT_CHART_VERSION` to `1.7.28-nilof470.8` and update the help text to the same value.

- [x] **Step 2: Verify separate TRON payout worker Deployment**

Change the wrapper to wait for `deployment/tron-usdt-payouts` when it exists, and change the Python verifier to validate `tron-shkeeper` and `tron-usdt-payouts` as separate Deployments.

- [x] **Step 3: Update deploy script tests**

Add focused unittest coverage for the current chart version and for verifying the separate `tron-usdt-payouts` Deployment command.

- [x] **Step 4: Run focused tests**

Run:

```bash
.venv/bin/python -m unittest tests/test_shkeeper_deploy_scripts.py -v
```

Expected: all tests pass.

### Task 2: Document Single-Node SQLite Scope

**Files:**
- Modify: `deploy/shkeeper/README.md`
- Modify: `docs/DEPLOYMENT.md`

- [x] **Step 1: Update active deploy docs**

Replace stale chart version references in the active deploy path with `1.7.28-nilof470.8`.

- [x] **Step 2: Correct TRON topology docs**

Document `tron-shkeeper` as API/tasks/redis and `tron-usdt-payouts` as a separate sequential worker Deployment.

- [x] **Step 3: Add SQLite operating note**

Document that this deployment intentionally uses `singleNodeSqlitePvc` for the current Grither Pay gateway scope, with no horizontal write scaling assumption.

### Task 3: Add Drift Guard

**Files:**
- Modify: `scripts/verify_payout_release_gate.py`

- [x] **Step 1: Add chart version alignment check**

Parse the Helm chart version from the sibling chart checkout and require `deploy/shkeeper/upgrade.sh` plus `deploy/shkeeper/README.md` to reference that exact version.

- [x] **Step 2: Include the check in release gate**

Run the check in normal release-gate execution and show it in `--list`.

- [x] **Step 3: Run focused verification**

Run:

```bash
.venv/bin/python -m unittest tests/test_shkeeper_deploy_scripts.py -v
.venv/bin/python scripts/verify_payout_release_gate.py --list
git diff --check
```

Expected: tests pass, release-gate list prints the new chart-version check, and diff check exits cleanly.
