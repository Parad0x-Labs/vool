"""Held-out recall benchmark + the dimension-mismatch regression guard.

Two things this locks down:
  * recall actually ranks the right memory for a held-out query (deterministic —
    uses the hash-BoW fallback, no Ollama needed), and
  * a stored vector of a DIFFERENT dimension (e.g. a legacy 768-dim Ollama vector)
    is still recalled — projected-and-compared — instead of silently scoring 0 and
    vanishing, which is the failure this feature fixes.
"""
from __future__ import annotations

import core.embedding_service as es
from core.embedding_service import EMBED_DIM, cosine_similarity, embed, project_to_dim
from core.vool_memory import VoolMemory

# ── embed() is dimension-stable regardless of backend ─────────────────────────

def test_embed_is_dim_stable_across_backends(monkeypatch):
    # Ollama present + returning 768-dim -> projected down to the canonical dim.
    monkeypatch.setattr(es, "_best_embed_model", lambda: "nomic-embed-text")
    monkeypatch.setattr(es, "_ollama_embed", lambda text, model, timeout=15: [0.01] * 768)
    assert len(es.embed("anything")) == EMBED_DIM
    # Ollama absent -> hash-BoW fallback, same canonical dim.
    monkeypatch.setattr(es, "_best_embed_model", lambda: None)
    assert len(es.embed("anything")) == EMBED_DIM


def test_project_to_dim_is_deterministic_and_comparable():
    v = [float(i % 7) for i in range(768)]
    assert project_to_dim(v, EMBED_DIM) == project_to_dim(v, EMBED_DIM)   # deterministic
    assert len(project_to_dim(v, EMBED_DIM)) == EMBED_DIM
    # a 768-dim vector vs its own 384 projection compares ~1.0 (same fold both sides)
    assert cosine_similarity(v, project_to_dim(v, EMBED_DIM)) > 0.99


# ── held-out recall ranks the right memory ────────────────────────────────────

def test_held_out_recall_ranks_the_right_memory(tmp_path):
    mem = VoolMemory(runtime_home=tmp_path, agent_id="bench")
    facts = [
        ("the database connection timeout is thirty seconds",
         ["database", "connection", "timeout"], "how long is the database connection timeout"),
        ("the project deadline is september fifteenth",
         ["project", "deadline", "september"], "when is the project deadline date"),
        ("the api key lives in the credentials file",
         ["api", "key", "credentials"], "where is the api key credentials stored"),
    ]
    ids = {}
    for content, kw, _q in facts:
        node = mem.node_store(content=content, keywords=kw, tags=["bench"],
                              context_description=content, embedding=embed(content))
        ids[content] = node.node_id
    try:
        for content, _kw, query in facts:
            hits = mem.node_search(embed(query), top_k=3, min_score=0.0)
            assert hits, f"no recall for {query!r}"
            assert hits[0][0].node_id == ids[content], (
                f"{query!r} -> top {hits[0][0].content!r}, expected {content!r}")
    finally:
        mem.close()


# ── regression: a legacy different-dim vector is NOT silently zeroed ───────────

def test_legacy_768_dim_node_is_still_recalled(tmp_path):
    mem = VoolMemory(runtime_home=tmp_path, agent_id="bench2")
    content = "the nightly backup runs at midnight to cold storage"
    legacy_768 = project_to_dim(embed(content), 768)  # simulate an old 768-dim backend
    assert len(legacy_768) == 768
    node = mem.node_store(content=content, keywords=["backup", "midnight"], tags=["b"],
                          context_description=content, embedding=legacy_768)
    try:
        # query with the canonical 384-dim embedding
        hits = mem.node_search(embed(content), top_k=3, min_score=0.1)
        assert node.node_id in [h[0].node_id for h in hits], (
            "legacy 768-dim node must still be recalled via project-and-compare, "
            "not silently scored 0 and dropped")
    finally:
        mem.close()


def test_cross_dim_cosine_is_not_a_silent_zero():
    # The exact bug: comparing different-dim vectors used to return 0.0 silently.
    same = [1.0, 2.0, 3.0, 4.0]
    a = same * 2          # length 8
    b = list(same)        # length 4 (== a folded: out[i%4] += a[i])
    assert cosine_similarity(a, b) > 0.0
