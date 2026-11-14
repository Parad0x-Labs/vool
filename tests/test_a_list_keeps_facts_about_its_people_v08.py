"""Facts said inside a list the owner opened are kept without "I" or "my" in them.

Measured on the real local model (held-out set, fresh home): after "ok a few things to remember for me. my brother Tobiah
lives in Quillon", "his daughter is called Marta and she turns 7 on 3 august" and "oh and Tobiah is vegetarian" were
answered "Got it ... Nothing was saved: no tool stored it"; later "when does Marta have her birthday?" was searched in
the files and the bbq ask did not know Tobiah is vegetarian. All names, places and dates are invented.
"""
from __future__ import annotations

import pytest

from core.memory import everyday_remember as everyday
from tests.test_everyday_remember_v08 import _facts, _say
from tests.test_memory_forget_resurrection_p0 import isolated_home  # isolated_home is a fixture

CHAT = "people-list"


def test_third_person_facts_in_an_open_list_are_saved(isolated_home):
    assert _say("ok a few things to remember for me. my brother Tobiah lives in Quillon", CHAT)[1].startswith("Locked in")
    handled, reply = _say("his daughter is called Marta and she turns 7 on 3 august", CHAT)
    assert handled and reply.startswith("Locked in"), reply
    handled, reply = _say("oh and Tobiah is vegetarian", CHAT)
    assert handled and reply.startswith("Locked in"), reply
    facts = _facts(CHAT)
    assert "his daughter is called Marta and she turns 7 on 3 august" in facts
    assert "Tobiah is vegetarian" in facts


@pytest.mark.parametrize("text", ["ok that's all", "thanks!", "great, thanks", "that's it for now", "cool"])
def test_a_closing_in_an_open_list_is_no_fact(isolated_home, text):
    assert _say("couple of things to remember: my locker is 214", CHAT)[0]
    handled, reply = _say(text, CHAT)
    assert not reply.startswith("Locked in"), reply
    assert not everyday.list_statement(text)


def test_a_third_person_statement_outside_a_list_is_not_saved(isolated_home):
    handled, reply = _say("Tobiah is vegetarian", "no-list")
    assert not handled, reply
