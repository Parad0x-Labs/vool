"""Obligation classes of the evidence compiler: a semantic family per class (clean paraphrases, sloppy user-typed
variants, negative controls, a near-miss), so the classifier is checked on wording it was never written against."""
from __future__ import annotations

import pytest

from core.evidence_compiler import obligations

FAMILIES = {
    "assistant_output": [
        "Can you remind me what was the 7th job in the list you provided?",
        "What did you tell me the third step was?",
        "remind me the recipe u gave me for the lentil thing",
        "What was the product you recommended for my dry skin?",
        "you mentioned a book about habits earlier, which one was it",
        "what were the three objectives we outlined, the ones you listed",
    ],
    "interval_ago": [
        "How many days ago did I participate in the 5K charity run?",
        "how long ago did i move to the new flat",
        "How many weeks ago was my dentist appointment?",
        "how many months ago did i adopt the cat",
        "how long has it been since I started the new job?",
    ],
    "order": [
        "Which did I attend first, the lecture or the workshop?",
        "which device did i set up first the thermostat or the mesh network",
        "Did I buy the camera before or after the trip to Porto?",
        "What came first, the dentist visit or the race?",
        "which happened earlier, my move or my job change",
    ],
    "interval_between": [
        "How many days passed between the workshop and the day I planted the saplings?",
        "how long after the sign-up did I run the race",
        "How much time elapsed between my two trips?",
        "how many weeks between the first and second appointment",
        "How long did it take me to assemble the IKEA bookshelf?",
    ],
    "current_value": [
        "How much time do I dedicate to coding exercises each day?",
        "what is my current job",
        "Which city do I live in now?",
        "whats my coffee order these days",
        "How many plants do I have now?",
    ],
    "aggregate": [
        "How many charity events did I participate in before the run?",
        "What is the total I spent on camera gear?",
        "how much did i pay altogether for the three jackets",
        "What is the average price of the books I bought?",
        "how many doctor's appointments did I go to in March",
    ],
    "preference": [
        "I'm planning my meal prep next week, any suggestions for new recipes?",
        "any tips for getting around Tokyo",
        "Can you recommend a gift for my sister?",
        "what should i cook for the dinner party",
        "looking for a new podcast, ideas?",
    ],
}
NEGATIVE = [  # must not land in a dated-operand class
    ("What is the name of my dog?", {"single_fact", "current_value"}),
    ("Where did I grow up?", {"single_fact", "when", "current_value"}),
    ("What am I allergic to?", {"single_fact", "current_value"}),
]
NEAR_MISS = [
    ("What time did I say the meeting starts?", {"when", "single_fact", "assistant_output"}),  # a clock, not an interval
    ("How long is my commute to work?", {"current_value", "single_fact", "interval_between"}),   # a stated duration
]


@pytest.mark.parametrize("kind,question", [(k, q) for k, qs in FAMILIES.items() for q in qs])
def test_obligation_family(kind, question):
    assert obligations(question).kind == kind, question


@pytest.mark.parametrize("question,allowed", NEGATIVE + NEAR_MISS)
def test_obligation_controls(question, allowed):
    assert obligations(question).kind in allowed, (question, obligations(question).kind)
