from datetime import datetime, timedelta, timezone

from core.order_book import OrderBookQueue


def test_order_book():
    print("--- Testing Dynamic Bidding Order Book (Phase 27) ---")
    queue = OrderBookQueue()

    now = datetime.now(timezone.utc)

    # 1. Base Task (10 points)
    queue.push(
        raw_bytes=b"raw1",
        source_addr=("127.0.0.1", 1000),
        offer_dict={
            "task_id": "task_base",
            "reward_hint": {"points": 10},
            "deadline_ts": (now + timedelta(minutes=10)).isoformat()
        }
    )

    # 2. Urgent Task but low bid (5 points)
    queue.push(
        raw_bytes=b"raw2",
        source_addr=("127.0.0.1", 1000),
        offer_dict={
            "task_id": "task_urgent_cheap",
            "reward_hint": {"points": 5},
            "deadline_ts": (now + timedelta(minutes=1)).isoformat()
        }
    )

    # 3. High Bid Task (30 points)
    queue.push(
        raw_bytes=b"raw3",
        source_addr=("127.0.0.1", 1000),
        offer_dict={
            "task_id": "task_whale",
            "reward_hint": {"points": 30},
            "deadline_ts": (now + timedelta(minutes=20)).isoformat()
        }
    )

    # 4. Highest Bid Task (100 points)
    queue.push(
        raw_bytes=b"raw4",
        source_addr=("127.0.0.1", 1000),
        offer_dict={
            "task_id": "task_mega_whale",
            "reward_hint": {"points": 100},
            "deadline_ts": (now + timedelta(minutes=5)).isoformat()
        }
    )

    # 5. Expired Task (should be skipped by pop)
    queue.push(
        raw_bytes=b"raw5",
        source_addr=("127.0.0.1", 1000),
        offer_dict={
            "task_id": "task_expired",
            "reward_hint": {"points": 500},
            "deadline_ts": (now - timedelta(minutes=5)).isoformat()
        }
    )

    print(f"Queue size before pop: {queue.size()}")
    assert queue.size() == 5, "Queue size mismatch"

    # Pop 1: Mega whale (100 points)
    best1 = queue.pop_best_offer()
    print(f"1st Pop: {best1.offer_dict['task_id']} (Bid: {best1.bid_price})")
    assert best1.offer_dict["task_id"] == "task_mega_whale", "Highest bidder should be first"

    # Pop 2: Whale (30 points)
    best2 = queue.pop_best_offer()
    print(f"2nd Pop: {best2.offer_dict['task_id']} (Bid: {best2.bid_price})")
    assert best2.offer_dict["task_id"] == "task_whale", "Second highest bidder should be second"

    # Pop 3: Base (10 points)
    best3 = queue.pop_best_offer()
    print(f"3rd Pop: {best3.offer_dict['task_id']} (Bid: {best3.bid_price})")
    assert best3.offer_dict["task_id"] == "task_base", "Base should be third"

    # Pop 4: Urgent Cheap (5 points)
    best4 = queue.pop_best_offer()
    print(f"4th Pop: {best4.offer_dict['task_id']} (Bid: {best4.bid_price})")
    assert best4.offer_dict["task_id"] == "task_urgent_cheap", "Cheap should be last, despite urgency"

    # Pop 5: Should be None (expired was skipped)
    best5 = queue.pop_best_offer()
    print("5th Pop:", best5)
    assert best5 is None, "Expired task should have been dropped during pop_best_offer"

    print("\nSUCCESS! OrderBookQueue correctly sorts by bid price and discards expired offers.")

if __name__ == "__main__":
    test_order_book()
