"""Knowledge-marketplace buy side: atomic, idempotent purchase with credit burn.

A purchase burns the listing price from the buyer's compute-credit balance
exactly once and unlocks an entitlement. Insufficient credits buy nothing; a
repeat purchase (same receipt, or just the same buyer+shard) is an idempotent
no-op that never double-debits.
"""
from __future__ import annotations

import threading
import uuid

from core import knowledge_marketplace as km
from core.credit_ledger import award_credits, get_credit_balance
from core.runtime_execution_tools import execute_runtime_tool
from network.signer import get_local_peer_id
from storage.db import get_connection


def _clear_marketplace() -> None:
    km.ensure_marketplace_table()
    km.ensure_entitlements_table()
    conn = get_connection()
    try:
        conn.execute("DELETE FROM knowledge_marketplace")
        conn.execute("DELETE FROM knowledge_entitlements")
        conn.commit()
    finally:
        conn.close()


def _list_shard(*, price: float = 5.0, seller: str | None = None) -> tuple[str, str]:
    seller = seller or f"seller-{uuid.uuid4().hex[:8]}"
    shard = f"shard-{uuid.uuid4().hex[:8]}"
    km.publish_listing(shard, seller, "ZK reputation recipe", "how to gate", ["zk"], price_credits=price, quality_score=0.9)
    return shard, seller


def _fund(buyer: str, amount: float) -> None:
    award_credits(buyer, amount, "test_seed", receipt_id=f"seed-{buyer}")


def test_search_returns_published_listings() -> None:
    _clear_marketplace()
    shard, _ = _list_shard(price=3.0)
    assert shard in [r.shard_id for r in km.search_listings()]


def test_purchase_burns_price_and_unlocks() -> None:
    _clear_marketplace()
    shard, seller = _list_shard(price=5.0)
    buyer = f"buyer-{uuid.uuid4().hex[:8]}"
    _fund(buyer, 20.0)

    out = km.purchase_knowledge(buyer, shard, receipt_id=f"r1-{buyer}")

    assert out["ok"] and out["reason"] == "purchased"
    assert out["charged_credits"] == 5.0 and out["unlocked"] is True
    assert out["seller_peer_id"] == seller
    assert km.has_entitlement(buyer, shard)
    assert get_credit_balance(buyer) == 15.0
    assert km.get_listing(shard).purchase_count == 1


def test_insufficient_credits_rejected_and_nothing_unlocked() -> None:
    _clear_marketplace()
    shard, _ = _list_shard(price=50.0)
    buyer = f"buyer-{uuid.uuid4().hex[:8]}"
    _fund(buyer, 5.0)

    out = km.purchase_knowledge(buyer, shard, receipt_id="r1")

    assert not out["ok"] and out["reason"] == "insufficient_credits"
    assert not km.has_entitlement(buyer, shard)
    assert get_credit_balance(buyer) == 5.0  # balance untouched


def test_double_spend_same_receipt_is_idempotent_and_debits_once() -> None:
    _clear_marketplace()
    shard, _ = _list_shard(price=8.0)
    buyer = f"buyer-{uuid.uuid4().hex[:8]}"
    _fund(buyer, 20.0)
    rid = "purchase-fixed-1"

    first = km.purchase_knowledge(buyer, shard, receipt_id=rid)
    second = km.purchase_knowledge(buyer, shard, receipt_id=rid)

    assert first["ok"] and first["reason"] == "purchased" and first["charged_credits"] == 8.0
    assert second["ok"] and second["reason"] == "already_purchased" and second["charged_credits"] == 0.0
    assert get_credit_balance(buyer) == 12.0  # 20 - 8, debited exactly once
    assert km.get_listing(shard).purchase_count == 1
    assert len(km.list_entitlements(buyer)) == 1


def test_repeat_purchase_without_receipt_is_idempotent() -> None:
    _clear_marketplace()
    shard, _ = _list_shard(price=4.0)
    buyer = f"buyer-{uuid.uuid4().hex[:8]}"
    _fund(buyer, 20.0)

    first = km.purchase_knowledge(buyer, shard)   # auto-generated receipt
    second = km.purchase_knowledge(buyer, shard)  # different receipt, but already entitled

    assert first["reason"] == "purchased"
    assert second["reason"] == "already_purchased" and second["charged_credits"] == 0.0
    assert get_credit_balance(buyer) == 16.0


def test_listing_not_found_rejected() -> None:
    _clear_marketplace()
    buyer = f"buyer-{uuid.uuid4().hex[:8]}"
    _fund(buyer, 10.0)

    out = km.purchase_knowledge(buyer, "nonexistent-shard", receipt_id="r")

    assert not out["ok"] and out["reason"] == "listing_not_found"
    assert get_credit_balance(buyer) == 10.0


def test_seller_cannot_buy_own_listing() -> None:
    _clear_marketplace()
    shard, seller = _list_shard(price=2.0)
    _fund(seller, 10.0)

    out = km.purchase_knowledge(seller, shard, receipt_id="r")

    assert not out["ok"] and out["reason"] == "seller_cannot_buy_own_listing"
    assert get_credit_balance(seller) == 10.0


# ---------------------------------------------------------------------------
# the marketplace intents route through the runtime dispatch
# ---------------------------------------------------------------------------

def test_search_listings_intent_routes() -> None:
    _clear_marketplace()
    shard, _ = _list_shard(price=3.0)
    res = execute_runtime_tool("marketplace.search_listings", {"domain_tag": "zk"})
    assert res.ok and res.status == "executed"
    assert shard in [row["shard_id"] for row in res.details["listings"]]


def test_purchase_intent_buys_for_the_local_node_only() -> None:
    _clear_marketplace()
    shard, _ = _list_shard(price=6.0)
    buyer = get_local_peer_id()  # the intent always spends the local node's credits
    _fund(buyer, 20.0)

    res = execute_runtime_tool("marketplace.purchase_knowledge", {"shard_id": shard, "receipt_id": "intent-r1"})
    assert res.ok and res.status == "executed"
    assert res.details["buyer_peer_id"] == buyer
    assert res.details["unlocked"] is True
    assert km.has_entitlement(buyer, shard)
    assert get_credit_balance(buyer) == 14.0

    again = execute_runtime_tool("marketplace.purchase_knowledge", {"shard_id": shard, "receipt_id": "intent-r1"})
    assert again.ok and again.details["reason"] == "already_purchased"
    assert get_credit_balance(buyer) == 14.0  # idempotent: not charged twice


def test_purchase_intent_requires_a_shard_id() -> None:
    _clear_marketplace()
    res = execute_runtime_tool("marketplace.purchase_knowledge", {})
    assert not res.ok and res.status == "rejected"


# ---------------------------------------------------------------------------
# audit regressions: seller payment, concurrency double-debit, free listing
# ---------------------------------------------------------------------------

def test_purchase_actually_pays_the_seller() -> None:
    _clear_marketplace()
    shard, seller = _list_shard(price=7.0)
    buyer = f"buyer-{uuid.uuid4().hex[:8]}"
    _fund(buyer, 10.0)

    km.purchase_knowledge(buyer, shard)

    assert get_credit_balance(buyer) == 3.0    # 10 - 7 debited
    assert get_credit_balance(seller) == 7.0   # seller credited the price (not burned)


def test_concurrent_purchase_same_shard_debits_once() -> None:
    _clear_marketplace()
    shard, seller = _list_shard(price=5.0)
    buyer = f"buyer-{uuid.uuid4().hex[:8]}"
    _fund(buyer, 50.0)  # enough for two debits — proves it does NOT take two

    results: list[dict] = []
    barrier = threading.Barrier(2)

    def buy() -> None:
        barrier.wait()
        results.append(km.purchase_knowledge(buyer, shard))  # no receipt_id -> deterministic dedup

    threads = [threading.Thread(target=buy) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert get_credit_balance(buyer) == 45.0   # exactly one 5-credit debit
    assert get_credit_balance(seller) == 5.0    # seller paid exactly once
    assert km.get_listing(shard).purchase_count == 1
    assert len(km.list_entitlements(buyer)) == 1
    assert all(r["ok"] for r in results)
    assert {r["reason"] for r in results} <= {"purchased", "already_purchased"}


def test_free_listing_unlocks_without_charge() -> None:
    _clear_marketplace()
    shard, seller = _list_shard(price=0.0)
    buyer = f"buyer-{uuid.uuid4().hex[:8]}"  # never funded: zero balance

    out = km.purchase_knowledge(buyer, shard)

    assert out["ok"] and out["reason"] == "purchased" and out["charged_credits"] == 0.0
    assert out["unlocked"] is True and km.has_entitlement(buyer, shard)
    assert get_credit_balance(buyer) == 0.0
    assert get_credit_balance(seller) == 0.0   # no ledger movement for a free shard

    again = km.purchase_knowledge(buyer, shard)
    assert again["reason"] == "already_purchased" and again["charged_credits"] == 0.0


def test_record_purchase_rating_average_is_over_ratings_not_purchases() -> None:
    _clear_marketplace()
    shard, _ = _list_shard(price=1.0)
    # Two unrated purchases, then one 5.0 rating: the average must be 5.0, not
    # diluted toward 0 by the unrated buys (the old purchase_count divisor bug).
    km.record_purchase(shard, "b1")
    km.record_purchase(shard, "b2")
    km.record_purchase(shard, "b3", rating=5.0)
    assert km.get_listing(shard).avg_rating == 5.0
    # A second rating of 3.0 -> mean of [5, 3] == 4.0.
    km.record_purchase(shard, "b4", rating=3.0)
    assert km.get_listing(shard).avg_rating == 4.0
