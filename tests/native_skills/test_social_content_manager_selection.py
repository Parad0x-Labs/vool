"""Selection law for the vool-social-content-manager native skill (P1, 2026-09-04).

Pins, against the ONE typed authority (``core.native_skill_library.select_native_skills``):

- the shipped contract is valid and carries the body law tokens;
- explicit social-content planning/managing/calendar/campaign/adaptation/measurement requests
  select it (semantic family: clean paraphrases + sloppy variants);
- bare mentions of X / "post" / "social" / "LinkedIn" / a quoted post do NOT select it;
- X-only drafting keeps x-editorial-studio as the task skill; the manager co-binds only when
  campaign/planning/measurement context is explicit;
- a publish-shaped request cannot cause the selection to grant a publishing tool;
- enable/disable/version truth survives restart (disk-backed store);
- guidance packing stays inside the shared prompt budget;
- selection is deterministic and home-scoped (no cross-home disable leakage).
"""
from __future__ import annotations

import pytest

from tests.native_skills.test_native_selection import library_world  # noqa: F401  (fixture re-export)

SKILL_ID = "vool-social-content-manager"

# Semantic family — clean paraphrases (same intent, different vocabulary/shape).
POSITIVE_CLEAN = (
    "Plan a launch campaign for our privacy tool across X and LinkedIn",
    "Help me manage our social content for the next month",
    "Build me a content calendar for the Bluesky and Mastodon launch",
    "Coordinate the posting plan across all our social channels",
    "Set up campaign tracking for our social media push",
    "Map out our publishing schedule across platforms",
)

# Semantic family — sloppy/user-typed variants.
POSITIVE_SLOPPY = (
    "make a social content campain plz",            # typo, fragment
    "content calendar??",                            # bare fragment noun
    "wanna run the launch campaing socials",         # typo, casual
    "organize posts plan for linkedin bluesky",      # no prepositions, lowercase
    "manage campaign content 4 next week",           # leetspeak digit, fragment
)

# Negative controls — must NOT select the manager.
NEGATIVE = (
    "What did he mean by posting that on X?",        # bare mention of X/post
    'Explain this quoted post: "budget it cannot talk its way past"',  # quoted post explanation
    "What is good marketing for developer tools?",   # generic marketing research, no content op
    "I saw something on social media about that",    # casual social mention
    "Write a poem about the sea",                    # unrelated writing
    "Post this to LinkedIn",                         # publish-only request
)

# Adversarial near-miss — reads adjacent, belongs to a different lane.
NEAR_MISS_EDITORIAL_ONLY = (
    "Draft a tweet about our launch",                # X-only drafting → editorial lane
    "Write a long post about debugging",
    "Edit my tweet to make it tighter",
)


def _select(user_text: str, task_class: str = ""):
    from core.native_skill_library import select_native_skills

    return select_native_skills(task_class=task_class, user_text=user_text)


def _contract(library_world):  # noqa: ANN001 (fixture)
    from core.native_skill_library import load_native_library

    contracts = {c.id: c for c in load_native_library().contracts}
    return contracts.get(SKILL_ID)


def test_contract_is_shipped_and_valid(library_world) -> None:
    contract = _contract(library_world)
    assert contract is not None, "the native skill must ship in skills/"
    assert contract.risk_class == "read_only"
    assert contract.tool_intents == (), "the manager must declare no tool seating"


def test_body_law_carries_the_no_authority_doctrine(library_world) -> None:
    """REQUIRED_BODY_TOKENS for this id must include the no-publishing doctrine, and the
    shipped body must contain it — an edit that strips the doctrine must fail the load."""
    from core.native_skill_library import REQUIRED_BODY_TOKENS, load_native_library

    tokens = REQUIRED_BODY_TOKENS.get(SKILL_ID, ())
    assert "never publishes" in tokens
    assert any("publishing authority" in t for t in tokens)
    contract = _contract(library_world)
    flattened = " ".join((contract.body or "").lower().split())
    for token in tokens:
        assert token.lower() in flattened


@pytest.mark.parametrize("text", POSITIVE_CLEAN + POSITIVE_SLOPPY)
def test_social_content_operations_select_the_manager(library_world, text: str) -> None:
    selected = [c.id for c in _select(text).selected]
    assert SKILL_ID in selected, f"expected manager selection for {text!r}, got {selected}"


@pytest.mark.parametrize("text", NEGATIVE)
def test_bare_mentions_and_publish_requests_do_not_select(library_world, text: str) -> None:
    selected = [c.id for c in _select(text).selected]
    assert SKILL_ID not in selected, f"manager must not fire for {text!r}"


@pytest.mark.parametrize("text", NEAR_MISS_EDITORIAL_ONLY)
def test_x_only_drafting_stays_in_the_editorial_lane(library_world, text: str) -> None:
    selected = [c.id for c in _select(text).selected]
    assert SKILL_ID not in selected
    if selected:  # if anything fires, it is the editorial studio, not the manager
        assert selected == ["x-editorial-studio"]


def test_manager_co_binds_only_with_campaign_context(library_world) -> None:
    """X drafting alone keeps editorial; adding explicit campaign planning co-binds both."""
    only_x = [c.id for c in _select("Draft the X thread announcing v2").selected]
    assert "x-editorial-studio" in only_x or only_x == []
    with_campaign = [c.id for c in _select(
        "Plan the launch campaign and draft the X thread announcing v2"
    ).selected]
    assert SKILL_ID in with_campaign


def test_publish_request_grants_no_publishing_tool(library_world) -> None:
    """A publish-shaped request that does select must not produce a publishing tool offer."""
    guidance_text, provenance, permitted = _guidance(
        "Manage our campaign and publish the approved post to Telegram"
    )
    assert SKILL_ID in [row["name"] for row in provenance] or guidance_text == ""
    assert all("publish" not in str(p) and "channel" not in str(p) for p in permitted), (
        "native skill guidance must never contribute publishing tools"
    )


def _guidance(user_text: str):
    from core.native_skill_library import guidance_for_selection

    selection = _select(user_text)
    return guidance_for_selection(selection.selected)


def test_disable_store_survives_restart_and_is_home_scoped(library_world, tmp_path, monkeypatch) -> None:
    from core.native_skill_library import (
        disabled_skill_ids,
        select_native_skills,
        set_skill_enabled,
    )

    text = "Plan a launch campaign for our socials"
    assert SKILL_ID in [c.id for c in select_native_skills(user_text=text).selected]
    set_skill_enabled(SKILL_ID, False)
    # "Restart": the disable store is read from disk on every call, so a fresh call in the
    # same home is equivalent to a restarted process.
    assert SKILL_ID in disabled_skill_ids()
    assert SKILL_ID not in [c.id for c in select_native_skills(user_text=text).selected]

    # A different home must not inherit this home's disable state.
    other = tmp_path / "other-home"
    other.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(other))
    import core.runtime_paths as runtime_paths

    runtime_paths.configure_runtime_home(other)
    try:
        assert SKILL_ID not in disabled_skill_ids()
        assert SKILL_ID in [c.id for c in select_native_skills(user_text=text).selected]
    finally:
        runtime_paths.configure_runtime_home(None)


def test_reenable_restores_selection_and_version_truth(library_world) -> None:
    from core.native_skill_library import inspect_skill, select_native_skills, set_skill_enabled

    before = inspect_skill(SKILL_ID)
    set_skill_enabled(SKILL_ID, False)
    set_skill_enabled(SKILL_ID, True)
    after = inspect_skill(SKILL_ID)
    assert after["status"] == "ok" and after["enabled"] is True
    assert after["version"] == before["version"] == "1.0.0"
    text = "Build a content calendar for the launch"
    assert SKILL_ID in [c.id for c in select_native_skills(user_text=text).selected]


def test_guidance_packing_respects_the_shared_budget(library_world) -> None:
    from core.tool_offer_assembly import MAX_SKILL_CHARS

    text, provenance, _permitted = _guidance(
        "Plan the launch campaign and draft the X thread announcing v2"
    )
    assert provenance, "expected co-bound skill guidance"
    assert len(text) <= MAX_SKILL_CHARS
    rows = {row["name"]: row for row in provenance}
    assert rows[SKILL_ID]["origin"] == "native"
    assert rows[SKILL_ID]["version"] == "1.0.0"


def test_selection_is_deterministic(library_world) -> None:
    text = "Set up campaign tracking and review our content metrics"
    first = tuple(c.id for c in _select(text).selected)
    for _ in range(3):
        assert tuple(c.id for c in _select(text).selected) == first
