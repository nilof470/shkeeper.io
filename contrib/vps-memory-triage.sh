#!/bin/sh
set -u

NS="${NS:-shkeeper}"
STABILIZE_DEPLOYS="${STABILIZE_DEPLOYS:-bnb-shkeeper ethereum-shkeeper ton-shkeeper}"
APPLY=0
STABILIZE=0

usage() {
    cat <<'EOF'
Usage:
  sh contrib/vps-memory-triage.sh [options]

Options:
  --namespace NAME          Kubernetes namespace to inspect. Default: shkeeper
  --stabilize-small-vps    Print scale-down commands for high-cost optional sidecars
  --apply                  Execute stabilization commands. Without this, stabilization is dry-run
  --help                   Show this help

Environment:
  NS                       Alternative way to set namespace
  STABILIZE_DEPLOYS        Space-separated deployments to scale to 0 in apply mode.
                           Default: bnb-shkeeper ethereum-shkeeper ton-shkeeper

The default mode is read-only. Stabilization stays in dry-run mode unless
--apply is passed. Scaling down a sidecar stops processing for that network.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --namespace)
            if [ "$#" -lt 2 ]; then
                echo "ERROR: --namespace requires a value" >&2
                exit 2
            fi
            NS="$2"
            shift 2
            ;;
        --stabilize-small-vps)
            STABILIZE=1
            shift
            ;;
        --apply)
            APPLY=1
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

have() {
    command -v "$1" >/dev/null 2>&1
}

section() {
    printf '\n## %s\n' "$1"
}

run() {
    printf '\n$'
    printf ' %s' "$@"
    printf '\n'
    status=0
    "$@" 2>&1 || status=$?
    if [ "$status" -ne 0 ]; then
        printf 'WARN: command failed:'
        printf ' %s' "$@"
        printf ' (exit %s)\n' "$status"
    fi
}

run_sh() {
    printf '\n$ %s\n' "$1"
    sh -c "$1" 2>&1 || printf 'WARN: command failed: %s\n' "$1"
}

system_snapshot() {
    section "Host snapshot"
    run_sh 'date -Is 2>/dev/null || date'
    run uname -a
    run uptime

    if have free; then
        run free -h
    else
        echo "WARN: free is not installed"
    fi

    if have swapon; then
        run swapon --show
    fi

    run df -h
    run df -i

    section "Top host memory consumers"
    if ps -eo pid,ppid,user,%mem,%cpu,rss,vsz,comm,args --sort=-rss >/dev/null 2>&1; then
        run_sh 'ps -eo pid,ppid,user,%mem,%cpu,rss,vsz,comm,args --sort=-rss | head -30'
    elif have top; then
        run_sh 'top -b -n 1 | head -60'
    else
        echo "WARN: neither ps summary nor top is available"
    fi

    section "Kernel and k3s pressure signals"
    if have dmesg; then
        run_sh "dmesg -T 2>/dev/null | grep -Ei 'oom|out of memory|killed process|memory cgroup|evict|no space|i/o error' | tail -80"
    fi
    if have journalctl; then
        run_sh "journalctl -b --no-pager 2>/dev/null | grep -Ei 'oom|out of memory|killed process|memory cgroup|evict|no space|i/o error' | tail -80"
        run_sh "journalctl -b -u k3s --no-pager 2>/dev/null | grep -Ei 'slow sql|deadline exceeded|handler timeout|apiserver|kine|lease|heartbeat|oom|memory|evict' | tail -120"
    fi

    if have iostat; then
        section "Current disk pressure"
        run iostat -xz 1 3
    else
        echo "WARN: iostat is not installed. On Ubuntu: apt-get install -y sysstat"
    fi
}

kubernetes_snapshot() {
    section "Kubernetes snapshot"
    if ! have kubectl; then
        echo "WARN: kubectl is not installed; skipping Kubernetes checks"
        return
    fi

    run kubectl version --client=true
    run kubectl get nodes -o wide
    run kubectl top nodes
    run kubectl top pods -A --sort-by=memory
    run kubectl get pods -n "$NS" -o wide
    run kubectl get deploy -n "$NS"
    run kubectl get pvc -n "$NS"

    section "Pod restarts and recent pressure events"
    run_sh "kubectl get pods -n '$NS' -o jsonpath='{range .items[*]}{.metadata.name}{\"\\t\"}{range .status.containerStatuses[*]}{.name}{\":restarts=\"}{.restartCount}{\",last=\"}{.lastState.terminated.reason}{\",exit=\"}{.lastState.terminated.exitCode}{\" \"}{end}{\"\\n\"}{end}' 2>/dev/null"
    run_sh "kubectl get pods -A | grep -Ei 'oomkilled|evicted|crashloop|error|pending' || true"
    run_sh "kubectl get events -n '$NS' --sort-by=.lastTimestamp 2>/dev/null | tail -80"

    section "Configured memory requests and limits"
    run kubectl get pods -n "$NS" -o custom-columns='POD:.metadata.name,CONTAINERS:.spec.containers[*].name,MEM_REQUESTS:.spec.containers[*].resources.requests.memory,MEM_LIMITS:.spec.containers[*].resources.limits.memory'

    section "Known high-cost SHKeeper sidecars"
    for deploy in $STABILIZE_DEPLOYS; do
        run_sh "kubectl get deploy -n '$NS' '$deploy' -o jsonpath='{.metadata.name} replicas={.spec.replicas} ready={.status.readyReplicas} images={.spec.template.spec.containers[*].image}{\"\\n\"}' 2>/dev/null || echo '$deploy not found'"
    done
}

stabilize_small_vps() {
    section "Small VPS stabilization"
    echo "Target namespace: $NS"
    echo "Target deployments: $STABILIZE_DEPLOYS"
    echo "Impact: each scaled deployment stops deposits, sweeps, and payouts for that network."

    if [ "$APPLY" -ne 1 ]; then
        echo "dry-run: no changes made. Re-run with --apply to execute:"
        if have journalctl; then
            echo "  journalctl --vacuum-size=200M"
        fi
        for deploy in $STABILIZE_DEPLOYS; do
            echo "  kubectl -n $NS scale deploy $deploy --replicas=0"
        done
        return
    fi

    if ! have kubectl; then
        echo "ERROR: kubectl is required for stabilization" >&2
        exit 1
    fi

    if have journalctl; then
        run journalctl --vacuum-size=200M
    fi

    for deploy in $STABILIZE_DEPLOYS; do
        if kubectl get deploy -n "$NS" "$deploy" >/dev/null 2>&1; then
            run kubectl -n "$NS" scale deploy "$deploy" --replicas=0
        else
            echo "$deploy not found; skipping"
        fi
    done

    run kubectl top nodes
    run kubectl top pods -A --sort-by=memory
}

system_snapshot
kubernetes_snapshot

section "Interpretation checklist"
cat <<'EOF'
1. If dmesg/journal shows OOMKilled or memory cgroup kills, identify the pod or
   process name and compare it with "kubectl top pods -A --sort-by=memory".
2. If k3s logs show Slow SQL, handler timeouts, missed heartbeats, or lease
   failures while iostat shows high await/%util, the VPS is I/O/CPU saturated
   even if free memory looks acceptable.
3. On small VPS hosts, first disable networks that are not production-critical.
   The usual high-cost SHKeeper sidecars are BNB, Ethereum, and TON.
4. Make any emergency scale-down persistent in /root/shkeeper-values.yaml before
   the next helm upgrade, otherwise Helm may recreate the deployments.
EOF

if [ "$STABILIZE" -eq 1 ]; then
    stabilize_small_vps
fi
