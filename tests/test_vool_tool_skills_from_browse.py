"""VOOL's own tool skills: listed in Browse, added with no key, and used whole on a turn with tools.

VOOL's skills repository writes its ``allowed-tools`` in VOOL's own tool names, so Browse lists
those skills (core.addon_discovery) where every other repository's tool list stays refused. Added
from Browse, one of VOOL's own skills keeps the untrusted label on its text, and its list narrows
what VOOL offers. The only tools it may add are the ones its own list names from the social list
(core.tool_offer_assembly: public X reads, VOOL's own notes, ratings and proposal status, web
reads, an owner-approved proposal), and only because it is the copy shipped in the app
(core.skill_origin.ORIGIN_VOOL_BROWSE). These tests take the real route a person takes: POST
/api/addons local_install and install with no key and no network, then the real tool-turn offer
for an ask the skill is meant for.

Measured 2026-11-12 before this change: 18 of the 19 tool skills were longer than a tool turn's
4,200 characters as a Browse block, so they could never be used once added; and on every security
turn the expert lens was charged to the task pool as well as its own, leaving an add-on 2,947.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core import addon_packages
from core import addon_store as store
from tests.test_vool_skills_install_without_a_key import _post, _runtime, home, no_network  # fixtures, used by name

_OWN_ROWS = [e for e in store.CATALOG if store.is_vool_bundled(e)]

# One ask each skill is made for, in a person's words, that VOOL's own built-in skill for the same
# job does not take first (where one does, the lane order gives it the turn; see the last test).
TOOL_TURNS = {
    "contacts-tidy": "my contacts have lots of duplicates, can you tidy them",
    "creative-series": "make a series of pictures with the same character",
    "csv-insights": "what does this csv say about our sales",
    "disk-space-advisor": "my disk is full, where did the space go",
    "downloads-tidy": "tidy my downloads folder",
    "email-to-calendar": "put the dates from this email in my calendar",
    "gentle-workout-plan": "I want a gentle workout plan for 15 minutes a day",
    "habit-builder": "I want to start a habit of reading every night",
    "inbox-triage": "sort my inbox, what needs a reply today",
    "meeting-follow-up": "write a follow-up email after the meeting with the print shop",
    "notes-digest": "summarise my notes from this week",
    "pdf-document-review": "explain this pdf contract in Downloads",
    "pdf-share-check": "before I share this pdf, check it for personal details",
    "press-pr-manager": "write a press release for our app launch",
    "phishing-email-check": "is this email phishing",
    "price-compare": "compare these product links, which is cheaper with shipping",
    "research-brief": "research brief on heat pumps with sources",
    "social-manager": "how did my X posts do this week",
    "wallet-explainer": "explain my wallet balance",
    "what-vool-knows": "what does VOOL know about me",
    "test-failure-helper": "assertion error in my tests, what is the cause",
    "repo-explainer": "explain what this repo does",
}


def _shipped(entry: dict):
    """The bundled SKILL.md as VOOL loads it once added from Browse (a discover- add-on)."""
    from core.plugin_skills import parse_skill_content

    pid = "discover-" + entry["id"]
    text = addon_packages.source(entry["sha256"]).decode("utf-8")
    path = Path("/nonexistent-profile/addons/plugins") / pid / "skills" / entry["skill_name"] / "SKILL.md"
    return parse_skill_content(text, path=path, plugin_id=pid)


def test_every_vool_tool_skill_is_in_browse():
    with_tools = {e["skill_name"] for e in _OWN_ROWS if _shipped(e).allowed_tools}
    assert with_tools == set(TOOL_TURNS), sorted(with_tools ^ set(TOOL_TURNS))


@pytest.mark.parametrize("entry", _OWN_ROWS, ids=lambda e: e["skill_name"])
def test_every_vool_browse_skill_fits_whole_on_the_turns_it_is_made_for(entry):
    """A Browse skill reaches a turn whole or not at all. One that names tools is used on turns
    with tools (MAX_TOOL_LANE_SKILL_CHARS for all guidance); any other on advisory turns."""
    from core.tool_offer_assembly import MAX_SKILL_CHARS, MAX_TOOL_LANE_SKILL_CHARS, render_complete_block

    skill = _shipped(entry)
    limit = MAX_TOOL_LANE_SKILL_CHARS if skill.allowed_tools else MAX_SKILL_CHARS
    block = render_complete_block(skill, limit)
    assert block, (entry["skill_name"], len(skill.body), limit)
    assert "(source: UNTRUSTED" in block, "a Browse add-on keeps the untrusted label"


@pytest.mark.parametrize("entry", [e for e in _OWN_ROWS if e["skill_name"] in TOOL_TURNS], ids=lambda e: e["skill_name"])
def test_a_vool_browse_tool_skill_names_only_real_tools_and_says_which(entry):
    from core.runtime_tool_contracts import runtime_tool_contract_map

    tools = _shipped(entry).allowed_tools
    assert tools and set(tools) <= set(runtime_tool_contract_map()), tools
    assert entry["requirements"] == (
        "Uses these VOOL tools on the turns it is picked: " + ", ".join(tools) + ". It grants nothing: "
        "each call goes through your usual approvals, and a tool that is off stays off."
    )


def _install(entry_id: str, home: Path, monkeypatch) -> tuple[str, Path]:
    from core import plugin_catalog
    from core import tool_offer_assembly as offers

    status, review = _post({"action": "local_install", "id": entry_id, "approved": True})
    assert status == 200 and review["report"]["status"] == "clean", review
    status, done = _post({"action": "install", "review_id": review["review_id"], "accepted": True})
    assert status == 200, done
    pid = done["plugin_id"]
    pack = home / "addons" / "plugins" / pid
    monkeypatch.setattr(plugin_catalog, "discovered_plugin_sources", lambda: ((pid, pack),))
    offers.reset_skill_cache()
    return pid, pack


def _offer(ask: str):
    from core.tool_offer_assembly import assemble_tool_offer

    return assemble_tool_offer(user_text=ask, source_context={"session_id": "browse-tool-turn"})


@pytest.mark.parametrize("name", sorted(TOOL_TURNS))
def test_a_vool_tool_skill_added_from_browse_is_used_whole_on_a_tool_turn(home, no_network, monkeypatch, name):
    from core.skill_origin import ORIGIN_VOOL_BROWSE, origin_of
    from core.tool_offer_assembly import (
        MAX_TOOL_LANE_DOCTRINE_CHARS,
        MAX_TOOL_LANE_SKILL_CHARS,
        _skill_may_seat,
        loaded_skills,
        render_complete_block,
    )

    ask = TOOL_TURNS[name]
    entry = next(e for e in _OWN_ROWS if e["skill_name"] == name)
    before = set(_offer(ask).intents)
    pid, _pack = _install(entry["id"], home, monkeypatch)
    skill = next(s for s in loaded_skills() if s.name == name)
    offer = _offer(ask)
    rows = [r for r in offer.skill_guidance.skills if r.get("name") == name]
    assert rows, f"not picked for {ask!r}: {offer.skill_guidance.skills}"
    row = rows[0]
    assert not row.get("reason") and row.get("complete", True), row
    block = render_complete_block(skill, MAX_TOOL_LANE_SKILL_CHARS)
    assert row["plugin_id"] == pid and row["chars"] == len(block) and block in offer.skill_guidance.text
    assert row["tools"] == "narrow_only"
    # Its tool list narrows the offer. What it adds is only what its own list names from the
    # social list VOOL lets its own Browse skills seat: never a private read, a write or a send.
    assert origin_of(skill) == ORIGIN_VOOL_BROWSE
    added = set(offer.intents) - before
    assert added <= set(skill.allowed_tools), sorted(added - set(skill.allowed_tools))
    assert all(_skill_may_seat(intent) for intent in added), sorted(i for i in added if not _skill_may_seat(i))
    assert len(offer.skill_guidance.text) <= MAX_TOOL_LANE_SKILL_CHARS + MAX_TOOL_LANE_DOCTRINE_CHARS


def test_an_expert_lens_no_longer_shrinks_the_room_for_a_browse_skill(home, no_network, monkeypatch):
    """The lens is paid from the doctrine pool only. Before, it was charged to the task pool too,
    and a Browse skill on a security turn had 4,200 minus the lens: phishing-email-check never fit."""
    from core.tool_offer_assembly import MAX_TOOL_LANE_DOCTRINE_CHARS, MAX_TOOL_LANE_SKILL_CHARS

    entry = next(e for e in _OWN_ROWS if e["skill_name"] == "phishing-email-check")
    _install(entry["id"], home, monkeypatch)
    offer = _offer("is this email phishing")
    rows = {r.get("name"): r for r in offer.skill_guidance.skills}
    lens, mine = rows["lens-security"], rows["phishing-email-check"]
    assert lens["lane"] == "doctrine" and lens["complete"] and lens["chars"] > 0
    assert not mine.get("reason") and mine["chars"] > 0, mine
    # It fits only because the lens is not charged to this pool a second time.
    assert mine["chars"] > MAX_TOOL_LANE_SKILL_CHARS - lens["chars"]
    task = sum(r["chars"] for r in offer.skill_guidance.skills if r.get("lane") != "doctrine" and not r.get("reason"))
    doctrine = sum(r["chars"] for r in offer.skill_guidance.skills if r.get("lane") == "doctrine" and r.get("complete"))
    assert task <= MAX_TOOL_LANE_SKILL_CHARS and doctrine <= MAX_TOOL_LANE_DOCTRINE_CHARS
    assert len(offer.skill_guidance.text) <= MAX_TOOL_LANE_SKILL_CHARS + MAX_TOOL_LANE_DOCTRINE_CHARS


def test_every_expert_lens_is_paid_from_the_doctrine_pool_and_nothing_else_is():
    """The task pool skips exactly the doctrine rows, so a row may say doctrine only when the doctrine
    pool paid for it: every expert lens (each vool-lens-* skill among them), and no task skill."""
    from core.native_skill_library import (
        KIND_EXPERT_LENS,
        LANE_DOCTRINE,
        LANE_TASK,
        guidance_for_selection,
        lane_for,
        load_native_library,
        native_skills_root,
    )

    contracts = load_native_library().contracts
    lens_dirs = {p.parent.name for p in native_skills_root().glob("vool-lens-*/SKILL.md")}
    lenses = [c for c in contracts if Path(str(c.path)).parent.name in lens_dirs]
    assert len(lenses) == len(lens_dirs) >= 6
    for contract in contracts:
        assert lane_for(contract) == (LANE_DOCTRINE if contract.kind == KIND_EXPERT_LENS else LANE_TASK), contract.id
    for lens in lenses:
        assert lens.kind == KIND_EXPERT_LENS and lane_for(lens) == LANE_DOCTRINE, lens.id
    task = next(c for c in contracts if lane_for(c) == LANE_TASK)
    _text, rows, _tools = guidance_for_selection([*lenses, task], task_chars=4200, doctrine_chars=1800)
    paid = {"doctrine": 0, "task": 0}
    for row in rows:
        paid[row["lane"]] += row["chars"] if row.get("complete") else 0
        assert row["lane"] == (LANE_DOCTRINE if row["name"] in {c.id for c in lenses} else LANE_TASK)
    assert paid["doctrine"] <= 1800 and paid["task"] <= 4200


def test_where_vool_has_its_own_skill_for_the_job_that_skill_takes_the_turn(home, no_network, monkeypatch):
    """Lane order stays: the person's own skills, then VOOL's built-in skills, then Browse skills
    with what is left. A developer asking about a failing test gets VOOL's root-cause skill; the
    Browse helper is recorded as left out, never cut short."""
    entry = next(e for e in _OWN_ROWS if e["skill_name"] == "test-failure-helper")
    _install(entry["id"], home, monkeypatch)
    rows = {r.get("name"): r for r in _offer("my unit test is failing, why").skill_guidance.skills}
    assert rows["root-cause-repair"]["complete"]
    assert rows["test-failure-helper"]["reason"] == "instruction_budget_exceeded"
    assert rows["test-failure-helper"]["chars"] == 0


def test_a_posting_plan_goes_to_vools_own_social_content_manager_first(home, no_network, monkeypatch):
    """VOOL's own social content manager plans posts. With social-manager added from Browse, a
    posting-plan ask still goes to VOOL's skill whole, and social-manager is recorded as left out,
    never cut short. Its own asks about the person's X posts reach it whole (TOOL_TURNS)."""
    entry = next(e for e in _OWN_ROWS if e["skill_name"] == "social-manager")
    _install(entry["id"], home, monkeypatch)
    rows = {r.get("name"): r for r in _offer("help me plan my social media posts for my tea shop").skill_guidance.skills}
    assert rows["vool-social-content-manager"]["complete"]
    assert rows["social-manager"]["reason"] == "instruction_budget_exceeded"
    assert rows["social-manager"]["chars"] == 0
