"""Review-exposed regressions (independent review of c005d791, 2026-09-25).

Preserved verbatim from the reviewer's challenge set
(artifacts/memrepair-focused-review-20260925/test_independent_focused_review.py,
sha256 0e2b385164ef63612b5acf6e9e68c2c9c19e70ec1630f10462fe3a2d269b32a2)
plus engine-level companions. These five cases failed on c005d791 (four also
on the pristine baseline; the date-role collapse was introduced by cc6829d0):

1. known-fact shortcut lines (exact code / current region) bypassed date
   attachment entirely;
2. timezone-qualified envelope timestamps were truncated to ambiguous ones;
3. unrelated date roles (publish date + warranty expiry) collapsed into one
   unlabeled pair attached to an unrelated fact;
4. subject-only queries lost every lexical candidate in a 3-record store
   (anchor-required admission);
5. generic stopword-mass queries must STILL earn nothing (control).
"""
from __future__ import annotations

import math

from core import context_retrieval as cr
from core.vool_memory import VoolMemory, _scoped_bm25_scores


def test_exact_fact_date_survives_the_known_fact_path():
    content = "Session date: 2024-06-13\nThe deployment region changed to eu-west-3."
    out, tel = cr._distill_retrieved_hits(
        "When did the deployment region change?", [(content, 0.9)]
    )
    assert "eu-west-3" in out
    assert "2024-06-13" in out, out


def test_code_fact_date_survives_the_known_fact_path():
    content = "Logged on 2024-07-22\nThe access code is XR-552."
    out, tel = cr._distill_retrieved_hits(
        "When was the access code assigned?", [(content, 0.9)]
    )
    assert "XR-552" in out
    assert "2024-07-22" in out, out


def test_source_date_keeps_timezone():
    content = "Message sent: 2025-04-03T23:30:00-07:00\nThe conservatory reopened to visitors."
    out, tel = cr._distill_retrieved_hits(
        "When did the conservatory reopen?", [(content, 0.9)]
    )
    assert "conservatory" in out
    assert "2025-04-03T23:30:00-07:00" in out, out


def test_distinct_date_roles_do_not_become_an_unlabeled_pair():
    content = "Published on 2024-01-10\nWarranty expires on 2026-07-08.\nThe invoice was paid in full."
    out, tel = cr._distill_retrieved_hits("When was the invoice paid?", [(content, 0.9)])
    # An expiry date may be omitted or retained with its role, not restated as
    # an unlabeled source date associated with invoice payment.
    assert "invoice" in out
    assert "2026-07-08" not in out or "expires" in out.lower(), out


def test_subject_only_query_has_a_lexical_path_in_three_record_store(tmp_path):
    chat = "review-aurora"
    scope = "session:" + cr._session_scope_key(chat)
    m = VoolMemory(agent_id="review", db_path=str(tmp_path / "memory.db"))
    for s in [
        "Aurora irrigation pump arrived.",
        "Aurora seed order delayed.",
        "Aurora greenhouse inspection completed.",
    ]:
        m.node_store(
            s, [], ["user", "scope:chat", scope],
            "session=" + cr._session_scope_key(chat) + " scope=chat", [0.0, 1.0],
        )
    hits = m.node_search_hybrid("Aurora", [1.0, 0.0], top_k=10, min_score=0.0, session_id=chat)
    m.close()
    assert hits, "Exact project-name query lost every lexical candidate"


def test_generic_query_still_does_not_manufacture_lexical_evidence(tmp_path):
    chat = "review-generic"
    scope = "session:" + cr._session_scope_key(chat)
    m = VoolMemory(agent_id="review", db_path=str(tmp_path / "memory.db"))
    for s in ["Orchard apples ripened.", "Garden watering completed.", "Workshop benches repaired."]:
        m.node_store(
            s, [], ["user", "scope:chat", scope],
            "session=" + cr._session_scope_key(chat) + " scope=chat", [0.0, 1.0],
        )
    hits = m.node_search_hybrid("what is my", [1.0, 0.0], top_k=10, min_score=0.0, session_id=chat)
    m.close()
    assert not hits


def test_ordinary_source_date_preservation_positive():
    out, tel = cr._distill_retrieved_hits(
        "When did the conservatory reopen?",
        [("Session date: 2024-10-12\nThe conservatory reopened to visitors.", 0.9)],
    )
    assert "2024-10-12" in out
    assert "conservatory" in out


# ── engine-level companions for the admission rule ──────────────────────────


def test_subject_only_df_equals_n_admits_with_tied_idf() -> None:
    """df == N admission at the scoring level: all containing records earn the
    leg and the normalized scores tie (IDF ~ 0 cannot order anything)."""
    rows = [
        {"node_id": "a", "content": "Aurora irrigation pump arrived.", "keywords": ""},
        {"node_id": "b", "content": "Aurora seed order delayed.", "keywords": ""},
        {"node_id": "c", "content": "Aurora greenhouse inspection completed.", "keywords": ""},
    ]
    scores = _scoped_bm25_scores(rows, ["aurora"])
    assert set(scores) == {"a", "b", "c"}
    assert len(set(scores.values())) == 1


def test_no_matching_term_still_earns_no_leg() -> None:
    """df == 0 for every term: no lexical leg (unknown-value queries inject
    nothing)."""
    rows = [
        {"node_id": "a", "content": "Orchard apples ripened.", "keywords": ""},
    ]
    assert _scoped_bm25_scores(rows, ["passport", "number"]) == {}
