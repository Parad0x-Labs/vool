"""A question is not an asserted fact: the receipt extractor types no value out of one (core/memory_receipts.py).

The packet read the user's own earlier question "Should I pay 290 euros for a folding kayak?" as
<amount: 290 euros>, and the served turn for "How much did I pay for the folding kayak?" carried 290 to
the reader as a typed fact, which the capsule's own assertion law withholds. The same family as the
quoted "ASSISTANT:" label and the "60 crowns" age reads: words the user did not assert are not the
user's facts. A declarative clause with a confirmation tag ("..., right?") asserts and keeps its value.
Every sentence below was written for this file.
"""
from __future__ import annotations

import pytest

from core.memory_receipts import extract_facts

STATED = 1738368000.0  # 2025-02-01

QUESTIONS = [
    # amounts
    "Should I pay 290 euros for a folding kayak?",
    "Is $1,450 a fair price for the walnut dresser?",
    "Would you spend 75 pounds on a kite like that?",
    # dates and relative days
    "Did the ferry really leave on 14 March 2024?",
    "Was the kiln delivered yesterday or the day before?",
    "Should I book the recital for 9 May?",
    # ages
    "Is my nephew 11 or 12 this year?",
    "Would a 34 year old still qualify for the rookie league?",
    # counts, measures, clock times, durations
    "Do I need 6 spare filters for the observatory cabinet?",
    "Is 27 km too far to cycle to the cannery?",
    "Can we meet at 7:30 pm instead?",
    "Did the hike take 3 hours or 4?",
    # sloppy, no question mark, interrogative head
    "should i pay 290 euros for a folding kayak",
    "did i say the padlock code was 7-3-1 or 731",
    "is 42 chairs enough for the recital hall",
]

ASSERTIONS = [
    ("I paid 290 euros for the folding kayak.", "amount", "290"),
    ("The walnut dresser cost $1,450.", "amount", "1450"),
    ("I paid 290 euros for the folding kayak, right?", "amount", "290"),      # tag question asserts
    ("My nephew is 12 years old.", "age", "12"),
    ("The observatory cabinet contains 16 filters.", "count", "16 filter"),
    ("The ride to the cannery is 27 km.", "measure", "27 km"),
    ("We meet at 7:30 pm.", "clock", "19:30"),
    ("The hike took 3 hours.", "duration", "180min"),
    ("What I paid for the kayak was 290 euros.", "amount", "290"),            # 'what' head, declarative
]


@pytest.mark.parametrize("sentence", QUESTIONS)
@pytest.mark.parametrize("role", ["user", "assistant"])
def test_a_question_yields_no_typed_fact(sentence: str, role: str) -> None:
    facts = extract_facts(sentence, STATED, role)
    assert facts == [], [(f.value_type, f.norm) for f in facts]


@pytest.mark.parametrize("sentence,value_type,norm", ASSERTIONS)
def test_an_assertion_still_yields_its_fact(sentence: str, value_type: str, norm: str) -> None:
    facts = extract_facts(sentence, STATED, "user")
    assert any(f.value_type == value_type and f.norm.startswith(norm) for f in facts), \
        [(f.value_type, f.norm) for f in facts]


def test_a_mixed_turn_keeps_only_its_asserted_sentence() -> None:
    body = "I paid 290 euros for the folding kayak. Should I also pay 60 euros for the paddle?"
    facts = extract_facts(body, STATED, "user")
    norms = {(f.value_type, f.norm) for f in facts}
    assert ("amount", "290") in norms, norms
    assert ("amount", "60") not in norms, norms
