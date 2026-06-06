#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from pathlib import Path


DEFAULT_CONSUMER = "grither-pay"
DEFAULT_SIGNING_SECRET_NAME = "grither-prod-shkeeper-payout-sidecar-signing-keys"
DEFAULT_SIGNING_SECRET_KEY = "PAYOUT_SIDECAR_KEYS_JSON"
DEFAULT_SIDECAR_CONSUMER_SECRET_NAME = "grither-prod-sidecar-payout-consumer-keys"
DEFAULT_SIDECAR_CONSUMER_SECRET_KEY = "PAYOUT_CONSUMER_KEYS_JSON"
TRON_RAILS = {"TRON-USDT"}


class SecretContractError(RuntimeError):
    pass


class KubectlError(RuntimeError):
    pass


def load_json_file(path):
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError as exc:
        raise SecretContractError(f"JSON file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SecretContractError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise SecretContractError(f"{path} must contain a JSON object")
    return value


def write_secret_json(path, payload):
    output_path = Path(path)
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(output_path, flags, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(encoded)


def normalize_rail(rail):
    if not isinstance(rail, str) or not rail.strip():
        raise SecretContractError("rail names must be non-empty strings")
    return rail.strip().upper()


def parse_required_rails(required_rails, repeated_required_rails):
    rails = []
    for raw in repeated_required_rails or []:
        rails.append(normalize_rail(raw))
    if required_rails:
        for raw in required_rails.split(","):
            if raw.strip():
                rails.append(normalize_rail(raw))
    return unique_preserving_order(rails)


def unique_preserving_order(values):
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def consumer_config(mapping, consumer):
    config = mapping.get(consumer)
    if not isinstance(config, dict):
        raise SecretContractError(f"{consumer}: missing payout secret configuration")
    return config


def signing_key_entries(signing_keys, consumer=DEFAULT_CONSUMER):
    config = consumer_config(signing_keys, consumer)
    entries = []
    for key_id, key_config in sorted(config.items()):
        if not isinstance(key_config, dict):
            raise SecretContractError(
                f"{consumer}:{key_id}: signing key must be an object with secret and rails"
            )
        secret = key_config.get("secret")
        if not isinstance(secret, str) or not secret:
            raise SecretContractError(f"{consumer}:{key_id}: signing secret is empty")
        raw_rails = key_config.get("rails") or key_config.get("allowed_rails")
        if not isinstance(raw_rails, list) or not raw_rails:
            raise SecretContractError(f"{consumer}:{key_id}: signing rails are empty")
        rails = unique_preserving_order([normalize_rail(rail) for rail in raw_rails])
        entries.append({"key_id": key_id, "secret": secret, "rails": rails})
    if not entries:
        raise SecretContractError(f"{consumer}: no payout sidecar signing keys configured")
    return entries


def signing_rails(entries):
    rails = []
    for entry in entries:
        rails.extend(entry["rails"])
    return unique_preserving_order(rails)


def top_level_auth_rails(rails):
    return [rail for rail in rails if rail not in TRON_RAILS]


def top_level_key_entries(entries, rails):
    required_rails = set(top_level_auth_rails(rails))
    if not required_rails:
        return []
    return [
        entry
        for entry in entries
        if required_rails.intersection(set(entry["rails"]))
    ]


def build_sidecar_consumer_keys(signing_keys, consumer=DEFAULT_CONSUMER):
    entries = signing_key_entries(signing_keys, consumer)
    rails = signing_rails(entries)
    consumer_payload = {
        "rails": rails,
    }
    key_entries = top_level_key_entries(entries, rails)
    if key_entries:
        consumer_payload["keys"] = {
            entry["key_id"]: entry["secret"] for entry in key_entries
        }
    for entry in entries:
        consumer_payload[entry["key_id"]] = {
            "secret": entry["secret"],
            "rails": entry["rails"],
        }
    return {consumer: consumer_payload}


def validate_sidecar_secret_contract(
    signing_keys,
    sidecar_consumer_keys,
    *,
    consumer=DEFAULT_CONSUMER,
    required_rails=None,
):
    entries = signing_key_entries(signing_keys, consumer)
    rails_to_check = unique_preserving_order(required_rails or signing_rails(entries))
    if not rails_to_check:
        raise SecretContractError("no payout rails selected for validation")

    consumer_keys = consumer_config(sidecar_consumer_keys, consumer)
    rails_requiring_top_level_auth = top_level_auth_rails(rails_to_check)
    keys_map = None
    if rails_requiring_top_level_auth:
        keys_map = consumer_keys.get("keys")
        if not isinstance(keys_map, dict) or not keys_map:
            raise SecretContractError(
                f"{consumer}: sidecar PAYOUT_CONSUMER_KEYS_JSON must include a "
                "non-empty 'keys' map for ETH/TON sidecars; do not apply "
                "PAYOUT_SIDECAR_KEYS_JSON as the sidecar consumer secret"
            )

        consumer_rails = consumer_keys.get("rails")
        if not isinstance(consumer_rails, list) or not consumer_rails:
            raise SecretContractError(
                f"{consumer}: sidecar PAYOUT_CONSUMER_KEYS_JSON must include "
                "top-level rails"
            )
        allowed_consumer_rails = {normalize_rail(rail) for rail in consumer_rails}
        configured_signing_rails = set(signing_rails(entries))
        extra_consumer_rails = allowed_consumer_rails - configured_signing_rails
        if extra_consumer_rails:
            raise SecretContractError(
                f"{consumer}: sidecar consumer rails include rails with no SHKeeper "
                f"signing key: {sorted(extra_consumer_rails)}"
            )
        missing_consumer_rails = set(rails_requiring_top_level_auth) - allowed_consumer_rails
        if missing_consumer_rails:
            raise SecretContractError(
                f"{consumer}: sidecar consumer secret does not allow selected "
                f"ETH/TON rails {sorted(missing_consumer_rails)}"
            )

        configured_signing_key_ids = {entry["key_id"] for entry in entries}
        unknown_key_ids = set(keys_map) - configured_signing_key_ids
        if unknown_key_ids:
            raise SecretContractError(
                "PAYOUT_AUTH_UNKNOWN_KEY: "
                f"{consumer}: sidecar consumer key ids are not configured as "
                f"SHKeeper signing keys: {sorted(unknown_key_ids)}"
            )

        for rail in rails_requiring_top_level_auth:
            rail_entries = [entry for entry in entries if rail in entry["rails"]]
            if not rail_entries:
                raise SecretContractError(
                    f"{consumer}:{rail}: SHKeeper has no signing key for this rail"
                )
            for entry in rail_entries:
                key_id = entry["key_id"]
                if key_id not in keys_map:
                    raise SecretContractError(
                        f"PAYOUT_AUTH_UNKNOWN_KEY: sidecar consumer secret does not "
                        f"allow SHKeeper payout auth key {key_id!r} for "
                        f"{consumer}:{rail}"
                    )
                if keys_map[key_id] != entry["secret"]:
                    raise SecretContractError(
                        f"{consumer}:{rail}:{key_id}: sidecar consumer key secret "
                        "does not match SHKeeper signing secret"
                    )

    for rail in rails_to_check:
        rail_entries = [entry for entry in entries if rail in entry["rails"]]
        if not rail_entries:
            raise SecretContractError(
                f"{consumer}:{rail}: SHKeeper has no signing key for this rail"
            )

        for entry in rail_entries:
            key_id = entry["key_id"]
            if rail in TRON_RAILS:
                validate_legacy_tron_key(consumer_keys, consumer, rail, entry)

    return {
        "consumer": consumer,
        "key_ids": sorted({entry["key_id"] for entry in entries}),
        "rails": rails_to_check,
    }


def validate_legacy_tron_key(consumer_keys, consumer, rail, entry):
    key_id = entry["key_id"]
    legacy_config = consumer_keys.get(key_id)
    if not isinstance(legacy_config, dict):
        raise SecretContractError(
            f"{consumer}:{rail}:{key_id}: TRON sidecar requires legacy nested key "
            "configuration in PAYOUT_CONSUMER_KEYS_JSON"
        )
    if legacy_config.get("secret") != entry["secret"]:
        raise SecretContractError(
            f"{consumer}:{rail}:{key_id}: TRON legacy key secret does not match "
            "SHKeeper signing secret"
        )
    legacy_rails = legacy_config.get("rails") or legacy_config.get("allowed_rails")
    if not isinstance(legacy_rails, list) or rail not in {
        normalize_rail(item) for item in legacy_rails
    }:
        raise SecretContractError(
            f"{consumer}:{rail}:{key_id}: TRON legacy key does not allow this rail"
        )


def load_kubernetes_secret_json(namespace, secret_name, data_key):
    command = ["kubectl", "-n", namespace, "get", "secret", secret_name, "-o", "json"]
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise KubectlError(
            f"kubectl could not read secret {namespace}/{secret_name}: {detail}"
        )
    try:
        secret = json.loads(result.stdout)
        encoded = secret["data"][data_key]
        decoded = base64.b64decode(encoded).decode("utf-8")
        payload = json.loads(decoded)
    except KeyError as exc:
        raise SecretContractError(
            f"{namespace}/{secret_name}: missing secret data key {data_key}"
        ) from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise SecretContractError(
            f"{namespace}/{secret_name}:{data_key}: secret data is not valid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise SecretContractError(
            f"{namespace}/{secret_name}:{data_key}: secret data must be a JSON object"
        )
    return payload


def print_validation_summary(summary, location):
    key_ids = ", ".join(summary["key_ids"])
    rails = ", ".join(summary["rails"])
    print(
        "OK: payout sidecar secret contract verified "
        f"for {summary['consumer']} at {location}; key_ids={key_ids}; rails={rails}"
    )


def render_sidecar_consumer(args):
    signing_keys = load_json_file(args.signing_keys_file)
    consumer_keys = build_sidecar_consumer_keys(signing_keys, consumer=args.consumer)
    validate_sidecar_secret_contract(
        signing_keys,
        consumer_keys,
        consumer=args.consumer,
        required_rails=parse_required_rails(args.required_rails, args.required_rail),
    )
    write_secret_json(args.output, consumer_keys)
    print(f"OK: wrote sidecar consumer key JSON to {args.output}")


def verify_files(args):
    signing_keys = load_json_file(args.signing_keys_file)
    sidecar_consumer_keys = load_json_file(args.sidecar_consumer_keys_file)
    summary = validate_sidecar_secret_contract(
        signing_keys,
        sidecar_consumer_keys,
        consumer=args.consumer,
        required_rails=parse_required_rails(args.required_rails, args.required_rail),
    )
    print_validation_summary(summary, "local files")


def verify_cluster(args):
    signing_keys = load_kubernetes_secret_json(
        args.namespace,
        args.signing_secret_name,
        args.signing_secret_key,
    )
    sidecar_consumer_keys = load_kubernetes_secret_json(
        args.namespace,
        args.sidecar_consumer_secret_name,
        args.sidecar_consumer_secret_key,
    )
    summary = validate_sidecar_secret_contract(
        signing_keys,
        sidecar_consumer_keys,
        consumer=args.consumer,
        required_rails=parse_required_rails(args.required_rails, args.required_rail),
    )
    print_validation_summary(summary, f"kubernetes namespace {args.namespace}")


def add_common_validation_args(parser):
    parser.add_argument("--consumer", default=DEFAULT_CONSUMER)
    parser.add_argument(
        "--required-rail",
        action="append",
        default=[],
        help="Rail to validate, for example ETH-USDT. May be repeated.",
    )
    parser.add_argument(
        "--required-rails",
        default="",
        help="Comma-separated rails to validate. Defaults to rails in signing keys.",
    )


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Generate and verify SHKeeper payout sidecar auth secrets without "
            "printing secret values."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    render = subparsers.add_parser(
        "render-sidecar-consumer",
        help="Render sidecar PAYOUT_CONSUMER_KEYS_JSON from PAYOUT_SIDECAR_KEYS_JSON.",
    )
    render.add_argument("--signing-keys-file", required=True)
    render.add_argument("--output", required=True)
    add_common_validation_args(render)
    render.set_defaults(func=render_sidecar_consumer)

    files = subparsers.add_parser(
        "verify-files",
        help="Validate local signing and sidecar consumer JSON files.",
    )
    files.add_argument("--signing-keys-file", required=True)
    files.add_argument("--sidecar-consumer-keys-file", required=True)
    add_common_validation_args(files)
    files.set_defaults(func=verify_files)

    cluster = subparsers.add_parser(
        "verify-cluster",
        help="Validate Kubernetes signing and sidecar consumer Secrets.",
    )
    cluster.add_argument("--namespace", required=True)
    cluster.add_argument(
        "--signing-secret-name",
        default=DEFAULT_SIGNING_SECRET_NAME,
    )
    cluster.add_argument(
        "--signing-secret-key",
        default=DEFAULT_SIGNING_SECRET_KEY,
    )
    cluster.add_argument(
        "--sidecar-consumer-secret-name",
        default=DEFAULT_SIDECAR_CONSUMER_SECRET_NAME,
    )
    cluster.add_argument(
        "--sidecar-consumer-secret-key",
        default=DEFAULT_SIDECAR_CONSUMER_SECRET_KEY,
    )
    add_common_validation_args(cluster)
    cluster.set_defaults(func=verify_cluster)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except (SecretContractError, KubectlError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
