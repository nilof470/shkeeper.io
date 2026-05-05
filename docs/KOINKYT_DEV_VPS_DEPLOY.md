# Koinkyt AML Dev VPS Deployment

This runbook deploys the Koinkyt AML integration on top of the existing SHKeeper
k3s/Helm deployment. It is intentionally written as an overlay to the main
`/root/shkeeper-values.yaml` used by the official `vsys-host/shkeeper` chart.

## Local Build And Push

Run locally from this workstation. Replace the GHCR namespace only if the image
owner changes.

```bash
docker login ghcr.io -u nilof470

cd /Users/test/PycharmProjects/shkeeper.io
docker buildx build \
  --platform linux/amd64 \
  -t ghcr.io/nilof470/shkeeper.io:koinkyt-dev \
  --push .

cd /Users/test/PycharmProjects/aml-shkeeper
docker buildx build \
  --platform linux/amd64 \
  -t ghcr.io/nilof470/aml-shkeeper:koinkyt-dev \
  --push .
```

The mutable dev image tag is `koinkyt-dev`. The Koinkyt integration code was
introduced in these commits:

- `shkeeper.io`: `982c804`
- `aml-shkeeper`: `f2a25dd`

## VPS Files

Copy the overlay to the VPS:

```bash
scp /Users/test/PycharmProjects/shkeeper.io/deploy/koinkyt-dev-values.yaml root@DEV_VPS_IP:/root/koinkyt-dev-values.yaml
```

Keep the existing `/root/shkeeper-values.yaml`; it still owns the enabled coins,
domain, storage class, TRON/re:Fee settings, and other deployment-specific
values.

## VPS Secrets

Create or refresh the private GHCR pull secret:

```bash
read -s GHCR_TOKEN

kubectl create namespace shkeeper --dry-run=client -o yaml | kubectl apply -f -
kubectl label namespace shkeeper app.kubernetes.io/managed-by=Helm --overwrite
kubectl annotate namespace shkeeper \
  meta.helm.sh/release-name=shkeeper \
  meta.helm.sh/release-namespace=default \
  --overwrite

kubectl -n shkeeper create secret docker-registry ghcr-nilof470 \
  --docker-server=ghcr.io \
  --docker-username=nilof470 \
  --docker-password="$GHCR_TOKEN" \
  --docker-email=none@example.com \
  --dry-run=client -o yaml | kubectl apply -f -

unset GHCR_TOKEN
```

Create the Koinkyt API secret. `KOINKYT_RISK_PROFILE_IDS` can stay empty for
dev; the integration will use Koinkyt `risk_score` and will not receive profile
alerts.

```bash
read -s KOINKYT_API_KEY

kubectl -n shkeeper create secret generic koinkyt-aml \
  --from-literal=KOINKYT_API_KEY="$KOINKYT_API_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -

unset KOINKYT_API_KEY
```

## Helm Upgrade

Install the chart if this is a fresh VPS:

```bash
helm repo add vsys-host https://vsys-host.github.io/helm-charts
helm repo add mittwald https://helm.mittwald.de
helm repo update
helm install kubernetes-secret-generator mittwald/kubernetes-secret-generator

helm install \
  -f /root/shkeeper-values.yaml \
  -f /root/koinkyt-dev-values.yaml \
  shkeeper vsys-host/shkeeper
```

Upgrade an existing VPS:

```bash
helm repo update
helm upgrade \
  -f /root/shkeeper-values.yaml \
  -f /root/koinkyt-dev-values.yaml \
  shkeeper vsys-host/shkeeper
```

Inject the Koinkyt secret into both `aml-shkeeper` containers:

```bash
kubectl -n shkeeper set env deployment/aml-shkeeper \
  --containers=app,tasks \
  --from=secret/koinkyt-aml
```

Wait for rollout:

```bash
kubectl rollout status deployment/shkeeper-deployment -n shkeeper
kubectl rollout status deployment/aml-shkeeper -n shkeeper
kubectl get pods -n shkeeper
```

## Verify Images And Env

```bash
kubectl get deployment shkeeper-deployment -n shkeeper \
  -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}'

kubectl get deployment aml-shkeeper -n shkeeper \
  -o jsonpath='{.spec.template.spec.containers[*].image}{"\n"}'

kubectl exec -n shkeeper deployment/shkeeper-deployment -- env | grep '^AML_'
kubectl exec -n shkeeper deployment/aml-shkeeper -c app -- env | grep -E '^(CURRENT_PROVIDER|KOINKYT_HOST|KOINKYT_API_KEY|KOINKYT_RISK_PROFILE_IDS|AML_DEFAULT_THRESHOLD)='
```

Expected images:

```text
ghcr.io/nilof470/shkeeper.io:koinkyt-dev
ghcr.io/nilof470/aml-shkeeper:koinkyt-dev ghcr.io/nilof470/aml-shkeeper:koinkyt-dev redis
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
