#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DEFAULT_CHART=$(CDPATH= cd -- "$SCRIPT_DIR/../../../shkeeper-helm-charts/charts/shkeeper" 2>/dev/null && pwd || true)

VALUES_FILE="${1:-/root/shkeeper-values.yaml}"
RELEASE="${RELEASE:-shkeeper}"
RELEASE_NS="${RELEASE_NS:-default}"
APP_NS="${APP_NS:-shkeeper}"
CHART="${CHART:-$DEFAULT_CHART}"
TIMEOUT="${TIMEOUT:-300s}"

usage() {
    cat <<'EOF'
Usage:
  deploy/shkeeper/upgrade.sh [VALUES_FILE]

Environment:
  RELEASE       Helm release name. Default: shkeeper
  RELEASE_NS    Helm release namespace. Default: default
  APP_NS        Kubernetes namespace with SHKeeper deployments. Default: shkeeper
  CHART         Helm chart path/ref. Default: sibling shkeeper-helm-charts fork.
  TIMEOUT       kubectl rollout timeout. Default: 300s

This wrapper is the supported production deploy path for the SHKeeper fork.
It applies the repo-owned Helm chart fork, waits for rollouts, and verifies
the TRON USDT payout worker when payout resource provisioning is enabled.
EOF
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    usage
    exit 0
fi

if [ ! -f "$VALUES_FILE" ]; then
    echo "ERROR: values file not found: $VALUES_FILE" >&2
    exit 1
fi

if [ -z "$CHART" ] || [ ! -f "$CHART/Chart.yaml" ]; then
    echo "ERROR: Helm chart fork not found: ${CHART:-<empty>}" >&2
    echo "Clone the chart fork next to shkeeper.io or set CHART=/path/to/charts/shkeeper." >&2
    exit 1
fi

echo "==> Helm upgrade: release=$RELEASE release_ns=$RELEASE_NS chart=$CHART values=$VALUES_FILE"
helm upgrade --install -n "$RELEASE_NS" -f "$VALUES_FILE" "$RELEASE" "$CHART" \
    --atomic --timeout "$TIMEOUT"

echo "==> Waiting for main SHKeeper deployment"
kubectl -n "$APP_NS" rollout status deployment/shkeeper-deployment --timeout="$TIMEOUT"

if kubectl -n "$APP_NS" get deployment/tron-shkeeper >/dev/null 2>&1; then
    echo "==> Waiting for TRON sidecar rollout"
    kubectl -n "$APP_NS" rollout status deployment/tron-shkeeper --timeout="$TIMEOUT"

    echo "==> Verifying TRON USDT payout worker"
    python3 "$SCRIPT_DIR/verify-tron-usdt-payout-worker.py" \
        --namespace "$APP_NS" \
        --deployment tron-shkeeper
else
    echo "==> TRON sidecar is disabled; skipping TRON worker verification"
fi

kubectl -n "$APP_NS" get pods | grep tron-shkeeper || true
echo "OK: SHKeeper deployment completed"
