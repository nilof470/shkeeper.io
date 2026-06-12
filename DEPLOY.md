# SHKeeper HTTPS Domain Deployment

This document records the HTTPS domain setup used for SHKeeper so the same flow can be repeated for production.

The current dev domain is:

```text
dev.core.grither.company
```

The setup follows the official SHKeeper domain/SSL approach:

- k3s built-in Traefik
- cert-manager from Jetstack Helm chart
- Let's Encrypt HTTP-01 challenge
- Traefik `IngressRoute` to the SHKeeper service on port `5000`

Reference:

```text
https://shkeeper.io/kb/use-cases/how-to-use-domain-or-ssl-with-shkeeper
```

## DNS

Create a DNS record in Cloudflare.

For dev:

```text
Type: A
Name: dev.core
Content: <public VPS IP>
Proxy status: DNS only
```

For prod, use the chosen production hostname, for example:

```text
Type: A
Name: core
Content: <production public VPS IP>
Proxy status: DNS only
```

Do not use Cloudflare proxy mode for this setup. The record must be `DNS only`.

Ports `80/tcp` and `443/tcp` must be reachable from the internet. Port `80` is required for the Let's Encrypt HTTP-01 challenge.

## Install cert-manager

Run on the VPS where k3s and SHKeeper are installed:

```bash
helm repo add jetstack https://charts.jetstack.io
helm repo update
helm install cert-manager jetstack/cert-manager \
  --namespace cert-manager \
  --create-namespace \
  --version v1.9.1 \
  --set installCRDs=true
```

Check that cert-manager is running:

```bash
kubectl get pods -n cert-manager
```

## Verify SHKeeper Service

```bash
kubectl get ns
kubectl get svc -n shkeeper
```

Expected:

- namespace: `shkeeper`
- service: `shkeeper`
- service port: `5000`

Do not expose the SHKeeper web container directly on public port `5000`.
Public traffic must enter through Traefik on `80/tcp` and `443/tcp` only. A
`LoadBalancer`/NodePort service such as `shkeeper-external` bypasses Traefik and
lets internet clients connect directly to gunicorn; malformed, TLS-on-HTTP, or
slow direct clients can occupy every gunicorn `gthread` request thread and make
the admin UI and `/healthz` hang.

## Create HTTPS Config

Create `k3s_cert.yaml` on the VPS.

For dev:

```yaml
---
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: shkeeper-cert
  namespace: shkeeper
spec:
  commonName: dev.core.grither.company
  secretName: shkeeper-cert
  dnsNames:
    - dev.core.grither.company
  issuerRef:
    name: letsencrypt-production
    kind: ClusterIssuer
---
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-production
spec:
  acme:
    email: Grither.company@gmail.com
    server: https://acme-v02.api.letsencrypt.org/directory
    privateKeySecretRef:
      name: your-own-very-secretive-key
    solvers:
      - http01:
          ingress:
            class: traefik
---
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: shkeeper
  namespace: shkeeper
spec:
  entryPoints:
    - web
    - websecure
  routes:
    - match: "Host(`dev.core.grither.company`)"
      kind: Rule
      services:
        - name: shkeeper
          port: 5000
          namespace: shkeeper
  tls:
    secretName: shkeeper-cert
```

For production, replace every `dev.core.grither.company` value with the production hostname.

Example:

```bash
sed -i 's/dev\.core\.grither\.company/core.grither.company/g' k3s_cert.yaml
```

## Apply Config

Validate the YAML first:

```bash
kubectl apply --dry-run=client -f k3s_cert.yaml
```

Expected dry-run output:

```text
certificate.cert-manager.io/shkeeper-cert created (dry run)
clusterissuer.cert-manager.io/letsencrypt-production created (dry run)
ingressroute.traefik.io/shkeeper created (dry run)
```

Apply:

```bash
kubectl apply -f k3s_cert.yaml
```

## Check Certificate

```bash
kubectl get certificate -n shkeeper
kubectl describe certificate shkeeper-cert -n shkeeper
kubectl get orders -A
kubectl get challenges -A
```

Wait until:

```text
READY=True
```

## Check HTTPS

For dev:

```bash
curl -Iv https://dev.core.grither.company
```

A working SHKeeper response may return:

```text
HTTP/2 302
location: /wallets
server: gunicorn
```

For prod:

```bash
curl -Iv https://core.grither.company
```

## Runtime Health Guard

The SHKeeper image includes `/healthz` and gunicorn worker timeouts. Deploy the
new image before enabling probes, because older images do not have `/healthz`.

The SHKeeper web image runs behind Traefik with gunicorn backend keep-alive
disabled (`--keep-alive 0`). Keep this setting for the web container. With a
single `gthread` worker, backend keep-alive sockets can otherwise occupy all
request threads while gunicorn waits for the next HTTP request line; in that
state even `/healthz` hangs although the pod is still `Ready`.

Also make sure no raw SHKeeper service is exposed on the node:

```bash
kubectl get svc -n shkeeper -o wide
curl -v --connect-timeout 5 --max-time 8 http://<public-vps-ip>:5000/healthz
```

The direct `:5000` curl must fail to connect from the internet. If it connects,
remove or disable the public `LoadBalancer`/NodePort service and keep only the
internal `shkeeper` ClusterIP service used by Traefik.

After pushing a new `ghcr.io/nilof470/shkeeper.io:<tag>` image:

```bash
NEW_TAG=REPLACE_WITH_TAG

sed -i "s|image: ghcr.io/nilof470/shkeeper.io:.*|image: ghcr.io/nilof470/shkeeper.io:${NEW_TAG}|" /root/shkeeper-values.yaml
helm upgrade -f /root/shkeeper-values.yaml shkeeper vsys-host/shkeeper
kubectl rollout status deployment/shkeeper-deployment -n shkeeper --timeout=180s
```

Verify the health endpoint:

```bash
kubectl exec -n shkeeper deployment/shkeeper-deployment -- \
  curl -sS -o /dev/null -w "HEALTH=%{http_code} TIME=%{time_total}\n" \
  --connect-timeout 2 -m 5 http://127.0.0.1:5000/healthz
```

Enable readiness and liveness probes:

```bash
kubectl patch deployment shkeeper-deployment -n shkeeper --type=json -p='[
  {"op":"add","path":"/spec/template/spec/containers/0/readinessProbe","value":{"httpGet":{"path":"/healthz","port":5000},"initialDelaySeconds":5,"periodSeconds":10,"timeoutSeconds":2,"failureThreshold":3}},
  {"op":"add","path":"/spec/template/spec/containers/0/livenessProbe","value":{"httpGet":{"path":"/healthz","port":5000},"initialDelaySeconds":30,"periodSeconds":20,"timeoutSeconds":3,"failureThreshold":3}}
]'

kubectl rollout status deployment/shkeeper-deployment -n shkeeper --timeout=180s
kubectl describe deployment shkeeper-deployment -n shkeeper | grep -A8 -E 'Liveness|Readiness'
```

If `/healthz` times out inside the web pod, check whether all gunicorn request
threads are blocked in HTTP request parsing:

```bash
POD=$(kubectl get pod -n shkeeper \
  -l app.kubernetes.io/name=shkeeper,app.kubernetes.io/component=web \
  -o jsonpath='{.items[0].metadata.name}')

kubectl exec -n shkeeper "$POD" -- ps -eLf
kubectl exec -n shkeeper "$POD" -- sh -lc 'pip install -q py-spy'
kubectl exec -n shkeeper "$POD" -- sh -lc 'py-spy dump --pid 8' || true
```

A dump where every `ThreadPoolExecutor-0_*` thread is in
`gunicorn/http/unreader.py` `read()` or `chunk()` means gunicorn has exhausted
its request thread pool on idle or incomplete backend HTTP connections. Deploy
an image that uses `--keep-alive 0`, then verify `/healthz` and enable the
readiness/liveness probes above.

## Troubleshooting YAML Indentation

If Kubernetes returns:

```text
error converting YAML to JSON: yaml: line 2: mapping values are not allowed in this context
```

check for leading spaces:

```bash
sed -n '1,10l' k3s_cert.yaml
```

Top-level lines must not have leading spaces:

```text
apiVersion: cert-manager.io/v1$
kind: Certificate$
metadata:$
  name: shkeeper-cert$
```

If every line has two extra leading spaces, remove them:

```bash
sed -i 's/^  //' k3s_cert.yaml
```

Then run the dry-run check again.

## Security Note

This official SHKeeper setup exposes the whole SHKeeper web service for the configured hostname. That includes the admin UI and API endpoints unless additional firewall, security group, or reverse-proxy rules are added.
