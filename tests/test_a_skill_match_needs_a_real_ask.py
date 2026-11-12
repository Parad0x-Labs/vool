"""A skill match needs a REAL ask: description overlap alone never seats a tool, never
releases the tool lane, the operator's disable switch removes both, and a skill whose tools
this runtime does not have widens neither the seat budget nor the fill (R3, R4, R8).

The seats seam (``matched_skill_tool_seats``) and the lane release
(``core.execution.planner._matched_tool_skill_releases_the_lane``) agree through one law: a
skill really matches an ASK-SHAPED turn only when the turn speaks every content word of the
skill's name, or shares at least three content words with the skill's declared vocabulary
(``triggers`` + ``description``). One or two shared description words are topic talk, not a
match, and a statement that shares the topic seats nothing however many words it shares.

Seats are tools a skill ADDS, so the positive cases use a skill the person wrote in VOOL
(``skill.create`` then ``skill.install``, tests._toolchain_fixtures.person_writes_skill): only
such a skill may add tools (core.skill_origin, red team f109f118 #5). The same skill installed
from a file on disk is the negative case: a real match, and still no seat.
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
from tests._toolchain_fixtures import person_writes_skill

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


def _world(tmp_path, monkeypatch):
    (tmp_path / "plugins").mkdir()
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path))
    # The ONE disable store and the skill-origin record live in this test's own home.
    monkeypatch.delenv("VOOL_MCP_CONFIG", raising=False)
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))


@pytest.fixture()
def skill_installed(tmp_path, monkeypatch):
    """The skill as the person makes it in VOOL: the case where seats are allowed at all."""
    _world(tmp_path, monkeypatch)
    person_writes_skill(_SKILL, source_context=dict(OWNER_LOCAL))
    reset_skill_cache()
    cg.reset()
    yield tmp_path
    reset_skill_cache()
    cg.reset()


@pytest.fixture()
def skill_from_file(tmp_path, monkeypatch):
    """The SAME skill installed from a file on disk: not written in VOOL, so it only narrows."""
    _world(tmp_path, monkeypatch)
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


@pytest.mark.parametrize(
    "text",
    [
        "can you prepare a mood board with scene cards for the evening?",
        "please use the stage whisper caller skill on this",
    ],
)
def test_the_same_real_match_from_a_skill_installed_from_a_file_seats_nothing(skill_from_file, text):
    """Negative twin of the two laws above: the match is just as real, but the skill was not
    written in VOOL, so its allowed-tools never add a tool (they may only narrow)."""
    assert matched_skill_tool_seats(text, source_context=dict(OWNER_LOCAL)) == ()
    assert matched_skill_tool_seats(text, source_context=dict(OWNER_LOCAL), adding=False), (
        "the match itself must still be real: the narrowing lever sees it"
    )
    reset_skill_cache()
    cg.reset()
    offer = assemble_tool_offer(user_text=text, task_class="media_editing", source_context=dict(OWNER_LOCAL))
    rows = [row for row in offer.skill_guidance.skills if row.get("name") == _SKILL_NAME]
    assert all(row.get("tools") == "narrow_only" for row in rows), rows


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

    # The ONE disable store is isolated by the fixture (VOOL_HOME inside this test's tmp), so
    # no real config is touched.
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


def _phantom_world(tmp_path, monkeypatch):
    _world(tmp_path, monkeypatch)
    reset_skill_cache()
    cg.reset()
    text = "please drill the phantom budget rows for me"
    before = assemble_tool_offer(
        user_text=text, task_class="chat_conversation", source_context=dict(OWNER_LOCAL)
    )
    return text, before


def test_the_seat_budget_counts_only_seatable_tools(tmp_path, monkeypatch):
    """Law: a matched skill whose tools this runtime lacks widens neither the budget nor the fill.

    The person wrote the skill in VOOL (so it may add tools at all); its tools then went away.
    install validates allowed-tools against the runtime's real contracts, so that state is
    reached the way it happens: the skill the person wrote now names tools this runtime does
    not have. The digest of those bytes is what skill.create would have recorded for them.
    """
    from core.skill_origin import note_created_here

    text, before = _phantom_world(tmp_path, monkeypatch)
    try:
        installed = person_writes_skill(_PHANTOM_INSTALLED, source_context=dict(OWNER_LOCAL))
        path = Path(installed["path"])
        unseatable = path.read_text(encoding="utf-8").replace(
            'allowed-tools: ["media.open"]', 'allowed-tools: ["no.such_one", "no.such_two", "no.such_three"]'
        )
        assert "no.such_one" in unseatable, "the fixture must really swap the tool list"
        path.write_text(unseatable, encoding="utf-8")
        note_created_here(__import__("hashlib").sha256(unseatable.encode("utf-8")).hexdigest())
        reset_skill_cache()
        cg.reset()

        # The match is REAL and the skill MAY add tools, so an unchanged offer is the budget
        # counting only seatable tools, not a match that never happened.
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


def test_a_file_installed_skill_swapped_on_disk_seats_nothing_at_all(tmp_path, monkeypatch):
    """Negative twin: the old route (install from a file, then swap its bytes on disk) is a
    skill VOOL did not write, so its tool list adds nothing -- before or after the swap."""
    text, before = _phantom_world(tmp_path, monkeypatch)
    try:
        staged = tmp_path / "staged-phantom.md"
        staged.write_text(_PHANTOM_INSTALLED, encoding="utf-8")
        result = install_skill(str(staged))
        assert result["status"] == "ok", result
        reset_skill_cache()
        cg.reset()
        assert matched_skill_tool_seats(text, source_context=dict(OWNER_LOCAL)) == ()
        Path(result["path"]).write_text(_PHANTOM_UNSEATABLE, encoding="utf-8")
        reset_skill_cache()
        cg.reset()

        assert matched_skill_tool_seats(text, source_context=dict(OWNER_LOCAL)) == ()
        assert matched_skill_tool_seats(text, source_context=dict(OWNER_LOCAL), adding=False)
        after = assemble_tool_offer(
            user_text=text, task_class="chat_conversation", source_context=dict(OWNER_LOCAL)
        )
        assert set(after.intents) <= set(before.intents)
        assert not [name for name in after.intents if name.startswith("no.such")]
    finally:
        reset_skill_cache()
        cg.reset()
