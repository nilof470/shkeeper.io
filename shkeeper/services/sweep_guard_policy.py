GUARDED_SWEEP_NETWORKS = {
    "USDT": "TRON",
    "ETH-USDT": "ETHEREUM",
}

NETWORK_ALIASES = {
    "BEP20": "BSC",
    "BEP-20": "BSC",
    "BINANCE": "BSC",
    "BSC": "BSC",
    "BNB": "BSC",
    "ETH": "ETHEREUM",
    "ERC20": "ETHEREUM",
    "ERC-20": "ETHEREUM",
    "ETHEREUM": "ETHEREUM",
    "TON": "TON",
    "TON-USDT": "TON",
    "TRC20": "TRON",
    "TRC-20": "TRON",
    "TRON": "TRON",
}


def normalize_crypto(crypto):
    return str(crypto or "").strip().upper()


def normalize_sweep_network(network):
    value = str(network or "").strip().upper()
    return NETWORK_ALIASES.get(value, value)


def is_sweep_guarded_crypto(crypto):
    return normalize_crypto(crypto) in GUARDED_SWEEP_NETWORKS


def expected_sweep_network(crypto):
    return GUARDED_SWEEP_NETWORKS.get(normalize_crypto(crypto))


def network_matches_guarded_crypto(crypto, network):
    expected = expected_sweep_network(crypto)
    if expected is None:
        return True
    return normalize_sweep_network(network) == expected


def uses_case_insensitive_address(crypto):
    return normalize_sweep_network(expected_sweep_network(crypto)) in {"ETHEREUM", "BSC"}


def addresses_match(crypto, left, right):
    if left is None or right is None:
        return False
    if uses_case_insensitive_address(crypto):
        return str(left).lower() == str(right).lower()
    return left == right
