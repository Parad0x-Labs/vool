"""A question after a retraction cue is the live request; the withdrawn instruction is gone.

Measured 2026-08-15 (pipeline audit, executed probes): "Cancel the search immediately. What is
25+20?" produced NO narrowing -- the continuation gate was a closed alternation of IMPERATIVE
openers, and a question (the most common follow-up form) matched none of them, so the withdrawn
search stayed the live request for the planner, classifier, and research gate. The module's own
rule ("what follows the cue is the live request") already said otherwise.
"""

from __future__ import annotations

import pytest

from core.within_turn_retraction import live_request_after_retraction


@pytest.mark.parametrize(
    ("turn", "expected_live"),
    (
        ("Cancel the search immediately. What is 25+20?", "What is 25+20?"),
        ("Cancel the search. What's the capital of France?", "What's the capital of France?"),
        (
            "Cancel the search. How much disk space is left on this machine?",
            "How much disk space is left on this machine?",
        ),
        ("cancel that lookup. can you explain how dns works", "can you explain how dns works"),
        ("Cancel the download. which port does redis use", "which port does redis use"),
        ("forget that request. does python cache imports", "does python cache imports"),
    ),
)
def test_interrogative_continuations_narrow_to_the_question(turn: str, expected_live: str) -> None:
    assert live_request_after_retraction(turn) == expected_live


@pytest.mark.parametrize(
    "turn",
    (
        # No retraction at all: a question that merely contains cue-like vocabulary.
        "Who wrote Hamlet, and when?",
        "what should I do to cancel my gym membership",
        # Compound, not replacement: cancelling IS the request and the question rides along.
        "cancel the subscription and tell me why it failed",
    ),
)
def test_questions_without_a_real_retraction_stay_whole(turn: str) -> None:
    assert live_request_after_retraction(turn) is None


def test_imperative_continuations_still_narrow() -> None:
    # The prior channel is untouched.
    assert (
        live_request_after_retraction("Cancel the search. Instead, write a haiku about rain")
        is not None
    )
