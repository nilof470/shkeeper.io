# SHKeeper Helm Deploy Notes

The Helm chart fork is the source of truth for Kubernetes manifests. Production
deploys must use the published OCI chart and explicit chart version directly;
do not depend on a local checkout or a wrapper script on a new VPS.

The chart fork is published as a versioned OCI chart:

```text
oci://ghcr.io/nilof470/helm-charts/shkeeper
version: 1.7.28-nilof470.10
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

## Production Command

The chart is not upgraded with `--atomic` or `--wait`: the upstream chart can
render unused PVCs that stay in `WaitForFirstConsumer`, which makes Helm wait
time out even when the relevant deployments are healthy.

```bash
export HELM_RELEASE_NAMESPACE=default
export SHKEEPER_WORKLOAD_NAMESPACE=shkeeper
export CHART_VERSION=1.7.28-nilof470.10

helm show chart oci://ghcr.io/nilof470/helm-charts/shkeeper \
  --version "${CHART_VERSION}"

helm upgrade --install -n "${HELM_RELEASE_NAMESPACE}" \
  -f /root/shkeeper-values.yaml \
  -f /root/shkeeper-payout-values.yaml \
  shkeeper oci://ghcr.io/nilof470/helm-charts/shkeeper \
  --version "${CHART_VERSION}" \
  --timeout 300s

kubectl rollout status deployment/shkeeper-deployment \
  -n "${SHKEEPER_WORKLOAD_NAMESPACE}" --timeout=180s
kubectl rollout status deployment/tron-shkeeper \
  -n "${SHKEEPER_WORKLOAD_NAMESPACE}" --timeout=180s
```

Expected TRON pod shape after deploy:

```text
tron-shkeeper ... 3/3 Running
tron-usdt-payouts ... 1/1 Running
```

Do not leave `TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED=true` in production
without the worker invariant once the rail is active. A staged rail with
`paused=true` or `killSwitch=true` may not render the worker yet; after enabling
execution, run the verifier with `--required` if you need a hard worker gate.

Optional topology verification:

```bash
python3 deploy/shkeeper/verify-tron-usdt-payout-worker.py \
  --namespace shkeeper \
  --deployment tron-shkeeper \
  --worker-deployment tron-usdt-payouts
```
