"""Regression tests for two core/vool_memory.py defects:

1. Retrieval temporal-collapse over-merge: two DISTINCT, never-superseded facts
   about the same entity sit close in embedding space (cosine ~0.9 >= COLLAPSE_SIM)
   but have low token overlap and no supersession marker. They must BOTH survive
   retrieval — collapse must not merge on embedding cosine alone.

2. FTS backfill cross-agent mis-attribution: _migrate backfills memory_fts for any
   node missing from it. Each backfilled row must carry that node's OWN agent_id,
   not the agent_id of whoever opened the DB.

Deterministic explicit embeddings; no embedding backend required.
"""
from __future__ import annotations

import math
import tempfile
from pathlib import Path

from core.vool_memory import VoolMemory


def _vec(*xs: float) -> list[float]:
    mag = math.sqrt(sum(x * x for x in xs)) or 1.0
    return [x / mag for x in xs]


def _mem(tmp: str, agent_id: str = "t") -> VoolMemory:
    return VoolMemory(agent_id=agent_id, db_path=str(Path(tmp) / "m.db"))


def test_distinct_same_entity_facts_both_survive_retrieval() -> None:
    """cosine ~0.9 (>= COLLAPSE_SIM 0.85), low token overlap, no supersession
    marker -> both distinct facts must be returned, not collapsed to one."""
    with tempfile.TemporaryDirectory() as tmp:
        m = _mem(tmp)
        # Two distinct facts about the same entity (Alice). High mutual cosine,
        # but different content tokens and no 'moved/updated/now' marker.
        a = _vec(1.0, 0.3, 0.0)    # cosine(a,b) ~= 0.90 (>= COLLAPSE_SIM)
        b = _vec(1.0, 0.9, 0.0)
        assert _cosine(a, b) >= VoolMemory._COLLAPSE_SIM  # precondition: would collapse on cosine alone
        m.node_store(
            "Alice prefers tabs over spaces in source files",
            ["alice", "tabs", "spaces"], ["user"], "c1", a,
        )
        m.node_store(
            "Alice lives in Berlin near the river",
            ["alice", "berlin", "river"], ["user"], "c2", b,
        )
        assert m.node_count() == 2  # write-dedup correctly kept both (low overlap)
        hits = m.node_search(_vec(1.0, 1.0, 0.0), top_k=5, min_score=0.0)
        contents = {h[0].content for h in hits}
        assert len(hits) == 2, f"both distinct facts must survive, got {contents}"
        assert any("tabs over spaces" in c for c in contents)
        assert any("Berlin" in c for c in contents)
        m.close()


def test_true_supersession_still_collapses_to_latest() -> None:
    """A genuine value-change (shared subject + supersession marker) must still
    collapse to the newest value — the fix must not regress latest-value-wins."""
    import time
    with tempfile.TemporaryDirectory() as tmp:
        m = _mem(tmp)
        now = time.time()
        old = _vec(1.0, 0.0, 0.0)
        new = _vec(0.97, 0.24, 0.0)  # cosine ~0.97 >= COLLAPSE_SIM
        m.node_store("The launch deadline is 2026-07-15", ["launch", "deadline"], ["user"], "wk1",
                     old, timestamp=now - 21 * 86400)
        m.node_store("Moved the launch deadline to 2026-08-01", ["launch", "deadline", "moved"], ["user"], "wk3",
                     new, timestamp=now - 3 * 86400)
        hits = m.node_search(_vec(0.99, 0.1, 0.0), top_k=3, min_score=0.0)
        assert hits
        top_text = hits[0][0].content.lower()
        assert "2026-08-01" in top_text and "2026-07-15" not in top_text
        m.close()


def test_fts_backfill_preserves_per_node_agent_id() -> None:
    """A DB holding rows from multiple agents, opened/migrated by one agent, must
    keep each node's OWN agent_id in memory_fts — never re-attribute foreign rows
    to the opener (which would leak them into the opener's BM25 leg)."""
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "shared.db")
        # Two agents write nodes into the SAME db.
        alice = VoolMemory(agent_id="alice", db_path=db)
        alice.node_store("alpha secret token", ["alpha", "token"], ["user"], "a", _vec(1, 0, 0))
        alice.close()
        bob = VoolMemory(agent_id="bob", db_path=db)
        bob.node_store("beta config value", ["beta", "config"], ["user"], "b", _vec(0, 1, 0))
        bob.close()

        # Simulate a pre-upgrade gap: wipe memory_fts so _migrate backfills it.
        import sqlite3
        conn = sqlite3.connect(db)
        conn.execute("DELETE FROM memory_fts")
        conn.commit()
        conn.close()

        # alice re-opens the shared DB -> _migrate runs the FTS backfill.
        alice2 = VoolMemory(agent_id="alice", db_path=db)
        with alice2._lock:  # reach into internals: regression assertion on FTS attribution
            rows = alice2._conn.execute(
                "SELECT node_id, agent_id FROM memory_fts ORDER BY node_id"
            ).fetchall()
        attribution = {str(r["node_id"]): str(r["agent_id"]) for r in rows}
        # Map node_id -> owning agent from the durable table for comparison.
        with alice2._lock:
            node_owner = {
                str(r["node_id"]): str(r["agent_id"])
                for r in alice2._conn.execute("SELECT node_id, agent_id FROM memory_nodes")
            }
        assert attribution == node_owner, "FTS rows must keep each node's own agent_id"
        assert "bob" in attribution.values(), "bob's node must remain attributed to bob"
        alice2.close()


def test_bm25_leg_does_not_leak_foreign_agent_after_backfill() -> None:
    """End-to-end: after a backfill, alice's keyword (BM25) search must not surface
    bob's node, because bob's FTS row keeps agent_id='bob'."""
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "shared.db")
        alice = VoolMemory(agent_id="alice", db_path=db)
        alice.node_store("widget assembly instructions", ["widget", "assembly"], ["user"], "a", _vec(1, 0, 0))
        alice.close()
        bob = VoolMemory(agent_id="bob", db_path=db)
        bob.node_store("widget assembly instructions", ["widget", "assembly"], ["user"], "b", _vec(0, 1, 0))
        bob.close()

        import sqlite3
        conn = sqlite3.connect(db)
        conn.execute("DELETE FROM memory_fts")
        conn.commit()
        conn.close()

        alice2 = VoolMemory(agent_id="alice", db_path=db)
        with alice2._lock:  # reach into internals: assert BM25 leg excludes foreign agent
            scores = alice2._bm25_scores("widget assembly")
        with alice2._lock:
            bob_ids = {
                str(r["node_id"])
                for r in alice2._conn.execute(
                    "SELECT node_id FROM memory_nodes WHERE agent_id = 'bob'"
                )
            }
        assert not (set(scores) & bob_ids), "alice's BM25 leg must not include bob's nodes"
        alice2.close()


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    ma = math.sqrt(sum(x * x for x in a))
    mb = math.sqrt(sum(y * y for y in b))
    return dot / (ma * mb) if ma and mb else 0.0
