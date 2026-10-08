"""The one door a Zcash key comes through: a viewing key is accepted, anything that can spend is refused.

Accepted: a Unified Full Viewing Key (``uview1...`` mainnet, ``uviewtest1...`` testnet) or a Sapling
extended full viewing key (``zxviews1...`` / ``zxviewtestsapling1...``), on the configured network only.
Everything else is refused without being stored, logged or echoed -- and a value that looks like a
spending key or a seed phrase gets its own refusal so the owner learns why. The accepted key is kept in
the OS keychain (bounded call; a blocked or failing keychain refuses, it never falls back to a file).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from core.zcash import config

KEYRING_SERVICE = "vool-zcash"
_BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_VIEWING_PREFIXES = {
    "uview1": config.NETWORK_MAIN,
    "uviewtest1": config.NETWORK_TEST,
    "zxviews1": config.NETWORK_MAIN,
    "zxviewtestsapling1": config.NETWORK_TEST,
}
#: Encodings that carry spending authority (Sapling extended spending keys, BIP32 private keys, WIF).
_SPENDING_MARKERS = ("secret-extended-key", "xprv", "tprv", "zprv")
_INCOMING_ONLY_PREFIXES = ("uivk1", "uivktest1")
_HEX_SECRET = re.compile(r"^(?:0x)?[0-9a-fA-F]{64,128}$")
_WIF = re.compile(r"^[5KLc9][1-9A-HJ-NP-Za-km-z]{50,51}$")


class ZcashKeyRefused(ValueError):
    """A key the watch-only lane will not take. ``code`` is stable; the message never contains the key."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ViewingKey:
    encoded: str
    network: str
    kind: str  # "unified" | "sapling"

    def __repr__(self) -> str:  # never print the key itself
        return f"ViewingKey(kind={self.kind!r}, network={self.network!r})"


def _looks_like_seed_phrase(text: str) -> bool:
    words = text.split()
    return len(words) >= 12 and all(word.isalpha() for word in words)


def parse_viewing_key(raw: str, *, expected_network: str | None = None) -> ViewingKey:
    text = str(raw or "").strip()
    if not text:
        raise ZcashKeyRefused("viewing_key_missing", "Give a Zcash full viewing key (it starts with uview1).")
    lowered = text.lower()
    if _looks_like_seed_phrase(text):
        raise ZcashKeyRefused("seed_phrase_refused", "That looks like a recovery phrase. VOOL never takes one for Zcash: give the full viewing key instead (it starts with uview1). Nothing was stored.")
    if any(marker in lowered for marker in _SPENDING_MARKERS) or _HEX_SECRET.match(text) or _WIF.match(text):
        raise ZcashKeyRefused("spending_key_refused", "That looks like a spending key. VOOL only takes a viewing key for Zcash, which cannot move money. Nothing was stored.")
    if lowered.startswith(_INCOMING_ONLY_PREFIXES):
        raise ZcashKeyRefused("incoming_viewing_key_unsupported", "That is an incoming viewing key; this lane needs the full viewing key (it starts with uview1). Nothing was stored.")
    if any(ch.isspace() for ch in text) or text not in (lowered, text.upper()):  # bech32m is single-case
        raise ZcashKeyRefused("viewing_key_malformed", "That is not a Zcash full viewing key. Nothing was stored.")
    for prefix, net in sorted(_VIEWING_PREFIXES.items(), key=lambda item: -len(item[0])):
        if lowered.startswith(prefix):
            body = lowered[len(prefix):]
            if len(body) < 60 or any(ch not in _BECH32 for ch in body):
                raise ZcashKeyRefused("viewing_key_malformed", "That is not a Zcash full viewing key. Nothing was stored.")
            want = expected_network or config.network()
            if net != want:
                raise ZcashKeyRefused("viewing_key_wrong_network", f"That viewing key is for {'testnet' if net == config.NETWORK_TEST else 'mainnet'}, but VOOL's Zcash lane is set to {'testnet' if want == config.NETWORK_TEST else 'mainnet'}. Nothing was stored.")
            return ViewingKey(encoded=lowered, network=net, kind="unified" if prefix.startswith("uview") else "sapling")
    raise ZcashKeyRefused("viewing_key_malformed", "That is not a Zcash full viewing key. Nothing was stored.")


def _account(net: str) -> str:
    return f"ufvk-{net}"


def store_viewing_key(key: ViewingKey) -> None:
    import keyring

    from core.bounded_keyring import bounded_keyring_call, keychain_blocked

    if keychain_blocked():
        raise ZcashKeyRefused("keychain_unavailable", "The system keychain is not answering, so the viewing key was not saved.")
    try:
        bounded_keyring_call(lambda: keyring.set_password(KEYRING_SERVICE, _account(key.network), key.encoded), what="zcash viewing key write")
    except Exception as exc:
        raise ZcashKeyRefused("keychain_unavailable", "The system keychain refused the viewing key, so it was not saved.") from exc


def load_viewing_key(net: str | None = None) -> ViewingKey | None:
    """The saved key for the network, or None when none is saved. A keychain failure raises; it is not 'no key'."""
    import keyring

    from core.bounded_keyring import bounded_keyring_call, keychain_blocked

    want = net or config.network()
    if keychain_blocked():
        raise ZcashKeyRefused("keychain_unavailable", "The system keychain is not answering.")
    try:
        value = bounded_keyring_call(lambda: keyring.get_password(KEYRING_SERVICE, _account(want)), what="zcash viewing key read")
    except Exception as exc:
        raise ZcashKeyRefused("keychain_unavailable", "The system keychain could not be read.") from exc
    if not value:
        return None
    return parse_viewing_key(str(value), expected_network=want)
