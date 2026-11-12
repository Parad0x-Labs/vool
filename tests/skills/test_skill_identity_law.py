"""The recovered skill-identity law, live at the install/versioning seams.

Pins:
- the two-digest law in ``_record_version`` (package sha256 + effective
  sha256 with hidden blocks stripped);
- the identity-collision gate in ``install_skill`` (one instruction set,
  one name);
- hidden HTML-comment blocks do not change the effective digest (a tamper
  channel), while real instruction changes do.
"""

from __future__ import annotations

import json

from core import skill_tools
from core.skill_identity import compute_effective_digest, strip_hidden_blocks

SKILL_A = """---
name: weather-brief
description: Brief the operator on weather with units they use.
---

You answer weather questions concisely.
Always state the unit system.
"""

SKILL_A_REPACKAGED = """---
name: weather-brief
description: Brief the operator on weather with units they use.
---

You answer weather questions concisely.
Always state the unit system.
<!-- repackaged by someone else — a comment is not an instruction -->
"""

SKILL_B = """---
name: unit-converter
description: Convert quantities between unit systems.
---

You convert units exactly, showing the factor.
"""


def _install(tmp_path, monkeypatch, name: str, body: str) -> dict:
    plugins = tmp_path / "plugins-root"
    monkeypatch.setattr(skill_tools, "plugins_root", lambda: plugins)
    staged = tmp_path / f"{name}.md"
    staged.write_text(body, encoding="utf-8")
    return skill_tools.install_skill(str(staged))


def test_record_version_carries_both_digests(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_tools, "plugins_root", lambda: tmp_path)
    skill_dir = tmp_path / "plugins" / "p" / "skills" / "weather-brief"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(SKILL_A, encoding="utf-8")
    entry = skill_tools._record_version(skill_dir, source="install")
    assert len(entry["sha256"]) == 64
    assert len(entry["effective_sha256"]) == 64
    assert entry["sha256"] != entry["effective_sha256"]
    history = json.loads((skill_dir / "versions" / "history.json").read_text())
    assert history["versions"][-1]["effective_sha256"] == entry["effective_sha256"]


def test_hidden_blocks_do_not_change_effective_digest():
    clean, blocks = strip_hidden_blocks("instructions here<!-- hidden -->x<!-- /hidden -->")
    assert "instructions here" in clean
    assert blocks  # extracted, never instruction-bearing
    # The pipeline law: comments are stripped BEFORE hashing, so a hidden block
    # smuggled into one copy of a skill leaves its effective identity unchanged.
    manifest = {"name": "x", "description": "y"}
    stripped, _ = strip_hidden_blocks("body text\n<!-- note -->")
    with_comment = compute_effective_digest(manifest, stripped)
    without_comment = compute_effective_digest(manifest, "body text")
    assert with_comment == without_comment


def test_instruction_change_changes_effective_digest():
    manifest = {"name": "x", "description": "y"}
    assert compute_effective_digest(manifest, "do A") != compute_effective_digest(manifest, "do B")


def test_install_refuses_identity_collision(tmp_path, monkeypatch):
    first = _install(tmp_path, monkeypatch, "weather-brief", SKILL_A)
    assert first.get("status") == "ok", first
    # Same instructions under a different name and description: one instruction set, one name.
    renamed = SKILL_A.replace("weather-brief", "weather-relay").replace(
        "Brief the operator on weather with units they use.", "Relay the weather in the operator's units.")
    second = _install(tmp_path, monkeypatch, "weather-relay", renamed)
    assert second.get("status") == "refused", second
    assert second.get("collision_with") == "weather-brief"
    # The reason is for a person (audit L1); the exact check stays in problems.
    assert 'same instructions as your skill "Weather brief"' in second.get("reason", "")
    assert any("identity collision" in item for item in second.get("problems", ()))


def test_a_copy_repackaged_with_a_hidden_comment_is_refused_before_it_can_collide(tmp_path, monkeypatch):
    """A hidden HTML comment is a tamper channel, so the repackaged copy is now refused by the
    safety check itself, naming the line, why, and what to write instead."""
    first = _install(tmp_path, monkeypatch, "weather-brief", SKILL_A)
    assert first.get("status") == "ok", first
    second = _install(tmp_path, monkeypatch, "weather-relay",
                      SKILL_A_REPACKAGED.replace("weather-brief", "weather-relay"))
    assert second.get("status") == "refused", second
    reason = second.get("reason", "")
    assert reason.startswith("This skill did not pass VOOL's safety check, so it was not installed.")
    assert 'Line 8 ("<!--' in reason
    assert "VOOL skills can't carry hidden comments" in reason
    assert "Write everything as plain visible text." in reason
    assert not (tmp_path / "plugins-root" / "plugins" / "local-skills" / "skills" / "weather-relay").exists()


def test_distinct_skill_installs_cleanly(tmp_path, monkeypatch):
    first = _install(tmp_path, monkeypatch, "weather-brief", SKILL_A)
    assert first.get("status") == "ok", first
    second = _install(tmp_path, monkeypatch, "unit-converter", SKILL_B)
    assert second.get("status") == "ok", second
