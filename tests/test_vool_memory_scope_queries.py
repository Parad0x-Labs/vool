from __future__ import annotations

import hashlib
from pathlib import Path

import core.vool_memory as vool_memory_module
from core.vool_memory import VoolMemory


def _scope(session_id: str) -> str:
    return f"v2:{hashlib.sha256(session_id.encode('utf-8')).hexdigest()}"


def _store(
    memory: VoolMemory,
    *,
    session_id: str,
    content: str,
    embedding: list[float],
    links: list[str] | None = None,
):
    scope = _scope(session_id)
    return memory.node_store(
        content=content,
        keywords=content.lower().split(),
        tags=["user", f"session:{scope}"],
        context_description=f"session={scope} role=user",
        embedding=embedding,
        linked_node_ids=links,
    )


def _access_count(memory: VoolMemory, node_id: str) -> int:
    with memory._lock:
        row = memory._conn.execute(
            "SELECT access_count FROM memory_nodes WHERE node_id = ?",
            (node_id,),
        ).fetchone()
    assert row is not None
    return int(row["access_count"])


def test_scoped_bm25_normalization_ignores_foreign_corpus(tmp_path: Path) -> None:
    memory = VoolMemory(db_path=tmp_path / "memory.db")
    own_first = _store(
        memory,
        session_id="chat-a",
        content="ORBITAL_NEEDLE alpha launch checklist",
        embedding=[1.0, 0.0, 0.0],
    )
    own_second = _store(
        memory,
        session_id="chat-a",
        content="ORBITAL_NEEDLE beta beta recovery checklist",
        embedding=[0.9, 0.1, 0.0],
    )
    before = memory._bm25_scores(
        "ORBITAL_NEEDLE beta",
        session_id="chat-a",
    )

    for index in range(20):
        _store(
            memory,
            session_id="chat-b",
            content=(
                "ORBITAL_NEEDLE beta "
                f"foreign-only-marker-{index} beta beta beta"
            ),
            embedding=[0.0, 1.0, float(index + 1)],
        )

    after = memory._bm25_scores(
        "ORBITAL_NEEDLE beta",
        session_id="chat-a",
    )
    assert set(before) == {own_first.node_id, own_second.node_id}
    assert after == before
    memory.close()


def test_scoped_dedup_never_reads_or_bumps_foreign_nodes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    memory = VoolMemory(db_path=tmp_path / "memory.db")
    foreign = _store(
        memory,
        session_id="chat-b",
        content="DEDUP_NEEDLE exact private statement",
        embedding=[1.0, 0.0, 0.0],
    )
    original_row_to_node = vool_memory_module._row_to_node

    def guarded_row_to_node(row):
        node = original_row_to_node(row)
        if (
            "DEDUP_NEEDLE" in node.content
            and f"session:{_scope('chat-b')}" in node.tags
        ):
            raise AssertionError("foreign node reached scoped deduplication")
        return node

    monkeypatch.setattr(
        vool_memory_module,
        "_row_to_node",
        guarded_row_to_node,
    )
    own = _store(
        memory,
        session_id="chat-a",
        content="DEDUP_NEEDLE exact private statement",
        embedding=[1.0, 0.0, 0.0],
    )
    duplicate = _store(
        memory,
        session_id="chat-a",
        content="DEDUP_NEEDLE exact private statement",
        embedding=[1.0, 0.0, 0.0],
    )

    assert duplicate.node_id == own.node_id
    assert own.node_id != foreign.node_id
    assert _access_count(memory, own.node_id) == 1
    assert _access_count(memory, foreign.node_id) == 0
    memory.close()


def test_scoped_link_expansion_never_hydrates_or_bumps_foreign_nodes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    memory = VoolMemory(db_path=tmp_path / "memory.db")
    foreign = _store(
        memory,
        session_id="chat-b",
        content="FOREIGN_LINK_NEEDLE private linked payload",
        embedding=[0.0, 1.0, 0.0],
    )
    own = _store(
        memory,
        session_id="chat-a",
        content="LOCAL_LINK_NEEDLE authorized root payload",
        embedding=[1.0, 0.0, 0.0],
        links=[foreign.node_id],
    )
    original_row_to_node = vool_memory_module._row_to_node

    def guarded_row_to_node(row):
        node = original_row_to_node(row)
        if "FOREIGN_LINK_NEEDLE" in node.content:
            raise AssertionError("foreign link was hydrated before scope filtering")
        return node

    monkeypatch.setattr(
        vool_memory_module,
        "_row_to_node",
        guarded_row_to_node,
    )
    hits = memory.node_search(
        [1.0, 0.0, 0.0],
        top_k=5,
        min_score=0.0,
        session_id="chat-a",
    )

    assert [node.node_id for node, _score in hits] == [own.node_id]
    assert _access_count(memory, own.node_id) == 1
    assert _access_count(memory, foreign.node_id) == 0
    memory._bump_access(
        [foreign.node_id],
        123.0,
        session_id="chat-a",
    )
    assert _access_count(memory, foreign.node_id) == 0
    memory.close()


def test_ambiguous_and_legacy_scopes_fail_closed_for_scoped_search(
    tmp_path: Path,
    monkeypatch,
) -> None:
    memory = VoolMemory(db_path=tmp_path / "memory.db")
    scope_a = _scope("chat-a")
    scope_b = _scope("chat-b")
    ambiguous = memory.node_store(
        content="AMBIGUOUS_SCOPE_NEEDLE",
        keywords=["ambiguous", "scope", "needle"],
        tags=[f"session:{scope_a}", f"session:{scope_b}"],
        context_description=f"session={scope_a} session={scope_b}",
        embedding=[1.0, 0.0, 0.0],
    )
    legacy = memory.node_store(
        content="LEGACY_SCOPE_NEEDLE",
        keywords=["legacy", "scope", "needle"],
        tags=["session:v2:legacy"],
        context_description="session=v2:legacy",
        embedding=[0.9, 0.1, 0.0],
    )

    original_row_to_node = vool_memory_module._row_to_node

    def reject_quarantined_rows(row):
        node = original_row_to_node(row)
        if "SCOPE_NEEDLE" in node.content:
            raise AssertionError("quarantined row reached scoped ranking")
        return node

    with monkeypatch.context() as patch:
        patch.setattr(
            vool_memory_module,
            "_row_to_node",
            reject_quarantined_rows,
        )
        assert memory.node_search(
            [1.0, 0.0, 0.0],
            top_k=5,
            min_score=0.0,
            session_id="chat-a",
        ) == []
    assert memory.node_search(
        [1.0, 0.0, 0.0],
        top_k=5,
        min_score=0.0,
        session_id="",
    ) == []

    diagnostic_hits = memory.node_search(
        [1.0, 0.0, 0.0],
        top_k=5,
        min_score=0.0,
        session_id=None,
    )
    assert {node.node_id for node, _score in diagnostic_hits} == {
        ambiguous.node_id,
        legacy.node_id,
    }
    memory.close()
