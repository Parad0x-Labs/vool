"""A Find pick stands on a yes/no of its own, asked with the chat's earlier message beside it.

iMac gate32 on 71cadd43f (qwen3:8b): a follow-up about the world that pointed back at the chat's river question was
picked Find; the files loop listed a folder and the reply was "Local directory ~/Desktop does not exist". The shared
yes/no reads a question about the person's own things as no and dropped real Lithuanian finds, so a Find pick has its
own question (iMac probe g32: 18 of 18 finds kept, 10 of 15 world questions dropped). No answer keeps the pick.
Wordings here are fresh; names are invented.
"""
from __future__ import annotations

import pytest

from core import intent_arbiter, plugin_ask


@pytest.fixture
def find_judge(monkeypatch):
    asked: list[tuple[str, str]] = []

    def judge(verdict):
        monkeypatch.setattr(intent_arbiter, "arbitrate_find_ask",
                            lambda text, previous="", **_k: asked.append((text, previous)) or verdict)
        monkeypatch.setattr(intent_arbiter, "arbitrate_choice_ask",
                            lambda *_a, **_k: pytest.fail("a Find pick was put to the shared yes/no"))
        monkeypatch.setattr(plugin_ask, "_previous_request", lambda text, context: "which sea is the saltiest?")
        return asked
    return judge


def test_a_find_pick_the_find_question_reads_no_is_dropped(find_judge) -> None:
    asked = find_judge("no")
    assert plugin_ask._actions_confirmed("and how deep does it get?", ("find",), {}, "chat-sea") == ()
    assert asked == [("and how deep does it get?", "which sea is the saltiest?")]


@pytest.mark.parametrize("verdict", ["yes", None])
def test_a_find_pick_stands_on_a_yes_or_on_no_answer(find_judge, verdict) -> None:
    find_judge(verdict)
    assert plugin_ask._actions_confirmed("kur padėjau savo draudimo polisą?", ("find",), {}, "chat-1") == ("find",)


def test_a_dropped_find_leaves_the_other_picks(find_judge) -> None:
    find_judge("no")
    assert plugin_ask._actions_confirmed("how deep is it?", ("find", "reminders"), {}, "chat-1") == ("reminders",)


def test_the_find_prompt_shows_the_earlier_message_only_when_there_is_one() -> None:
    with_one = intent_arbiter._find_ask_prompt("wie tief ist es?", "welches Meer ist am salzigsten?")
    assert 'Earlier: "welches Meer ist am salzigsten?"' in with_one
    lines = intent_arbiter._find_ask_prompt("find my gym membership pdf").split("\n")
    asked_at = lines.index('Message: "find my gym membership pdf" ->')
    assert lines[asked_at - 1] == ""
