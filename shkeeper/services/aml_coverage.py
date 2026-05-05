import os

from flask import current_app, has_app_context


SUPPORTED_STATUS = "provider_supported"
UNSUPPORTED_STATUS = "unsupported_manual_review"
DEFAULT_PROVIDER = "koinkyt"


def _current_provider():
    if has_app_context():
        return current_app.config.get("AML_PROVIDER", DEFAULT_PROVIDER)
    return os.environ.get("AML_PROVIDER", os.environ.get("CURRENT_PROVIDER", DEFAULT_PROVIDER))


def _supported(provider, asset, network):
    return {
        "status": SUPPORTED_STATUS,
        "provider": provider,
        "asset": asset,
        "network": network,
        "reason": None,
    }


def _unsupported(provider, reason="unsupported_asset"):
    return {
        "status": UNSUPPORTED_STATUS,
        "provider": provider,
        "asset": None,
        "network": None,
        "reason": reason,
    }


def _coverage(provider, supported):
    known_symbols = {
        "BTC",
        "LTC",
        "DOGE",
        "ETH",
        "ETH-USDT",
        "ETH-USDC",
        "ETH-PYUSD",
        "TRX",
        "USDT",
        "USDC",
        "SOL",
        "SOLANA-USDT",
        "SOLANA-USDC",
        "SOLANA-PYUSD",
        "BNB",
        "BNB-USDT",
        "BNB-USDC",
        "MATIC",
        "POLYGON-USDT",
        "POLYGON-USDC",
        "AVAX",
        "AVALANCHE-USDT",
        "AVALANCHE-USDC",
        "XRP",
        "ARBETH",
        "ARB-USDC",
        "ARB-PYUSD",
        "ARB-TOKEN",
        "OPETH",
        "OP-USDT",
        "OP-USDC",
        "OP-TOKEN",
        "TON",
        "TON-USDT",
        "FIRO",
        "FIRO-SPARK",
        "MONERO",
        "XMR",
        "BTC-LIGHTNING",
    }
    coverage = {symbol: _unsupported(provider) for symbol in known_symbols}
    coverage.update(supported)
    for symbol in ("FIRO-SPARK", "MONERO", "XMR", "BTC-LIGHTNING"):
        coverage[symbol] = _unsupported(provider, "limited_analysis_requires_review")
    return coverage


KOINKYT_COVERAGE = _coverage(
    "koinkyt",
    {
        "BTC": _supported("koinkyt", "BTC", "BTC"),
        "ETH": _supported("koinkyt", "ETH", "ETHEREUM"),
        "ETH-USDT": _supported("koinkyt", "USDT", "ETHEREUM"),
        "ETH-USDC": _supported("koinkyt", "USDC", "ETHEREUM"),
        "TRX": _supported("koinkyt", "TRX", "TRON"),
        "USDT": _supported("koinkyt", "USDT", "TRON"),
        "USDC": _supported("koinkyt", "USDC", "TRON"),
    },
)

AMLBOT_COVERAGE = _coverage(
    "amlbot",
    {
        "BTC": _supported("amlbot", "BTC", "BTC"),
        "LTC": _supported("amlbot", "LTC", "LTC"),
        "DOGE": _supported("amlbot", "DOGE", "DOGE"),
        "ETH": _supported("amlbot", "ETH", "ETHEREUM"),
        "ETH-USDT": _supported("amlbot", "USDT", "ETHEREUM"),
        "ETH-USDC": _supported("amlbot", "USDC", "ETHEREUM"),
        "ETH-PYUSD": _supported("amlbot", "PYUSD", "ETHEREUM"),
        "TRX": _supported("amlbot", "TRX", "TRON"),
        "USDT": _supported("amlbot", "USDT", "TRON"),
        "USDC": _supported("amlbot", "USDC", "TRON"),
        "SOL": _supported("amlbot", "SOL", "SOLANA"),
        "SOLANA-USDT": _supported("amlbot", "USDT", "SOLANA"),
        "SOLANA-USDC": _supported("amlbot", "USDC", "SOLANA"),
        "SOLANA-PYUSD": _supported("amlbot", "PYUSD", "SOLANA"),
    },
)

AML_COVERAGE_BY_PROVIDER = {
    "koinkyt": KOINKYT_COVERAGE,
    "amlbot": AMLBOT_COVERAGE,
}

# Backward-compatible default map used by tests and static coverage checks.
AML_COVERAGE = KOINKYT_COVERAGE


def get_coverage_policy(crypto_symbol, provider=None):
    provider = provider or _current_provider()
    coverage = AML_COVERAGE_BY_PROVIDER.get(provider, KOINKYT_COVERAGE)
    return coverage.get(str(crypto_symbol).upper(), _unsupported(provider))
