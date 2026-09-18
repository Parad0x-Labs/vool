"""When the turn supplies the values, the internet cannot know the answer.

Measured on the served surface, 10:09::

    U: Variable X holds "Apple". Variable Y holds "Banana". Variable Z holds "Cherry".
       I swap the contents of X and Y. Then I swap the contents of Y and Z. Then I
       overwrite Z with "Grape". What is the current value of Y? Output exactly one word.

    Work log: web.search  query=Variable X holds "Apple". Variable Y holds "Banana"...

The runtime searched the WEB for the value of a variable the user had defined one sentence
earlier, using the puzzle itself as the query. No page knows what "Y" holds. It bought a search, a
`research` classification and a deep-lane summarisation for a question that needed none of them --
and the answer it eventually gave was wrong anyway (Banana; the correct value is Cherry).

The research gate trusted the classifier -- `task_class=research` produced `enabled=True,
reason=research_task` -- and nothing in between asked whether an external source COULD know.

The controls are the dangerous half. Refusing to look something up returns a stale or invented
answer where the user asked for a current one, so the detector fires only on an unmistakable
shape: at least two LITERAL bindings, and a question naming one of those very names.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

import core.curiosity_roamer as curiosity_roamer
from core.curiosity_roamer import _adaptive_research_decision
from core.self_contained_turn import stipulated_names, turn_answers_itself

REPORTED = (
    'Variable X holds "Apple". Variable Y holds "Banana". Variable Z holds "Cherry". '
    "I swap the contents of X and Y. Then I swap the contents of Y and Z. Then I overwrite Z "
    'with "Grape". What is the current value of Y? Output exactly one word.'
)


def _decide(text: str, *, task_class: str = "research") -> dict:
    # The web policy is pinned OPEN so these tests measure the gate under test rather than the
    # runtime's remote-fetch policy: with it closed every case returns disabled and the detector
    # would never be reached.
    with mock.patch.object(curiosity_roamer.policy_engine, "allow_web_fallback", return_value=True):
        return _adaptive_research_decision(
            user_input=text,
            classification={"task_class": task_class},
            interpretation=SimpleNamespace(topic_hints=["web"]),
            source_context={"surface": "api", "platform": "api"},
        )


# ---------------------------------------------------------------------------------------------
# The reported turn, at the gate that let it through
# ---------------------------------------------------------------------------------------------


def test_the_reported_puzzle_no_longer_authorises_a_search() -> None:
    decision = _decide(REPORTED)

    assert decision["enabled"] is False
    assert decision["reason"] == "self_contained_turn"


def test_the_classifier_no_longer_gets_the_last_word() -> None:
    """It was classified `research` and that alone opened the gate. A classification is a guess
    about the KIND of turn; whether an external source could know the answer is a separate
    question, and this one is answerable from the text."""

    for task_class in ("research", "chat_research", "unknown"):
        assert _decide(REPORTED, task_class=task_class)["enabled"] is False, task_class


# ---------------------------------------------------------------------------------------------
# The class -- other self-contained shapes, no shared wording with the report
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    (
        'bucket1 holds "red", bucket2 holds "blue". I swap them. What does bucket1 contain now?',
        "A = 5. B = 7. I add them then double the result. What is A?",
        'slot_one contains "hat". slot_two contains "coat". I move slot_one into slot_two. '
        "What is in slot_two?",
        "counter is set to 10. limit is set to 3. I decrement counter by limit twice. "
        "What is counter?",
    ),
)
def test_a_stipulated_world_is_derived_not_retrieved(text: str) -> None:
    assert turn_answers_itself(text) is True, text
    assert _decide(text)["enabled"] is False, text


# ---------------------------------------------------------------------------------------------
# Negative controls -- the expensive direction
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    (
        "look up the current sqlite release notes",
        "what is the current price of bitcoin",
        # Describes a thing without BINDING a value to it -- a category statement is not a premise.
        "the dollar is a currency. what is the current value of the dollar?",
    ),
)
def test_a_genuine_lookup_still_reaches_the_web(text: str) -> None:
    """Silently refusing to look something up returns a stale or invented answer where the user
    asked for a current one -- the worse failure, and the reason this detector is narrow."""

    assert turn_answers_itself(text) is False, text
    assert _decide(text)["enabled"] is True, text


@pytest.mark.parametrize(
    "text",
    (
        # The gate already declines these for its OWN reasons (static knowledge, no live-data
        # need). What this pins is narrower and matters more: MY detector must not be the thing
        # that declined them, because its verdict would then be wrong -- none of these stipulates
        # a world. Asserting gate_enabled=True here was measured wrong: these inputs were never
        # gate-enabled to begin with.
        "what is the capital of france",
        "a cat is an animal. what is a cat?",
        # A single binding is context for a real lookup, not a stipulated world.
        "my budget is 500. what is the going rate for a plumber?",
        # Bindings present, but the question is about something else entirely.
        'my api key is "abc123" and my region is "eu". what is the weather in Paris?',
    ),
)
def test_the_detector_never_claims_an_ordinary_turn(text: str) -> None:
    assert turn_answers_itself(text) is False, text
    assert _decide(text)["reason"] != "self_contained_turn", text


def test_two_bindings_are_required() -> None:
    """One binding is a passing mention; two or more is a premise set."""

    assert turn_answers_itself('X holds "Apple". What is X?') is False
    assert turn_answers_itself('X holds "Apple". Y holds "Pear". What is X?') is True


def test_the_question_must_name_a_bound_thing() -> None:
    """Bindings alone are not enough, or any turn carrying config would stop researching."""

    assert turn_answers_itself('X holds "Apple". Y holds "Pear". What is the capital of Peru?') is False


def test_generic_nouns_are_never_treated_as_stipulated_names() -> None:
    """"the price is 5" plus "what is the price of X today" must stay a live lookup."""

    assert "price" not in stipulated_names("the price is 5 and the total is 9")
    assert "value" not in stipulated_names("the value is 3 and the rate is 4")


def test_a_single_uppercase_letter_is_a_variable_not_an_article() -> None:
    """"a" is both the commonest article in English and a common variable name; case separates
    them. Without this, "A = 5. B = 7. What is A?" lost a binding and read as a lookup."""

    assert "a" in stipulated_names("A = 5. B = 7.")
    assert "a" not in stipulated_names('a cat is an animal. a dog is "friendly".')
