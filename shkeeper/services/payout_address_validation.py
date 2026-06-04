from __future__ import annotations

import base64
import hashlib
import re

from shkeeper.services.payout_errors import PayoutRequestError


_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BASE58_INDEX = {char: index for index, char in enumerate(_BASE58_ALPHABET)}
_ETH_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_TON_RAW_ADDRESS_RE = re.compile(r"^-?\d+:[0-9a-fA-F]{64}$")
_TON_USER_FRIENDLY_RE = re.compile(r"^[A-Za-z0-9_+/=-]+$")
_TON_USER_FRIENDLY_FLAGS = {0x11, 0x51, 0x91, 0xD1}


def validate_payout_destination(network: str, destination: str) -> str:
    if not destination:
        raise _invalid_destination()
    normalized_network = str(network).upper()
    if normalized_network == "TRON":
        if not _is_valid_tron_base58check(destination):
            raise _invalid_destination()
    elif normalized_network == "ETH":
        if not _ETH_ADDRESS_RE.match(destination):
            raise _invalid_destination()
    elif normalized_network == "TON":
        if not _is_valid_ton_address(destination):
            raise _invalid_destination()
    else:
        raise PayoutRequestError(
            "Unsupported payout network",
            code="UNSUPPORTED_NETWORK",
        )
    return destination


def _invalid_destination():
    return PayoutRequestError(
        "destination is invalid for payout network",
        code="INVALID_DESTINATION",
    )


def _base58_decode(value: str) -> bytes | None:
    number = 0
    for char in value:
        index = _BASE58_INDEX.get(char)
        if index is None:
            return None
        number = number * 58 + index
    decoded = number.to_bytes((number.bit_length() + 7) // 8, "big")
    leading_zeroes = len(value) - len(value.lstrip("1"))
    return b"\x00" * leading_zeroes + decoded


def _is_valid_tron_base58check(value: str) -> bool:
    decoded = _base58_decode(value)
    if decoded is None or len(decoded) != 25:
        return False
    payload = decoded[:-4]
    checksum = decoded[-4:]
    if not payload or payload[0] != 0x41:
        return False
    expected = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    return checksum == expected


def _is_valid_ton_address(value: str) -> bool:
    if _TON_RAW_ADDRESS_RE.match(value):
        return True
    if len(value) != 48 or not _TON_USER_FRIENDLY_RE.match(value):
        return False
    try:
        decoded = base64.urlsafe_b64decode(value)
    except Exception:
        return False
    if len(decoded) != 36:
        return False
    payload = decoded[:-2]
    checksum = decoded[-2:]
    if payload[0] not in _TON_USER_FRIENDLY_FLAGS:
        return False
    expected = _ton_crc16(payload).to_bytes(2, "big")
    return checksum == expected


def _ton_crc16(payload: bytes) -> int:
    crc = 0
    for byte in payload:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc
