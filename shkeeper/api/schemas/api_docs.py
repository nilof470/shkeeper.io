from shkeeper.api.schemas.marshmallow_schemas import (
    DecryptionKeyErrorSchema,
    GetCryptoResponseSchema,
    TaskResponseSchema,
    BalancesResponseSchema,
    BalancesErrorSchema,
    ListAddressesResponseSchema,
    PaymentResponseSchema,
    PaymentRequestSchema,
    MultipayoutItemSchema,
    MultipayoutResponseSchema,
    ErrorPaymentResponseSchema,
    QuoteRequestSchema,
    QuoteResponseSchema,
    MetricsResponseSchema,
    ErrorSchema,
    PayoutCallbackSchema,
    PaymentCallbackSchema,
    BalanceResponseSchema,
    PayoutRequestSchema,
    PayoutResponseSchema,
    RetrieveTransactionsResponseSchema,
    InvoicesListResponseSchema,
    TxInfoResponseSchema,
    DecryptionKeyFormSchema,
    DecryptionKeySuccessSchema,
    PayoutStatusErrorSchema,
    PayoutStatusResponseSchema,
    PayoutExecutionCallbackEventSchema,
    PayoutExecutionErrorSchema,
    PayoutManualResolutionRequestSchema,
    PayoutExecutionRequestSchema,
    PayoutExecutionResponseSchema,
)

crypto_list_doc = {
    "description": (
        "Retrieve the list of available cryptocurrencies.\n\n"
        "Use `crypto_list` for integrations. "
        "The `crypto` field is kept only for backward compatibility."
    ),
    "tags": ["Cryptos"],
    "responses": {
        200: {
            "description": "Success – available cryptocurrencies retrieved",
            "content": {"application/json": {"schema": GetCryptoResponseSchema}},
        }
    },
    "x-codeSamples": [
        {
            "lang": "cURL",
            "label": "CLI",
            "source": (
                "curl --location --request GET "
                "'https://demo.shkeeper.io/api/v1/crypto'\n"
            ),
        }
    ],
}


crypto_balances_doc = {
    "description": (
        "Retrieve balances for all enabled cryptos, or for a subset "
        "specified via query parameter 'includes'."
    ),
    "tags": ["Cryptos"],
    "security": [{"API_Key": []}],
    "parameters": [
        {
            "name": "includes",
            "in": "query",
            "required": False,
            "description": (
                "Comma-separated list of crypto identifiers "
                "(e.g., BTC,ETH,TRX)"
            ),
            "schema": {"type": "string", "example": "BTC,ETH"},
        }
    ],
    "responses": {
        200: {
            "description": "Success – balances retrieved",
            "content": {"application/json": {"schema": BalancesResponseSchema}},
        },
        400: {
            "description": "Error – invalid includes or no valid cryptos requested",
            "content": {"application/json": {"schema": BalancesErrorSchema}},
        },
    },
    "x-codeSamples": [
        {
            "lang": "cURL",
            "label": "CLI",
            "source": (
                "curl --location --request GET "
                "'https://demo.shkeeper.io/api/v1/crypto/balances?includes=BTC,ETH' \\\n"
                "--header 'X-Shkeeper-API-Key: YOUR_API_KEY'\n"
            ),
        }
    ],
}


payment_request_doc = {
    "description": "Create a payment request",
    "tags": ["Payments"],
    "security": [{"API_Key": []}],
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": PaymentRequestSchema}},
    },
    "responses": {
        200: {
            "description": "Success",
            "content": {"application/json": {"schema": PaymentResponseSchema}},
        },
        400: {
            "description": "Error",
            "content": {
                "application/json": {"schema": ErrorPaymentResponseSchema}
            },
        },
    },
    "x-codeSamples": [
        {
            "lang": "cURL",
            "label": "CLI",
            "source": (
                "curl --location --request POST "
                "'https://demo.shkeeper.io/api/v1/ETH/payment_request' \\\n"
                "--header 'X-Shkeeper-API-Key: YOUR_API_KEY' \\\n"
                "--header 'Content-Type: application/json' \\\n"
                '--data-raw \'{"external_id":107,"fiat":"USD","amount":"18.25",'
                '"callback_url":"https://my-billing/callback.php"}\'\n'
            ),
        }
    ],
}

payout_callback_doc = {
    "description": (
        "SHKeeper sends a callback notification after a payout is completed.\n\n"
        "If `enable_payout_callback` is enabled, the callback will be sent automatically.\n"
        "If a `callback_url` was provided during payout creation, SHKeeper will send the notification to that URL."
    ),
    "tags": ["Notifications"],
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": PayoutCallbackSchema}},
    },
    "responses": {
        202: {
            "description": "Callback accepted",
            "content": {"application/json": {"schema": {"type": "object", "properties": {"status": {"type": "string", "example": "accepted"}}}}}
        },
        400: {
            "description": "Bad request",
            "content": {"application/json": {"schema": {"type": "object", "properties": {"status": {"type": "string"}, "message": {"type": "string"}}}}}
        },
        500: {
            "description": "Internal server error",
            "content": {"application/json": {"schema": {"type": "object", "properties": {"status": {"type": "string"}, "message": {"type": "string"}}}}}
        }
    }
}

metrics_doc = {
    "description": (
        "Retrieve system and cryptocurrency metrics.\n\n"
        "Authorization: HTTP Basic Auth using metric credentials. "
        "Metric credentials can be set via environment variables: METRICS_USERNAME, METRICS_PASSWORD. "
        "Default username/password: shkeeper/shkeeper."
    ),
    "tags": ["Metrics"],
    "security": [{"Basic_Metrics": []}],
    "responses": {
        200: {
            "description": "Success – metrics retrieved",
            "content": {"application/json": {"schema": MetricsResponseSchema}},
        },
        401: {
            "description": "Unauthorized – invalid credentials",
            "content": {"application/json": {"schema": {"type": "object", "properties": {"msg": {"type": "string"}}}}},
        },
    },
    "x-codeSamples": [
        {
            "lang": "cURL",
            "label": "CLI",
            "source": (
                "curl --location --request GET 'https://demo.shkeeper.io/metrics' \\\n"
                "--header 'Authorization: Basic c2hrZWVwZXI6c2hrZWVwZXI='\n"
            ),
        }
    ],
}

transaction_callback_doc = {
    "description": (
        "SHKeeper sends payment notifications for invoice-related transactions.\n\n"
        "Each transaction is sent individually, and the transaction that triggered the callback has `trigger = true`.\n"
        "Your server should respond with HTTP 202 if successfully processed."
    ),
    "tags": ["Notifications"],
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": PaymentCallbackSchema}},
    },
    "responses": {
        202: {
            "description": "Callback accepted",
            "content": {"application/json": {"schema": {"type": "object", "properties": {"status": {"type": "string", "example": "accepted"}}}}}
        },
        400: {
            "description": "Bad request",
            "content": {"application/json": {"schema": {"type": "object", "properties": {"status": {"type": "string"}, "message": {"type": "string"}}}}}
        },
        500: {
            "description": "Internal server error",
            "content": {"application/json": {"schema": {"type": "object", "properties": {"status": {"type": "string"}, "message": {"type": "string"}}}}}
        }
    }
}

quote_doc = {
    "description": "Create a quote",
    "tags": ["Cryptos"],
    "security": [{"API_Key": []}],
    "requestBody": {
        "required": False,
        "content": {"application/json": {"schema": QuoteRequestSchema}},
    },
    "responses": {
        200: {
            "description": "Success",
            "content": {"application/json": {"schema": QuoteResponseSchema}},
        },
        400: {
            "description": "Error",
            "content": {"application/json": {"schema": ErrorSchema}},
        },
    },
    "x-codeSamples": [
        {
            "lang": "cURL",
            "label": "CLI",
            "source": (
                "curl --location --request POST "
                "'https://demo.shkeeper.io/api/v1/ETH/quote' \\\n"
                "--header 'X-Shkeeper-API-Key: YOUR_API_KEY' \\\n"
                "--header 'Content-Type: application/json' \\\n"
                '--data-raw \'{"fiat":"USD","amount":"100.00"}\'\n'
            ),
        }
    ],
}


balance_doc = {
    "description": (
        "Retrieve balance information for a specific crypto, "
        "including amount in crypto, fiat, and server status."
    ),
    "tags": ["Cryptos"],
    "security": [{"API_Key": []}],
    "responses": {
        200: {
            "description": "Success – balance retrieved",
            "content": {"application/json": {"schema": BalanceResponseSchema}},
        },
        400: {
            "description": "Error – crypto not enabled or invalid",
            "content": {"application/json": {"schema": ErrorSchema}},
        },
    },
    "x-codeSamples": [
        {
            "lang": "cURL",
            "label": "CLI",
            "source": (
                "curl --location --request GET "
                "'https://demo.shkeeper.io/api/v1/ETH/balance' \\\n"
                "--header 'X-Shkeeper-Api-Key: YOUR_API_KEY'\n"
            ),
        }
    ],
}

payout_doc = {
    "description": "Create a single payout for the specified crypto.",
    "tags": ["Payouts"],
    "security": [{"Basic_Optional": []}, {"Basic": []}],
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": PayoutRequestSchema}},
    },
    "responses": {
        200: {
            "description": "Payout task successfully created",
            "content": {"application/json": {"schema": PayoutResponseSchema}},
        },
        400: {
            "description": "Error creating payout",
            "content": {"application/json": {"schema": ErrorSchema}},
        },
    },
    "x-codeSamples": [
        {
            "lang": "cURL",
            "label": "CLI",
            "source": (
                "curl --location --request POST "
                "'https://demo.shkeeper.io/api/v1/BTC/payout' \\\n"
                "--header 'Authorization: Basic YOUR_BASIC_AUTH' \\\n"
                "--header 'Content-Type: application/json' \\\n"
                '--data-raw \'{"amount":100,"destination":"0x123...","fee":"10"}\'\n'
            ),
        }
    ],
}

task_status_doc = {
    "description": (
        "Check the status of a multi-payout task for the specified crypto."
    ),
    "tags": ["Other"],
    "security": [{"Basic_Optional": []}, {"Basic": []}],
    "responses": {
        200: {
            "description": "Task status (PENDING, SUCCESS, or FAILURE)",
            "content": {"application/json": {"schema": TaskResponseSchema}},
        },
        400: {
            "description": "Error",
            "content": {"application/json": {"schema": ErrorSchema}},
        },
    },
    "x-codeSamples": [
        {
            "lang": "cURL",
            "label": "CLI",
            "source": (
                "curl --location --request GET "
                "'https://demo.shkeeper.io/api/v1/ETH-USDC/task/"
                "7028c45b-0c88-483e-b703-dd455a361b2e' \\\n"
                "--header 'Authorization: Basic YOUR_BASE64_CREDENTIALS'\n"
            ),
        }
    ],
}


multipayout_doc = {
    "description": "Execute a multi-payout for the specified crypto.",
    "tags": ["Payouts"],
    "security": [{"Basic_Optional": []}],
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": MultipayoutItemSchema}},
    },
    "responses": {
        200: {
            "description": "Success",
            "content": {
                "application/json": {"schema": MultipayoutResponseSchema}
            },
        },
        400: {
            "description": "Error",
            "content": {"application/json": {"schema": ErrorSchema}},
        },
    },
    "x-codeSamples": [
        {
            "lang": "cURL",
            "label": "CLI",
            "source": (
                "curl --location --request POST "
                "'https://demo.shkeeper.io/api/v1/ETH-USDT/multipayout' \\\n"
                "--header 'Authorization: Basic YOUR_BASE64_CREDENTIALS' \\\n"
                "--header 'Content-Type: application/json' \\\n"
                "--data-raw '["
                '{"dest":"0xE77895BAda700d663f033510f73f1E988CF55756",'
                '"amount":"100","external_id":"43234",'
                '"callback_url":"https://my.payout.com/notification"},'
                '{"dest":"0x7C4C7D3010d31329dd8244617C46e460E5EF8a6F",'
                '"amount":"200.11","external_id":"43235",'
                '"callback_url":"https://my.payout.com/notification"}'
                "]'\n"
            ),
        }
    ],
}


addresses_doc = {
    "description": (
        "Retrieve all known wallet addresses for the specified crypto."
    ),
    "tags": ["Transactions"],
    "security": [{"API_Key": []}],
    "responses": {
        200: {
            "description": "Success",
            "content": {
                "application/json": {"schema": ListAddressesResponseSchema}
            },
        },
        400: {
            "description": "Error",
            "content": {"application/json": {"schema": ErrorSchema}},
        },
    },
    "x-codeSamples": [
        {
            "lang": "cURL",
            "label": "CLI",
            "source": (
                "curl --location --request GET "
                "'https://demo.shkeeper.io/api/v1/ETH-USDC/addresses' \\\n"
                "--header 'X-Shkeeper-Api-Key: YOUR_API_KEY'\n"
            ),
        }
    ],
}


transactions_doc = {
    "description": (
        "Retrieve transactions for a given crypto and address. "
        "Address-scoped reads return incoming/deposit invoice transactions "
        "and unconfirmed deposit transactions; outgoing operational "
        "transactions are excluded. If none provided, returns all transactions."
    ),
    "tags": ["Transactions"],
    "security": [{"API_Key": []}],
    "responses": {
        200: {
            "description": "Success",
            "content": {
                "application/json": {
                    "schema": RetrieveTransactionsResponseSchema
                }
            },
        },
        400: {
            "description": "Error",
            "content": {"application/json": {"schema": ErrorSchema}},
        },
    },
    "x-codeSamples": [
        {
            "lang": "cURL",
            "label": "CLI",
            "source": (
                "curl --location --request GET "
                "'https://demo.shkeeper.io/api/v1/transactions/ETH/"
                "0xDCA83F12D963c7233E939a32e31aD758C7cCF307' \\\n"
                "--header 'X-Shkeeper-API-Key: YOUR_API_KEY'\n"
            ),
        }
    ],
}


invoices_doc = {
    "description": (
        "Retrieve invoices. Optionally filter by external_id. "
        "Excludes invoices with status 'OUTGOING'."
    ),
    "tags": ["Invoices"],
    "security": [{"API_Key": []}],
    "responses": {
        200: {
            "description": "List of invoices",
            "content": {
                "application/json": {"schema": InvoicesListResponseSchema}
            },
        },
        400: {
            "description": "Error occurred",
            "content": {"application/json": {"schema": ErrorSchema}},
        },
    },
    "x-codeSamples": [
        {
            "lang": "cURL",
            "label": "CLI",
            "source": (
                "curl --location --request GET "
                "'https://demo.shkeeper.io/api/v1/invoices/107' \\\n"
                "--header 'X-Shkeeper-API-Key: YOUR_API_KEY'\n"
            ),
        }
    ],
}


tx_info_doc = {
    "description": "Retrieve transaction info by txid and external_id.",
    "tags": ["Transactions"],
    "security": [{"API_Key": []}],
    "responses": {
        200: {
            "description": "Success",
            "content": {
                "application/json": {"schema": TxInfoResponseSchema}
            },
        },
        400: {
            "description": "Error",
            "content": {"application/json": {"schema": ErrorSchema}},
        },
    },
    "x-codeSamples": [
        {
            "lang": "cURL",
            "label": "CLI",
            "source": (
                "curl --location --request GET "
                "'https://demo.shkeeper.io/api/v1/tx-info/"
                "0xbcf68720db79454f40b2acf6bfb18897d497ab4d8bc9faf243c859d14d5d6b66/240' \\\n"
                "--header 'X-Shkeeper-API-Key: YOUR_API_KEY'\n"
            ),
        }
    ],
}


decryption_key_doc = {
    "description": (
        "Create an encryption key (enter a decryption key via API)."
    ),
    "tags": ["Encryption"],
    "security": [{"API_Key": []}],
    "requestBody": {
        "required": False,
        "content": {
            "multipart/form-data": {
                "schema": DecryptionKeyFormSchema
            }
        },
    },
    "responses": {
        200: {
            "description": "Success",
            "content": {
                "application/json": {
                    "schema": DecryptionKeySuccessSchema
                }
            },
        },
        400: {
            "description": "Error",
            "content": {
                "application/json": {
                    "schema": DecryptionKeyErrorSchema
                }
            },
        },
    },
    "x-codeSamples": [
        {
            "lang": "cURL",
            "label": "CLI",
            "source": (
                "curl --location --request POST "
                "'https://demo.shkeeper.io/api/v1/decryption-key' \\\n"
                "--header 'X-Shkeeper-API-Key: YOUR_API_KEY' \\\n"
                "--form 'key=YOUR_DECRYPTION_KEY'\n"
            ),
        }
    ],
}


payout_status_doc = {
    "description": (
        "Retrieve payout status by external_id for the specified "
        "cryptocurrency."
    ),
    "tags": ["Payouts"],
    "security": [{"API_Key": []}],
    "parameters": [
        {
            "name": "external_id",
            "in": "query",
            "required": True,
            "description": "External ID assigned to the payout",
            "schema": {"type": "string", "example": "abc123"},
        }
    ],
    "responses": {
        200: {
            "description": "Payout status retrieved",
            "content": {
                "application/json": {
                    "schema": PayoutStatusResponseSchema
                }
            },
        },
        400: {
            "description": "Missing external_id parameter",
            "content": {
                "application/json": {
                    "schema": PayoutStatusErrorSchema
                }
            },
        },
        404: {
            "description": "Payout not found",
            "content": {
                "application/json": {
                    "schema": PayoutStatusErrorSchema
                }
            },
        },
    },
    "x-codeSamples": [
        {
            "lang": "cURL",
            "label": "CLI",
            "source": (
                "curl --location --request GET "
                "'https://demo.shkeeper.io/api/v1/BTC/payout/status"
                "?external_id=abc123' \\\n"
                "--header 'X-Shkeeper-API-Key: YOUR_API_KEY'\n"
            ),
        }
    ],
}


payout_hmac_headers = [
    {
        "name": "X-Payout-Consumer",
        "in": "header",
        "required": True,
        "description": "Configured service consumer id, for example `merchant-app`.",
        "schema": {"type": "string", "example": "merchant-app"},
    },
    {
        "name": "X-Payout-Key-Id",
        "in": "header",
        "required": True,
        "description": "Configured key id for the service consumer.",
        "schema": {"type": "string", "example": "default"},
    },
    {
        "name": "X-Payout-Timestamp",
        "in": "header",
        "required": True,
        "description": "Unix timestamp in seconds. Default allowed clock skew is 300 seconds.",
        "schema": {"type": "integer", "example": 1780560600},
    },
    {
        "name": "X-Payout-Nonce",
        "in": "header",
        "required": True,
        "description": "Unique nonce for this signed request. Reuse is rejected.",
        "schema": {"type": "string", "example": "2c338f55-05ba-4a9c-aaf4-caa8fbd3148f"},
    },
    {
        "name": "X-Payout-Signature",
        "in": "header",
        "required": True,
        "description": (
            "Hex HMAC-SHA256 signature over "
            "`timestamp\\nnonce\\nMETHOD\\ncanonical_path\\ncanonical_query\\nbody_sha256`."
        ),
        "schema": {
            "type": "string",
            "example": "7db8cb77f00f3a8f2f6e2b9a960ad9ff3f7e4b1d4a77c1ac0f1d2e3f4a5b6c7d8",
        },
    },
]


payout_execution_create_doc = {
    "operationId": "CreatePayoutExecution",
    "summary": "Create payout execution",
    "description": (
        "Create an idempotent service-consumer USDT payout execution.\n\n"
        "This endpoint is a durable accept boundary. A `202` response with "
        "`state=CREATED` means SHKeeper stored the execution and will dispatch it "
        "through the payout reconciler. Consumers must use callbacks and "
        "`GET /api/v1/payout-executions/{external_id}` for monotonic progression.\n\n"
        "The request accepts only execution-contract fields: `external_id`, "
        "`asset`, `network`, `amount`, and `destination`. Additional request "
        "fields are not part of the signed execution contract.\n\n"
        "The request is signed with HMAC-SHA256. The signature base is "
        "`timestamp\\nnonce\\nMETHOD\\ncanonical_path\\ncanonical_query\\nbody_sha256`."
    ),
    "tags": ["Payouts"],
    "security": [{"Payout_HMAC": []}],
    "parameters": payout_hmac_headers,
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": PayoutExecutionRequestSchema}},
    },
    "responses": {
        202: {
            "description": "Execution accepted or idempotent existing execution returned",
            "content": {"application/json": {"schema": PayoutExecutionResponseSchema}},
        },
        400: {
            "description": "Invalid payout request or disabled/unsupported rail",
            "content": {"application/json": {"schema": PayoutExecutionErrorSchema}},
        },
        401: {
            "description": "Missing or unknown payout auth headers",
            "content": {"application/json": {"schema": PayoutExecutionErrorSchema}},
        },
        403: {
            "description": "Invalid signature, replayed nonce, expired timestamp, or forbidden rail key",
            "content": {"application/json": {"schema": PayoutExecutionErrorSchema}},
        },
        409: {
            "description": "The external_id already exists with a different canonical request",
            "content": {"application/json": {"schema": PayoutExecutionErrorSchema}},
        },
        503: {
            "description": "Enabled rail is not dispatchable or callback endpoint is not configured",
            "content": {"application/json": {"schema": PayoutExecutionErrorSchema}},
        },
        500: {
            "description": "Unexpected server error",
            "content": {"application/json": {"schema": PayoutExecutionErrorSchema}},
        },
    },
    "x-codeSamples": [
        {
            "lang": "cURL",
            "label": "CLI",
            "source": (
                "curl --location --request POST "
                "'https://demo.shkeeper.io/api/v1/payout-executions' \\\n"
                "--header 'Content-Type: application/json' \\\n"
                "--header 'X-Payout-Consumer: merchant-app' \\\n"
                "--header 'X-Payout-Key-Id: default' \\\n"
                "--header 'X-Payout-Timestamp: 1780560600' \\\n"
                "--header 'X-Payout-Nonce: 2c338f55-05ba-4a9c-aaf4-caa8fbd3148f' \\\n"
                "--header 'X-Payout-Signature: SIGNATURE_HEX' \\\n"
                "--data-raw '{\"external_id\":\"W123456789\",\"asset\":\"USDT\","
                "\"network\":\"TRON\",\"amount\":\"25.000000\","
                "\"destination\":\"TQZL6tWjV3L1y7mK7Q9...\"}'\n"
            ),
        }
    ],
}


payout_execution_status_doc = {
    "operationId": "GetPayoutExecutionStatus",
    "summary": "Get payout execution status",
    "description": (
        "Return payout execution status scoped by the authenticated payout consumer.\n\n"
        "Use this endpoint after submit timeout, callback delay, scheduler "
        "reconciliation, and before any manual payout action. The response contains "
        "monotonic state evidence: `event_version`, `state_transition_id`, hashes, "
        "sidecar state, txids/message hashes, failure fields, and "
        "`reconciliation_required`."
    ),
    "tags": ["Payouts"],
    "security": [{"Payout_HMAC": []}],
    "parameters": [
        {
            "name": "external_id",
            "in": "path",
            "required": True,
            "description": "Consumer payout request id used when the execution was submitted.",
            "schema": {"type": "string", "example": "W123456789"},
        },
        *payout_hmac_headers,
    ],
    "responses": {
        200: {
            "description": "Execution status retrieved",
            "content": {"application/json": {"schema": PayoutExecutionResponseSchema}},
        },
        401: {
            "description": "Missing or unknown payout auth headers",
            "content": {"application/json": {"schema": PayoutExecutionErrorSchema}},
        },
        403: {
            "description": "Invalid signature, replayed nonce, expired timestamp, or forbidden rail key",
            "content": {"application/json": {"schema": PayoutExecutionErrorSchema}},
        },
        400: {
            "description": "Invalid external_id",
            "content": {"application/json": {"schema": PayoutExecutionErrorSchema}},
        },
        404: {
            "description": "Execution not found for the authenticated consumer",
            "content": {"application/json": {"schema": PayoutExecutionErrorSchema}},
        },
        500: {
            "description": "Unexpected server error",
            "content": {"application/json": {"schema": PayoutExecutionErrorSchema}},
        },
    },
    "x-codeSamples": [
        {
            "lang": "cURL",
            "label": "CLI",
            "source": (
                "curl --location --request GET "
                "'https://demo.shkeeper.io/api/v1/payout-executions/W123456789' \\\n"
                "--header 'X-Payout-Consumer: merchant-app' \\\n"
                "--header 'X-Payout-Key-Id: default' \\\n"
                "--header 'X-Payout-Timestamp: 1780560600' \\\n"
                "--header 'X-Payout-Nonce: 6a1f3072-d275-4a2e-b31b-f29d0926b2f3' \\\n"
                "--header 'X-Payout-Signature: SIGNATURE_HEX'\n"
            ),
        }
    ],
}


payout_execution_manual_resolution_doc = {
    "description": (
        "Record operator manual-resolution evidence for a payout execution.\n\n"
        "This endpoint is for SHKeeper operators when an execution is in a manual "
        "review boundary such as `RECONCILIATION_REQUIRED`. It records structured "
        "technical evidence, writes an audit row, and moves the execution through "
        "a constrained resolution state."
    ),
    "tags": ["Payouts"],
    "security": [{"Basic": []}],
    "parameters": [
        {
            "name": "execution_id",
            "in": "path",
            "required": True,
            "description": "Internal SHKeeper payout execution id.",
            "schema": {"type": "integer", "example": 123},
        }
    ],
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {"schema": PayoutManualResolutionRequestSchema}
        },
    },
    "responses": {
        200: {
            "description": "Manual resolution recorded",
            "content": {"application/json": {"schema": PayoutExecutionResponseSchema}},
        },
        400: {
            "description": "Invalid or incomplete manual-resolution evidence",
            "content": {"application/json": {"schema": PayoutExecutionErrorSchema}},
        },
        403: {
            "description": "Authenticated operator is required",
            "content": {"application/json": {"schema": PayoutExecutionErrorSchema}},
        },
        404: {
            "description": "Execution not found",
            "content": {"application/json": {"schema": PayoutExecutionErrorSchema}},
        },
        409: {
            "description": "Requested resolution is not allowed from the current execution state",
            "content": {"application/json": {"schema": PayoutExecutionErrorSchema}},
        },
    },
    "x-codeSamples": [
        {
            "lang": "cURL",
            "label": "CLI",
            "source": (
                "curl --location --request POST "
                "'https://demo.shkeeper.io/api/v1/payout-executions/123/manual-resolution' \\\n"
                "--header 'Authorization: Basic YOUR_BASIC_AUTH' \\\n"
                "--header 'Content-Type: application/json' \\\n"
                "--data-raw '{\"resolution_status\":\"SAFE_FOR_MANUAL_PAYOUT\","
                "\"operator_note\":\"negative chain evidence checked\","
                "\"evidence\":{\"network\":\"TRON\",\"asset\":\"USDT\","
                "\"execution_id\":123,\"external_id\":\"W123456789\","
                "\"destination\":\"TQZL6tWjV3L1y7mK7Q9...\","
                "\"amount\":\"25.000000\",\"last_state\":\"RECONCILIATION_REQUIRED\","
                "\"last_sidecar_state\":\"RECONCILIATION_REQUIRED\","
                "\"source_wallet\":\"fee_deposit\",\"token_contract\":\"TRC20-USDT\","
                "\"checked_sources\":[\"tron-fullnode\",\"tron-indexer\"],"
                "\"searched_block_range\":{\"from\":100,\"to\":200},"
                "\"matching_transfer_found\":false,"
                "\"pending_original_artifact\":false}}'\n"
            ),
        }
    ],
}


payout_execution_callback_doc = {
    "description": (
        "Outbound callback body sent by SHKeeper to the configured consumer callback "
        "endpoint when a payout execution changes state. It is signed with the same "
        "`X-Payout-*` HMAC header family as the submit/status API. This schema is "
        "for consumer webhook implementation; it is not a SHKeeper inbound endpoint."
    ),
    "tags": ["Notifications"],
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": PayoutExecutionCallbackEventSchema}},
    },
    "responses": {
        202: {
            "description": "Callback accepted by consumer",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {"status": {"type": "string", "example": "accepted"}},
                    }
                }
            },
        }
    },
}
