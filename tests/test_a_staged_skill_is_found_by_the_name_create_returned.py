"""`create` hands back a slug and says "validate it, then install it". That sequence did not work.

Found by a QA drive on 2026-07-30, driving the three tools in the order the first one prescribes::

    skill.create   {"name": "qa-echo", ...}   -> ok, slug "qa-echo",
                                                 "It is not active yet - validate it, then install it."
    skill.validate {"path": "qa-echo"}        -> not_found, "no SKILL.md at qa-echo"
    skill.install  {"path": "qa-echo"}        -> refused, "the skill does not validate, so installing
                                                 it would add a capability that cannot work"

Every step behaved as written and the sequence still failed. ``validate`` resolved "qa-echo" against
the process working directory like any relative path, so the one identifier ``create`` returns was
the one identifier the next step could not take. The three steps are deliberately separate calls --
the module docstring says so -- which is exactly why the handle passed between them has to survive
the gap.

The second half is the wording. ``install`` reported a file that does not exist as a skill that
"does not validate", which names the wrong problem: it sends the reader to fix the contents of a
skill that was never written. A missing skill is not an invalid one.
"""
from __future__ import annotations

from unittest import mock

import pytest


@pytest.fixture
def skills(tmp_path):
    """A real plugins tree, isolated from the operator's own."""

    import core.skill_tools as module

    with mock.patch.object(module, "plugins_root", lambda: tmp_path):
        yield module, tmp_path


# ---------------------------------------------------------------------------
# 1. The sequence create prescribes
# ---------------------------------------------------------------------------


def test_the_slug_create_returns_is_accepted_by_validate_and_install(skills) -> None:
    module, root = skills
    created = module.create_skill(
        name="qa-echo", description="Echo a word back twice.", body="Repeat the word two times."
    )
    assert created["status"] == "ok"

    # Verbatim: the handle create returned, nothing else.
    handle = created["slug"]
    assert module.validate_skill(handle)["status"] == "ok", "create's own next step could not find it"
    assert module.install_skill(handle)["status"] == "ok"

    from core.plugin_skills import load_skills

    assert [s.name for s in load_skills(root / "plugins" / "local-skills", plugin_id="local-skills")] == ["qa-echo"]


@pytest.mark.parametrize("handle", ["qa-echo", "QA Echo", "qa echo", "  qa-echo  ", "`qa-echo`"])
def test_a_skill_is_found_by_the_name_a_person_would_type(skills, handle: str) -> None:
    """"validate the qa-echo skill" is how it gets asked for. The slug is derived, not demanded."""

    module, _root = skills
    module.create_skill(name="qa-echo", description="Echo a word back twice.", body="Repeat it.")

    assert module.validate_skill(handle)["status"] == "ok"


def test_an_installed_skill_is_still_found_by_name(skills) -> None:
    """After install the file has MOVED out of staging. The name must keep resolving to it."""

    module, _root = skills
    created = module.create_skill(name="qa-echo", description="Echo a word.", body="Repeat it.")
    module.install_skill(created["path"])

    module.staging_root().joinpath("qa-echo", "SKILL.md").unlink()

    assert module.validate_skill("qa-echo")["status"] == "ok"


def test_staging_is_searched_before_the_installed_tree(skills) -> None:
    """A redraft is the copy being validated -- otherwise validate reports on the old installed one."""

    module, _root = skills
    created = module.create_skill(name="qa-echo", description="Echo a word.", body="the first body")
    module.install_skill(created["path"])
    module.create_skill(name="qa-echo", description="Echo a word.", body="the second body", overwrite=True)

    resolved = module.resolve_skill_markdown("qa-echo")
    assert resolved.parent.parent.name == module.STAGING_DIRNAME
    assert module.validate_skill("qa-echo")["body_characters"] == len("the second body")


# ---------------------------------------------------------------------------
# 2. A missing skill is not an invalid one
# ---------------------------------------------------------------------------


def test_a_name_that_was_never_created_says_it_was_never_created(skills) -> None:
    module, _root = skills

    verdict = module.validate_skill("qa-never-made")
    assert verdict["status"] == "not_found"

    refusal = module.install_skill("qa-never-made")
    assert refusal["status"] == "not_found", "a missing skill was reported as one that does not validate"
    assert "does not validate" not in str(refusal.get("reason") or "")


def test_a_real_validation_failure_is_still_refused_not_reported_missing(skills) -> None:
    """The not_found branch must not swallow the case install was written for."""

    module, _root = skills
    created = module.create_skill(
        name="qa-bad", description="Echo a word.", body="do it", allowed_tools=["workspace.no_such_tool"]
    )

    refusal = module.install_skill(created["path"])
    assert refusal["status"] == "refused"
    assert any("no_such_tool" in str(p) for p in refusal["problems"])


# ---------------------------------------------------------------------------
# 3. A real path still means a real path
# ---------------------------------------------------------------------------


def test_an_explicit_path_is_not_reinterpreted_as_a_name(skills) -> None:
    module, _root = skills
    created = module.create_skill(name="qa-echo", description="Echo a word.", body="Repeat it.")

    assert module.validate_skill(created["path"])["status"] == "ok"
    assert module.validate_skill(str(module.staging_root() / "qa-echo"))["status"] == "ok"
    # A rooted path that does not exist stays not_found -- it is not looked up as a skill name.
    assert module.validate_skill("/tmp/vool-qa-no-such-dir/qa-echo")["status"] == "not_found"


def test_a_multi_segment_path_is_never_slugged_into_a_name(skills) -> None:
    """`if True:` in place of the bare-name guard would slug "a/b/qa-echo" and find the staged skill,
    answering about a file the caller did not name."""

    module, _root = skills
    module.create_skill(name="qa-echo", description="Echo a word.", body="Repeat it.")

    assert module.validate_skill("some/other/qa-echo")["status"] == "not_found"
    assert module.validate_skill("./qa-echo")["status"] == "not_found"


def test_the_search_order_is_staging_then_installed(skills) -> None:
    """Pinned directly, so a reordering cannot hide behind a probe that never matches anything."""

    module, _root = skills
    created = module.create_skill(name="qa-echo", description="Echo a word.", body="Repeat it.")
    module.install_skill(created["path"])

    locations = module._skill_locations("qa-echo")

    # P1 native skill library: the first-party package root joined the search between staging
    # (the copy being redrafted) and installed (the active copy). Three homes, order pinned.
    assert len(locations) == 3, "all three homes must be in the search, or the order says nothing"
    assert locations[0] == module.staging_root() / "qa-echo" / "SKILL.md"
    from core.native_skill_library import native_skills_root

    assert locations[1] == native_skills_root() / "qa-echo" / "SKILL.md"
    assert locations[2].parent.parent.name == "skills"
