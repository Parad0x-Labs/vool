"""A turn a skill keeps stays in the normal lane, with the clean loop on or off.

The clean local loop (on by default in 0.8) answers asks about the owner's own files with its own
brain and file tools, and that brain never sees skills. Two kinds of turn now keep the normal lane,
where a skill's guidance reaches the turn whole (core.clean_loop.route.skill_keeps_turn):

- the ask picks a skill the person added (made by them, or added from Browse): measured
  2026-11-12 with the loop on, six of the 22 VOOL tool skills added from Browse were passed over
  for their own asks on the v0.8 candidate c9a7f426 (TAKEN below), and four still were on
  28fc78dd after the notes and "minutes" fixes (STILL_TAKEN);
- the person names a skill VOOL has, built-in or added: "... Use the project archaeology skill
  for the file history." was taken as a files ask (tests/project_archaeology/test_served_selection).

A built-in skill that only matches the ask's words does not count, so the loop keeps those file asks.
Nor does an added Browse skill whose tools the normal lane may not seat while the loop holds them
(the PDF, disk and CSV skills, LOOP_KEEPS): measured on the Mac A/B (11eef8dc, loop on),
pdf-document-review took "explain this pdf contract in Downloads" from the loop, the normal lane's
offer held no pdf tool, and the turn failed (tests/test_a_skill_keeps_a_split_turn_whole.py).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_vool_skills_install_without_a_key import _runtime, home, no_network  # fixtures, used by name
from tests.test_vool_tool_skills_from_browse import _OWN_ROWS, _install, _offer

OWNER = {"surface": "desktop", "_owner_local": True, "client_host": "127.0.0.1"}
REPO = Path(__file__).resolve().parents[1]

# The six asks the loop took from their own skills on c9a7f426, in a person's words (the same asks
# as the tool-turn test of the Browse skills).
TAKEN = {
    "csv-insights": "what does this csv say about our sales",
    "disk-space-advisor": "my disk is full, where did the space go",
    "gentle-workout-plan": "I want a gentle workout plan for 15 minutes a day",
    "notes-digest": "summarise my notes from this week",
    "pdf-document-review": "explain this pdf contract in Downloads",
    "test-failure-helper": "assertion error in my tests, what is the cause",
}
# The four the loop still takes without the skill on 28fc78dd (notes asks now go to the notes
# tools, and "minutes" is no longer a files topic): for these the skill rule is what keeps the turn.
STILL_TAKEN = ("csv-insights", "disk-space-advisor", "pdf-document-review", "test-failure-helper")
# Of those, the skills whose tools only the loop has here: with the loop on, it keeps their asks.
LOOP_KEEPS = ("csv-insights", "disk-space-advisor", "pdf-document-review")
# project-archaeology ships with VOOL; "which task changed this file" is one of its own triggers.
HISTORY_ASK = "which task changed this file"


def _decide(ask: str):
    from core.clean_loop.route import decide

    # A ready brain, so the route's own gates are what decide; the hook resolves the real one.
    return decide(ask, source_context=dict(OWNER), session_id="added-skill-turn", resolve_brain=lambda: object())


def _loop(monkeypatch, flag: str) -> None:
    if flag == "on":
        monkeypatch.delenv("VOOL_CLEAN_LOCAL_LOOP", raising=False)  # the 0.8 default
    else:
        monkeypatch.setenv("VOOL_CLEAN_LOCAL_LOOP", "0")


def _whole(offer, name: str) -> bool:
    rows = [r for r in offer.skill_guidance.skills if r.get("name") == name]
    return bool(rows) and not rows[0].get("reason") and rows[0].get("chars", 0) > 0


@pytest.mark.parametrize("flag", ["on", "off"])
@pytest.mark.parametrize("name", sorted(TAKEN))
def test_an_added_browse_skill_keeps_its_own_ask_in_the_normal_lane(home, no_network, monkeypatch, name, flag):
    _loop(monkeypatch, flag)
    ask = TAKEN[name]
    before = _decide(ask)
    if flag == "on" and name in STILL_TAKEN:
        assert before.admit, before.reason  # without the skill, the loop answers this ask itself
    entry = next(e for e in _OWN_ROWS if e["skill_name"] == name)
    _install(entry["id"], home, monkeypatch)
    after = _decide(ask)
    if flag == "on" and name in LOOP_KEEPS:
        # The loop has this skill's tools and the normal lane may not seat them: the loop keeps it.
        assert after.admit, after.reason
    else:
        assert not after.admit, after.reason
    if flag == "off":
        assert after.reason == "flag_off"
    elif before.admit and name not in LOOP_KEEPS:
        assert after.reason == "added_skill_turn"
    # In the normal lane the skill reaches the turn whole.
    assert _whole(_offer(ask), name)


def test_a_skill_the_person_made_keeps_the_turn_too(home, tmp_path, monkeypatch):
    from core import plugin_catalog
    from core import tool_offer_assembly as offers
    from core.skill_tools import install_skill

    _loop(monkeypatch, "on")
    ask = "summarise the invoices in my downloads folder"
    assert _decide(ask).admit
    folder = tmp_path / "draft" / "invoice-summary"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\nname: invoice-summary\n"
        "description: Summarises the invoices in the person's Downloads folder, with amounts, due dates "
        "and who to pay. Use when the user asks about their invoices.\n"
        "triggers: [invoice summary, summarise my invoices, summarise the invoices]\n---\n\n"
        "List each invoice with its sender, amount and due date, soonest first, then the total. "
        "Say which invoices could not be read.\n",
        encoding="utf-8",
    )
    assert install_skill(str(folder), plugin_id="local-skills", scope="global")["status"] == "ok"
    plugin_catalog.reset_storage_state()
    offers.reset_skill_cache()
    after = _decide(ask)
    assert not after.admit and after.reason == "added_skill_turn", after.reason
    named = _decide("use my invoice-summary skill on the pdf files in my downloads folder")
    assert not named.admit and named.reason == "named_skill_turn", named.reason


def test_vool_own_bundled_skill_never_keeps_the_turn(home, monkeypatch):
    """The database pack ships with VOOL: it is picked for this ask, and the loop still takes it."""
    from core import plugin_catalog, plugin_skills
    from core import tool_offer_assembly as offers
    from core.skill_origin import ORIGIN_BUNDLED, origin_of

    # The suite runs without the repo's bundled packs (tests/conftest.py); this test needs them.
    monkeypatch.setenv("VOOL_BUNDLED_PLUGINS_DIR", str(REPO / "plugins"))
    plugin_catalog.reset_storage_state()
    offers.reset_skill_cache()
    _loop(monkeypatch, "on")
    ask = "what is the schema of the sqlite database file in my documents"
    picked = plugin_skills.rank_skills(offers.loaded_skills(), ask, limit=1)
    assert [s.name for s in picked] == ["vool-database"] and origin_of(picked[0]) == ORIGIN_BUNDLED
    assert offers.added_skill_for(ask, source_context=dict(OWNER)) == ""
    assert _decide(ask).admit


def test_an_added_skill_for_another_job_leaves_the_loop_its_ask(home, no_network, monkeypatch):
    _loop(monkeypatch, "on")
    entry = next(e for e in _OWN_ROWS if e["skill_name"] == "csv-insights")
    _install(entry["id"], home, monkeypatch)
    decision = _decide(TAKEN["disk-space-advisor"])
    assert decision.admit, decision.reason


def test_a_turned_off_skill_does_not_keep_the_turn(home, no_network, monkeypatch):
    from core import native_skill_library

    _loop(monkeypatch, "on")
    entry = next(e for e in _OWN_ROWS if e["skill_name"] == "csv-insights")
    _install(entry["id"], home, monkeypatch)
    monkeypatch.setattr(native_skill_library, "disabled_skill_ids", lambda: frozenset({"csv-insights"}))
    decision = _decide(TAKEN["csv-insights"])
    assert decision.admit, decision.reason
    named = _decide("use the csv insights skill: " + TAKEN["csv-insights"])
    assert named.admit, named.reason


@pytest.mark.parametrize("flag", ["on", "off"])
def test_a_built_in_skill_the_person_names_keeps_the_turn(home, monkeypatch, flag):
    """The served archaeology turn, routed: named, the built-in skill keeps the normal lane."""
    _loop(monkeypatch, flag)
    ask = (
        "A unit test in my project fails after my change. Diagnose the root cause before proposing "
        "any fix. Use the project archaeology skill for the file history."
    )
    decision = _decide(ask)
    assert not decision.admit
    assert decision.reason == ("named_skill_turn" if flag == "on" else "flag_off"), decision.reason


@pytest.mark.parametrize(
    "ask",
    [
        "use the project archaeology skill: " + HISTORY_ASK,
        HISTORY_ASK + "? Use the vool-project-archaeology skill",
        "use the skill called project archaeology to find " + HISTORY_ASK,
        "with the `project-archaeology` skill, " + HISTORY_ASK,
        "Use the Project Archaeology skill and tell me " + HISTORY_ASK,
    ],
)
def test_every_way_of_naming_a_skill_keeps_the_turn(home, monkeypatch, ask):
    _loop(monkeypatch, "on")
    decision = _decide(ask)
    assert not decision.admit and decision.reason == "named_skill_turn", decision.reason


@pytest.mark.parametrize(
    "ask",
    [
        HISTORY_ASK,  # the built-in skill only matches the words
        "my database skills are rusty, " + HISTORY_ASK,  # "skills" is about the person
        "use the teleport-to-mars skill: " + HISTORY_ASK,  # VOOL has no such skill
        "is there a skill for this? " + HISTORY_ASK,
    ],
)
def test_a_skill_not_named_leaves_the_loop_its_file_ask(home, monkeypatch, ask):
    _loop(monkeypatch, "on")
    decision = _decide(ask)
    assert decision.admit, decision.reason


def test_a_named_skill_that_is_turned_off_does_not_keep_the_turn(home, monkeypatch):
    from core import native_skill_library

    _loop(monkeypatch, "on")
    monkeypatch.setattr(native_skill_library, "disabled_skill_ids", lambda: frozenset({"project-archaeology"}))
    decision = _decide("use the project archaeology skill: " + HISTORY_ASK)
    assert decision.admit, decision.reason


def test_a_renamed_built_in_skill_is_known_by_its_new_name(home, tmp_path, monkeypatch):
    """The built-in names are cached, and a changed SKILL.md is read again."""
    import os
    import shutil
    import time

    from core.tool_offer_assembly import named_skill_for

    library = tmp_path / "library"
    shutil.copytree(REPO / "skills" / "vool-release-gate", library / "vool-release-gate")
    monkeypatch.setenv("VOOL_NATIVE_SKILLS_DIR", str(library))
    assert named_skill_for("use the release gate skill on my files") == "release-gate"
    assert named_skill_for("use the ship gate skill on my files") == ""
    skill_md = library / "vool-release-gate" / "SKILL.md"
    skill_md.write_text(skill_md.read_text(encoding="utf-8").replace("name: vool-release-gate", "name: vool-ship-gate", 1), encoding="utf-8")
    later = time.time_ns() + 5_000_000_000
    os.utime(skill_md, ns=(later, later))
    assert named_skill_for("use the ship gate skill on my files") == "release-gate"
