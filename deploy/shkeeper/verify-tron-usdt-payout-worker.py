#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys


DEFAULT_PAYOUT_QUEUE = "tron_usdt_fee_payouts"
PAYOUT_QUEUE_ENV = "TRON_USDT_PAYOUT_QUEUE"


def run_json(command):
    return json.loads(subprocess.check_output(command, text=True))


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


def parse_args():
    parser = argparse.ArgumentParser(
        description="Verify tron-shkeeper dedicated USDT payout worker deployment."
    )
    parser.add_argument("--namespace", default="shkeeper")
    parser.add_argument("--deployment", default="tron-shkeeper")
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
    deployment = run_json(
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
    containers = {
        container["name"]: container
        for container in deployment["spec"]["template"]["spec"]["containers"]
    }
    tasks = containers.get("tasks")
    if tasks is None:
        fail("tasks container is missing")

    feature_enabled = (
        str(env_value(tasks, "TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED")).lower()
        == "true"
    )
    if not args.required and not feature_enabled:
        print("TRON USDT payout provisioning is disabled; dedicated worker is optional.")
        return

    payouts = containers.get("tron-usdt-payouts")
    if payouts is None:
        fail("tron-usdt-payouts container is missing")

    tasks_command = container_command(tasks)
    payouts_command = container_command(payouts)
    queue = expected_payout_queue(tasks, explicit_queue=args.queue)
    if not command_has_queue(tasks_command, "celery"):
        fail("tasks container must consume only the celery queue")
    if not command_has_queue(payouts_command, queue):
        fail(f"tron-usdt-payouts must consume {queue}")
    if not command_option_equals(payouts_command, "--concurrency", "1"):
        fail("tron-usdt-payouts must run with concurrency=1")
    if not command_option_equals(payouts_command, "--prefetch-multiplier", "1"):
        fail("tron-usdt-payouts must run with prefetch multiplier 1")

    selector = selector_string(deployment)
    pods = ready_pods(args.namespace, selector)
    if not pods:
        fail("no ready tron-shkeeper pods found")
    if not any(pod_container_count_matches(pod, len(containers)) for pod in pods):
        fail("no ready tron-shkeeper pod has the expected container count")

    print(
        f"OK: {args.deployment} has {len(containers)} containers and "
        f"tron-usdt-payouts consumes {queue}"
    )


if __name__ == "__main__":
    main()
