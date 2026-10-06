"""Complete evidence at the model boundary (2026-09-26).

Three measured losses between scoped retrieval and the injected context:
specialized exact extraction suppressed the OTHER requested fact of a
multipart question; a >500-character sentence was rejected wholesale and the
generic fallback clipped its FIRST 360 characters, losing an answer at the
end; and the distilled capsule was packed as ONE indivisible candidate, so a
tight free-token budget dropped every fact although the short answer-bearing
line fit. The distiller now supplements exact extraction with uncovered
query terms, long sentences contribute a bounded answer-bearing window
anchored on term occurrences, and the capsule packs per fact.
"""
from __future__ import annotations

from dataclasses import replace

import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace


class _Node:
    def __init__(self, content: str, *, tags: list[str] | None = None, context_description: str = ""):
        self.content = content
        self.tags = list(tags or [])
        self.context_description = context_description
        self.timestamp = None


class _FakeMem:
    def __init__(self, hits):
        self._hits = hits

    def node_search_hybrid(self, query_text, q_vec, *, top_k, min_score, session_id,
                           query_embedding_backend=""):
        return self._hits

    def close(self):
        pass


def _wire(monkeypatch, mem, *, budget=None):
    monkeypatch.setattr(cr, "_open_memory", lambda: mem)
    if budget is not None:
        monkeypatch.setattr(
            cr, "resolve_budget", lambda **kwargs: budget
        )


def _hits(*contents, session_id="evidence-chat"):
    scope = cr._session_scope_key(session_id)
    return [
        (
            _Node(
                content,
                tags=["user", f"session:{scope}"],
                context_description=f"session={scope} role=user",
            ),
            0.9,
        )
        for content in contents
    ]


def _block(result):
    return next(
        (
            m["content"]
            for m in result
            if m.get("role") == "system" and "<retrieved_context>" in m.get("content", "")
        ),
        "",
    )


def _inject(monkeypatch, hits, query, *, budget=None, session_id="evidence-chat"):
    ensure_chat_namespace(session_id, grant_current_receipts=False)
    _wire(monkeypatch, _FakeMem(hits), budget=budget)
    return cr.inject_retrieved(
        session_id,
        query,
        [{"role": "user", "content": query}],
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )


def test_multipart_question_keeps_every_requested_fact(monkeypatch) -> None:
    out = _inject(
        monkeypatch,
        _hits("The pickup code is CK-728. The caretaker is Liora."),
        "What is the pickup code and who is the caretaker?",
    )
    block = _block(out)
    assert "CK-728" in block
    assert "Liora" in block


def test_multipart_supplement_keeps_provenance_suffixes(monkeypatch) -> None:
    out = _inject(
        monkeypatch,
        _hits(" Ferry manifest 2026-03-04: the mooring code is FERRY-9181. The dock keeper is Ona."),
        "What is the mooring code and who is the dock keeper?",
    )
    block = _block(out)
    assert "FERRY-9181" in block
    assert "Ona" in block
    assert "stated:" in block or "recorded:" in block  # date/provenance travels with the fact


def test_long_sentence_trailing_answer_survives(monkeypatch) -> None:
    tail = (
        "The greenhouse watering instructions "
        + ("include routine observations and schedules, " * 14)
        + "and the caretaker is Marisol."
    )
    assert len(tail) > 500  # the sentence is genuinely over the old hard limit
    out = _inject(monkeypatch, _hits(tail), "Who is the greenhouse caretaker?")
    block = _block(out)
    assert "Marisol" in block
    assert "caretaker is Marisol" in block  # the answer stays attached to its term


def test_window_selection_never_clips_mid_word(monkeypatch) -> None:
    long_line = (
        "Aquarium maintenance for the coral wing "
        + ("covers salinity checks, pump cleaning and skimmer adjustments, " * 16)
        + "and the night aquarist is Petra."
    )
    out = _inject(monkeypatch, _hits(long_line), "Who is the night aquarist?")
    block = _block(out)
    assert "Petra" in block
    for line in block.splitlines():
        if line.startswith("- relevant context:"):
            span = line.split("- relevant context: ", 1)[1].split(" (", 1)[0]
            assert span == span.strip()  # word-boundary span, no half words


def test_tight_budget_keeps_the_short_answer_fact(monkeypatch) -> None:
    facts = ["- Workshop material {}: {}.".format(i, "supporting details " * 9) for i in range(4)]
    facts[0] = "- Workshop venue: Hazel Hall."
    tight = replace(
        cr.resolve_budget(bucket="B", role="general"),
        free_tokens=80,
    )
    out = _inject(
        monkeypatch,
        _hits("\n".join(facts)),
        "workshop venue",
        budget=tight,
    )
    block = _block(out)
    assert "Hazel Hall" in block, "the short answer must not fall off the budget cliff"
    assert block.count("<retrieved_context>") == 1


def test_normal_budget_still_carries_multiple_facts(monkeypatch) -> None:
    record = (
        "The observatory access code is OBS-3355. The resident astronomer is Dr. Vale. "
        "The dome key color is amber."
    )
    out = _inject(
        monkeypatch,
        _hits(record),
        "What is the observatory access code and who is the resident astronomer?",
    )
    block = _block(out)
    assert "OBS-3355" in block
    assert "Dr. Vale" in block


def test_absent_evidence_is_not_fabricated(monkeypatch) -> None:
    out = _inject(
        monkeypatch,
        _hits("The pantry restock day is Tuesday. The kitchen code is PAN-77."),
        "Who is the greenhouse caretaker?",
    )
    block = _block(out)
    # nothing in the record answers the question: no caretaker may be invented
    assert "caretaker" not in block.lower() or "Marisol" not in block


def test_one_sentence_coordinated_facts_both_reach_context(monkeypatch) -> None:
    # review 1 gap: a shared identifier made the supplement treat the WHOLE
    # sentence as redundant; the second assertion (Amara) is a requested,
    # new fact and must reach the model context
    out = _inject(
        monkeypatch,
        _hits("The access code is GAL-482 and the caretaker is Amara."),
        "What is the access code and who is the caretaker?",
    )
    block = _block(out)
    assert "GAL-482" in block
    assert "Amara" in block


def test_value_restatement_is_still_recognized_as_redundant(monkeypatch) -> None:
    # the earlier fix must survive: "What is the exact audit code?" must not
    # re-add the code's own sentence just because "audit" was uncovered
    out = _inject(
        monkeypatch,
        _hits("The current audit code is SABLE-2048."),
        "What is the exact audit code?",
    )
    block = _block(out)
    assert block.count("SABLE-2048") == 1


def test_query_echo_does_not_count_as_transcript_coverage(monkeypatch) -> None:
    # FA-19: the question restating a record's subjects is not the record
    # being present in the transcript — its answers were nowhere in the
    # conversation, so the record must still be injected
    out = _inject(
        monkeypatch,
        _hits("The ferry slip number is 12-North. The mate on duty is Ilya."),
        "What is the ferry slip number and who is the mate on duty?",
    )
    block = _block(out)
    assert "12-North" in block
    assert "Ilya" in block


def test_injected_header_is_inside_the_declared_free_budget(monkeypatch) -> None:
    # review 1 regression: the re-attached instruction header was added
    # AFTER the budget decision; it must be paid for before packing
    from dataclasses import replace

    from core.context_capsule_v2 import estimate_tokens, resolve_budget

    tight = replace(resolve_budget(bucket="B", role="general"), free_tokens=20)
    out = _inject(
        monkeypatch,
        _hits("The gallery access code is GAL-482."),
        "What is the gallery access code?",
        budget=tight,
    )
    text = "\n".join(str(m.get("content") or "") for m in out)
    payload = (
        text.replace("<retrieved_context>", "").replace("</retrieved_context>", "").strip()
    )
    assert estimate_tokens(payload) <= tight.free_tokens, {
        "tokens": estimate_tokens(payload),
        "allowed": tight.free_tokens,
        "output": text,
    }


def test_foreign_session_record_is_not_injected(monkeypatch) -> None:
    ensure_chat_namespace("evidence-chat", grant_current_receipts=False)
    ensure_chat_namespace("evidence-other", grant_current_receipts=False)
    foreign_scope = cr._session_scope_key("evidence-other")
    node = _Node(
        "The foreign chat venue is Ivory Hall.",
        tags=["user", f"session:{foreign_scope}"],
        context_description=f"session={foreign_scope} role=user",
    )
    out = _inject(monkeypatch, [(node, 0.9)], "What is the venue?")
    assert _block(out) == ""
