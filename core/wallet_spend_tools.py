"""Off-by-default agent-wallet spend capability — guardrail shape only; no signer in this module.

This wires the *rules* around spending from the agent wallet: a hard enable flag, the user's
SpendPolicy caps + panic freeze (core/wallet_spend_policy), an append-only SpendLedger, and a
tamper-evident (HMAC-signed) policy file. It deliberately owns NO signing or broadcasting: the
actual transfer is an injected wallet seam (`_WALLET_PROVIDER`, None by default), so with nothing
wired this module cannot move funds — every path fails closed. Wiring a real transfer is a separate,
supervised integration.

Amounts are lamports (integer base units). See [[wallet-spend-security-requirements]].
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from core import wallet_spend_policy as wsp
from core.runtime_paths import data_path

_ENABLE_FLAG = "VOOL_ENABLE_WALLET_SPEND"
_POLICY_LABEL = "vool-wallet-spend-policy-v1"
_POLICY_FILE = "wallet_spend_policy.json"
_LEDGER_FILE = "wallet_spend_ledger.json"

# Injected signer seam. None => no wallet => every spend fails closed. A supervised integration sets
# this (via register_wallet_provider) to a factory returning a WalletProvider; it stays None here.
_WALLET_PROVIDER: Callable[[], Any] | None = None


class WalletProvider(Protocol):
    """The shape a real wallet must implement to be registered as the signer seam. `transfer` signs and
    broadcasts one spend and returns a transaction reference string; it is the ONLY money-moving surface,
    supplied by a separate supervised integration — never wired to a live signer in this module."""

    def transfer(self, to: str, amount_lamports: int, asset: str) -> str: ...


@dataclass
class SpendResult:
    ok: bool
    status: str
    message: str
    details: dict[str, Any]


def spend_enabled() -> bool:
    """True only when the hard env flag is set. Off by default; the runtime contract is gated too."""
    return str(os.environ.get(_ENABLE_FLAG, "")).strip().lower() in {"1", "true", "yes", "on"}


def _key() -> bytes | None:
    # Same node-key derivation the credential store uses. Deferred import so the module stays
    # importable where the signer backend isn't loaded; no key => policy can't be trusted => None.
    try:
        from network.signer import derive_local_secret

        return derive_local_secret(_POLICY_LABEL, length=32)
    except Exception:
        return None


def _policy_path():
    return data_path(_POLICY_FILE)


def _ledger_path():
    return data_path(_LEDGER_FILE)


def save_policy(policy: wsp.SpendPolicy) -> bool:
    """Persist the policy HMAC-signed with the node key. Returns False if no key is available."""
    key = _key()
    if key is None:
        return False
    _atomic_write(_policy_path(), json.dumps({"policy": policy.to_json(), "sig": wsp.sign_policy(policy, key)}))
    return True


def load_policy() -> wsp.SpendPolicy | None:
    """Load + verify the policy. Missing, unkeyed, or tampered => None (caller fails closed)."""
    path = _policy_path()
    if not path.exists():
        return None
    key = _key()
    if key is None:
        return None
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
        policy = wsp.SpendPolicy.from_json(blob.get("policy", ""))
        if not wsp.verify_policy(policy, blob.get("sig", ""), key):
            return None
        return policy
    except Exception:
        return None


def _atomic_write(path: Any, text: str) -> None:
    """Write text durably: to a temp file, then atomically replace, so a crash mid-write cannot leave a
    truncated file that would silently reset the ledger or policy."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    try:  # best-effort tighten perms on posix; Windows relies on the user profile ACL
        if os.name == "posix":
            tmp.chmod(0o600)
    except Exception:
        pass
    os.replace(tmp, path)


def _ledger_canonical(ledger: wsp.SpendLedger) -> str:
    return json.dumps(
        {"entries": [[float(ts), int(amt)] for ts, amt in ledger.entries]},
        sort_keys=True, separators=(",", ":"),
    )


def _sign_ledger(ledger: wsp.SpendLedger, key: bytes) -> str:
    return hmac.new(bytes(key), _ledger_canonical(ledger).encode("utf-8"), hashlib.sha256).hexdigest()


def _verify_ledger_sig(ledger: wsp.SpendLedger, signature: str, key: bytes) -> bool:
    return hmac.compare_digest(_sign_ledger(ledger, key), str(signature or ""))


def load_ledger() -> wsp.SpendLedger | None:
    """Load + verify the spend ledger. A missing file is a fresh empty ledger; a present file that is
    unsigned, tampered, unparseable, or unverifiable returns None so the caller fails closed instead of
    silently resetting the caps. The ledger is HMAC-signed with the node key, like the policy."""
    path = _ledger_path()
    if not path.exists():
        return wsp.SpendLedger()
    key = _key()
    if key is None:
        return None
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
        entries = [(float(ts), int(amt)) for ts, amt in blob.get("entries", [])]
        ledger = wsp.SpendLedger(entries=entries)
        if not _verify_ledger_sig(ledger, str(blob.get("sig") or ""), key):
            return None
        return ledger
    except Exception:
        return None


def save_ledger(ledger: wsp.SpendLedger) -> bool:
    """Persist the ledger HMAC-signed and atomically. Never raises: returns False if there is no key or
    the write fails, so a failed persist after a completed spend cannot crash the caller."""
    key = _key()
    if key is None:
        return False
    try:
        blob = {
            "entries": [[float(ts), int(amt)] for ts, amt in ledger.entries],
            "sig": _sign_ledger(ledger, key),
        }
        _atomic_write(_ledger_path(), json.dumps(blob))
        return True
    except Exception:
        return False


def _policy_is_configured(policy: wsp.SpendPolicy) -> bool:
    # Only an explicitly-capped policy authorizes a spend; an all-zero policy (no caps) is treated
    # as unconfigured and refused, so "no setup" can never mean "unlimited".
    return (
        policy.per_tx_cap_lamports > 0
        or policy.daily_cap_lamports > 0
        or policy.weekly_cap_lamports > 0
    )


def register_wallet_provider(provider: Callable[[], Any] | None) -> None:
    """RETIRED signer back-door: no provider can be registered; the canonical signers live in core.wallet."""
    from core.wallet.authority import refuse_legacy

    raise refuse_legacy("wallet_spend_tools.register_wallet_provider")


def wallet_provider_registered() -> bool:
    return False


def _resolve_wallet(wallet: Any | None) -> Any | None:
    return None


def execute_spend(
    *,
    to: str,
    amount_lamports: int,
    asset: str = "SOL",
    now: float | None = None,
    wallet: Any | None = None,
) -> SpendResult:
    """RETIRED: the spend seam. Returns the typed, receipt-backed refusal; nothing is signed or sent."""
    from core.wallet.authority import refuse_legacy

    fault = refuse_legacy("wallet_spend_tools.execute_spend")
    return SpendResult(
        ok=False,
        status=fault.code,
        message=f"{fault.user_message} Propose it with wallet.propose and approve it in your wallet.",
        details={"fault": fault.to_dict(), "to": str(to or ""), "amount_lamports": _echo_int(amount_lamports), "asset": str(asset or "SOL")},
    )


def _echo_int(value: Any) -> int | None:
    """Echo a caller-supplied amount in the refusal without letting a bad value break the refusal."""
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


__all__ = [
    "SpendResult",
    "WalletProvider",
    "execute_spend",
    "load_ledger",
    "load_policy",
    "register_wallet_provider",
    "save_ledger",
    "save_policy",
    "spend_enabled",
    "wallet_provider_registered",
]
