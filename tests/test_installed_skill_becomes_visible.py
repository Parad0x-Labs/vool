"""An installed skill must be VISIBLE — manifest, catalog, loader, ranking, instructions.

Two defects found by the 2026-08-29 tooling census made every skill the agent authored a no-op:

1. `install_skill` writes into `<root>/plugins/local-skills/`, but `plugin_catalog` returns None
   for any plugin directory without `.codex-plugin/plugin.json` — and nothing ever created one.
   Measured live: three plugin directories on disk, `/api/plugins` reported two, and the missing
   one was the default install target.
2. `skill_tools.plugins_root()` read `_DEFAULT_PLUGINS_DIR` directly, ignoring the
   `VOOL_PLUGINS_DIR` override that `plugin_catalog.plugins_root()` honours — despite its own
   docstring promising it resolved "through it, never guessed at separately". With an override set,
   skills installed into one tree while the catalog listed another, silently.

These tests drive the real functions against a real temporary plugin root.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.plugin_catalog import plugins_root as catalog_plugins_root
from core.plugin_skills import instructions_for, load_skills, rank_skills
from core.skill_tools import install_skill, plugins_root as writer_plugins_root

_SKILL = """---
name: visible-proof-skill
description: Proves an installed skill reaches the loader.
triggers: compress archive vault workspace
---

# Visible proof

When asked to prove skill installation, state that the skill body was loaded.
"""


@pytest.fixture()
def plugin_root(tmp_path, monkeypatch) -> Path:
    (tmp_path / "plugins").mkdir()
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture()
def staged_skill(tmp_path) -> Path:
    staged = tmp_path / "staged.md"
    staged.write_text(_SKILL, encoding="utf-8")
    return staged


def test_the_writer_and_the_catalog_resolve_the_same_root(plugin_root: Path) -> None:
    """Defect 2: with an override set these disagreed, so installs vanished."""
    assert writer_plugins_root() == plugin_root
    assert catalog_plugins_root() == plugin_root


def test_installing_a_skill_creates_the_manifest_the_catalog_requires(
    plugin_root: Path, staged_skill: Path
) -> None:
    """Defect 1: without this manifest the whole plugin directory is skipped."""
    result = install_skill(str(staged_skill))
    assert result["status"] == "ok", result

    manifest = plugin_root / "plugins" / "local-skills" / ".codex-plugin" / "plugin.json"
    assert manifest.is_file(), "the install target is invisible to the catalog without a manifest"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["name"] == "local-skills"
    assert payload["skills"] == "./skills/"


def test_an_existing_manifest_is_never_overwritten(plugin_root: Path, staged_skill: Path) -> None:
    """The operator's own manifest is authority; installation may only fill an absence."""
    manifest = plugin_root / "plugins" / "local-skills" / ".codex-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    original = '{"name": "local-skills", "version": "9.9.9", "operator": "hand-written"}\n'
    manifest.write_text(original, encoding="utf-8")

    assert install_skill(str(staged_skill))["status"] == "ok"
    assert manifest.read_text(encoding="utf-8") == original


def test_an_installed_skill_reaches_the_loader_with_its_body(
    plugin_root: Path, staged_skill: Path
) -> None:
    """The end-to-end chain: install -> catalog-visible -> loaded -> body present."""
    assert install_skill(str(staged_skill))["status"] == "ok"

    plugin_dir = plugin_root / "plugins" / "local-skills"
    skills = load_skills(plugin_dir, plugin_id="local-skills")
    assert [skill.name for skill in skills] == ["visible-proof-skill"]

    body = skills[0].body
    assert body.strip(), "the skill body is what gets injected; an empty body is a dead skill"
    assert "skill body was loaded" in body


def test_the_installed_skill_ranks_and_produces_instructions(
    plugin_root: Path, staged_skill: Path
) -> None:
    """Ranking gates injection: a skill that cannot rank can never reach a turn."""
    assert install_skill(str(staged_skill))["status"] == "ok"
    skills = load_skills(plugin_root / "plugins" / "local-skills", plugin_id="local-skills")

    ranked = rank_skills(skills, "compress and archive my workspace into a vault")
    assert [skill.name for skill in ranked] == ["visible-proof-skill"]
    assert instructions_for(ranked).strip(), "a ranked skill must yield injectable instructions"
