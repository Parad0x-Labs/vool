"""Machine and workspace questions must reach the lane that can answer them (re-audit N7, 2026-10-29).

Each case below was answered by a fast path that grabbed it on a word it shares with a different
question:

* "whats running on my computer right now?" -> the model-status card (runtime lane: "running on
  my computer" read as "where does VOOL run").
* "anything eating up my cpu right now?" ran the process list, then the final-output backstop
  replaced the measured table with "I didn't run any live lookup on this turn".
* "my laptop is slow, can you clear out my temp files?" -> the memory-hog report and "say free up
  memory".
* "what does the calendar script print ..." -> "no calendar provider is configured".
* "which project are you looking at right now?" -> a search of old chats.
* "... somewhere, anyway can you look up how long to boil eggs" -> a search of old chats.
* "what can you tell me about the cat_on_sofa picture" -> the short capability list.

Every fix narrows the trigger that over-matched, so each one is paired with the real ask its fast
path exists for, which must still be claimed.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from apps.vool_agent import VoolAgent
from core.agent_runtime import fast_paths_machine
from core.agent_runtime.housekeeping_lane import looks_like_search
from core.execution.constants import machine_live_load_intent
from core.operator.parser import parse_operator_action_intent
from core.runtime_lane_truth import runtime_lane_question

NO_LOOKUP = "didn't run any live lookup"


@pytest.fixture
def agent() -> VoolAgent:
    return VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")


def _frontdoor(agent: VoolAgent, text: str) -> dict:
    """The front door's claim for this text: {} when nothing claims it and the turn goes on."""
    outcome = agent._handle_turn_frontdoor(
        raw_user_input=text,
        effective_input=text,
        normalized_input=text,
        source_surface="api",
        session_id=f"n7-{abs(hash(text)) % 100_000}",
        source_context={"surface": "api", "_owner_local": True},
        persona=None,
        interpreted=SimpleNamespace(understanding_confidence=0.8),
    )
    return (outcome or {}).get("result") or {}


def _reason(result: dict) -> str:
    return str(result.get("route_reason") or "")


# ---------------------------------------------------------------------------------------------
# "what's running on my computer" is a process list, not the runtime lane
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "whats running on my computer right now?",
        "what's running on my mac?",
        "what apps are running on my laptop right now",
        "anything running on this machine at the moment?",
    ],
)
def test_a_question_about_what_runs_on_the_machine_is_not_a_lane_question(text: str) -> None:
    assert runtime_lane_question(text) == ""
    assert machine_live_load_intent(text) == "machine.list_processes"


@pytest.mark.parametrize(
    "text",
    [
        "are you running locally right now?",
        "is vool running on my computer?",
        "are you running on my machine or in the cloud?",
        "is this running in the cloud?",
    ],
)
def test_where_vool_itself_runs_is_still_the_lane_question(text: str) -> None:
    assert runtime_lane_question(text) == "lane"


def _fake_process_table(*_args, **_kwargs):
    return SimpleNamespace(
        ok=True,
        status="executed",
        response_text="Top processes by CPU right now:\nffmpeg (pid 4242)  87.5% CPU  412.0 MB",
        details={"observation": {"schema": "tool_observation_v1", "intent": "machine.list_processes",
                                 "tool_surface": "machine", "ok": True, "status": "executed"}},
    )


@pytest.mark.parametrize(
    "text",
    ["whats running on my computer right now?", "anything eating up my cpu right now?"],
)
def test_the_process_question_ships_the_measured_table(agent: VoolAgent, text: str) -> None:
    with mock.patch.object(fast_paths_machine, "execute_authorized_runtime_tool", side_effect=_fake_process_table):
        result = _frontdoor(agent, text)
    assert _reason(result) == "machine_read_fast_path"
    response = str(result.get("response") or "")
    assert "ffmpeg (pid 4242)" in response
    assert NO_LOOKUP not in response
    assert "Last model call" not in response


def test_the_machine_read_is_recorded_as_this_turns_observation() -> None:
    recorder = SimpleNamespace(
        _emit_runtime_event=lambda *a, **k: None,
        _fast_path_result=lambda **k: {"response": k.get("response", "")},
    )
    context: dict = {"session_id": "s1"}
    fast_paths_machine._machine_tool_fast_path_result(
        recorder, user_input="what's eating my cpu", session_id="s1", source_context=context,
        intent="machine.list_processes", execution=_fake_process_table(), reason="machine_read_fast_path",
    )
    observations = context.get("runtime_tool_observations") or []
    assert observations and observations[-1]["intent"] == "machine.list_processes"
    assert observations[-1]["ok"] is True

    failed = SimpleNamespace(ok=False, status="failed", response_text="", details={"observation": {"intent": "x"}})
    context_failed: dict = {}
    fast_paths_machine._machine_tool_fast_path_result(
        recorder, user_input="q", session_id="s1", source_context=context_failed,
        intent="machine.list_processes", execution=failed, reason="machine_read_fast_path",
    )
    # a failed read is recorded as a failure, which counts for nothing as evidence
    assert context_failed["runtime_tool_observations"][-1]["ok"] is False


# ---------------------------------------------------------------------------------------------
# temp-file cleanup vs. the memory governor
# ---------------------------------------------------------------------------------------------

_QUIET_REPORT = {
    "hog": None, "squeezed": False, "available_gb": 9.5, "total_gb": 16.0,
    "pressure": "normal", "swap_used_gb": 0.0, "resident_models": [], "comfyui_running": False,
}


@pytest.mark.parametrize(
    "text",
    [
        "my laptop is slow, can you clear out my temp files?",
        "clear out my temp files",
        "can you clean up the temporary files",
        "empty my temp files please",
    ],
)
def test_a_temp_file_cleanup_is_the_operator_cleanup_not_the_hog_report(agent: VoolAgent, text: str) -> None:
    intent = parse_operator_action_intent(text)
    assert intent is not None and intent.kind == "cleanup_temp_files"
    with mock.patch("core.resource_governor.resource_hog_report", return_value=dict(_QUIET_REPORT)):
        result = _frontdoor(agent, text)
    assert _reason(result) not in {"governor_hog_report", "governor_free_memory"}
    assert "free up memory" not in str(result.get("response") or "").lower()


def test_free_up_memory_still_frees_memory_and_keeps_its_numbers(agent: VoolAgent) -> None:
    freed = {"unloaded": [], "stopped_comfyui": False, "available_before_gb": 9.4, "available_after_gb": 9.5}
    with mock.patch("core.resource_governor.free_up_memory", return_value=freed):
        result = _frontdoor(agent, "free up memory")
    assert _reason(result) == "governor_free_memory"
    response = str(result.get("response") or "")
    assert "9.5 GB" in response and NO_LOOKUP not in response


def test_why_is_my_laptop_slow_still_gets_the_measured_hog_report(agent: VoolAgent) -> None:
    with mock.patch("core.resource_governor.resource_hog_report", return_value=dict(_QUIET_REPORT)):
        result = _frontdoor(agent, "why is my laptop so slow?")
    assert _reason(result) == "governor_hog_report"
    response = str(result.get("response") or "")
    assert "9.5 GB free" in response and NO_LOOKUP not in response


# ---------------------------------------------------------------------------------------------
# "the calendar script" is a file in the project
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "what does the calendar script print these days in garden planner?",
        "show me what calendar.py does",
        "check the calendar module for a bug",
    ],
)
def test_calendar_as_the_name_of_code_is_not_a_calendar_request(text: str) -> None:
    intent = parse_operator_action_intent(text)
    assert intent is None or "calendar" not in intent.kind and intent.kind not in {
        "check_availability", "show_agenda", "search_calendar_event"
    }


@pytest.mark.parametrize(
    "text,kind",
    [
        ("what's on my calendar today?", "show_agenda"),
        ("check my calendar for a free slot on tuesday", "check_availability"),
        ("cancel the dentist calendar event", "cancel_calendar_event"),
        ("what does my schedule look like this week, am i free thursday?", "check_availability"),
    ],
)
def test_real_calendar_requests_still_reach_the_calendar(text: str, kind: str) -> None:
    intent = parse_operator_action_intent(text)
    assert intent is not None and intent.kind == kind


# ---------------------------------------------------------------------------------------------
# chat search: current project and knowledge lookups are not searches of old chats
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "quick one - which project are you looking at right now?",
        "which project are you pointed at?",
        "i saved a recipe link somewhere, anyway can you look up how long youre meant to boil eggs",
        "look up how long to boil eggs",
    ],
)
def test_not_a_search_of_old_chats(text: str) -> None:
    assert looks_like_search(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "search my chats for the garden planner",
        "which chat did we talk about taxes in",
        "look up what we talked about the tax return",
        "can you look up the chat where we discussed taxes",
        "which project did we discuss the greenhouse in?",
    ],
)
def test_real_chat_searches_are_still_searches(text: str) -> None:
    assert looks_like_search(text) is True


def test_current_project_question_is_not_answered_by_chat_search(agent: VoolAgent) -> None:
    result = _frontdoor(agent, "quick one - which project are you looking at right now?")
    assert _reason(result) != "chat_housekeeping"


def test_egg_lookup_is_not_answered_by_chat_search(agent: VoolAgent) -> None:
    result = _frontdoor(
        agent, "i saved a recipe link somewhere, anyway can you look up how long youre meant to boil eggs"
    )
    assert _reason(result) != "chat_housekeeping"


def test_search_my_chats_still_runs_the_chat_search(agent: VoolAgent) -> None:
    result = _frontdoor(agent, "search my chats for the garden planner")
    assert _reason(result) == "chat_housekeeping"


# ---------------------------------------------------------------------------------------------
# a question about a picture is not a question about VOOL's capabilities
# ---------------------------------------------------------------------------------------------

def test_a_picture_question_does_not_get_the_capability_list(agent: VoolAgent) -> None:
    result = _frontdoor(agent, "what can you tell me about the cat_on_sofa picture in my Pictures folder?")
    assert _reason(result) != "smalltalk_fast_path"
    assert "The short version" not in str(result.get("response") or "")


@pytest.mark.parametrize("text", ["so what can you do", "what can we build"])
def test_what_can_you_do_still_gets_the_short_answer(agent: VoolAgent, text: str) -> None:
    from core.agent_runtime.fast_paths_utility import smalltalk_fast_path

    reply = smalltalk_fast_path(agent, text, source_surface="api", session_id="n7-smalltalk")
    assert reply and "short version" in reply.lower()
