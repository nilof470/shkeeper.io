#!/usr/bin/env python3
"""Probe re:Fee rent_resource order lifecycle without leaking the API key.

This script is intentionally stdlib-only so the spike can run on an operator machine
without installing dependencies. It writes a JSON report with public order metadata,
status transitions, and optional TRON resource snapshots.
"""

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_REFEE_BASE_URL = "https://api.refee.bot/v2"
DEFAULT_TRON_FULLNODE_URL = "https://api.trongrid.io"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
SUCCESS_STATUSES = {"delegated", "completed"}
FAILURE_STATUSES = {"failed", "insufficient_funds", "canceled"}
RATE_LIMIT_HEADERS = {
    "retry-after",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
    "ratelimit-limit",
    "ratelimit-remaining",
    "ratelimit-reset",
}


class ProbeAbort(Exception):
    """Raised for expected probe stops that should still write a report."""

    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return int(value)


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return float(value)


def api_url(base_url: str, path: str) -> str:
    return urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def parse_json(text: str) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_raw": text}


def normalize_headers(headers: Iterable[Tuple[str, str]]) -> Dict[str, str]:
    return {key.lower(): value for key, value in headers}


def rate_limit_headers(headers: Dict[str, str]) -> Dict[str, str]:
    return {key: value for key, value in headers.items() if key.lower() in RATE_LIMIT_HEADERS}


def request_json(
    method: str,
    url: str,
    api_key: Optional[str] = None,
    body: Optional[Dict[str, Any]] = None,
    timeout: float = 20.0,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    headers = {"accept": "application/json", "user-agent": DEFAULT_USER_AGENT}
    if api_key:
        headers["x-api-key"] = api_key
    if body is not None:
        headers["content-type"] = "application/json"
    if extra_headers:
        headers.update(extra_headers)

    encoded_body = None
    if body is not None:
        encoded_body = json.dumps(body, separators=(",", ":")).encode("utf-8")

    req = urllib.request.Request(url=url, method=method, data=encoded_body, headers=headers)
    started = time.monotonic()

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
            response_headers = normalize_headers(response.headers.items())
            return {
                "ok": True,
                "status_code": response.status,
                "headers": response_headers,
                "rate_limit_headers": rate_limit_headers(response_headers),
                "payload": parse_json(text),
                "raw_text": text,
                "elapsed_sec": round(time.monotonic() - started, 3),
            }
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        response_headers = normalize_headers(exc.headers.items())
        return {
            "ok": False,
            "status_code": exc.code,
            "headers": response_headers,
            "rate_limit_headers": rate_limit_headers(response_headers),
            "payload": parse_json(text),
            "raw_text": text,
            "elapsed_sec": round(time.monotonic() - started, 3),
        }
    except urllib.error.URLError as exc:
        return {
            "ok": False,
            "status_code": 0,
            "headers": {},
            "rate_limit_headers": {},
            "payload": {"error": str(exc)},
            "raw_text": "",
            "elapsed_sec": round(time.monotonic() - started, 3),
        }


def public_response(result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status_code": result["status_code"],
        "ok": result["ok"],
        "rate_limit_headers": result["rate_limit_headers"],
        "payload": result["payload"],
        "elapsed_sec": result["elapsed_sec"],
    }


def add_event(report: Dict[str, Any], name: str, **fields: Any) -> None:
    report["events"].append({"at": utc_now(), "event": name, **fields})


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def energy_snapshot(result: Dict[str, Any]) -> Dict[str, Any]:
    payload = result.get("payload")
    if not isinstance(payload, dict):
        return {"ok": False, "status_code": result["status_code"], "error": payload}
    limit = as_int(payload.get("EnergyLimit"))
    used = as_int(payload.get("EnergyUsed"))
    return {
        "ok": result["status_code"] == 200,
        "status_code": result["status_code"],
        "energy_limit": limit,
        "energy_used": used,
        "energy_available": max(limit - used, 0),
        "raw": payload,
    }


def tron_resource_request(args: argparse.Namespace, address: str) -> Dict[str, Any]:
    headers = {}
    if args.trongrid_api_key:
        headers["TRON-PRO-API-KEY"] = args.trongrid_api_key
    return request_json(
        "POST",
        api_url(args.tron_fullnode_url, "/wallet/getaccountresource"),
        body={"address": address, "visible": True},
        timeout=args.http_timeout_sec,
        extra_headers=headers,
    )


def find_tariff(tariffs: Any, duration_label: str) -> Optional[Dict[str, Any]]:
    if not isinstance(tariffs, list):
        return None
    for item in tariffs:
        if isinstance(item, dict) and item.get("duration_label") == duration_label:
            return item
    return None


def estimate_cost_sun(tariff: Optional[Dict[str, Any]], amount: int) -> Optional[int]:
    if not tariff:
        return None
    price = tariff.get("energy_price_sun")
    if price is None:
        return None
    return int(float(price) * amount)


def write_report(report: Dict[str, Any], output: Optional[str]) -> Path:
    if output:
        path = Path(output)
    else:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = Path(__file__).resolve().parent / "artifacts" / ("refee-rent-lifecycle-" + stamp + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def validate_inputs(args: argparse.Namespace) -> None:
    if not args.api_key:
        raise ProbeAbort("REFEE_API_KEY is required", 2)
    if not args.address:
        raise ProbeAbort("REFEE_TEST_TRON_ADDRESS is required", 2)
    if len(args.address) != 34 or not args.address.startswith("T"):
        raise ProbeAbort("REFEE_TEST_TRON_ADDRESS must be a 34-character mainnet TRON address", 2)
    if args.amount <= 0:
        raise ProbeAbort("REFEE_RENT_AMOUNT must be positive", 2)
    if args.poll_interval_sec <= 0:
        raise ProbeAbort("REFEE_RENT_POLL_INTERVAL_SEC must be positive", 2)
    if args.timeout_sec <= 0:
        raise ProbeAbort("REFEE_RENT_TIMEOUT_SEC must be positive", 2)


def poll_order_until(
    args: argparse.Namespace,
    report: Dict[str, Any],
    order_id: str,
    success_statuses: Iterable[str],
    timeout_sec: float,
) -> Tuple[str, Dict[str, Any], float]:
    success = set(success_statuses)
    started = time.monotonic()
    latest_payload: Dict[str, Any] = {}

    while True:
        elapsed = time.monotonic() - started
        if elapsed > timeout_sec:
            return "timeout", latest_payload, round(elapsed, 3)

        result = request_json(
            "GET",
            api_url(args.base_url, "/api/rent_resource/orders/" + order_id),
            api_key=args.api_key,
            timeout=args.http_timeout_sec,
        )
        add_event(report, "order_poll", response=public_response(result))

        payload = result.get("payload")
        if isinstance(payload, dict):
            latest_payload = payload
            status = str(payload.get("status", ""))
            if status in success:
                return status, latest_payload, round(time.monotonic() - started, 3)
            if status in FAILURE_STATUSES:
                return status, latest_payload, round(time.monotonic() - started, 3)
        elif result["status_code"] in {401, 403, 404, 422}:
            return "poll_error", {"response": public_response(result)}, round(time.monotonic() - started, 3)

        time.sleep(args.poll_interval_sec)


def wait_for_chain_energy(
    args: argparse.Namespace,
    report: Dict[str, Any],
    before: Dict[str, Any],
) -> Tuple[bool, Dict[str, Any], float]:
    started = time.monotonic()
    minimum_delta = int(args.amount * args.minimum_energy_delta_ratio)

    while True:
        result = tron_resource_request(args, args.address)
        snapshot = energy_snapshot(result)
        delta = snapshot.get("energy_available", 0) - before.get("energy_available", 0)
        add_event(
            report,
            "chain_resource_after",
            response=public_response(result),
            snapshot=snapshot,
            energy_available_delta=delta,
            minimum_expected_delta=minimum_delta,
        )
        if snapshot.get("ok") and delta >= minimum_delta:
            return True, snapshot, round(time.monotonic() - started, 3)
        if time.monotonic() - started > args.chain_verify_timeout_sec:
            return False, snapshot, round(time.monotonic() - started, 3)
        time.sleep(min(args.poll_interval_sec, 5.0))


def run_probe(args: argparse.Namespace) -> int:
    report: Dict[str, Any] = {
        "run_started_at": utc_now(),
        "config": {
            "base_url": args.base_url,
            "address": args.address,
            "amount": args.amount,
            "duration_label": args.duration_label,
            "resource": "energy",
            "poll_interval_sec": args.poll_interval_sec,
            "timeout_sec": args.timeout_sec,
            "skip_chain_check": args.skip_chain_check,
            "api_key_present": bool(args.api_key),
        },
        "events": [],
        "result": {},
    }

    try:
        validate_inputs(args)

        profile = request_json(
            "GET",
            api_url(args.base_url, "/api/users/me"),
            api_key=args.api_key,
            timeout=args.http_timeout_sec,
        )
        add_event(report, "profile", response=public_response(profile))
        if profile["status_code"] != 200 or not isinstance(profile.get("payload"), dict):
            raise ProbeAbort("Unable to read re:Fee profile", 3)

        balance_sun = as_int(profile["payload"].get("balance_sun"))
        tariffs = request_json(
            "GET",
            api_url(args.base_url, "/api/rent_resource/tariffs"),
            api_key=args.api_key,
            timeout=args.http_timeout_sec,
        )
        add_event(report, "tariffs", response=public_response(tariffs))
        if tariffs["status_code"] != 200:
            raise ProbeAbort("Unable to read re:Fee rent_resource tariffs", 3)

        tariff = find_tariff(tariffs.get("payload"), args.duration_label)
        cost_sun = estimate_cost_sun(tariff, args.amount)
        report["cost_estimate"] = {
            "balance_sun": balance_sun,
            "duration_label": args.duration_label,
            "energy_price_sun": None if not tariff else tariff.get("energy_price_sun"),
            "estimated_cost_sun": cost_sun,
        }
        if cost_sun is not None and balance_sun < cost_sun and not args.allow_insufficient_balance_probe:
            raise ProbeAbort(
                "Balance is below estimated cost; refusing to create order without --allow-insufficient-balance-probe",
                2,
            )

        before_snapshot = {"ok": False, "energy_available": 0}
        if not args.skip_chain_check:
            chain_before = tron_resource_request(args, args.address)
            before_snapshot = energy_snapshot(chain_before)
            add_event(report, "chain_resource_before", response=public_response(chain_before), snapshot=before_snapshot)

        order_body = {
            "address": args.address,
            "amount": args.amount,
            "resource": "energy",
            "duration_label": args.duration_label,
        }
        create_started = time.monotonic()
        created = request_json(
            "POST",
            api_url(args.base_url, "/api/rent_resource/orders"),
            api_key=args.api_key,
            body=order_body,
            timeout=args.http_timeout_sec,
        )
        add_event(report, "order_create", request_body=order_body, response=public_response(created))
        if created["status_code"] != 202 or not isinstance(created.get("payload"), dict):
            report["result"] = {"verdict": "ORDER_REJECTED", "response": public_response(created)}
            raise ProbeAbort("Order creation did not return HTTP 202", 3)

        order = created["payload"]
        order_id = order.get("id")
        if not order_id:
            report["result"] = {"verdict": "INVALID_RESPONSE", "response": public_response(created)}
            raise ProbeAbort("Created order response has no id field", 3)

        status = str(order.get("status", ""))
        if status not in SUCCESS_STATUSES:
            status, latest_order, poll_latency = poll_order_until(
                args,
                report,
                str(order_id),
                SUCCESS_STATUSES,
                args.timeout_sec,
            )
        else:
            latest_order = order
            poll_latency = round(time.monotonic() - create_started, 3)

        delegated_latency = round(time.monotonic() - create_started, 3)
        if status not in SUCCESS_STATUSES:
            report["result"] = {
                "verdict": "NOT_DELEGATED",
                "status": status,
                "delegation_latency_sec": delegated_latency,
                "latest_order": latest_order,
            }
            raise ProbeAbort("Order did not reach delegated/completed", 4)

        chain_verified = None
        after_snapshot = None
        chain_latency = None
        if not args.skip_chain_check and before_snapshot.get("ok"):
            chain_verified, after_snapshot, chain_latency = wait_for_chain_energy(args, report, before_snapshot)
            if not chain_verified:
                report["result"] = {
                    "verdict": "CHAIN_NOT_VERIFIED",
                    "status": status,
                    "delegation_latency_sec": delegated_latency,
                    "chain_verify_latency_sec": chain_latency,
                    "latest_order": latest_order,
                    "chain_after": after_snapshot,
                }
                raise ProbeAbort("Order delegated but on-chain energy delta was not verified", 4)

        completion_status = None
        completion_order = None
        completion_latency = None
        if args.wait_completion:
            completion_status, completion_order, completion_latency = poll_order_until(
                args,
                report,
                str(order_id),
                {"completed"},
                args.completion_timeout_sec,
            )

        report["result"] = {
            "verdict": "DELEGATED",
            "order_id": order_id,
            "status": status,
            "delegation_latency_sec": delegated_latency,
            "poll_latency_sec": poll_latency,
            "chain_verified": chain_verified,
            "chain_verify_latency_sec": chain_latency,
            "chain_after": after_snapshot,
            "latest_order": latest_order,
            "completion_status": completion_status,
            "completion_latency_sec": completion_latency,
            "completion_order": completion_order,
        }
        return 0
    except ProbeAbort as exc:
        report.setdefault("result", {})
        report["result"].setdefault("verdict", "ABORTED")
        report["result"]["message"] = str(exc)
        return exc.exit_code
    finally:
        report["run_finished_at"] = utc_now()
        path = write_report(report, args.output)
        result = report.get("result", {})
        print("report:", path)
        print("verdict:", result.get("verdict", "UNKNOWN"))
        if result.get("message"):
            print("message:", result["message"])
        if result.get("delegation_latency_sec") is not None:
            print("delegation_latency_sec:", result["delegation_latency_sec"])


def self_test() -> int:
    assert api_url("https://api.refee.bot/v2", "/api/users/me") == "https://api.refee.bot/v2/api/users/me"
    assert find_tariff([{"duration_label": "1h", "energy_price_sun": 37}], "1h") is not None
    assert estimate_cost_sun({"energy_price_sun": 37}, 65000) == 2405000
    assert rate_limit_headers({"x-ratelimit-limit": "10", "server": "nginx"}) == {"x-ratelimit-limit": "10"}
    ok = energy_snapshot({"status_code": 200, "payload": {"EnergyLimit": 65000, "EnergyUsed": 1000}})
    assert ok["energy_available"] == 64000
    print("self-test: OK")
    return 0


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe re:Fee rent_resource order lifecycle")
    parser.add_argument("--self-test", action="store_true", help="Run offline sanity checks and exit")
    parser.add_argument("--base-url", default=os.getenv("REFEE_API_BASE_URL", DEFAULT_REFEE_BASE_URL))
    parser.add_argument("--api-key", default=os.getenv("REFEE_API_KEY"))
    parser.add_argument("--address", default=os.getenv("REFEE_TEST_TRON_ADDRESS"))
    parser.add_argument("--amount", type=int, default=env_int("REFEE_RENT_AMOUNT", 65000))
    parser.add_argument("--duration-label", default=os.getenv("REFEE_RENT_DURATION_LABEL", "1h"))
    parser.add_argument("--poll-interval-sec", type=float, default=env_float("REFEE_RENT_POLL_INTERVAL_SEC", 2.0))
    parser.add_argument("--timeout-sec", type=float, default=env_float("REFEE_RENT_TIMEOUT_SEC", 90.0))
    parser.add_argument("--http-timeout-sec", type=float, default=env_float("REFEE_HTTP_TIMEOUT_SEC", 20.0))
    parser.add_argument("--chain-verify-timeout-sec", type=float, default=env_float("TRON_CHAIN_VERIFY_TIMEOUT_SEC", 30.0))
    parser.add_argument("--completion-timeout-sec", type=float, default=env_float("REFEE_COMPLETION_TIMEOUT_SEC", 4200.0))
    parser.add_argument("--minimum-energy-delta-ratio", type=float, default=env_float("TRON_MINIMUM_ENERGY_DELTA_RATIO", 0.95))
    parser.add_argument("--tron-fullnode-url", default=os.getenv("TRON_FULLNODE_URL", DEFAULT_TRON_FULLNODE_URL))
    parser.add_argument("--trongrid-api-key", default=os.getenv("TRONGRID_API_KEY"))
    parser.add_argument("--skip-chain-check", action="store_true", default=env_bool("SKIP_CHAIN_CHECK", False))
    parser.add_argument("--allow-insufficient-balance-probe", action="store_true")
    parser.add_argument("--wait-completion", action="store_true", help="Poll until status=completed after delegation")
    parser.add_argument("--output", help="Write JSON report to this path instead of artifacts/")
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    if args.self_test:
        return self_test()
    return run_probe(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
