"""Standard BIP39 recovery phrases and the canonical Solana derivation Phantom-compatible wallets use.

Nothing here is invented: the wordlist is the canonical BIP39 English list (sha256
2f5eed53…4dbda), mnemonic -> seed is PBKDF2-HMAC-SHA512 exactly as BIP39 specifies (Python's
standard library), and seed -> keypair on m/44'/501'/<account>'/0' is the maintained solders
implementation (`Keypair.from_seed_and_derivation_path`). The SLIP-0010 walk in this module
exists only as an independent cross-check against the published vectors; production derivation
goes through solders. The derivation path is assembled from a constant and an integer account
index -- a caller can never supply a path string (solders panics on a malformed one).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import unicodedata
from pathlib import Path
from typing import Any

from solders.keypair import Keypair

WORDLIST_PATH = Path(__file__).with_name("bip39_english.txt")
WORDS: tuple[str, ...] = tuple(WORDLIST_PATH.read_text(encoding="utf-8").split())
INDEX: dict[str, int] = {word: i for i, word in enumerate(WORDS)}
assert len(WORDS) == 2048, len(WORDS)

SOLANA_DERIVATION_PATH = "m/44'/501'/0'/0'"
_PATH_TEMPLATE = "m/44'/501'/{account}'/0'"
_VALID_WORD_COUNTS = {12, 15, 18, 21, 24}
_PBKDF2_ROUNDS = 2048


def _nfkd(text: str) -> str:
    return unicodedata.normalize("NFKD", str(text or ""))


def entropy_to_mnemonic(entropy: bytes) -> str:
    """BIP39 entropy (16..32 bytes, multiple of 4) -> checksummed mnemonic."""
    data = bytes(entropy)
    if len(data) not in (16, 20, 24, 28, 32):
        raise ValueError("entropy must be 16, 20, 24, 28 or 32 bytes")
    checksum_bits = len(data) * 8 // 32
    digest = hashlib.sha256(data).digest()
    bits = bin(int.from_bytes(data, "big"))[2:].zfill(len(data) * 8) + bin(digest[0])[2:].zfill(8)[:checksum_bits]
    return " ".join(WORDS[int(bits[i : i + 11], 2)] for i in range(0, len(bits), 11))


def validate_mnemonic(phrase: str) -> bool:
    words = _nfkd(phrase).lower().split()
    if len(words) not in _VALID_WORD_COUNTS or any(w not in INDEX for w in words):
        return False
    bits = "".join(bin(INDEX[w])[2:].zfill(11) for w in words)
    checksum_bits = len(words) * 11 // 33
    entropy_bits = bits[:-checksum_bits]
    entropy = int(entropy_bits, 2).to_bytes(len(entropy_bits) // 8, "big")
    expected = bin(hashlib.sha256(entropy).digest()[0])[2:].zfill(8)[:checksum_bits]
    return bits[-checksum_bits:] == expected


def generate_mnemonic(*, strength_bits: int = 128) -> str:
    if strength_bits not in (128, 160, 192, 224, 256):
        raise ValueError("strength must be 128..256 bits in 32-bit steps")
    return entropy_to_mnemonic(secrets.token_bytes(strength_bits // 8))


def mnemonic_to_seed(phrase: str, *, passphrase: str = "") -> bytes:
    """BIP39: PBKDF2-HMAC-SHA512, 2048 rounds, salt "mnemonic" + passphrase, NFKD both."""
    return hashlib.pbkdf2_hmac("sha512", _nfkd(phrase).encode("utf-8"), ("mnemonic" + _nfkd(passphrase)).encode("utf-8"), _PBKDF2_ROUNDS, dklen=64)


def slip10_ed25519_private_key(seed: bytes, path: tuple[int, ...] | list[int]) -> bytes:
    """SLIP-0010 ed25519 (hardened-only) master + child derivation. Cross-check only."""
    digest = hmac.new(b"ed25519 seed", bytes(seed), hashlib.sha512).digest()
    key, chain = digest[:32], digest[32:]
    for index in path:
        digest = hmac.new(chain, b"\x00" + key + (int(index) | 0x80000000).to_bytes(4, "big"), hashlib.sha512).digest()
        key, chain = digest[:32], digest[32:]
    return key


def derive_solana_keypair(seed: bytes, *, account: int = 0) -> Keypair:
    """The Phantom-compatible keypair for a BIP39 seed: m/44'/501'/<account>'/0' via solders."""
    index = int(account)
    if index < 0 or index > 2**31 - 1:
        raise ValueError("account index out of range")
    return Keypair.from_seed_and_derivation_path(bytes(seed), _PATH_TEMPLATE.format(account=index))


def keypair_for_mnemonic(phrase: str, *, passphrase: str = "", account: int = 0) -> Keypair:
    if not validate_mnemonic(phrase):
        raise ValueError("not a valid BIP39 mnemonic")
    return derive_solana_keypair(mnemonic_to_seed(phrase, passphrase=passphrase), account=account)


def address_for_mnemonic(phrase: str, *, passphrase: str = "", account: int = 0) -> str:
    return str(keypair_for_mnemonic(phrase, passphrase=passphrase, account=account).pubkey())


__all__: list[Any] = [
    "INDEX", "SOLANA_DERIVATION_PATH", "WORDLIST_PATH", "WORDS", "address_for_mnemonic", "derive_solana_keypair", "entropy_to_mnemonic",
    "generate_mnemonic", "keypair_for_mnemonic", "mnemonic_to_seed", "slip10_ed25519_private_key", "validate_mnemonic",
]
