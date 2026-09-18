"""Step 9: the deterministic follow-up intent classifier -- every required phrasing example, plus
false-positive guards against a longer, unrelated message that happens to contain a trigger word.
"""

from __future__ import annotations

import pytest

from core.agent_runtime.attempt_followup import (
    CONTINUE_ATTEMPT,
    CORRECT_PREVIOUS_RESPONSE,
    EXPLAIN_ATTEMPT_FAILURE,
    LIST_ORIGINAL_ENTITIES,
    REPEAT_ORIGINAL_REQUEST,
    RETRY_ATTEMPT,
    classify_followup_intent,
    find_referenced_attempt_id,
)


@pytest.mark.parametrize("text", [
    "why?", "why did that fail?", "why did it fail", "what went wrong?", "explain that failure",
    "explain the failure", "why did that request fail?",
])
def test_explain_failure_phrasings(text: str) -> None:
    assert classify_followup_intent(text) == EXPLAIN_ATTEMPT_FAILURE, text


@pytest.mark.parametrize("text", [
    "retry", "try again", "retry the exact failed request", "retry the exact failed request now",
    "run that again", "run it again", "do it again",
])
def test_retry_phrasings(text: str) -> None:
    assert classify_followup_intent(text) == RETRY_ATTEMPT, text


@pytest.mark.parametrize("text", [
    "check the original message", "check the original message and answer it",
    "what did I ask?", "what did I ask for?", "answer my original question",
])
def test_original_request_phrasings(text: str) -> None:
    assert classify_followup_intent(text) == REPEAT_ORIGINAL_REQUEST, text


@pytest.mark.parametrize("text", [
    "which assets and cities did I ask for?", "which assets and cities did I originally ask for?",
    "what did I originally ask for?",
])
def test_list_entities_phrasings(text: str) -> None:
    assert classify_followup_intent(text) == LIST_ORIGINAL_ENTITIES, text


@pytest.mark.parametrize("text", [
    "that is not what I asked", "that's not what I asked", "you answered the wrong thing",
    "fix the previous answer",
])
def test_correction_phrasings(text: str) -> None:
    assert classify_followup_intent(text) == CORRECT_PREVIOUS_RESPONSE, text


@pytest.mark.parametrize("text", ["continue", "keep going", "go on", "pick up where you left off"])
def test_continue_phrasings(text: str) -> None:
    assert classify_followup_intent(text) == CONTINUE_ATTEMPT, text


@pytest.mark.parametrize("text", [
    "what is the capital of France",
    "how do I retry a failed HTTP request in Python using the requests library and exponential backoff and jitter for a production service",
    "why is the sky blue",
    "market prices for gold and bitcoin plus weather in Atlantisxyzabc123 and Kaunas",
    "tell me a joke",
    "",
    "   ",
])
def test_unrelated_or_empty_text_never_classifies(text: str) -> None:
    """The false-positive guard: a longer, genuinely unrelated message that happens to contain a
    trigger substring (e.g. "retry" inside a Python question) must not be misclassified -- the
    word-count guard in `classify_followup_intent` exists specifically for this."""
    assert classify_followup_intent(text) is None, text


def test_find_referenced_attempt_id_extracts_a_literal_id() -> None:
    text = "what happened to attempt-d0cdef8658aa4d8998df336cb9d72151"
    assert find_referenced_attempt_id(text) == "attempt-d0cdef8658aa4d8998df336cb9d72151"


def test_find_referenced_attempt_id_returns_empty_when_absent() -> None:
    assert find_referenced_attempt_id("why did that fail") == ""
