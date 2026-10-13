"""Equal-score memories come back newest first, in the same order on every ingest, not in the order of their ids.

A memory node's id hashes the wall-clock time it was stored, so the same conversation stored at another moment gets
other ids. Ranking broke exact score ties by that id, so the same question over the same history could pick
different evidence on each ingest (measured on the LoCoMo-150 retrieval replay: one question's record section and
lane flipped between two variants run to run). Ties now break by statement time, newest first (the recency law: a
restatement outranks the value it replaced), then content, then id.
"""
from __future__ import annotations

import pytest

from core.vool_memory import VoolMemory

# Same words, so the keyword leg ties; embeddings at equal angles to the query, so the semantic leg ties too.
FIRST = "the harbour ferry leaves at noon"
SECOND = "noon the ferry leaves at harbour"
QUERY = "when does the harbour ferry leave"


def _ranked_pair(tmp_path, base_ts: float):
    mem = VoolMemory(runtime_home=tmp_path / f"home-{int(base_ts)}")
    try:
        first = mem.node_store(FIRST, [], [], "", [1.0, 0.0, 0.0, 0.0], timestamp=base_ts)
        second = mem.node_store(SECOND, [], [], "", [0.0, 1.0, 0.0, 0.0], timestamp=base_ts + 1.0)
        hits = mem.node_search_hybrid(QUERY, [1.0, 1.0, 0.0, 0.0], top_k=5)
        order = [node.node_id for node, _score in hits]
        return first.node_id, second.node_id, order
    finally:
        close = getattr(mem, "close", None)
        if callable(close):
            close()


def test_tied_memories_rank_newest_first_whatever_their_ids(tmp_path):
    seen_id_orders = set()
    for base_ts in (1_700_000_000.0 + 3600.0 * step for step in range(12)):
        first, second, order = _ranked_pair(tmp_path, base_ts)
        assert first in order and second in order, order
        seen_id_orders.add(first < second)
        assert order.index(second) < order.index(first), (base_ts, first, second, order)
    # the ids must have sorted both ways across the stores, or the case proves nothing about id order
    assert seen_id_orders == {True, False}, seen_id_orders
