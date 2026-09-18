"""Found by the browser proof: a green receipt is not grounding.

BOTH root causes are CLOSED on the unified candidate, by two different lanes.
Root cause 2: `core.claim_support` gives `inspect_evidence_binding` per-claim,
per-source support, and the one-shared-word floor is gone. Root cause 1: the
recognizer asymmetry belonged to the requirements lane, and
`core.current_information_signals` — the canonical current-information authority
integrated here — now fires `current_info_signal:news_request` on the phrasing
that used to be missed. The fixtures stay verbatim; the assertions now pin the
repaired behaviour on both axes so neither can regress unnoticed.

WHAT WAS MEASURED
-----------------
Isolated daemon 127.0.0.1:4291 (candidate build), chat
`openclaw:365939aae19b7e5f3a8d`, request "show me recent news coverage about the
Rust programming language". Runtime event order, read back from
`/api/runtime/events` after the turn::

    task_classified          Task classified as unknown.
    model_lane_selected      Selected ollama-local:qwen2.5:7b for the daily lane.
    model.call_completed     Model call completed with ollama-local:qwen2.5:7b.
    web_retrieval_started    Started an authorized web retrieval.
    web_retrieval_completed  Web retrieval: Keyless search (google_news_rss) - 3 sources
    task_completed           Completed task with final response: Sure! Here are ...
    runtime_attempt_completed  -> SUCCEEDED.

The model answered BEFORE retrieval ran, so three real, dated Google News rows
never reached synthesis. What shipped was four invented headlines -- "Rust 1.64
Released with Improved Performance and Safety Features", a 2022 version -- with
no date, no URL, and `lifecycle=succeeded` on a provider receipt behind it. The
Activity rail truthfully said `Keyless search (google_news_rss) - 3 sources`,
which is what made the fabrication look sourced.

TWO ROOT CAUSES, both present unchanged at base 840a2392
--------------------------------------------------------
1. `requirements_for(...).current_information_required` reads **False** for
   "show me recent news coverage about the Rust programming language" and
   **True** for "what is the latest news on the Rust programming language".
   The same demand, two phrasings, and the unsourced-current-claim guard in
   `core.agent_runtime.turn_reasoning` silently off for one of them.

2. `core.evidence_binding.inspect_evidence_binding` -- the one predicate that
   asks whether the answer carries what retrieval returned -- grounded on a
   SINGLE shared introduced term. The fabricated headline and the real one both
   contain "Released", so it returned `grounded=True` for an answer that shared
   nothing else. FIXED on this branch: support is now judged per claim against
   bound source content (`core.claim_support`), one generic shared word never
   suffices, and `grounded` requires every claim covered. The assertions below
   hold the repaired verdict in place; `tests/test_claim_to_source_support.py`
   carries the full matrix.
"""
from __future__ import annotations

from core.evidence_binding import compose_grounded_report, inspect_evidence_binding
from core.execution_requirements import requirements_for

MISSED = "show me recent news coverage about the Rust programming language"
RECOGNIZED = "what is the latest news on the Rust programming language"

# Rows shaped like what `_news_rss_fallback` actually returned on the drive:
# dated, attributed, linked.
RETRIEVED = [
    {
        "summary": "Phoronix | 2026-08-31 | Rust Coreutils 0.11 Released With Debug Helper Messages",
        "result_title": "Rust Coreutils 0.11 Released",
        "result_url": "https://www.phoronix.com/news/rust-coreutils-0-11",
        "origin_domain": "phoronix.com",
        "search_provider": "google_news_rss",
        "source_type": "web_derived",
    },
    {
        "summary": "InfoWorld | 2026-08-28 | Rust language adds algebraic floating-point methods",
        "result_title": "Rust adds algebraic floating-point methods",
        "result_url": "https://www.infoworld.com/article/rust-algebraic-floats",
        "origin_domain": "infoworld.com",
        "search_provider": "google_news_rss",
        "source_type": "web_derived",
    },
]

# Verbatim from the measured turn.
FABRICATED = (
    "Sure! Here are some recent headlines:\n\n"
    '1. "Rust 1.64 Released with Improved Performance and Safety Features" - TechCrunch\n'
    '2. "Mozilla\'s Firefox Now Uses Rust for WebAssembly Runtime" - Hacker News\n'
    '3. "How Mozilla is Using Rust to Improve Firefox" - Ars Technica'
)


# Ordinary requests about the same subject that ask for no current fact. They hold
# the fix honest: closing the asymmetry must not arm the guard on everything.
NOT_CURRENT = (
    "write a haiku about rust",
    "explain how ownership works in Rust",
)


def test_the_current_information_recognizer_is_symmetric_on_the_same_demand():
    """ROOT CAUSE 1, pinned in its repaired form. Both phrasings of the one demand
    now arm the guard that protects current claims, and the phrasing that used to
    be missed says which signal caught it. The negative controls are asserted in
    the same test so the symmetry cannot be bought by widening the recognizer onto
    every request about the same subject."""
    missed = requirements_for(MISSED, source_context={"surface": "openclaw"})
    recognized = requirements_for(RECOGNIZED, source_context={"surface": "openclaw"})

    assert bool(recognized.current_information_required) is True, RECOGNIZED
    assert bool(missed.current_information_required) is True, MISSED
    assert any(
        code.startswith("current_info_signal:") for code in missed.reason_codes
    ), missed.reason_codes

    for text in NOT_CURRENT:
        control = requirements_for(text, source_context={"surface": "openclaw"})
        assert bool(control.current_information_required) is False, (text, control)


def test_one_incidental_shared_word_no_longer_reads_as_grounded():
    """ROOT CAUSE 2, now pinned in its repaired form. The fabricated headlines and
    the real ones still share exactly one introduced term -- "released" -- and
    that no longer satisfies the predicate: every claim in the reply lacks
    structural support, so the verdict is ungrounded and the evidence discarded."""
    binding = inspect_evidence_binding(
        answer=FABRICATED, notes=RETRIEVED, request_text=MISSED
    )

    assert binding.has_evidence is True, binding
    assert binding.grounded is False, binding
    assert binding.evidence_discarded is True, binding
    assert binding.shared_terms == ("released",), binding
    assert binding.claim_support is not None
    assert not binding.claim_support.supported_claims, binding.claim_support
    # Everything the retrieval actually introduced, that the answer never carried.
    shared = {term.lower() for term in binding.shared_terms}
    for term in ("coreutils", "algebraic", "phoronix", "infoworld"):
        assert term in binding.introduced_terms, binding
        assert term not in shared, binding


def test_the_evidence_the_turn_paid_for_is_renderable_and_was_not_rendered():
    """The replacement a fix would ship already exists and already works: the
    retrieved rows, dated, attributed and linked. Nothing had to be authored --
    the turn simply never used it."""
    report = compose_grounded_report(RETRIEVED)

    assert "Coreutils 0.11" in report, report
    assert "algebraic floating-point methods" in report, report
    assert "https://www.phoronix.com/news/rust-coreutils-0-11" in report, report
    assert "Rust 1.64" not in report, report


def test_an_answer_that_carries_none_of_the_retrieval_is_not_grounded():
    """WHAT THE FIX DELIVERS. This was `xfail(strict=True)` on the lane that found
    the defect; the marker is removed because the behaviour now holds."""
    binding = inspect_evidence_binding(
        answer=FABRICATED, notes=RETRIEVED, request_text=MISSED
    )

    assert binding.grounded is False, binding
