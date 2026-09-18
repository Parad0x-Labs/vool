"""Per-transaction, daily, per-destination-daily and global ceilings, enforced by an ATOMIC
reservation that always includes every non-sponsored maximum fee.

The ledger is the reservation: an approved amount (principal + fee) is written as
``reserved`` inside one BEGIN IMMEDIATE transaction that also reads every held (reserved +
settled) amount in the window, so two concurrent approvals cannot both pass a ceiling they
jointly exceed. The row settles after broadcast and is released when signing or broadcast
fails. Ceilings are per wallet, per ASSET and per CHAIN: a ledger row carries its chain, so
USDC on Base Sepolia and USDC on Ethereum Sepolia draw on separate daily buckets.

Honest totals: a cross-asset comparison (any global ceiling, or a fee quoted in a different
asset than the principal) is only made when the operator declared conversion factors
(``VOOL_WALLET_ASSET_CONVERSIONS`` JSON: {"USDC": 10000, ...} in global-minor per asset
unit); where a needed factor is missing the reservation REFUSES with ``global_uncomparable``
rather than pretending the total fits.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass

from core.wallet.store import connection

DAY_SECONDS = 86_400
#: Conservative defaults for a testnet slice (minor units): per-tx, daily, per-destination-daily.
DEFAULT_LIMITS = {
    "SOL": (100_000_000, 500_000_000, 250_000_000),
    "USDC": (10_000_000, 50_000_000, 25_000_000),
    "ETH": (10_000_000_000_000_000, 50_000_000_000_000_000, 25_000_000_000_000_000),
    "BNB": (20_000_000_000_000_000, 100_000_000_000_000_000, 50_000_000_000_000_000),
}
FALLBACK_LIMITS = (1_000_000, 5_000_000, 2_500_000)

LIMIT_PER_TRANSACTION = "per_transaction"
LIMIT_DAILY = "daily"
LIMIT_PER_DESTINATION_DAILY = "per_destination_daily"
LIMIT_GLOBAL_DAILY = "global_daily"
LIMIT_GLOBAL_UNCOMPARABLE = "global_uncomparable"
LIMIT_FROZEN = "frozen"
_FREEZE_KEY = "spend_frozen"

GLOBAL_DAILY_ENV = "VOOL_WALLET_GLOBAL_DAILY_MINOR"
CONVERSIONS_ENV = "VOOL_WALLET_ASSET_CONVERSIONS"

RESERVATION_RESERVED = "reserved"
RESERVATION_SETTLED = "settled"
RESERVATION_RELEASED = "released"
_HELD_STATES = (RESERVATION_RESERVED, RESERVATION_SETTLED)


@dataclass(frozen=True)
class SpendLimits:
    per_tx_minor: int
    daily_minor: int
    per_destination_daily_minor: int

    def to_dict(self) -> dict[str, int]:
        return {"per_tx_minor": self.per_tx_minor, "daily_minor": self.daily_minor, "per_destination_daily_minor": self.per_destination_daily_minor}


@dataclass(frozen=True)
class LimitVerdict:
    ok: bool
    limit: str
    reason: str


def _env_json(name: str) -> dict[str, float]:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in loaded.items():
        try:
            out[str(key).strip().upper()] = float(value)
        except (TypeError, ValueError):
            continue
    return out


def global_daily_minor() -> int:
    raw = str(os.environ.get(GLOBAL_DAILY_ENV) or "").strip()
    try:
        return max(0, int(raw)) if raw else 0
    except ValueError:
        return 0


def set_limits(wallet_id: str, asset: str, limits: SpendLimits) -> SpendLimits:
    with connection() as conn:
        conn.execute(
            "INSERT INTO wallet_limits (wallet_id, asset, per_tx_minor, daily_minor, per_destination_daily_minor) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(wallet_id, asset) DO UPDATE SET per_tx_minor = excluded.per_tx_minor, daily_minor = excluded.daily_minor, per_destination_daily_minor = excluded.per_destination_daily_minor",
            (str(wallet_id), str(asset).upper(), int(limits.per_tx_minor), int(limits.daily_minor), int(limits.per_destination_daily_minor)),
        )
    return limits


def _load_limits(conn, wallet_id: str, asset: str) -> SpendLimits:
    row = conn.execute("SELECT per_tx_minor, daily_minor, per_destination_daily_minor FROM wallet_limits WHERE wallet_id = ? AND asset = ?", (str(wallet_id), str(asset).upper())).fetchone()
    if row:
        return SpendLimits(int(row[0]), int(row[1]), int(row[2]))
    return SpendLimits(*DEFAULT_LIMITS.get(str(asset).upper(), FALLBACK_LIMITS))


def load_limits(wallet_id: str, asset: str) -> SpendLimits:
    with connection() as conn:
        return _load_limits(conn, wallet_id, asset)


def _held_within(conn, wallet_id: str, asset: str, since: float, destination: str | None = None, chain: str | None = None) -> int:
    """Everything currently counted against the window: reserved (in flight) plus settled,
    principal PLUS every reserved fee. The guard's eyes."""
    params: list = [str(wallet_id), str(asset).upper(), float(since), *_HELD_STATES]
    sql = "SELECT COALESCE(SUM(amount_minor + fee_minor), 0) FROM wallet_spend_ledger WHERE wallet_id = ? AND asset = ? AND spent_at >= ? AND state IN (?, ?)"
    if chain is not None:
        sql += " AND chain = ?"
        params.append(str(chain))
    if destination is not None:
        sql += " AND destination = ?"
        params.append(str(destination))
    row = conn.execute(sql, params).fetchone()
    return int(row[0] or 0)


def _global_held(conn, wallet_id: str, since: float, factors: dict[str, float]) -> int | None:
    """Every held amount converted into global-minor. An asset without a declared factor
    makes the global total incomparable: None, and the caller refuses rather than guesses."""
    rows = conn.execute(
        "SELECT asset, amount_minor, fee_minor FROM wallet_spend_ledger WHERE wallet_id = ? AND spent_at >= ? AND state IN (?, ?)",
        (str(wallet_id), float(since), *_HELD_STATES),
    ).fetchall()
    total = 0
    for asset, amount, fee in rows:
        factor = factors.get(str(asset).upper())
        if factor is None:
            return None
        total += round((amount + fee) * factor)
    return total


def _frozen(conn) -> bool:
    row = conn.execute("SELECT value FROM wallet_controls WHERE key = ?", (_FREEZE_KEY,)).fetchone()
    return bool(row) and str(row[0]) == "1"


def is_frozen() -> bool:
    """The panic freeze: when set, no amount can be held and no payment approved."""
    with connection() as conn:
        return _frozen(conn)


def set_frozen(frozen: bool) -> bool:
    """Flip the panic freeze; returns the PRIOR state."""
    from core.wallet.store import utcnow

    with connection() as conn:
        before = _frozen(conn)
        conn.execute("INSERT INTO wallet_controls (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at", (_FREEZE_KEY, "1" if frozen else "0", utcnow()))
        if before != bool(frozen):
            from core.wallet import controls

            # a signed transfer that has not been sent sees every freeze change, including one that was undone
            controls.bump(conn, "freeze")
    return before


def _verdict(conn, wallet_id: str, asset: str, amount: int, destination: str, moment: float, *, fee: int = 0, chain: str = "") -> LimitVerdict:
    if _frozen(conn):
        return LimitVerdict(False, LIMIT_FROZEN, "spending is frozen (panic freeze is on)")
    limits = _load_limits(conn, wallet_id, asset)
    total = amount + max(0, int(fee))
    if total > limits.per_tx_minor:
        return LimitVerdict(False, LIMIT_PER_TRANSACTION, f"{total} (with fees) > per-transaction cap {limits.per_tx_minor}")
    since = moment - DAY_SECONDS
    to_destination = _held_within(conn, wallet_id, asset, since, destination, chain or None)
    if to_destination + total > limits.per_destination_daily_minor:
        return LimitVerdict(False, LIMIT_PER_DESTINATION_DAILY, f"{to_destination + total} > per-destination daily cap {limits.per_destination_daily_minor}")
    today_on_chain = _held_within(conn, wallet_id, asset, since, chain=chain or None)
    if today_on_chain + total > limits.daily_minor:
        return LimitVerdict(False, LIMIT_DAILY, f"{today_on_chain + total} > daily cap {limits.daily_minor} on this chain")
    global_cap = global_daily_minor()
    if global_cap > 0:
        factors = _env_json(CONVERSIONS_ENV)
        held_global = _global_held(conn, wallet_id, since, factors)
        own_factor = factors.get(str(asset).upper())
        if held_global is None or own_factor is None:
            return LimitVerdict(False, LIMIT_GLOBAL_UNCOMPARABLE, "a held asset has no declared conversion; the global total cannot be compared honestly")
        if held_global + round(total * own_factor) > global_cap:
            return LimitVerdict(False, LIMIT_GLOBAL_DAILY, f"global converted total would exceed {global_cap}")
    return LimitVerdict(True, "", "within limits")


def _canonical_chain(chain: str) -> str:
    """One spelling per chain: a legacy name ("solana-devnet") and its CAIP-2 must hit the
    SAME per-chain window, or a mixed-caller day silently splits its budget."""
    try:
        from core.wallet import chains

        return chains.resolve_network(str(chain or "")).network
    except Exception:
        return str(chain or "")


def check_limits(wallet_id: str, asset: str, amount_minor: int, destination: str, *, now: float | None = None, fee_minor: int = 0, chain: str = "") -> LimitVerdict:
    """Advisory pre-check (simulation stage). The binding check is :func:`reserve_spend`."""
    moment = float(now if now is not None else time.time())
    with connection() as conn:
        return _verdict(conn, str(wallet_id), str(asset).upper(), amount_minor, str(destination), moment, fee=fee_minor, chain=_canonical_chain(chain))


def _begin_immediate(conn) -> None:
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:  # already inside a transaction on this handle: the lock is ours
        if "within a transaction" not in str(exc):
            raise


class LimitRefusedError(Exception):
    """A reservation refused by a ceiling or the freeze; carries the verdict."""

    def __init__(self, verdict: LimitVerdict) -> None:
        super().__init__(verdict.reason)
        self.verdict = verdict


class HoldStateConflictError(Exception):
    """A hold was not in the state the operation requires (already held, not reserved, unknown)."""


def _fee_hold_id(proposal_id: str) -> str:
    """The companion hold of a token transfer's network fee, kept in the native coin: one row per asset."""
    return f"{proposal_id}:fee"


def _token_transfer(chain: str, asset: str) -> bool:
    """A registered token on a Solana row: its principal and its fee are denominated in different assets."""
    if not chain:
        return False
    try:
        from core.wallet import svm_tokens

        return svm_tokens.is_token_transfer(chain, asset)
    except Exception:
        return False


def _native_symbol(chain: str) -> str:
    from core.wallet import chains

    return chains.native_asset(chain).symbol.upper()


def _has_hold(conn, hold_id: str) -> bool:
    return conn.execute("SELECT 1 FROM wallet_spend_ledger WHERE proposal_id = ?", (str(hold_id),)).fetchone() is not None


def _reserve(conn, *, wallet_id: str, asset: str, amount_minor: int, destination: str, proposal_id: str, now: float, fee_minor: int = 0, chain: str = "") -> LimitVerdict:
    """Hold the amount PLUS the reserved fee against every ceiling on the caller's connection, which holds BEGIN
    IMMEDIATE. A token transfer holds its principal in the token and its fee in the native coin -- two rows, each
    checked against its own asset's ceilings -- so a fee in lamports is never added to a token amount. Raises
    LimitRefusedError on a ceiling and HoldStateConflictError when this proposal already holds."""
    fee = max(0, fee_minor)
    chain = _canonical_chain(chain)
    split = _token_transfer(chain, asset)
    ids = (str(proposal_id), _fee_hold_id(proposal_id)) if split else (str(proposal_id),)
    placeholders = ", ".join("?" for _ in ids)
    existing = {str(r[0]): str(r[1]) for r in conn.execute(f"SELECT proposal_id, state FROM wallet_spend_ledger WHERE proposal_id IN ({placeholders})", ids).fetchall()}
    held = [state for state in existing.values() if state in _HELD_STATES]
    if held:
        raise HoldStateConflictError(f"already_held:{held[0]}")
    verdict = _verdict(conn, str(wallet_id), str(asset).upper(), amount_minor, str(destination), float(now), fee=0 if split else fee, chain=chain)
    if not verdict.ok:
        raise LimitRefusedError(verdict)
    holds = [(str(proposal_id), str(asset).upper(), amount_minor, 0 if split else fee)]
    if split:
        native = _native_symbol(chain)
        fee_verdict = _verdict(conn, str(wallet_id), native, 0, str(destination), float(now), fee=fee, chain=chain)
        if not fee_verdict.ok:
            raise LimitRefusedError(fee_verdict)
        holds.append((_fee_hold_id(proposal_id), native, 0, fee))
    for hold_id, hold_asset, hold_amount, hold_fee in holds:
        if hold_id in existing:
            conn.execute(
                "UPDATE wallet_spend_ledger SET state = ?, spent_at = ?, amount_minor = ?, fee_minor = ?, chain = ? WHERE proposal_id = ?",
                (RESERVATION_RESERVED, float(now), hold_amount, hold_fee, str(chain or ""), hold_id),
            )
        else:
            conn.execute(
                "INSERT INTO wallet_spend_ledger (wallet_id, asset, amount_minor, fee_minor, destination, proposal_id, spent_at, state, chain) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(wallet_id), hold_asset, hold_amount, hold_fee, str(destination), hold_id, float(now), RESERVATION_RESERVED, str(chain or "")),
            )
    return verdict


def _settle(conn, proposal_id: str, *, charged_fee_minor: int | None, amount_moved: bool, now: float) -> None:
    """Settle a reserved hold to what the chain charged, on the caller's connection.

    Caps count ``amount_minor + fee_minor``: an amount that did not move (failed on chain) counts 0, a known charged fee
    replaces the reserved maximum, and an absent fee keeps the reserved maximum counted (never 0). ``spent_at`` stays at
    reservation time, so the hold keeps its daily window; ``settled_at`` records when it settled. A token transfer's
    fee is settled on its native companion row; its principal row keeps a zero fee. Raises HoldStateConflictError
    unless exactly one reserved hold (and its fee companion, when it has one) changed."""
    fee_row = _has_hold(conn, _fee_hold_id(proposal_id))
    charged = None if charged_fee_minor is None else max(0, int(charged_fee_minor))
    cursor = conn.execute(
        "UPDATE wallet_spend_ledger SET state = ?, amount_minor = CASE WHEN ? THEN amount_minor ELSE 0 END, "
        "fee_minor = COALESCE(?, fee_minor), settled_at = ? WHERE proposal_id = ? AND state = ?",
        (RESERVATION_SETTLED, 1 if amount_moved else 0, None if fee_row else charged, float(now), str(proposal_id), RESERVATION_RESERVED),
    )
    if cursor.rowcount != 1:
        raise HoldStateConflictError(f"settle_found_no_reserved_hold:{proposal_id}")
    if fee_row:
        cursor = conn.execute(
            "UPDATE wallet_spend_ledger SET state = ?, fee_minor = COALESCE(?, fee_minor), settled_at = ? WHERE proposal_id = ? AND state = ?",
            (RESERVATION_SETTLED, charged, float(now), _fee_hold_id(proposal_id), RESERVATION_RESERVED),
        )
        if cursor.rowcount != 1:
            raise HoldStateConflictError(f"settle_found_no_reserved_fee_hold:{proposal_id}")


def _release(conn, proposal_id: str) -> None:
    """Release a reserved hold (and a token transfer's fee companion) on the caller's connection. Raises
    HoldStateConflictError unless the principal hold changed."""
    cursor = conn.execute("UPDATE wallet_spend_ledger SET state = ? WHERE proposal_id = ? AND state = ?", (RESERVATION_RELEASED, str(proposal_id), RESERVATION_RESERVED))
    if cursor.rowcount != 1:
        raise HoldStateConflictError(f"release_found_no_reserved_hold:{proposal_id}")
    conn.execute("UPDATE wallet_spend_ledger SET state = ? WHERE proposal_id = ? AND state = ?", (RESERVATION_RELEASED, _fee_hold_id(proposal_id), RESERVATION_RESERVED))


def _resettle(conn, proposal_id: str, *, charged_fee_minor: int | None, amount_moved: bool) -> None:
    """Correct a settled hold once the chain answers for a transfer the owner stopped waiting on: an amount that did not
    move counts 0 and a known fee replaces the maximum (on a token transfer's native fee companion). ``spent_at`` and
    ``settled_at`` stay. Raises HoldStateConflictError unless exactly one settled hold changed."""
    fee_row = _has_hold(conn, _fee_hold_id(proposal_id))
    charged = None if charged_fee_minor is None else max(0, int(charged_fee_minor))
    cursor = conn.execute(
        "UPDATE wallet_spend_ledger SET amount_minor = CASE WHEN ? THEN amount_minor ELSE 0 END, fee_minor = COALESCE(?, fee_minor) "
        "WHERE proposal_id = ? AND state = ?",
        (1 if amount_moved else 0, None if fee_row else charged, str(proposal_id), RESERVATION_SETTLED),
    )
    if cursor.rowcount != 1:
        raise HoldStateConflictError(f"resettle_found_no_settled_hold:{proposal_id}")
    if fee_row:
        conn.execute("UPDATE wallet_spend_ledger SET fee_minor = COALESCE(?, fee_minor) WHERE proposal_id = ? AND state = ?", (charged, _fee_hold_id(proposal_id), RESERVATION_SETTLED))


def reserve_spend(*, wallet_id: str, asset: str, amount_minor: int, destination: str, proposal_id: str, now: float | None = None, fee_minor: int = 0, chain: str = "") -> LimitVerdict:
    """Hold the amount PLUS every reserved fee against every ceiling, atomically. Idempotent per proposal."""
    moment = float(now if now is not None else time.time())
    with connection() as conn:
        _begin_immediate(conn)
        try:
            return _reserve(conn, wallet_id=wallet_id, asset=asset, amount_minor=amount_minor, destination=destination, proposal_id=proposal_id, now=moment, fee_minor=fee_minor, chain=chain)
        except HoldStateConflictError:
            return LimitVerdict(True, "", "already held")
        except LimitRefusedError as refused:
            return refused.verdict


def settle_spend(proposal_id: str, *, now: float | None = None) -> None:
    moment = float(now if now is not None else time.time())
    with connection() as conn:
        conn.execute(
            "UPDATE wallet_spend_ledger SET state = ?, spent_at = ?, settled_at = ? WHERE proposal_id IN (?, ?) AND state = ?",
            (RESERVATION_SETTLED, moment, moment, str(proposal_id), _fee_hold_id(proposal_id), RESERVATION_RESERVED),
        )


def release_spend(proposal_id: str) -> None:
    with connection() as conn:
        try:
            _release(conn, proposal_id)
        except HoldStateConflictError:
            return  # nothing reserved: the legacy helper has always been silent here


def reservation_state(proposal_id: str) -> str:
    with connection() as conn:
        row = conn.execute("SELECT state FROM wallet_spend_ledger WHERE proposal_id = ?", (str(proposal_id),)).fetchone()
    return str(row[0]) if row else ""


def spent_today(wallet_id: str, asset: str, *, now: float | None = None, chain: str = "") -> int:
    moment = float(now if now is not None else time.time())
    with connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount_minor + fee_minor), 0) FROM wallet_spend_ledger WHERE wallet_id = ? AND asset = ? AND spent_at >= ? AND state = ?" + (" AND chain = ?" if chain else ""),
            (str(wallet_id), str(asset).upper(), moment - DAY_SECONDS, RESERVATION_SETTLED, str(chain)) if chain else (str(wallet_id), str(asset).upper(), moment - DAY_SECONDS, RESERVATION_SETTLED),
        ).fetchone()
    return int(row[0] or 0)


def record_spend(*, wallet_id: str, asset: str, amount_minor: int, destination: str, proposal_id: str, now: float | None = None, fee_minor: int = 0, chain: str = "") -> None:
    """Compatibility: a spend known to have happened lands settled (reserve + settle in one step)."""
    moment = float(now if now is not None else time.time())
    with connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO wallet_spend_ledger (wallet_id, asset, amount_minor, fee_minor, destination, proposal_id, spent_at, state, chain) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (str(wallet_id), str(asset).upper(), int(amount_minor), max(0, int(fee_minor)), str(destination), str(proposal_id), moment, RESERVATION_SETTLED, str(chain or "")),
        )
        conn.execute("UPDATE wallet_spend_ledger SET state = ? WHERE proposal_id = ? AND state = ?", (RESERVATION_SETTLED, str(proposal_id), RESERVATION_RESERVED))


__all__ = [
    "DAY_SECONDS",
    "DEFAULT_LIMITS",
    "FALLBACK_LIMITS",
    "LIMIT_DAILY",
    "LIMIT_FROZEN",
    "LIMIT_GLOBAL_DAILY",
    "LIMIT_GLOBAL_UNCOMPARABLE",
    "LIMIT_PER_DESTINATION_DAILY",
    "LIMIT_PER_TRANSACTION",
    "RESERVATION_RELEASED",
    "RESERVATION_RESERVED",
    "RESERVATION_SETTLED",
    "LimitVerdict",
    "SpendLimits",
    "check_limits",
    "global_daily_minor",
    "is_frozen",
    "load_limits",
    "record_spend",
    "release_spend",
    "reservation_state",
    "reserve_spend",
    "set_frozen",
    "set_limits",
    "settle_spend",
    "spent_today",
]
