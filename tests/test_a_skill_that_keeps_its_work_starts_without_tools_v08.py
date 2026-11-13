"""A skill whose tools only keep its work and find it again starts its job without a tool round.

Skills A/B round 7 on the v0.8 head (Mac, real model, 2026-11-13): "I want a gentle workout plan for 15
minutes a day" matched gentle-workout-plan (tools: save, find and show a note). The turn went to the tool
lane with a tool call required, so the model called operator.list_tools, then a refused call, and only
then asked its health questions: 131 s and 143 s, loop on and off. The skill still guides that turn; its
tools are seated when the person asks for their notes or comes back to the plan. The wordings below are
new, except the measured one named.
"""
from __future__ import annotations

import pytest

from tests.test_vool_skills_install_without_a_key import _runtime, home, no_network  # fixtures, used by name
from tests.test_vool_tool_skills_from_browse import _OWN_ROWS, _install, _offer

OWNER = {"surface": "openclaw", "session_id": "skill-keeps-its-work", "_owner_local": True}


def _added(name, home, monkeypatch):
    entry = next(e for e in _OWN_ROWS if e["skill_name"] == name)
    _install(entry["id"], home, monkeypatch)


def _tool_turn(ask: str) -> bool:
    from core.execution.planner import should_attempt_tool_intent

    return should_attempt_tool_intent(ask, task_class="general", source_context=dict(OWNER))


@pytest.mark.parametrize(("name", "ask"), (
    ("gentle-workout-plan", "I want a gentle workout plan for 15 minutes a day"),   # measured
    ("gentle-workout-plan", "can you make me a beginner home workout routine"),
    ("habit-builder", "I want to start a habit of reading every night"),
))
def test_the_ask_that_starts_the_job_is_answered_directly(home, no_network, monkeypatch, name, ask):
    _added(name, home, monkeypatch)
    assert not _tool_turn(ask)
    rows = [r for r in _offer(ask).skill_guidance.skills if r.get("name") == name]
    assert rows and not rows[0].get("reason"), "the skill still guides the turn"


@pytest.mark.parametrize("ask", (
    "find my notes about the workout plan",
    "search my notes for my workout plan",
))
def test_asking_for_the_kept_notes_gets_the_tools(home, no_network, monkeypatch, ask):
    _added("gentle-workout-plan", home, monkeypatch)
    assert _tool_turn(ask)


def test_a_skill_whose_tools_do_the_job_keeps_the_tool_lane(home, no_network, monkeypatch):
    _added("disk-space-advisor", home, monkeypatch)
    assert _tool_turn("my disk is full, where did the space go")
