# SHKeeper Production Deploy Wrapper

This directory contains the guarded production deploy entry point for this
SHKeeper fork. The Helm chart fork is the source of truth for Kubernetes
manifests; this wrapper only standardizes the production command, waits for
rollouts, and runs post-deploy verification.

The chart fork is published as a versioned OCI chart:

```text
oci://ghcr.io/nilof470/helm-charts/shkeeper
version: 1.7.28-nilof470.1
```

For a private GHCR package, log Helm in once on the VPS:

```bash
echo "GITHUB_TOKEN_WITH_READ_PACKAGES" | helm registry login ghcr.io -u nilof470 --password-stdin
```

The upstream `vsys-host/helm-charts` chart renders the TRON sidecar with three
containers: `app`, `tasks`, and `redis`. Our chart fork renders one additional
worker in the same pod when TRON USDT payout resource provisioning is enabled:
`tron-usdt-payouts`.

The worker must be in the same pod because the TRON sidecar Redis broker is
pod-local. Running it as a separate Deployment is unsafe unless `REDIS_HOST` is
moved to a shared Redis service.

The wrapper applies the published chart fork directly. There is no local chart
clone, post-renderer, or Python YAML dependency in the deploy path.

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

The wrapper:

- runs `helm upgrade --install` against the chart fork without Helm wait mode;
- waits for `shkeeper-deployment`;
- waits for `tron-shkeeper` when TRON is enabled;
- verifies `tron-usdt-payouts` when
  `TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED=true`.

The chart is not upgraded with `--atomic` or `--wait`: the upstream chart can
render unused PVCs that stay in `WaitForFirstConsumer`, which makes Helm wait
time out even when the relevant deployments are healthy.

The equivalent direct Helm command is:

```bash
helm upgrade --install -n default -f /root/shkeeper-values.yaml \
  shkeeper oci://ghcr.io/nilof470/helm-charts/shkeeper \
  --version 1.7.28-nilof470.1 --timeout 300s

/opt/shkeeper.io/deploy/shkeeper/verify-tron-usdt-payout-worker.py \
  --namespace shkeeper \
  --deployment tron-shkeeper
```

Use the wrapper in production so the Helm command and verification do not drift.

Expected TRON pod shape after deploy:

```text
tron-shkeeper ... 4/4 Running
```

Do not leave `TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED=true` in production
without this worker invariant. The TRON sidecar also fails closed when the queue
has no consumer, but the deploy wrapper prevents that state during normal
operations.
