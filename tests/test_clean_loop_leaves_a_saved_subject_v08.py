"""The clean loop does not take an ask only because a word of it names a file, when that word is what a saved fact is
about and the ask points at no file.

Measured on the real local model (qwen3:8b, 88b3247e, H2H memory set, fresh home, an owner home with "cabin
booking.md"): "if today is 1 march, how many days until the cabin trip starts?" was admitted because "cabin" names a
file; the loop read the file and its answer was withheld, and the follow-up "and how many nights are we staying?" stayed
in the loop and answered "182 days". All names, places and dates are invented.
"""
from __future__ import annotations

from core.clean_loop import route
from tests.test_everyday_remember_v08 import _say
from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home  # isolated_home is a fixture

OWNER = {"_owner_local": True}
FILES = {"cabin", "booking", "boat", "letter", "standup", "notes"}


def _decide(text: str) -> route.RouteDecision:
    return route.decide(text, source_context=OWNER, session_id="ask", resolve_brain=lambda: object(), env={},
                        file_words=lambda: FILES)


def test_a_saved_subject_is_not_a_file_search(isolated_home):
    assert _say("keep in mind that we booked the cabin at lake Varna for 13-16 march", "seed")[1].startswith("Locked in")
    _policy("ask")
    decision = _decide("if today is 1 march, how many days until the cabin trip starts?")
    assert not decision.admit and decision.reason == "saved_fact_subject", decision


def test_with_nothing_saved_the_file_word_still_admits(isolated_home):
    _policy("ask")
    assert _decide("if today is 1 march, how many days until the cabin trip starts?").admit


def test_an_ask_that_points_at_a_file_stays_a_file_ask(isolated_home):
    assert _say("keep in mind that we booked the boat on lake Orrin for 4-7 june", "seed")[1].startswith("Locked in")
    _policy("ask")
    assert route.points_at_files("where is the boat booking letter?")
    decision = _decide("where is the boat booking letter?")
    assert decision.reason not in {"saved_fact_subject", "saved_memory_answers"}, decision
