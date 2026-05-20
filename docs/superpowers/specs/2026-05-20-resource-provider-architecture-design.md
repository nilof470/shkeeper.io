# Resource Provider Architecture for TRON Bandwidth and Energy

Date: 2026-05-20
Status: Approved design, pending implementation plan
Target implementation repo: `../tron-shkeeper`
Source docs:
- `docs/openapi-refeebot.json`
- `docs/openapi-profeex.json`
- `docs/PROFEEX_API_DOCS_EN.md`

## Context

The current TRON sweep flow in `../tron-shkeeper` provisions resources before
broadcasting a TRC-20 transfer from an onetime wallet. Energy can come from
local staking or re:Fee. A recent bandwidth rental change added
`RefeeEnergyProvider.acquire_bandwidth`, which works but mixes two resource
capabilities in a class selected by `ENERGY_SOURCE`.

The next requirement is to add ProfeeX as a bandwidth provider while keeping
energy on re:Fee for now. ProfeeX may later become an energy provider too. The
code does not need backward compatibility with the current unshipped config.

## Goals

- Select energy and bandwidth providers independently.
- Preserve the old no-rental bandwidth behavior when bandwidth rental is
  disabled.
- Allow a provider to support one resource now and another resource later.
- Keep `transfer_trc20_from` focused on sweep orchestration, not provider API
  details.
- Avoid paying for energy when the transfer cannot be broadcast because the
  onetime wallet lacks bandwidth.

## Non-goals

- No provider fallback chain in this phase. A configured provider either
  succeeds or the sweep attempt stops.
- No ProfeeX webhook integration in this phase.
- No ProfeeX activation flow in this phase.
- No ProfeeX flash bandwidth or flash energy in this phase.

## Configuration

Replace the current resource source settings with explicit provider settings:

```python
ENERGY_PROVIDER: Literal["staking", "refee", "profeex"] = "staking"
BANDWIDTH_PROVIDER: Literal["disabled", "refee", "profeex"] = "disabled"
REFEE: Json[RefeeConfig] | None = None
PROFEEX: Json[ProfeeXConfig] | None = None
```

`REFEE_RENT_BANDWIDTH` is removed. Its behavior is represented by
`BANDWIDTH_PROVIDER`:

- `disabled`: use only bandwidth already available on the onetime wallet. If
  the wallet does not currently have enough bandwidth, stop this sweep attempt
  and let later scanner runs try again after TRON daily resource recovery.
- `refee`: rent bandwidth from re:Fee only when current wallet bandwidth is
  insufficient.
- `profeex`: rent bandwidth from ProfeeX only when current wallet bandwidth is
  insufficient.

`ENERGY_SOURCE` is replaced by `ENERGY_PROVIDER`. `staking` continues to use
the existing local delegation path, while `refee` uses re:Fee. `profeex` is
reserved by the config and factory for the future ProfeeX energy integration,
but implementation may initially reject it until the energy provider class is
added.

`ProfeeXConfig`:

```python
class ProfeeXConfig(BaseModel):
    api_base_url: str = "https://api.profeex.io/api/v1"
    api_key: SecretStr
    currency: Literal["TRX", "USDT"] = "TRX"
    bandwidth_duration_label: Literal["1h", "1d", "3d", "7d", "14d"] = "1h"
    min_bandwidth_order_amount: int = 350
    max_bandwidth_order_amount: int = 10_000
    energy_duration_label: Literal["1h", "1d", "3d", "7d", "14d"] = "1h"
    min_energy_order_amount: int = 64_285
    max_energy_order_amount: int = 3_000_000
    poll_interval_sec: float = 2.0
    timeout_sec: int = 60
```

Validation rules:

- `PROFEEX` is required when `BANDWIDTH_PROVIDER == "profeex"` or
  `ENERGY_PROVIDER == "profeex"`.
- `REFEE` is required when `BANDWIDTH_PROVIDER == "refee"` or
  `ENERGY_PROVIDER == "refee"`.
- ProfeeX base URL must be HTTPS.
- ProfeeX bandwidth order amount must remain inside the OpenAPI range
  `350..10_000`.

## Architecture

Introduce resource-specific provider interfaces. Concrete services may
implement one or both interfaces.

```python
class EnergyProvider(Protocol):
    def acquire_energy(
        self,
        receiver: str,
        energy_to_provision: int,
        account_resource: dict,
        *,
        minimum_energy_required: int | None = None,
    ) -> bool: ...

    def release_energy(self, receiver: str) -> None: ...


class BandwidthProvider(Protocol):
    def acquire_bandwidth(self, receiver: str, bandwidth_required: int) -> bool: ...
```

Provider factories:

```python
get_energy_provider(tron_client=None) -> EnergyProvider
get_bandwidth_provider(tron_client=None) -> BandwidthProvider | None
```

Concrete providers:

- `StakingEnergyProvider`: supports energy only and keeps the current local
  delegation and undelegation behavior.
- `RefeeProvider` or split `RefeeEnergyProvider` plus `RefeeBandwidthProvider`:
  supports re:Fee energy and bandwidth using existing re:Fee API behavior.
- `ProfeeXBandwidthProvider`: supports ProfeeX ordinary bandwidth delegation.
- `ProfeeXEnergyProvider`: future class for ProfeeX energy delegation.

The implementation can keep files small by moving provider code into a package:

```text
app/resource_providers/
  __init__.py
  base.py
  factory.py
  staking.py
  refee.py
  profeex.py
```

If the implementation keeps the existing `app/energy_provider.py` during the
first refactor, the public factory and interface boundaries should still match
this design so later file movement is mechanical.

## Sweep Data Flow

`transfer_trc20_from` keeps the current high-level transfer flow. Resource
handling changes to:

1. Determine whether the sweep uses an energy provider:
   - `ENERGY_PROVIDER == "staking"` uses local delegation when
     `ENERGY_DELEGATION_MODE` is enabled.
   - `ENERGY_PROVIDER in {"refee", "profeex"}` uses the configured external
     provider.
2. Before estimating or buying energy, call an `ensure_onetime_bandwidth`
   helper with `BANDWIDTH_PER_TRC20_TRANSFER_CALL`.
3. `ensure_onetime_bandwidth` first checks `has_free_bw`.
4. If bandwidth is already sufficient, return success without calling any
   external API.
5. If bandwidth is insufficient and `BANDWIDTH_PROVIDER == "disabled"`, log
   that no external bandwidth rental is configured and return failure. This is
   the old pre-rental behavior.
6. If bandwidth is insufficient and a provider is configured, call
   `get_bandwidth_provider(...).acquire_bandwidth(...)`.
7. After provider success, recheck `has_free_bw`. Continue only if the on-chain
   resource check confirms enough bandwidth.
8. Estimate and provision energy using `get_energy_provider`.
9. Broadcast the TRC-20 transfer.
10. Call `release_energy` only for the energy provider. Bandwidth providers do
    not need release hooks for this phase.

This ordering prevents buying energy for a transfer that cannot be signed and
broadcast without bandwidth.

## ProfeeX Bandwidth API Design

Use ordinary ProfeeX bandwidth delegation:

- `POST /api/v1/delegation/buybandwidth`
- `GET /api/v1/delegation/status/{task_id}`
- Auth header: `X-API-Key: <api key>`
- Base URL: `https://api.profeex.io/api/v1`
- Request parameters are query parameters, not JSON body:
  - `target`: onetime TRON address
  - `volume`: bandwidth amount
  - `days`: duration label
  - `currency`: `TRX` or `USDT`

Order sizing:

- `volume = max(bandwidth_required, PROFEEX.min_bandwidth_order_amount)`
- For the current TRC-20 transfer estimate, `bandwidth_required` is `346`, so
  ProfeeX orders should request `350`.
- If a future required amount exceeds `max_bandwidth_order_amount`, log and
  fail instead of silently clipping the value.

Status handling:

- Initial create response must have `task_id`.
- Pending statuses: `QUEUED`, `PENDING`, `PROCESSING`.
- Success status: `ACTIVE`.
- Failure statuses: `FAILED`, `CANCELLED`, `COMPLETED`, `unknown`.
- On `FAILED`, log `error_code` and `details.error_message` when present.
- On timeout or repeated transport errors until deadline, return failure.
- After `ACTIVE`, always recheck bandwidth on-chain through the configured TRON
  client.

Do not use `flashbandwidth`. ProfeeX docs state that flash bandwidth requires
the target address to have its own staked bandwidth that is currently consumed.
The onetime sweep addresses normally do not satisfy that condition.

## Error Handling

- Missing provider config fails at settings validation where possible.
- HTTP request exceptions are logged without exposing API keys.
- Non-202 order creation responses are treated as provider failure.
- Non-object JSON responses and missing `task_id` are treated as provider
  failure.
- Polling tolerates transient HTTP failures until `timeout_sec`.
- External bandwidth provider failure stops the sweep before energy provisioning.
- No implicit fallback from ProfeeX to re:Fee is added in this phase.

## Testing

Add or update tests for:

- Config validation for `ENERGY_PROVIDER`, `BANDWIDTH_PROVIDER`, `REFEE`, and
  `PROFEEX`.
- `BANDWIDTH_PROVIDER=disabled` preserves old behavior:
  - enough wallet bandwidth continues without external calls;
  - insufficient wallet bandwidth stops before energy provisioning.
- Sufficient bandwidth skips external bandwidth rental for every provider.
- ProfeeX bandwidth order:
  - uses `X-API-Key`;
  - sends query parameters to `/api/v1/delegation/buybandwidth`;
  - requests minimum `350` for the current `346` requirement;
  - polls `/api/v1/delegation/status/{task_id}`;
  - succeeds only on `ACTIVE`;
  - fails on terminal failure statuses and timeout;
  - never calls `flashbandwidth`.
- Mixed-provider flow: bandwidth from ProfeeX and energy from re:Fee.
- Existing re:Fee energy behavior remains covered.
- Existing staking energy behavior remains covered.

## Rollout Notes

Because this work is not in production yet, implementation may remove the
current unshipped `REFEE_RENT_BANDWIDTH` and `ENERGY_SOURCE` names instead of
supporting aliases. Configuration examples should be updated to the new names
in the same implementation phase.
