"""A skill match needs a REAL ask: description overlap alone never seats a tool, never
releases the tool lane, the operator's disable switch removes both, and a skill whose tools
this runtime does not have widens neither the seat budget nor the fill (R3, R4, R8).

The seats seam (``matched_skill_tool_seats``) and the lane release
(``core.execution.planner._matched_tool_skill_releases_the_lane``) agree through one law: a
skill really matches an ASK-SHAPED turn only when the turn speaks every content word of the
skill's name, or shares at least three content words with the skill's declared vocabulary
(``triggers`` + ``description``). One or two shared description words are topic talk, not a
match, and a statement that shares the topic seats nothing however many words it shares.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core import capability_graph as cg
from core.skill_tools import install_skill
from core.tool_offer_assembly import (
    assemble_tool_offer,
    matched_skill_tool_seats,
    reset_skill_cache,
)

_SKILL_NAME = "stage-whisper-caller"

_SKILL = """---
name: stage-whisper-caller
description: Rehearse video takes for a recital evening and prepare a mood board with scene cards
triggers: [rehearse, recital, teleprompter]
allowed-tools:
  - media.open
  - media.inspect
  - media.edit
  - media.undo
  - media.redo
  - media.export
---

# Stage whisper caller

Rehearse the take, cue the caller, export the clip.
"""

# install_skill validates allowed-tools against the runtime's real tool contracts, so a pack
# naming tools this runtime lacks can only reach the tree by being swapped on disk after a
# valid install -- the R8 shape of a pack whose tools went away after it was made.
_PHANTOM_INSTALLED = """---
name: phantom-budget-drill
description: Drill phantom budget rows into a weekly plan
triggers: [drill, phantom, budget, rows]
allowed-tools:
  - media.open
---

# Phantom budget drill

Drill the phantom rows, then export the plan.
"""

_PHANTOM_UNSEATABLE = """---
name: phantom-budget-drill
description: Drill phantom budget rows into a weekly plan
triggers: [drill, phantom, budget, rows]
allowed-tools:
  - no.such_one
  - no.such_two
  - no.such_three
---

# Phantom budget drill

Drill the phantom rows, then export the plan.
"""

OWNER_LOCAL = {"_owner_local": True, "runtime_session_id": "s-real-ask", "turn_id": "t-real-ask"}
REMOTE = {"_owner_local": False, "runtime_session_id": "s-real-ask-r", "turn_id": "t-real-ask-r"}


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


def test_description_overlap_alone_never_seats_or_releases(skill_installed, monkeypatch):
    """Law: one or two shared description words are topic talk, never a seat or a lane release."""
    from core.execution.planner import should_attempt_tool_intent

    text = "how do I build my bedroom mood board"
    assert matched_skill_tool_seats(text, source_context=dict(OWNER_LOCAL)) == ()
    monkeypatch.setenv("VOOL_KEYCHAIN_ALLOWED", "0")
    assert not should_attempt_tool_intent(
        text, task_class="chat_conversation", source_context=dict(OWNER_LOCAL)
    )


def test_three_overlapping_words_on_an_ask_still_seat(skill_installed):
    """Law: three or more shared description words on an ask-shaped turn ARE a real match."""
    seats = matched_skill_tool_seats(
        "can you prepare a mood board with scene cards for the evening?",
        source_context=dict(OWNER_LOCAL),
    )
    assert seats, "three shared description words on an ask must still seat the skill's tools"
    assert "media.open" in seats, seats


def test_the_skill_named_outright_still_seats(skill_installed):
    """Law: naming the skill outright is the strongest match there is, with no shared vocabulary."""
    seats = matched_skill_tool_seats(
        "please use the stage whisper caller skill on this",
        source_context=dict(OWNER_LOCAL),
    )
    assert seats, "a turn that speaks every name word must seat the skill's tools"


def test_statement_prose_with_topic_words_seats_nothing(skill_installed):
    """Law: a statement that merely narrates the topic seats nothing, however many words it shares."""
    assert matched_skill_tool_seats(
        "the weekly summary already went out, and it included a mood board and scene cards"
        " from the evening",
        source_context=dict(OWNER_LOCAL),
    ) == ()


def test_a_disabled_skill_seats_and_releases_nothing(skill_installed, tmp_path, monkeypatch):
    """Law: the operator's disable switch removes a real match's seats and its lane release."""
    from core.execution.planner import should_attempt_tool_intent
    from core.native_skill_library import set_skill_enabled

    # Isolate the ONE disable store (mirrors tests/test_native_skill_library._iso_home) so
    # active_vool_home() resolves inside this test's tmp and no real config is touched.
    monkeypatch.delenv("VOOL_MCP_CONFIG", raising=False)
    iso_home = tmp_path / "home"
    iso_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VOOL_HOME", str(iso_home))

    text = "please rehearse my recital takes with the teleprompter"
    assert matched_skill_tool_seats(text, source_context=dict(OWNER_LOCAL))

    off = set_skill_enabled(_SKILL_NAME, False)
    assert off["status"] == "ok", off
    assert matched_skill_tool_seats(text, source_context=dict(OWNER_LOCAL)) == ()
    monkeypatch.setenv("VOOL_KEYCHAIN_ALLOWED", "0")
    assert not should_attempt_tool_intent(
        text, task_class="chat_conversation", source_context=dict(OWNER_LOCAL)
    )

    on = set_skill_enabled(_SKILL_NAME, True)
    assert on["status"] == "ok", on
    assert matched_skill_tool_seats(text, source_context=dict(OWNER_LOCAL))


def test_the_seat_budget_counts_only_seatable_tools(tmp_path, monkeypatch):
    """Law: a matched skill whose tools this runtime lacks widens neither the budget nor the fill."""
    (tmp_path / "plugins").mkdir()
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path))
    reset_skill_cache()
    cg.reset()
    try:
        text = "please drill the phantom budget rows for me"
        before = assemble_tool_offer(
            user_text=text, task_class="chat_conversation", source_context=dict(OWNER_LOCAL)
        )

        staged = tmp_path / "staged-phantom.md"
        staged.write_text(_PHANTOM_INSTALLED, encoding="utf-8")
        result = install_skill(str(staged))
        assert result["status"] == "ok", result
        Path(result["path"]).write_text(_PHANTOM_UNSEATABLE, encoding="utf-8")
        reset_skill_cache()
        cg.reset()

        # The match is REAL (its trigger words are spoken), so an unchanged offer is the
        # budget counting only seatable tools, not a match that never happened.
        assert matched_skill_tool_seats(text, source_context=dict(OWNER_LOCAL))
        after = assemble_tool_offer(
            user_text=text, task_class="chat_conversation", source_context=dict(OWNER_LOCAL)
        )
        assert not [name for name in after.intents if name.startswith("no.such")]
        assert sorted(after.intents) == sorted(before.intents)
        assert len(after.specs) == len(before.specs)
    finally:
        reset_skill_cache()
        cg.reset()
