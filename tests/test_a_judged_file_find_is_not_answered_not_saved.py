"""A message the turn's judge read as a file on this computer is looked for, not answered "not saved" or "no receipt".

iMac gate on 6f13177cc (from source, qwen3:8b): the judge picked Find for both, the memory question added a recall, and
- "can you find my boiler warranty document?" (no project) was answered by the cross-chat recall lane: "I don't have
  that saved, and I looked through your other chats and projects" while the file sat in Documents;
- "I need the electric bill, where is it?" was answered by the file-receipt lane: "I have no file-write receipt in this
  session ... I will not guess by searching your disk".
No find ran for either. Here the judge's picks are recorded as qwen3:8b gave them; the wordings are drawn at random.
"""
from __future__ import annotations

import random
import uuid

import pytest

from core import plugin_ask

FILE_ASKS = (
    "can you find my {thing}?", "where's my {thing}?", "I need my {thing}, where is it?",
    "do you know where my {thing} is?", "help me find the {thing} please",
)
THINGS = ("boiler warranty document", "electric bill", "tax return 2023", "lease agreement", "car insurance policy")


@pytest.fixture(autouse=True)
def _clean():
    plugin_ask.forget()
    yield
    plugin_ask.forget()


@pytest.fixture
def turn(tmp_path, runtime_storage_reset, monkeypatch):
    import tempfile
    from pathlib import Path

    from core.agent_runtime.agent import VoolAgent

    workspace = Path(tempfile.mkdtemp(prefix="vool-find-ws-"))
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(workspace))
    agent = VoolAgent(backend_name="test-backend", device="judged-find", persona_id="default")

    def run(text: str, chat: str) -> dict:
        return agent.run_once(text, session_id_override=chat, source_context={"chat_id": chat})

    return run


@pytest.mark.parametrize("seed", range(5))
def test_a_judged_find_is_not_answered_from_memory_or_receipts(seed, turn, monkeypatch) -> None:
    rng = random.Random(f"judged-find-{seed}")
    message = rng.choice(FILE_ASKS).format(thing=rng.choice(THINGS))

    def judged(text, source_context, session_id):
        # What qwen3:8b's tool judgement and memory question gave for these asks on the iMac.
        plugin_ask._record_builtins(text, ("find", "memory_recall"))
        return None

    monkeypatch.setattr(plugin_ask, "_judge_turn", judged)

    result = turn(message, f"judged-find-{uuid.uuid4().hex[:8]}")
    reply = str(result.get("response") or "")

    assert result.get("route") not in {"cross_chat_recall", "action_receipt_location"}, (message, result.get("route"))
    assert "I don't have that saved" not in reply and "file-write receipt" not in reply, (message, reply)


def test_the_receipt_lane_still_answers_a_deictic_ask_nobody_judged_a_find() -> None:
    from core.agent_runtime.fast_paths_receipt_location import maybe_handle_action_receipt_location_request

    class _Agent:
        def _fast_path_result(self, **kwargs):
            return {"reason": kwargs.get("reason"), "response": kwargs.get("response")}

    found = maybe_handle_action_receipt_location_request(
        _Agent(), "where did you save it?", session_id=f"loc-{uuid.uuid4().hex[:8]}", source_surface="api",
        source_context={"workspace": "", "surface": "api"},
    )
    assert found is not None and found["reason"] == "action_receipt_location"


def test_the_receipt_lane_hands_on_a_deictic_ask_judged_a_find() -> None:
    from core.agent_runtime.fast_paths_receipt_location import maybe_handle_action_receipt_location_request

    class _Agent:
        def _fast_path_result(self, **kwargs):
            return {"reason": kwargs.get("reason"), "response": kwargs.get("response")}

    message = "I need the electric bill, where is it?"
    plugin_ask._record_builtins(message, ("find", "memory_recall"))
    assert maybe_handle_action_receipt_location_request(
        _Agent(), message, session_id=f"loc-{uuid.uuid4().hex[:8]}", source_surface="api",
        source_context={"workspace": "", "surface": "api"},
    ) is None
