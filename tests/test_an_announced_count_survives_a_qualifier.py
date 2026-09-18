"""An answer that promises five items and delivers four is incomplete, however it words the promise.

Measured live on the served surface (scratch API server, `route=ordinary_plain_text_chat`,
`ollama-local:qwen2.5:7b`, `output_tokens=417`)::

    Here are the top 5 best-selling cars globally with their max speeds and engine sizes:

    1. Toyota Corolla: ~186 mph (300 km/h), 1. 8L to 2. 4L
    2. Volkswagen Golf: ~155 mph (250 km/h), 1. 0L to 2. 0L
    3. Honda Civic: ~130 mph (210 km/h), 1. 0L to 2. 4L
    4. Ford Focus: ~150 mph (240 km/h), 1.

Four cars out of five, the last one cut mid-value, shipped with `done: true` and
`done_reason: "stop"` -- reported to the user as a finished answer.

`fewer_items_than_announced` exists for exactly this and did not fire. `_ANNOUNCED_ITEMS_RE`
allowed only an optional bare determiner before the count::

    (?:here are|below are|there are|these are)\\s+(?:the\\s+)?(?P<count>\\d{1,2}|two|...)

so "here are the **top** 5" did not match, the announced count resolved to 0, and the comparison
that would have caught a short list never ran. The guard was correct; one missing qualifier
disabled it on the most common shape a ranked list takes.

Attribution, from a control run: the same prompt sent straight to `qwen2.5:7b` returns all five
cars, complete, with intact decimals (`1.8L to 2.0L`). The model is not what shortened this.
"""

from __future__ import annotations

import pytest

from core.incomplete_answer import _announced_item_count, inspect_answer_completeness

_LIVE_ANSWER = (
    "Here are the top 5 best-selling cars globally with their max speeds and engine sizes:\n\n"
    "1. Toyota Corolla: ~186 mph (300 km/h), 1. 8L to 2. 4L\n"
    "2. Volkswagen Golf: ~155 mph (250 km/h), 1. 0L to 2. 0L\n"
    "3. Honda Civic: ~130 mph (210 km/h), 1. 0L to 2. 4L\n"
    "4. Ford Focus: ~150 mph (240 km/h), 1."
)


def _reasons(text: str) -> tuple[str, ...]:
    return tuple(inspect_answer_completeness(text).reasons)


# ---------------------------------------------------------------------------------------------
# G1 -- the measured reproduction, verbatim
# ---------------------------------------------------------------------------------------------


def test_the_live_four_of_five_answer_is_incomplete() -> None:
    verdict = inspect_answer_completeness(_LIVE_ANSWER)

    assert verdict.incomplete
    assert "fewer_items_than_announced" in verdict.reasons


def test_the_live_answer_announces_five() -> None:
    assert _announced_item_count(_LIVE_ANSWER) == 5


# ---------------------------------------------------------------------------------------------
# CLEAN -- other qualifiers, other determiners, none of them "top 5 cars"
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("opening", "expected"),
    (
        ("Here are the best 4 ways to do it:", 4),
        ("Here are the main 3 differences:", 3),
        ("Here are the key 6 findings:", 6),
        ("Below are the following 5 steps:", 5),
        ("These are the other 7 options:", 7),
        ("Here are the remaining 8 items:", 8),
        ("Here are the most common 3 causes:", 3),
        ("Here are my top 4 picks:", 4),
        ("Below are our top three priorities:", 3),
        ("There are their top 5 concerns:", 5),
    ),
)
def test_a_qualified_announcement_still_states_its_count(opening: str, expected: int) -> None:
    assert _announced_item_count(opening + "\n1. one\n2. two") == expected, opening


@pytest.mark.parametrize(
    "opening",
    (
        "Here are the top 5 reasons:",
        "Here are the best 5 approaches:",
        "Below are my top 5 recommendations:",
    ),
)
def test_a_short_qualified_list_is_incomplete(opening: str) -> None:
    assert "fewer_items_than_announced" in _reasons(opening + "\n1. a\n2. b"), opening


# ---------------------------------------------------------------------------------------------
# NEGATIVE CONTROLS
# ---------------------------------------------------------------------------------------------


def test_a_complete_qualified_list_is_untouched() -> None:
    """The whole point: five promised, five delivered, nothing flagged."""

    full = "Here are the top 5 cars:\n1. a\n2. b\n3. c\n4. d\n5. e"

    assert not inspect_answer_completeness(full).incomplete


@pytest.mark.parametrize(
    "text",
    (
        "There are 12 months in a year.",
        "Here are the results.",
        "These are the numbers you asked about.",
        "There are two sides to the argument, and both have merit.",
    ),
)
def test_prose_that_merely_contains_a_number_is_not_an_announcement(text: str) -> None:
    assert not inspect_answer_completeness(text).incomplete, text


def test_the_unqualified_forms_still_work() -> None:
    """The pre-existing behaviour must survive the widening."""

    assert _announced_item_count("Here are three things:") == 3
    assert _announced_item_count("below are the 4 options:") == 4
    assert "fewer_items_than_announced" in _reasons("Here are three things:\n1. a\n2. b")


def test_the_other_reasons_still_fire() -> None:
    assert "empty_answer" in _reasons("")
    assert "enumeration_without_content" in _reasons("1.\n2.\n3.")
    assert "table_cut_mid_row" in _reasons("| A | B |\n|---|---|\n| 1 | 2 |\n| 3")


# ---------------------------------------------------------------------------------------------
# ADVERSARIAL
# ---------------------------------------------------------------------------------------------


def test_a_qualifier_that_changes_what_is_counted_is_not_an_announcement() -> None:
    """`first` is deliberately NOT in the set.

    "Here are the first 5 of 20" announces a DELIBERATE subset, not the whole list, so flagging a
    five-item answer as short would be wrong. Pinned so the qualifier set is not widened by reflex
    into words that change the meaning rather than decorate the count.
    """

    assert _announced_item_count("Here are the first 5 of 20 results:") == 0


def test_the_qualifier_must_sit_between_the_determiner_and_the_count() -> None:
    """Not a free-floating keyword search: the shape is what qualifies it."""

    assert _announced_item_count("Here are the results, and the top scorer had 5 goals.") == 0
    assert _announced_item_count("The top 5 are listed in the appendix.") == 0


def test_a_severed_answer_reaches_the_fulfilment_translator() -> None:
    """End of the chain. The inspector alone changes nothing about what the user is told."""

    from core.runtime_task_outcome import output_validation_outcome

    verdict = inspect_answer_completeness(_LIVE_ANSWER)
    outcome = output_validation_outcome({"final_ui": {"answer_completeness": verdict.as_dict()}})

    assert outcome is not None
    assert outcome["fulfillment_status"] == "partially_fulfilled"
    assert outcome["retryable"] is True
