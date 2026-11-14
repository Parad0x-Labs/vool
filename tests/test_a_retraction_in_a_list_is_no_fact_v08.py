"""Inside a list the owner opened, taking something back or correcting it is never saved as a fact of the list.

Independent review of the job 19 commits (C2): once a list was open, any statement of three or more words was saved, so
"never mind, don't keep that one" was kept as a fact and answered "Locked in. I'll remember that.", and chatter that
is about nobody in the list ("the weather is awful today") was saved too. All names and figures are invented.
"""
from __future__ import annotations

import pytest

from core.memory import everyday_remember as everyday
from tests.test_everyday_remember_v08 import _facts, _say
from tests.test_memory_forget_resurrection_p0 import isolated_home  # isolated_home is a fixture

CHAT = "list-retraction"
OPENER = "a few things to remember for me: my locker is 214"


@pytest.mark.parametrize(
    "text",
    ["never mind, don't keep that one", "actually scratch the last one", "the second one was wrong",
     "forget that last one please"],
)
def test_a_retraction_in_an_open_list_is_not_locked_in(isolated_home, text):
    assert _say(OPENER, CHAT)[1].startswith("Locked in")
    _handled, reply = _say(text, CHAT)
    assert not reply.startswith("Locked in"), reply
    assert not any(text in fact for fact in _facts(CHAT)), _facts(CHAT)
    assert not everyday.list_statement(text, [OPENER])


def test_chatter_about_nobody_in_the_list_is_not_saved(isolated_home):
    assert _say(OPENER, CHAT)[1].startswith("Locked in")
    _handled, reply = _say("the weather is awful today", CHAT)
    assert not reply.startswith("Locked in"), reply
    assert "the weather is awful today" not in _facts(CHAT)


def test_a_lowercase_name_from_the_list_still_belongs_to_it():
    opener = "ok a few things to remember for me. my brother Tobiah lives in Quillon"
    assert everyday.list_statement("tobiah is vegetarian", [opener])
    assert not everyday.list_statement("tobiah is vegetarian", [])
