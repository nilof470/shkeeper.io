# API Gateway — Endpoint Reference

> Public API Gateway endpoint documentation as displayed in Swagger UI (`/api/v1/docs`).
>
> **Base URL:** `api.profeex.io/api/v1`  
> **OpenAPI Spec:** `/api/v1/openapi.json`  
> **Swagger UI:** `/api/v1/docs`  
> **ReDoc:** `/api/v1/redoc`

---

## Authentication

All endpoints (except Webhook Documentation) require authentication via one of two methods:

| Method | Header | Format |
|--------|--------|--------|
| **API Key** | `X-API-Key` | String — your personal API key |
| **JWT Token** | `Authorization` | `Bearer <token>` |

If neither header is provided or the credentials are invalid, the API returns `401 Unauthorized`.

---

## Table of Contents

1. [Health Check (Root)](#1-health-check-root)
2. [Users — User Information](#2-users--user-information)
3. [Balance — User Balance](#3-balance--user-balance)
4. [Orders — Order History](#4-orders--order-history)
5. [Resource Delegation](#5-resource-delegation)
   - [Pre-calculate Energy Cost](#51-get-apiv1delegationprecountenergy)
   - [Pre-calculate Bandwidth Cost](#52-get-apiv1delegationprecountbandwidth)
   - [Pre-calculate Batch Energy Cost](#53-get-apiv1delegationprecountbatchenergy)
   - [Buy Energy](#54-post-apiv1delegationbuyenergy)
   - [Buy Batch Energy](#55-post-apiv1delegationbatchenergy)
   - [Buy Bandwidth](#56-post-apiv1delegationbuybandwidth)
   - [Order Status](#57-get-apiv1delegationstatustask_id)
   - [USDT Transfer Fee Calculation](#58-get-apiv1delegationfee)
6. [Address Activation](#6-address-activation)
   - [Request Activation](#61-post-apiv1activationactivate)
   - [Activation Cost](#62-get-apiv1activationcost)
7. [TRON Node — Blockchain Proxy](#7-tron-node--blockchain-proxy)
   - [Get Account Resource](#71-get-apiv1nodewalletgetaccountresource)
   - [Get Account](#72-get-apiv1nodewalletgetaccount)
8. [Webhook Documentation](#8-webhook-documentation)
   - [Payload Format](#81-webhook-payload-format)
   - [HTTP Headers](#82-webhook-http-headers)
   - [Signature Verification](#83-signature-verification)
   - [Notification Codes](#84-notification-codes-notification_code)
   - [Setup Steps](#85-setup-steps-via-telegram-bot)
   - [Security Considerations](#86-security-considerations)
   - [Retry Policy](#87-retry-policy)

---

## 1. Health Check (Root)

### `GET /`

Basic service health check.

**Authentication:** Not required

**Response (`200 OK`):**
```json
{
  "message": "API Gateway is running.",
  "service": "API Gateway",
  "version": "0.1.0",
  "status": "healthy",
  "docs_url": "/api/v1/docs",
  "health_check": "/api/v1/health"
}
```

---

## 2. Users — User Information

**Tag:** `Users`  
**Prefix:** `/api/v1/users`

### `GET /api/v1/users/info`

**Summary:** Get User Info  
**Description:** Returns user ID, balances, deposit address, and registration date.

**Authentication:** Required (`X-API-Key` or `Bearer Token`)

**Response (`200 OK`) — `UserInfoResponse`:**

| Field | Type | Description |
|-------|------|-------------|
| `user_id` | `integer` | User ID |
| `balances` | `object` | Balance dictionary (e.g., `{"TRX": "100.0", "USDT": "50.0"}`) |
| `deposit_address` | `string \| null` | Deposit TRON address |
| `registration_date` | `datetime \| null` | Registration date (ISO 8601) |

**Example response:**
```json
{
  "user_id": 12345,
  "balances": {
    "TRX": "1500.25",
    "USDT": "320.00"
  },
  "deposit_address": "TLsV52sRDL79HXGGm9yzwKibb6BeruhUzy",
  "registration_date": "2024-06-15T10:30:00Z"
}
```

**Error codes:**

| Code | Description |
|------|-------------|
| `401` | Invalid or expired token / API key |
| `500` | Internal server error |

---

## 3. Balance — User Balance

**Tag:** `Balance`  
**Prefix:** `/api/v1/balance`

### `GET /api/v1/balance`

**Summary:** Get User Balance  
**Description:** Retrieves the current TRX and USDT balances for the authenticated user.

**Authentication:** Required

**Response (`200 OK`) — `BalanceResponse`:**

| Field | Type | Description |
|-------|------|-------------|
| `user_id` | `integer` | User ID |
| `balances` | `object` | Balance dictionary (e.g., `{"TRX": "100.0", "USDT": "50.0"}`) |

**Example response:**
```json
{
  "user_id": 12345,
  "balances": {
    "TRX": "1500.25",
    "USDT": "320.00"
  }
}
```

**Error codes:**

| Code | Description |
|------|-------------|
| `401` | Invalid or expired token / API key |
| `503` | Account Service unavailable |

---

## 4. Orders — Order History

**Tag:** `Orders`  
**Prefix:** `/api/v1/orders`

### `GET /api/v1/orders/history`

**Summary:** Get Order History  
**Description:** Retrieves paginated order history with filtering, sorting, and date ranges. Supports simple dates (`YYYY-MM-DD`), ISO datetime, and relative periods (e.g., `7d`, `24h`).

**Authentication:** Required

**Query parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|:--------:|:-------:|-------------|
| `page` | `integer` | No | `1` | Page number (1-indexed) |
| `size` | `integer` | No | `10` | Items per page (1–10000) |
| `date_from` | `datetime` | No | — | Start date filter (inclusive). Formats: `YYYY-MM-DD` or ISO datetime |
| `date_to` | `datetime` | No | — | End date filter (inclusive). Formats: `YYYY-MM-DD` or ISO datetime |
| `last` | `string` | No | — | Relative time period. Examples: `7d`, `30d`, `24h`, `1h`, `1w`, `1m`, `1y`. **Overrides** `date_from`/`date_to` |
| `status` | `string` | No | — | Filter by status: `PENDING`, `ACTIVE`, `COMPLETED`, `FAILED`, `CANCELLED` |
| `resource_type` | `string` | No | — | Filter by resource type: `ENERGY`, `BANDWIDTH` |
| `sort` | `string` | No | `-created_at` | Sort field. Prefix with `-` for descending. Valid fields: `created_at`, `status`, `summa`, `volume`, `resource_type` |

> **Note:** `date_from` and `date_to` must both be provided or both be omitted (unless `last` is used).

**Response (`200 OK`) — `PaginatedOrderHistoryResponse`:**

| Field | Type | Description |
|-------|------|-------------|
| `items` | `array` | Array of orders (see below) |
| `total` | `integer` | Total number of records |
| `page` | `integer` | Current page |
| `size` | `integer` | Page size |
| `pages` | `integer` | Total number of pages |
| `has_next` | `boolean` | Whether there is a next page |
| `has_previous` | `boolean` | Whether there is a previous page |

**`items[]` element — `OrderHistoryResponse`:**

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | `string` | Task UUID |
| `user_id` | `integer` | User ID |
| `target_address` | `string` | Target TRON address |
| `resource_type` | `string` | Resource type (`ENERGY` / `BANDWIDTH`) |
| `currency` | `string` | Payment currency (`TRX` / `USDT`) |
| `duration` | `string` | Duration (`1h`, `3d`, `7d`, `14d`) |
| `volume` | `integer` | Resource volume |
| `summa` | `decimal` | Total cost |
| `status` | `string` | Order status |
| `txid` | `string \| null` | Blockchain transaction ID |
| `created_at` | `datetime` | Creation date (ISO 8601) |

**Example response:**
```json
{
  "items": [
    {
      "task_id": "550e8400-e29b-41d4-a716-446655440000",
      "user_id": 12345,
      "target_address": "TLsV52sRDL79HXGGm9yzwKibb6BeruhUzy",
      "resource_type": "ENERGY",
      "currency": "TRX",
      "duration": "3d",
      "volume": 65000,
      "summa": "150.75",
      "status": "COMPLETED",
      "txid": "a1b2c3d4e5f6...",
      "created_at": "2025-01-15T12:30:00Z"
    }
  ],
  "total": 42,
  "page": 1,
  "size": 10,
  "pages": 5,
  "has_next": true,
  "has_previous": false
}
```

**Error codes:**

| Code | Description |
|------|-------------|
| `400` | Invalid parameters (`last` format, mismatched dates, invalid sort field) |
| `401` | Invalid or expired token / API key |
| `503` | Account Service unavailable |

---

## 5. Resource Delegation

**Tag:** `Resource Delegation`  
**Prefix:** `/api/v1/delegation`

### 5.1. `GET /api/v1/delegation/precount/energy`

**Summary:** Pre-calculate Energy Cost  
**Description:** Calculates the estimated cost for delegating energy based on volume, duration, and currency.

**Authentication:** Required

**Query parameters:**

| Parameter | Type | Required | Description |
|-----------|------|:--------:|-------------|
| `volume` | `integer` | Yes | Amount of energy per day (> 0) |
| `days` | `string` | Yes | Duration: `1h`, `1d`, `3d`, `7d`, `14d` |
| `currency` | `string` | Yes | Payment currency: `TRX` or `USDT` |

**Response (`200 OK`) — `PricingResponse`:**

| Field | Type | Description |
|-------|------|-------------|
| `duration` | `string` | Duration |
| `volume` | `integer` | Volume |
| `price` | `decimal` | Price per unit |
| `summa` | `decimal` | Total cost |
| `currency` | `string` | Currency |

**Example response:**
```json
{
  "duration": "3d",
  "volume": 65000,
  "price": "0.0023",
  "summa": "150.75",
  "currency": "TRX"
}
```

**Error codes:**

| Code | Description |
|------|-------------|
| `401` | Invalid or expired token |
| `404` | Pricing not found for the specified parameters |
| `422` | Invalid duration format |
| `503` | Service unavailable |

---

### 5.2. `GET /api/v1/delegation/precount/bandwidth`

**Summary:** Pre-calculate Bandwidth Cost  
**Description:** Calculates the estimated cost for delegating bandwidth based on volume, duration, and currency.

**Parameters and response format** are identical to [`/precount/energy`](#51-get-apiv1delegationprecountenergy), but for bandwidth resources.

---

### 5.3. `GET /api/v1/delegation/precount/batchenergy`

**Summary:** Pre-calculate Batch Energy Cost  
**Description:** Calculates the estimated cost for batch energy delegation with optional address activation.

**Parameters and response format** are identical to [`/precount/energy`](#51-get-apiv1delegationprecountenergy). The total cost may include address activation charges.

---

### 5.4. `POST /api/v1/delegation/buyenergy`

**Summary:** Request Energy Delegation  
**Description:** Accepts a request to delegate energy and queues it for processing.

**Authentication:** Required

**Process flow:**
1. Order is validated and created (synchronous)
2. Payment is charged from user balance
3. Order is queued for delegation (asynchronous)
4. Client receives `task_id` for status tracking
5. Use `GET /delegation/status/{task_id}` to check progress

**Query parameters:**

| Parameter | Type | Required | Description |
|-----------|------|:--------:|-------------|
| `target` | `string` | Yes | Target TRON address (Base58 format, 34 characters) |
| `volume` | `integer` | Yes | Amount of energy per day (e.g., 65000, 131000) |
| `days` | `string` | Yes | Duration: `1h`, `1d`, `3d`, `7d`, `14d` |
| `currency` | `string` | Yes | Payment currency: `TRX` or `USDT` |

**Response (`202 Accepted`) — `OrderAcceptedResponse`:**

| Field | Type | Description |
|-------|------|-------------|
| `message` | `string` | Status message (`"Task accepted and queued"`) |
| `task_id` | `string` | UUID for order tracking |
| `status` | `string` | Initial status (`"QUEUED"`) |
| `target` | `string` | Target address |
| `volume` | `integer` | Volume |
| `days` | `string` | Duration |
| `currency` | `string` | Currency |
| `resource_type` | `string` | Resource type (`"ENERGY"`) |
| `balances` | `object \| null` | Current user balances after charge |

**Example response:**
```json
{
  "message": "Task accepted and queued",
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "QUEUED",
  "target": "TLsV52sRDL79HXGGm9yzwKibb6BeruhUzy",
  "volume": 65000,
  "days": "3d",
  "currency": "TRX",
  "resource_type": "ENERGY",
  "balances": {
    "TRX": "1349.25",
    "USDT": "320.00"
  }
}
```

**Error codes:**

| Code | Description |
|------|-------------|
| `401` | Invalid or expired token |
| `422` | Invalid parameters or insufficient funds |
| `428` | Target address not activated (send 1 TRX to activate first) |
| `503` | Service temporarily unavailable |

---

### 5.5. `POST /api/v1/delegation/batchenergy`

**Summary:** Request Batch Energy Delegation  
**Description:** Accepts a request to delegate energy using batch processing with optional automatic address activation.

**Parameters** are identical to [`/buyenergy`](#54-post-apiv1delegationbuyenergy).

**Response (`202 Accepted`)** — `OrderAcceptedResponse` format (same structure).

**Error codes:**

| Code | Description |
|------|-------------|
| `401` | Invalid or expired token |
| `422` | Invalid input parameters |
| `503` | Failed to queue order request |

---

### 5.6. `POST /api/v1/delegation/buybandwidth`

**Summary:** Request Bandwidth Delegation  
**Description:** Accepts a request to delegate bandwidth and queues it for processing.

**Parameters** are identical to [`/buyenergy`](#54-post-apiv1delegationbuyenergy), but `volume` represents bandwidth amount.

**Response (`202 Accepted`)** — `OrderAcceptedResponse` with `resource_type = "BANDWIDTH"`.

**Error codes:**

| Code | Description |
|------|-------------|
| `401` | Invalid or expired token |
| `422` | Invalid input parameters |
| `503` | Failed to queue order request |

---

### 5.7. `GET /api/v1/delegation/status/{task_id}`

**Summary:** Get Order Status by Task ID  
**Description:** Retrieves the current status of a previously submitted order.

**Authentication:** Required

**Path parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `task_id` | `string` | Task UUID received when the order was created |

**Response (`200 OK`) — `OrderStatusResponse`:**

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | `string` | Task UUID |
| `status` | `string` | Current status (see below) |
| `details` | `object \| null` | Additional details (includes `error_message` when `FAILED`) |
| `error_code` | `string \| null` | Structured error code (only when `status = FAILED`) |

**Status values:**

| Status | Description |
|--------|-------------|
| `PENDING` | Order created, awaiting processing |
| `PROCESSING` | Orchestration started |
| `ACTIVE` | Delegation confirmed, resource is active |
| `COMPLETED` | Order finished (duration expired or resource released) |
| `FAILED` | Orchestration or delegation failed (see `error_code`) |
| `CANCELLED` | Order cancelled by user or system |
| `unknown` | Technical error (e.g., service timeout) |

**`error_code` values (when `status = FAILED`):**

| Code | Category | Description | Retryable |
|------|----------|-------------|:---------:|
| `INVALID_ADDRESS` | Validation | Invalid TRON address format | No |
| `INVALID_PARAMETERS` | Validation | Invalid request parameters | No |
| `INSUFFICIENT_BALANCE` | Resource | Provider balance insufficient | Yes |
в| `RATE_LIMIT_EXCEEDED` | Temporary | Too many requests | Yes (auto-retry) |
| `SERVICE_UNAVAILABLE` | Temporary | Service temporarily unavailable | Yes |
| `REQUEST_TIMEOUT` | Temporary | Request processing timeout | Yes |
| `PROCESSING_FAILED` | Processing | Failed after multiple retry attempts | Contact support |
| `CONFIGURATION_ERROR` | Processing | Service configuration issue | Contact support |
| `UNKNOWN_ERROR` | Processing | Unknown error | Contact support |

**Example response (successful order):**
```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "ACTIVE",
  "details": {
    "target_address": "TLsV52sRDL79HXGGm9yzwKibb6BeruhUzy",
    "volume": 65000,
    "txid": "a1b2c3d4..."
  },
  "error_code": null
}
```

**Example response (failed order):**
```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "FAILED",
  "details": {
    "error_message": "This address has received energy delegation recently.",
    "target_address": "TXxxx...",
    "volume": 65000
  },
  "error_code": "DUPLICATE_REQUEST"
}
```

**HTTP error codes:**

| Code | Description |
|------|-------------|
| `401` | Invalid or expired token |
| `404` | Order not found for the given Task ID |
| `503` | Account Service unavailable |

---

### 5.8. `GET /api/v1/delegation/fee`

**Summary:** Calculate USDT Transfer Fee  
**Description:** Calculates the energy required and TRX fee for a USDT transfer to a specific address.

**Authentication:** Required

**Query parameters:**

| Parameter | Type | Required | Description |
|-----------|------|:--------:|-------------|
| `receiver_address` | `string` | Yes | Recipient TRON address (Base58 format, 34 characters) |

**Response (`200 OK`) — `FeeCalculationResponse`:**

| Field | Type | Description |
|-------|------|-------------|
| `receiver_address` | `string` | Recipient address |
| `energy_required` | `integer` | Energy required for the transfer |
| `trx_burned` | `float` | TRX cost (burned if no energy available) |
| `is_new_address` | `boolean` | Whether new address activation fee is included |

**Example response:**
```json
{
  "receiver_address": "TQjaZ9FD473QBTdUzMLmSyoGB6Yz1CGpux",
  "energy_required": 31664,
  "trx_burned": 6.649,
  "is_new_address": false
}
```

**Error codes:**

| Code | Description |
|------|-------------|
| `401` | Invalid or expired token |
| `422` | Invalid TRON address format |
| `503` | TronClientService unavailable |

---

## 6. Address Activation

**Tag:** `Address Activation`  
**Prefix:** `/api/v1/activation`

### 6.1. `POST /api/v1/activation/activate`

**Summary:** Request Address Activation  
**Description:** Submits a request to activate a TRON address. The minimum required TRX (~1.5 TRX) is sent to the target address to make it active on the TRON network.

**Authentication:** Required

**Query parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|:--------:|:-------:|-------------|
| `address` | `string` | Yes | — | TRON address to activate (Base58 format) |
| `currency` | `string` | No | `TRX` | Payment currency: `TRX` or `USDT` |

**Response (`202 Accepted`) — `ActivationAcceptedResponse`:**

| Field | Type | Description |
|-------|------|-------------|
| `message` | `string` | Status message (`"Activation task accepted and queued"`) |
| `task_id` | `string` | UUID for tracking |
| `status` | `string` | Initial status (`"QUEUED"`) |
| `target` | `string` | Target address |
| `balances` | `object \| null` | Current user balances after charge |

**Example response:**
```json
{
  "message": "Activation task accepted and queued",
  "task_id": "660e8400-f29b-51d4-b716-556655440000",
  "status": "QUEUED",
  "target": "TNPeeaaFB7K9cmo4uQpcU32zGK8G1NYqeL",
  "balances": {
    "TRX": "1498.50",
    "USDT": "320.00"
  }
}
```

**Error codes:**

| Code | Description |
|------|-------------|
| `401` | Invalid or expired token |
| `409` | Address already activated or duplicate request |
| `422` | Invalid address, insufficient funds, or validation error |
| `500` | Internal server error |
| `503` | Service unavailable, timeout, or configuration error |

---

### 6.2. `GET /api/v1/activation/cost`

**Summary:** Get Activation Cost  
**Description:** Returns the current cost for activating a TRON address.

**Authentication:** Required

**Response (`200 OK`):**

| Field | Type | Description |
|-------|------|-------------|
| `cost_trx` | `decimal` | Activation cost in TRX |
| `description` | `string` | Description |
| `currency` | `string` | Currency (`"TRX"`) |

**Example response:**
```json
{
  "cost_trx": "1.5",
  "description": "Minimum TRX required to activate a TRON address",
  "currency": "TRX"
}
```

**Error codes:**

| Code | Description |
|------|-------------|
| `503` | Failed to retrieve activation cost |

---

## 7. TRON Node — Blockchain Proxy

**Tag:** `TRON Node`  
**Prefix:** `/api/v1/node`

> **Authentication:** Required (`X-API-Key` or `Bearer Token`)  
> **Rate Limit:** 1 request per second per IP address

### 7.1. `GET /api/v1/node/wallet/getaccountresource`

**Summary:** Get Account Resource (Raw TRON API)  
**Description:** Proxy to TRON node `wallet/getaccountresource` API. Returns raw account resource information from the TRON blockchain.

**Query parameters:**

| Parameter | Type | Required | Description |
|-----------|------|:--------:|-------------|
| `address` | `string` | Yes | TRON address (Base58 format, exactly 34 characters) |

**Key response fields (`200 OK`):**

| Field | Description |
|-------|-------------|
| `EnergyLimit` | Total energy limit (own staking + delegated) |
| `EnergyUsed` | Energy consumed |
| `TotalEnergyLimit` | Total energy available in the network |
| `TotalEnergyWeight` | Total TRX staked for energy network-wide |
| `NetLimit` | Bandwidth from staking |
| `NetUsed` | Bandwidth used |
| `freeNetLimit` | Free bandwidth (1500 for active accounts) |
| `freeNetUsed` | Free bandwidth used |
| `tronPowerLimit` | Available TRON Power |

**Formula: TRX to Energy Conversion:**
```
Energy = Staked_TRX × TotalEnergyLimit / TotalEnergyWeight
```

**Example response:**
```json
{
  "EnergyLimit": 196878,
  "EnergyUsed": 50000,
  "freeNetLimit": 1500,
  "NetLimit": 3500,
  "NetUsed": 100,
  "TotalEnergyLimit": 90000000000,
  "TotalEnergyWeight": 9500000000,
  "TotalNetLimit": 43200000000,
  "TotalNetWeight": 4500000000,
  "tronPowerLimit": 1000
}
```

**Error codes:**

| Code | Description |
|------|-------------|
| `400` | Invalid address format |
| `401` | Invalid or expired token / API key |
| `404` | Address not found on-chain |
| `429` | Rate limit exceeded (1 req/sec). `Retry-After` header included |
| `503` | TRON node unavailable |

---

### 7.2. `GET /api/v1/node/wallet/getaccount`

**Summary:** Get Account Info (Raw TRON API)  
**Description:** Proxy to TRON node `wallet/getaccount` API. Returns full account information from the TRON blockchain.

**Query parameters:**

| Parameter | Type | Required | Description |
|-----------|------|:--------:|-------------|
| `address` | `string` | Yes | TRON address (Base58 format, exactly 34 characters) |

**Key response fields (`200 OK`):**

| Field | Description |
|-------|-------------|
| `address` | Account address (hex format) |
| `balance` | Free TRX balance in SUN (1 TRX = 1,000,000 SUN) |
| `create_time` | Account creation timestamp (ms) |
| `frozenV2` | Array of Stake 2.0 own staking entries (`type` + `amount`) |
| `account_resource.delegated_frozenV2_balance_for_energy` | TRX delegated to others for energy (SUN) |
| `delegated_frozenV2_balance_for_bandwidth` | TRX delegated to others for bandwidth (SUN) |
| `acquired_delegated_frozenV2_balance_for_energy` | TRX received for energy from others (SUN) |
| `acquired_delegated_frozenV2_balance_for_bandwidth` | TRX received for bandwidth from others (SUN) |

> All SUN values: divide by 1,000,000 to get TRX.

**Example response:**
```json
{
  "address": "41...",
  "balance": 1000000000,
  "create_time": 1609459200000,
  "frozenV2": [
    {"type": "BANDWIDTH", "amount": 50000000000},
    {"type": "ENERGY", "amount": 100000000000}
  ],
  "account_resource": {
    "delegated_frozenV2_balance_for_energy": 20000000000
  },
  "delegated_frozenV2_balance_for_bandwidth": 10000000000,
  "acquired_delegated_frozenV2_balance_for_energy": 50000000000
}
```

**Error codes:**

| Code | Description |
|------|-------------|
| `400` | Invalid address format |
| `401` | Invalid or expired token / API key |
| `404` | Address not found on-chain (not activated) |
| `429` | Rate limit exceeded (1 req/sec) |
| `503` | TRON node unavailable |

---

## 8. Webhook Documentation

**Tag:** `Webhook Documentation`  
**Prefix:** `/api/v1/webhooks`

### `GET /api/v1/webhooks/setup-guide`

**Summary:** Complete Webhook Documentation and Setup Guide  
**Description:** Returns the webhook integration guide.

**Authentication:** Not required

**Response (`200 OK`):**

```json
{
  "success": true,
  "message": "Webhook integration guide available in endpoint description above"
}
```

> All detailed webhook documentation is provided in the OpenAPI endpoint description and reproduced below.

---

### 8.1. Webhook Payload Format

The system sends HTTP notifications to your server when events occur.

- **Method:** `POST`
- **Content-Type:** `application/json`

**Payload structure:**

| Field | Type | Description |
|-------|------|-------------|
| `user_id` | `integer` | Telegram user ID |
| `notification_code` | `string` | Unique notification type code |
| `data` | `object` | Notification-specific data |
| `timestamp` | `string` | ISO 8601 timestamp in UTC |
| `source_service` | `string` | Service that generated the notification |
| `correlation_id` | `string \| null` | Optional correlation ID for tracking |

**Example payload:**
```json
{
  "user_id": 12345,
  "notification_code": "DEPOSIT_CONFIRMED",
  "data": {
    "amount": "150.75",
    "currency": "TRX",
    "new_balance": "1200.50",
    "old_balance": "1049.75",
    "tx_id": "a1b2c3d4e5f6789012345678901234567890abcdef"
  },
  "timestamp": "2024-01-20T15:30:45.123456Z",
  "source_service": "account_service",
  "correlation_id": "dep_20241220_150230_12345"
}
```

---

### 8.2. Webhook HTTP Headers

Each webhook request includes the following headers:

```
Content-Type: application/json
User-Agent: Webhook/1.0
X-Webhook-Signature: sha256=<hmac_signature>
```

The `X-Webhook-Signature` header contains the HMAC-SHA256 signature for payload verification.

---

### 8.3. Signature Verification

| Parameter | Value |
|-----------|-------|
| **Algorithm** | HMAC-SHA256 |
| **Key** | Your API Key (from user profile) |
| **Input** | Raw JSON payload (sorted keys, no extra spaces) |
| **Format** | `sha256=<calculated_signature>` |

**Python example:**
```python
import hmac
import hashlib

def verify_webhook(payload_json: str, signature: str, api_key: str) -> bool:
    expected_signature = signature.replace('sha256=', '')
    calculated = hmac.new(
        api_key.encode('utf-8'),
        payload_json.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected_signature, calculated)
```

**JavaScript example:**
```javascript
const crypto = require('crypto');

function verifyWebhook(payloadJson, signature, apiKey) {
    const expectedSignature = signature.replace('sha256=', '');
    const calculated = crypto
        .createHmac('sha256', apiKey)
        .update(payloadJson, 'utf8')
        .digest('hex');
    
    return crypto.timingSafeEqual(
        Buffer.from(expectedSignature, 'hex'),
        Buffer.from(calculated, 'hex')
    );
}
```

---

### 8.4. Notification Codes (`notification_code`)

#### Financial Operations

| Code | Description | Fields in `data` |
|------|-------------|-----------------|
| `DEPOSIT_CONFIRMED` | Deposit confirmed | `amount`, `currency`, `new_balance`, `old_balance`, `tx_id` |
| `DEPOSIT_FAILED` | Deposit failed | `amount`, `currency`, `error_message` |

#### Order Operations

| Code | Description | Fields in `data` |
|------|-------------|-----------------|
| `ORDER_PROCESSING_SUCCESS` | Order processed successfully | `order_id`, `resource_type`, `volume`, `duration`, `target_address`, `txid`, `start_date`, `end_date`, `source_service` |
| `ORDER_CREATION_INSUFFICIENT_FUNDS` | Insufficient funds at creation | `task_id`, `required_amount`, `available_amount`, `currency` |
| `ORDER_PROCESSING_FAILED` | Order processing failed | `order_id`, `resource_type`, `target_address`, `error_message` |
| `ORDER_CREATION_ERROR_PRE_SUBMIT` | Order creation error | `task_id`, `error_message` |

#### Delegation Operations

| Code | Description | Fields in `data` |
|------|-------------|-----------------|
| `RESOURCE_DELEGATION_FAILED_ORCHESTRATOR` | Resource delegation failed | `order_id`, `operation_id`, `error_message` |
| `BATCHENERGY_ACTIVATION_INSUFFICIENT_FUNDS` | Insufficient funds for activation | `order_id`, `activation_cost`, `activation_currency`, `target_address`, `trx_balance_before`, `usdt_balance_before`, `balance_after` |

---

### 8.5. Setup Steps (via Telegram Bot)

1. **Get your API Key** — Settings → Profile → copy your personal API key
2. **Prepare your HTTPS server** — e.g., `https://your-domain.com/webhook`
   - HTTPS required (except localhost for testing)
   - Must respond with 2xx status codes
   - Response timeout: 10 seconds
3. **Register webhook URL** — Settings → Webhooks → enter your URL and enable notifications
4. **Implement signature verification** — use your API Key and the `X-Webhook-Signature` header (code examples above)
5. **Configure notification types** — Settings → Webhooks → Notification Settings → enable/disable specific types
6. **Test your integration** — Settings → Webhooks → Test Webhook → send a test notification

---

### 8.6. Security Considerations

- Always verify webhook signatures
- Use HTTPS endpoints in production
- Implement idempotency to handle duplicate webhooks
- Set up proper error handling and logging
- Consider rate limiting on your webhook endpoint

---

### 8.7. Retry Policy

| Parameter | Value |
|-----------|-------|
| Max attempts | 3 |
| Delays | [1, 4, 16] seconds (exponential backoff) |
| Timeout | 10 seconds |
| Success codes | 200–299 |

---

## Common HTTP Response Codes

| Code | Description |
|------|-------------|
| `200` | Successful request |
| `202` | Request accepted and queued for processing |
| `400` | Bad request |
| `401` | Authentication failed |
| `404` | Resource not found |
| `409` | Conflict (duplicate request) |
| `422` | Validation error |
| `428` | Precondition required (address not activated) |
| `429` | Rate limit exceeded |
| `500` | Internal server error |
| `503` | Service temporarily unavailable |

---