"""A short continuation is told which tool the chat's previous request ran.

Measured Nov 21 on the installed app with qwen3:8b: "what is the weather in Vilnius right now?" ran
the weather lookup; the follow-up "and tomorrow?" was sent to the calendar availability tool with
the bare text "and tomorrow?" as its argument, and asked for approval. A follow-up names nothing by
itself, so the model guessed. The tool loop now tells the model what the previous request ran.
"""
from __future__ import annotations

import pytest

from core import runtime_continuity as rc
from core.agent_runtime.research_tool_loop_facade import _continued_tool_note
from core.tool_demand_signals import continues_previous_request


def _finished_turn(session: str, text: str, tool: str) -> str:
    row = rc.create_runtime_checkpoint(session_id=session, request_text=text)
    rc.update_runtime_checkpoint(row["checkpoint_id"], last_tool_name=tool, status="running")
    rc.finalize_runtime_checkpoint(row["checkpoint_id"], status="completed", final_response="done")
    return row["checkpoint_id"]


@pytest.mark.parametrize("text", ["and tomorrow?", "what about Kaunas", "and in Riga?", "same for Paris", "how about next week?",
                                  "now divide that by 8", "and the day after?"])
def test_continuation_shapes(text: str) -> None:
    assert continues_previous_request(text)


@pytest.mark.parametrize("text", ["ok thanks", "and what is on my calendar tomorrow?", "and am i free tomorrow?",
                                  "what's the weather in Oslo", "search the web for chess news",
                                  "please remind me tomorrow at 9 to call the dentist and also bring a cake to the office party"])
def test_not_a_continuation(text: str) -> None:
    assert not continues_previous_request(text)


def test_the_note_names_the_previous_tool_and_request() -> None:
    session = "openclaw:continuation-note-1"
    _finished_turn(session, "what is the weather in Vilnius right now?", "live_data.weather_lookup")
    current = rc.create_runtime_checkpoint(session_id=session, request_text="and tomorrow?")
    note = _continued_tool_note("and tomorrow?", session, current["checkpoint_id"])
    assert "live_data.weather_lookup" in note
    assert "what is the weather in Vilnius right now?" in note
    # The current turn's own (unfinished) row is never "the previous request".
    assert rc.previous_turn_tool(session, exclude_checkpoint_id=current["checkpoint_id"])["tool_name"] == "live_data.weather_lookup"


def test_no_note_without_a_previous_tool_or_for_a_new_request() -> None:
    session = "openclaw:continuation-note-2"
    assert _continued_tool_note("and tomorrow?", session) == ""
    _finished_turn(session, "hi", "respond.direct")
    assert _continued_tool_note("and tomorrow?", session) == ""
    _finished_turn(session, "Calculate 1234 * 56", "math.calculate")
    assert "math.calculate" in _continued_tool_note("now divide that by 8", session)
    assert _continued_tool_note("what's the weather in Oslo", session) == ""
    # Another chat's tool is never borrowed.
    assert _continued_tool_note("and tomorrow?", "openclaw:continuation-note-3") == ""
