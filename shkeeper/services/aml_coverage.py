SUPPORTED_STATUS = "amlbot_supported"
UNSUPPORTED_STATUS = "unsupported_manual_review"


def _supported(asset, network):
    return {
        "status": SUPPORTED_STATUS,
        "provider": "amlbot",
        "asset": asset,
        "network": network,
        "reason": None,
    }


def _unsupported(reason="unsupported_asset"):
    return {
        "status": UNSUPPORTED_STATUS,
        "provider": "amlbot",
        "asset": None,
        "network": None,
        "reason": reason,
    }


AML_COVERAGE = {
    "BTC": _supported("BTC", "BTC"),
    "LTC": _supported("LTC", "LTC"),
    "DOGE": _supported("DOGE", "DOGE"),
    "ETH": _supported("ETH", "ETHEREUM"),
    "ETH-USDT": _supported("ETH", "ETHEREUM"),
    "ETH-USDC": _supported("ETH", "ETHEREUM"),
    "ETH-PYUSD": _supported("ETH", "ETHEREUM"),
    "TRX": _supported("TRX", "TRON"),
    "USDT": _supported("TRX", "TRON"),
    "USDC": _supported("TRX", "TRON"),
    "SOL": _supported("SOL", "SOLANA"),
    "SOLANA-USDT": _supported("SOL", "SOLANA"),
    "SOLANA-USDC": _supported("SOL", "SOLANA"),
    "SOLANA-PYUSD": _supported("SOL", "SOLANA"),
    "BNB": _unsupported(),
    "BNB-USDT": _unsupported(),
    "BNB-USDC": _unsupported(),
    "MATIC": _unsupported(),
    "POLYGON-USDT": _unsupported(),
    "POLYGON-USDC": _unsupported(),
    "AVAX": _unsupported(),
    "AVALANCHE-USDT": _unsupported(),
    "AVALANCHE-USDC": _unsupported(),
    "XRP": _unsupported(),
    "ARBETH": _unsupported(),
    "ARB-USDC": _unsupported(),
    "ARB-PYUSD": _unsupported(),
    "ARB-TOKEN": _unsupported(),
    "OPETH": _unsupported(),
    "OP-USDT": _unsupported(),
    "OP-USDC": _unsupported(),
    "OP-TOKEN": _unsupported(),
    "TON": _unsupported(),
    "TON-USDT": _unsupported(),
    "FIRO": _unsupported(),
    "FIRO-SPARK": _unsupported("limited_analysis_requires_review"),
    "MONERO": _unsupported("limited_analysis_requires_review"),
    "XMR": _unsupported("limited_analysis_requires_review"),
    "BTC-LIGHTNING": _unsupported("limited_analysis_requires_review"),
}


def get_coverage_policy(crypto_symbol):
    return AML_COVERAGE.get(str(crypto_symbol).upper(), _unsupported())
