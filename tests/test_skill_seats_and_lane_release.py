"""A matched tool skill's ``allowed-tools`` SEAT in the offer, and the lanes that used to
answer before the model stand down for asks they cannot serve.

Red on b744af81: ``allowed-tools`` only ever NARROWED the ranked fill (TOOL-RECHECK C4), so a
skill whose tools the ranking left out had nothing to call; and four deterministic lanes (the
date clock, the disk capacity read, the web-lookup plan, the URL fetch) answered skill-shaped
turns before any model round, so the skill never even rendered.
"""
from __future__ import annotations

import pytest

from core import capability_graph as cg
from core.capability_graph import _HARD_MAX_CANDIDATES
from core.execution.constants import machine_diagnostics_intent
from core.skill_tools import install_skill
from core.tool_offer_assembly import assemble_tool_offer, reset_skill_cache

_SKILL = """---
name: media-trimmer
description: Trim and square up video clips for posts
triggers: trim video clip square post
allowed-tools:
  - media.open
  - media.inspect
  - media.edit
  - media.undo
  - media.redo
  - media.export
---

# Media trimmer

Trim the clip, make it square, export under a new name.
"""

OWNER_LOCAL = {"_owner_local": True, "runtime_session_id": "s-skill", "turn_id": "t-skill"}
REMOTE = {"_owner_local": False, "runtime_session_id": "s-skill-r", "turn_id": "t-skill-r"}


@pytest.fixture()
def skill_installed(tmp_path, monkeypatch):
    (tmp_path / "plugins").mkdir()
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path))
    staged = tmp_path / "staged.md"
    staged.write_text(_SKILL, encoding="utf-8")
    result = install_skill(str(staged))
    assert result["status"] == "ok", result
    reset_skill_cache()
    cg.reset()
    yield tmp_path
    reset_skill_cache()
    cg.reset()


def test_a_matched_skills_allowed_tools_seat_on_round_zero(skill_installed):
    offer = assemble_tool_offer(
        user_text="trim my video clip and make it square for a post",
        task_class="media_editing",
        source_context=dict(OWNER_LOCAL),
    )
    intents = set(offer.intents)
    for wanted in ("media.open", "media.inspect", "media.edit", "media.undo", "media.redo", "media.export"):
        assert wanted in intents, f"{wanted} missing; offered {sorted(intents)}"
    assert len(offer.specs) <= _HARD_MAX_CANDIDATES
    assert "respond.direct" in intents and "capability.expand_family" in intents


def test_a_remote_turn_gets_no_skill_seats(skill_installed):
    offer = assemble_tool_offer(
        user_text="trim my video clip and make it square for a post",
        task_class="media_editing",
        source_context=dict(REMOTE),
    )
    intents = set(offer.intents)
    assert not intents & {"media.open", "media.inspect", "media.edit", "media.undo", "media.redo",
                          "media.export"}, sorted(intents)


def test_unmatched_words_seat_no_skill_tools(skill_installed):
    offer = assemble_tool_offer(
        user_text="what is the capital of France",
        task_class="chat_conversation",
        source_context=dict(OWNER_LOCAL),
    )
    intents = set(offer.intents)
    assert not intents & {"media.open", "media.inspect", "media.edit", "media.undo", "media.redo",
                          "media.export"}, sorted(intents)


def test_a_matched_tool_skill_releases_the_tool_lane(skill_installed, monkeypatch):
    from core.execution.planner import should_attempt_tool_intent

    monkeypatch.setenv("VOOL_KEYCHAIN_ALLOWED", "0")
    assert should_attempt_tool_intent(
        "trim my video clip and make it square for a post",
        task_class="chat_conversation",
        source_context=dict(OWNER_LOCAL),
    )


def test_the_lane_release_is_owner_local_only(skill_installed):
    from core.execution.planner import should_attempt_tool_intent

    assert not should_attempt_tool_intent(
        "trim my video clip and make it square for a post",
        task_class="chat_conversation",
        source_context=dict(REMOTE),
    )


def test_the_clock_no_longer_claims_a_day_planning_turn():
    from core.agent_runtime.fast_paths_utility import _ASKS_DATE_RE

    # The possessive before "day" makes it the sentence's object, not the clock's subject.
    assert not _ASKS_DATE_RE.search("organise my day today from my calendar, reminders and to-dos")
    assert not _ASKS_DATE_RE.search("plan my day today around the meetings")
    # Real date asks keep their arms.
    assert _ASKS_DATE_RE.search("what's the day today")
    assert _ASKS_DATE_RE.search("day today?")


def test_a_disk_analysis_ask_stands_down_from_the_capacity_read():
    # Totals-only asks keep the direct read.
    assert machine_diagnostics_intent("is my drive nearly full") == "machine.disk_usage"
    assert machine_diagnostics_intent("how much disk space do I have left") == "machine.disk_usage"
    # Analysis asks need the breakdown, not the totals.
    assert machine_diagnostics_intent("my disk is nearly full, where did the space go?") is None
    assert machine_diagnostics_intent("what is taking up all my storage") is None
    assert machine_diagnostics_intent("find the biggest files on my disk") is None


def test_a_local_code_ask_is_not_planned_onto_the_web():
    from core.execution.planner import _local_code_ask_beats_web_planning

    # R6 (round 2, OWNER OK sls 2026-10-27 22:16Z): the guard is owner-local only and needs
    # the own-project anchor. A missing context is remote (section 12), so every row now
    # carries its context; "why does my build break, look it up" has no own-project anchor
    # and belongs with the not-local rows.
    owner_local = {"_owner_local": True, "runtime_session_id": "s-code", "turn_id": "t-code"}
    remote = {"_owner_local": False, "runtime_session_id": "s-code-r", "turn_id": "t-code-r"}
    assert _local_code_ask_beats_web_planning(
        "a test in my project fails, find out why without changing code", owner_local
    )
    assert not _local_code_ask_beats_web_planning(
        "a test in my project fails, find out why without changing code", remote
    )
    assert not _local_code_ask_beats_web_planning("why does my build break, look it up", remote)
    assert not _local_code_ask_beats_web_planning("find out why the Eiffel Tower is famous", owner_local)
    assert not _local_code_ask_beats_web_planning("look up the latest docs for this API", remote)


def test_a_beyond_reading_url_ask_is_not_answered_by_the_raw_fetch():
    from core.agent_runtime.fast_paths_web import url_read_request

    # Raw reads keep the lane.
    assert url_read_request("read this: https://example.com/") == {"url": "https://example.com/"}
    assert url_read_request("what does https://example.com/ say") is not None
    # Beyond-reading asks stand down for the model.
    assert url_read_request("read this page and quote the parts that matter: https://example.com/") is None
    assert url_read_request("extract the key points from https://example.com/") is None
