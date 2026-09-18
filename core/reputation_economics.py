"""Reputation decay and stake/bond mechanism for the economics layer."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from storage.db import get_connection

logger = logging.getLogger(__name__)

# ── Reputation Decay ───────────────────────────────────────────────────
# Scores decay toward 0.5 (neutral) at a rate of ~1% per day of inactivity.
_DECAY_RATE = 0.01
_DECAY_INTERVAL_HOURS = 24


def decay_reputation_scores(*, dry_run: bool = False) -> list[dict[str, Any]]:
    """Apply time-based reputation decay to inactive peers.

    Peers who haven't participated in tasks for >24h have their scores
    decayed toward 0.5 (neutral baseline).
    """
    conn = get_connection()
    cutoff_hours = _DECAY_INTERVAL_HOURS
    results: list[dict[str, Any]] = []

    try:
        rows = conn.execute(
            """
            SELECT peer_id, composite_score, tasks_completed, last_active_at
            FROM scoreboard
            WHERE last_active_at IS NOT NULL
            """,
        ).fetchall()

        for row in rows:
            peer_id = row["peer_id"]
            score = float(row["composite_score"] or 0.5)
            last_active = row["last_active_at"]

            if not last_active:
                continue

            try:
                last_active_dt = datetime.fromisoformat(str(last_active).replace("Z", "+00:00"))
            except Exception:
                continue

            hours_idle = (datetime.now(timezone.utc) - last_active_dt).total_seconds() / 3600.0
            if hours_idle < cutoff_hours:
                continue

            # Decay toward 0.5 based on idle time
            decay_factor = _DECAY_RATE * (hours_idle / cutoff_hours)
            decay_factor = min(decay_factor, 0.10)  # Cap at 10% per decay pass
            new_score = score + (0.5 - score) * decay_factor

            results.append({
                "peer_id": peer_id,
                "old_score": round(score, 4),
                "new_score": round(new_score, 4),
                "hours_idle": round(hours_idle, 1),
                "decay_applied": round(decay_factor, 4),
            })

            if not dry_run:
                conn.execute(
                    "UPDATE scoreboard SET composite_score = ? WHERE peer_id = ?",
                    (round(new_score, 4), peer_id),
                )

        if not dry_run and results:
            conn.commit()
            logger.info("Decayed reputation for %d idle peers", len(results))

    except Exception as e:
        logger.error("Reputation decay failed: %s", e, exc_info=True)
    finally:
        conn.close()

    return results


# ── Stake/Bond Mechanism ───────────────────────────────────────────────

def stake_for_task(peer_id: str, task_id: str, stake_amount: float) -> bool:
    """Reserve credits as a stake before claiming a high-value task.

    If the helper fails to deliver, the stake is burnt.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT balance FROM compute_credit_ledger WHERE peer_id = ?",
            (peer_id,),
        ).fetchone()

        if not row or float(row["balance"]) < stake_amount:
            logger.warning("Insufficient balance for stake: peer=%s, required=%.1f", peer_id, stake_amount)
            return False

        conn.execute(
            """
            INSERT INTO compute_credit_ledger (peer_id, balance, op, amount, receipt_id, created_at)
            VALUES (?, ?, 'stake_lock', ?, ?, ?)
            """,
            (peer_id, float(row["balance"]) - stake_amount, stake_amount,
             f"stake_{task_id}", datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        logger.info("Staked %.1f credits for task %s (peer=%s)", stake_amount, task_id, peer_id)
        return True
    except Exception as e:
        logger.error("Stake failed: %s", e)
        return False
    finally:
        conn.close()


def release_stake(peer_id: str, task_id: str, burn: bool = False) -> None:
    """Release stake back to the peer (success) or burn it (failure)."""
    action = "stake_burn" if burn else "stake_release"
    logger.info("%s for task %s (peer=%s)", action, task_id, peer_id)
    # In a real implementation, this would update the ledger entry
    # For now, the burn is implicit (the balance was already deducted)
    if not burn:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT balance FROM compute_credit_ledger WHERE peer_id = ? ORDER BY created_at DESC LIMIT 1",
                (peer_id,),
            ).fetchone()
            if row:
                # Restore the staked amount
                conn.execute(
                    "UPDATE compute_credit_ledger SET balance = balance + (SELECT amount FROM compute_credit_ledger WHERE receipt_id = ? LIMIT 1) WHERE peer_id = ?",
                    (f"stake_{task_id}", peer_id),
                )
                conn.commit()
        except Exception as e:
            logger.error("Stake release failed: %s", e)
        finally:
            conn.close()
