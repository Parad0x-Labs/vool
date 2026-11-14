"""The clean loop leaves to memory and the chat what the owner told VOOL.

Measured on the real local model (qwen3:8b, fresh home, a fake owner home whose files share the asks' words):
"booking a flight later, any seat preference i told you about?" was admitted because "seat" names a file; the
loop's model read the seat notes and answered "You mentioned preferring an aisle seat" while "i always prefer
window seats when i fly" was saved.
All names, places and dates are invented.
"""
from __future__ import annotations

import pytest

from core.clean_loop import route

OWNER = {"_owner_local": True}


def _decide(text: str, session: str, words: set[str]) -> route.RouteDecision:
    return route.decide(text, source_context=OWNER, session_id=session, resolve_brain=lambda: object(), env={},
                        file_words=lambda: words)


@pytest.mark.parametrize(
    "text",
    [
        "booking a flight later, any seat preference i told you about?",
        "did I ever mention where my sister lives?",
        "do you remember what my landlord is called?",
        "what was the gate code i gave you",
    ],
)
def test_an_ask_about_what_the_owner_told_vool_is_not_a_file_search(text):
    assert route.asks_what_owner_told(text)
    decision = _decide(text, "told-ask", {"seat", "sister", "landlord", "gate", "code", "preferences"})
    assert not decision.admit and decision.reason == "asks_what_owner_told", decision


@pytest.mark.parametrize(
    "text",
    [
        "did I save the seat preferences file?",
        "find the seat notes i told you about",
        "what's in the landlord letters document I mentioned?",
    ],
)
def test_a_told_ask_that_points_at_files_stays_a_file_ask(text):
    assert route.points_at_files(text)
    decision = _decide(text, "told-file-ask", {"seat", "landlord", "letters", "notes", "preferences"})
    assert decision.reason != "asks_what_owner_told", decision
