"""`skill.install` names what is wrong with a skill it refuses.

An outside author installed a skill whose allowed-tools named a tool that does not exist
(`workspace.writefile`). `skill.validate` and `try` named the tool; `skill.install` said only
that the skill "does not validate". The refusal now carries the validator's own findings.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def profile(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(home / "vool"))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path / "plugins"))
    return tmp_path


def _skill(folder: Path, tools: str) -> Path:
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\nname: note-saver\ndescription: Save the meeting notes the user gives into a file.\n"
        f"allowed-tools: {tools}\n---\n\n1. Save the notes.\n",
        encoding="utf-8",
    )
    return folder


def test_install_refusal_names_the_unknown_tool(profile):
    from core.skill_tools import install_skill

    result = install_skill(str(_skill(profile / "src" / "note-saver", "[workspace.writefile]")))
    assert result["status"] == "refused"
    assert "workspace.writefile" in result["reason"]
    assert "allowed-tools names tools this runtime does not have" in result["reason"]
    assert any("workspace.writefile" in item for item in result["problems"])


def test_install_tool_reply_names_the_unknown_tool(profile):
    from core.runtime_execution_tools import execute_runtime_tool
    from core.skill_tools import staging_root

    # A draft in VOOL's drafts folder: the refusal names the unknown tool.
    folder = _skill(staging_root() / "note-saver", "[workspace.writefile]")
    result = execute_runtime_tool("skill.install", {"path": str(folder)})
    assert result.status == "refused"
    assert "workspace.writefile" in result.response_text
    assert "not installed" in result.response_text


def test_install_tool_does_not_open_a_skill_file_outside_the_skill_folders(profile):
    """The old scenario (a folder somewhere under the person's files) is refused before the file is
    read, and the reply says what was refused, why, and what to do instead (red team t17)."""
    from core.runtime_execution_tools import execute_runtime_tool

    folder = _skill(profile / "src" / "note-saver", "[workspace.writefile]")
    result = execute_runtime_tool("skill.install", {"path": str(folder)})
    assert result.status == "not_allowed"
    assert "workspace.writefile" not in result.response_text, "the file was not read"
    assert result.response_text.startswith("I did not open that file")
    assert "VOOL's own skill folders" in result.response_text and "attach its zip" in result.response_text
