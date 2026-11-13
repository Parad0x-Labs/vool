"""A short follow-up carries the chat's last task (0.7.8 sweep blocks 1c/1e). Fresh wording only.
Contributor: sls_0x.
"""
from __future__ import annotations

import pytest

from tests.test_database_plain_ask_lane import world  # noqa: F401  (fixture)

from core.agent_runtime.task_continuation import compose_continuation

TRANSLATE = "translate lantern into Dutch and Polish"
SPLIT = "split a 90 euro dinner bill between 5 people"


@pytest.mark.parametrize("said,obj", [("orchard?", "orchard"), ("alright and saddle", "saddle"),
                                       ("try river too", "river"), ("how about ladder?", "ladder")])
def test_a_new_object_repeats_the_task(said, obj):
    assert compose_continuation(said, TRANSLATE) == f"Same as my previous request — “{TRANSLATE}” — but for: {obj}"


@pytest.mark.parametrize("said,change", [("my cousin dropped out, recalculate", "my cousin dropped out"),
                                          ("two more people are coming", "two more people are coming")])
def test_a_change_redoes_the_task(said, change):
    assert compose_continuation(said, SPLIT) == f"Redo my previous request — “{SPLIT}” — with this change: {change}"


@pytest.mark.parametrize("said", ["thanks!", "why?", "what does lantern mean exactly?", "can you explain the grammar"])
def test_ordinary_replies_are_not_continuations(said):
    assert compose_continuation(said, TRANSLATE) == ""


def test_no_task_before_means_no_continuation():
    assert compose_continuation("orchard?", "tell me about your day") == ""


def test_in_a_database_chat_a_short_follow_up_is_its_own_question(world):
    from core.agent_runtime.task_continuation import continuation_for_chat
    from core.database_work_state import _ASKED, note_database_demand

    previous = "list the neighbours, newest first"
    said = "who joined most recently"
    ctx = {"session_id": "s-tc-db", "runtime_session_id": "s-tc-db"}
    _ASKED.clear()
    assert continuation_for_chat(said, previous, ctx).startswith("Redo my previous request")  # no database chat yet
    note_database_demand(ctx)
    try:
        assert continuation_for_chat(said, previous, ctx) == ""
        assert continuation_for_chat(said, previous, {"session_id": "s-other"}).startswith("Redo my previous request")
    finally:
        _ASKED.clear()
