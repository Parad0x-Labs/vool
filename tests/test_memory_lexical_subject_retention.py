"""Subject-term retention in the scoped BM25 leg (job memrepair-focused-20260925).

In a small project store the subject term appears in every record, so the old
document-frequency cutoff max(2, N/4) deleted it; the lexical leg collapsed to
whatever anchor terms survived, and records below the semantic floor became
unreachable by hybrid search (measured: "What phase of Helios is left to do?"
surfaced only 3 of 6 sessions; the remaining-work evidence was absent from the
engine result AND the injected capsule).

The cutoff conflated two jobs: WEIGHTING shared terms (IDF's job — normal BM25
weighting already gives a ubiquitous term ~0 influence on ordering) and
ADMITTING terms to the leg. Admission is now explicit: informative terms
(0 < df < N) are always kept with normal weighting; a term present in every
scoped record is kept only when an informative anchor exists, or in a 1-2
record store. Stopword-mass queries still earn no leg (_QUERY_STOPWORDS).

These are internal mechanism tests (fixtures), not independent validation.
"""
from __future__ import annotations

import math

import pytest

from core.vool_memory import VoolMemory, _QUERY_STOPWORDS, _scoped_bm25_scores


def _vec(*xs: float) -> list[float]:
    mag = math.sqrt(sum(x * x for x in xs)) or 1.0
    return [x / mag for x in xs]


def _rows(*contents: str) -> list[dict[str, str]]:
    return [{"node_id": f"n{i}", "content": c, "keywords": ""} for i, c in enumerate(contents)]


HELIOS = [
    "Kicking off the Helios migration plan: two phases, database first.",
    "The Helios migration failed with error E-4471 during the database phase.",
    "Retried the Helios migration and it succeeded, database phase complete.",
    "Helios rollout status: completed about 80 percent of the services.",
    "Helios cleanup scheduled, old cluster shuts down after the audit.",
    "Audit passed, so the Helios old cluster is now shut down.",
]


def _store(tmp_path, contents, *, subject="Helios", session="proj"):
    """Deterministic store: every record shares one embedding orthogonal to the
    query vector, so cosine sits below _SEMANTIC_FLOOR and ONLY the lexical leg
    can surface records — the exact shape of the measured defect."""
    from core.context_retrieval import _session_scope_key

    m = VoolMemory(agent_id="t", db_path=str(tmp_path / "m.db"))
    scope = "session:" + _session_scope_key(session)
    for c in contents:
        m.node_store(
            c, [], ["user", "scope:chat", scope],
            "session=" + _session_scope_key(session) + " scope=chat",
            _vec(0.0, 1.0, 0.0),
        )
    return m


def test_subject_in_every_record_keeps_evidence_reachable(tmp_path) -> None:
    """1a. Original mechanism: hybrid search must surface the remaining-work
    sessions when the subject appears in all 6 records."""
    m = _store(tmp_path, HELIOS)
    hits = m.node_search_hybrid(
        "What phase of Helios is left to do?", _vec(1.0, 0.0, 0.0),
        top_k=10, min_score=0.0, session_id="proj",
    )
    texts = {n.content for n, _s in hits}
    assert any("cleanup" in t for t in texts), texts
    assert any("shut down" in t for t in texts), texts
    assert len(hits) >= 4, [n.content for n, _s in hits]
    m.close()


def test_different_domain_same_mechanism(tmp_path) -> None:
    """1b. Different domain, no renamed entities: the orchard journal."""
    orchard = [
        "Orchard diary: pruned the old apple rows.",
        "Orchard diary: fixed the irrigation pump.",
        "Orchard diary: grafting workshop attended.",
        "Orchard diary: mowing still pending near the fence.",
    ]
    m = _store(tmp_path, orchard)
    hits = m.node_search_hybrid(
        "What orchard work is still pending?", _vec(1.0, 0.0, 0.0),
        top_k=10, min_score=0.0, session_id="proj",
    )
    texts = {n.content for n, _s in hits}
    assert any("mowing still pending" in t for t in texts), texts
    m.close()


@pytest.mark.parametrize("n", [1, 2, 6, 12])
def test_tiny_stores_keep_matching_terms(n) -> None:
    """2. Stores of 1, 2, 6, 12 records: every record containing the subject
    earns a lexical leg when an anchor exists; 1-2 record stores keep their
    only-matching terms even without an anchor."""
    contents = [f"kestrel node {i}: routine telemetry recorded fine" for i in range(n)]
    contents[-1] = f"kestrel node {n-1}: recalibration pending"
    scores = _scoped_bm25_scores(_rows(*contents), ["kestrel", "recalibration"])
    assert len(scores) == n, scores  # subject in every record
    top = max(scores, key=lambda k: scores[k])
    top_row = next(r for r in _rows(*contents) if r["node_id"] == top)
    assert "recalibration" in top_row["content"]  # anchor orders the leg

    only = _scoped_bm25_scores(_rows(*contents[:2]), ["kestrel"])
    if n >= 2:
        # no informative anchor (df(kestrel)==N) but a 1-2 record store keeps
        # its vocabulary match; larger stores do not (baseline behavior)
        assert len(only) == 2
    else:
        assert len(only) == 1


def test_case_is_never_the_rule() -> None:
    """3. Capitalization is not consulted: lowercase identifiers and
    sentence-initial ordinary words behave identically."""
    contents = [
        "the kestrel tracker rebooted twice overnight",
        "Today the printer jammed before the standup",
    ]
    lower = _scoped_bm25_scores(_rows(*contents), ["kestrel", "rebooted"])
    upper = _scoped_bm25_scores(_rows(*contents), ["Kestrel", "Rebooted"])
    assert lower == upper
    assert len(lower) == 1  # only the kestrel record matches

    sentence_initial = _scoped_bm25_scores(_rows(*contents), ["today", "printer", "jammed"])
    assert len(sentence_initial) == 1


def test_query_shapes_subject_only_mixed_generic() -> None:
    """4. Subject-only / mixed / generic-only queries.

    Subject-only: a term present in every scoped record is a genuine keyword
    match — every containing record earns the leg (IDF ~ 0 keeps it from
    ordering anything by itself). This replaces an earlier assertion that
    encoded the anchor-required implementation, which left subject-only
    queries with no lexical path at all (review finding: exact project-name
    queries lost every candidate)."""
    subject_only = _scoped_bm25_scores(_rows(*HELIOS), ["helios"])
    assert len(subject_only) == 6
    assert all(v > 0.0 for v in subject_only.values())  # every containing record admitted
    # IDF ~ 0 leaves only tf/length differences — ordering influence is minimal
    assert max(subject_only.values()) - min(subject_only.values()) < 0.1
    # mixed subject+action: everything containing the subject competes, the
    # anchor record leads
    mixed = _scoped_bm25_scores(_rows(*HELIOS), ["helios", "cleanup"])
    assert len(mixed) == 6
    top = max(mixed, key=lambda k: mixed[k])
    assert "cleanup" in next(r for r in _rows(*HELIOS) if r["node_id"] == top)["content"]
    # generic/stopword-mass: no leg, nothing manufactured
    generic = [t for t in ["what", "is", "my", "name", "number"] if t not in _QUERY_STOPWORDS]
    assert _scoped_bm25_scores(_rows(*HELIOS), generic) == {}


def test_rare_identifier_and_digit_terms_survive() -> None:
    """Rare identifiers, punctuation/digit forms keep their lexical path."""
    contents = [
        "Helios incident E-4471 logged by the on-call engineer",
        "Helios weekly report: nothing to report",
        "Helios config pinned to build 2024.11.3-rc1",
    ]
    # tokens are [a-z0-9_]{2,}; the digit-bearing forms still match their own
    # records through the lexical path
    scores = _scoped_bm25_scores(_rows(*contents), ["helios", "4471", "2024"])
    rows = {r["node_id"]: r["content"] for r in _rows(*contents)}
    assert scores[next(n for n, c in rows.items() if "4471" in c)] > 0
    assert scores[next(n for n, c in rows.items() if "2024" in c)] > 0


def test_strong_distractor_does_not_bury_evidence_or_flood(tmp_path) -> None:
    """5. A rare high-IDF distractor keyword may lead the lexical leg, but the
    subject-anchored evidence still surfaces and results stay bounded."""
    contents = [
        "Helios sprint note: the deadline for the audit is tight",
        "Helios migration succeeded, database phase complete",
        "Helios rollout at 80 percent of services",
    ]
    m = _store(tmp_path, contents)
    hits = m.node_search_hybrid(
        "Helios deadline audit", _vec(1.0, 0.0, 0.0),
        top_k=10, min_score=0.0, session_id="proj",
    )
    assert 0 < len(hits) <= 10
    texts = [n.content for n, _s in hits]
    assert any("deadline" in t for t in texts)
    assert any("succeeded" in t or "80 percent" in t for t in texts)
    m.close()


def test_scope_isolation_keeps_foreign_records_out(tmp_path) -> None:
    """7a. df is computed over the authorized scoped rows only; a foreign
    session's records never earn legs for a scoped query."""
    from core.context_retrieval import _session_scope_key

    m = VoolMemory(agent_id="t", db_path=str(tmp_path / "m.db"))
    for session, contents in (
        ("alpha", ["Helios alpha note: recalibration pending"]),
        ("beta", ["Helios beta note: nothing pending, all clear"]),
    ):
        scope = "session:" + _session_scope_key(session)
        for c in contents:
            m.node_store(
                c, [], ["user", "scope:chat", scope],
                "session=" + _session_scope_key(session) + " scope=chat",
                _vec(0.0, 1.0, 0.0),
            )
    scores = m._bm25_scores("Helios pending recalibration", session_id="alpha")
    got = []
    with m._lock:
        for nid in scores:
            row = m._conn.execute(
                "SELECT content FROM memory_nodes WHERE node_id = ?", (nid,)
            ).fetchone()
            got.append(row["content"])
    assert len(got) == 1 and "alpha" in got[0], got
    m.close()


def test_no_supported_evidence_means_no_injection(tmp_path, monkeypatch) -> None:
    """7b. No unsupported-evidence injection: a query whose terms match no
    scoped record injects nothing at the model boundary."""
    import core.embedding_service as es

    monkeypatch.setattr(es, "_best_embed_model", lambda: None)
    from core.context_retrieval import inject_retrieved, get_last_retrieval_telemetry
    from core.runtime_paths import configure_runtime_home

    profile = tmp_path / "capsule-profile"
    profile.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(profile))
    monkeypatch.setenv("VOOL_HOME", str(profile))
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(profile / "workspace"))
    monkeypatch.setenv("VOOL_CONTEXT_CAPSULE_V2", "1")
    configure_runtime_home(profile)

    m = _store(tmp_path / "iso", HELIOS, session="iso-chat")
    m.close()
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy

    ensure_chat_namespace("iso-chat", grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id="iso-chat")
    out = inject_retrieved(
        "iso-chat",
        "What is my passport number?",
        [{"role": "user", "content": "What is my passport number?"}],
        access_policy=policy,
        source_context={"chat_id": "iso-chat", "runtime_home": str(profile / "iso")},
    )
    injected = " ".join(str(msg.get("content", "")) for msg in out)
    assert "<retrieved_context>" not in injected
    tel = get_last_retrieval_telemetry()
    assert tel.get("capsule_mode") in {"no_hits", "empty", "disabled_no_hits"}


def test_backend_stamping_with_real_counters(monkeypatch) -> None:
    """6. Neural backend available and unavailable, with actual counters."""
    import core.embedding_service as es

    # unavailable: hash fallback stamped and counted
    monkeypatch.setattr(es, "_best_embed_model", lambda: None)
    vec, backend = es.embed_stamped("kestrel recalibration")
    assert backend.startswith("hash-bow")
    assert es.embedding_stats()["hash_fallback_calls"] >= 1

    # available (simulated deterministic model behind the same seam): neural
    # stamp and counters; cross-space cosine must not happen for hash queries
    monkeypatch.setattr(es, "_neural_down_until", 0.0)
    monkeypatch.setattr(es, "_best_embed_model", lambda: "test-embed")
    monkeypatch.setattr(
        es,
        "_ollama_embed_batch",
        lambda texts, model, timeout=15: [[0.1, 0.2, 0.3] for _ in texts],
    )
    before = es.embedding_stats()["neural_calls"]
    vec2, backend2 = es.embed_stamped("kestrel recalibration")
    assert backend2 == "ollama:test-embed#p384"
    assert es.embedding_stats()["neural_calls"] > before
