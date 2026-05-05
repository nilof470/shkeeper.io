# Koinkyt AML Deployment

This runbook deploys the Koinkyt AML integration on the existing SHKeeper
k3s/Helm installation. The deployment shape intentionally matches the previous
TRON/re:Fee process: keep one private `/root/shkeeper-values.yaml` per
environment and deploy it with a single Helm command.

Dev and prod should use the same image artifacts. The environment difference is
only in the private values file: domain, enabled coins, API keys, risk profile
IDs, thresholds, and other operational settings.

## Current Local Images

These images have been built locally for `linux/amd64`:

```text
ghcr.io/nilof470/shkeeper.io:f8bbd4d
ghcr.io/nilof470/aml-shkeeper:fcc7416
```

## Build Or Push Images

If the local images already exist, push them:

```bash
docker login ghcr.io -u nilof470

docker push ghcr.io/nilof470/shkeeper.io:f8bbd4d
docker push ghcr.io/nilof470/aml-shkeeper:fcc7416
```

If rebuilding is needed, build and push immutable commit tags:

```bash
docker login ghcr.io -u nilof470

cd /Users/test/PycharmProjects/shkeeper.io
docker buildx build \
  --platform linux/amd64 \
  -t ghcr.io/nilof470/shkeeper.io:f8bbd4d \
  --push .

cd /Users/test/PycharmProjects/aml-shkeeper
docker buildx build \
  --platform linux/amd64 \
  -t ghcr.io/nilof470/aml-shkeeper:fcc7416 \
  --push .
```

## First-Time Values Setup

Merge the Koinkyt block from `deploy/koinkyt-values.yaml` into the private
`/root/shkeeper-values.yaml` on the target VPS. Keep the rest of the existing
values file intact: enabled coins, domain, storage class, TRON/re:Fee settings,
and any existing sidecar settings remain environment-owned.

Required Koinkyt values:

```yaml
dev:
  imagePullSecrets:
    - name: ghcr-nilof470

shkeeper:
  image: ghcr.io/nilof470/shkeeper.io:f8bbd4d
  extraEnv:
    AML_ENABLED: "true"
    AML_PROVIDER: koinkyt
    AML_SHKEEPER_HOST: http://aml-shkeeper:6000
    AML_MAX_ACCEPT_SCORE: "0.10"
    AML_MIN_CHECK_AMOUNT_FIAT: "100"
    AML_SKIP_CUMULATIVE_LIMIT_FIAT: "300"
    AML_SKIP_CUMULATIVE_WINDOW_HOURS: "24"
    AML_PENDING_TIMEOUT_SECONDS: "1800"
    AML_RETRY_DELAY_SECONDS: "120"

aml:
  enabled: true

aml_shkeeper:
  image: ghcr.io/nilof470/aml-shkeeper:fcc7416
  extraEnv:
    CURRENT_PROVIDER: koinkyt
    KOINKYT_HOST: https://explorer.coinkyt.com/openapi/v1
    KOINKYT_API_KEY: "REPLACE_WITH_KOINKYT_API_KEY"
    KOINKYT_RISK_PROFILE_IDS: ""
    AML_DEFAULT_THRESHOLD: "0.10"
    CHECK_TIMEOUT_SECONDS: "1800"
    CHECK_RETRY_SECONDS: "120"
    RECHECK_TXS_EVERY_SECONDS: "120"
    KOINKYT_REQUEST_TIMEOUT_SECONDS: "10"
```

`dev.imagePullSecrets` is the official chart key name. It is not a statement
that the deployment is dev-only.

## Updating Image Tags

This is the direct analogue of the TRON command:

```bash
SHKEEPER_TAG=f8bbd4d
AML_TAG=fcc7416

sed -i "s|image: ghcr.io/nilof470/shkeeper.io:.*|image: ghcr.io/nilof470/shkeeper.io:${SHKEEPER_TAG}|" /root/shkeeper-values.yaml
sed -i "s|image: ghcr.io/nilof470/aml-shkeeper:.*|image: ghcr.io/nilof470/aml-shkeeper:${AML_TAG}|" /root/shkeeper-values.yaml
```

After that, deploy with the same single Helm command:

```bash
helm upgrade -f /root/shkeeper-values.yaml shkeeper vsys-host/shkeeper
```

For a fresh VPS, install instead of upgrade:

```bash
helm install -f /root/shkeeper-values.yaml shkeeper vsys-host/shkeeper
```

## Required VPS Secret For Private GHCR

If the GHCR packages are private, the pull secret must exist:

```bash
read -s GHCR_TOKEN

kubectl -n shkeeper create secret docker-registry ghcr-nilof470 \
  --docker-server=ghcr.io \
  --docker-username=nilof470 \
  --docker-password="$GHCR_TOKEN" \
  --docker-email=none@example.com \
  --dry-run=client -o yaml | kubectl apply -f -

unset GHCR_TOKEN
```

## Rollout Verification

```bash
kubectl rollout status deployment/shkeeper-deployment -n shkeeper
kubectl rollout status deployment/aml-shkeeper -n shkeeper
kubectl get pods -n shkeeper

kubectl get deployment shkeeper-deployment -n shkeeper \
  -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}'

kubectl get deployment aml-shkeeper -n shkeeper \
  -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}'
```

Expected images:

```text
ghcr.io/nilof470/shkeeper.io:f8bbd4d
ghcr.io/nilof470/aml-shkeeper:fcc7416 ghcr.io/nilof470/aml-shkeeper:fcc7416 redis
```

Verify AML env:

```bash
kubectl exec -n shkeeper deployment/shkeeper-deployment -- env | grep '^AML_'
kubectl exec -n shkeeper deployment/aml-shkeeper -c app -- env | grep -E '^(CURRENT_PROVIDER|KOINKYT_HOST|KOINKYT_API_KEY|KOINKYT_RISK_PROFILE_IDS|AML_DEFAULT_THRESHOLD)='
```

## Smoke Test Without A Real Koinkyt Check

This checks Kubernetes DNS, Basic Auth, and the sidecar API contract. `DOGE` is
intentionally unsupported by Koinkyt in this integration, so a `400 unsupported
crypto` response is expected.

```bash
kubectl exec -n shkeeper deployment/shkeeper-deployment -- sh -lc '
curl -i -u "$AML_USERNAME:$AML_PASSWORD" \
  -H "Content-Type: application/json" \
  -d "{\"deposit_id\":\"smoke\",\"idempotency_key\":\"smoke\",\"crypto\":\"DOGE\",\"txid\":\"x\",\"address\":\"x\",\"amount_crypto\":\"1\",\"asset\":\"DOGE\",\"network\":\"DOGE\",\"direction\":\"deposit\"}" \
  http://aml-shkeeper:6000/api/v1/checks
'
```

Expected result:

```text
HTTP/1.1 400 BAD REQUEST
{"msg":"unsupported crypto","status":"error"}
```

## Optional Real Koinkyt Connectivity Test

Use a known public transaction hash for the selected chain. This calls Koinkyt
directly from the pod and does not create a SHKeeper deposit.

```bash
kubectl exec -n shkeeper deployment/aml-shkeeper -c app -- sh -lc '
curl -i -sS -G "$KOINKYT_HOST/transaction" \
  -H "accept: application/json" \
  -H "X-API-Key: $KOINKYT_API_KEY" \
  --data-urlencode "blockchain=btc" \
  --data-urlencode "token=" \
  --data-urlencode "transaction=REPLACE_WITH_BTC_TXID"
'
```

## Deposit Flow Test

Create a normal SHKeeper payment request with `callback_url`. The callback URL
is not configured in Helm; it belongs to each invoice/payment request.

```bash
read -s SHKEEPER_API_KEY

curl -sS -X POST 'http://127.0.0.1:5000/api/v1/USDT/payment_request' \
  -H "X-Shkeeper-Api-Key: ${SHKEEPER_API_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{
    "external_id": "dev-koinkyt-usdt-001",
    "fiat": "USD",
    "amount": "1",
    "callback_url": "https://example.com/shkeeper-callback"
  }'

unset SHKEEPER_API_KEY
```

After the customer sends funds to the returned wallet, SHKeeper should:

1. detect the incoming transaction;
2. create an AML check in `aml-shkeeper`;
3. wait for Koinkyt `risk_score`;
4. credit automatically only when the policy returns `deposit_decision=credit`;
5. send the merchant callback only after the AML gate passes.

Useful logs:

```bash
kubectl logs -n shkeeper deployment/shkeeper-deployment --tail=120
kubectl logs -n shkeeper deployment/aml-shkeeper -c app --tail=120
kubectl logs -n shkeeper deployment/aml-shkeeper -c tasks --tail=120
```
