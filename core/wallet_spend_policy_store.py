"""
core/wallet_spend_policy_store.py
=================================
Tamper-evident persistence for the wallet spend policy + spend ledger.

The policy logic lives in :mod:`core.wallet_spend_policy` (pure, no I/O). This is
the persistence edge it referenced: it loads/saves the :class:`SpendPolicy` and
:class:`SpendLedger` beside the encrypted wallet, HMAC-signed so that an attacker
editing the file on disk is detected and the loader fails closed.

Integrity key derivation
------------------------
The HMAC key is ``wallet.sign(_HMAC_DOMAIN)`` — an Ed25519 signature over a fixed,
non-transaction domain string. Ed25519 is deterministic (RFC 8032), so this is a
stable 64-byte secret that ONLY the wallet-seed holder can produce, derived
through the wallet's existing public interface without exposing the seed. A
process that cannot decrypt the wallet cannot forge a valid policy signature.

Threat model (stated honestly)
------------------------------
* A compromised *agent process* that cannot decrypt the wallet cannot forge a
  policy (e.g. cannot raise a cap or clear the ledger with a valid HMAC), and
  cannot spend at all without the live OS-consent (Windows Hello) prompt.
* An attacker with full local write access can DELETE the policy file, reverting
  to permissive defaults — this discards BOTH the caps AND an active panic
  freeze (missing file = not frozen). Such an attacker still cannot spend without
  Windows Hello, so OS-consent remains the anti-theft backstop; but a user who
  froze precisely because they distrust the environment should know the freeze
  bit lives in a deletable file. Hardening a "policy exists" anchor into the
  encrypted wallet envelope (so file absence => fail closed) is a known follow-up.
  Net: caps + freeze guard against a compromised agent and accidental overspend;
  OS-consent is the gate that guards against outright theft.

Concurrency: read-modify-write of the ledger is serialized with a small
cross-process file lock (:func:`record_spend`). The check-then-broadcast window
in the signer is not itself locked, but deliberate registrations are naturally
serialized by the per-spend OS-consent prompt and bounded by the signer's
per-tx hard ceiling.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from core.runtime_paths import active_data_dir
from core.wallet_spend_policy import SpendLedger, SpendPolicy

logger = logging.getLogger("vool.spend_policy")

# Fixed domain string signed to derive the integrity key. The 26-byte ASCII input
# is far too short to parse as a Solana transaction message (which needs a 3-byte
# header + account array + 32-byte blockhash + instructions), so signing it is safe
# and can never be replayed as a transaction; the resulting 64-byte Ed25519
# signature is the HMAC-SHA256 key.
_HMAC_DOMAIN = b"vool/spend-policy/hmac/v1"
_POLICY_FILENAME = "spend_policy.json"
_LEDGER_KEEP_SECONDS = 7 * 24 * 60 * 60  # prune ledger entries older than a week on save
_LOCK_TIMEOUT_SECONDS = 10.0
_LOCK_STALE_SECONDS = 30.0


class PolicyIntegrityError(Exception):
    """The on-disk policy failed its HMAC check (possible tampering). Fail closed."""


def policy_path() -> Path:
    return (active_data_dir() / "keys" / _POLICY_FILENAME).resolve()


def _derive_hmac_key(wallet: Any) -> bytes:
    """The policy integrity key comes from the DEVICE secret, never from a signing wallet: reading a
    spend policy must not require (or grant) signing authority. `wallet` is accepted for callers that
    still pass one and ignored."""
    from network.signer import derive_local_secret

    key = bytes(derive_local_secret(b"vool/spend-policy/hmac/v2", length=32))
    if len(key) < 32:
        raise PolicyIntegrityError("could not derive a policy integrity key from the device secret")
    return key


def _mac(key: bytes, policy_json: str, ledger_json: str) -> str:
    msg = (policy_json + "\n" + ledger_json).encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def _ledger_to_json(ledger: SpendLedger) -> str:
    return json.dumps([[float(ts), int(amt)] for ts, amt in ledger.entries], separators=(",", ":"))


def _ledger_from_json(text: str) -> SpendLedger:
    try:
        rows = json.loads(text) if text else []
    except Exception:
        rows = []
    entries: list[tuple[float, int]] = []
    for row in rows:
        try:
            entries.append((float(row[0]), int(row[1])))
        except Exception:
            continue
    return SpendLedger(entries=entries)


def _wallet_present(wallet: Any) -> bool:
    """True if an encrypted wallet exists on disk for this handle. A present wallet with
    a missing policy is the deletion-attack signature (spending is possible, limits gone)."""
    try:
        exists_fn = getattr(wallet, "exists", None)
        if callable(exists_fn):
            return bool(exists_fn())
        wallet_path = getattr(wallet, "wallet_path", None)
        return bool(wallet_path and Path(wallet_path).exists())
    except Exception:
        return False


def load_policy_and_ledger(
    wallet: Any, *, allow_missing_policy: bool = False
) -> tuple[SpendPolicy, SpendLedger]:
    """Load the (policy, ledger) for this wallet.

    Missing file with no wallet -> permissive defaults (first run, no wallet to protect).
    Missing file while a wallet EXISTS -> PolicyIntegrityError (fail closed): a deleted
    policy would silently discard a panic freeze and the caps, so read/authorization paths
    must refuse rather than revert to permissive defaults. Only the establish/write path
    (setting the first freeze or caps) passes ``allow_missing_policy=True`` to bootstrap.
    Present-but-tampered -> PolicyIntegrityError as before.
    """
    path = policy_path()
    if not path.exists():
        if _wallet_present(wallet) and not allow_missing_policy:
            raise PolicyIntegrityError(
                "spend policy is missing while a wallet exists; refusing permissive defaults "
                "(a deleted policy could hide a freeze or caps). Re-establish the spend policy "
                "(e.g. set a freeze or a cap) to continue."
            )
        return SpendPolicy(), SpendLedger()
    try:
        env = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PolicyIntegrityError(f"spend policy file is unreadable: {exc}") from exc

    policy_json = str(env.get("policy") or "")
    ledger_json = str(env.get("ledger") or "")
    stored_mac = str(env.get("hmac") or "")
    expected = _mac(_derive_hmac_key(wallet), policy_json, ledger_json)
    if not hmac.compare_digest(expected, stored_mac):
        raise PolicyIntegrityError("spend policy HMAC mismatch (possible tampering)")

    try:
        policy = SpendPolicy.from_json(policy_json)
    except Exception as exc:
        raise PolicyIntegrityError(f"spend policy is corrupt: {exc}") from exc
    return policy, _ledger_from_json(ledger_json)


def save_policy_and_ledger(wallet: Any, policy: SpendPolicy, ledger: SpendLedger, *, now: float | None = None) -> None:
    """Persist (policy, ledger) atomically with a fresh HMAC. Prunes stale ledger rows."""
    if now is not None:
        ledger.prune(now, keep_seconds=_LEDGER_KEEP_SECONDS)
    policy_json = policy.to_json()
    ledger_json = _ledger_to_json(ledger)
    mac = _mac(_derive_hmac_key(wallet), policy_json, ledger_json)
    envelope = {"version": 1, "policy": policy_json, "ledger": ledger_json, "hmac": mac}

    # Reuse the wallet's private atomic writer (0o600, fsync, atomic replace).
    from core.vool_wallet import _atomic_write_private

    _atomic_write_private(policy_path(), json.dumps(envelope, sort_keys=True) + "\n")


@contextlib.contextmanager
def _policy_lock():
    """Small cross-process advisory lock around ledger read-modify-write.

    Uses an atomic O_EXCL lock file with stale-lock stealing. If it can't be
    acquired within the timeout it proceeds anyway (best-effort): for the ledger
    record step, a rare unsynchronized write is less harmful than blocking a spend
    that already committed on-chain.
    """
    lock_path = policy_path().with_name(_POLICY_FILENAME + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    acquired = False
    deadline = time.time() + _LOCK_TIMEOUT_SECONDS
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            acquired = True
            break
        except FileExistsError:
            try:
                if (time.time() - lock_path.stat().st_mtime) > _LOCK_STALE_SECONDS:
                    with contextlib.suppress(OSError):
                        lock_path.unlink()
                    continue
            except OSError:
                pass
            if time.time() >= deadline:
                logger.warning(
                    "spend policy lock not acquired within %.0fs; proceeding best-effort",
                    _LOCK_TIMEOUT_SECONDS,
                )
                break
            time.sleep(0.05)
    try:
        yield
    finally:
        if acquired:
            with contextlib.suppress(OSError):
                lock_path.unlink()


def set_frozen_mirrored(
    frozen: bool,
    *,
    wallet: Any = None,
    policy: SpendPolicy | None = None,
    ledger: SpendLedger | None = None,
    now: float | None = None,
) -> bool:
    """Flip the panic freeze in BOTH stores and return the PRIOR state (frozen if EITHER was).

    The canonical freeze lives in core.wallet.limits (every proposal, reservation and approval
    consults it); this device-keyed file is the mirror the legacy readers consult. Order matters:
    the fallible file write goes FIRST, the canonical flip LAST, and a canonical failure rolls the
    file back -- so a caller that reports "unchanged" after an exception is telling the truth in
    both stores, and a resume never lifts the canonical freeze behind a failed persist.
    """
    import contextlib

    from core.wallet import limits as wallet_limits
    from core.wallet_spend_policy import freeze, unfreeze

    if policy is None or ledger is None:
        loaded_policy, loaded_ledger = load_policy_and_ledger(wallet, allow_missing_policy=True)
        policy = loaded_policy if policy is None else policy
        ledger = loaded_ledger if ledger is None else ledger
    prior_legacy = bool(policy.frozen)
    prior_canonical = bool(wallet_limits.is_frozen())
    policy = freeze(policy) if frozen else unfreeze(policy)
    save_policy_and_ledger(wallet, policy, ledger, now=now)
    try:
        wallet_limits.set_frozen(bool(frozen))
    except Exception:
        rolled_back = freeze(policy) if prior_legacy else unfreeze(policy)
        with contextlib.suppress(Exception):
            save_policy_and_ledger(wallet, rolled_back, ledger, now=now)
        raise
    return prior_legacy or prior_canonical


def record_spend(wallet: Any, lamports: int, now: float) -> None:
    """Atomically append a spend to the ledger under the file lock (reload → append → save).

    Re-reads the current ledger inside the lock so a concurrent writer's entry is never
    lost to last-writer-wins. If the on-disk policy can't be verified at record time, the
    spend is not recorded (best-effort) — the tx already committed, so we never raise here.
    """
    with _policy_lock():
        try:
            policy, ledger = load_policy_and_ledger(wallet)
        except Exception as exc:
            logger.warning("could not reload policy to record a spend (%s); skipping ledger update", exc)
            return
        ledger.record(now, int(lamports))
        save_policy_and_ledger(wallet, policy, ledger, now=now)


__all__ = [
    "PolicyIntegrityError",
    "load_policy_and_ledger",
    "policy_path",
    "record_spend",
    "save_policy_and_ledger",
]
