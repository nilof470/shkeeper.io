#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import closing, contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROJECTS = ROOT.parent
sys.path.insert(0, str(ROOT))
CONSUMER = "test-payout-consumer"
CLIENT_KEY_ID = "client-key"
CLIENT_SECRET = "client-secret"
SIDECAR_KEY_ID = "sidecar-key"
SIDECAR_SECRET = "sidecar-secret"
CALLBACK_ENDPOINT_ID = "payout-e2e-callback"
CONTRACT_VERSION = "usdt-payout-execution-v1"


RAILS = {
    "TRON": {
        "repo": PROJECTS / "tron-shkeeper",
        "python": Path("/tmp/tron-shkeeper-py312-venv/bin/python"),
        "sidecar_kind": "tron",
        "sidecar_symbol": "USDT",
        "crypto_id": "USDT",
        "queue": "tron_usdt_fee_payouts",
        "destination": "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb",
    },
    "TON": {
        "repo": PROJECTS / "ton-shkeeper",
        "python": PROJECTS / "ton-shkeeper/.venv/bin/python",
        "sidecar_kind": "ton",
        "sidecar_symbol": "TON-USDT",
        "crypto_id": "TON-USDT",
        "queue": "ton_usdt_payouts",
        "destination": "UQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAJKZ",
    },
    "ETH": {
        "repo": PROJECTS / "ethereum-shkeeper",
        "python": PROJECTS / "ethereum-shkeeper/.venv/bin/python",
        "sidecar_kind": "eth",
        "sidecar_symbol": "ETH-USDT",
        "crypto_id": "ETH-USDT",
        "queue": "eth_usdt_payouts",
        "destination": "0x0000000000000000000000000000000000000001",
    },
}


class E2EFailure(RuntimeError):
    pass


def _json(value):
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _free_port():
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_port(port, process, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            raise E2EFailure(
                "sidecar server exited before listening\n"
                f"stdout:\n{stdout}\n\nstderr:\n{stderr}"
            )
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise E2EFailure(f"sidecar server did not listen on 127.0.0.1:{port}")


def _require_paths():
    missing = []
    for network, rail in RAILS.items():
        if not rail["repo"].exists():
            missing.append(f"{network} repo: {rail['repo']}")
        if not rail["python"].exists():
            missing.append(f"{network} python: {rail['python']}")
    if missing:
        raise E2EFailure("missing payout e2e dependency paths:\n" + "\n".join(missing))


def _consumer_keys_for_sidecar(network):
    rail_name = f"{network}-USDT"
    if network in ("ETH", "TON"):
        return {
            CONSUMER: {
                "rails": [rail_name],
                "keys": {SIDECAR_KEY_ID: SIDECAR_SECRET},
            }
        }
    return {
        CONSUMER: {
            SIDECAR_KEY_ID: {
                "secret": SIDECAR_SECRET,
                "rails": [rail_name],
            }
        }
    }


def _sidecar_env(network, port, temp_dir):
    rail = RAILS[network]
    env = os.environ.copy()
    env.update(
        {
            "PAYOUT_CONSUMER_KEYS_JSON": _json(_consumer_keys_for_sidecar(network)),
            "PAYOUT_AUTH_MAX_AGE_SECONDS": "300",
            "PAYOUT_EXECUTION_PREFLIGHT_CHECKS_ENABLED": "false",
            "PAYOUT_EXECUTION_AUTO_ENQUEUE_ENABLED": "false",
            "PAYOUT_EXECUTION_REQUIRE_AUTO_ENQUEUE": "false",
            "REDIS_HOST": "127.0.0.1:1",
            "E2E_SIDECAR_PORT": str(port),
        }
    )
    if network == "ETH":
        env["SQLALCHEMY_DATABASE_URI"] = (
            f"sqlite:///{temp_dir}/ethereum-shkeeper-payout-e2e.db"
        )
        env["ETH_USDT_PAYOUT_QUEUE"] = rail["queue"]
    elif network == "TON":
        env["SQLALCHEMY_DATABASE_URI"] = (
            f"sqlite:///{temp_dir}/ton-shkeeper-payout-e2e.db"
        )
        env["TON_USDT_PAYOUT_QUEUE"] = rail["queue"]
    else:
        env["DATABASE"] = f"{temp_dir}/tron-shkeeper-payout-e2e.db"
        env["BALANCES_DATABASE"] = f"{temp_dir}/tron-shkeeper-balances-e2e.db"
        env["TRON_USDT_PAYOUT_QUEUE"] = rail["queue"]
        env["TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED"] = "false"
    return env


@contextmanager
def _sidecar_server(network, temp_dir):
    rail = RAILS[network]
    port = _free_port()
    process = subprocess.Popen(
        [
            str(rail["python"]),
            str(Path(__file__).resolve()),
            "--sidecar-server",
            rail["sidecar_kind"],
            "--port",
            str(port),
        ],
        cwd=rail["repo"],
        env=_sidecar_env(network, port, temp_dir),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for_port(port, process)
        yield f"http://127.0.0.1:{port}"
    finally:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)


def _signed_headers(method, path, body=b"", nonce="nonce-1"):
    from shkeeper.services.payout_execution_auth import (
        PAYOUT_CONSUMER_HEADER,
        PAYOUT_KEY_ID_HEADER,
        PAYOUT_NONCE_HEADER,
        PAYOUT_SIGNATURE_HEADER,
        PAYOUT_TIMESTAMP_HEADER,
        sign_request,
        signature_base,
    )

    timestamp = int(time.time())
    base = signature_base(timestamp, nonce, method, path, "", body)
    return {
        PAYOUT_CONSUMER_HEADER: CONSUMER,
        PAYOUT_KEY_ID_HEADER: CLIENT_KEY_ID,
        PAYOUT_TIMESTAMP_HEADER: str(timestamp),
        PAYOUT_NONCE_HEADER: nonce,
        PAYOUT_SIGNATURE_HEADER: sign_request(CLIENT_SECRET, base),
    }


def _create_shkeeper_app(temp_dir):
    from flask import Flask, g

    from shkeeper import db
    from shkeeper.api_v1 import bp

    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{temp_dir}/shkeeper-payout-e2e.db",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        PAYOUT_CONSUMER_KEYS={
            CONSUMER: {
                CLIENT_KEY_ID: {
                    "secret": CLIENT_SECRET,
                    "rails": ["TRON-USDT", "TON-USDT", "ETH-USDT"],
                }
            }
        },
        PAYOUT_SIDECAR_KEYS={
            CONSUMER: {
                SIDECAR_KEY_ID: {
                    "secret": SIDECAR_SECRET,
                    "rails": ["TRON-USDT", "TON-USDT", "ETH-USDT"],
                }
            }
        },
        PAYOUT_CALLBACK_KEYS={
            CONSUMER: {
                "callback-key": {
                    "secret": "callback-secret",
                }
            }
        },
        PAYOUT_CALLBACK_ENDPOINTS={
            CONSUMER: {
                CALLBACK_ENDPOINT_ID: "http://127.0.0.1/payout-callbacks",
            }
        },
        PAYOUT_AUTH_MAX_AGE_SECONDS=300,
        PAYOUT_SIDECAR_REQUEST_TIMEOUT=5,
    )
    db.init_app(app)
    app.register_blueprint(bp)

    @app.before_request
    def set_test_user_context():
        if not hasattr(g, "user"):
            g.user = None

    ctx = app.app_context()
    ctx.push()
    db.create_all()
    return app, ctx


def _add_rail(network, sidecar_url):
    from shkeeper import db
    from shkeeper.models import (
        PayoutRail,
        PayoutRailHotWalletPolicy,
        PayoutRailLegacySpendPolicy,
    )

    rail = RAILS[network]
    db.session.add(
        PayoutRail(
            consumer=CONSUMER,
            asset="USDT",
            network=network,
            crypto_id=rail["crypto_id"],
            sidecar_service=sidecar_url,
            sidecar_symbol=rail["sidecar_symbol"],
            payout_queue=rail["queue"],
            source_wallet_ref="fee_deposit",
            hot_wallet_policy=PayoutRailHotWalletPolicy.CURRENT_SIDECAR_SOURCE_WALLET,
            legacy_spend_policy=PayoutRailLegacySpendPolicy.BLOCK_AUTOMATIC_BYPASS,
            execution_enabled=True,
            decimals=6,
            callback_endpoint_id=CALLBACK_ENDPOINT_ID,
            contract_version=CONTRACT_VERSION,
        )
    )
    db.session.commit()


def _post_execution(client, network, nonce):
    payload = {
        "external_id": f"E2E-{network}-1",
        "asset": "USDT",
        "network": network,
        "amount": "12.345678",
        "destination": RAILS[network]["destination"],
    }
    body = _json(payload).encode("utf-8")
    path = "/api/v1/payout-executions"
    return client.post(
        path,
        data=body,
        headers=_signed_headers("POST", path, body, nonce=nonce),
        content_type="application/json",
    )


def _assert(condition, message):
    if not condition:
        raise E2EFailure(message)


def _verify_network(network, temp_dir):
    from shkeeper import db
    from shkeeper.models import PayoutExecution, PayoutExecutionState
    from shkeeper.services.payout_execution_reconciler import PayoutExecutionReconciler
    from shkeeper.services.payout_sidecar_client import HttpPayoutSidecarClient

    with _sidecar_server(network, temp_dir) as sidecar_url:
        app, ctx = _create_shkeeper_app(temp_dir)
        try:
            _add_rail(network, sidecar_url)
            response = _post_execution(app.test_client(), network, f"{network}-submit")
            _assert(
                response.status_code == 202,
                f"{network} SHKeeper submit failed: {response.status_code} {response.get_data(as_text=True)}",
            )
            execution_id = response.get_json()["execution_id"]

            processed = PayoutExecutionReconciler.dispatch_ready(batch_size=10)
            _assert(processed == 1, f"{network} reconciler processed {processed}, expected 1")

            execution = PayoutExecution.query.get(execution_id)
            _assert(execution is not None, f"{network} execution row missing")
            _assert(
                execution.state == PayoutExecutionState.ENQUEUED,
                f"{network} expected ENQUEUED after sidecar submit, got {execution.state}; "
                f"error_code={execution.error_code}; error_message={execution.error_message}",
            )
            _assert(
                execution.sidecar_state == "RECEIVED",
                f"{network} expected sidecar RECEIVED, got {execution.sidecar_state}",
            )
            _assert(
                execution.sidecar_execution_id == str(execution.id),
                f"{network} sidecar execution id mismatch",
            )

            status = HttpPayoutSidecarClient().status(execution)
            for field in (
                "consumer",
                "execution_id",
                "external_id",
                "asset",
                "network",
                "request_hash",
                "sidecar_payload_hash",
            ):
                expected = getattr(execution, "id" if field == "execution_id" else field)
                _assert(
                    str(status.get(field)) == str(expected),
                    f"{network} sidecar status mismatch for {field}: {status.get(field)} != {expected}",
                )
            _assert(
                status.get("state") == "RECEIVED",
                f"{network} expected sidecar status RECEIVED, got {status.get('state')}",
            )

            processed = PayoutExecutionReconciler.dispatch_ready(batch_size=10)
            _assert(
                processed == 1,
                f"{network} status poll processed {processed}, expected 1",
            )
            db.session.refresh(execution)
            _assert(
                execution.state == PayoutExecutionState.ENQUEUED,
                f"{network} status poll unexpectedly changed state to {execution.state}; "
                f"error_code={execution.error_code}; error_message={execution.error_message}",
            )
            print(f"{network} SHKeeper-to-sidecar payout e2e OK")
        finally:
            db.session.remove()
            db.drop_all()
            ctx.pop()


def run_parent():
    _require_paths()
    with tempfile.TemporaryDirectory(prefix="shkeeper-payout-e2e-") as temp_dir:
        for network in ("TRON", "TON", "ETH"):
            _verify_network(network, temp_dir)
    print("SHKeeper payout sidecar e2e passed.")


def _run_eth_or_ton_server(kind, port):
    sys.path.insert(0, os.getcwd())
    import werkzeug
    from werkzeug.serving import make_server

    if not hasattr(werkzeug, "__version__"):
        werkzeug.__version__ = "3"

    from app import create_app

    flask_app = create_app()
    flask_app.config.update(TESTING=True)
    make_server("127.0.0.1", port, flask_app).serve_forever()


def _run_tron_server(port):
    sys.path.insert(0, os.getcwd())

    from flask import Flask
    from werkzeug.serving import make_server

    from app.config import config

    config.DATABASE = os.environ["DATABASE"]
    config.BALANCES_DATABASE = os.environ["BALANCES_DATABASE"]
    config.PAYOUT_CONSUMER_KEYS = _consumer_keys_for_sidecar("TRON")
    config.PAYOUT_AUTH_MAX_AGE_SECONDS = 300
    config.PAYOUT_EXECUTION_PREFLIGHT_CHECKS_ENABLED = False
    config.PAYOUT_EXECUTION_AUTO_ENQUEUE_ENABLED = False
    config.TRON_USDT_PAYOUT_RESOURCE_PROVISIONING_ENABLED = False
    config.TRON_USDT_PAYOUT_QUEUE = RAILS["TRON"]["queue"]

    app = Flask(__name__, root_path=os.path.join(os.getcwd(), "app"))
    app.config.update(
        TESTING=True,
        DATABASE=config.DATABASE,
        BALANCES_DATABASE=config.BALANCES_DATABASE,
        API_USERNAME="shkeeper",
        API_PASSWORD="shkeeper",
        PAYOUT_CONSUMER_KEYS=config.PAYOUT_CONSUMER_KEYS,
        PAYOUT_AUTH_MAX_AGE_SECONDS=300,
    )
    app.config.DATABASE = config.DATABASE
    app.config.BALANCES_DATABASE = config.BALANCES_DATABASE

    from app import db
    from app import utils

    db.init_app(app)
    app.url_map.converters["decimal"] = utils.DecimalConverter

    from app.api import api

    app.register_blueprint(api)
    make_server("127.0.0.1", port, app).serve_forever()


def run_sidecar_server(kind, port):
    if kind in ("eth", "ton"):
        _run_eth_or_ton_server(kind, port)
        return
    if kind == "tron":
        _run_tron_server(port)
        return
    raise SystemExit(f"unknown sidecar kind: {kind}")


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Run SHKeeper payout execution e2e against ETH/TON/TRON sidecars."
    )
    parser.add_argument("--sidecar-server", choices=("eth", "ton", "tron"))
    parser.add_argument("--port", type=int)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.sidecar_server:
            if not args.port:
                raise E2EFailure("--port is required for --sidecar-server")
            run_sidecar_server(args.sidecar_server, args.port)
        else:
            run_parent()
    except E2EFailure as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
