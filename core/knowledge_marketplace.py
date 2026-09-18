"""Knowledge Marketplace — publish, discover, and trade knowledge shards.

Peers can:
- Publish knowledge adverts with pricing
- Browse and search the marketplace
- Purchase access to remote knowledge
- Track popularity and ratings
"""
from __future__ import annotations

import contextlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.credit_ledger import get_credit_balance, receipt_exists, transfer_credits
from storage.db import get_connection

logger = logging.getLogger(__name__)

_PURCHASE_REASON = "knowledge_purchase"


@dataclass
class MarketplaceListing:
    shard_id: str
    seller_peer_id: str
    title: str
    description: str
    domain_tags: list[str]
    price_credits: float
    quality_score: float = 0.0
    purchase_count: int = 0
    created_at: str = ""
    avg_rating: float = 0.0


def ensure_marketplace_table() -> None:
    """Create the marketplace table if it doesn't exist."""
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_marketplace (
                shard_id TEXT PRIMARY KEY,
                seller_peer_id TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                domain_tags_json TEXT DEFAULT '[]',
                price_credits REAL DEFAULT 0.0,
                quality_score REAL DEFAULT 0.0,
                purchase_count INTEGER DEFAULT 0,
                avg_rating REAL DEFAULT 0.0,
                rating_count INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        # Defensive migration: add rating_count to a table created before it existed
        # (no-op once the column is present).
        with contextlib.suppress(Exception):
            conn.execute("ALTER TABLE knowledge_marketplace ADD COLUMN rating_count INTEGER DEFAULT 0")
        conn.commit()
    finally:
        conn.close()


def publish_listing(
    shard_id: str,
    seller_peer_id: str,
    title: str,
    description: str = "",
    domain_tags: list[str] | None = None,
    price_credits: float = 1.0,
    quality_score: float = 0.5,
) -> MarketplaceListing:
    """Publish a knowledge shard to the marketplace."""
    ensure_marketplace_table()
    now = datetime.now(timezone.utc).isoformat()
    tags = domain_tags or []
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO knowledge_marketplace
            (shard_id, seller_peer_id, title, description, domain_tags_json, price_credits, quality_score, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (shard_id, seller_peer_id, title, description, json.dumps(tags), price_credits, quality_score, now, now),
        )
        conn.commit()
    finally:
        conn.close()

    logger.info("Published marketplace listing: shard=%s, price=%.1f credits", shard_id, price_credits)
    return MarketplaceListing(
        shard_id=shard_id, seller_peer_id=seller_peer_id,
        title=title, description=description,
        domain_tags=tags, price_credits=price_credits,
        quality_score=quality_score, created_at=now,
    )


def search_listings(query: str = "", domain_tag: str = "", max_results: int = 50) -> list[MarketplaceListing]:
    """Search marketplace listings by keyword or domain tag."""
    ensure_marketplace_table()
    conn = get_connection()
    try:
        if domain_tag:
            rows = conn.execute(
                "SELECT * FROM knowledge_marketplace WHERE domain_tags_json LIKE ? ORDER BY quality_score DESC LIMIT ?",
                (f"%{domain_tag}%", max_results),
            ).fetchall()
        elif query:
            rows = conn.execute(
                "SELECT * FROM knowledge_marketplace WHERE title LIKE ? OR description LIKE ? ORDER BY quality_score DESC LIMIT ?",
                (f"%{query}%", f"%{query}%", max_results),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM knowledge_marketplace ORDER BY purchase_count DESC, quality_score DESC LIMIT ?",
                (max_results,),
            ).fetchall()

        results = []
        for row in rows:
            results.append(MarketplaceListing(
                shard_id=row["shard_id"],
                seller_peer_id=row["seller_peer_id"],
                title=row["title"],
                description=row["description"],
                domain_tags=json.loads(row["domain_tags_json"] or "[]"),
                price_credits=float(row["price_credits"]),
                quality_score=float(row["quality_score"]),
                purchase_count=int(row["purchase_count"]),
                avg_rating=float(row["avg_rating"] or 0.0),
                created_at=row["created_at"],
            ))
        return results
    finally:
        conn.close()


def record_purchase(shard_id: str, buyer_peer_id: str, rating: float | None = None) -> bool:
    """Record a knowledge purchase and optionally rate it."""
    ensure_marketplace_table()
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE knowledge_marketplace SET purchase_count = purchase_count + 1, updated_at = ? WHERE shard_id = ?",
            (datetime.now(timezone.utc).isoformat(), shard_id),
        )
        if rating is not None:
            # Running average over RATINGS, not purchases: dividing by purchase_count
            # would dilute each rating by the unrated buys in between. rating_count
            # tracks only how many ratings have actually been recorded.
            row = conn.execute("SELECT avg_rating, rating_count FROM knowledge_marketplace WHERE shard_id = ?", (shard_id,)).fetchone()
            if row:
                old_avg = float(row["avg_rating"] or 0.0)
                rcount = int(row["rating_count"] or 0) + 1
                new_avg = ((old_avg * (rcount - 1)) + rating) / rcount
                conn.execute(
                    "UPDATE knowledge_marketplace SET avg_rating = ?, rating_count = ? WHERE shard_id = ?",
                    (round(new_avg, 3), rcount, shard_id),
                )
        conn.commit()
        logger.info("Recorded purchase: shard=%s, buyer=%s", shard_id, buyer_peer_id[:12])
        return True
    except Exception as e:
        logger.error("Purchase recording failed: %s", e)
        return False
    finally:
        conn.close()


def _row_to_listing(row) -> MarketplaceListing:
    return MarketplaceListing(
        shard_id=row["shard_id"],
        seller_peer_id=row["seller_peer_id"],
        title=row["title"],
        description=row["description"],
        domain_tags=json.loads(row["domain_tags_json"] or "[]"),
        price_credits=float(row["price_credits"]),
        quality_score=float(row["quality_score"]),
        purchase_count=int(row["purchase_count"]),
        avg_rating=float(row["avg_rating"] or 0.0),
        created_at=row["created_at"],
    )


def get_listing(shard_id: str) -> MarketplaceListing | None:
    """Fetch a single marketplace listing by shard id, or None if not listed."""
    ensure_marketplace_table()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM knowledge_marketplace WHERE shard_id = ?",
            (str(shard_id),),
        ).fetchone()
        return _row_to_listing(row) if row else None
    finally:
        conn.close()


def ensure_entitlements_table() -> None:
    """Create the per-buyer entitlement table if it doesn't exist."""
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_entitlements (
                buyer_peer_id TEXT NOT NULL,
                shard_id TEXT NOT NULL,
                receipt_id TEXT,
                price_paid REAL DEFAULT 0.0,
                created_at TEXT NOT NULL,
                PRIMARY KEY (buyer_peer_id, shard_id)
            )
        """)
        conn.commit()
    finally:
        conn.close()


def has_entitlement(buyer_peer_id: str, shard_id: str) -> bool:
    """True if this buyer already holds access to this shard."""
    ensure_entitlements_table()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM knowledge_entitlements WHERE buyer_peer_id = ? AND shard_id = ? LIMIT 1",
            (str(buyer_peer_id), str(shard_id)),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def grant_entitlement(buyer_peer_id: str, shard_id: str, receipt_id: str, price_paid: float) -> None:
    """Unlock a shard for a buyer. Idempotent on (buyer, shard) — a second grant is a no-op."""
    ensure_entitlements_table()
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO knowledge_entitlements
            (buyer_peer_id, shard_id, receipt_id, price_paid, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (str(buyer_peer_id), str(shard_id), str(receipt_id), float(price_paid), now),
        )
        conn.commit()
    finally:
        conn.close()


def list_entitlements(buyer_peer_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    """List the shards a buyer has unlocked, most recent first."""
    ensure_entitlements_table()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT shard_id, receipt_id, price_paid, created_at
            FROM knowledge_entitlements
            WHERE buyer_peer_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (str(buyer_peer_id), max(1, min(int(limit), 500))),
        ).fetchall()
        return [
            {
                "shard_id": row["shard_id"],
                "receipt_id": row["receipt_id"],
                "price_paid": float(row["price_paid"] or 0.0),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
    finally:
        conn.close()


def purchase_knowledge(
    buyer_peer_id: str,
    shard_id: str,
    *,
    receipt_id: str | None = None,
) -> dict[str, Any]:
    """Buy access to a listed knowledge shard, paying the seller in compute credits.

    Atomic and idempotent. The price moves buyer -> seller in a single ledger
    transaction (``transfer_credits``), so the seller is actually paid and the
    buyer can never go negative. Idempotency is keyed on ``(buyer, shard)`` — a
    repeat purchase is a no-op with no second debit — and the transfer's
    receipt key is *deterministic* on ``(buyer, shard)`` so two concurrent buys
    of the same shard collapse to one debit at the ledger replay guard rather
    than double-charging. ``receipt_id``, if supplied, is recorded for the
    caller's bookkeeping but does NOT widen the idempotency key. A free listing
    (price 0) unlocks without any ledger movement. Returns a structured result;
    never raises on the ordinary insufficient-credit / already-owned paths.
    """
    listing = get_listing(shard_id)
    if listing is None:
        return {"ok": False, "reason": "listing_not_found", "shard_id": str(shard_id)}

    if str(buyer_peer_id) == str(listing.seller_peer_id):
        return {"ok": False, "reason": "seller_cannot_buy_own_listing", "shard_id": str(shard_id)}

    price = max(0.0, float(listing.price_credits or 0.0))
    # Deterministic on (buyer, shard): the ledger replay guard collapses a
    # concurrent / repeated debit even when no caller receipt_id is supplied.
    dedup_id = f"{_PURCHASE_REASON}:{buyer_peer_id}:{shard_id}"
    record_id = str(receipt_id or dedup_id)

    def _already(charged: float = 0.0) -> dict[str, Any]:
        # Ensure the buyer that paid is unlocked even if a race granted late.
        grant_entitlement(buyer_peer_id, shard_id, record_id, price)
        return {
            "ok": True,
            "reason": "already_purchased",
            "shard_id": str(shard_id),
            "seller_peer_id": listing.seller_peer_id,
            "price_credits": price,
            "charged_credits": charged,
            "unlocked": True,
            "receipt_id": record_id,
        }

    # Idempotency: already entitled -> no second debit, no second unlock.
    if has_entitlement(buyer_peer_id, shard_id):
        return _already()

    def _purchased() -> dict[str, Any]:
        record_purchase(shard_id, buyer_peer_id)
        grant_entitlement(buyer_peer_id, shard_id, record_id, price)
        logger.info("Knowledge purchased: shard=%s buyer=%s price=%.2f", shard_id, str(buyer_peer_id)[:12], price)
        return {
            "ok": True,
            "reason": "purchased",
            "shard_id": str(shard_id),
            "seller_peer_id": listing.seller_peer_id,
            "price_credits": price,
            "charged_credits": price,
            "unlocked": True,
            "receipt_id": record_id,
            "balance": get_credit_balance(buyer_peer_id),
        }

    # Free listing: unlock with no ledger movement (transfer_credits rejects 0).
    if price <= 0.0:
        return _purchased()

    balance = get_credit_balance(buyer_peer_id)
    if balance < price:
        return {
            "ok": False,
            "reason": "insufficient_credits",
            "shard_id": str(shard_id),
            "price_credits": price,
            "balance": balance,
        }

    # Atomic buyer -> seller transfer; a False means the deterministic receipt
    # was already used (concurrent/replay) or the balance moved under us.
    if not transfer_credits(buyer_peer_id, listing.seller_peer_id, price, reason=_PURCHASE_REASON, receipt_id=dedup_id):
        if has_entitlement(buyer_peer_id, shard_id) or receipt_exists(dedup_id):
            return _already()  # the one debit already happened under dedup_id
        return {
            "ok": False,
            "reason": "insufficient_credits",
            "shard_id": str(shard_id),
            "price_credits": price,
            "balance": get_credit_balance(buyer_peer_id),
        }

    return _purchased()
