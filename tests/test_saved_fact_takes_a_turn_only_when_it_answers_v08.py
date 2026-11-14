"""A saved fact takes a turn from the file search or the entity check only when it answers it.

Review of 88b3247e (everyday remember on the real routing): the clean loop and the entity check stood down whenever
one saved row held more than half of a question's terms:
  - with only "we booked the cabin at lake Varna ..., booking code KV-3381" saved, "what's the booking code for the
    boat?" held two of three terms, so the file search stood down and the model was shown the cabin's code;
  - "where is the boat booking letter?" was kept from the files by the saved boat booking.
All names, places and dates are invented.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.cross_chat_recall import saved_memory_answers
from core.clean_loop import route
from tests.test_everyday_remember_v08 import _say
from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home  # isolated_home is a fixture


def _seed(*facts: str) -> None:
    for n, fact in enumerate(facts):
        handled, reply = _say(f"keep in mind that {fact}", f"seed-{n}")
        assert handled and reply.startswith("Locked in"), (fact, reply)
    for chat in ("ask-new", "say-new"):
        _policy(chat)  # a new chat reads the owner's saved facts, as a served chat does


@pytest.mark.parametrize(
    ("saved", "asked"),
    [
        ("we booked the cabin at lake Varna for 6-9 march, booking code KV-3381", "what's the booking code for the boat?"),
        ("we booked the boat on lake Orrin for 4-7 june, booking code BR-5527", "where is the boat booking letter?"),
        ("my sister Ilse has her birthday on 14 february", "when is Ilse's dentist appointment?"),
        ("the bike lock code is 4471", "what's the gym locker code?"),
    ],
)
def test_a_fact_that_holds_part_of_the_question_does_not_answer_it(isolated_home, saved, asked):
    _seed(saved)
    assert not saved_memory_answers(asked, session_id="ask-new", source_context={})
    assert not route._saved_memory_answers(asked, "ask-new", {})


@pytest.mark.parametrize(
    ("saved", "asked"),
    [
        ("we booked the boat on lake Orrin for 4-7 june, booking code BR-5527", "what's the booking code for the boat?"),
        ("my neighbour Anselm waters the plants on fridays", "what day does Anselm water the plants?"),
        ("my team lead is Rosalind and the audit is on january 20", "who is my team lead?"),
        ("my car is a green Corvalla, plate KZ-4471", "whats the plate number on my car?"),
        ("the cabin dates changed, its now 13-16 march", "which dates are we at the cabin?"),
    ],
)
def test_a_fact_that_holds_the_whole_subject_answers_it(isolated_home, saved, asked):
    _seed(saved)
    assert saved_memory_answers(asked, session_id="ask-new", source_context={})
