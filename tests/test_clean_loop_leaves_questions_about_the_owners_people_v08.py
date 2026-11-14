"""The clean loop leaves a question about someone the owner told VOOL about to memory and the model.

Measured on the real local model (qwen3:8b, held-out set, fresh home, "birthday ideas.md" in the owner's Documents):
with "his daughter is called Marta and she turns 7 on 3 august" saved in a list about "my brother Tobiah", "when does
Marta have her birthday?" was admitted because "birthday" names a file and answered "Marta's birthday is not mentioned
in the file". All names, places and dates are invented.
"""
from __future__ import annotations

from core.clean_loop import route
from tests.test_everyday_remember_v08 import _say
from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home  # isolated_home is a fixture

OWNER = {"_owner_local": True}
FILES = {"birthday", "ideas", "photos"}


def _decide(text: str) -> route.RouteDecision:
    return route.decide(text, source_context=OWNER, session_id="ask", resolve_brain=lambda: object(), env={},
                        file_words=lambda: FILES)


def _seed() -> None:
    chat = "people"
    assert _say("ok a few things to remember for me. my brother Tobiah lives in Quillon", chat)[1].startswith("Locked in")
    assert _say("his daughter is called Marta and she turns 7 on 3 august", chat)[1].startswith("Locked in")
    _policy("ask")


def test_a_question_about_the_owners_person_is_not_a_file_search(isolated_home):
    _seed()
    decision = _decide("when does Marta have her birthday?")
    assert not decision.admit and decision.reason == "names_owners_person", decision


def test_an_ask_for_their_files_stays_a_file_ask(isolated_home):
    _seed()
    decision = _decide("find the birthday photos of Marta")
    assert decision.reason != "names_owners_person", decision


def test_a_name_nobody_saved_still_admits(isolated_home):
    _seed()
    assert _decide("when does Ottoline have her birthday?").admit
