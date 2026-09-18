from __future__ import annotations

import heapq
import threading
from datetime import datetime, timezone
from typing import Any


class OrderBookItem:
    def __init__(self, raw_bytes: bytes, source_addr: tuple[str, int], offer_dict: dict[str, Any]):
        self.raw_bytes = raw_bytes
        self.source_addr = source_addr
        self.offer_dict = offer_dict

        # We sort primarily by reward points (highest first)
        reward_hint = offer_dict.get("reward_hint", {})
        self.bid_price = float(reward_hint.get("points", 0))

        # Secondarily by deadline (earliest first)
        deadline_str = offer_dict.get("deadline_ts", "")
        if deadline_str:
            try:
                # Convert ISO string to timestamp for sorting
                dt = datetime.fromisoformat(deadline_str.replace("Z", "+00:00"))
                self.deadline_ts = dt.timestamp()
            except Exception:
                self.deadline_ts = float('inf')
        else:
            self.deadline_ts = float('inf')

    def __lt__(self, other: OrderBookItem):
        # Python heapq is a min-heap. We want max bid, so we invert bid_price.
        if self.bid_price != other.bid_price:
            return self.bid_price > other.bid_price

        # If bids are equal, closest deadline wins (min deadline_ts)
        return self.deadline_ts < other.deadline_ts

class OrderBookQueue:
    """
    A thread-safe priority queue for incoming Swarm tasks.
    Idle helpers pull from this queue to maximize their earnings.
    """
    def __init__(self):
        self._heap: list[OrderBookItem] = []
        self._lock = threading.Lock()

    def push(self, raw_bytes: bytes, source_addr: tuple[str, int], offer_dict: dict[str, Any]) -> None:
        item = OrderBookItem(raw_bytes, source_addr, offer_dict)
        with self._lock:
            heapq.heappush(self._heap, item)

    def pop_best_offer(self) -> OrderBookItem | None:
        """
        Pulls the highest bidding task that hasn't expired.
        """
        now_ts = datetime.now(timezone.utc).timestamp()

        with self._lock:
            while self._heap:
                best = heapq.heappop(self._heap)
                if best.deadline_ts > now_ts:
                    return best
        return None

    def size(self) -> int:
        with self._lock:
            return len(self._heap)

# Global Order Book for the node to pull from
global_order_book = OrderBookQueue()
