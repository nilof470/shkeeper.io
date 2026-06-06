# SHKeeper Production Deploy Wrapper

This directory contains the guarded production deploy entry point for this
SHKeeper fork. The Helm chart fork is the source of truth for Kubernetes
manifests; this wrapper only standardizes the production command, waits for
rollouts, and runs post-deploy verification.

The chart fork is published as a versioned OCI chart:

```text
oci://ghcr.io/nilof470/helm-charts/shkeeper
version: 1.7.28-nilof470.9
```

For a private GHCR package, log Helm in once on the VPS:

```bash
echo "GITHUB_TOKEN_WITH_READ_PACKAGES" | helm registry login ghcr.io -u nilof470 --password-stdin
```

The upstream `vsys-host/helm-charts` chart renders the TRON sidecar with three
containers: `app`, `tasks`, and `redis`. Our chart fork keeps that API pod shape
and, when TRON USDT payout execution is active, renders a separate sequential
`tron-usdt-payouts` Deployment.

The worker reaches the TRON sidecar Redis broker through the `tron-shkeeper`
Service on port `6379`. Keep `payouts.networkPolicies.enabled=true` so that
Redis port is internal to the payout topology.

The current production scope intentionally uses `payouts.storage.mode:
singleNodeSqlitePvc`. This is accepted for the Grither Pay gateway deployment
because it is a single-node, controlled-throughput installation. Do not scale
SHKeeper writers horizontally without replacing this storage mode.

The wrapper applies the published chart fork directly. There is no local chart
clone, post-renderer, or Python YAML dependency in the deploy path.

Before the Helm upgrade, the wrapper verifies the payout sidecar auth Secret
contract in the SHKeeper namespace. This catches the common deployment mistake
where `PAYOUT_CONSUMER_KEYS_JSON` for ETH/TON sidecars is created from the
SHKeeper signing JSON and payouts fail at runtime with
`PAYOUT_AUTH_UNKNOWN_KEY`.

Generate the sidecar consumer Secret payload from the signing payload instead
of editing it by hand:

```bash
python3 /opt/shkeeper.io/deploy/shkeeper/payout-secret-guard.py render-sidecar-consumer \
  --signing-keys-file /root/payout-sidecar-signing-keys.json \
  --output /root/payout-sidecar-consumer-keys.json
```

## Usage

```bash
cd /opt/shkeeper.io
deploy/shkeeper/upgrade.sh /root/shkeeper-values.yaml
```

Optional environment overrides:

```bash
RELEASE=shkeeper RELEASE_NS=default APP_NS=shkeeper \
  deploy/shkeeper/upgrade.sh /root/shkeeper-values.yaml
```

Use `CHART=/path/to/charts/shkeeper` only for local chart development. Use
`CHART_VERSION=...` to pin a different published chart version.
Use `PAYOUT_SECRET_PREFLIGHT=skip` only for a deployment that intentionally does
not configure payout execution Secrets.

The wrapper:

- verifies the payout sidecar signing/consumer Secret contract;
- runs `helm upgrade --install` against the chart fork without Helm wait mode;
- waits for `shkeeper-deployment`;
- waits for `tron-shkeeper` when TRON is enabled;
- waits for `tron-usdt-payouts` when that worker Deployment is rendered;
- verifies the TRON API pod and the sequential payout worker topology.

The chart is not upgraded with `--atomic` or `--wait`: the upstream chart can
render unused PVCs that stay in `WaitForFirstConsumer`, which makes Helm wait
time out even when the relevant deployments are healthy.

The equivalent direct Helm command is:

```bash
python3 /opt/shkeeper.io/deploy/shkeeper/payout-secret-guard.py verify-cluster \
  --namespace shkeeper

helm upgrade --install -n default -f /root/shkeeper-values.yaml \
  shkeeper oci://ghcr.io/nilof470/helm-charts/shkeeper \
  --version 1.7.28-nilof470.9 --timeout 300s

/opt/shkeeper.io/deploy/shkeeper/verify-tron-usdt-payout-worker.py \
  --namespace shkeeper \
  --deployment tron-shkeeper \
  --worker-deployment tron-usdt-payouts
```

Use the wrapper in production so the Helm command and verification do not drift.

Expected TRON pod shape after deploy:

```text
tron-shkeeper ... 3/3 Running
tron-usdt-payouts ... 1/1 Running
```

Do not leave `TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED=true` in production
without the worker invariant once the rail is active. A staged rail with
`paused=true` or `killSwitch=true` may not render the worker yet; after enabling
execution, run the verifier with `--required` if you need a hard worker gate.
