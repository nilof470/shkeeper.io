#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys


DEFAULT_PAYOUT_QUEUE = "tron_usdt_fee_payouts"
PAYOUT_QUEUE_ENV = "TRON_USDT_PAYOUT_QUEUE"


def run_json(command):
    return json.loads(subprocess.check_output(command, text=True))


def run_json_optional(command):
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode == 0:
        return json.loads(result.stdout)
    output = f"{result.stdout}\n{result.stderr}".strip()
    if "NotFound" in output or "not found" in output:
        return None
    fail(output or f"command failed: {' '.join(command)}")


def fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def command_has_queue(command, queue):
    for index, arg in enumerate(command):
        if arg in ("-Q", "--queues") and index + 1 < len(command):
            return command[index + 1] == queue
        if arg in (f"-Q={queue}", f"--queues={queue}"):
            return True
    return False


def command_option_equals(command, option, expected):
    for index, arg in enumerate(command):
        if arg == option and index + 1 < len(command):
            return command[index + 1] == expected
        if arg == f"{option}={expected}":
            return True
    return False


def env_value(container, name):
    for item in container.get("env") or []:
        if item.get("name") == name:
            return item.get("value")
    return None


def container_command(container):
    return (container.get("command") or []) + (container.get("args") or [])


def deployment_containers(deployment):
    return {
        container["name"]: container
        for container in deployment["spec"]["template"]["spec"]["containers"]
    }


def expected_payout_queue(tasks_container, explicit_queue=None):
    return explicit_queue or env_value(tasks_container, PAYOUT_QUEUE_ENV) or DEFAULT_PAYOUT_QUEUE


def selector_string(deployment):
    labels = deployment["spec"]["selector"].get("matchLabels") or {}
    if not labels:
        fail("deployment has no matchLabels selector")
    return ",".join(f"{key}={value}" for key, value in labels.items())


def ready_pods(namespace, selector):
    pods = run_json(
        [
            "kubectl",
            "-n",
            namespace,
            "get",
            "pods",
            "-l",
            selector,
            "-o",
            "json",
        ]
    )
    ready = []
    for pod in pods.get("items", []):
        if pod.get("metadata", {}).get("deletionTimestamp"):
            continue
        statuses = pod.get("status", {}).get("containerStatuses") or []
        if statuses and all(status.get("ready") for status in statuses):
            ready.append(pod)
    return ready


def pod_container_count_matches(pod, expected_count):
    statuses = pod.get("status", {}).get("containerStatuses") or []
    return len(statuses) == expected_count


def verify_api_deployment(deployment, explicit_queue=None):
    containers = deployment_containers(deployment)
    for name in ("app", "tasks", "redis"):
        if name not in containers:
            fail(f"{name} container is missing from tron-shkeeper")

    tasks = containers["tasks"]
    feature_enabled = (
        str(env_value(tasks, "TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED")).lower()
        == "true"
    )
    queue = expected_payout_queue(tasks, explicit_queue=explicit_queue)
    if feature_enabled and not command_has_queue(container_command(tasks), "celery"):
        fail("tasks container must consume only the celery queue when TRON payouts are enabled")
    return containers, queue, feature_enabled


def verify_worker_deployment(deployment, queue):
    containers = deployment_containers(deployment)
    payouts = containers.get("tron-usdt-payouts")
    if payouts is None:
        fail("tron-usdt-payouts container is missing")

    payouts_command = container_command(payouts)
    if not command_has_queue(payouts_command, queue):
        fail(f"tron-usdt-payouts must consume {queue}")
    if not command_option_equals(payouts_command, "--concurrency", "1"):
        fail("tron-usdt-payouts must run with concurrency=1")
    if not command_option_equals(payouts_command, "--prefetch-multiplier", "1"):
        fail("tron-usdt-payouts must run with prefetch multiplier 1")
    redis_host = env_value(payouts, "REDIS_HOST")
    if redis_host != "tron-shkeeper:6379":
        fail("tron-usdt-payouts must use tron-shkeeper:6379 as Redis broker")
    env_queue = env_value(payouts, PAYOUT_QUEUE_ENV)
    if env_queue and env_queue != queue:
        fail(f"tron-usdt-payouts env queue must match {queue}")
    return containers


def parse_args():
    parser = argparse.ArgumentParser(
        description="Verify the TRON API sidecar and dedicated USDT payout worker deployments."
    )
    parser.add_argument("--namespace", default="shkeeper")
    parser.add_argument("--deployment", default="tron-shkeeper")
    parser.add_argument("--worker-deployment", default="tron-usdt-payouts")
    parser.add_argument(
        "--queue",
        default=None,
        help=(
            "Payout queue name. Defaults to the TRON_USDT_PAYOUT_QUEUE env value "
            "from the tasks container, then tron_usdt_fee_payouts."
        ),
    )
    parser.add_argument(
        "--required",
        action="store_true",
        help="Require the worker even if the feature flag is not enabled.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    api_deployment = run_json(
        [
            "kubectl",
            "-n",
            args.namespace,
            "get",
            "deployment",
            args.deployment,
            "-o",
            "json",
        ]
    )
    api_containers, queue, feature_enabled = verify_api_deployment(
        api_deployment,
        explicit_queue=args.queue,
    )

    api_selector = selector_string(api_deployment)
    api_pods = ready_pods(args.namespace, api_selector)
    if not api_pods:
        fail("no ready tron-shkeeper pods found")
    if not any(pod_container_count_matches(pod, len(api_containers)) for pod in api_pods):
        fail("no ready tron-shkeeper pod has the expected container count")

    worker_deployment = run_json_optional(
        [
            "kubectl",
            "-n",
            args.namespace,
            "get",
            "deployment",
            args.worker_deployment,
            "-o",
            "json",
        ]
    )
    if worker_deployment is None:
        if args.required:
            fail(f"{args.worker_deployment} deployment is missing")
        if not feature_enabled:
            print("TRON USDT payout provisioning is disabled; dedicated worker is optional.")
        else:
            print(
                "TRON USDT payout worker deployment is absent; rail is likely "
                "paused, kill-switched, or disabled. Use --required after enabling execution."
            )
        print(f"OK: {args.deployment} API topology verified; {args.worker_deployment} not rendered")
        return

    worker_containers = verify_worker_deployment(worker_deployment, queue)

    worker_selector = selector_string(worker_deployment)
    worker_pods = ready_pods(args.namespace, worker_selector)
    if not worker_pods:
        fail(f"no ready {args.worker_deployment} pods found")
    if not any(pod_container_count_matches(pod, len(worker_containers)) for pod in worker_pods):
        fail(f"no ready {args.worker_deployment} pod has the expected container count")

    print(
        f"OK: {args.deployment} has {len(api_containers)} containers; "
        f"{args.worker_deployment} consumes {queue}"
    )


if __name__ == "__main__":
    main()
