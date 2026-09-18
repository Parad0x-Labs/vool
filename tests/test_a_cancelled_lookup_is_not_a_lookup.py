"""A turn that calls off a retrieval must not be claimed by the lane it called off.

THE MEASURED DEFECT (2026-08-18). `live_info_mode()` read the cancellation itself as a request,
because the cancelling clause carries the vocabulary of the thing it cancels:

    "ACTUALLY, cancel the live rate lookup."        -> "fresh_lookup"
    "WAIT. Abort the product lookup completely."    -> "fresh_lookup"
    "skip the web search"                           -> "fresh_lookup"
    "forget the weather lookup"                     -> "fresh_lookup"

and on the whole turn the opening request survived into the retrieval-eligible text, so

    "Search the web for local coffee shops. Abort lookup. Output ONLY `SEARCH_ABORTED`."

still searched. Three separate causes, each a vocabulary that had drifted narrower than the one
beside it:

1. `_NEGATED_RETRIEVAL_DIRECTIVE_RE` knew prohibitions ("do not search") but not cancellations,
   so an imperative "cancel the lookup" produced no span at all.
2. `_CANCEL_PRIOR_RETRIEVAL_RE` extended a span back over the cancelled request only when the cue
   TRAILED the verb ("look it up ... never mind"), and it searched `text[:start]` -- which cannot
   contain the cancelling clause when that clause is what opened the span.
3. `_CANCEL_PRIOR_RETRIEVAL_RE` carried its own seven-verb list that omitted `find`, so
   "Find me ... on the web. WAIT. Abort the product lookup." kept its opening clause.

WHAT IS ASSERTED. The classifier's own verdict on real turns -- no mocks, no injected answers. The
negative controls are the point: a request that merely CONTAINS a cancel-shaped word ("what to drop
from my packing list", "how to cancel a Ryanair booking") must still reach the lane, and an
instruction about how to search ("ignore case when searching") must not read as a withdrawal.
"""

from __future__ import annotations

import re

import pytest

from core.agent_runtime.fast_live_info_mode_classifier import live_info_mode
from core.retrieval_constraints import analyze_retrieval_constraints

CANCELLED = (
    "Search the web for the CEO of Apple. HOLD ON. Cancel the lookup. Do not access the "
    "internet. Take the word 'TIM', reverse the character order, and output ONLY that.",
    "Search the web for local coffee shops. Abort lookup. Output ONLY `SEARCH_ABORTED`.",
    "Find me the best mechanical keyboard under $100 on the web. WAIT. Abort the product "
    "lookup completely. Output ONLY the standard QWERTY top row.",
    "Look up the live exchange rate for USD to JPY. Use that live rate to convert $500. "
    "ACTUALLY, cancel the live rate lookup. Just assume the rate is 150 JPY. Convert $500 "
    "using the assumed rate. Output ONLY the final JPY amount as an integer.",
    "Check my system location for my current city. Search the web for local gyms in that "
    "city. HOLD ON. Cancel the web search.",
    "Search the internet for the highest grossing movie of 2023. HOLD ON. Abort the web "
    "search. Do not use the internet.",
)

STILL_LIVE = (
    "Search the web for the CEO of Apple.",
    "what is the weather in Vilnius, Berlin and Rome?",
    "Look up the official Sony A7RV weight and sensor megapixel count. Give me both values.",
    "get me the current bitcoin price",
    "what's the latest news on the ECB rate decision?",
    "find me the current gold price on the web",
    # The hard negatives: a cancel-shaped word inside an ordinary request.
    "search the web and tell me what to drop from my packing list",
    "look up how to cancel a Ryanair booking",
    "search the web for how to abort a stuck git rebase",
)


@pytest.mark.parametrize("text", CANCELLED)
def test_a_cancelled_retrieval_does_not_claim_the_live_info_lane(text: str) -> None:
    assert live_info_mode(None, text, interpretation=None) == ""


@pytest.mark.parametrize("text", STILL_LIVE)
def test_an_ordinary_live_request_still_claims_the_lane(text: str) -> None:
    assert live_info_mode(None, text, interpretation=None) != ""


@pytest.mark.parametrize(
    "clause",
    (
        "ACTUALLY, cancel the live rate lookup.",
        "Abort the product lookup completely.",
        "skip the web search",
        "forget the weather lookup",
        "cancel the live lookup",
    ),
)
def test_the_cancelling_clause_alone_is_a_prohibition(clause: str) -> None:
    """Per clause, because the arbitration in `answer_coverage` reads clauses one at a time."""
    assert analyze_retrieval_constraints(clause).has_prohibition is True
    assert live_info_mode(None, clause, interpretation=None) == ""


def test_an_instruction_about_how_to_search_is_not_a_withdrawal_of_it() -> None:
    assert analyze_retrieval_constraints("ignore case when searching").has_prohibition is False


def test_the_classifier_needs_no_agent_instance() -> None:
    """The single agent call was a passthrough to a module function, and it kept this family out
    of the probe registry -- which is what made a weather clause invisible to the arbitration."""
    assert live_info_mode(None, "what is the weather in Vilnius?", interpretation=None) == "weather"


# =================================================================================================
# Sabotage: revert each cause in turn and name the test that dies.
# =================================================================================================


def test_sabotage_dropping_the_imperative_cancel_form_restores_the_claim(monkeypatch) -> None:
    """Cause 1. Without the imperative cancellation, the cancelling clause claims the lane."""
    from core import retrieval_constraints as rc

    without = re.compile(
        rc._NEGATED_RETRIEVAL_DIRECTIVE_RE.pattern.replace(
            "|\\b" + rc._IMPERATIVE_CANCEL_RE, ""
        ),
        re.IGNORECASE,
    )
    assert without.pattern != rc._NEGATED_RETRIEVAL_DIRECTIVE_RE.pattern, "sabotage was a no-op"
    monkeypatch.setattr(rc, "_NEGATED_RETRIEVAL_DIRECTIVE_RE", without)
    try:
        assert live_info_mode(None, "ACTUALLY, cancel the live rate lookup.",
                             interpretation=None) == "fresh_lookup"
    finally:
        pass


def test_sabotage_narrowing_the_cancel_prior_window_restores_the_search(monkeypatch) -> None:
    """Cause 2. Searching only `text[:start]` cannot see the clause that opened the span, so the
    cancelled opening request survives into the eligible text and the turn searches anyway."""
    from core import retrieval_constraints as rc

    original = rc._negative_clause_spans

    def narrowed(text: str) -> tuple[tuple[int, int], ...]:
        spans: list[tuple[int, int]] = []
        cursor = 0
        while match := rc._NEGATED_RETRIEVAL_DIRECTIVE_RE.search(text, cursor):
            start = match.start()
            if not spans and rc._CANCEL_PRIOR_RETRIEVAL_RE.search(text[:start]):
                start = 0
            end = rc._negative_clause_end(text, match.start())
            if end <= start:
                end = match.end()
            spans.append((start, end))
            cursor = max(end, match.end())
        return tuple(spans)

    monkeypatch.setattr(rc, "_negative_clause_spans", narrowed)
    try:
        assert original is not narrowed
        assert live_info_mode(
            None,
            "Search the web for local coffee shops. Abort lookup. Output ONLY `SEARCH_ABORTED`.",
            interpretation=None,
        ) == "fresh_lookup"
    finally:
        pass


def test_sabotage_restoring_the_private_verb_list_restores_the_find_case(monkeypatch) -> None:
    """Cause 3. The seven-verb list omitted `find`, so a request opened with "Find me ..." kept
    its opening clause even after the abort."""
    from core import retrieval_constraints as rc

    private = re.compile(
        r"\b(?:look\s*up|lookup|search|browse|fetch|retrieve|check)\b"
        + rc._CANCEL_PRIOR_RETRIEVAL_RE.pattern.split(r"\b", 2)[-1],
        re.IGNORECASE,
    )
    monkeypatch.setattr(rc, "_CANCEL_PRIOR_RETRIEVAL_RE", private)
    try:
        assert live_info_mode(
            None,
            "Find me the best mechanical keyboard under $100 on the web. WAIT. Abort the "
            "product lookup completely. Output ONLY the standard QWERTY top row.",
            interpretation=None,
        ) == "fresh_lookup"
    finally:
        pass
