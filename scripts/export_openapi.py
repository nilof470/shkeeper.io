import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shkeeper import create_app


PAYOUT_EXECUTION_PATHS = (
    "/api/v1/payout-executions",
    "/api/v1/payout-executions/{external_id}",
    "/api/v1/payout-executions/{execution_id}/manual-resolution",
)

PAYOUT_SCHEMA_NAMES = (
    "PayoutExecutionRequest",
    "PayoutExecutionResponse",
    "PayoutExecutionError",
    "PayoutManualResolutionRequest",
    "PayoutExecutionCallbackEvent",
)

PAYOUT_HMAC_PARAMETER_NAMES = (
    "PayoutConsumerHeader",
    "PayoutKeyIdHeader",
    "PayoutTimestampHeader",
    "PayoutNonceHeader",
    "PayoutSignatureHeader",
)


def _response(description, schema):
    return {
        "description": description,
        "content": {
            "application/json": {
                "schema": schema,
            },
        },
    }


def _schema_ref(name):
    return {"$ref": f"#/components/schemas/{name}"}


def _payout_hmac_parameters():
    return {
        "PayoutConsumerHeader": {
            "name": "X-Payout-Consumer",
            "in": "header",
            "required": True,
            "description": "Configured service consumer id for payout execution.",
            "schema": {"type": "string", "example": "merchant-app"},
        },
        "PayoutKeyIdHeader": {
            "name": "X-Payout-Key-Id",
            "in": "header",
            "required": True,
            "description": "Configured key id for the payout HMAC secret.",
            "schema": {"type": "string", "example": "default"},
        },
        "PayoutTimestampHeader": {
            "name": "X-Payout-Timestamp",
            "in": "header",
            "required": True,
            "description": (
                "Unix timestamp in seconds. Default allowed clock skew is 300 "
                "seconds."
            ),
            "schema": {"type": "integer", "example": 1780560600},
        },
        "PayoutNonceHeader": {
            "name": "X-Payout-Nonce",
            "in": "header",
            "required": True,
            "description": (
                "Submit/status requests must use a one-time nonce. Callback "
                "deliveries use the callback `event_id` as the nonce; retries "
                "for the same callback reuse that nonce and must be deduplicated "
                "by `event_id`."
            ),
            "schema": {
                "type": "string",
                "example": "2c338f55-05ba-4a9c-aaf4-caa8fbd3148f",
            },
        },
        "PayoutSignatureHeader": {
            "name": "X-Payout-Signature",
            "in": "header",
            "required": True,
            "description": (
                "Lowercase hex HMAC-SHA256 over "
                "`timestamp\\nnonce\\nMETHOD\\ncanonical_path\\ncanonical_query\\nbody_sha256`."
            ),
            "schema": {
                "type": "string",
                "example": (
                    "7db8cb77f00f3a8f2f6e2b9a960ad9ff3f7e4b1d4a77c1ac0f1d2e3f4a5b6c7d8"
                ),
            },
        },
    }


def _external_id_parameter():
    return {
        "name": "external_id",
        "in": "path",
        "required": True,
        "description": (
            "Immutable consumer-side payout request id used when "
            "the payout execution was submitted."
        ),
        "schema": {"type": "string", "example": "W123456789"},
    }


def _payout_header_refs():
    return [_payout_hmac_parameters()[name] for name in PAYOUT_HMAC_PARAMETER_NAMES]


def _payout_hmac_header_parameters():
    return list(_payout_hmac_parameters().values())


def _without_default_response(operation):
    cleaned = dict(operation)
    responses = dict(cleaned.get("responses", {}))
    responses.pop("default", None)
    cleaned["responses"] = responses
    return cleaned


def _build_submit_operation(runtime_spec):
    operation = _without_default_response(
        runtime_spec["paths"]["/api/v1/payout-executions"]["post"]
    )
    operation["parameters"] = _payout_header_refs()
    return operation


def _build_status_operation(runtime_spec):
    operation = _without_default_response(
        runtime_spec["paths"]["/api/v1/payout-executions/{external_id}"]["get"]
    )
    operation["parameters"] = [
        _external_id_parameter(),
        *_payout_header_refs(),
    ]
    return operation


def _build_manual_resolution_operation(runtime_spec):
    return _without_default_response(
        runtime_spec["paths"][
            "/api/v1/payout-executions/{execution_id}/manual-resolution"
        ]["post"]
    )


def _build_callback_operation():
    return {
        "operationId": "ReceiveShKeeperPayoutExecutionCallback",
        "summary": "Receive SHKeeper payout execution callback",
        "description": (
            "Consumer webhook implemented by the upstream application. SHKeeper "
            "sends this signed callback whenever a payout execution changes "
            "state. Callback retries reuse `event_id` as `X-Payout-Nonce`; the "
            "consumer must deduplicate by `event_id` and apply events "
            "monotonically."
        ),
        "servers": [
            {
                "url": "https://consumer.example.com",
                "description": "Consumer webhook server",
            }
        ],
        "tags": ["Payout callbacks"],
        "security": [{"Payout_HMAC": []}],
        "parameters": _payout_hmac_header_parameters(),
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": _schema_ref("PayoutExecutionCallbackEvent")
                }
            },
        },
        "responses": {
            "202": _response(
                "Callback accepted",
                {
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "example": "accepted",
                        }
                    },
                    "required": ["status"],
                },
            ),
            "400": _response(
                "Malformed callback body or stale/conflicting event",
                _schema_ref("PayoutExecutionError"),
            ),
            "401": _response(
                "Missing or unknown payout auth headers",
                _schema_ref("PayoutExecutionError"),
            ),
            "403": _response(
                "Invalid signature, expired timestamp, or invalid callback auth",
                _schema_ref("PayoutExecutionError"),
            ),
        },
    }


def _required_runtime_components(runtime_spec):
    components = runtime_spec.get("components", {})
    schemas = components.get("schemas", {})
    security_schemes = components.get("securitySchemes", {})

    missing_schemas = [name for name in PAYOUT_SCHEMA_NAMES if name not in schemas]
    if missing_schemas:
        raise SystemExit(
            "runtime OpenAPI is missing payout schemas: "
            + ", ".join(sorted(missing_schemas))
        )
    missing_security = [
        name
        for name in ("Payout_HMAC", "Basic_Optional")
        if name not in security_schemes
    ]
    if missing_security:
        raise SystemExit(
            "runtime OpenAPI is missing payout security schemes: "
            + ", ".join(sorted(missing_security))
        )

    return schemas, security_schemes


def _merge_payout_methods(base_spec, runtime_spec):
    schemas, security_schemes = _required_runtime_components(runtime_spec)

    base_spec.setdefault("paths", {})
    base_spec["paths"]["/api/v1/payout-executions"] = {
        "post": _build_submit_operation(runtime_spec),
    }
    base_spec["paths"]["/api/v1/payout-executions/{external_id}"] = {
        "get": _build_status_operation(runtime_spec),
    }
    base_spec["paths"][
        "/api/v1/payout-executions/{execution_id}/manual-resolution"
    ] = {
        "post": _build_manual_resolution_operation(runtime_spec),
    }

    components = base_spec.setdefault("components", {})
    component_schemas = components.setdefault("schemas", {})
    for name in PAYOUT_SCHEMA_NAMES:
        component_schemas[name] = schemas[name]

    component_security = components.setdefault("securitySchemes", {})
    component_security["Payout_HMAC"] = security_schemes["Payout_HMAC"]
    component_security.setdefault("Basic_Optional", security_schemes["Basic_Optional"])

    webhooks = base_spec.setdefault("webhooks", {})
    webhooks["shkeeperPayoutExecutionCallback"] = {
        "post": _build_callback_operation(),
    }

    tags = base_spec.setdefault("tags", [])
    if not any(tag.get("name") == "Payout callbacks" for tag in tags):
        tags.append(
            {
                "name": "Payout callbacks",
                "x-displayName": "Payout callbacks",
                "description": "Callbacks sent by SHKeeper after payout execution state changes.",
            }
        )

    return base_spec


def _iter_local_refs(value):
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            yield ref
        for child in value.values():
            yield from _iter_local_refs(child)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_local_refs(item)


def _resolve_local_ref(spec, ref):
    target = spec
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(target, dict) or part not in target:
            return False
        target = target[part]
    return True


def _validate_local_refs(spec):
    missing = sorted(
        {
            ref
            for ref in _iter_local_refs(spec)
            if not _resolve_local_ref(spec, ref)
        }
    )
    if missing:
        raise SystemExit("OpenAPI contains unresolved local refs: " + ", ".join(missing))


def _validate_payout_methods(spec):
    missing = [
        path
        for path in PAYOUT_EXECUTION_PATHS
        if path not in spec.get("paths", {})
    ]
    if missing:
        raise SystemExit(
            "OpenAPI is missing payout execution paths: " + ", ".join(missing)
        )


def main():
    profile = "merge-payouts"
    check = False
    args = []
    for arg in sys.argv[1:]:
        if arg.startswith("--profile="):
            profile = arg.split("=", 1)[1]
        elif arg == "--check":
            check = True
        else:
            args.append(arg)
    if profile not in ("merge-payouts", "runtime"):
        raise SystemExit("profile must be merge-payouts or runtime")
    default_output = (
        "docs/openapi-3.json"
        if profile == "merge-payouts"
        else "docs/openapi-runtime.json"
    )
    output_path = args[0] if args else default_output
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": os.environ.get(
                "OPENAPI_DATABASE_URI",
                "sqlite:///:memory:",
            ),
            "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            "DISABLE_SCHEDULER": True,
        }
    )
    with app.app_context():
        api = app.extensions["smorest"]
        spec = api.spec.to_dict()
        if profile == "merge-payouts":
            with open(output_path, "r", encoding="utf-8") as f:
                base_spec = json.load(f)
            spec = _merge_payout_methods(base_spec, spec)
            _validate_payout_methods(spec)
        _validate_local_refs(spec)
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        rendered = json.dumps(spec, indent=2, ensure_ascii=False) + "\n"
        if check:
            with open(output_path, "r", encoding="utf-8") as f:
                existing = f.read()
            if existing != rendered:
                raise SystemExit(
                    f"{output_path} is not up to date; run scripts/export_openapi.py"
                )
            return
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(rendered)

if __name__ == "__main__":
    main()
