"""A plain file name in a named folder is read from that folder (fresh-eyes test 2026-10-29: "read
notes.txt in Documents" came back "There is no file" because only typed paths were read directly,
and the model's own read looked in the workspace)."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    for place in ("Desktop", "Documents", "Downloads"):
        (home / place).mkdir(parents=True)
    (home / "Documents" / "notes.txt").write_text("buy milk on tuesday\n")
    (home / "Desktop" / "todo list.md").write_text("call the bank\n")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    return home


def _ask(text):
    from core.agent_runtime.agent import VoolAgent

    agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
    out = agent.run_once(text, session_id_override=f"openclaw:named-{abs(hash(text)) % 10**8}",
                         source_context={"surface": "openclaw", "platform": "openclaw"})
    return str(out.get("response") if isinstance(out, dict) else out)


@pytest.mark.parametrize("text", [
    "read notes.txt in Documents",
    "read notes.txt in my documents folder",
    "open notes.txt from Documents",
    "what does notes.txt in my Documents say?",
])
def test_a_file_named_in_a_named_folder_is_read(home, text):
    assert "buy milk on tuesday" in _ask(text)


def test_the_target_is_resolved_only_when_the_file_is_there(home):
    from core.agent_runtime.fast_paths_machine import _extract_machine_file_read_target

    assert _extract_machine_file_read_target("read notes.txt in Documents")["path"] == "~/Documents/notes.txt"
    assert _extract_machine_file_read_target("read notes.txt in Downloads") is None
    assert _extract_machine_file_read_target("read missing.txt in Documents") is None
