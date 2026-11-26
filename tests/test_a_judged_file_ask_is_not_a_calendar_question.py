"""An ask the turn's judge read as something else of the person's is not answered "No calendar is connected".

iMac gate38: "what did they say at the school meeting? the notes are somewhere on my mac" was judged a file find, and
the word "meeting" took it to the no-calendar reply; no file tool ran. Wordings are fresh.
"""
from __future__ import annotations

import pytest

from core import plugin_ask


@pytest.fixture(autouse=True)
def _no_calendar(monkeypatch):
    plugin_ask.forget()
    monkeypatch.setattr("core.operator.calendar_agenda.selected_sources", lambda **_k: [])
    monkeypatch.delenv("VOOL_CALENDAR_PROVIDER", raising=False)
    yield
    plugin_ask.forget()


ASK = "what was decided at my book club meeting? I wrote it down in a file"


@pytest.mark.parametrize("picked", [("find",), ("read",), ("notes",), ("memory_recall",)])
def test_a_judged_ask_for_something_else_is_left_to_its_lane(picked):
    from core.agent_runtime.agent import _no_calendar_reply

    plugin_ask._record_builtins(ASK, picked)
    assert _no_calendar_reply(ASK) is None


@pytest.mark.parametrize("picked", [("calendar",), ("reminders",), ()])
def test_a_calendar_pick_or_no_pick_keeps_the_plain_truth(picked):
    from core.agent_runtime.agent import _no_calendar_reply

    asked = "do I have any meetings on friday?"
    if picked:
        plugin_ask._record_builtins(asked, picked)
    assert "No calendar is connected" in str(_no_calendar_reply(asked))
