"""Contract tests for the two-miss ranking repair (2026-09-28).

Covers the three repaired contracts without network access:
  1. neural evidence reservations prefer records lexical ranking cannot reach;
  2. the historical-anchor lane admits only possessive-of-new acquisitions on
     the asked side of the anchor, nearest-in-meaning first, bounded window;
  3. the anchor lane never fires without explicit first-person historical
     intent, and the focus union keeps stronger main-leg candidates.
"""
from types import SimpleNamespace

from core import context_retrieval as cr

QUERY = "What new kitchen gadget did I invest in before getting the Air Fryer?"


def _node(content, *, node_id, ts, backend="ollama:nomic-embed-text#retrieval-mrl384-v1"):
    return SimpleNamespace(
        node_id=node_id, content=content, timestamp=ts,
        embedding_backend=backend, embedding=[0.1] * 8,
    )


def test_reservations_prefer_zero_hit_records_over_single_hit_chatter():
    selected = [
        ("Session date: 2025-01-02\nThanks for the suggestions about my desk.", 0.9),   # 2 hits
        ("Session date: 2025-01-03\nI moved my studio easel to the sunroom.", 0.8),     # 0 hits
        ("Session date: 2025-01-04\nI keep my new chisels in the cellar.", 0.7),        # 1 hit ("new")
        ("Session date: 2025-01-05\nA ledger note about the old projector.", 0.6),      # 0 hits
    ]
    scores = {0: 0.90, 1: 0.80, 2: 0.70, 3: 0.60}
    reserved = cr._neural_evidence_reservations(
        "Where should the new suggestions for my desk setup go?", selected, scores)
    # Zero-hit records take the slots first; single-hit records stay eligible
    # only for leftover slots (measured: <=1-hit priority promoted one-hit
    # conversational chatter into the rescue interleave and displaced
    # answer-bearing lexical winners in three previously passing cases).
    assert reserved == {1, 3}


def test_reservations_fill_by_neural_rank_when_all_expressible():
    selected = [
        ("Session date: 2025-01-02\nMy desk lamp is the suggestions pick.", 0.9),
        ("Session date: 2025-01-03\nAnother suggestions note for my desk.", 0.8),
    ]
    reserved = cr._neural_evidence_reservations(
        "Which suggestions cover my desk?", selected, {0: 0.8, 1: 0.9})
    assert reserved == {1, 0} or reserved == {0, 1}


def test_acquired_possession_form_is_possessive_of_new_only():
    assert cr._ACQUIRED_POSSESSION_RE.search("I'm using my new Instant Pot for soups.")
    assert cr._ACQUIRED_POSSESSION_RE.search("My brand-new lathe arrived.")
    # gifts for others / plans / food orders are not self-acquisitions
    assert not cr._ACQUIRED_POSSESSION_RE.search("I bought a beautiful vase for my mom's anniversary.")
    assert not cr._ACQUIRED_POSSESSION_RE.search("We ordered pizza and wings at game night.")
    assert not cr._ACQUIRED_POSSESSION_RE.search("Excited to go shopping for my puffer jacket next week.")


class _FakeMem:
    def __init__(self, nodes):
        self._nodes = nodes

    def session_nodes(self, *, session_id, limit=1024):
        return self._nodes


def _fake_hits():
    # anchor first, then unrelated
    return [
        (_node("Session date: 2025/03/04 (Mon) 09:00\nThe Air Fryer I got yesterday is great.",
               node_id="anchor", ts=200.0), 0.30),
        (_node("Session date: 2025/03/01\n unrelated planner note", node_id="n1", ts=100.0), 0.29),
    ]


def test_anchor_lane_requires_first_person_historical_query():
    q_vec = [0.1] * 8
    pot = _node("Session date: 2025/03/04 (Mon) 06:00\nI'm using my new Instant Pot for stews.",
                node_id="pot", ts=150.0)
    out = cr._historical_anchor_candidates(
        _FakeMem([pot]), QUERY, _fake_hits(), q_vec=q_vec,
        query_backend="ollama:nomic-embed-text#retrieval-mrl384-v1", session_id="s")
    assert out is not None  # sanity: the target query shape fires
    out = cr._historical_anchor_candidates(
        _FakeMem([pot]), "What complements my current photography setup?", _fake_hits(),
        q_vec=q_vec, query_backend="ollama:nomic-embed-text#retrieval-mrl384-v1", session_id="s")
    assert out is None
    out = cr._historical_anchor_candidates(
        _FakeMem([pot]), "What did the neighbor buy before getting the Air Fryer?", _fake_hits(),
        q_vec=q_vec, query_backend="ollama:nomic-embed-text#retrieval-mrl384-v1", session_id="s")
    assert out is None


def test_anchor_lane_admits_possessive_of_new_on_asked_side_within_window():
    pot = _node("Session date: 2025/03/04 (Mon) 06:00\nI'm using my new Instant Pot for stews.",
                node_id="pot", ts=150.0)
    gift = _node("Session date: 2025/03/04 (Mon) 12:00\nI bought a vase for my mom today.",
                 node_id="gift", ts=180.0)
    far = _node("Session date: 2025/02/20\nI moved my new lathe to the barn.",
                node_id="far", ts=10.0)
    later = _node("Session date: 2025/03/05\nMy new kettle arrived this morning.",
                  node_id="later", ts=220.0)
    mem = _FakeMem([pot, gift, far, later])
    out = cr._historical_anchor_candidates(
        mem, QUERY, _fake_hits(), q_vec=[0.1] * 8,
        query_backend="ollama:nomic-embed-text#retrieval-mrl384-v1", session_id="s")
    assert out is not None
    anchor_pos, extras, keep, _reserve = out
    assert anchor_pos == 0
    assert [n.node_id for n, _score in extras] == ["pot"]  # same day, stored before anchor
    assert all(n.node_id != "pot" for n, _s in keep)  # lifted, not duplicated
    # after-direction picks the later acquisition instead
    q_after = "What new kitchen gadget did I get after getting the Air Fryer?"
    out = cr._historical_anchor_candidates(
        mem, q_after, _fake_hits(), q_vec=[0.1] * 8,
        query_backend="ollama:nomic-embed-text#retrieval-mrl384-v1", session_id="s")
    assert out is not None
    _pos, extras, _keep, _reserve = out
    assert [n.node_id for n, _score in extras] == ["later"]


def test_anchor_lane_ranks_nearest_in_meaning_first():
    strong = _node("Session date: 2025/03/04 (Mon) 06:00\nMy new pressure cooker handles stews.",
                   node_id="strong", ts=140.0)
    weak = _node("Session date: 2025/03/04 (Mon) 05:00\nMy new workshop apron fits well.",
                 node_id="weak", ts=130.0)

    class VecMem(_FakeMem):
        pass

    mem = VecMem([strong, weak])
    hits = _fake_hits()
    anchor_embedding = [0.2] * 8
    hits[0][0].embedding = anchor_embedding

    def cos(a, b):
        return 1.0 if sum(x * y for x, y in zip(a, b)) > 0 else 0.0

    strong.embedding = [0.3] * 8   # same direction as the query vector
    weak.embedding = [-0.3] * 8    # opposite direction: low similarity
    import core.embedding_service as es
    original = es.cosine_similarity
    es.cosine_similarity = lambda a, b: cos(a, b)
    try:
        out = cr._historical_anchor_candidates(
            mem, QUERY, hits, q_vec=[0.9] * 8,
            query_backend="ollama:nomic-embed-text#retrieval-mrl384-v1", session_id="s")
    finally:
        es.cosine_similarity = original
    assert out is not None
    _pos, extras, _keep, _reserve = out
    assert [n.node_id for n, _score in extras] == ["strong"]


def _probe_capsule(main, focus, query):
    """Run the REAL capsule pipeline over controlled hybrid-search results.

    Same shape as the independent review's frozen probes: the memory object's
    hybrid search returns `main` on the first call and `focus` on the second
    (the focus probe), embeddings are a fixed stamped vector, and the genuine
    selection, distillation and packing code executes.
    """
    from unittest.mock import patch

    from core.context_capsule_v2 import resolve_budget
    BE = "ollama:nomic-embed-text#retrieval-mrl384-v1"
    chat = "contract-probe"

    class Mem:
        def __init__(self):
            self.calls = []

        def node_search_hybrid(self, q, v, **kw):
            self.calls.append(q)
            return main if len(self.calls) == 1 else focus

        def session_nodes(self, **kw):
            return []

        def close(self):
            pass

    mem = Mem()
    with patch.object(cr, "_open_memory_for_runtime", return_value=mem), \
            patch.object(cr, "embed_stamped", return_value=([1.0, 0.0, 0.0, 0.0], BE)):
        out = cr._capsule_v2_inject_retrieved(
            chat, query, [{"role": "user", "content": query}],
            budget=resolve_budget(bucket="B", role="general"), runtime_home="/tmp/probe-home")
    return "\n".join(x["content"] for x in out if "retrieved_context" in x.get("content", ""))


def _probe_node(i, text):
    return SimpleNamespace(
        node_id=i, content=text, timestamp=1741000000.0,
        embedding_backend="ollama:nomic-embed-text#retrieval-mrl384-v1",
        embedding=[1.0, 0.0, 0.0, 0.0],
        tags=["session:" + cr._session_scope_key("contract-probe")],
        context_description="")


def test_focus_only_evidence_survives_selection_under_full_and_partial_main_pools():
    # Regressions from the independent review (2026-09-28): with a full
    # 12-result main pool the focus-only answer never entered the candidate
    # pool; with 8 results it sat beyond the 8-item selection fold. The
    # production merge must keep focus-only subject evidence reachable by
    # selection in both shapes, under unchanged budgets.
    cases = [
        (12, "What should I pack for my observatory visit?",
         "My observatory visit requires the amber entry badge.", "amber entry badge"),
        (8, "What should I bring for my ceramics workshop?",
         "My ceramics workshop requires the cobalt apron.", "cobalt apron"),
        (2, "What should I bring for my rowing lesson?",
         "My rowing lesson requires the orange wristband.", "orange wristband"),
    ]
    for count, query, fact, expected in cases:
        main = [(_probe_node(f"filler-{i}", f"My locker number is {300 + i}."), 0.8 - i * 0.01)
                for i in range(count)]
        focus = [(_probe_node("answer", fact), 0.99)]
        block = _probe_capsule(main, focus, query)
        assert expected in block, (count, expected, block[:200])


def test_main_leg_head_survives_focus_junk_in_production_path():
    # The fold interleave must not displace the main leg's own head evidence:
    # the strongest main result stays selected even when the focus leg is
    # full of unrelated uniques.
    main = [(_probe_node("main-answer", "My climbing gym requires the gray chalk bag."), 0.9)] + [
        (_probe_node(f"m-{i}", f"My locker number is {400 + i}."), 0.8 - i * 0.01) for i in range(11)]
    focus = [(_probe_node(f"f-{i}", f"My storage unit invoice is {500 + i}."), 0.5) for i in range(12)]
    block = _probe_capsule(main, focus, "What should I pack for my climbing gym session?")
    assert "gray chalk bag" in block


def test_named_reference_lane_requires_possessive_category_query():
    ref = _node("Session date: 2025/03/04 (Mon) 09:00\nWhich cases fit my Field Notes notebook?",
                node_id="ref", ts=100.0)
    anchor = _node("Session date: 2025/03/05 (Tue) 10:00\nI love my desk gear overall.",
                   node_id="anchor", ts=110.0)

    class VecMem(_FakeMem):
        pass

    import core.embedding_service as es
    original = es.cosine_similarity
    es.cosine_similarity = lambda a, b: 0.9
    try:
        hits = [(anchor, 0.4)]
        out = cr._named_reference_candidates(
            VecMem([ref, anchor]), "What should I add to my current stationery setup?",
            hits, q_vec=[0.1] * 8,
            query_backend="ollama:nomic-embed-text#retrieval-mrl384-v1", session_id="s")
        assert out is not None
        _pos, extras, keep, _reserve = out
        assert [n.node_id for n, _s in extras] == ["ref"]
        assert [n.node_id for n, _s in keep] == ["anchor"]
        out = cr._named_reference_candidates(
            VecMem([ref, anchor]), "What is the capital of France?", hits,
            q_vec=[0.1] * 8,
            query_backend="ollama:nomic-embed-text#retrieval-mrl384-v1", session_id="s")
        assert out is None
    finally:
        es.cosine_similarity = original


def test_named_reference_lane_lifts_fold_blocked_reference_records():
    # a named-reference record already in the candidate list but sitting past
    # the selection fold is lifted below the anchor, not duplicated
    ref = _node("Session date: 2025/03/04 (Mon) 09:00\nWhich cases fit my Field Notes notebook?",
                node_id="ref", ts=100.0)
    anchor = _node("Session date: 2025/03/05 (Tue) 10:00\nI love my desk gear overall.",
                   node_id="anchor", ts=110.0)
    filler = [(_node(f"filler-{i}", node_id=f"f{i}", ts=50.0 + i), 0.28) for i in range(9)]
    hits = [(anchor, 0.4)] + filler[:8] + [(ref, 0.26)]  # ref at position 9, past the fold

    import core.embedding_service as es
    original = es.cosine_similarity
    es.cosine_similarity = lambda a, b: 0.9
    try:
        out = cr._named_reference_candidates(
            _FakeMem([ref, anchor] + [n for n, _ in filler]),
            "What should I add to my current stationery setup?",
            hits, q_vec=[0.1] * 8,
            query_backend="ollama:nomic-embed-text#retrieval-mrl384-v1", session_id="s")
        assert out is not None
        _pos, extras, keep, _reserve = out
        assert [n.node_id for n, _s in extras] == ["ref"]
        assert len(keep) == len(hits) - 1
        assert all(n.node_id != "ref" for n, _s in keep)
    finally:
        es.cosine_similarity = original


def test_rescue_cap_does_not_delete_unpromoted_records():
    # Regression from the second independent review (2026-09-28): short
    # assertion-group removal used to happen for EVERY eligible record before
    # the rescue list was capped to two, so the third eligible record was
    # deleted from ordinary ranking entirely even though the four-chunk
    # output budget could fit it. The capped promoted set must be chosen
    # before any destructive replacement.
    q = "What do I need for my wetland outing?"
    contents = [
        "My outing kit includes the scarlet drybag.",
        "My outing kit includes the silver whistle.",
        "My outing kit includes the jade gaiters.",
    ]
    ranked = cr._ranked_recall_chunks(
        q, contents, semantic_record_indices={0, 1}, anchor_record_indices={2})
    top4_records = [item[4] for item in ranked[:4]]
    assert set(top4_records) == {0, 1, 2}
    block, telemetry = cr._distill_retrieved_hits(
        q, [(text, 0.9 - i * 0.01) for i, text in enumerate(contents)],
        semantic_record_indices={0, 1}, anchor_record_indices={2})
    for fact in ("scarlet drybag", "silver whistle", "jade gaiters"):
        assert fact in block, fact
