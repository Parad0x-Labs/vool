from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from core import policy_engine
from storage.db import get_connection
from storage.migrations import run_migrations

LEDGER_MODE = "simulated"
# USDC has 6 decimal places on Solana — the smallest transferable unit ("atomic"
# unit) is 1e-6 USDC. A whole-USDC amount maps to ``amount * 10**USDC_DECIMALS``
# atomic units. We round (not truncate) to the nearest atomic unit so fractional
# value is not silently dropped, and reject amounts that round to 0.
USDC_DECIMALS = 6
USDC_ATOMIC_PER_UNIT = 10 ** USDC_DECIMALS
# Settlement modes that denote a payout backed by a REAL on-chain x402 receipt.
# A receipt is "real" when its hash is a 64-char hex SHA-256 digest (see
# core.x402.client.X402Receipt) rather than a "stub-*" placeholder. Such payouts
# earn full proof-of-settlement reputation; LEDGER_MODE ("simulated") earns less.
SETTLED_MODES = ("mainnet", "devnet")
_LEDGER_TABLE_READY = False
_LEDGER_TABLE_LOCK = Lock()


@dataclass(frozen=True)
class LedgerReconciliation:
    peer_id: str
    balance: float
    entries: int
    mode: str


@dataclass(frozen=True)
class DispatchBudgetReservation:
    allowed: bool
    mode: str
    reason: str
    amount: float
    paid_credits_charged: float
    free_tier_points_used: float
    free_tier_points_limit: float


def credit_purchases_enabled() -> bool:
    return bool(policy_engine.get("economics.credit_purchase_enabled", False))


def starter_credits_enabled() -> bool:
    return bool(policy_engine.get("economics.starter_credits_enabled", True))


def starter_credit_amount() -> float:
    try:
        return max(0.0, float(policy_engine.get("economics.starter_credits_amount", 24.0)))
    except (TypeError, ValueError):
        return 24.0


def usdc_to_atomic(amount_usdc: float) -> int:
    """Convert a USDC amount to integer atomic units (1 unit = 1e-6 USDC).

    The amount is **rounded** to the nearest atomic unit (banker's rounding via
    :func:`round`) rather than truncated, so fractional value below 1e-6 is not
    silently discarded on the way down. A positive amount that rounds to 0 atomic
    units (i.e. below half an atomic unit) is rejected with :class:`ValueError`
    rather than emitting a silent zero-value transfer.

    Parameters
    ----------
    amount_usdc:
        Whole-USDC amount, e.g. ``0.001`` for one milli-USDC.

    Returns
    -------
    int
        The amount expressed in atomic units (always ``> 0``).

    Raises
    ------
    ValueError
        If ``amount_usdc`` is non-finite, ``<= 0``, or rounds to 0 atomic units.
    """
    try:
        value = float(amount_usdc)
    except (TypeError, ValueError):
        raise ValueError(f"amount_usdc is not a number: {amount_usdc!r}") from None
    if value != value or value in (float("inf"), float("-inf")):  # NaN / inf guard
        raise ValueError(f"amount_usdc must be finite, got {amount_usdc!r}")
    if value <= 0:
        raise ValueError(f"amount_usdc must be > 0, got {value}")
    atomic = round(value * USDC_ATOMIC_PER_UNIT)
    if atomic <= 0:
        raise ValueError(
            f"amount_usdc={value} rounds to 0 atomic units "
            f"(< {1 / USDC_ATOMIC_PER_UNIT:.0e} USDC); refusing silent zero-value transfer"
        )
    return atomic


def _allocate_pool_atomic(pool_atomic: int, shares: int) -> list[int]:
    """Split ``pool_atomic`` atomic units across ``shares`` recipients exactly.

    Uses integer division so no atomic unit is lost to float/round drift; the
    leftover remainder is handed out one unit at a time to the leading recipients
    so the returned allocations always sum to ``pool_atomic`` exactly.
    """
    if shares <= 0:
        return []
    base = pool_atomic // shares
    remainder = pool_atomic - base * shares
    allocations = [base + (1 if i < remainder else 0) for i in range(shares)]
    return allocations


def _init_ledger_table() -> None:
    global _LEDGER_TABLE_READY
    if _LEDGER_TABLE_READY:
        return
    with _LEDGER_TABLE_LOCK:
        if _LEDGER_TABLE_READY:
            return
        # Ledger schema is owned by storage migrations; do not fork DDL here.
        run_migrations()
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='compute_credit_ledger' LIMIT 1"
            ).fetchone()
            if not row:
                raise RuntimeError("compute_credit_ledger table is missing after migrations.")
            _LEDGER_TABLE_READY = True
        finally:
            conn.close()


def _init_dispatch_budget_table() -> None:
    _init_ledger_table()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='swarm_dispatch_budget_events' LIMIT 1"
        ).fetchone()
        if not row:
            raise RuntimeError("swarm_dispatch_budget_events table is missing after migrations.")
    finally:
        conn.close()


def _utcnow_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _unique_receipt_id(prefix: str) -> str:
    """Build a collision-proof auto-generated receipt/escrow id.

    ``_utcnow_iso`` only has second granularity, so two distinct ops in the same
    wall-clock second that fall back to a timestamped default would share an id
    and the second one would be mis-detected as a replay. We append a uuid4 hex
    so every auto-generated id is unique. Callers that pass an explicit
    ``receipt_id`` keep their exact value (idempotency is preserved).
    """
    return f"{prefix}:{uuid.uuid4().hex}"


def _utc_day_bucket(now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _dispatch_limits() -> tuple[float, float]:
    try:
        daily_limit = max(0.0, float(policy_engine.get("economics.free_tier_daily_swarm_points", 24.0)))
    except (TypeError, ValueError):
        daily_limit = 24.0
    try:
        per_dispatch_limit = max(0.0, float(policy_engine.get("economics.free_tier_max_dispatch_points", 12.0)))
    except (TypeError, ValueError):
        per_dispatch_limit = 12.0
    return daily_limit, per_dispatch_limit


def _dispatch_receipt_record(
    conn, receipt_id: str | None, *, reason: str | None = None
) -> tuple[str, float] | None:
    """Find a prior dispatch reservation that reused ``receipt_id``.

    A paid dispatch reservation is recorded in ``compute_credit_ledger`` as a
    **charge** (negative ``amount``) with the dispatch ``reason``. Matching on the
    ``receipt_id`` alone is too loose: the same id can also appear on a positive
    award/refund/transfer-receive row, which would be mis-reported here as a prior
    *paid* dispatch and select the wrong row. We therefore tighten the match to
    the dispatch debit (``amount < 0``) and, when a ``reason`` is supplied, to that
    exact reason — so only a genuine prior reservation for this dispatch is
    treated as a replay.
    """
    if not receipt_id:
        return None
    if reason is not None:
        row = conn.execute(
            """
            SELECT 'paid' AS dispatch_mode, ABS(amount) AS amount
            FROM compute_credit_ledger
            WHERE receipt_id = ? AND amount < 0 AND reason = ?
            LIMIT 1
            """,
            (receipt_id, reason),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT 'paid' AS dispatch_mode, ABS(amount) AS amount
            FROM compute_credit_ledger
            WHERE receipt_id = ? AND amount < 0
            LIMIT 1
            """,
            (receipt_id,),
        ).fetchone()
    if row:
        return str(row["dispatch_mode"]), float(row["amount"] or 0.0)
    if reason is not None:
        row = conn.execute(
            """
            SELECT dispatch_mode, amount
            FROM swarm_dispatch_budget_events
            WHERE receipt_id = ? AND reason = ?
            LIMIT 1
            """,
            (receipt_id, reason),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT dispatch_mode, amount
            FROM swarm_dispatch_budget_events
            WHERE receipt_id = ?
            LIMIT 1
            """,
            (receipt_id,),
        ).fetchone()
    if row:
        return str(row["dispatch_mode"]), float(row["amount"] or 0.0)
    return None


def _credit_balance_in_tx(conn, peer_id: str) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM compute_credit_ledger WHERE peer_id = ?",
        (peer_id,),
    ).fetchone()
    return float(row["total"]) if row else 0.0


def _free_tier_usage_in_tx(conn, peer_id: str, *, day_bucket: str) -> float:
    row = conn.execute(
        """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM swarm_dispatch_budget_events
        WHERE peer_id = ?
          AND day_bucket = ?
          AND dispatch_mode = 'free_tier'
        """,
        (peer_id, day_bucket),
    ).fetchone()
    return float(row["total"]) if row else 0.0


def get_credit_balance(peer_id: str) -> float:
    _init_ledger_table()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM compute_credit_ledger WHERE peer_id = ?",
            (peer_id,),
        ).fetchone()
        return float(row["total"]) if row else 0.0
    finally:
        conn.close()


def list_credit_ledger_entries(peer_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
    _init_ledger_table()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT peer_id, amount, reason, receipt_id, settlement_mode, timestamp
            FROM compute_credit_ledger
            WHERE peer_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (peer_id, max(1, min(int(limit), 50))),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def estimate_hive_task_credit_cost(
    title: str,
    summary: str,
    *,
    topic_tags: list[str] | None = None,
    auto_start_research: bool = False,
) -> float:
    clean_title = " ".join(str(title or "").split()).strip().lower()
    clean_summary = " ".join(str(summary or "").split()).strip().lower()
    title_tokens = len(clean_title.split())
    summary_tokens = len(clean_summary.split())
    tag_set = {str(item or "").strip().lower() for item in list(topic_tags or []) if str(item).strip()}
    complexity_hits = sum(
        1
        for marker in (
            "research",
            "security",
            "privacy",
            "architecture",
            "integration",
            "migration",
            "design",
            "economy",
            "credits",
        )
        if marker in clean_title or marker in clean_summary or marker in tag_set
    )
    estimate = 4.0
    estimate += min(2.0, title_tokens / 8.0)
    estimate += min(8.0, summary_tokens / 24.0)
    estimate += min(3.0, len(tag_set) * 0.5)
    estimate += min(3.0, complexity_hits * 0.6)
    if auto_start_research:
        estimate += 2.0
    rounded = round(max(2.0, min(24.0, estimate)) * 2.0) / 2.0
    return float(rounded)


def get_free_tier_dispatch_usage(peer_id: str, *, day_bucket: str | None = None) -> float:
    _init_dispatch_budget_table()
    conn = get_connection()
    try:
        return _free_tier_usage_in_tx(conn, peer_id, day_bucket=day_bucket or _utc_day_bucket())
    finally:
        conn.close()


def _receipt_exists(conn, receipt_id: str | None) -> bool:
    if not receipt_id:
        return False
    row = conn.execute(
        "SELECT 1 FROM compute_credit_ledger WHERE receipt_id = ? LIMIT 1",
        (receipt_id,),
    ).fetchone()
    return bool(row)


def receipt_exists(receipt_id: str | None) -> bool:
    """True if any ledger entry already used ``receipt_id`` (a consumed replay key)."""
    if not receipt_id:
        return False
    _init_ledger_table()
    conn = get_connection()
    try:
        return _receipt_exists(conn, receipt_id)
    finally:
        conn.close()


def is_real_receipt_hash(receipt_hash: str | None) -> bool:
    """True when ``receipt_hash`` is a real on-chain x402 receipt digest.

    Real receipts carry a 64-char hex SHA-256 hash. "stub-*" placeholders and
    empty/None values are NOT real — they get no proof-of-settlement weight.
    """
    if not receipt_hash:
        return False
    value = str(receipt_hash).strip().lower()
    if value.startswith("stub"):
        return False
    if len(value) != 64:
        return False
    return all(ch in "0123456789abcdef" for ch in value)


def settlement_mode_for_receipt_hash(receipt_hash: str | None, *, mode_hint: str | None = None) -> str:
    """Resolve the ledger settlement_mode for a payout.

    Returns ``LEDGER_MODE`` ("simulated") unless a real on-chain receipt hash is
    present, in which case it returns the on-chain cluster mode. ``mode_hint``
    (the x402 receipt's own "mainnet"/"devnet" tag) is honored when valid;
    otherwise a real receipt defaults to "mainnet".

    NOTE (audit 2026-07-19, docs/AUDIT_2026-07-19.md #19): a 64-hex SHA-256 is
    self-claimable, so this default-to-mainnet trusts an unverified hash. No live
    caller currently supplies one (the mesh economy is parked/loopback-only, all
    live entries stay "simulated"), so this is a flagged design decision, not a
    live exposure: whether a bare real-looking hash without a verified cluster tag
    should earn settled weight is intentionally left to a chain-verification pass
    rather than flipped here (it would change the proof-of-settlement contract).
    """
    if not is_real_receipt_hash(receipt_hash):
        return LEDGER_MODE
    hint = str(mode_hint or "").strip().lower()
    if hint in SETTLED_MODES:
        return hint
    return "mainnet"


def award_credits(peer_id: str, amount: float, reason: str = "provider_reward", *, receipt_id: str | None = None) -> bool:
    if amount <= 0:
        return False
    _init_ledger_table()
    now_iso = _utcnow_iso()
    conn = get_connection()
    try:
        if conn.in_transaction:
            conn.rollback()
        conn.execute("BEGIN IMMEDIATE")
        if _receipt_exists(conn, receipt_id):
            conn.rollback()
            return False
        conn.execute(
            """
            INSERT INTO compute_credit_ledger (
                peer_id, amount, reason, receipt_id, settlement_mode, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (peer_id, amount, reason, receipt_id, LEDGER_MODE, now_iso),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def ensure_starter_credits(peer_id: str, *, receipt_id: str | None = None) -> bool:
    clean_peer_id = str(peer_id or "").strip()
    if not clean_peer_id or not starter_credits_enabled():
        return False
    amount = starter_credit_amount()
    if amount <= 0:
        return False
    resolved_receipt = str(receipt_id or f"starter-bootstrap:{clean_peer_id}").strip()
    for attempt in range(3):
        if award_credits(
            clean_peer_id,
            amount,
            reason="starter_bootstrap",
            receipt_id=resolved_receipt,
        ):
            return True
        ledger = reconcile_ledger(clean_peer_id)
        if int(ledger.entries or 0) > 0:
            return False
        if attempt < 2:
            time.sleep(0.15)
    return False


def burn_credits(peer_id: str, amount: float, reason: str = "task_dispatch", *, receipt_id: str | None = None) -> bool:
    if amount <= 0:
        return True
    _init_ledger_table()
    conn = get_connection()
    try:
        if conn.in_transaction:
            conn.rollback()
        conn.execute("BEGIN IMMEDIATE")
        if _receipt_exists(conn, receipt_id):
            conn.rollback()
            return False

        now_iso = _utcnow_iso()
        cur = conn.execute(
            """
            INSERT INTO compute_credit_ledger (
                peer_id, amount, reason, receipt_id, settlement_mode, timestamp
            )
            SELECT ?, ?, ?, ?, ?, ?
            WHERE (
                SELECT COALESCE(SUM(amount), 0)
                FROM compute_credit_ledger
                WHERE peer_id = ?
            ) >= ?
            """,
            (peer_id, -amount, reason, receipt_id, LEDGER_MODE, now_iso, peer_id, amount),
        )
        if int(cur.rowcount or 0) != 1:
            conn.rollback()
            return False

        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def reserve_swarm_dispatch_budget(
    peer_id: str,
    amount: float,
    reason: str = "task_dispatch",
    *,
    receipt_id: str | None = None,
    metadata: dict | None = None,
) -> DispatchBudgetReservation:
    charge_amount = max(0.0, float(amount or 0.0))
    daily_limit, per_dispatch_limit = _dispatch_limits()
    if charge_amount <= 0:
        return DispatchBudgetReservation(
            allowed=True,
            mode="zero_cost",
            reason="zero_cost_dispatch",
            amount=0.0,
            paid_credits_charged=0.0,
            free_tier_points_used=get_free_tier_dispatch_usage(peer_id),
            free_tier_points_limit=daily_limit,
        )

    _init_dispatch_budget_table()
    now_iso = _utcnow_iso()
    day_bucket = _utc_day_bucket()
    conn = get_connection()
    try:
        if conn.in_transaction:
            conn.rollback()
        conn.execute("BEGIN IMMEDIATE")

        existing = _dispatch_receipt_record(conn, receipt_id, reason=reason)
        if existing:
            mode, reserved_amount = existing
            used = _free_tier_usage_in_tx(conn, peer_id, day_bucket=day_bucket)
            conn.rollback()
            return DispatchBudgetReservation(
                allowed=True,
                mode=mode,
                reason="receipt_reused",
                amount=float(reserved_amount),
                paid_credits_charged=float(reserved_amount) if mode == "paid" else 0.0,
                free_tier_points_used=used,
                free_tier_points_limit=daily_limit,
            )

        balance = _credit_balance_in_tx(conn, peer_id)
        if balance >= charge_amount:
            escrow_id = receipt_id or _unique_receipt_id(f"escrow:{reason}")
            conn.execute(
                """
                INSERT INTO compute_credit_ledger (
                    peer_id, amount, reason, receipt_id, settlement_mode, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (peer_id, -charge_amount, reason, escrow_id, LEDGER_MODE, now_iso),
            )
            task_id = str(metadata.get("parent_task_id", "") if metadata else "") or reason.removeprefix("dispatch_task:").strip()
            if task_id:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO dispatch_credit_escrow (
                        escrow_id, parent_task_id, poster_peer_id,
                        total_escrowed, total_released, total_refunded,
                        status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 0, 0, 'active', ?, ?)
                    """,
                    (escrow_id, task_id, peer_id, charge_amount, now_iso, now_iso),
                )
            conn.commit()
            return DispatchBudgetReservation(
                allowed=True,
                mode="paid",
                reason="credits_escrowed",
                amount=charge_amount,
                paid_credits_charged=charge_amount,
                free_tier_points_used=_free_tier_usage_in_tx(conn, peer_id, day_bucket=day_bucket),
                free_tier_points_limit=daily_limit,
            )

        used = _free_tier_usage_in_tx(conn, peer_id, day_bucket=day_bucket)
        if charge_amount > per_dispatch_limit:
            conn.rollback()
            return DispatchBudgetReservation(
                allowed=False,
                mode="blocked",
                reason="task_cost_exceeds_free_tier_cap",
                amount=charge_amount,
                paid_credits_charged=0.0,
                free_tier_points_used=used,
                free_tier_points_limit=daily_limit,
            )
        if used + charge_amount > daily_limit:
            conn.rollback()
            return DispatchBudgetReservation(
                allowed=False,
                mode="blocked",
                reason="daily_free_tier_budget_exhausted",
                amount=charge_amount,
                paid_credits_charged=0.0,
                free_tier_points_used=used,
                free_tier_points_limit=daily_limit,
            )

        conn.execute(
            """
            INSERT INTO swarm_dispatch_budget_events (
                peer_id, day_bucket, amount, dispatch_mode, reason, receipt_id, metadata_json, created_at
            ) VALUES (?, ?, ?, 'free_tier', ?, ?, ?, ?)
            """,
            (
                peer_id,
                day_bucket,
                charge_amount,
                reason,
                receipt_id,
                json.dumps(metadata or {}, sort_keys=True),
                now_iso,
            ),
        )
        conn.commit()
        return DispatchBudgetReservation(
            allowed=True,
            mode="free_tier",
            reason="free_tier_reserved",
            amount=charge_amount,
            paid_credits_charged=0.0,
            free_tier_points_used=used + charge_amount,
            free_tier_points_limit=daily_limit,
        )
    except Exception:
        conn.rollback()
        used = get_free_tier_dispatch_usage(peer_id, day_bucket=day_bucket)
        return DispatchBudgetReservation(
            allowed=False,
            mode="blocked",
            reason="dispatch_budget_error",
            amount=charge_amount,
            paid_credits_charged=0.0,
            free_tier_points_used=used,
            free_tier_points_limit=daily_limit,
        )
    finally:
        conn.close()


def escrow_credits_for_task(
    poster_peer_id: str,
    parent_task_id: str,
    amount: float,
    *,
    receipt_id: str | None = None,
) -> bool:
    """Move credits from poster's balance into escrow for a dispatched task."""
    if amount <= 0:
        return True
    _init_ledger_table()
    # An explicit receipt_id is an idempotency key: a re-run that finds the same
    # row already present is a genuine no-op success. An auto-generated id is made
    # unique per call (second-granularity timestamps alone collide), so a match on
    # one would be an id clash that moved no funds — report that as failure rather
    # than a value-losing false 'success'.
    explicit_receipt = bool(receipt_id)
    escrow_id = receipt_id or _unique_receipt_id(f"escrow:{parent_task_id}")
    now_iso = _utcnow_iso()
    conn = get_connection()
    try:
        if conn.in_transaction:
            conn.rollback()
        conn.execute("BEGIN IMMEDIATE")
        if _receipt_exists(conn, escrow_id):
            conn.rollback()
            return explicit_receipt
        balance = _credit_balance_in_tx(conn, poster_peer_id)
        if balance < amount:
            conn.rollback()
            return False
        conn.execute(
            """
            INSERT INTO compute_credit_ledger (
                peer_id, amount, reason, receipt_id, settlement_mode, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (poster_peer_id, -amount, f"escrow_hold:{parent_task_id}", escrow_id, LEDGER_MODE, now_iso),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO dispatch_credit_escrow (
                escrow_id, parent_task_id, poster_peer_id,
                total_escrowed, total_released, total_refunded,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 0, 0, 'active', ?, ?)
            """,
            (escrow_id, parent_task_id, poster_peer_id, amount, now_iso, now_iso),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def release_escrow_to_helper(
    parent_task_id: str,
    helper_peer_id: str,
    payout: float,
    *,
    receipt_id: str | None = None,
    receipt_hash: str | None = None,
    settlement_mode_hint: str | None = None,
    settlement_verifier: Callable[..., bool] | None = None,
    payment_tx: str | None = None,
    payment_recipient_wallet: str | None = None,
    payment_amount_usdc: float | None = None,
) -> bool:
    """Transfer credits from task escrow to a helper who completed work.

    When ``receipt_hash`` is a real on-chain x402 receipt digest, the ledger row
    is stamped with the on-chain settlement mode ("mainnet"/"devnet") and the
    receipt hash, so the payout earns full proof-of-settlement reputation.
    Otherwise the row stays ``LEDGER_MODE`` ("simulated") exactly as before.

    Optional settlement gate (default OFF, fully backward-compatible)
    ----------------------------------------------------------------
    A caller-supplied ``receipt_hash`` is a SELF-CLAIM: an
    :class:`core.x402.client.X402Receipt` carries a real 64-hex SHA-256 hash even
    in stub mode, so a hash alone proves nothing about an on-chain payment. To
    close that reputation-inflation hole, pass ``settlement_verifier`` (e.g.
    :func:`core.x402.receipt_verifier.verify_payment_receipt`) together with the
    ``payment_tx`` / ``payment_recipient_wallet`` / ``payment_amount_usdc`` of the
    claimed settlement. The row is then stamped "mainnet"/"devnet" ONLY when the
    verifier confirms the matching USDC transfer on-chain; on any failure it
    falls back to ``LEDGER_MODE`` ("simulated").

    When ``settlement_verifier`` is ``None`` (the default, and what every existing
    caller passes), behavior is byte-for-byte identical to before: the mode is
    resolved from ``receipt_hash`` / ``settlement_mode_hint`` with no network call.
    """
    if payout <= 0:
        return True
    _init_ledger_table()
    release_receipt = receipt_id or f"escrow_release:{parent_task_id}:{helper_peer_id}"
    settlement_mode = settlement_mode_for_receipt_hash(receipt_hash, mode_hint=settlement_mode_hint)
    # Opt-in on-chain gate: a claimed "mainnet"/"devnet" settlement is only
    # honored when an injected verifier confirms the real USDC transfer. The
    # verifier is read-only and fails closed; any False / exception downgrades
    # the payout to "simulated" rather than rewarding an unverifiable claim.
    if settlement_verifier is not None and settlement_mode in SETTLED_MODES:
        verified = False
        try:
            verified = bool(
                settlement_verifier(
                    str(payment_tx or "").strip(),
                    recipient_wallet=str(payment_recipient_wallet or helper_peer_id).strip(),
                    amount_usdc=(
                        float(payment_amount_usdc)
                        if payment_amount_usdc is not None
                        else float(payout)
                    ),
                    mode=settlement_mode,
                )
            )
        except Exception:
            verified = False
        if not verified:
            settlement_mode = LEDGER_MODE
    stamped_hash = str(receipt_hash).strip() if settlement_mode in SETTLED_MODES else ""
    now_iso = _utcnow_iso()
    conn = get_connection()
    try:
        if conn.in_transaction:
            conn.rollback()
        conn.execute("BEGIN IMMEDIATE")
        if _receipt_exists(conn, release_receipt):
            conn.rollback()
            return True
        escrow = conn.execute(
            """
            SELECT escrow_id, total_escrowed, total_released, total_refunded
            FROM dispatch_credit_escrow
            WHERE parent_task_id = ? AND status = 'active'
            LIMIT 1
            """,
            (parent_task_id,),
        ).fetchone()
        if not escrow:
            conn.rollback()
            return False
        remaining = float(escrow["total_escrowed"]) - float(escrow["total_released"]) - float(escrow["total_refunded"])
        actual_payout = min(payout, max(0.0, remaining))
        if actual_payout <= 0:
            conn.rollback()
            return True
        conn.execute(
            """
            INSERT INTO compute_credit_ledger (
                peer_id, amount, reason, receipt_id, settlement_mode, receipt_hash, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                helper_peer_id,
                actual_payout,
                f"task_reward:{parent_task_id}",
                release_receipt,
                settlement_mode,
                stamped_hash,
                now_iso,
            ),
        )
        conn.execute(
            """
            UPDATE dispatch_credit_escrow
            SET total_released = total_released + ?, updated_at = ?
            WHERE escrow_id = ?
            """,
            (actual_payout, now_iso, escrow["escrow_id"]),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def refund_escrow_remainder(parent_task_id: str) -> float:
    """Return any unused escrow credits back to the poster. Returns amount refunded."""
    _init_ledger_table()
    now_iso = _utcnow_iso()
    conn = get_connection()
    try:
        if conn.in_transaction:
            conn.rollback()
        conn.execute("BEGIN IMMEDIATE")
        escrow = conn.execute(
            """
            SELECT escrow_id, poster_peer_id, total_escrowed, total_released, total_refunded
            FROM dispatch_credit_escrow
            WHERE parent_task_id = ? AND status = 'active'
            LIMIT 1
            """,
            (parent_task_id,),
        ).fetchone()
        if not escrow:
            conn.rollback()
            return 0.0
        remaining = float(escrow["total_escrowed"]) - float(escrow["total_released"]) - float(escrow["total_refunded"])
        if remaining <= 0:
            conn.execute(
                "UPDATE dispatch_credit_escrow SET status = 'settled', updated_at = ? WHERE escrow_id = ?",
                (now_iso, escrow["escrow_id"]),
            )
            conn.commit()
            return 0.0
        refund_receipt = f"escrow_refund:{parent_task_id}"
        if not _receipt_exists(conn, refund_receipt):
            conn.execute(
                """
                INSERT INTO compute_credit_ledger (
                    peer_id, amount, reason, receipt_id, settlement_mode, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (escrow["poster_peer_id"], remaining, f"escrow_refund:{parent_task_id}", refund_receipt, LEDGER_MODE, now_iso),
            )
        conn.execute(
            """
            UPDATE dispatch_credit_escrow
            SET total_refunded = total_refunded + ?, status = 'settled', updated_at = ?
            WHERE escrow_id = ?
            """,
            (remaining, now_iso, escrow["escrow_id"]),
        )
        conn.commit()
        return remaining
    except Exception:
        conn.rollback()
        return 0.0
    finally:
        conn.close()


def get_escrow_for_task(parent_task_id: str) -> dict | None:
    """Return the escrow state for a task, or None if no escrow exists."""
    _init_ledger_table()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT escrow_id, parent_task_id, poster_peer_id,
                   total_escrowed, total_released, total_refunded, status
            FROM dispatch_credit_escrow
            WHERE parent_task_id = ?
            LIMIT 1
            """,
            (parent_task_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "escrow_id": row["escrow_id"],
            "parent_task_id": row["parent_task_id"],
            "poster_peer_id": row["poster_peer_id"],
            "total_escrowed": float(row["total_escrowed"]),
            "total_released": float(row["total_released"]),
            "total_refunded": float(row["total_refunded"]),
            "remaining": float(row["total_escrowed"]) - float(row["total_released"]) - float(row["total_refunded"]),
            "status": row["status"],
        }
    finally:
        conn.close()


def settle_hive_task_escrow(
    parent_task_id: str,
    helper_peer_ids: list[str],
    *,
    result_status: str,
    receipt_prefix: str | None = None,
) -> dict[str, Any]:
    clean_task_id = str(parent_task_id or "").strip()
    normalized_status = str(result_status or "").strip().lower()
    unique_helpers: list[str] = []
    seen_helpers: set[str] = set()
    for helper_peer_id in list(helper_peer_ids or []):
        clean_helper = str(helper_peer_id or "").strip()
        if not clean_helper or clean_helper in seen_helpers:
            continue
        seen_helpers.add(clean_helper)
        unique_helpers.append(clean_helper)

    escrow_before = get_escrow_for_task(clean_task_id)
    if not clean_task_id or not escrow_before:
        return {
            "ok": False,
            "status": "no_active_escrow",
            "topic_id": clean_task_id,
            "settlements": [],
            "refunded_amount": 0.0,
        }

    if normalized_status == "solved":
        payout_fraction = 1.0
    elif normalized_status == "partial":
        payout_fraction = 0.5
    else:
        return {
            "ok": False,
            "status": "no_payout_for_status",
            "topic_id": clean_task_id,
            "settlements": [],
            "refunded_amount": 0.0,
            "remaining": float(escrow_before.get("remaining") or 0.0),
        }

    remaining_before = float(escrow_before.get("remaining") or 0.0)
    if remaining_before <= 0.0:
        return {
            "ok": False,
            "status": "empty_escrow",
            "topic_id": clean_task_id,
            "settlements": [],
            "refunded_amount": 0.0,
            "remaining": 0.0,
        }

    if not unique_helpers:
        refunded_amount = refund_escrow_remainder(clean_task_id) if normalized_status == "solved" else 0.0
        escrow_after = get_escrow_for_task(clean_task_id)
        return {
            "ok": normalized_status == "solved",
            "status": "refunded_without_helpers" if normalized_status == "solved" else "no_helpers",
            "topic_id": clean_task_id,
            "settlements": [],
            "refunded_amount": refunded_amount,
            "remaining": float((escrow_after or {}).get("remaining") or 0.0),
        }

    payout_pool = round(remaining_before * payout_fraction, 4)
    helper_count = len(unique_helpers)
    # Allocate in integer credit-ticks (1e-4 granularity, matching the 4-decimal
    # rounding used throughout this function) so no value is lost to float/round
    # drift. The leftover tick remainder is distributed deterministically to the
    # leading helpers; the per-helper allocations therefore sum to ``payout_pool``
    # exactly rather than dropping a fractional remainder.
    _CREDIT_TICKS = 10_000  # 1 credit = 10_000 ticks (4 decimal places)
    pool_ticks = round(payout_pool * _CREDIT_TICKS)
    allocation_ticks = _allocate_pool_atomic(pool_ticks, helper_count)
    allocations = [round(ticks / _CREDIT_TICKS, 4) for ticks in allocation_ticks]

    settlements: list[dict[str, Any]] = []
    total_released = 0.0
    prefix = str(receipt_prefix or f"hive_settlement:{clean_task_id}:{normalized_status}").strip()
    for index, helper_peer_id in enumerate(unique_helpers):
        allocation = allocations[index]
        payout_amount = max(0.0, float(allocation or 0.0))
        if payout_amount <= 0.0:
            continue
        receipt_id = f"{prefix}:{index}"
        ok = release_escrow_to_helper(
            clean_task_id,
            helper_peer_id,
            payout_amount,
            receipt_id=receipt_id,
        )
        if not ok:
            continue
        total_released += payout_amount
        settlements.append(
            {
                "helper_peer_id": helper_peer_id,
                "amount": payout_amount,
                "receipt_id": receipt_id,
            }
        )

    refunded_amount = refund_escrow_remainder(clean_task_id) if normalized_status == "solved" else 0.0
    escrow_after = get_escrow_for_task(clean_task_id)
    return {
        "ok": bool(settlements) or refunded_amount > 0.0,
        "status": "settled" if normalized_status == "solved" else "partially_settled",
        "topic_id": clean_task_id,
        "result_status": normalized_status,
        "settlements": settlements,
        "released_amount": round(total_released, 4),
        "refunded_amount": round(refunded_amount, 4),
        "remaining": float((escrow_after or {}).get("remaining") or 0.0),
    }


def transfer_credits(
    from_peer_id: str,
    to_peer_id: str,
    amount: float,
    reason: str = "peer_transfer",
    *,
    receipt_id: str | None = None,
) -> bool:
    """Transfer credits between peers. Atomic: debit sender + credit receiver in one tx."""
    if amount <= 0 or from_peer_id == to_peer_id:
        return False
    _init_ledger_table()
    now_iso = _utcnow_iso()
    send_receipt = receipt_id or _unique_receipt_id(f"transfer:{from_peer_id}:{to_peer_id}:{now_iso}")
    recv_receipt = f"{send_receipt}:recv"
    conn = get_connection()
    try:
        if conn.in_transaction:
            conn.rollback()
        conn.execute("BEGIN IMMEDIATE")
        if _receipt_exists(conn, send_receipt):
            conn.rollback()
            return False
        balance = _credit_balance_in_tx(conn, from_peer_id)
        if balance < amount:
            conn.rollback()
            return False
        conn.execute(
            "INSERT INTO compute_credit_ledger (peer_id, amount, reason, receipt_id, settlement_mode, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (from_peer_id, -amount, f"sent:{reason}", send_receipt, LEDGER_MODE, now_iso),
        )
        conn.execute(
            "INSERT INTO compute_credit_ledger (peer_id, amount, reason, receipt_id, settlement_mode, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (to_peer_id, amount, f"received:{reason}", recv_receipt, LEDGER_MODE, now_iso),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def award_presence_credits(peer_id: str, amount: float = 0.10, *, receipt_id: str | None = None) -> bool:
    """Award a small credit for responding to a heartbeat health check."""
    return award_credits(peer_id, amount, "presence_heartbeat", receipt_id=receipt_id)


def reconcile_ledger(peer_id: str) -> LedgerReconciliation:
    _init_ledger_table()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS total, COUNT(*) AS entries
            FROM compute_credit_ledger
            WHERE peer_id = ?
            """,
            (peer_id,),
        ).fetchone()
        return LedgerReconciliation(
            peer_id=peer_id,
            balance=float(row["total"]) if row else 0.0,
            entries=int(row["entries"]) if row else 0,
            mode=LEDGER_MODE,
        )
    finally:
        conn.close()
