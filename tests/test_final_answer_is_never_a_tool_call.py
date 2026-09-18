"""A tool call is a request the runtime should run, never an answer the user reads.

Live 0.5.0 evidence: on the ordinary chat lane the model emitted its tool call as prose and the
runtime committed it verbatim as the visible reply --

    turn 3   search({"query": "Vilnius weather forecast next week"})
    turn 6   searching weather for Vilnius next week...
    turn 11  search_web("Berlin 7-day weather forecast")

`claims_pending_tool` exists for this, but it is consulted only by the tool loop's synthesis
validator and by stepped audit, and it matches only first-person announcements ("I'll search").
It returns False for all three strings above.  So the guard was both out of scope and blind.

The invariant is lane-independent and topic-independent: no lane may commit an answer that IS a
call, or that promises retrieval and delivers none.  Nothing here may be repaired by naming a tool
or a subject -- the detector is shape-based, and the cases below deliberately roam off weather.
"""

from __future__ import annotations

import pytest

from core.agent_runtime.response import _validate_final_chat_output
from core.model_output_guard import answer_is_tool_invocation, claims_pending_tool

LIVE_LEAKS = (
    'search({"query": "Vilnius weather forecast next week"})',
    'search_web("Berlin 7-day weather forecast")',
    "searching weather for Vilnius next week...",
)


@pytest.mark.parametrize("leaked", LIVE_LEAKS)
def test_the_exact_live_leaks_are_rejected(leaked: str) -> None:
    assert answer_is_tool_invocation(leaked)


@pytest.mark.parametrize("leaked", LIVE_LEAKS)
def test_the_old_guard_could_not_see_them(leaked: str) -> None:
    """Pin WHY this repair exists, so nobody "simplifies" it back onto the old predicate."""

    assert not claims_pending_tool(leaked)


@pytest.mark.parametrize(
    "leaked",
    (
        # Dotted namespace, and a subject the reproduction never mentioned.
        'web.search("mitral valve prolapse prevalence")',
        # Keyword arguments rather than JSON.
        'lookup_flight(carrier="LO", number=281)',
        # A fenced call is still a call.
        '```\nget_quote(symbol="ORSTED")\n```',
        # Trailing punctuation, no arguments at all.
        "fetch_inventory().",
        # Progressive narration in an unrelated domain, no result delivered.
        "Retrieving the latest tide tables for Bergen",
        # Sloppy user-style casing and spacing.
        "  Looking  up   the shipping manifest  ",
    ),
)
def test_unseen_shapes_and_domains_are_rejected_too(leaked: str) -> None:
    assert answer_is_tool_invocation(leaked)


@pytest.mark.parametrize(
    "answer",
    (
        # Prose that MENTIONS a call is an answer about code, not a call.
        "Use search(query) to do that.",
        # A real code deliverable.
        "def add(a, b):\n    return a + b",
        # The terse imperative reading survives -- it answers, it does not announce.
        "Look up the manual.",
        # These open with a retrieval verb but state a result.
        "Checking the logs is the next step.",
        "Search results are below.",
        "Searching is a core concept in computer science.",
        # A real live-data answer, which is what the working lane returns.
        "Berlin: Sunny, 27 C (today's high 28 C / low 16 C). Source: wttr.in, observed 05:48 PM.",
        # Ordinary short answers.
        "green",
        "0",
        "The API means Application Programming Interface in software.",
    ),
)
def test_real_answers_are_never_mistaken_for_tool_calls(answer: str) -> None:
    assert not answer_is_tool_invocation(answer)


@pytest.mark.parametrize("leaked", LIVE_LEAKS)
def test_the_chat_lane_refuses_to_commit_a_tool_call(leaked: str) -> None:
    """The boundary, not just the predicate: this is where the visible reply is decided."""

    source_context: dict[str, object] = {}
    committed = _validate_final_chat_output(leaked, source_context=source_context)

    assert committed != leaked
    assert "search" not in committed.casefold()
    final_ui = dict(source_context["response_control"]["final_ui"])  # type: ignore[index]
    assert final_ui["tool_invocation_rejected"] is True
    assert final_ui["fallback_applied"] is True


#: A real answer and the turn that earned it. The weather line is the live lane's own composed
#: shape, so it travels with the receipt that lane really publishes -- this test is about a genuine
#: answer surviving the final seam untouched, and an observed turn is what makes it genuine. Given
#: an EMPTY context it asserted instead that a reading attributed to wttr.in ships from a turn that
#: fetched nothing, which is the fabrication shape `core.model_output_guard` exists to stop (see
#: `tests/test_live_claims_require_evidence.py::test_an_attributed_reading_is_not_evidence_of_its_own_source`).
#: "green" needs no evidence: it states no value and claims no nowness.
_REAL_ANSWERS = (
    (
        "Berlin: Sunny, 27 C (today's high 28 C / low 16 C). Source: wttr.in, observed 05:48 PM.",
        {"web_retrieval_receipts": [{"status": "available", "source_count": 1, "failure_class": ""}]},
    ),
    ("green", {}),
)


@pytest.mark.parametrize(("answer", "context"), _REAL_ANSWERS)
def test_a_real_answer_reaches_the_user_byte_for_byte(answer: str, context: dict) -> None:
    source_context: dict[str, object] = dict(context)
    committed = _validate_final_chat_output(answer, source_context=source_context)

    assert committed == answer
    final_ui = dict(source_context["response_control"]["final_ui"])  # type: ignore[index]
    assert final_ui["tool_invocation_rejected"] is False


def test_sabotage_removing_the_boundary_check_recommits_the_live_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prove the boundary call is load-bearing, not decoration."""

    import core.agent_runtime.response as response_module

    monkeypatch.setattr(response_module, "answer_is_tool_invocation", lambda _text: False)
    leaked = 'search_web("Berlin 7-day weather forecast")'

    assert _validate_final_chat_output(leaked, source_context={}) == leaked


def test_sabotage_narrowing_the_detector_to_first_person_recommits_the_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second half of the defect: the predicate itself was blind, not only mis-scoped."""

    import core.model_output_guard as guard

    monkeypatch.setattr(guard, "_TOOL_CALL_EXPRESSION_RE", guard.re.compile(r"(?!x)x"))
    assert not guard.answer_is_tool_invocation('search_web("Berlin 7-day weather forecast")')
