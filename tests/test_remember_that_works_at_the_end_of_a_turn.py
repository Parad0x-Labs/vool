"""An explicit "Remember that." governs the sentences beside it, wherever it sits in the turn.

MEASURED 2026-08-18. Three turns seeded a 50-turn chat, each asking in the same way:

    "My name is Alex and I live in Berlin. Remember that."           -> stored
    "My project is called VOOL and my budget for it is 4200 euros. Remember that too."
                                                                         -> stored
    "My favourite colour is teal and my cat is called Mira. Remember these two things."
                                                                         -> NOTHING STORED

At turn 48 the runtime could recall the name, city, project and budget and said the colour and the
cat were "not specified in your recent memory". They had never been written.

TWO GATES, AND NEITHER READ THE INSTRUCTION. The explicit-capture lane
(`persistent_memory._MEMORY_CAPTURE_RE`) is `^`-anchored, so it only sees the directive when it
OPENS the turn. The auto-capture lane admits a sentence only if it matches one of four closed
vocabularies, of which `_FACT_PATTERNS` is eight literal phrases: `i use`, `i work on`,
`i'm building`, `my project`, `my setup`, `i maintain`, `i live in`, `i work in`.

So turn 1 survived on `i live in` and turn 2 on `my project`. They were kept by accident of the
phrase list, not because the user asked. Turn 3 asked identically and matched nothing.

WHY THE FIX IS NOT TWO MORE ROWS. Appending "my favourite colour" and "my cat" would leave the next
unlisted fact in exactly this position -- the same defect with a longer list. The user's explicit
instruction IS the admission; a heuristic built to guess at IMPLICIT facts must not be able to veto
an explicit one.
"""

from __future__ import annotations

import pytest

from core.memory.learning import extract_memory_candidates

THE_THREE_SEED_TURNS = (
    ("My name is Alex and I live in Berlin. Remember that.", "Berlin"),
    ("My project is called VOOL and my budget for it is 4200 euros. Remember that too.", "4200"),
    ("My favourite colour is teal and my cat is called Mira. Remember these two things.", "teal"),
)


@pytest.mark.parametrize("text,expected", THE_THREE_SEED_TURNS)
def test_each_seed_turn_is_captured(text: str, expected: str) -> None:
    captured = " | ".join(item["text"] for item in extract_memory_candidates(text))

    assert expected in captured


@pytest.mark.parametrize(
    "text",
    (
        "My cat is called Mira. Remember that.",
        "My dentist appointment is on Tuesday at 3pm. Please remember.",
        "The wifi code is hunter2. Note that.",
        "My sister's birthday is in March. Remember this.",
        "I take the 7:15 train. Store that.",
        "My allergy is shellfish. Keep in mind.",
        "My budget is 900 euros. Remember these two things.",
        "My flight is at 6am. remember that too",
    ),
)
def test_a_trailing_directive_admits_a_fact_no_phrase_list_would_have(text: str) -> None:
    assert extract_memory_candidates(text), f"nothing captured from an explicit instruction: {text!r}"


@pytest.mark.parametrize(
    "text",
    (
        "The weather is nice today.",
        "What is my cat called?",
        # A QUESTION about remembering is not an instruction to remember.
        "Do you remember that I mentioned a cat?",
        "Can you remember things between chats?",
        "I wonder if you remember my name.",
    ),
)
def test_a_turn_without_an_instruction_captures_nothing_new(text: str) -> None:
    assert extract_memory_candidates(text) == []


def test_the_directive_itself_is_not_stored_as_a_fact() -> None:
    """"Remember these two things." is an instruction, not something true about the user."""
    captured = [item["text"] for item in extract_memory_candidates(
        "My favourite colour is teal. Remember these two things."
    )]

    assert captured
    assert not any("remember" in text.lower() for text in captured)


def test_an_explicit_instruction_outranks_a_pattern_guess() -> None:
    """A fact the user asked for is held with more confidence than one a phrase list guessed at."""
    directed = extract_memory_candidates("My cat is called Mira. Remember that.")
    guessed = extract_memory_candidates("I work on the VOOL runtime every day.")

    assert directed and guessed
    assert directed[0]["confidence"] > guessed[0]["confidence"]


def test_sabotage_anchoring_the_directive_to_the_start_loses_the_third_turn(monkeypatch) -> None:
    """Revert to only seeing a directive that OPENS the turn, and the colour/cat turn is dropped
    again while the two that matched the phrase list survive -- which is exactly the split that was
    measured, and the reason it went unnoticed."""
    import core.memory.learning as learning

    monkeypatch.setattr(learning, "_turn_carries_an_explicit_remember_directive", lambda sentences: False)

    survived = [
        text for text, _ in THE_THREE_SEED_TURNS if learning.extract_memory_candidates(text)
    ]

    assert len(survived) == 2, "sabotage did not reproduce the measured split"
    assert not any("teal" in text for text in survived)
