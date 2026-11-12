"""The pet's bubble keeps an operator that is part of the ask: "Calculate 23 * 19", not "Calculate 23 19".

v0.8 Linux window run (2026-11-12, deb-window-2404/02-chat-answer.png): the chat list, the message
and the composer said "Calculate 23 * 19" while the pet's bubble said "Calculate 23 19". The bubble's
task title trims punctuation off every word, so a lone "*" became empty and was dropped. An
operator between two kept words now stays; a bullet at the start and a mark at the end still go,
and every earlier title stays the same.

Driven on the real page and the real companion fragment under the shared node DOM stub.
"""

from __future__ import annotations

import json

from tests.test_companion_presentation_p2 import run_presentation
from tests.test_pet_bubble_follows_conversation import DRIVER_TITLES, TITLE_CASES

OPERATOR_CASES = [
    ("Calculate 23 * 19", "Calculate 23 * 19"),
    ("calculate 23 * 19 please", "Calculate 23 * 19"),
    ("what's 5 + 3", "5 + 3"),
    ("solve x = 2 * y", "Solve x = 2 * y"),
    ("convert 100 / 4 to a fraction", "Convert 100 / 4 to fraction"),
    ("is 7 × 8 = 56?", "Is 7 × 8 = 56"),
]

STILL_DROPPED = [
    ("* buy milk", "Buy milk"),
    ("- call mom tonight", "Call mom tonight"),
    ("calculate 23 *", "Calculate 23"),
    ("*important* call the bank", "Important call bank"),
]


def _titles(asks: list[str]) -> list[str]:
    result = run_presentation(DRIVER_TITLES % json.dumps(asks))
    assert not result.get("error"), result
    return result["titles"]


def test_an_operator_between_two_words_stays_in_the_bubble_title() -> None:
    assert _titles([ask for ask, _ in OPERATOR_CASES]) == [want for _, want in OPERATOR_CASES]


def test_a_bullet_a_trailing_operator_and_emphasis_marks_still_go() -> None:
    assert _titles([ask for ask, _ in STILL_DROPPED]) == [want for _, want in STILL_DROPPED]


def test_the_earlier_titles_are_unchanged() -> None:
    assert _titles([ask for ask, _ in TITLE_CASES]) == [want for _, want in TITLE_CASES]
