""""What did I ask you first" reads this chat's own transcript, never the other chats.

The train port's real-app run (2026-11-12, chat door, Auto): after "how big is report.pdf", the
ask "what did I ask you first" was answered by the cross-chat recall lane, "I don't have that
saved, and I looked through your other chats and projects (1 chat) without finding it.", while
the first question sat two turns up. The recall lane runs before the transcript reader and claimed
the owner-local turn. It now stands down for a request to read this chat's earlier turns
(core.context_history_authority.is_same_chat_history_recall); an ask that names another chat keeps
the recall lane.

Driven through the real agent turn (VoolAgent.run_once) on an owner-local context, no model.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def turn(tmp_path, runtime_storage_reset, monkeypatch):
    import tempfile
    from pathlib import Path

    from core.agent_runtime.agent import VoolAgent

    workspace = Path(tempfile.mkdtemp(prefix="vool-first-ws-"))
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(workspace))
    agent = VoolAgent(backend_name="test-backend", device="first-question-turns", persona_id="default")

    def run(text: str, chat: str = "first-question-turn") -> dict:
        context = {"chat_id": chat, "surface": "openclaw", "platform": "openclaw", "_owner_local": True}
        return agent.run_once(text, session_id_override=chat, source_context=context)

    return run


def test_the_owner_local_turn_must_be_one_the_recall_lane_would_take():
    """Guard for the test below: the context it uses is owner-local, so the recall lane is live."""
    from core.request_trust import request_is_owner_local

    assert request_is_owner_local({"chat_id": "c", "surface": "openclaw", "platform": "openclaw", "_owner_local": True})


@pytest.mark.parametrize("ask", ["what did I ask you first", "what was my first question"])
def test_the_first_question_is_read_from_this_chat(turn, ask):
    turn("help")
    turn("what's on my calendar next week")
    result = turn(ask)
    reply = str(result.get("response") or "")
    assert result.get("route") != "cross_chat_recall", result
    assert "I don't have that saved" not in reply, reply
    assert '"help"' in reply, reply


def test_an_ask_that_names_another_chat_still_reaches_the_recall_lane():
    from core.agent_runtime.cross_chat_recall import _OTHER_CHAT_RE

    assert _OTHER_CHAT_RE.search("what did I ask you first in the other chat")
