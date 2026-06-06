#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DEFAULT_CHART="oci://ghcr.io/nilof470/helm-charts/shkeeper"
DEFAULT_CHART_VERSION="1.7.28-nilof470.9"

VALUES_FILE="${1:-/root/shkeeper-values.yaml}"
RELEASE="${RELEASE:-shkeeper}"
RELEASE_NS="${RELEASE_NS:-default}"
APP_NS="${APP_NS:-shkeeper}"
if [ -z "${CHART+x}" ]; then
    CHART="$DEFAULT_CHART"
fi
if [ -z "${CHART_VERSION+x}" ]; then
    if [ "$CHART" = "$DEFAULT_CHART" ]; then
        CHART_VERSION="$DEFAULT_CHART_VERSION"
    else
        CHART_VERSION=""
    fi
fi
TIMEOUT="${TIMEOUT:-300s}"
PAYOUT_SECRET_PREFLIGHT="${PAYOUT_SECRET_PREFLIGHT:-required}"

usage() {
    cat <<'EOF'
Usage:
  deploy/shkeeper/upgrade.sh [VALUES_FILE]

Environment:
  RELEASE       Helm release name. Default: shkeeper
  RELEASE_NS    Helm release namespace. Default: default
  APP_NS        Kubernetes namespace with SHKeeper deployments. Default: shkeeper
  CHART         Helm chart path/ref. Default: oci://ghcr.io/nilof470/helm-charts/shkeeper
  CHART_VERSION Helm chart version for remote chart refs. Default: 1.7.28-nilof470.9
  TIMEOUT       kubectl rollout timeout. Default: 300s
  PAYOUT_SECRET_PREFLIGHT
                Payout sidecar secret contract preflight: required or skip.
                Default: required
  PAYOUT_SECRET_GUARD_REQUIRED_RAILS
                Optional comma-separated rails to validate, for example ETH-USDT.
                Default: all rails declared by PAYOUT_SIDECAR_KEYS_JSON
  PAYOUT_SIDECAR_SIGNING_SECRET_NAME
                Default: grither-prod-shkeeper-payout-sidecar-signing-keys
  PAYOUT_SIDECAR_SIGNING_SECRET_KEY
                Default: PAYOUT_SIDECAR_KEYS_JSON
  PAYOUT_SIDECAR_CONSUMER_SECRET_NAME
                Default: grither-prod-sidecar-payout-consumer-keys
  PAYOUT_SIDECAR_CONSUMER_SECRET_KEY
                Default: PAYOUT_CONSUMER_KEYS_JSON

This is the guarded production deploy entry point for the SHKeeper fork.
The Helm chart fork owns Kubernetes manifests; this script applies it, waits
for rollouts, and verifies the TRON payout topology when the TRON sidecar is enabled.
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

if [ -z "$CHART" ]; then
    echo "ERROR: Helm chart ref is empty" >&2
    exit 1
fi

CHART_IS_LOCAL=false
if [ -f "$CHART/Chart.yaml" ]; then
    CHART_IS_LOCAL=true
elif ! printf '%s' "$CHART" | grep -Eq '^(oci://|[^/]+/[^/]+$)'; then
    echo "ERROR: Helm chart not found or unsupported ref: $CHART" >&2
    echo "Use the default OCI chart ref, a repo/chart ref, or CHART=/path/to/charts/shkeeper." >&2
    exit 1
fi

case "$PAYOUT_SECRET_PREFLIGHT" in
    required)
        echo "==> Verifying payout sidecar secret contract"
        python3 "$SCRIPT_DIR/payout-secret-guard.py" verify-cluster \
            --namespace "$APP_NS" \
            --signing-secret-name "${PAYOUT_SIDECAR_SIGNING_SECRET_NAME:-grither-prod-shkeeper-payout-sidecar-signing-keys}" \
            --signing-secret-key "${PAYOUT_SIDECAR_SIGNING_SECRET_KEY:-PAYOUT_SIDECAR_KEYS_JSON}" \
            --sidecar-consumer-secret-name "${PAYOUT_SIDECAR_CONSUMER_SECRET_NAME:-grither-prod-sidecar-payout-consumer-keys}" \
            --sidecar-consumer-secret-key "${PAYOUT_SIDECAR_CONSUMER_SECRET_KEY:-PAYOUT_CONSUMER_KEYS_JSON}" \
            --required-rails "${PAYOUT_SECRET_GUARD_REQUIRED_RAILS:-}"
        ;;
    skip)
        echo "==> Skipping payout sidecar secret preflight"
        ;;
    *)
        echo "ERROR: PAYOUT_SECRET_PREFLIGHT must be 'required' or 'skip'" >&2
        exit 1
        ;;
esac

echo "==> Helm upgrade: release=$RELEASE release_ns=$RELEASE_NS chart=$CHART values=$VALUES_FILE"
if [ "$CHART_IS_LOCAL" = true ] || [ -z "$CHART_VERSION" ]; then
    helm upgrade --install -n "$RELEASE_NS" -f "$VALUES_FILE" "$RELEASE" "$CHART" \
        --timeout "$TIMEOUT"
else
    helm upgrade --install -n "$RELEASE_NS" -f "$VALUES_FILE" "$RELEASE" "$CHART" \
        --version "$CHART_VERSION" --timeout "$TIMEOUT"
fi

echo "==> Waiting for main SHKeeper deployment"
kubectl -n "$APP_NS" rollout status deployment/shkeeper-deployment --timeout="$TIMEOUT"

if kubectl -n "$APP_NS" get deployment/tron-shkeeper >/dev/null 2>&1; then
    echo "==> Waiting for TRON sidecar rollout"
    kubectl -n "$APP_NS" rollout status deployment/tron-shkeeper --timeout="$TIMEOUT"

    if kubectl -n "$APP_NS" get deployment/tron-usdt-payouts >/dev/null 2>&1; then
        echo "==> Waiting for TRON USDT payout worker rollout"
        kubectl -n "$APP_NS" rollout status deployment/tron-usdt-payouts --timeout="$TIMEOUT"
    else
        echo "==> TRON USDT payout worker deployment is not rendered; skipping worker rollout wait"
    fi

    echo "==> Verifying TRON payout topology"
    python3 "$SCRIPT_DIR/verify-tron-usdt-payout-worker.py" \
        --namespace "$APP_NS" \
        --deployment tron-shkeeper \
        --worker-deployment tron-usdt-payouts
else
    echo "==> TRON sidecar is disabled; skipping TRON worker verification"
fi

kubectl -n "$APP_NS" get pods | grep -E 'tron-shkeeper|tron-usdt-payouts' || true
echo "OK: SHKeeper deployment completed"
