"""User-owned spend policy for the agent wallet: caps, freeze, and secure auto-withdrawal.

Pure policy logic (no I/O, no signing) so it is fully unit-testable. The persistence and
OS-consent wiring live at the edges; this module decides *whether* a spend is allowed and
enforces the rules sls set:

  * per-transaction, per-day, and per-week SOL caps (spend refused past any of them)
  * a panic "freeze" kill switch that blocks all spending instantly
  * a tamper-evident config: the policy is HMAC-signed with the wallet's node key, so an
    attacker editing the file on disk is detected and the loader must fail closed
  * secure auto-withdrawal (sweep to a main wallet at a balance threshold) where the
    withdrawal address can NEVER be silently changed — a new address is staged behind a
    time-lock (and OS-consent at the edge), so a malicious change is visible and cancelable

See [[wallet-spend-security-requirements]].
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import asdict, dataclass, field

# A new withdrawal address only takes effect after this delay, during which the user is
# notified and can cancel — so a compromise cannot instantly redirect the sweep.
ADDRESS_CHANGE_LOCK_SECONDS = 48 * 60 * 60
_DAY_SECONDS = 24 * 60 * 60
_WEEK_SECONDS = 7 * _DAY_SECONDS


@dataclass
class SpendPolicy:
    per_tx_cap_lamports: int = 0
    daily_cap_lamports: int = 0
    weekly_cap_lamports: int = 0
    frozen: bool = False
    # Auto-withdrawal (sweep) to the user's main wallet.
    auto_withdraw_enabled: bool = False
    auto_withdraw_threshold_lamports: int = 0
    auto_withdraw_address: str = ""
    # Staged address change (time-locked).
    pending_address: str = ""
    pending_address_since: float = 0.0

    def to_json(self) -> str:
        # Sorted keys → a stable byte string to sign/verify.
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> SpendPolicy:
        data = json.loads(text) if text else {}
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in allowed})


# --- tamper-evident integrity ----------------------------------------------

def sign_policy(policy: SpendPolicy, key: bytes) -> str:
    """HMAC-SHA256 of the canonical policy JSON under the wallet's node key."""
    return hmac.new(bytes(key), policy.to_json().encode("utf-8"), hashlib.sha256).hexdigest()


def verify_policy(policy: SpendPolicy, signature: str, key: bytes) -> bool:
    """Constant-time verify. A tampered file (or wrong key) → False → caller fails closed."""
    expected = sign_policy(policy, key)
    return hmac.compare_digest(expected, str(signature or ""))


# --- spend ledger + caps ----------------------------------------------------

@dataclass
class SpendLedger:
    """Append-only record of spends: entries are (unix_ts, lamports)."""

    entries: list[tuple[float, int]] = field(default_factory=list)

    def spent_within(self, now: float, window_seconds: int) -> int:
        floor = now - window_seconds
        return sum(amount for ts, amount in self.entries if ts >= floor)

    def record(self, now: float, lamports: int) -> None:
        self.entries.append((float(now), int(lamports)))

    def prune(self, now: float, keep_seconds: int = _WEEK_SECONDS) -> None:
        floor = now - keep_seconds
        self.entries = [(ts, amt) for ts, amt in self.entries if ts >= floor]


def check_spend_allowed(policy: SpendPolicy, ledger: SpendLedger, amount_lamports: int, now: float) -> tuple[bool, str]:
    """Return (allowed, reason) for spending `amount_lamports` right now. Never raises."""
    amount = int(amount_lamports)
    if amount <= 0:
        return False, "amount must be positive"
    if policy.frozen:
        return False, "wallet is frozen (panic freeze active)"
    if policy.per_tx_cap_lamports > 0 and amount > policy.per_tx_cap_lamports:
        return False, f"amount {amount} exceeds the per-transaction cap {policy.per_tx_cap_lamports}"
    if policy.daily_cap_lamports > 0:
        if ledger.spent_within(now, _DAY_SECONDS) + amount > policy.daily_cap_lamports:
            return False, "daily spend cap reached"
    if policy.weekly_cap_lamports > 0:
        if ledger.spent_within(now, _WEEK_SECONDS) + amount > policy.weekly_cap_lamports:
            return False, "weekly spend cap reached"
    return True, "ok"


def remaining_today(policy: SpendPolicy, ledger: SpendLedger, now: float) -> int | None:
    if policy.daily_cap_lamports <= 0:
        return None
    return max(0, policy.daily_cap_lamports - ledger.spent_within(now, _DAY_SECONDS))


def remaining_week(policy: SpendPolicy, ledger: SpendLedger, now: float) -> int | None:
    if policy.weekly_cap_lamports <= 0:
        return None
    return max(0, policy.weekly_cap_lamports - ledger.spent_within(now, _WEEK_SECONDS))


# --- panic freeze -----------------------------------------------------------

def freeze(policy: SpendPolicy) -> SpendPolicy:
    """Instantly block all spending. No consent needed to become safer."""
    policy.frozen = True
    return policy


def unfreeze(policy: SpendPolicy) -> SpendPolicy:
    """Re-enable spending. The caller MUST gate this behind OS consent."""
    policy.frozen = False
    return policy


# --- secure auto-withdrawal (time-locked address) ---------------------------

def propose_withdraw_address(policy: SpendPolicy, new_address: str, now: float) -> SpendPolicy:
    """Stage a new withdrawal address behind the time-lock. Caller gates with OS consent."""
    policy.pending_address = str(new_address or "")
    policy.pending_address_since = float(now)
    return policy


def address_change_ready(policy: SpendPolicy, now: float, lock_seconds: int = ADDRESS_CHANGE_LOCK_SECONDS) -> bool:
    """True once a staged address has waited out the full time-lock."""
    if not policy.pending_address:
        return False
    return (now - float(policy.pending_address_since)) >= lock_seconds


def commit_withdraw_address(policy: SpendPolicy, now: float, lock_seconds: int = ADDRESS_CHANGE_LOCK_SECONDS) -> tuple[bool, str]:
    """Promote the staged address to active — only after the lock elapses. Gate with consent."""
    if not policy.pending_address:
        return False, "no pending address"
    if not address_change_ready(policy, now, lock_seconds):
        return False, "address change is still within its time-lock"
    policy.auto_withdraw_address = policy.pending_address
    policy.pending_address = ""
    policy.pending_address_since = 0.0
    return True, "ok"


def cancel_withdraw_address(policy: SpendPolicy) -> SpendPolicy:
    """Cancel a staged address change (e.g. the user did not initiate it)."""
    policy.pending_address = ""
    policy.pending_address_since = 0.0
    return policy


def should_auto_withdraw(policy: SpendPolicy, balance_lamports: int) -> tuple[bool, str]:
    """Whether a sweep should fire now. The sweep tx itself still goes through the spend gate."""
    if policy.frozen:
        return False, "wallet frozen"
    if not policy.auto_withdraw_enabled:
        return False, "auto-withdraw disabled"
    if not policy.auto_withdraw_address:
        return False, "no active withdrawal address"
    if policy.auto_withdraw_threshold_lamports <= 0:
        return False, "no threshold set"
    if int(balance_lamports) < policy.auto_withdraw_threshold_lamports:
        return False, "balance below threshold"
    return True, "ok"
