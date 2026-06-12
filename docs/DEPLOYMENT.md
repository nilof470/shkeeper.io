# SHKeeper Deployment Runbook

This guide records the deployment procedure used for the re:Fee-enabled
`tron-shkeeper` fork. It is written so the same process can be repeated for
production without relying on chat history.

Do not commit real API keys, wallet passwords, GitHub tokens, or generated
Kubernetes secrets. Keep `/root/shkeeper-values.yaml` on the target server or in
a private secret store.

## Deployment Shape

Use the SHKeeper fork deployment model:

- k3s on the VPS
- Helm chart fork from `oci://ghcr.io/nilof470/helm-charts/shkeeper`
- custom private GHCR image for the main `shkeeper.io` app
- custom private GHCR image for `tron-shkeeper`
- custom private GHCR image for `ton-shkeeper` when using the TON scanner
  resilience fix
- custom private GHCR image for `ethereum-shkeeper` when using ETH-USDT payouts
- Kubernetes `imagePullSecret` for the private image
- re:Fee as the TRC20 energy provider

The chart runs the TRON sidecar API as one pod. Base TRON has three containers:

- `app`: `gunicorn run:server`
- `tasks`: `celery -A celery_worker.celery worker ...`
- `redis`: local Redis for the sidecar

When TRON USDT payout execution is active, the chart fork renders a separate
`tron-usdt-payouts` Deployment with `concurrency=1` and `prefetchMultiplier=1`.
The worker reaches the TRON Redis broker through the `tron-shkeeper` Service on
port `6379`.

## Local SHKeeper Core Release Build

Run from the local `shkeeper.io` checkout.

```bash
cd /Users/test/PycharmProjects/shkeeper.io
git status --short --branch

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v
PYTHONPYCACHEPREFIX=/private/tmp/shkeeper-pycache .venv/bin/python -m compileall -q shkeeper tests
git diff --check

git add REPLACE_WITH_CHANGED_FILES
git commit -m "REPLACE_WITH_RELEASE_COMMIT_MESSAGE"
git push origin "$(git branch --show-current)"
TAG=$(git rev-parse --short HEAD)
echo "$TAG"
```

Do not build a release image from a dirty working tree. The Docker tag is the
current commit short SHA, so commit and push the code first.

Build and push the `linux/amd64` image:

```bash
docker login ghcr.io -u nilof470

docker buildx build \
  --platform linux/amd64 \
  -t ghcr.io/nilof470/shkeeper.io:${TAG} \
  --push .
```

Verify the remote manifest:

```bash
docker buildx imagetools inspect ghcr.io/nilof470/shkeeper.io:${TAG}
```

## Local TRON Release Build

Run from the local repository checkout.

```bash
cd /Users/test/PycharmProjects/tron-shkeeper
git checkout master
git pull origin master
git status --short --branch
git add REPLACE_WITH_CHANGED_FILES
git commit -m "REPLACE_WITH_RELEASE_COMMIT_MESSAGE"
git push origin "$(git branch --show-current)"
TAG=$(git rev-parse --short HEAD)
echo "$TAG"
```

Do not build a release image from a dirty working tree. The Docker tag is the
current commit short SHA, so commit and push the code first, then build the
image with that new tag. Reusing an existing tag can leave k3s running a cached
old image when `imagePullPolicy` is `IfNotPresent`.

Run tests before building:

```bash
/tmp/tron-shkeeper-py312-venv/bin/python -m unittest discover -s tests
```

Log in to GHCR once per workstation session if needed. Use a GitHub token with
`repo`, `write:packages`, and `read:packages` for private packages.

```bash
docker login ghcr.io -u nilof470
```

Build and push the `linux/amd64` image:

```bash
docker buildx build \
  --platform linux/amd64 \
  -t ghcr.io/nilof470/tron-shkeeper:${TAG} \
  --push .
```

Verify the remote manifest:

```bash
docker buildx imagetools inspect ghcr.io/nilof470/tron-shkeeper:${TAG}
```

Record the tag and digest in the release notes. Example:

```text
ghcr.io/nilof470/tron-shkeeper:5a6133b
sha256:48fbe2727c428965e4b74baccb29bd3aefcbdba3c0b15aeee57c134e04cef281
```

## Local TON Release Build

Use this section for the forked `ton-shkeeper` image that contains the scanner
resilience fix for transient Toncenter indexer `404` gaps and canonical payout
consumer key support.

Run from the local `ton-shkeeper` checkout:

```bash
cd /Users/test/PycharmProjects/ton-shkeeper
git checkout fix/ton-scanner-indexer-404-resilience
git status --short --branch

.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall app tests

TAG=$(git rev-parse --short HEAD)
echo "$TAG"
```

The current payout-compatible TON fix commit is:

```text
3691bb3 Accept canonical payout consumer keys
```

Do not build from a dirty working tree. The Docker tag should be the commit
short SHA so k3s cannot reuse an old cached image by mistake.

Build and push the `linux/amd64` image to GHCR:

```bash
docker login ghcr.io -u nilof470

docker buildx build \
  --platform linux/amd64 \
  -t ghcr.io/nilof470/ton-shkeeper:${TAG} \
  --push .
```

Verify the remote manifest:

```bash
docker buildx imagetools inspect ghcr.io/nilof470/ton-shkeeper:${TAG}
```

Record the final image tag in release notes and in `/root/shkeeper-values.yaml`.
For the current fix branch the expected tag is:

```text
ghcr.io/nilof470/ton-shkeeper:3691bb3
```

## Local ETH Release Build

Use this section for the owned `ethereum-shkeeper` fork. ETH-USDT client
payouts must stay disabled until the forked image is built, pushed, referenced
by Helm, and verified with the payout worker.

Run from the local `ethereum-shkeeper` checkout:

```bash
cd /Users/test/PycharmProjects/ethereum-shkeeper
git status --short --branch

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -v
PYTHONPYCACHEPREFIX=/private/tmp/ethereum-shkeeper-pycache .venv/bin/python -m compileall -q app tests
git diff --check

git add REPLACE_WITH_CHANGED_FILES
git commit -m "REPLACE_WITH_RELEASE_COMMIT_MESSAGE"
git push origin "$(git branch --show-current)"
TAG=$(git rev-parse --short HEAD)
echo "$TAG"
```

Build and push the `linux/amd64` image to GHCR:

```bash
docker login ghcr.io -u nilof470

docker buildx build \
  --platform linux/amd64 \
  -t ghcr.io/nilof470/ethereum-shkeeper:${TAG} \
  --push .
```

Verify the remote manifest:

```bash
docker buildx imagetools inspect ghcr.io/nilof470/ethereum-shkeeper:${TAG}
```

For the current payout auth fix the expected tag is:

```text
ghcr.io/nilof470/ethereum-shkeeper:69511a3
```

## Local Helm Chart Release

Run from the local `shkeeper-helm-charts` checkout after committing the chart
changes that render payout workers, migration jobs, rail sync, services,
network policies, and payout values.

```bash
cd /Users/test/PycharmProjects/shkeeper-helm-charts
git status --short --branch

python3 -m unittest tests/test_shkeeper_fork_chart.py -v
helm lint charts/shkeeper

git add REPLACE_WITH_CHANGED_FILES
git commit -m "REPLACE_WITH_CHART_RELEASE_COMMIT_MESSAGE"
git push origin "$(git branch --show-current)"
```

The payout chart version is recorded in `charts/shkeeper/Chart.yaml`. Do not
reuse an already published OCI chart version for different chart contents.

```bash
PAYOUT_CHART_VERSION=$(awk '/^version:/ {print $2; exit}' charts/shkeeper/Chart.yaml)

helm package charts/shkeeper --version "${PAYOUT_CHART_VERSION}"
helm registry login ghcr.io -u nilof470
helm push "shkeeper-${PAYOUT_CHART_VERSION}.tgz" oci://ghcr.io/nilof470/helm-charts

helm show chart oci://ghcr.io/nilof470/helm-charts/shkeeper \
  --version "${PAYOUT_CHART_VERSION}"
```

## USDT Payout Release Command Sequence

Use this sequence after the SHKeeper core, ETH sidecar, TON sidecar, and TRON
sidecar changes have been reviewed, committed, and pushed. Do not build images
from uncommitted payout changes.

The clean release gate also verifies that the checked-in Helm production
overlay `image:` fields point at the exact clean commit tags below. Update and
commit the Helm overlay image tags before running `--require-clean`.

```bash
SHKEEPER_TAG=$(git -C /Users/test/PycharmProjects/shkeeper.io rev-parse --short HEAD)
TRON_TAG=$(git -C /Users/test/PycharmProjects/tron-shkeeper rev-parse --short HEAD)
TON_TAG=$(git -C /Users/test/PycharmProjects/ton-shkeeper rev-parse --short HEAD)
ETH_TAG=$(git -C /Users/test/PycharmProjects/ethereum-shkeeper rev-parse --short HEAD)
PAYOUT_CHART_VERSION=$(awk '/^version:/ {print $2; exit}' /Users/test/PycharmProjects/shkeeper-helm-charts/charts/shkeeper/Chart.yaml)

cd /Users/test/PycharmProjects/shkeeper-helm-charts
perl -0pi -e "s|ghcr.io/nilof470/shkeeper.io:[A-Za-z0-9._-]+|ghcr.io/nilof470/shkeeper.io:${SHKEEPER_TAG}|g" \
  charts/shkeeper/environments/values-prod-*-payout.yaml
perl -0pi -e "s|ghcr.io/nilof470/tron-shkeeper:[A-Za-z0-9._-]+|ghcr.io/nilof470/tron-shkeeper:${TRON_TAG}|g" \
  charts/shkeeper/environments/values-prod-tron-payout.yaml
perl -0pi -e "s|ghcr.io/nilof470/ton-shkeeper:[A-Za-z0-9._-]+|ghcr.io/nilof470/ton-shkeeper:${TON_TAG}|g" \
  charts/shkeeper/environments/values-prod-ton-payout.yaml
perl -0pi -e "s|ghcr.io/nilof470/ethereum-shkeeper:[A-Za-z0-9._-]+|ghcr.io/nilof470/ethereum-shkeeper:${ETH_TAG}|g" \
  charts/shkeeper/environments/values-prod-eth-payout.yaml
git diff -- charts/shkeeper/environments

git add charts/shkeeper/environments/values-prod-*-payout.yaml
git commit -m "Update payout production image tags"
git push origin "$(git branch --show-current)"

cd /Users/test/PycharmProjects/shkeeper.io
python3 scripts/verify_payout_release_gate.py --require-clean

docker login ghcr.io -u nilof470

docker buildx build --platform linux/amd64 \
  -t ghcr.io/nilof470/shkeeper.io:${SHKEEPER_TAG} \
  --push /Users/test/PycharmProjects/shkeeper.io

docker buildx build --platform linux/amd64 \
  -t ghcr.io/nilof470/tron-shkeeper:${TRON_TAG} \
  --push /Users/test/PycharmProjects/tron-shkeeper

docker buildx build --platform linux/amd64 \
  -t ghcr.io/nilof470/ton-shkeeper:${TON_TAG} \
  --push /Users/test/PycharmProjects/ton-shkeeper

docker buildx build --platform linux/amd64 \
  -t ghcr.io/nilof470/ethereum-shkeeper:${ETH_TAG} \
  --push /Users/test/PycharmProjects/ethereum-shkeeper

docker buildx imagetools inspect ghcr.io/nilof470/shkeeper.io:${SHKEEPER_TAG}
docker buildx imagetools inspect ghcr.io/nilof470/tron-shkeeper:${TRON_TAG}
docker buildx imagetools inspect ghcr.io/nilof470/ton-shkeeper:${TON_TAG}
docker buildx imagetools inspect ghcr.io/nilof470/ethereum-shkeeper:${ETH_TAG}

cd /Users/test/PycharmProjects/shkeeper-helm-charts
python3 -m unittest tests/test_shkeeper_fork_chart.py -v
helm lint charts/shkeeper
helm package charts/shkeeper --version "${PAYOUT_CHART_VERSION}"
helm registry login ghcr.io -u nilof470
helm push "shkeeper-${PAYOUT_CHART_VERSION}.tgz" oci://ghcr.io/nilof470/helm-charts
helm show chart oci://ghcr.io/nilof470/helm-charts/shkeeper \
  --version "${PAYOUT_CHART_VERSION}"
```

Deploy the staged release with all rails still paused and kill-switched in
`/root/shkeeper-payout-values.yaml`. The production VPS deploy path must not
depend on a local `/opt/shkeeper.io` checkout or on `git pull`; use the
published OCI chart and root-only values files as the source of truth.

```bash
export HELM_NS=default
export APP_NS=shkeeper
export CHART_REF=oci://ghcr.io/nilof470/helm-charts/shkeeper
export CHART_VERSION=1.7.28-nilof470.13

helm -n "$HELM_NS" get values shkeeper -o yaml > /root/shkeeper-current-values.yaml

helm upgrade shkeeper "$CHART_REF" \
  --version "$CHART_VERSION" \
  -n "$HELM_NS" \
  -f /root/shkeeper-current-values.yaml \
  -f /root/shkeeper-payout-values.yaml \
  --timeout 15m \
  --history-max 2
```

Enable only one rail at a time after the restore-drill, smoke-payout, callback,
and upstream ledger gates pass by changing that rail's `paused` and
`killSwitch` values to `false`, then rerunning the same Helm upgrade command.

### Production TRON Payout Activation

Persist production payout activation in `/root/shkeeper-payout-values.yaml`.
Do not use `--set` for normal payout operation. Edit the root-only values file
and rerun the same file-only Helm upgrade so the server state remains
recoverable.

The TRON payout release validated on 2026-06-06 used these images:

```text
ghcr.io/nilof470/shkeeper.io:92263d0
sha256:d0da1a8763f72c1e8f66a1755bc985d2c8414ac124c8335bfc71813fd29fc92e

ghcr.io/nilof470/tron-shkeeper:038e93b
sha256:7a98513490d7d84db0316a850d609dbd3b208c41e79c3043bb5cae8a7423d399
```

The persistent TRON payout block must be present in
`/root/shkeeper-payout-values.yaml`:

```yaml
payouts:
  enabled: true
  rails:
    tronUsdt:
      enabled: true
      paused: false
      killSwitch: false
      queue: tron_usdt_fee_payouts
      sidecarService: tron-shkeeper
      sidecarSymbol: USDT
      sourceWalletRef: fee_deposit
      ownedImageRepository: ghcr.io/nilof470/tron-shkeeper
      executionStateStorage: sidecar-db
      callbackEndpointId: grither-pay-main
```

Runtime tuning that belongs to the TRON sidecar can stay under
`tron_shkeeper.extraEnv`:

```yaml
tron_shkeeper:
  extraEnv:
    PAYOUT_RESOURCE_POST_ACTIVE_RECHECK_ATTEMPTS: "10"
    PAYOUT_RESOURCE_POST_ACTIVE_RECHECK_SLEEP_SEC: "2"
    BLOCK_SCANNER_MAX_BLOCK_CHUNK_SIZE: "5"
    BLOCK_SCANNER_INTERVAL_TIME: "1"
    BLOCK_SCANNER_STATS_LOG_PERIOD: "60"
```

Do not set chart-owned payout env keys directly in `tron_shkeeper.extraEnv`.
The chart renders them from `payouts.rails` and Kubernetes Secret references.
If these keys are present directly, Helm must fail before deployment:

```text
TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED
TRON_USDT_PAYOUT_QUEUE
PAYOUT_CONSUMER_KEYS_JSON
PAYOUT_AUTH_MAX_AGE_SECONDS
PAYOUT_EXECUTION_AUTO_ENQUEUE_ENABLED
PAYOUT_EXECUTION_PREFLIGHT_CHECKS_ENABLED
```

Before production upgrade, check for forbidden direct env keys:

```bash
grep -n "TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED\|TRON_USDT_PAYOUT_QUEUE" \
  /root/shkeeper-values.yaml /root/shkeeper-payout-values.yaml || true
grep -n -A30 "tron_shkeeper:" \
  /root/shkeeper-values.yaml /root/shkeeper-payout-values.yaml
```

If any chart-owned key is under `tron_shkeeper.extraEnv`, remove it and express
the setting through `payouts.rails` or the chart's payout secret references
instead. In managed-secret mode the chart renders `PAYOUT_CONSUMER_KEYS_JSON`
for sidecars from `payouts.auth.shkeeperToSidecars`; in legacy external-secret
mode it may be referenced as `payouts.secrets.sidecarConsumerKeys.key`. It is
only invalid as a direct sidecar `extraEnv` value.

Use the file-only deployment shape on production. Image tags, rail state, and
payout auth all come from the two root-owned values files.

```bash
export HELM_NS=default
export APP_NS=shkeeper
export CHART_REF=oci://ghcr.io/nilof470/helm-charts/shkeeper
export CHART_VERSION=1.7.28-nilof470.13

helm show chart oci://ghcr.io/nilof470/helm-charts/shkeeper \
  --version "$CHART_VERSION"

helm -n "$HELM_NS" get values shkeeper -o yaml > /root/shkeeper-current-values.yaml

helm upgrade shkeeper "$CHART_REF" \
  --version "$CHART_VERSION" \
  -n "$HELM_NS" \
  -f /root/shkeeper-current-values.yaml \
  -f /root/shkeeper-payout-values.yaml \
  --timeout 15m \
  --history-max 2
```

Do not use `--no-hooks` for the normal payout deployment path. The chart hooks
run payout migrations and rail sync jobs; skipping them can leave a release with
new Deployments but stale payout schema or rail state.

Verify the release after the upgrade:

```bash
helm status shkeeper -n "$HELM_NS"
helm history shkeeper -n "$HELM_NS" --max 5

helm get values shkeeper -n "$HELM_NS" -a | grep -A20 "tronUsdt:"

kubectl rollout status deployment/shkeeper-deployment \
  -n "$APP_NS" --timeout=180s
kubectl rollout status deployment/tron-shkeeper \
  -n "$APP_NS" --timeout=180s
kubectl rollout status deployment/shkeeper-payout-execution-reconciler \
  -n "$APP_NS" --timeout=180s
kubectl rollout status deployment/shkeeper-payout-callback-dispatcher \
  -n "$APP_NS" --timeout=180s
kubectl rollout status deployment/tron-usdt-payouts \
  -n "$APP_NS" --timeout=180s

kubectl get deploy -A | grep -E 'tron-usdt-payouts|tron-shkeeper|shkeeper-payout'
kubectl get pods -n "$APP_NS" -o wide
```

The expected active TRON payout values are:

```yaml
enabled: true
paused: false
killSwitch: false
queue: tron_usdt_fee_payouts
```

Check the deployed image tags:

```bash
kubectl get deployment shkeeper-deployment -n "$APP_NS" \
  -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}'
kubectl get deployment tron-shkeeper -n "$APP_NS" \
  -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}'
kubectl get deployment tron-usdt-payouts -n "$APP_NS" \
  -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}'
```

Check payout logs:

```bash
kubectl logs deployment/tron-usdt-payouts \
  -n "$APP_NS" --tail=100
kubectl logs deployment/shkeeper-payout-execution-reconciler \
  -n "$APP_NS" --tail=100
kubectl logs deployment/shkeeper-payout-callback-dispatcher \
  -n "$APP_NS" --tail=100
kubectl logs deployment/tron-shkeeper \
  -n "$APP_NS" --all-containers --tail=100
```

Finish the production gate with a small Grither Pay TRON USDT payout. Confirm
the user-facing payout is created from the mini app, the Grither Pay backend
creates the SHKeeper payout execution, `tron-usdt-payouts` sends the transaction,
the transaction appears on-chain, and the callback/status flow reaches Grither
Pay.

After a successful smoke payout, save the working server state:

```bash
helm get values shkeeper -n "$HELM_NS" -a \
  > /root/shkeeper-values-working-$(date +%Y%m%d%H%M%S).yaml
kubectl get deploy -n "$APP_NS" -o wide \
  > /root/shkeeper-deployments-working-$(date +%Y%m%d%H%M%S).txt
```

Troubleshooting:

- `Could not locate a version matching provided version string` means
  `CHART_VERSION` is empty or wrong. Export
  `CHART_VERSION=1.7.28-nilof470.13` and confirm with `helm show chart`.
- `tron_shkeeper.extraEnv.TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED is
  managed by payouts.rails` means a chart-owned key is set directly under
  `tron_shkeeper.extraEnv`. Remove the direct env value from the values file.
- `deployment/tron-usdt-payouts not found` means either the workload namespace is
  wrong or the final Helm values still have `payouts.rails.tronUsdt.paused=true`
  or `payouts.rails.tronUsdt.killSwitch=true`. Check with
  `helm get values shkeeper -n "$HELM_NS" -a | grep -A20 "tronUsdt:"`,
  then fix `/root/shkeeper-payout-values.yaml` and rerun the same
  Helm upgrade.

## VPS Preflight

If replacing another stack such as Bitcart, stop and remove it before
installing SHKeeper. These commands are destructive for that old stack.

```bash
docker ps -a
docker compose ls
systemctl stop bitcart.service || true
systemctl disable bitcart.service || true
rm -f /etc/systemd/system/bitcart.service
rm -f /etc/profile.d/bitcart-env.sh
systemctl daemon-reload
rm -rf /root/bitcart-docker
```

Check that required ports are free and the server has enough disk and memory:

```bash
ss -ltnp | grep -E ':(80|443|5000)\b' || true
df -h
free -h
```

## Install k3s and Helm

Run as `root` on the VPS.

```bash
curl -sfL https://get.k3s.io | sh -
mkdir -p /root/.kube
ln -sf /etc/rancher/k3s/k3s.yaml /root/.kube/config
chmod 600 /etc/rancher/k3s/k3s.yaml
kubectl get nodes
```

Install Helm and add the third-party secret-generator chart repository:

```bash
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
helm version

helm repo add mittwald https://helm.mittwald.de
helm repo update
```

Install the secret generator used by the official chart:

```bash
helm install kubernetes-secret-generator mittwald/kubernetes-secret-generator
helm list -A
```

Log Helm in to GHCR if the chart package is private. Create a GitHub token with
`read:packages` at `https://github.com/settings/tokens/new?scopes=read:packages`.

```bash
read -s GHCR_TOKEN
echo "$GHCR_TOKEN" | helm registry login ghcr.io -u nilof470 --password-stdin
unset GHCR_TOKEN

helm show chart oci://ghcr.io/nilof470/helm-charts/shkeeper --version 1.7.28-nilof470.13
```

## Namespace and Private GHCR Pull Secret

The chart creates a namespace object, but the pull secret must exist before the
TRON sidecar pod pulls the private image. Pre-create the namespace and annotate
it so Helm can adopt it.

```bash
kubectl create namespace shkeeper --dry-run=client -o yaml | kubectl apply -f -
kubectl label namespace shkeeper app.kubernetes.io/managed-by=Helm --overwrite
kubectl annotate namespace shkeeper \
  meta.helm.sh/release-name=shkeeper \
  meta.helm.sh/release-namespace=default \
  --overwrite
```

Create the GHCR pull secret. Paste the GitHub token when prompted; it will not
be displayed.

```bash
read -s GHCR_TOKEN

kubectl -n shkeeper create secret docker-registry ghcr-nilof470 \
  --docker-server=ghcr.io \
  --docker-username=nilof470 \
  --docker-password="$GHCR_TOKEN" \
  --docker-email=none@example.com \
  --dry-run=client -o yaml | kubectl apply -f -

unset GHCR_TOKEN
kubectl get secret -n shkeeper ghcr-nilof470
```

## Helm Values

Create `/root/shkeeper-values.yaml`. Replace placeholders before installing.

For production, set `domain` to the public hostname and point DNS to the VPS.
For a temporary dev install, `domain: ""` and direct port `5000` access are
acceptable.

```yaml
namespace: shkeeper
storageClassName: local-path
domain: ""

dev:
  imagePullSecrets:
    - name: ghcr-nilof470

btc:
  enabled: false
ltc:
  enabled: false
doge:
  enabled: false

tron_fullnode:
  enabled: false
  url: http://fullnode.tron.shkeeper.io
  mainnet: true

tron_shkeeper:
  image: ghcr.io/nilof470/tron-shkeeper:REPLACE_WITH_TAG
  extraEnv:
    ENERGY_SOURCE: refee
    REFEE: '{"api_key":"REPLACE_WITH_REFEE_API_KEY","rent_duration_label":"1h"}'
    REFEE_FIXED_ENERGY_ORDER_AMOUNT: "65000"
    ENERGY_DELEGATION_MODE_ALLOW_BURN_TRX_ON_PAYOUT: "false"
    ENERGY_DELEGATION_MODE_ALLOW_BURN_TRX_FOR_BANDWITH: "true"
    USDT_MIN_TRANSFER_THRESHOLD: "0.5"
    TRX_MIN_TRANSFER_THRESHOLD: "1.01"

trx:
  enabled: true
usdt:
  enabled: true
usdc:
  enabled: false
```

Notes:

- `ENERGY_DELEGATION_MODE_ALLOW_BURN_TRX_ON_PAYOUT=false` prevents fallback to
  funding onetime wallets for TRC20 transfer fee burn if re:Fee fails.
- `ENERGY_DELEGATION_MODE_ALLOW_BURN_TRX_FOR_BANDWITH=true` allows TRX burn for
  account activation bandwidth. Keep this only if activation burn is acceptable.
- `REFEE_FIXED_ENERGY_ORDER_AMOUNT=65000` ensures at least 65k energy is
  available before a USDT sweep. Set it to `0` to return to fullnode
  estimate-based sizing. Nonzero values must be greater than or equal to the
  configured re:Fee `min_energy_order_amount`.
- `USDT_MIN_TRANSFER_THRESHOLD` must be lower than the smallest USDT payment that
  should be swept. The TRC20 sweep check requires `balance > threshold`.
- `TRX_MIN_TRANSFER_THRESHOLD` prevents sweeping activation dust. TRX sweep uses
  `balance >= threshold`, so use a value above dust, for example `1.01`.

## Production Environment Checklist

Keep production-only values in `/root/shkeeper-values.yaml` or a private secret
store. Do not commit real API keys, callback URLs, wallet passwords, admin
passwords, or Kubernetes secrets.

### Production Deploy Entry Point

The Helm chart fork is the source of truth for Kubernetes manifests. It is
published as `oci://ghcr.io/nilof470/helm-charts/shkeeper` version
`1.7.28-nilof470.13`. Use the published OCI chart directly for production
deploys. This keeps a new VPS deployment independent from a local chart
checkout.

Do not deploy this fork with the upstream `vsys-host/shkeeper` chart when TRON
USDT payout resource provisioning is enabled: upstream renders the TRON sidecar
with the base `3/3` pod shape, while this fork owns the additional
`tron-usdt-payouts` worker Deployment.

The chart fork renders the TRON payout worker directly. There is no local chart
clone, post-renderer, or PyYAML dependency. When TRON USDT payout execution is
active, the chart renders `tron-shkeeper` as the API/tasks/redis sidecar and
`tron-usdt-payouts` as a separate sequential worker consuming
`tron_usdt_fee_payouts`.

```bash
helm show chart oci://ghcr.io/nilof470/helm-charts/shkeeper --version 1.7.28-nilof470.13
```

The production deploy command is a direct Helm upgrade from root-only values
files. The target VPS does not need a local repository checkout for this step.

```bash
export HELM_NS=default
export APP_NS=shkeeper
export CHART_REF=oci://ghcr.io/nilof470/helm-charts/shkeeper
export CHART_VERSION=1.7.28-nilof470.13

helm -n "$HELM_NS" get values shkeeper -o yaml > /root/shkeeper-current-values.yaml

helm upgrade shkeeper "$CHART_REF" \
  --version "$CHART_VERSION" \
  -n "$HELM_NS" \
  -f /root/shkeeper-current-values.yaml \
  -f /root/shkeeper-payout-values.yaml \
  --timeout 15m \
  --history-max 2

kubectl rollout status deployment/shkeeper-deployment -n "$APP_NS" --timeout=180s
kubectl rollout status deployment/tron-shkeeper -n "$APP_NS" --timeout=180s

# Run these after TRON payout execution is active.
kubectl rollout status deployment/tron-usdt-payouts -n "$APP_NS" --timeout=180s
kubectl get pods -n "$APP_NS" | grep tron-usdt-payouts
```

Expected TRON pod shape when payout execution is active:

```text
tron-shkeeper ... 3/3 Running
tron-usdt-payouts ... 1/1 Running
```

Do not add `--atomic` or `--wait` to this chart upgrade. The upstream chart can
render PVCs for disabled networks, and Kubernetes leaves those PVCs in
`WaitForFirstConsumer` until a matching pod is scheduled. Helm's wait mode can
therefore time out even when the SHKeeper and TRON deployments are healthy.
Verify the specific deployments with `kubectl rollout status` instead.

If the local values file is missing or empty on the server, export the active
Helm release values first:

```bash
helm list -A | grep shkeeper
helm get values shkeeper -n default -o yaml > /root/shkeeper-values.yaml
nano /root/shkeeper-values.yaml
```

If `helm list -A` shows the Helm release in another namespace, use that
namespace in `helm get values` and in `helm upgrade -n ...`.

Core image tags:

```yaml
shkeeper:
  image: ghcr.io/nilof470/shkeeper.io:0e4c415

tron_shkeeper:
  image: ghcr.io/nilof470/tron-shkeeper:REPLACE_WITH_TAG

aml_shkeeper:
  image: ghcr.io/nilof470/aml-shkeeper:f17e309
```

AML / Koinkyt settings:

```yaml
shkeeper:
  extraEnv:
    AML_ENABLED: "true"
    AML_PROVIDER: koinkyt
    AML_SHKEEPER_HOST: http://aml-shkeeper:6000
    AML_SHKEEPER_USERNAME: "REPLACE_WITH_INTERNAL_AML_USERNAME"
    AML_SHKEEPER_PASSWORD: "REPLACE_WITH_INTERNAL_AML_PASSWORD"
    AML_MAX_ACCEPT_SCORE: "0.70"
    AML_MIN_CHECK_AMOUNT_FIAT: "100"
    AML_SKIP_CUMULATIVE_LIMIT_FIAT: "300"
    AML_SKIP_CUMULATIVE_WINDOW_HOURS: "24"
    AML_PENDING_TIMEOUT_SECONDS: "1800"
    AML_RETRY_DELAY_SECONDS: "120"

aml:
  enabled: true

aml_shkeeper:
  extraEnv:
    AML_USERNAME: "REPLACE_WITH_INTERNAL_AML_USERNAME"
    AML_PASSWORD: "REPLACE_WITH_INTERNAL_AML_PASSWORD"
    CURRENT_PROVIDER: koinkyt
    KOINKYT_HOST: https://explorer.coinkyt.com/openapi/v1
    KOINKYT_API_KEY: "REPLACE_WITH_KOINKYT_API_KEY"
    KOINKYT_RISK_PROFILE_IDS: ""
    AML_DEFAULT_THRESHOLD: "0.70"
    CHECK_TIMEOUT_SECONDS: "1800"
    CHECK_RETRY_SECONDS: "120"
    RECHECK_TXS_EVERY_SECONDS: "120"
    KOINKYT_REQUEST_TIMEOUT_SECONDS: "10"
```

`AML_MAX_ACCEPT_SCORE` in SHKeeper and `AML_DEFAULT_THRESHOLD` in
`aml-shkeeper` should normally match. `0.70` is the AML-gated sweep policy value;
raise it only with an explicit compliance decision.

AML parameter reference:

| Env var | Component | Default | Meaning |
| --- | --- | --- | --- |
| `AML_ENABLED` | `shkeeper` | `true` | Enables deposit AML handling in the main SHKeeper app. |
| `AML_PROVIDER` | `shkeeper` | `CURRENT_PROVIDER` or `koinkyt` | Provider coverage map used by SHKeeper. Supported values in this fork: `koinkyt`, `amlbot`. |
| `AML_SHKEEPER_HOST` | `shkeeper` | `http://aml-shkeeper:6000` | Internal URL of the AML sidecar API. |
| `AML_SHKEEPER_USERNAME` | `shkeeper` | `AML_USERNAME` or `shkeeper` | Basic Auth user for calls from SHKeeper to `aml-shkeeper`. |
| `AML_SHKEEPER_PASSWORD` | `shkeeper` | `AML_PASSWORD` or `shkeeper` | Basic Auth password for calls from SHKeeper to `aml-shkeeper`. |
| `AML_MAX_ACCEPT_SCORE` | `shkeeper` | `0.70` | Main accept threshold. A result with score `<=` this value is credited; higher score goes to manual review. |
| `AML_MIN_CHECK_AMOUNT_FIAT` | `shkeeper` | `100` | Deposits at or above this fiat value must be checked by AML. |
| `AML_SKIP_CUMULATIVE_LIMIT_FIAT` | `shkeeper` | `300` | Maximum cumulative fiat amount that may skip AML within the skip window. |
| `AML_SKIP_CUMULATIVE_WINDOW_HOURS` | `shkeeper` | `24` | Time window for the cumulative skip limit. |
| `AML_PENDING_TIMEOUT_SECONDS` | `shkeeper` | `1800` | Main app timeout before an unresolved AML check is moved to manual review. |
| `AML_RETRY_DELAY_SECONDS` | `shkeeper` | `120` | Main app delay before polling `aml-shkeeper` again. |
| `REQUESTS_TIMEOUT` | `shkeeper` | `10` | HTTP timeout used by the `aml-shkeeper` client and other internal requests. |
| `AML_USERNAME` | `aml-shkeeper` | `shkeeper` | Basic Auth user exposed by the AML sidecar. Must match `AML_SHKEEPER_USERNAME`. |
| `AML_PASSWORD` | `aml-shkeeper` | `shkeeper` | Basic Auth password exposed by the AML sidecar. Must match `AML_SHKEEPER_PASSWORD`. |
| `CURRENT_PROVIDER` | `aml-shkeeper` | `koinkyt` | Provider used by the sidecar. Supported values in this fork: `koinkyt`, `amlbot`. |
| `AML_DEFAULT_THRESHOLD` | `aml-shkeeper` | `0.70` | Stored on legacy checks, or on v1 checks only when SHKeeper does not send a threshold. Keep it aligned with `AML_MAX_ACCEPT_SCORE`. |
| `KOINKYT_API_KEY` | `aml-shkeeper` | empty | Required when `CURRENT_PROVIDER=koinkyt`. Sent as `X-API-Key`. |
| `KOINKYT_HOST` | `aml-shkeeper` | `https://explorer.coinkyt.com/openapi/v1` | Koinkyt API base URL. |
| `KOINKYT_RISK_PROFILE_IDS` | `aml-shkeeper` | empty | Optional comma- or semicolon-separated Koinkyt risk profile IDs. |
| `KOINKYT_REQUEST_TIMEOUT_SECONDS` | `aml-shkeeper` | `REQUESTS_TIMEOUT` or `10` | HTTP timeout for Koinkyt requests. |
| `AMLBOT_ACCESS_ID` | `aml-shkeeper` | empty | AMLBot access ID when `CURRENT_PROVIDER=amlbot`. |
| `AMLBOT_ACCESS_KEY` | `aml-shkeeper` | empty | AMLBot secret key when `CURRENT_PROVIDER=amlbot`. |
| `AMLBOT_ACCESS_POINT` | `aml-shkeeper` | `https://extrnlapiendpoint.silencatech.com` | AMLBot API base URL. |
| `AMLBOT_FLOW` | `aml-shkeeper` | `fast` | AMLBot flow: `fast`, `accurate`, or `advanced`. |
| `PROVIDERS` | `aml-shkeeper` | provider-specific defaults | Optional JSON override for provider config, credentials, risk profiles, and per-crypto `min_check_amount`. |
| `CHECK_TIMEOUT_SECONDS` | `aml-shkeeper` | `1800` | Sidecar timeout before a provider result becomes `timeout`. |
| `CHECK_RETRY_SECONDS` | `aml-shkeeper` | `120` | Delay before the sidecar retries a pending/checking provider result. |
| `RECHECK_TXS_EVERY_SECONDS` | `aml-shkeeper` | `120` | Celery periodic interval for sidecar rechecks. |
| `RETRY_UNTIL_FAILED` | `aml-shkeeper` | `3` | Number of retryable provider attempts before marking the sidecar check failed. |
| `REDIS_HOST` | `aml-shkeeper` | `localhost` | Redis host for Celery broker/backend. |
| `SQLALCHEMY_DATABASE_URI` | `aml-shkeeper` | MariaDB URI | Database URI for AML sidecar checks. |
| `SHKEEPER_BACKEND_KEY` | `aml-shkeeper` | `shkeeper` | Backend key for callbacks to SHKeeper if used by legacy flows. |
| `SHKEEPER_HOST` | `aml-shkeeper` | `shkeeper:5000` | SHKeeper host for sidecar-to-SHKeeper callbacks if used by legacy flows. |
| `DEBUG` | `aml-shkeeper` | `false` | Flask/debug flag. |
| `LOGGING_LEVEL` | `aml-shkeeper` | `INFO` | Sidecar logging level. |

`aml-shkeeper` supports these symbols by provider in the current fork:

- Koinkyt: `BTC`, `ETH`, `ETH-USDT`, `ETH-USDC`, `TRX`, `USDT`, `USDC`.
- AMLBot: `BTC`, `LTC`, `DOGE`, `ETH`, `ETH-USDT`, `ETH-USDC`,
  `ETH-PYUSD`, `TRX`, `USDT`, `USDC`, `SOL`, `SOLANA-USDT`,
  `SOLANA-USDC`, `SOLANA-PYUSD`.

TON assets are not AML-supported by these provider maps in the current code.

## Production VPS Capacity Notes

Do not run the full SHKeeper chart with many enabled networks on a small
`2 vCPU / 4 GB RAM / 20 GB SSD` VPS. A production incident on 2026-05-19 showed
that this shape can become unstable even when Linux memory and filesystem space
are not exhausted.

Observed failure pattern:

- VPS monitoring showed CPU near `70-100%`.
- Disk read/write latency and disk throttler latency jumped toward `500 ms`.
- Disk read load reached roughly `12 MB/s` and `300 IOPS`.
- Network traffic dropped close to zero after the disk latency spike.
- Connection quota utilization was low, so the failure was not caused by an
  inbound connection limit or obvious DDoS.
- `journalctl -b -1 -u k3s` showed repeated k3s/kine SQLite symptoms:
  `Slow SQL` queries taking `7-20s`, `context deadline exceeded`,
  `apiserver ... Handler timeout`, missed heartbeats, and failed lease/status
  updates.
- `dmesg` showed no OOM kill and no clear block-device I/O error after reboot.
- `df -h` was high but not full (`81%` before cleanup), and `df -i` was normal.

Root-cause assessment:

- The server was overloaded by combined k3s, SHKeeper, AML, MariaDB, TRON, TON,
  Ethereum, and BNB sidecars.
- The k3s control plane became unable to read/write its SQLite/kine datastore
  quickly enough under disk latency.
- The biggest steady CPU consumers were `bnb-shkeeper`,
  `ethereum-shkeeper`, and `ton-shkeeper`.
- Disabling `bnb-shkeeper` and removing event noise reduced node CPU from about
  `70%` to about `23%`; current `iostat` then showed near-zero `iowait` and
  disk `%util` under `1%`.

Recommended production sizing:

- Minimum: `4 vCPU / 8 GB RAM / 60 GB SSD`.
- Prefer `60-100 GB` SSD or larger with explicit IOPS/throughput headroom.
- For small VPS deployments, enable only the networks needed for production.
  For example, keep `bnb`, `bnb_usdt`, and `bnb_usdc` disabled unless BNB/BSC
  payments are required.
- If Ethereum, BNB, and TON are all production-critical, use a larger VPS or
  split heavy sidecars onto separate infrastructure.

Capacity checks after every Helm upgrade:

```bash
kubectl -n shkeeper rollout status deployment/shkeeper-deployment --timeout=180s
kubectl -n shkeeper get pods
kubectl -n shkeeper get deploy | grep -E 'bnb|ethereum|ton|tron|shkeeper'
kubectl top pods -A --sort-by=cpu
kubectl top nodes
iostat -xz 1 5
df -h
```

Read `iostat` carefully: the first report is the average since boot. For current
health, use the later per-interval lines. Healthy steady state should have low
current `iowait` and low disk `%util`.

Fast memory/capacity triage:

```bash
# From this repository checkout on the VPS:
sh contrib/vps-memory-triage.sh

# Show, but do not execute, the small-VPS stabilization commands.
sh contrib/vps-memory-triage.sh --stabilize-small-vps

# Execute stabilization only after confirming the listed networks are optional.
sh contrib/vps-memory-triage.sh --stabilize-small-vps --apply
```

By default the stabilization dry-run targets `bnb-shkeeper`,
`ethereum-shkeeper`, and `ton-shkeeper`, because those were the largest steady
consumers in the 2026-05-19 incident. Override the list when needed:

```bash
STABILIZE_DEPLOYS="bnb-shkeeper ethereum-shkeeper" \
  sh contrib/vps-memory-triage.sh --stabilize-small-vps --apply
```

Safe emergency stabilization on a small VPS:

```bash
# Free journal space.
journalctl --vacuum-size=200M

# Temporarily disable heavy networks that are not required immediately.
kubectl -n shkeeper scale deploy bnb-shkeeper --replicas=0
kubectl -n shkeeper scale deploy ethereum-shkeeper --replicas=0
kubectl -n shkeeper scale deploy ton-shkeeper --replicas=0

# Re-check the node and disk.
kubectl top nodes
kubectl top pods -A --sort-by=cpu
iostat -xz 1 5
```

Scaling a sidecar to `0` stops processing for that network. Make the same
choice persistent in `/root/shkeeper-values.yaml` before the next
Helm upgrade; otherwise Helm can recreate the deployment.

The official chart may create `Pending` PVCs for disabled or unused networks.
They do not contain data, but they generate repeated `WaitForFirstConsumer`
events and can add noise to the k3s event store on a weak VPS. After a Helm
upgrade, check:

```bash
kubectl -n shkeeper get pvc
kubectl -n shkeeper get events --sort-by=.lastTimestamp | tail -50
```

Only delete PVCs that are still `Pending`. Do not delete `Bound` PVCs such as
`mariadb`, `shkeeper-db-claim`, `tron-shkeeper-data`, or
`tron-shkeeper-redis-data`, because they contain persistent data.

Example cleanup for unused Pending PVCs:

```bash
kubectl -n shkeeper delete pvc \
  arbitrum-datadir avalanchego-volume bitcoind-claim bnb-datadir \
  bor-config-volume dogecoind-claim ethereum-datadir firod-claim \
  heimdall-config-volume lightning-lnbits lightning-lnd lightning-rtl \
  litecoind-claim monero-wallet-rpc monerod optimism-datadir \
  tron-output-directory
```

If old failed `create-db-bitcoin-shkeeper-*` jobs remain after BTC is disabled,
they are not a blocker once one initialization job completed and core pods are
healthy. They can be removed to reduce output noise:

```bash
kubectl -n shkeeper delete job \
  create-db-bitcoin-shkeeper-69t4h \
  create-db-bitcoin-shkeeper-bdwvg \
  create-db-bitcoin-shkeeper-dwf5h \
  create-db-bitcoin-shkeeper-mm972
```

TRON / USDT settings:

```yaml
tron_shkeeper:
  extraEnv:
    ENERGY_SOURCE: refee
    REFEE: '{"api_key":"REPLACE_WITH_REFEE_API_KEY","rent_duration_label":"1h"}'
    REFEE_FIXED_ENERGY_ORDER_AMOUNT: "65000"
    ENERGY_DELEGATION_MODE_ALLOW_BURN_TRX_ON_PAYOUT: "false"
    ENERGY_DELEGATION_MODE_ALLOW_BURN_TRX_FOR_BANDWITH: "true"
    USDT_MIN_TRANSFER_THRESHOLD: "1"
    USDC_MIN_TRANSFER_THRESHOLD: "1"
    TRX_MIN_TRANSFER_THRESHOLD: "1.01"
    BALANCES_RESCAN_PERIOD: "3600"
```

Operational notes:

- Callback URLs are not Helm env vars. They are provided per payment request in
  `callback_url`.
- Webhook endpoints must return a 2xx response. `webhook.site` test URLs can
  return `429` or reset connections when rate-limited.
- `USDT_MIN_TRANSFER_THRESHOLD` is a strict TRC20 sweep threshold:
  `balance > threshold`. A balance exactly equal to the threshold is left on the
  one-time address.
- Lowering a sweep threshold does not immediately sweep old addresses unless
  `scan_accounts` runs. The default rescan period is `3600` seconds.
- Keep the TRON fee-deposit account funded with a production reserve. Manual
  energy rental can reduce or avoid TRX burn, but the code still signs payouts
  with `fee_limit=50 TRX`.
- If private GHCR packages are used, `dev.imagePullSecrets` must include the
  `ghcr-nilof470` pull secret. The chart key is named `dev` even in production.

TRON parameter reference:

| Env var | Default | Meaning |
| --- | --- | --- |
| `TRON_NETWORK` | `main` | Network selector. Code supports `main` and `nile`. |
| `DEBUG` | `false` | App debug flag. |
| `DATABASE` | `data/database.db` | SQLite key/settings database path used by legacy DB helpers. |
| `DB_URI` | `sqlite:///data/tron.db` | SQLModel database URI. |
| `BALANCES_DATABASE` | `data/trc20balances.db` | Legacy balances database path. |
| `REDIS_HOST` | `localhost` | Redis host for Celery broker/backend. |
| `CONCURRENT_MAX_WORKERS` | `1` | Thread pool size for payout execution. |
| `CONCURRENT_MAX_RETRIES` | `10` | Retry loop bound used while reading balances. |
| `BALANCES_RESCAN_PERIOD` | `3600` | Default sweep scanner interval when `EXTERNAL_DRAIN_CONFIG` is not set. |
| `SAVE_BALANCES_TO_DB` | `true` | Stores scanned account balances in the sidecar DB. |
| `FULLNODE_URL` | `http://fullnode.tron.shkeeper.io` | Single TRON fullnode URL. Ignored when `MULTISERVER_CONFIG_JSON` is set. |
| `MULTISERVER_CONFIG_JSON` | empty | Optional JSON list of fullnodes, each with `name` and `url`. |
| `MULTISERVER_REFRESH_BEST_SERVER_PERIOD` | `20` | Interval for refreshing the best fullnode when multiserver mode is used. |
| `TRON_NODE_USERNAME` | `shkeeper` | Basic Auth user for the TRON node proxy if required. |
| `TRON_NODE_PASSWORD` | `tron` | Basic Auth password for the TRON node proxy if required. |
| `TRON_CLIENT_TIMEOUT` | `10` | TRON client HTTP timeout. |
| `BTC_USERNAME` | `shkeeper` | Sidecar API Basic Auth user. The setting name is `API_USERNAME`, but the env alias is `BTC_USERNAME`. |
| `BTC_PASSWORD` | `shkeeper` | Sidecar API Basic Auth password. The setting name is `API_PASSWORD`, but the env alias is `BTC_PASSWORD`. |
| `SHKEEPER_BACKEND_KEY` | `shkeeper` | Key used when notifying the main SHKeeper backend. |
| `SHKEEPER_HOST` | `localhost:5000` | Main SHKeeper backend host for payout notifications. |
| `FORCE_WALLET_ENCRYPTION` | `false` | Forces wallet encryption flow even when an admin password already exists. |
| `INTERNAL_TX_FEE` | `40` | TRX amount sent to an onetime account in burn-fee mode for TRC20 sweeps. |
| `TX_FEE` | `40` | Public payout fee estimate returned by `/calc-tx-fee` and used for payout dry-run balance checks. |
| `TX_FEE_LIMIT` | `50` | TRC20 transfer `fee_limit` in TRX. This is a maximum burn cap, not a guaranteed spend. |
| `BANDWIDTH_PER_TRX_TRANSFER` | `270` | Estimated bandwidth for a TRX transfer. |
| `BANDWIDTH_PER_DELEGE_CALL` | `278` | Estimated bandwidth for an energy delegation call. |
| `BANDWIDTH_PER_UNDELEGATE_CALL` | `280` | Estimated bandwidth for an undelegation call. |
| `BANDWIDTH_PER_TRC20_TRANSFER_CALL` | `346` | Estimated bandwidth for a TRC20 transfer call. |
| `TRX_PER_BANDWIDTH_UNIT` | `0.001` | TRX cost estimate per bandwidth unit. |
| `TRX_MIN_TRANSFER_THRESHOLD` | `0.5` | Native TRX sweep threshold. TRX sweeps when balance is `>=` this value. |
| `USDT_MIN_TRANSFER_THRESHOLD` | token default | Optional USDT sweep threshold override. Mainnet token default is `5`; sweep requires balance `>` threshold. |
| `USDC_MIN_TRANSFER_THRESHOLD` | token default | Optional USDC sweep threshold override. Mainnet token default is `5`; sweep requires balance `>` threshold. |
| `BLOCK_SCANNER_STATS_LOG_PERIOD` | `300` | How often scanner progress is logged. |
| `BLOCK_SCANNER_MAX_BLOCK_CHUNK_SIZE` | `1` | Maximum block chunk size per scanner iteration. |
| `BLOCK_SCANNER_INTERVAL_TIME` | `3` | Delay between scanner iterations. |
| `BLOCK_SCANNER_LAST_BLOCK_NUM_HINT` | empty | Optional starting block hint for scanner initialization. |
| `DEVMODE_ENCRYPTION_PW` | empty | Development-only wallet encryption password. Do not use in production. |
| `DEVMODE_SKIP_NOTIFICATIONS` | `false` | Development-only flag that skips backend notifications. |
| `DEVMODE_CELERY_NODELAY` | `false` | Development-only flag that runs selected Celery flows inline. |
| `EXTERNAL_DRAIN_CONFIG` | empty | Enables the legacy custom external drain/AML split workflow when set to JSON. |
| `DELAY_AFTER_FEE_TRANSFER` | `60` | Legacy custom AML flow delay after sending fee TRX. Currently commented out in the code path. |
| `AML_RESULT_UPDATE_PERIOD` | `120` | Legacy custom AML flow interval for rechecking pending AML results. |
| `AML_SWEEP_ACCOUNTS_PERIOD` | `3600` | Legacy custom AML flow interval for sweeping configured accounts. |
| `AML_WAIT_BEFORE_API_CALL` | `320` | Delay before legacy AMLBot check/payout after a deposit is seen. |
| `ENERGY_SOURCE` | `staking` | Energy source: `staking` or `refee`. |
| `ENERGY_DELEGATION_MODE` | `false` | Enables staking-based energy delegation when `ENERGY_SOURCE=staking`. |
| `ENERGY_DELEGATION_MODE_ALLOW_BURN_TRX_FOR_BANDWITH` | `false` | Allows burning TRX for bandwidth when free bandwidth is insufficient. Variable name is misspelled in code as `BANDWITH`. |
| `ENERGY_DELEGATION_MODE_ALLOW_BURN_TRX_ON_PAYOUT` | `false` | Allows fallback to TRX burn for TRC20 payout if energy provisioning fails. Keep `false` to cap losses. |
| `ENERGY_DELEGATION_MODE_ALLOW_ADDITIONAL_ENERGY_DELEGATION` | `false` | Allows extra delegation when existing delegated energy is below the estimated requirement. |
| `ENERGY_DELEGATION_MODE_ENERGY_DELEGATION_FACTOR` | `1.0` | Multiplier for staking-based energy delegation sizing. |
| `ENERGY_DELEGATION_MODE_SEPARATE_BALANCE_AND_ENERGY_ACCOUNTS` | `false` | Uses a separate energy account instead of fee-deposit for energy operations. |
| `ENERGY_DELEGATION_MODE_ENERGY_ACCOUNT_PUB_KEY` | empty | Public key of the separate energy account when enabled. |
| `REFEE` | empty | re:Fee JSON config. Required when `ENERGY_SOURCE=refee`. |
| `REFEE_FIXED_ENERGY_ORDER_AMOUNT` | `65000` | Fixed re:Fee energy order size. Set `0` to use estimate-based sizing. |
| `SR_VOTING` | `false` | Enables automatic Super Representative voting. |
| `SR_VOTES` | empty | JSON list of SR votes, each with `vote_address` and `vote_count`. |
| `SR_VOTING_ALLOW_BURN_TRX` | `false` | Allows burning TRX for voting bandwidth. |

`REFEE` JSON fields:

| Field | Default | Meaning |
| --- | --- | --- |
| `api_base_url` | `https://api.refee.bot/v2` | re:Fee API base URL. Must be HTTPS. |
| `api_key` | required | re:Fee API key. |
| `rent_duration_label` | `1h` | Energy rental duration: `1h`, `1d`, `3d`, `7d`, or `14d`. |
| `energy_overprovision_factor` | `1.05` | Buffer used when `REFEE_FIXED_ENERGY_ORDER_AMOUNT=0`. |
| `min_energy_order_amount` | `30000` | Minimum energy order amount accepted by re:Fee. |
| `poll_interval_sec` | `2.0` | Poll interval while waiting for an order. |
| `timeout_sec` | `60` | Timeout while waiting for an order. |

Legacy `EXTERNAL_DRAIN_CONFIG` JSON shape:

```json
{
  "aml_check": {
    "state": "enabled",
    "access_id": "REPLACE_WITH_AMLBOT_ACCESS_ID",
    "access_key": "REPLACE_WITH_AMLBOT_ACCESS_KEY",
    "access_point": "https://extrnlapiendpoint.silencatech.com",
    "flow": "fast",
    "cryptos": {
      "USDT": {
        "min_check_amount": "1",
        "risk_scores": {
          "low": {
            "min_value": "0",
            "max_value": "0.10",
            "addresses": {
              "T_REPLACE_LOW_RISK_DESTINATION": "1"
            }
          },
          "review": {
            "min_value": "0.11",
            "max_value": "1",
            "addresses": {
              "T_REPLACE_REVIEW_DESTINATION": "1"
            }
          }
        }
      }
    }
  },
  "regular_split": {
    "state": "disabled",
    "cryptos": {
      "USDT": {
        "addresses": {
          "T_REPLACE_DESTINATION": "1"
        }
      }
    }
  }
}
```

Use the main `shkeeper` + `aml-shkeeper` gate for new deployments. The legacy
`EXTERNAL_DRAIN_CONFIG` path is separate from `aml-shkeeper`, calls AMLBot
directly from `tron-shkeeper`, and its current code has a weak edge case for
amounts at or below `min_check_amount`.

Legacy `EXTERNAL_DRAIN_CONFIG` validation notes:

- The current code requires a symbol to be present in both `aml_check.cryptos`
  and `regular_split.cryptos`, even when one workflow is disabled.
- `aml_check.cryptos.<symbol>.min_check_amount` is strict: the legacy AML check
  starts only when `amount > min_check_amount`.
- `risk_scores` must cover the full `[0, 1]` score interval; each score bound
  must be between `0` and `1`.
- Every `addresses` split must sum to exactly `1`.

TON / USDT-TON settings:

```yaml
ton_fullnode:
  enabled: false
  mainnet: true

ton_shkeeper:
  image: ghcr.io/nilof470/ton-shkeeper:REPLACE_WITH_TON_TAG
  extraEnv:
    LAST_BLOCK_LOCKED: "FALSE"
    TONCENTER_API_URL: https://toncenter.com
    TONCENTER_INDEXER_URL: https://toncenter.com
    TONCENTER_API_KEY: "REPLACE_WITH_TONCENTER_API_KEY"
    TONCENTER_INDEXER_KEY: "REPLACE_WITH_TONCENTER_INDEXER_KEY"
    CURRENT_TON_NETWORK: "main"
    SCAN_NATIVE_TON_EVENTS: "false"
    EVENTS_MAX_THREADS_NUMBER: "4"
    GET_JETTON_TXS_LIMIT: "1000"
    TON_TRANSACTION_FEE: "0.001"
    JETTON_TRANSACTION_FEE: "0.008"
    JETTON_TRANSACTION_NEED_BALANCE: "0.010"
    MIN_TRANSFER_THRESHOLD: "0.003"
    MIN_TOKEN_TRANSFER_THRESHOLD: "5"

ton:
  enabled: true

ton_usdt:
  enabled: true
```

TON operational notes:

- As of 2026-05-08, the official chart pins `ton_shkeeper.image` to
  `vsyshost/ton-shkeeper:0.0.2`. Docker Hub had no newer stable tag; only
  `dev-*` tags were available. Use the forked
  `ghcr.io/nilof470/ton-shkeeper:<tag>` image for the scanner resilience fix.
- The current TON integration uses external Toncenter API/indexer endpoints, not
  a local TON full node. A paid Toncenter plan is recommended for production.
- `SCAN_NATIVE_TON_EVENTS=false` is recommended for current TON-USDT-only
  production. It prevents native TON `/api/v3/transactionsByMasterchainBlock`
  indexer gaps from blocking TON-USDT Jetton checkpoint progress. If native TON
  deposits are enabled in the future, set it back to `true` so native TON scan
  failures block checkpoint advancement safely.
- Keep both `ton.enabled=true` and `ton_usdt.enabled=true` for TON-USDT
  deposits. `SCAN_NATIVE_TON_EVENTS=false` only disables native TON transaction
  scanning; it does not replace the base `TON` crypto registration. The
  `ton-shkeeper` sidecar still calls `/api/v1/TON/decrypt` to get the wallet
  encryption key before it can create or use TON-USDT deposit wallets.
- Do not judge scanner health by `/TON/status` returning HTTP `200` alone. In
  the observed production incident, `/TON/status` stayed healthy while
  `last_block_timestamp` stopped moving.
- Large TON lag is acceptable while `last_block_timestamp` is increasing. The
  failure condition is no timestamp progress for several checks.
- `EVENTS_MAX_THREADS_NUMBER=4` is the recommended production starting point
  after initial sync when `TONCENTER_API_KEY` and `TONCENTER_INDEXER_KEY` each
  have a separate `25 rps` Toncenter quota. Raise it only temporarily if TON lag
  grows; avoid long-running `8+` because `ton-shkeeper:0.0.2` Toncenter calls do
  not set HTTP timeouts.
- `GET_JETTON_TXS_LIMIT=1000` raises the page size used for
  `/api/v3/jetton/transfers` from the image default of `20` to the documented
  in-code maximum of `1000`. This reduces pagination calls for TON-USDT block
  scanning, lowers `TONCENTER_INDEXER_KEY` request pressure, and reduces the
  chance of Toncenter connection resets or rate limiting.
- The TON-USDT sweep gas settings above are production starting values based on
  live Toncenter traces from 2026-05-16. They are not merchant invoice fees:
  `TON_TRANSACTION_FEE=0.001` is the native TON transfer fee reserve,
  `JETTON_TRANSACTION_FEE=0.008` is the TON attached to a Jetton transfer,
  `JETTON_TRANSACTION_NEED_BALANCE=0.010` is the target native TON balance on a
  client address before Jetton sweep, and `MIN_TRANSFER_THRESHOLD=0.003` is the
  minimum native TON dust balance to sweep back to `fee_deposit`.
- `MIN_TOKEN_TRANSFER_THRESHOLD=5` is the minimum TON-USDT balance swept from a
  client address to `fee_deposit`. With the gas settings above and live
  2026-05-16 Toncenter traces, expected all-in TON-USDT sweep cost is usually
  about `0.0022-0.0030 TON`, with a conservative planning value around
  `0.0040 TON`. At about `$1.97/TON`, this is roughly `$0.004-$0.008`, so a
  `5 USDT` threshold keeps the cost near or below `0.25%` while still sweeping
  small deposits. Use `MIN_TOKEN_TRANSFER_THRESHOLD=10` if the priority is
  lower fee percentage over faster consolidation; it usually keeps sweep cost
  closer to `0.05-0.10%`.
- For TON-USDT sweep, `ton-shkeeper` tops up the client address from
  `fee_deposit` only when native TON is below `JETTON_TRANSACTION_NEED_BALANCE`,
  deploys the client TON wallet if it is still uninitialized, then sends the
  Jetton transfer with `JETTON_TRANSACTION_FEE` attached. The Jetton transfer
  sets the excess response address to `fee_deposit`; only network fees are
  burned, while remaining native TON on the client address is collected later
  when it reaches `MIN_TRANSFER_THRESHOLD`.
- `ton-shkeeper:0.0.2` uses `TONCENTER_API_KEY` and `TONCENTER_INDEXER_KEY` for
  different request classes. Keep them on separate Toncenter accounts when
  possible so each key has its own quota.
- Keep the TON scanner watchdog installed on production until `ton-shkeeper`
  includes an internal retry/watchdog fix and the forked image has been observed
  in production for at least one full business cycle.
- The detailed root-cause analysis and scanner fix design for transient
  Toncenter indexer `404` gaps is tracked in
  [`TON_SCANNER_RESILIENCE.md`](TON_SCANNER_RESILIENCE.md).

TON Toncenter API call map:

| Key | Endpoint | Operation |
| --- | --- | --- |
| `TONCENTER_API_KEY` | `GET /api/v2/getMasterchainInfo` | scanner head, confirmations, drain freshness checks |
| `TONCENTER_API_KEY` | `GET /api/v2/getBlockHeader` | `/TON/status`, block timestamp, Jetton LT range |
| `TONCENTER_API_KEY` | `GET /api/v2/getAddressInformation` | TON balance |
| `TONCENTER_API_KEY` | `GET /api/v2/getWalletInformation` | wallet state and seqno |
| `TONCENTER_API_KEY` | `POST /api/v2/sendBocReturnHash` | deploy wallet, TON payout/drain, Jetton payout/drain |
| `TONCENTER_API_KEY` | `POST /api/v2/sendBoc` | send BoC helper; less common path |
| `TONCENTER_API_KEY` | `GET /api/v3/jetton/wallets` | Jetton balance and Jetton wallet address |
| `TONCENTER_API_KEY` | `GET /api/v3/jetton/masters` | Jetton decimals |
| `TONCENTER_INDEXER_KEY` | `GET /api/v3/transactionsByMasterchainBlock` | native TON block scan |
| `TONCENTER_INDEXER_KEY` | `GET /api/v3/jetton/transfers` | TON-USDT block scan |
| `TONCENTER_INDEXER_KEY` | `GET /api/v3/transactions` | transaction lookup by hash |
| `TONCENTER_INDEXER_KEY` | `GET /api/v3/transactionsByMessage` | transaction lookup fallback by message hash |
| `TONCENTER_INDEXER_KEY` | `GET /api/v3/blocks` | shard/master block lookup helper |
| `TONCENTER_INDEXER_KEY` | `GET /v1/getTransactionsByAddress` | address transaction lookup helper |

Steady-state scan request estimate:

| Setting | `TONCENTER_API_KEY` burst | `TONCENTER_INDEXER_KEY` burst | Notes |
| --- | --- | --- | --- |
| `EVENTS_MAX_THREADS_NUMBER=3` | about `6-9` requests/chunk | about `6-9` requests/chunk | conservative |
| `EVENTS_MAX_THREADS_NUMBER=4` | about `8-12` requests/chunk | about `8-12` requests/chunk | recommended for separate `25 rps` keys |
| `EVENTS_MAX_THREADS_NUMBER=6` | about `12-18` requests/chunk | about `12-18` requests/chunk | temporary if lag grows |

The rough minimum per scanned block is `2` API-key calls and `2` indexer-key
calls:

- `2x GET /api/v2/getBlockHeader` for Jetton LT range.
- `1x GET /api/v3/transactionsByMasterchainBlock` for native TON events.
- `1x GET /api/v3/jetton/transfers` for TON-USDT events when
  `GET_JETTON_TXS_LIMIT=1000` avoids pagination.

Real deposits add more calls:

- TON deposit notification lookup usually adds
  `GET /api/v3/transactions` plus `GET /api/v2/getMasterchainInfo`.
- TON-USDT deposit lookup usually adds `GET /api/v3/transactions`,
  `GET /api/v3/jetton/transfers`, another raw transaction lookup,
  `GET /api/v2/getMasterchainInfo`, and `GET /api/v3/jetton/masters`.
- Drain/payout adds wallet state, seqno, balance, Jetton wallet, decimals, and
  `POST /api/v2/sendBocReturnHash` calls.

## Install SHKeeper

```bash
helm upgrade --install -n default -f /root/shkeeper-values.yaml \
  shkeeper oci://ghcr.io/nilof470/helm-charts/shkeeper \
  --version 1.7.28-nilof470.13 --timeout 300s
```

Watch startup:

```bash
kubectl get pods -n shkeeper
kubectl get pvc -n shkeeper
kubectl get svc -n shkeeper
kubectl get pods -n shkeeper -w
```

Expected core pods:

```text
mariadb                 1/1 Running
shkeeper-deployment     1/1 Running
tron-shkeeper           3/3 Running
```

The official chart can leave old failed `create-db-bitcoin-shkeeper` retry pods
even with BTC disabled. If the job has one `Completed` pod and the core pods are
running, those old failed pods are not a blocker.

Check local access from the VPS:

```bash
curl -I http://127.0.0.1:5000/
```

For dev direct access, open inbound TCP `5000` in the cloud firewall/security
group and browse:

```text
http://PUBLIC_VPS_IP:5000/wallets
```

For production, prefer DNS + HTTPS through the chart's Traefik ingress. Set
`domain` in `shkeeper-values.yaml`, open `80` and `443`, and avoid exposing
`5000` publicly.

## First-Time Admin Setup

Open the SHKeeper UI and set:

1. admin password
2. wallet encryption password

The wallet encryption password is stored only in RAM by SHKeeper. Save it in a
password manager. After SHKeeper restarts, the UI may ask for it again before
sidecars can decrypt wallet keys.

Verify the TRON sidecar received the key:

```bash
kubectl logs -n shkeeper deployment/tron-shkeeper -c app --tail=80
kubectl logs -n shkeeper deployment/tron-shkeeper -c tasks --tail=80
```

Expected lines:

```text
Wallet encryption is enabled, encryption key is set!
Encryption settings are valid.
celery@... ready.
```

## Fee Deposit Wallet

Get the TRON `fee_deposit` address:

```bash
kubectl exec -n shkeeper deployment/tron-shkeeper -c app -- python -c 'import os, requests; r=requests.post("http://127.0.0.1:6000/TRX/fee-deposit-account", auth=(os.environ["BTC_USERNAME"], os.environ["BTC_PASSWORD"]), timeout=20); print(r.status_code, r.text)'
```

Fund this address with TRX before testing or going live. It is used for
activation transfers and TRX payouts. In dev we used about `30 TRX`; production
should use an operator-defined reserve and monitoring.

## re:Fee Requirements

The re:Fee API key must allow requests from the VPS public IP. Get the IP:

```bash
curl -4 ifconfig.me
```

Add that IP to the re:Fee whitelist. Without this, energy rental fails with:

```text
403 {"detail":"Your IP is not on the user's whitelist"}
```

## Create a Test USDT Deposit

In the SHKeeper UI, get the API key from the wallet management screen. Then
create a payment request from the VPS:

```bash
read -s SHKEEPER_API_KEY

curl -sS -X POST 'http://127.0.0.1:5000/api/v1/USDT/payment_request' \
  -H "X-Shkeeper-Api-Key: ${SHKEEPER_API_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{
    "external_id": "dev-usdt-001",
    "fiat": "USD",
    "amount": "1",
    "callback_url": "https://example.com/shkeeper-callback"
  }'
```

The response contains a `wallet` field. Send the exact returned `amount` to that
address. SHKeeper may return a value such as `1.02` even when the requested fiat
amount is `1`.

Watch worker logs:

```bash
kubectl logs -n shkeeper deployment/tron-shkeeper -c tasks -f
```

Expected successful flow:

```text
Balance OK
Activating ... by sending 0.1 TRX
0.1 TRX sent
Requesting re:Fee energy rental
re:Fee energy successfully delegated
... USDT sent to fee_deposit
```

If a retry is needed without waiting for the periodic scanner:

```bash
kubectl exec -n shkeeper deployment/tron-shkeeper -c tasks -- python -c 'from app.tasks import transfer_trc20_from; transfer_trc20_from.delay("ONETIME_ADDRESS", "USDT"); print("queued")'
```

The periodic balance scanner also retries stuck balances. Default interval:

```text
BALANCES_RESCAN_PERIOD=3600
```

## Payout Execution Production Overlay

The payout execution chart API is generic. SHKeeper accepts only execution
contract and routing configuration. Customer withdrawal policy belongs to the
upstream product ledger. In this chart, `payouts.shkeeperWorkers.batchSize`
controls only worker batch processing, and optional wallet balance values are
alert thresholds.

Create `/root/shkeeper-payout-values.yaml`. This stages all three USDT rails
and their workers, but keeps payout execution disabled in the SHKeeper rail
catalog by leaving `paused: true` and `killSwitch: true`. Flip one rail at a
time only after restore, smoke payout, callback, and upstream ledger gates pass.
The sidecar `*.usdtPayoutWorker.enabled` flags below intentionally stay
`false`: the chart derives the dedicated payout worker from
`payouts.rails.*.enabled`, while the sidecar-local flag remains only a guarded
manual override.

This root-only file also enables Helm-managed payout auth Secrets. Do not commit
it. Replace the `PASTE_ON_SERVER_ONLY` markers on the production server before
rendering or deploying. In managed mode, secret material is present in
`/root/shkeeper-payout-values.yaml`, rendered Kubernetes Secrets, and Helm
release metadata. Restrict root/admin access to the server and cluster, and do
not print, log, paste, or render this file with `helm --debug`.

The chart renders one shared sidecar auth key, `shkeeper-to-sidecars-v1`, into
both SHKeeper's sidecar signer payload and every enabled sidecar verifier
payload. TRON, TON, and ETH sidecars all validate the same key; do not create
rail-local sidecar consumer keys.

```yaml
dev:
  imagePullSecrets:
    - name: ghcr-nilof470

shkeeper:
  image: ghcr.io/nilof470/shkeeper.io:REPLACE_WITH_SHKEEPER_TAG

payouts:
  enabled: true
  consumer: grither-pay
  sidecarRequestTimeoutSeconds: 10
  authMaxAgeSeconds: 300
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
  networkPolicies:
    enabled: true
  storage:
    mode: singleNodeSqlitePvc
    claimName: shkeeper-db-claim
    allowSeparateWorkerDeployments: true
    backupRestoreEvidence: grither-prod-shkeeper-restore-drill-REPLACE-ME
  migrations:
    enabled: true
  shkeeperWorkers:
    enabled: true
    intervalSeconds: 5
    batchSize: 50
  rails:
    tronUsdt:
      enabled: true
      paused: true
      killSwitch: true
      queue: tron_usdt_fee_payouts
      sidecarService: tron-shkeeper
      sidecarSymbol: USDT
      sourceWalletRef: fee_deposit
      ownedImageRepository: ghcr.io/nilof470/tron-shkeeper
      executionStateStorage: sidecar-db
      callbackEndpointId: grither-pay-main
      hotWalletMinimumBalance: ""
      feeWalletMinimumBalance: ""
      backupRestoreEvidence: grither-prod-tron-sidecar-restore-drill-REPLACE-ME
    tonUsdt:
      enabled: true
      paused: true
      killSwitch: true
      queue: ton_usdt_payouts
      sidecarService: ton-shkeeper
      sidecarSymbol: TON-USDT
      sourceWalletRef: fee_deposit
      ownedImageRepository: ghcr.io/nilof470/ton-shkeeper
      executionStateStorage: sidecar-db
      callbackEndpointId: grither-pay-main
      hotWalletMinimumBalance: ""
      feeWalletMinimumBalance: ""
      backupRestoreEvidence: grither-prod-ton-sidecar-restore-drill-REPLACE-ME
    ethUsdt:
      enabled: true
      paused: true
      killSwitch: true
      queue: eth_usdt_payouts
      sidecarService: ethereum-shkeeper
      sidecarSymbol: ETH-USDT
      sourceWalletRef: fee_deposit
      ownedImageRepository: ghcr.io/nilof470/ethereum-shkeeper
      executionStateStorage: sidecar-db
      callbackEndpointId: grither-pay-main
      hotWalletMinimumBalance: ""
      feeWalletMinimumBalance: ""
      backupRestoreEvidence: grither-prod-eth-sidecar-restore-drill-REPLACE-ME

tron_shkeeper:
  image: ghcr.io/nilof470/tron-shkeeper:REPLACE_WITH_TRON_TAG
  usdtPayoutWorker:
    enabled: false
    queue: tron_usdt_fee_payouts
    concurrency: 1
    prefetchMultiplier: 1

ton_shkeeper:
  image: ghcr.io/nilof470/ton-shkeeper:REPLACE_WITH_TON_TAG
  usdtPayoutWorker:
    enabled: false
    queue: ton_usdt_payouts
    concurrency: 1
    prefetchMultiplier: 1

ethereum_shkeeper:
  image: ghcr.io/nilof470/ethereum-shkeeper:REPLACE_WITH_ETH_TAG
  usdtPayoutWorker:
    enabled: false
    queue: eth_usdt_payouts
    concurrency: 1
    prefetchMultiplier: 1
```

This storage mode is intentional for the current Grither Pay gateway scope: one
node, controlled throughput, and no horizontal SHKeeper writer scaling. Keep
backup/restore evidence current and do not scale the SQLite-backed SHKeeper
writers horizontally without moving the storage mode to a server database.

Apply the staged payout release with the published payout chart:

```bash
export HELM_NS=default
export APP_NS=shkeeper
export CHART_REF=oci://ghcr.io/nilof470/helm-charts/shkeeper
export CHART_VERSION=1.7.28-nilof470.13

helm -n "$HELM_NS" get values shkeeper -o yaml > /root/shkeeper-current-values.yaml

helm upgrade shkeeper "$CHART_REF" \
  --version "$CHART_VERSION" \
  -n "$HELM_NS" \
  -f /root/shkeeper-current-values.yaml \
  -f /root/shkeeper-payout-values.yaml \
  --timeout 15m \
  --history-max 2
```

Verify that SHKeeper workers, sidecar API deployments, and rail sync are
rendered before enabling client traffic. Dedicated payout workers render only
after the selected rail has both `paused=false` and `killSwitch=false`.

```bash
kubectl rollout status deployment/shkeeper-deployment -n "$APP_NS" --timeout=180s
kubectl rollout status deployment/shkeeper-payout-execution-reconciler -n "$APP_NS" --timeout=180s
kubectl rollout status deployment/shkeeper-payout-callback-dispatcher -n "$APP_NS" --timeout=180s
kubectl rollout status deployment/tron-shkeeper -n "$APP_NS" --timeout=180s
kubectl rollout status deployment/ton-shkeeper -n "$APP_NS" --timeout=180s
kubectl rollout status deployment/ethereum-shkeeper -n "$APP_NS" --timeout=180s

kubectl get job -n "$APP_NS" shkeeper-payout-rail-sync
kubectl get pods -n "$APP_NS" | grep -E 'shkeeper-payout|tron-shkeeper|ton-shkeeper|ethereum-shkeeper'

kubectl get deployment tron-shkeeper -n "$APP_NS" \
  -o jsonpath='{.spec.template.spec.containers[*].name}{"\n"}'
kubectl get deployment ton-shkeeper -n "$APP_NS" \
  -o jsonpath='{.spec.template.spec.containers[*].name}{"\n"}'
kubectl get deployment ethereum-shkeeper -n "$APP_NS" \
  -o jsonpath='{.spec.template.spec.containers[*].name}{"\n"}'
```

Expected container names include:

```text
app tasks redis
app tasks redis
app tasks redis
```

After a rail passes production gates, enable only that rail by changing both
`paused` and `killSwitch` to `false` for the selected rail, then run the same
Helm upgrade command. Rails left paused or kill-switched remain present in
Kubernetes but sync to `execution_enabled=false` in SHKeeper.

After enabling TRON payout execution, verify the worker Deployment:

```bash
kubectl rollout status deployment/tron-usdt-payouts -n "$APP_NS" --timeout=180s
kubectl get deployment tron-usdt-payouts -n "$APP_NS" \
  -o jsonpath='{.spec.template.spec.containers[*].name}{"\n"}'
```

Expected output:

```text
tron-usdt-payouts
```

## Updating an Existing VPS

After building and pushing a new image tag locally, update the VPS values file.
The Helm release can be in `default` while workloads run in the `shkeeper`
namespace, so confirm the release namespace first:

```bash
helm list -A | grep shkeeper
```

For the payout release, persist the published chart version and image tags in
`/root/shkeeper-payout-values.yaml`, then rerun the file-only upgrade. This
keeps the target VPS deploy step independent from a repository checkout and
avoids one-off CLI overrides that drift from the server values.

```bash
export HELM_NS=default
export APP_NS=shkeeper
export CHART_REF=oci://ghcr.io/nilof470/helm-charts/shkeeper
export CHART_VERSION=1.7.28-nilof470.13

helm -n "$HELM_NS" get values shkeeper -o yaml > /root/shkeeper-current-values.yaml

helm upgrade shkeeper "$CHART_REF" \
  --version "$CHART_VERSION" \
  -n "$HELM_NS" \
  -f /root/shkeeper-current-values.yaml \
  -f /root/shkeeper-payout-values.yaml \
  --timeout 15m \
  --history-max 2
```

Verify the deployed images and rollout:

```bash
kubectl rollout status deployment/shkeeper-deployment -n "$APP_NS" --timeout=180s
kubectl rollout status deployment/tron-shkeeper -n "$APP_NS" --timeout=180s
kubectl rollout status deployment/ton-shkeeper -n "$APP_NS" --timeout=180s
kubectl rollout status deployment/ethereum-shkeeper -n "$APP_NS" --timeout=180s
kubectl rollout status deployment/shkeeper-payout-execution-reconciler -n "$APP_NS" --timeout=180s
kubectl rollout status deployment/shkeeper-payout-callback-dispatcher -n "$APP_NS" --timeout=180s
kubectl get deployment shkeeper-deployment -n "$APP_NS" \
  -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}'
kubectl get deployment tron-shkeeper -n "$APP_NS" -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}'
kubectl get deployment ton-shkeeper -n "$APP_NS" -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}'
kubectl get deployment ethereum-shkeeper -n "$APP_NS" -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}'
kubectl get pods -n "$APP_NS" | grep -E 'shkeeper-payout|tron-shkeeper|ton-shkeeper|ethereum-shkeeper'
```

Expected image output shape:

```text
ghcr.io/nilof470/shkeeper.io:TAG
ghcr.io/nilof470/tron-shkeeper:TAG ghcr.io/nilof470/tron-shkeeper:TAG redis:7
ghcr.io/nilof470/ton-shkeeper:TAG ghcr.io/nilof470/ton-shkeeper:TAG ghcr.io/nilof470/ton-shkeeper:TAG redis:7
ghcr.io/nilof470/ethereum-shkeeper:TAG ghcr.io/nilof470/ethereum-shkeeper:TAG ghcr.io/nilof470/ethereum-shkeeper:TAG redis:7
tron-shkeeper ... 3/3 Running
ton-shkeeper ... 3/3 Running
ethereum-shkeeper ... 3/3 Running
```

If TRON payout execution is active, also verify:

```bash
kubectl rollout status deployment/tron-usdt-payouts -n "$APP_NS" --timeout=180s
kubectl get deployment tron-usdt-payouts -n "$APP_NS" -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}'
```

If TON or ETH payout execution is active, their sidecar pods include
`ton-usdt-payouts` or `eth-usdt-payouts` and report `4/4 Running`.

If `grep` does not print the expected image lines before the Helm upgrade, the
values file does not contain those image overrides. Export the active release
values and re-apply the image tags before upgrading:

```bash
helm get values shkeeper -n default -o yaml > /root/shkeeper-values.yaml
nano /root/shkeeper-payout-values.yaml
```

Use the namespace shown by `helm list -A` if it is not `default`.

For a legacy TON scanner-only release without payout execution enabled, update
the TON sidecar image in the base values file:

```bash
NEW_TON_TAG=REPLACE_WITH_TON_TAG

sed -i "s|image: .*ton-shkeeper:.*|image: ghcr.io/nilof470/ton-shkeeper:${NEW_TON_TAG}|" /root/shkeeper-values.yaml

grep -n "ghcr.io/nilof470/ton-shkeeper" /root/shkeeper-values.yaml
```

Ensure the TON extra environment contains the production scanner settings:

```yaml
ton_shkeeper:
  extraEnv:
    LAST_BLOCK_LOCKED: "FALSE"
    TONCENTER_API_URL: https://toncenter.com
    TONCENTER_INDEXER_URL: https://toncenter.com
    TONCENTER_API_KEY: "REPLACE_WITH_TONCENTER_API_KEY"
    TONCENTER_INDEXER_KEY: "REPLACE_WITH_TONCENTER_INDEXER_KEY"
    CURRENT_TON_NETWORK: "main"
    SCAN_NATIVE_TON_EVENTS: "false"
    EVENTS_MAX_THREADS_NUMBER: "4"
    GET_JETTON_TXS_LIMIT: "1000"
    TON_TRANSACTION_FEE: "0.001"
    JETTON_TRANSACTION_FEE: "0.008"
    JETTON_TRANSACTION_NEED_BALANCE: "0.010"
    MIN_TRANSFER_THRESHOLD: "0.003"
    MIN_TOKEN_TRANSFER_THRESHOLD: "5"
```

Apply the upgrade through the published chart fork. This matters even when only
the TON image changed, because Helm renders the whole release and must use the
chart fork that includes the TRON USDT payout worker:

```bash
helm list -A | grep shkeeper

helm upgrade --install -n default -f /root/shkeeper-values.yaml \
  shkeeper oci://ghcr.io/nilof470/helm-charts/shkeeper \
  --version 1.7.28-nilof470.13 --timeout 300s

kubectl rollout status deployment/ton-shkeeper -n shkeeper --timeout=180s
```

Verify the deployed TON image and env without printing secrets:

```bash
kubectl get deployment ton-shkeeper -n shkeeper \
  -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}'

kubectl exec -n shkeeper deployment/ton-shkeeper -c app -- sh -c \
  'env | egrep "^(CURRENT_TON_NETWORK|SCAN_NATIVE_TON_EVENTS|EVENTS_MAX_THREADS_NUMBER|GET_JETTON_TXS_LIMIT|TON_TRANSACTION_FEE|JETTON_TRANSACTION_FEE|JETTON_TRANSACTION_NEED_BALANCE|MIN_TRANSFER_THRESHOLD|MIN_TOKEN_TRANSFER_THRESHOLD|TONCENTER_API_URL|TONCENTER_INDEXER_URL)="'

kubectl exec -n shkeeper deployment/ton-shkeeper -c app -- sh -c \
  'env | egrep "^(TONCENTER_API_KEY|TONCENTER_INDEXER_KEY)=" | sed -E "s/(KEY)=.*/\1=***MASKED***/"'
```

Expected image output shape:

```text
ghcr.io/nilof470/ton-shkeeper:TAG ghcr.io/nilof470/ton-shkeeper:TAG redis
```

## Useful Diagnostics

General state:

```bash
kubectl get pods -n shkeeper
kubectl get svc -n shkeeper
kubectl get pvc -n shkeeper
kubectl get events -n shkeeper --sort-by=.lastTimestamp | tail -80
```

Logs:

```bash
kubectl logs -n shkeeper deployment/shkeeper-deployment --tail=100
kubectl logs -n shkeeper deployment/shkeeper-payout-execution-reconciler --tail=120
kubectl logs -n shkeeper deployment/shkeeper-payout-callback-dispatcher --tail=120
kubectl logs -n shkeeper deployment/tron-shkeeper -c app --tail=120
kubectl logs -n shkeeper deployment/tron-shkeeper -c tasks --tail=120
kubectl logs -n shkeeper deployment/tron-usdt-payouts -c tron-usdt-payouts --tail=120
kubectl logs -n shkeeper deployment/ton-shkeeper -c ton-usdt-payouts --tail=120
kubectl logs -n shkeeper deployment/ethereum-shkeeper -c eth-usdt-payouts --tail=120
```

Deployed image versions:

```bash
kubectl get deployment shkeeper-deployment -n shkeeper \
  -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}'

kubectl get deployment aml-shkeeper -n shkeeper \
  -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}'

kubectl get deployment tron-shkeeper -n shkeeper \
  -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}'

kubectl get deployment ton-shkeeper -n shkeeper \
  -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}'

kubectl get deployment ethereum-shkeeper -n shkeeper \
  -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}'

kubectl get deployment shkeeper-payout-execution-reconciler -n shkeeper \
  -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}'

kubectl get deployment shkeeper-payout-callback-dispatcher -n shkeeper \
  -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}'
```

Production env verification:

```bash
kubectl exec -n shkeeper deployment/shkeeper-deployment -- \
  env | grep -E '^(AML_ENABLED|AML_PROVIDER|AML_SHKEEPER_HOST|AML_SHKEEPER_USERNAME|AML_SHKEEPER_PASSWORD|AML_MAX_ACCEPT_SCORE|AML_MIN_CHECK_AMOUNT_FIAT|AML_SKIP_CUMULATIVE_LIMIT_FIAT|AML_SKIP_CUMULATIVE_WINDOW_HOURS|AML_PENDING_TIMEOUT_SECONDS|AML_RETRY_DELAY_SECONDS|REQUESTS_TIMEOUT)='

kubectl exec -n shkeeper deployment/shkeeper-deployment -- sh -c \
  'env | egrep "^(PAYOUT_SIDECAR_REQUEST_TIMEOUT|PAYOUT_CONSUMER_KEYS_JSON|PAYOUT_SIDECAR_KEYS_JSON|PAYOUT_CALLBACK_KEYS_JSON|PAYOUT_CALLBACK_ENDPOINTS_JSON)=" | sed -E "s/(JSON)=.*/\1=***MASKED***/"'

kubectl exec -n shkeeper deployment/tron-shkeeper -c app -- sh -c \
  'env | egrep "^(TRON_USDT_PAYOUT_QUEUE|TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED|PAYOUT_CONSUMER_KEYS_JSON|PAYOUT_AUTH_MAX_AGE_SECONDS|PAYOUT_EXECUTION_AUTO_ENQUEUE_ENABLED|PAYOUT_EXECUTION_PREFLIGHT_CHECKS_ENABLED)=" | sed -E "s/(JSON)=.*/\1=***MASKED***/"'

kubectl exec -n shkeeper deployment/ton-shkeeper -c app -- sh -c \
  'env | egrep "^(TON_USDT_PAYOUT_QUEUE|PAYOUT_CONSUMER_KEYS_JSON|PAYOUT_AUTH_MAX_AGE_SECONDS|PAYOUT_EXECUTION_AUTO_ENQUEUE_ENABLED|PAYOUT_EXECUTION_PREFLIGHT_CHECKS_ENABLED)=" | sed -E "s/(JSON)=.*/\1=***MASKED***/"'

kubectl exec -n shkeeper deployment/ethereum-shkeeper -c app -- sh -c \
  'env | egrep "^(ETH_USDT_PAYOUT_QUEUE|PAYOUT_CONSUMER_KEYS_JSON|PAYOUT_AUTH_MAX_AGE_SECONDS|PAYOUT_EXECUTION_AUTO_ENQUEUE_ENABLED|PAYOUT_EXECUTION_PREFLIGHT_CHECKS_ENABLED)=" | sed -E "s/(JSON)=.*/\1=***MASKED***/"'

kubectl exec -n shkeeper deployment/aml-shkeeper -c app -- \
  env | grep -E '^(AML_USERNAME|AML_PASSWORD|CURRENT_PROVIDER|KOINKYT_HOST|KOINKYT_API_KEY|KOINKYT_RISK_PROFILE_IDS|AMLBOT_ACCESS_ID|AMLBOT_ACCESS_KEY|AMLBOT_ACCESS_POINT|AMLBOT_FLOW|PROVIDERS|AML_DEFAULT_THRESHOLD|CHECK_TIMEOUT_SECONDS|CHECK_RETRY_SECONDS|RECHECK_TXS_EVERY_SECONDS|RETRY_UNTIL_FAILED|KOINKYT_REQUEST_TIMEOUT_SECONDS|REQUESTS_TIMEOUT|REDIS_HOST|SQLALCHEMY_DATABASE_URI|SHKEEPER_BACKEND_KEY|SHKEEPER_HOST|DEBUG|LOGGING_LEVEL)='

kubectl exec -n shkeeper deployment/tron-shkeeper -c app -- \
  env | grep -E '^(TRON_NETWORK|DEBUG|DATABASE|DB_URI|BALANCES_DATABASE|REDIS_HOST|FULLNODE_URL|MULTISERVER_CONFIG_JSON|MULTISERVER_REFRESH_BEST_SERVER_PERIOD|TRON_NODE_USERNAME|TRON_NODE_PASSWORD|TRON_CLIENT_TIMEOUT|BTC_USERNAME|BTC_PASSWORD|SHKEEPER_BACKEND_KEY|SHKEEPER_HOST|FORCE_WALLET_ENCRYPTION|CONCURRENT_MAX_WORKERS|CONCURRENT_MAX_RETRIES|SAVE_BALANCES_TO_DB|BANDWIDTH_PER_TRX_TRANSFER|BANDWIDTH_PER_DELEGE_CALL|BANDWIDTH_PER_UNDELEGATE_CALL|BANDWIDTH_PER_TRC20_TRANSFER_CALL|TRX_PER_BANDWIDTH_UNIT|BLOCK_SCANNER_STATS_LOG_PERIOD|BLOCK_SCANNER_MAX_BLOCK_CHUNK_SIZE|BLOCK_SCANNER_INTERVAL_TIME|BLOCK_SCANNER_LAST_BLOCK_NUM_HINT|ENERGY_SOURCE|REFEE|REFEE_FIXED_ENERGY_ORDER_AMOUNT|ENERGY_DELEGATION_MODE|ENERGY_DELEGATION_MODE_ALLOW_BURN_TRX_ON_PAYOUT|ENERGY_DELEGATION_MODE_ALLOW_BURN_TRX_FOR_BANDWITH|ENERGY_DELEGATION_MODE_ALLOW_ADDITIONAL_ENERGY_DELEGATION|ENERGY_DELEGATION_MODE_ENERGY_DELEGATION_FACTOR|ENERGY_DELEGATION_MODE_SEPARATE_BALANCE_AND_ENERGY_ACCOUNTS|ENERGY_DELEGATION_MODE_ENERGY_ACCOUNT_PUB_KEY|USDT_MIN_TRANSFER_THRESHOLD|USDC_MIN_TRANSFER_THRESHOLD|TRX_MIN_TRANSFER_THRESHOLD|INTERNAL_TX_FEE|TX_FEE|TX_FEE_LIMIT|BALANCES_RESCAN_PERIOD|DEVMODE_ENCRYPTION_PW|DEVMODE_SKIP_NOTIFICATIONS|DEVMODE_CELERY_NODELAY|DELAY_AFTER_FEE_TRANSFER|AML_RESULT_UPDATE_PERIOD|AML_SWEEP_ACCOUNTS_PERIOD|AML_WAIT_BEFORE_API_CALL|EXTERNAL_DRAIN_CONFIG|SR_VOTING|SR_VOTES|SR_VOTING_ALLOW_BURN_TRX)='
```

Sidecar API health:

```bash
kubectl exec -n shkeeper deployment/tron-shkeeper -c app -- python -c 'import os, requests; r=requests.post("http://127.0.0.1:6000/TRX/status", auth=(os.environ["BTC_USERNAME"], os.environ["BTC_PASSWORD"]), timeout=20); print(r.status_code, r.text)'
kubectl exec -n shkeeper deployment/tron-shkeeper -c app -- python -c 'import os, requests; r=requests.post("http://127.0.0.1:6000/USDT/balance", auth=(os.environ["BTC_USERNAME"], os.environ["BTC_PASSWORD"]), timeout=20); print(r.status_code, r.text)'
```

TON scanner health:

```bash
kubectl get pods -n shkeeper -l app=ton-shkeeper -o wide

kubectl get deployment ton-shkeeper -n shkeeper \
  -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}'

kubectl logs -n shkeeper deployment/ton-shkeeper -c app --tail=120
kubectl logs -n shkeeper deployment/ton-shkeeper -c tasks --tail=120
```

TON-USDT static address provisioning smoke test:

```bash
kubectl exec -n shkeeper deployment/ton-shkeeper -c app -- sh -c \
  'curl -sS -w "\nHTTP=%{http_code}\n" -H "X-Shkeeper-Backend-Key: ${SHKEEPER_BACKEND_KEY:-shkeeper}" http://shkeeper:5000/api/v1/TON/decrypt'

kubectl exec -n shkeeper deployment/shkeeper-deployment -- sh -c \
  'curl -sS -w "\nHTTP=%{http_code}\n" -u "${TON_USERNAME:-shkeeper}:${TON_PASSWORD:-shkeeper}" -X POST http://ton-shkeeper:6000/TON-USDT/generate-address'
```

Expected results:

- `/api/v1/TON/decrypt` returns `persistent_status=enabled`,
  `runtime_status=success`, and HTTP `200`. Do not paste or log the returned
  `key`.
- `/TON-USDT/generate-address` returns `{"status":"success","address":"..."}`
  and HTTP `200`.

Common TON address provisioning failures:

- `{"message":"Ignoring notification for TON: crypto is not available for processing"}`:
  the base `TON` crypto is not enabled in the main SHKeeper deployment. Keep
  `ton.enabled=true` even when the product only accepts `TON-USDT` deposits.
- `{"message":"Wrong backend key"}`: the request header does not match
  `SHKEEPER_BACKEND_KEY` in the main SHKeeper deployment. The TON sidecar sends
  the same `SHKEEPER_BACKEND_KEY`; those two values must match. Older
  deployments may still have `SHKEEPER_BTC_BACKEND_KEY` as a legacy fallback,
  but new configuration should use `SHKEEPER_BACKEND_KEY`. When diagnosing,
  compare hashes instead of printing the secret:

  ```bash
  kubectl exec -n shkeeper deployment/shkeeper-deployment -- sh -c 'v="${SHKEEPER_BACKEND_KEY:-${SHKEEPER_BTC_BACKEND_KEY:-shkeeper}}"; printf %s "$v" | sha256sum'
  kubectl exec -n shkeeper deployment/ton-shkeeper -c app -- sh -c 'v="${SHKEEPER_BACKEND_KEY:-shkeeper}"; printf %s "$v" | sha256sum'
  ```

- `persistent_status=enabled` with `runtime_status=pending`: wallet encryption
  is enabled, but the decryption password is not loaded into SHKeeper runtime
  memory. Open `/unlock` in the SHKeeper UI and enter the wallet encryption
  password, then repeat the smoke test. This can happen after
  `shkeeper-deployment` restarts because the runtime key is memory-only.
- `{"status":"error","msg":"'persistent_status'"}` from `TON-USDT/generate-address`:
  `ton-shkeeper` reached the main SHKeeper `/api/v1/TON/decrypt` endpoint, but
  the response did not contain the expected encryption status fields. Check the
  three cases above before changing code.

Check TON lag from inside the `app` container:

```bash
kubectl exec -i -n shkeeper deployment/ton-shkeeper -c app -- python - <<'PY'
import os
import time
import requests

url = "http://127.0.0.1:6000/TON/status"
response = requests.post(
    url,
    auth=(os.environ["TON_USERNAME"], os.environ["TON_PASSWORD"]),
    timeout=20,
)
data = response.json()
now = int(time.time())
last = int(data["last_block_timestamp"])
print("now", now)
print("last", last)
print("delta_sec", now - last)
print("delta_min", round((now - last) / 60, 2))
print(data)
PY
```

Live scanner progress:

```bash
kubectl logs -n shkeeper deployment/ton-shkeeper -c app -f --tail=100
```

Healthy scanner logs contain repeated block progress lines:

```text
Checked block 65409752. TON time: 0.32, Jetton time: 0.52
```

If the live logs only show repeated `/TON/status` access lines and no new
`Checked block` lines, the HTTP API is alive but the block scanner may be stuck.

Toncenter connectivity from inside the pod:

```bash
POD=$(kubectl get pod -n shkeeper -l app=ton-shkeeper -o jsonpath='{.items[0].metadata.name}')

kubectl exec -i -n shkeeper "$POD" -c app -- python - <<'PY'
import os
import time
import requests

base = os.environ.get("TONCENTER_API_URL", "https://toncenter.com").rstrip("/")
key = os.environ.get("TONCENTER_API_KEY") or os.environ.get("TONCENTER_INDEXER_KEY")
headers = {"X-API-Key": key} if key else {}

for name, url in [
    ("v2", f"{base}/api/v2/getMasterchainInfo"),
    ("v3", f"{base}/api/v3/masterchainInfo"),
]:
    started = time.time()
    try:
        response = requests.get(url, headers=headers, timeout=15)
        print(
            name,
            "code",
            response.status_code,
            "time",
            round(time.time() - started, 3),
            "body",
            response.text[:200],
        )
    except Exception as exc:
        print(name, "ERROR", repr(exc), "time", round(time.time() - started, 3))
PY
```

TON environment verification without printing secrets:

```bash
kubectl exec -n shkeeper "$POD" -c app -- sh -c \
  'env | egrep "TONCENTER|LAST_BLOCK|TON_" | sed -E "s/(KEY|PASSWORD)=.*/\1=***MASKED***/"'
```

Fee-deposit and TRON status:

```bash
kubectl exec -n shkeeper deployment/tron-shkeeper -c app -- sh -lc '
curl -sS -u "$BTC_USERNAME:$BTC_PASSWORD" \
  -X POST http://127.0.0.1:6000/TRX/fee-deposit-account
'

kubectl exec -n shkeeper deployment/tron-shkeeper -c app -- sh -lc '
curl -sS -u "$BTC_USERNAME:$BTC_PASSWORD" \
  -X POST http://127.0.0.1:6000/USDT/status
'
```

AML and callback flow for one txid:

```bash
TXID="REPLACE_WITH_TXID"

kubectl logs -n shkeeper deployment/shkeeper-deployment --since=60m \
  | grep "$TXID"

kubectl logs -n shkeeper deployment/aml-shkeeper -c tasks --since=60m \
  | grep -Ei 'check_transaction|recheck_transaction|succeeded|failed|ERROR'

kubectl logs -n shkeeper deployment/shkeeper-deployment --since=60m \
  | grep -Ei 'walletnotify|AML|Notification|Posting|accepted|failed|'"$TXID"
```

Expected successful callback line:

```text
Notification has been accepted by https://...
```

If webhook delivery fails:

- `HTTP code 429`: receiver rate-limited the callback.
- `ConnectionResetError`: receiver closed the connection.
- `HTTP code 405`: receiver URL exists but does not accept the method.
- SHKeeper will retry while the notification stays pending.

Manual balance rescan and sweep:

```bash
kubectl exec -n shkeeper deployment/tron-shkeeper -c tasks -- \
  celery -A celery_worker.celery call app.tasks.scan_accounts --serializer=pickle

kubectl logs -n shkeeper deployment/tron-shkeeper -c tasks --since=5m \
  | grep -Ei 'scan_accounts|TRC20 queue length|TRC20 balances histogram|Check ONETIME|Balance OK|Treshold not reached|transfer_trc20_from|Fee sent|has been sent|ERROR'
```

Manual single USDT payout through the TRON sidecar. This bypasses the UI /
`multipayout` precheck that requires `40 TRX` per payout, but the transaction
can still fail or burn TRX if the fee-deposit account lacks energy/bandwidth.

```bash
kubectl exec -n shkeeper deployment/tron-shkeeper -c app -- sh -lc '
DEST="REPLACE_WITH_TRON_DESTINATION"
AMOUNT="REPLACE_WITH_USDT_AMOUNT"
curl -sS -u "$BTC_USERNAME:$BTC_PASSWORD" \
  -X POST "http://127.0.0.1:6000/USDT/payout/${DEST}/${AMOUNT}"
'
```

Check the returned task:

```bash
kubectl exec -n shkeeper deployment/tron-shkeeper -c app -- sh -lc '
TASK_ID="REPLACE_WITH_TASK_ID"
curl -sS -u "$BTC_USERNAME:$BTC_PASSWORD" \
  -X POST "http://127.0.0.1:6000/USDT/task/${TASK_ID}"
'

kubectl logs -n shkeeper deployment/tron-shkeeper -c tasks --since=10m \
  | grep -Ei 'payout|Preparing payout|has been sent|TXID|receipt|energy|fee|ERROR|FAILED|resMessage|Not enough'
```

Koinkyt PDF/report URL:

```bash
kubectl exec -n shkeeper deployment/aml-shkeeper -c app -- sh -lc '
UID="REPLACE_WITH_AML_UID"
curl -sS -G "$KOINKYT_HOST/report-download/${UID}" \
  -H "X-API-Key: $KOINKYT_API_KEY" \
  --data-urlencode "language=ru"
'
```

## TON Scanner Watchdog

Install this watchdog on production while `ton-shkeeper` is pinned to
`vsyshost/ton-shkeeper:0.0.2`. It protects against the observed failure mode
where `/TON/status` keeps returning HTTP `200`, but `last_block_timestamp` stops
moving and no new `Checked block` lines appear in `app` logs.

The watchdog intentionally does not restart by lag size. A large lag is normal
while the scanner is catching up. It restarts only when `last_block_timestamp`
does not change for several checks.

Create `/root/check-ton-shkeeper.sh`:

```bash
cat > /root/check-ton-shkeeper.sh <<'EOF'
#!/bin/sh
set -eu

NS=shkeeper
DEPLOY=ton-shkeeper
STATE=/tmp/ton-shkeeper-watchdog-state
LOCKDIR=/tmp/ton-shkeeper-watchdog.lock
EVIDENCE_LOG=/var/log/ton-shkeeper-freeze-evidence.log
MAX_STUCK=2
MAX_FAILED=2
KUBECTL_TIMEOUT=45s

if ! mkdir "$LOCKDIR" 2>/dev/null; then
  echo "$(date -Is) already_running"
  exit 0
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null || true' EXIT

now="$(date +%s)"

prev_last=""
stuck_count=0
failed_count=0

if [ -f "$STATE" ]; then
  prev_last="$(awk '{print $1}' "$STATE")"
  stuck_count="$(awk '{print $2}' "$STATE")"
  failed_count="$(awk '{print $3}' "$STATE")"
fi

stuck_count="${stuck_count:-0}"
failed_count="${failed_count:-0}"

collect_evidence() {
  reason="$1"
  pod="$(kubectl get pod -n "$NS" -l app="$DEPLOY" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
  {
    echo "===== $(date -Is) freeze evidence reason=$reason pod=$pod last=${last:-unknown} lag_sec=${lag:-unknown} stuck_count=$stuck_count failed_count=$failed_count ====="
    echo "--- rollout state ---"
    timeout 20s kubectl get deployment -n "$NS" "$DEPLOY" -o wide || true
    timeout 20s kubectl get rs -n "$NS" -l app="$DEPLOY" -o wide || true
    timeout 20s kubectl get pods -n "$NS" -l app="$DEPLOY" -o wide || true
    timeout 20s kubectl get deployment -n "$NS" "$DEPLOY" -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}' || true

    echo "--- recent kubernetes events ---"
    timeout 20s kubectl get events -n "$NS" --sort-by=.lastTimestamp | tail -100 || true

    if [ -n "$pod" ]; then
      echo "--- app logs: recent errors and scanner lines ---"
      timeout 30s kubectl logs -n "$NS" "$pod" -c app --since=30m --tail=2000 \
        | grep -Ei '[СC]hecked block|Cannot|get all transactions|Block .*Failed|Exception|Traceback|Connection|timeout|429|Too Many|jetton|walletnotify|transaction|drain|sendBoc|ERROR|WARNING' \
        || true

      echo "--- app logs tail ---"
      timeout 30s kubectl logs -n "$NS" "$pod" -c app --tail=700 || true

      echo "--- tasks logs tail ---"
      timeout 30s kubectl logs -n "$NS" "$pod" -c tasks --tail=400 || true

      echo "--- redis logs tail ---"
      timeout 20s kubectl logs -n "$NS" "$pod" -c redis --tail=200 || true

      echo "--- pod describe ---"
      timeout 30s kubectl describe pod -n "$NS" "$pod" || true

      echo "--- app process and threads ---"
      timeout 20s kubectl exec -n "$NS" "$pod" -c app -- ps aux || true
      timeout 20s kubectl exec -n "$NS" "$pod" -c app -- ps -T -p 7 || true

      echo "--- app network sockets ---"
      timeout 20s kubectl exec -n "$NS" "$pod" -c app -- sh -c 'ss -tanp 2>/dev/null || netstat -tanp 2>/dev/null || true' || true

      echo "--- app disk and memory snapshot ---"
      timeout 20s kubectl exec -n "$NS" "$pod" -c app -- df -h || true
      timeout 20s kubectl exec -n "$NS" "$pod" -c app -- sh -c 'head -40 /proc/meminfo' || true

      echo "--- masked ton-shkeeper env ---"
      timeout 20s kubectl exec -n "$NS" "$pod" -c app -- sh -c 'env | egrep "TONCENTER|EVENTS|GET_JETTON|LAST_BLOCK|CHECK_NEW_BLOCK|SHKEEPER_HOST|CURRENT_TON_NETWORK" | sed -E "s/(KEY|PASSWORD)=.*/\1=***MASKED***/"' || true

      echo "--- database scanner state ---"
      timeout 30s kubectl exec -i -n "$NS" "$pod" -c app -- python - <<'PY' || true
from app import create_app
from app.models import Settings

app = create_app()
with app.app_context():
    for name in ("last_block",):
        row = Settings.query.filter_by(name=name).first()
        print(f"{name}={row.value if row else 'missing'}")
PY

      echo "--- toncenter probe from app container ---"
      timeout 45s kubectl exec -i -n "$NS" "$pod" -c app -- python - <<'PY' || true
import os
import time
import requests

try:
    from app import create_app
    from app.models import Settings
    app = create_app()
    with app.app_context():
        row = Settings.query.filter_by(name="last_block").first()
        seqno = int(row.value) if row and row.value is not None else None
except Exception as exc:
    print("db_last_block_error", repr(exc))
    seqno = None

api = os.environ.get("TONCENTER_API_URL", "https://toncenter.com").rstrip("/")
api_key = os.environ.get("TONCENTER_API_KEY", "")
indexer = os.environ.get("TONCENTER_INDEXER_URL", "https://toncenter.com").rstrip("/")
indexer_key = os.environ.get("TONCENTER_INDEXER_KEY", "")
headers = {"accept": "application/json"}

def probe(name, method, url, **kwargs):
    started = time.time()
    try:
        response = requests.request(method, url, timeout=15, headers=headers, **kwargs)
        print(name, "code", response.status_code, "time", round(time.time() - started, 3), "body", response.text[:300].replace("\n", " "))
    except Exception as exc:
        print(name, "ERROR", repr(exc), "time", round(time.time() - started, 3))

probe("api_getMasterchainInfo", "GET", f"{api}/api/v2/getMasterchainInfo", params={"api_key": api_key})
if seqno is not None:
    print("db_last_block_seqno", seqno)
    probe("api_getBlockHeader_last_block", "GET", f"{api}/api/v2/getBlockHeader", params={"api_key": api_key, "workchain": "-1", "shard": "8000000000000000", "seqno": seqno})
    probe("indexer_transactionsByMasterchainBlock", "GET", f"{indexer}/api/v3/transactionsByMasterchainBlock", params={"api_key": indexer_key, "seqno": seqno})
else:
    print("db_last_block_seqno missing")
PY
    else
      echo "No pod found for app=$DEPLOY"
    fi
    echo "===== end freeze evidence ====="
  } >> "$EVIDENCE_LOG" 2>&1 || true
}

restart_deploy() {
  reason="$1"
  echo "$(date -Is) TON scanner unhealthy reason=$reason, restarting $DEPLOY"
  kubectl rollout restart -n "$NS" deployment/"$DEPLOY"
  echo "${last:-$prev_last} 0 0" > "$STATE"
}

set +e
status_output="$(
  timeout "$KUBECTL_TIMEOUT" kubectl exec -i -n "$NS" deployment/"$DEPLOY" -c app -- python - <<'PY'
import os
import requests

url = "http://127.0.0.1:6000/TON/status"
response = requests.post(
    url,
    auth=(os.environ["TON_USERNAME"], os.environ["TON_PASSWORD"]),
    timeout=20,
)
data = response.json()
print(int(data["last_block_timestamp"]))
PY
)"
status_code=$?
set -e

if [ "$status_code" -ne 0 ]; then
  failed_count=$((failed_count + 1))
  echo "${prev_last:-0} $stuck_count $failed_count" > "$STATE"
  echo "$(date -Is) status_check_failed failed_count=$failed_count output=$status_output"

  if [ "$failed_count" -ge "$MAX_FAILED" ]; then
    collect_evidence "status_check_failed"
    restart_deploy "status_check_failed"
  fi
  exit 0
fi

last="$(printf '%s\n' "$status_output" | tail -n 1)"

case "$last" in
  ''|*[!0-9]*)
    failed_count=$((failed_count + 1))
    echo "${prev_last:-0} $stuck_count $failed_count" > "$STATE"
    echo "$(date -Is) status_check_invalid failed_count=$failed_count output=$status_output"

    if [ "$failed_count" -ge "$MAX_FAILED" ]; then
      collect_evidence "status_check_invalid"
      restart_deploy "status_check_invalid"
    fi
    exit 0
    ;;
esac

if [ -n "$prev_last" ] && [ "$last" = "$prev_last" ]; then
  stuck_count=$((stuck_count + 1))
else
  stuck_count=0
fi

failed_count=0
echo "$last $stuck_count $failed_count" > "$STATE"

lag=$((now - last))

echo "$(date -Is) last=$last lag_sec=$lag stuck_count=$stuck_count"

if [ "$stuck_count" -ge "$MAX_STUCK" ]; then
  collect_evidence "timestamp_stuck"
  restart_deploy "timestamp_stuck"
fi
EOF
```

Validate and run once:

```bash
chmod +x /root/check-ton-shkeeper.sh
sh -n /root/check-ton-shkeeper.sh
/root/check-ton-shkeeper.sh
```

Expected output shape:

```text
2026-05-08T14:10:03+00:00 last=1778231596 lag_sec=17807 stuck_count=0
```

Interpretation:

- `lag_sec` large but `stuck_count=0`: scanner is moving; do not restart.
- `last` increases every check: scanner is catching up.
- `last` unchanged for one check: watch closely; no restart yet.
- `last` unchanged for `MAX_STUCK=2` checks: watchdog writes pre-restart
  evidence to `/var/log/ton-shkeeper-freeze-evidence.log`, then restarts
  `deployment/ton-shkeeper`.
- `status_check_failed` means the watchdog could not call the local
  `/TON/status` endpoint inside the `ton-shkeeper` app container.
- `status_check_invalid` means `/TON/status` returned something that did not
  contain a numeric `last_block_timestamp`.

For a simple production setup, run the watchdog from cron once per minute. With
`MAX_STUCK=2`, the worst case is roughly two minutes from freeze to rollout
restart if the scanner freezes immediately after a successful check. If the
local status request itself hangs, add up to the Python request timeout on the
failed checks.

This production script intentionally keeps the current fast restart behavior and
adds pre-restart evidence collection. If a specific Toncenter block/chunk is
problematic, repeated restarts may still happen; the evidence log is what lets
us identify whether the failure is in block scan, Jetton transfer scan,
transaction lookup, walletnotify, or drain.

Install the cron job as `root`:

```bash
crontab -e
```

Add one line:

```cron
* * * * * /root/check-ton-shkeeper.sh >> /var/log/ton-shkeeper-watchdog.log 2>&1
```

For stricter production where TON must recover faster than cron granularity,
use a systemd loop instead of cron. Recommended starting point:

- `CHECK_INTERVAL=30` seconds and `MAX_STUCK=4`: restart after roughly two
  minutes without timestamp progress.
- `CHECK_INTERVAL=20` seconds and `MAX_STUCK=4`: restart after roughly 80
  seconds without timestamp progress.
- Avoid `MAX_STUCK=1`. A single slow Toncenter/API request should not force a
  rollout.

For a fast production loop, disable the cron job first if it was installed:

```bash
crontab -l
crontab -e
```

Remove this line if present:

```cron
* * * * * /root/check-ton-shkeeper.sh >> /var/log/ton-shkeeper-watchdog.log 2>&1
```

Install a systemd service that runs the existing one-shot script every 30
seconds:

```bash
cat > /etc/systemd/system/ton-shkeeper-watchdog.service <<'EOF'
[Unit]
Description=TON SHKeeper scanner progress watchdog
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Restart=always
RestartSec=5
Environment=CHECK_INTERVAL=30
ExecStart=/bin/sh -c 'while true; do /root/check-ton-shkeeper.sh >> /var/log/ton-shkeeper-watchdog.log 2>&1; sleep "$CHECK_INTERVAL"; done'

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now ton-shkeeper-watchdog.service
```

For a 20-second loop, change `CHECK_INTERVAL=30` to `CHECK_INTERVAL=20` and
restart the service:

```bash
sed -i 's/^Environment=CHECK_INTERVAL=.*/Environment=CHECK_INTERVAL=20/' /etc/systemd/system/ton-shkeeper-watchdog.service
systemctl daemon-reload
systemctl restart ton-shkeeper-watchdog.service
```

Verify the service and logs:

```bash
systemctl status ton-shkeeper-watchdog.service --no-pager
tail -n 20 /var/log/ton-shkeeper-watchdog.log
tail -n 200 /var/log/ton-shkeeper-freeze-evidence.log
```

To temporarily disable automatic restarts while collecting evidence, keep the
cron job but raise the threshold:

```bash
sed -i 's/^MAX_STUCK=.*/MAX_STUCK=999/' /root/check-ton-shkeeper.sh
```

Restore production restart behavior:

```bash
sed -i 's/^MAX_STUCK=.*/MAX_STUCK=2/' /root/check-ton-shkeeper.sh
```

## Troubleshooting

### TON scanner stops while `/TON/status` stays healthy

Observed production failure with `vsyshost/ton-shkeeper:0.0.2`:

```text
Cannot get all transactions from 65409257 block,
("Connection broken: ConnectionResetError(104, 'Connection reset by peer')",
ConnectionResetError(104, 'Connection reset by peer')) wait 10 seconds
```

After this error:

- `kubectl get pod` showed `3/3 Running`, `RESTARTS=0`.
- Disk was not full.
- `tasks` / Celery remained healthy and `refresh_balances` succeeded.
- `/TON/status` continued returning HTTP `200`.
- `last_block_timestamp` stopped changing.
- `app` logs stopped printing `Checked block ...`.
- `kubectl rollout restart -n shkeeper deployment/ton-shkeeper` immediately
  restored `Checked block ...` progress.

Current root-cause assessment:

- Trigger: remote Toncenter indexer gaps or upstream/network failures while
  reading transactions for specific masterchain blocks. Confirmed examples
  include repeated `404` responses from
  `/api/v3/transactionsByMasterchainBlock` for individual seqnos.
- Defect: `ton-shkeeper:0.0.2` treats those block reads as failed scanner work
  inside a parallel chunk and does not advance `last_block` unless the whole
  chunk succeeds, even though the gunicorn worker and HTTP API remain alive.
- Kubernetes readiness/liveness cannot detect the issue if it only checks HTTP
  availability. Health must include `last_block_timestamp` progress.
- See [`TON_SCANNER_RESILIENCE.md`](TON_SCANNER_RESILIENCE.md) for the concrete
  production-safe fix plan. Native TON is not required for the current
  TON-USDT-first deployment, but the fix must preserve safe native TON support
  for future enablement.

When the issue occurs, collect evidence before restarting if possible:

```bash
POD=$(kubectl get pod -n shkeeper -l app=ton-shkeeper -o jsonpath='{.items[0].metadata.name}')

kubectl logs -n shkeeper "$POD" -c app --since=60m \
  | egrep -i 'Cannot|get all transactions|Connection|Reset|timeout|429|Ratelimit|Traceback|ERROR|WARNING|Checked block' \
  | tail -300

kubectl exec -n shkeeper "$POD" -c app -- ps aux
kubectl exec -n shkeeper "$POD" -c app -- ps -T -p 7
kubectl exec -n shkeeper "$POD" -c app -- sh -c 'ss -tanp 2>/dev/null || netstat -tanp 2>/dev/null || true'
```

Then recover:

```bash
kubectl rollout restart -n shkeeper deployment/ton-shkeeper
kubectl rollout status -n shkeeper deployment/ton-shkeeper
kubectl logs -n shkeeper deployment/ton-shkeeper -c app -f --tail=100
```

Report upstream with:

```text
Image: vsyshost/ton-shkeeper:0.0.2
SHKeeper chart: shkeeper-1.7.22
Problem repeated.

Last scanner log before freeze:
Cannot get all transactions from ... block,
ConnectionResetError(104, 'Connection reset by peer') wait 10 seconds

After that:
- no more "Checked block ..." logs
- /TON/status still returns HTTP 200
- last_block_timestamp freezes
- pod has 0 restarts
- disk ok
- Celery/Redis ok
- rollout restart immediately resumes scanning
```

### ImagePullBackOff for `ghcr.io/nilof470/tron-shkeeper`

Check the pull secret and image tag:

```bash
kubectl get secret -n shkeeper ghcr-nilof470
kubectl describe pod -n shkeeper -l app=tron-shkeeper
```

Confirm the GitHub token has `repo`, `write:packages`, and `read:packages` for a
private GHCR package.

### Wallet encryption waits forever

Logs show:

```text
Waiting for encryption key...
```

Open the SHKeeper UI and enter the wallet encryption password. Then re-check the
sidecar logs.

### `Threshold not reached`

Example:

```text
Has: 1 USDT need: 1 USDT
```

For TRC20 sweeps, the code requires `balance > threshold`. Set
`USDT_MIN_TRANSFER_THRESHOLD` below the smallest amount to sweep.

### Activation burns TRX

When an onetime address is not active, the sidecar sends `0.1 TRX` from
`fee_deposit` to activate it. If `ENERGY_DELEGATION_MODE_ALLOW_BURN_TRX_FOR_BANDWITH`
is `true`, TRX may burn for the activation transfer bandwidth.

Keep `ENERGY_DELEGATION_MODE_ALLOW_BURN_TRX_ON_PAYOUT=false` to prevent fallback
TRX burn for the USDT sweep itself when re:Fee fails.

### re:Fee 403 whitelist error

Add the VPS public IP from `curl -4 ifconfig.me` to the re:Fee whitelist, then
retry the sweep.

### `One-time account has no bandwidth`

The onetime address is active but lacks bandwidth for the TRC20 transfer. Wait
for bandwidth to recover, manually delegate/rent bandwidth to that address, or
retry after activation has settled.

### `UNIQUE constraint failed: settings.name` on first startup

This can appear during first scanner startup when the app initializes
`last_seen_block_num` concurrently. If later logs show scanner stats with
`eta=in sync`, it recovered and is not blocking.

### USDC encryption warnings while USDC is disabled

Warnings like this are noisy but not blocking when `usdc.enabled=false`:

```text
Ignoring notification for USDC: crypto is not available for processing
```

## Backup Notes

Before production traffic, define and test a backup procedure for:

- SHKeeper MariaDB data
- `tron-shkeeper` SQLite data under the sidecar PVC
- `/root/shkeeper-values.yaml`
- wallet encryption password
- admin password
- re:Fee API key
- GHCR pull token or replacement deployment token

Do not rely only on container images; wallet state lives in persistent volumes.
