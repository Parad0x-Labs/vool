"""A file find the turn's judge read and kept is not answered by the chat search.

iMac gate34 on 571e05675 (qwen3:8b): "can you locate the packing list for my ski trip somewhere on this mac?" was judged
a file find and kept by its own yes/no, but the chat housekeeping grammar read "locate ... somewhere" as a search of old
chats and answered "I couldn't find ... in any of your 1 chat" with no file tool run. Wordings here are fresh.
"""
from __future__ import annotations

import pytest

from core import plugin_ask
from core.agent_runtime import housekeeping_lane

ASK = "could you track down the tax receipt for my bike somewhere on this laptop?"


@pytest.fixture(autouse=True)
def _fresh_judgements(monkeypatch):
    monkeypatch.setattr(plugin_ask, "_BUILTINS", type(plugin_ask._BUILTINS)())


def test_the_grammar_alone_reads_it_as_a_chat_search() -> None:
    plan = housekeeping_lane.parse_housekeeping(ASK)
    assert plan is not None and [action.kind for action in plan.actions] == ["search"]


def test_a_kept_find_pick_takes_it_from_the_chat_search() -> None:
    plugin_ask._record_builtins(ASK, ("find",))
    assert housekeeping_lane._a_file_find_the_judge_kept(housekeeping_lane.parse_housekeeping(ASK), ASK)


def test_without_a_find_pick_it_stays_a_chat_search() -> None:
    plugin_ask._record_builtins(ASK, ())
    assert not housekeeping_lane._a_file_find_the_judge_kept(housekeeping_lane.parse_housekeeping(ASK), ASK)


def test_a_message_that_names_a_chat_stays_a_chat_search_whatever_the_judge_read() -> None:
    ask = "can you find the chat where we talked about the bike receipt?"
    plan = housekeeping_lane.parse_housekeeping(ask)
    assert plan is not None
    plugin_ask._record_builtins(ask, ("find",))
    assert not housekeeping_lane._a_file_find_the_judge_kept(plan, ask)


def test_the_lane_hands_a_kept_find_on(monkeypatch) -> None:
    plugin_ask._record_builtins(ASK, ("find",))
    ran: list[object] = []
    monkeypatch.setattr(housekeeping_lane, "execute_plan", lambda *a, **k: ran.append(a) or None)
    assert housekeeping_lane.maybe_handle_housekeeping_request(
        object(), raw_user_input=ASK, effective_input=ASK, session_id="chat-bike", source_context={}) is None
    assert ran == []
