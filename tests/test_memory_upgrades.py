"""Unit tests for the 2026-06 memory upgrades: dedup-on-write, recency-weighted
retrieval with temporal collapse (latest value wins), bi-temporal invalidation,
and budgeted prune. Uses explicit embeddings so the tests are deterministic and
do not depend on an embedding backend."""
from __future__ import annotations

import hashlib
import math
import tempfile
import time
from pathlib import Path

from core.vool_memory import VoolMemory


def _vec(*xs: float) -> list[float]:
    mag = math.sqrt(sum(x * x for x in xs)) or 1.0
    return [x / mag for x in xs]


def _mem(tmp: str) -> VoolMemory:
    return VoolMemory(agent_id="t", db_path=str(Path(tmp) / "m.db"))


def _session_scope(session_id: str) -> str:
    return f"v2:{hashlib.sha256(session_id.encode('utf-8')).hexdigest()}"


def test_dedup_on_write_noops_near_identical() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        m = _mem(tmp)
        v = _vec(1, 0, 0)
        m.node_store("The deploy port is 8096 for staging", ["deploy", "port", "8096"], ["user"], "t1", v)
        m.node_store("The deploy port is 8096 for staging", ["deploy", "port", "8096"], ["user"], "t2", v)
        assert m.node_count() == 1  # second write is a NOOP-dedup
        m.close()


def test_dedup_preserves_distinct_session_provenance() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        m = _mem(tmp)
        v = _vec(1, 0, 0)
        content = "operator node ID is 8829145"
        first = m.node_store(
            content,
            ["operator", "node", "id", "8829145"],
            ["user", f"session:{_session_scope('first')}"],
            f"session={_session_scope('first')} role=user",
            v,
        )
        second = m.node_store(
            content,
            ["operator", "node", "id", "8829145"],
            ["user", f"session:{_session_scope('second')}"],
            f"session={_session_scope('second')} role=user",
            v,
        )
        assert first.node_id != second.node_id
        assert m.node_count() == 2
        m.close()


def test_dedup_still_noops_within_same_session_scope() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        m = _mem(tmp)
        v = _vec(1, 0, 0)
        content = "operator node ID is 8829145"
        first = m.node_store(
            content,
            ["operator", "node", "id", "8829145"],
            ["user", f"session:{_session_scope('same')}"],
            f"session={_session_scope('same')} role=user",
            v,
        )
        second = m.node_store(
            content,
            ["operator", "node", "id", "8829145"],
            ["user", f"session:{_session_scope('same')}"],
            f"session={_session_scope('same')} role=user",
            v,
        )
        assert first.node_id == second.node_id
        assert m.node_count() == 1
        m.close()


def test_distinct_values_are_not_deduped() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        m = _mem(tmp)
        m.node_store("Use Postgres for the database", ["postgres", "database"], ["user"], "t1", _vec(1, 0, 0))
        m.node_store("Use Redis for the cache layer", ["redis", "cache"], ["user"], "t2", _vec(0, 1, 0))
        assert m.node_count() == 2  # different facts -> both kept
        m.close()


def test_temporal_collapse_returns_latest_value() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        m = _mem(tmp)
        now = time.time()
        # same topic (high mutual cosine) but different values and timestamps
        old = _vec(1.0, 0.0, 0.0)
        new = _vec(0.97, 0.24, 0.0)  # cosine(old,new) ~0.97 >= COLLAPSE_SIM
        m.node_store("The launch deadline is 2026-07-15", ["launch", "deadline"], ["user"], "wk1",
                     old, timestamp=now - 21 * 86400)
        m.node_store("Moved the launch deadline to 2026-08-01", ["launch", "deadline", "moved"], ["user"], "wk3",
                     new, timestamp=now - 3 * 86400)
        hits = m.node_search(_vec(0.99, 0.1, 0.0), top_k=3, min_score=0.0)
        assert hits, "expected a retrieval hit"
        top_text = hits[0][0].content.lower()
        assert "2026-08-01" in top_text and "2026-07-15" not in top_text
        m.close()


def test_invalidate_excludes_from_retrieval() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        m = _mem(tmp)
        n = m.node_store("Secret token is abc123", ["secret", "token"], ["user"], "t1", _vec(1, 0, 0))
        assert m.node_search(_vec(1, 0, 0), top_k=3, min_score=0.0)
        m.node_invalidate(n.node_id)
        assert m.node_search(_vec(1, 0, 0), top_k=3, min_score=0.0) == []  # soft-deleted
        assert m.node_count() == 1  # row retained for history
        m.close()


def test_prune_keeps_highest_effective_importance() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        m = _mem(tmp)
        now = time.time()
        # 5 distinct nodes; a high-importance recent one must survive a prune-to-2
        for i in range(5):
            m.node_store(f"low value filler note number {i}", [f"note{i}"], ["user"], "f",
                         _vec(*[1.0 if j == i else 0.0 for j in range(5)]),
                         timestamp=now - (10 - i) * 86400, importance=0.2)
        keep = m.node_store("CRITICAL production api key sk-live-keepme", ["critical", "api", "key"], ["user"], "k",
                            _vec(0, 0, 0, 0, 0, 1), timestamp=now, importance=0.95)
        dropped = m.prune(max_nodes=2)
        assert dropped == 4
        live = m.node_search(_vec(0, 0, 0, 0, 0, 1), top_k=5, min_score=0.0)
        assert any(n.node_id == keep.node_id for n, _ in live)  # high-importance survived
        m.close()


def test_node_search_backward_compatible_signature() -> None:
    # the existing inject_relevant path calls node_search(vec, top_k=, min_score=)
    with tempfile.TemporaryDirectory() as tmp:
        m = _mem(tmp)
        m.node_store("hello world fact", ["hello", "world"], ["user"], "t1", _vec(1, 0, 0))
        out = m.node_search(_vec(1, 0, 0), top_k=5, min_score=0.6)
        assert len(out) == 1 and isinstance(out[0][1], float)
        m.close()
