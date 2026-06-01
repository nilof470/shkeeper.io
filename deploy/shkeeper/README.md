# SHKeeper Production Deploy Wrapper

This directory contains the production deploy wrapper for this SHKeeper fork.
Use it instead of running `helm upgrade` directly on production.

The chart itself lives in a separate fork project, expected next to this repo:

```text
/opt/shkeeper.io
/opt/shkeeper-helm-charts
```

If it is not present yet:

```bash
cd /opt
git clone https://github.com/nilof470/helm-charts.git shkeeper-helm-charts
```

The upstream `vsys-host/helm-charts` chart renders the TRON sidecar with three
containers: `app`, `tasks`, and `redis`. Our chart fork renders one additional
worker in the same pod when TRON USDT payout resource provisioning is enabled:
`tron-usdt-payouts`.

The worker must be in the same pod because the TRON sidecar Redis broker is
pod-local. Running it as a separate Deployment is unsafe unless `REDIS_HOST` is
moved to a shared Redis service.

The wrapper applies the chart fork directly. There is no post-renderer and no
Python YAML dependency in the deploy path.

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

Use `CHART=/path/to/charts/shkeeper` if the chart fork is not checked out next
to `shkeeper.io`.

The wrapper:

- runs `helm upgrade --install --atomic` against the chart fork;
- waits for `shkeeper-deployment`;
- waits for `tron-shkeeper` when TRON is enabled;
- verifies `tron-usdt-payouts` when
  `TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED=true`.

Expected TRON pod shape after deploy:

```text
tron-shkeeper ... 4/4 Running
```

Do not leave `TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED=true` in production
without this worker invariant. The TRON sidecar also fails closed when the queue
has no consumer, but the deploy wrapper prevents that state during normal
operations.
