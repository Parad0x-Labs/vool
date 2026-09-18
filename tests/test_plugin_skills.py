"""Skills: YAML frontmatter, the body that was being discarded, and trigger ranking.

Two defects in `core/plugin_catalog._skill_frontmatter` motivate this module, and both have tests:
it parses frontmatter with `line.split(":", 1)` per line, and it throws the body away.

The split parser turns `allowed-tools: [a, b]` into the string `"[a, b]"` and truncates any value
at its first colon — so a description reading "Use when: the user pastes a stack trace" keeps only
"Use when". Every installed SKILL.md today has a long trigger sentence in exactly that field.

Discarding the body is the larger loss: the instructions an author wrote are the whole point of
shipping a skill, and none of them reached the model. Measured against the real repo on this
machine, the two installed plugins carry 3 skills and 7.4 KB of instructions that were being read
and dropped.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core import plugin_skills

SKILL_TEXT = """---
name: jira-triage
description: "Turn a bug report into a Jira issue. Use when: the user pastes a stack trace."
allowed-tools: [vool-jira.search_issues, vool-jira.create_issue, workspace.read_file]
triggers: [jira, ticket, sprint, triage]
---

# Jira triage

1. Extract a one-line summary under 80 characters.
2. Search for an existing issue before filing a new one.
3. Quote the issue key in your answer.
"""


@pytest.fixture()
def skill_file(tmp_path: Path) -> Path:
    path = tmp_path / "skills" / "triage" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(SKILL_TEXT, encoding="utf-8")
    return path


# --------------------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------------------


def test_a_yaml_list_stays_a_list(skill_file: Path) -> None:
    """The split parser yields the string "[a, b]" here, which narrows to no real tool."""

    skill = plugin_skills.parse_skill(skill_file)
    assert skill is not None
    assert skill.allowed_tools == (
        "vool-jira.search_issues",
        "vool-jira.create_issue",
        "workspace.read_file",
    )


def test_a_value_containing_a_colon_survives(skill_file: Path) -> None:
    skill = plugin_skills.parse_skill(skill_file)
    assert skill is not None
    assert "pastes a stack trace" in skill.description


def test_the_body_is_kept(skill_file: Path) -> None:
    """Today it is parsed and dropped, so an author's instructions never reach the model."""

    skill = plugin_skills.parse_skill(skill_file)
    assert skill is not None
    assert "Quote the issue key" in skill.body
    assert "---" not in skill.body, "frontmatter must not bleed into the body"


def test_a_comma_separated_string_is_accepted_too(tmp_path: Path) -> None:
    """Authors write both forms; refusing one would be a papercut, not a safety rule."""

    path = tmp_path / "SKILL.md"
    path.write_text("---\nname: x\nallowed-tools: a.one, b.two\n---\nbody\n", encoding="utf-8")
    skill = plugin_skills.parse_skill(path)
    assert skill is not None and skill.allowed_tools == ("a.one", "b.two")


def test_a_file_without_frontmatter_is_not_a_skill(tmp_path: Path) -> None:
    path = tmp_path / "SKILL.md"
    path.write_text("# Just markdown\n", encoding="utf-8")
    assert plugin_skills.parse_skill(path) is None


def test_malformed_yaml_still_yields_a_usable_skill(tmp_path: Path) -> None:
    """One broken skill must not take down the plugin shipping it; the body is the valuable part."""

    path = tmp_path / "skills" / "broken" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\nname: [unclosed\n---\nThe instructions still matter.\n", encoding="utf-8")
    skill = plugin_skills.parse_skill(path)
    assert skill is not None and "instructions still matter" in skill.body


def test_a_missing_name_falls_back_to_the_directory(tmp_path: Path) -> None:
    path = tmp_path / "skills" / "triage" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\ndescription: d\n---\nbody\n", encoding="utf-8")
    skill = plugin_skills.parse_skill(path)
    assert skill is not None and skill.name == "triage"


def test_load_skills_walks_a_plugin(skill_file: Path) -> None:
    skills = plugin_skills.load_skills(skill_file.parent.parent.parent, plugin_id="vool-jira")
    assert [s.name for s in skills] == ["jira-triage"]
    assert skills[0].plugin_id == "vool-jira"


def test_a_plugin_without_skills_yields_nothing(tmp_path: Path) -> None:
    assert plugin_skills.load_skills(tmp_path) == ()


# --------------------------------------------------------------------------------------
# Ranking. A signal, never a gate.
# --------------------------------------------------------------------------------------


def _skills() -> tuple[plugin_skills.Skill, ...]:
    return (
        plugin_skills.Skill(
            name="jira-triage",
            description="File and search tickets.",
            body="triage body",
            triggers=("jira", "ticket", "sprint"),
            allowed_tools=("vool-jira.search_issues",),
        ),
        plugin_skills.Skill(
            name="render",
            description="Render an image locally.",
            body="render body",
            triggers=("image", "render"),
            allowed_tools=("vool-render.run",),
        ),
    )


def test_the_relevant_skill_ranks_first() -> None:
    top = plugin_skills.rank_skills(_skills(), "open a jira ticket for the flaky sprint test")
    assert top and top[0].name == "jira-triage"


def test_an_unrelated_request_matches_nothing() -> None:
    assert plugin_skills.rank_skills(_skills(), "what is the capital of France") == ()


def test_an_explicit_trigger_outweighs_a_description_word() -> None:
    """Otherwise a verbose description outranks a precise trigger list on volume alone."""

    skills = (
        plugin_skills.Skill(name="precise", body="b", triggers=("jira",)),
        plugin_skills.Skill(name="wordy", body="b", description="jira " * 1),
    )
    top = plugin_skills.rank_skills(skills, "jira please")
    assert top[0].name == "precise"


def test_ranking_is_capped() -> None:
    many = tuple(
        plugin_skills.Skill(name=f"s{i}", body="b", triggers=("jira",)) for i in range(10)
    )
    assert len(plugin_skills.rank_skills(many, "jira", limit=2)) == 2


def test_instructions_are_the_author_written_bodies() -> None:
    text = plugin_skills.instructions_for(_skills())
    assert "triage body" in text and "render body" in text


def test_a_skill_with_no_body_contributes_nothing() -> None:
    empty = (plugin_skills.Skill(name="x", description="d"),)
    assert plugin_skills.instructions_for(empty) == ""


def test_narrowing_is_the_union_of_matched_skills() -> None:
    assert plugin_skills.narrowed_tools(_skills()) == (
        "vool-jira.search_issues",
        "vool-render.run",
    )


def test_no_matched_skill_means_do_not_narrow() -> None:
    """Empty means the full catalog. A skill that forgot to list tools must not leave none."""

    assert plugin_skills.narrowed_tools(()) == ()


# --------------------------------------------------------------------------------------
# Against the real installed repo
# --------------------------------------------------------------------------------------


def test_the_installed_skills_parse_and_carry_bodies() -> None:
    from core.plugin_catalog import plugins_root

    root = plugins_root()
    if root is None:
        pytest.skip("no plugin repo installed on this machine")
    found = [
        skill
        for entry in sorted((root / "plugins").iterdir())
        if entry.is_dir()
        for skill in plugin_skills.load_skills(entry, plugin_id=entry.name)
    ]
    assert found, "expected the installed plugins to ship skills"
    assert all(s.body for s in found), "every installed skill should carry instructions"
    assert all(s.description for s in found), "installed skills use description as the trigger line"


def test_an_installed_plugins_own_example_prompt_matches_its_skill() -> None:
    """End to end on the real repo: the utterance a plugin advertises selects its own skill."""

    from core.plugin_catalog import plugins_root

    root = plugins_root()
    if root is None:
        pytest.skip("no plugin repo installed on this machine")
    found = tuple(
        skill
        for entry in sorted((root / "plugins").iterdir())
        if entry.is_dir()
        for skill in plugin_skills.load_skills(entry, plugin_id=entry.name)
    )
    top = plugin_skills.rank_skills(found, "prepare this local image as an identity-safe reference pack")
    assert top, "the plugin's own defaultPrompt matched no skill it ships"
    assert plugin_skills.instructions_for(top).strip()
