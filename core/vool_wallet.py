from __future__ import annotations

import json
import os
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.runtime_paths import active_vool_home

WALLET_FILENAME = "solana_" + "wallet.enc"  # the RETIRED legacy key file name; never written again
WALLET_VERSION = 1
_WALLET_AAD_PREFIX = b"vool-solana-wallet:v1:"
_RPC_ENDPOINTS = (
    "https://solana-rpc.publicnode.com",
    "https://solana.publicnode.com",
    "https://solana.api.onfinality.io/public",
)
_USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
_BASE58_ALPHABET = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BASE58_INDEX = {char: index for index, char in enumerate(_BASE58_ALPHABET)}


def _chmod_safe(path: Path, mode: int) -> None:
    if os.name != "posix":
        return
    try:
        path.chmod(mode)
    except Exception:
        return


def _ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _chmod_safe(path, 0o700)


def _atomic_write_private(path: Path, text: str) -> None:
    _ensure_private_dir(path.parent)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as handle:
        tmp_path = Path(handle.name)
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    _chmod_safe(tmp_path, 0o600)
    tmp_path.replace(path)
    _chmod_safe(path, 0o600)


def b58encode(data: bytes) -> str:
    value = int.from_bytes(data, "big")
    encoded = bytearray()
    while value:
        value, remainder = divmod(value, 58)
        encoded.append(_BASE58_ALPHABET[remainder])
    leading_zeroes = len(data) - len(data.lstrip(b"\x00"))
    encoded.extend(b"1" * leading_zeroes)
    if not encoded:
        encoded.append(_BASE58_ALPHABET[0])
    return bytes(reversed(encoded)).decode("ascii")


def b58decode(value: str) -> bytes:
    text = str(value or "").strip()
    if not text:
        raise ValueError("empty base58 value")
    number = 0
    for byte in text.encode("ascii"):
        if byte not in _BASE58_INDEX:
            raise ValueError("invalid base58 character")
        number = number * 58 + _BASE58_INDEX[byte]
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    leading_zeroes = len(text) - len(text.lstrip("1"))
    return (b"\x00" * leading_zeroes) + raw


def decode_solana_pubkey(pubkey: str) -> bytes:
    raw = b58decode(pubkey)
    if len(raw) != 32:
        raise ValueError("Solana public keys must decode to exactly 32 bytes")
    return raw


def is_solana_pubkey(pubkey: str) -> bool:
    try:
        decode_solana_pubkey(pubkey)
        return True
    except Exception:
        return False


def _rpc_call(method: str, params: list[Any], *, timeout: float = 5.0) -> Any:
    from core.remote_fetch_policy import RemoteFetchRefusedError, open_remote

    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode("utf-8")
    for url in _RPC_ENDPOINTS:
        try:
            request = urllib.request.Request(
                url,
                data=payload,
                # publicnode rejects the default Python-urllib User-Agent with a
                # 403, which the except below would swallow to None — silently
                # killing every on-chain read (resolution, balance, dial). Send a
                # plain app UA so the RPC actually answers.
                headers={"Content-Type": "application/json", "User-Agent": "vool/1.0"},
                method="POST",
            )
            # The ONE outbound door: on-chain reads obey the per-turn veto and
            # report themselves; a refused turn propagates rather than reading
            # as a quiet zero balance.
            with open_remote(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            if isinstance(data, dict) and "error" not in data:
                return data.get("result")
        except RemoteFetchRefusedError:
            raise
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            continue
    return None


@dataclass(frozen=True)
class WalletInfo:
    pubkey: str
    sol_balance: float
    usdc_balance: float


def _refuse(surface: str):
    from core.wallet.authority import refuse_legacy

    return refuse_legacy(surface)


def _refuse_export(surface: str):
    from core.wallet.authority import refuse_export

    return refuse_export(surface)


class VoolWallet:
    """RETIRED signing wallet. Every key operation refuses; the class remains so old imports fail typed, not loud."""

    def __init__(self, *, runtime_home: str | Path | None = None, derivation_key: bytes | None = None, rpc_call: Callable[..., Any] | None = None) -> None:
        self._runtime_home = Path(runtime_home).expanduser().resolve() if runtime_home else active_vool_home()
        self._keys_dir = (self._runtime_home / "data" / "keys").resolve()
        self._wallet_path = self._keys_dir / WALLET_FILENAME
        self._rpc_call = rpc_call or _rpc_call
        self._pubkey: str | None = None

    @property
    def wallet_path(self) -> Path:
        return self._wallet_path

    def exists(self) -> bool:
        return self._wallet_path.exists()

    def generate_and_save(self, *, overwrite: bool = False) -> str:
        raise _refuse("vool_wallet.generate_and_save")

    def load(self) -> VoolWallet:
        raise _refuse("vool_wallet.load")

    @property
    def pubkey(self) -> str:
        if self._pubkey is None:
            raise RuntimeError("the legacy wallet is retired; read the canonical wallet through core.wallet.custody")
        return self._pubkey

    @property
    def public_key_bytes(self) -> bytes:
        return decode_solana_pubkey(self.pubkey)

    def sign(self, payload: bytes) -> bytes:
        raise _refuse("vool_wallet.sign")

    def sign_message(self, message: bytes | str) -> bytes:
        raise _refuse("vool_wallet.sign")

    def sign_transaction(self, transaction_message: bytes) -> bytes:
        raise _refuse("vool_wallet.sign")

    def export_secret_key_base58(self) -> str:
        raise _refuse_export("vool_wallet.export_secret_key_base58")

    def get_sol_balance(self) -> float:
        raise _refuse("vool_wallet.get_sol_balance")

    def get_usdc_balance(self) -> float:
        raise _refuse("vool_wallet.get_usdc_balance")

    def info(self) -> WalletInfo:
        return WalletInfo(pubkey=self.pubkey, sol_balance=0.0, usdc_balance=0.0)

    def export_safe(self, *, include_balances: bool = True) -> dict[str, Any]:
        return legacy_wallet_view(include_balances=include_balances)

    def __repr__(self) -> str:
        return "VoolWallet(retired)"


def verify_wallet_signature(*, wallet_pubkey: str, message: bytes | str, signature: bytes) -> bool:
    payload = message.encode("utf-8") if isinstance(message, str) else bytes(message)
    try:
        Ed25519PublicKey.from_public_bytes(decode_solana_pubkey(wallet_pubkey)).verify(signature, payload)
        return True
    except Exception:
        return False


def legacy_wallet_view(*, include_balances: bool = True) -> dict[str, Any]:
    """Read-only compatibility adapter: the canonical wallet's public view in the old `/wallet/info` shape."""
    from core.wallet import config, custody

    payload: dict[str, Any] = {"authority": "core.wallet", "read_only": True, "pubkey": "", "network": config.NETWORK_SOLANA_DEVNET, "custody_mode": "none", "enabled": config.wallet_enabled(), "mainnet_enabled": config.mainnet_enabled()}
    try:
        profile = custody.default_wallet() if config.wallet_enabled() else None
    except Exception:
        profile = None
    if profile is not None:
        payload.update({"pubkey": profile.public_key, "network": profile.network, "custody_mode": profile.mode, "wallet_id": profile.wallet_id})
        if include_balances:
            try:
                from core.wallet import chains
                from core.wallet.lifecycle import RpcClient

                if chains.resolve_network(profile.network).is_svm:
                    payload["sol_balance"] = RpcClient(chains.network_rpc_url(profile.network), network=profile.network).balance(profile.public_key) / 1_000_000_000
                else:
                    payload["sol_balance"] = None
            except Exception:
                payload["sol_balance"] = None
    return payload


def get_or_create_wallet(*, runtime_home: str | Path | None = None, derivation_key: bytes | None = None) -> VoolWallet:
    """RETIRED: the legacy create-on-read door. Refuses typed; never creates a key."""
    raise _refuse("vool_wallet.get_or_create_wallet")


def reveal_wallet_secret_key_base58(*, runtime_home: str | Path | None = None, derivation_key: bytes | None = None, reason: str = "") -> str:
    """RETIRED: there is no private-key export. Refuses BEFORE any consent prompt could be asked."""
    raise _refuse_export("vool_wallet.reveal_wallet_secret_key_base58")


__all__ = [
    "WALLET_FILENAME",
    "VoolWallet",
    "WalletInfo",
    "b58decode",
    "b58encode",
    "decode_solana_pubkey",
    "get_or_create_wallet",
    "is_solana_pubkey",
    "legacy_wallet_view",
    "reveal_wallet_secret_key_base58",
    "verify_wallet_signature",
]
