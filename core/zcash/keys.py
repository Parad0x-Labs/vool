"""The one door a Zcash key comes through: a unified full viewing key is accepted, anything that can spend is refused.

Accepted: a Unified Full Viewing Key (``uview1...`` mainnet, ``uviewtest1...`` testnet) on the configured network,
and only after it DECODES: bech32m checksum, ZIP 316 F4Jumble and padding, and a well-formed item list with at
least one shielded viewing key. Shapes are not guessed from substrings, so a real key whose body happens to contain
``xprv`` is a viewing key like any other. Everything else is refused without being stored, logged or echoed; a value
that looks like a spending key or a seed phrase gets its own refusal so the owner learns why.

The accepted key is kept by VOOL's credential store (:mod:`core.credential_store`): the OS keychain only under the
operator's keychain grant and a vetted backend, otherwise the sealed vault -- the same policy every other VOOL
secret follows. A save is read back before it is reported; a store that cannot keep it refuses.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from core.zcash import config

CREDENTIAL_PREFIX = "zcash-ufvk-"
_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32M_CONST = 0x2BC830A3
_UNIFIED_HRPS = {"uview": config.NETWORK_MAIN, "uviewtest": config.NETWORK_TEST}
#: ZIP 316 FVK item typecodes and their fixed lengths: transparent P2PKH, Sapling, Orchard.
_ITEM_LENGTHS = {0x00: 65, 0x02: 128, 0x03: 96}
_SHIELDED_ITEMS = frozenset({0x02, 0x03})
#: Encodings that carry spending authority. BIP32 private keys are recognised by their PREFIX only; a Sapling
#: extended spending key by its human-readable part (a dash can never occur inside a bech32 body).
_BIP32_PRIVATE_PREFIXES = ("xprv", "tprv", "zprv")
_SAPLING_SPENDING_HRP = "secret-extended-key"
_INCOMING_ONLY_PREFIXES = ("uivk1", "uivktest1")
_SAPLING_VIEWING_PREFIXES = ("zxviews1", "zxviewtestsapling1")
_HEX_SECRET = re.compile(r"^(?:0x)?[0-9a-fA-F]{64,128}$")
_WIF = re.compile(r"^[5KLc9][1-9A-HJ-NP-Za-km-z]{50,51}$")
_NOT_A_KEY = "That is not a Zcash unified full viewing key. Nothing was stored."


class ZcashKeyRefused(ValueError):
    """A key the watch-only lane will not take. ``code`` is stable; the message never contains the key."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ViewingKey:
    encoded: str
    network: str
    kind: str = "unified"

    def __repr__(self) -> str:  # never print the key itself
        return f"ViewingKey(kind={self.kind!r}, network={self.network!r})"


# --- decoding (BIP 350 bech32m, ZIP 316 unified encoding) -----------------------------------------------

def _polymod(values: list[int]) -> int:
    generator = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    chk = 1
    for value in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ value
        for i in range(5):
            chk ^= generator[i] if (top >> i) & 1 else 0
    return chk


def _bech32m_decode(text: str) -> tuple[str, bytes] | None:
    """(hrp, payload) of a valid bech32m string with no length limit (unified encodings exceed 90 chars)."""
    pos = text.rfind("1")
    if pos < 1 or len(text) - pos - 1 < 6:
        return None
    hrp, data_part = text[:pos], text[pos + 1:]
    if any(ch not in _CHARSET for ch in data_part):
        return None
    data = [_CHARSET.index(ch) for ch in data_part]
    expanded = [ord(ch) >> 5 for ch in hrp] + [0] + [ord(ch) & 31 for ch in hrp]
    if _polymod(expanded + data) != _BECH32M_CONST:
        return None
    acc = bits = 0
    out = bytearray()
    for value in data[:-6]:
        acc = (acc << 5) | value
        bits += 5
        if bits >= 8:
            bits -= 8
            out.append((acc >> bits) & 0xFF)
    if bits >= 5 or (acc & ((1 << bits) - 1)):
        return None  # non-zero padding: not a canonical encoding
    return hrp, bytes(out)


def _blake2b(data: bytes, *, size: int, personal: bytes) -> bytes:
    return hashlib.blake2b(data, digest_size=size, person=personal).digest()


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b, strict=True))


def _f4jumble_inverse(message: bytes) -> bytes:
    """ZIP 316 F4Jumble^-1 (the BLAKE2b-based 4-round Feistel the unified encodings are jumbled with)."""
    left_len = min(64, len(message) // 2)
    right_len = len(message) - left_len

    def h(i: int, u: bytes) -> bytes:
        return _blake2b(u, size=left_len, personal=b"UA_F4Jumble_H" + bytes([i, 0, 0]))

    def g(i: int, u: bytes) -> bytes:
        blocks = (right_len + 63) // 64
        stream = b"".join(_blake2b(u, size=64, personal=b"UA_F4Jumble_G" + bytes([i]) + j.to_bytes(2, "little")) for j in range(blocks))
        return stream[:right_len]

    c, d = message[:left_len], message[left_len:]
    y = _xor(c, h(1, d))
    x = _xor(d, g(1, y))
    a = _xor(y, h(0, x))
    b = _xor(x, g(0, a))
    return a + b


def _compact_size(raw: bytes, pos: int) -> tuple[int, int]:
    first = raw[pos]
    if first < 0xFD:
        return first, pos + 1
    width = {0xFD: 2, 0xFE: 4, 0xFF: 8}[first]
    if pos + 1 + width > len(raw):
        raise ValueError("truncated")
    return int.from_bytes(raw[pos + 1:pos + 1 + width], "little"), pos + 1 + width


def _unified_items(hrp: str, payload: bytes) -> list[int]:
    """Typecodes of a decoded unified FVK, or ValueError when it is not one."""
    if not 48 <= len(payload) <= 4194368:
        raise ValueError("length")
    raw = _f4jumble_inverse(payload)
    padding = hrp.encode("ascii").ljust(16, b"\x00")
    if raw[-16:] != padding:
        raise ValueError("padding")
    body, pos, typecodes = raw[:-16], 0, []
    while pos < len(body):
        typecode, pos = _compact_size(body, pos)
        length, pos = _compact_size(body, pos)
        if pos + length > len(body) or _ITEM_LENGTHS.get(typecode, length) != length:
            raise ValueError("item")
        if typecodes and typecode <= typecodes[-1]:
            raise ValueError("order")  # ZIP 316: items in strictly ascending typecode order
        typecodes.append(typecode)
        pos += length
    if not _SHIELDED_ITEMS & set(typecodes):
        raise ValueError("no shielded viewing key")
    return typecodes


def _looks_like_seed_phrase(text: str) -> bool:
    words = [word.strip(".,;") for word in text.replace(",", " ").split()]
    words = [word for word in words if word and not word.isdigit()]
    return len(words) >= 12 and all(word.isalpha() for word in words)


def parse_viewing_key(raw: str, *, expected_network: str | None = None) -> ViewingKey:
    text = str(raw or "").strip().lstrip("﻿").strip()
    if not text:
        raise ZcashKeyRefused("viewing_key_missing", "Give a Zcash unified full viewing key (it starts with uview1).")
    lowered = text.lower()
    if _looks_like_seed_phrase(text):
        raise ZcashKeyRefused("seed_phrase_refused", "That looks like a recovery phrase. VOOL never takes one for Zcash: give the unified full viewing key instead (it starts with uview1). Nothing was stored.")
    if lowered.startswith(_BIP32_PRIVATE_PREFIXES) or _SAPLING_SPENDING_HRP in lowered or _HEX_SECRET.match(text) or _WIF.match(text):
        raise ZcashKeyRefused("spending_key_refused", "That looks like a spending key. VOOL only takes a viewing key for Zcash, which cannot move money. Nothing was stored.")
    if lowered.startswith(_INCOMING_ONLY_PREFIXES):
        raise ZcashKeyRefused("incoming_viewing_key_unsupported", "That is an incoming viewing key; this lane needs the unified full viewing key (it starts with uview1). Nothing was stored.")
    if lowered.startswith(_SAPLING_VIEWING_PREFIXES):
        raise ZcashKeyRefused("viewing_key_unsupported", "That is a Sapling-only viewing key; export the unified full viewing key instead (it starts with uview1). Nothing was stored.")
    if any(ch.isspace() for ch in text) or text not in (lowered, text.upper()):  # bech32m is single-case
        raise ZcashKeyRefused("viewing_key_malformed", _NOT_A_KEY)
    decoded = _bech32m_decode(lowered)
    if decoded is None or decoded[0] not in _UNIFIED_HRPS:
        raise ZcashKeyRefused("viewing_key_malformed", _NOT_A_KEY)
    hrp, payload = decoded
    try:
        _unified_items(hrp, payload)
    except (ValueError, KeyError, IndexError):
        raise ZcashKeyRefused("viewing_key_malformed", _NOT_A_KEY) from None
    net = _UNIFIED_HRPS[hrp]
    want = expected_network or config.network()
    if net != want:
        raise ZcashKeyRefused("viewing_key_wrong_network", f"That viewing key is for {'testnet' if net == config.NETWORK_TEST else 'mainnet'}, but VOOL's Zcash lane is set to {'testnet' if want == config.NETWORK_TEST else 'mainnet'}. Nothing was stored.")
    try:  # from here on, the exact key is masked wherever VOOL redacts text (logs, history, model context)
        from core.secret_redaction import register_exact_secret

        register_exact_secret(lowered)
    except Exception:
        pass
    return ViewingKey(encoded=lowered, network=net)


# --- the one source of truth: VOOL's credential store ---------------------------------------------------

def credential_name(net: str) -> str:
    return CREDENTIAL_PREFIX + net


def store_viewing_key(key: ViewingKey) -> None:
    """Save under VOOL's credential policy and read it back; anything short of a verified save refuses."""
    from core.credential_store import get_credential, store_credential, strict_reads

    try:
        store_credential(credential_name(key.network), key.encoded, label=f"Zcash viewing key ({key.network}net)")
        with strict_reads():
            echoed = get_credential(credential_name(key.network))
    except Exception:
        raise ZcashKeyRefused("keychain_unavailable", "VOOL's secure storage refused the viewing key, so it was not saved.") from None
    if echoed != key.encoded:
        raise ZcashKeyRefused("keychain_unavailable", "VOOL's secure storage did not keep the viewing key, so it was not saved.")


def load_viewing_key(net: str | None = None) -> ViewingKey | None:
    """The saved key for the network, or None when none is saved. A storage failure raises; it is not 'no key'."""
    from core.credential_store import get_credential, strict_reads

    want = net or config.network()
    try:
        with strict_reads():
            value = get_credential(credential_name(want))
    except Exception:
        raise ZcashKeyRefused("keychain_unavailable", "VOOL's secure storage could not be read.") from None
    if not value:
        return None
    return parse_viewing_key(str(value), expected_network=want)
