"""Asks for the person's own notes read the notes store, not the other chats.

On the v0.8 line merged with the full 0.7.7 train, "show me my notes" and "find my notes about
the boiler" were answered by the cross-chat recall lane ("I don't have that saved, and I looked
through your other chats ...") over a workspace that held the notes, where the train listed them
(audit N10). The operator parser already reads both as notes asks; the recall lane now stands down
for any ask the operator parser claims, read on the words as typed. Questions about what the
person said keep the recall lane.

Driven through the real agent turn (VoolAgent.run_once), no model.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def turn(tmp_path, runtime_storage_reset, monkeypatch):
    import tempfile
    from pathlib import Path

    from core.agent_runtime.agent import VoolAgent

    workspace = Path(tempfile.mkdtemp(prefix="vool-notes-ws-"))
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(workspace))
    agent = VoolAgent(backend_name="test-backend", device="notes-turns", persona_id="default")

    def run(text: str, chat: str = "notes-turn") -> dict:
        return agent.run_once(text, session_id_override=chat, source_context={"chat_id": chat})

    run.workspace = workspace
    return run


def _keep(turn, words: str) -> None:
    result = turn(f"keep a note for me: {words}")
    assert result.get("mode") == "tool_executed", result
    assert any(words in path.read_text() for path in (turn.workspace / "notes").glob("*.md"))


def test_show_me_my_notes_lists_the_notes_not_other_chats(turn):
    _keep(turn, "the boiler service is due on the 12th")
    result = turn("show me my notes")
    reply = str(result.get("response"))
    assert result.get("route") != "cross_chat_recall", result
    assert "boiler service" in reply.lower(), reply
    assert "I don't have that saved" not in reply


def test_find_my_notes_about_a_thing_searches_the_notes(turn):
    _keep(turn, "the boiler service is due on the 12th")
    result = turn("find my notes about the boiler")
    reply = str(result.get("response"))
    assert result.get("route") != "cross_chat_recall", result
    assert "boiler" in reply.lower() and "I don't have that saved" not in reply, reply


def test_a_question_about_what_the_person_said_keeps_the_recall_lane(turn):
    result = turn("what did I tell you about my sister")
    assert result.get("route") == "cross_chat_recall", result
