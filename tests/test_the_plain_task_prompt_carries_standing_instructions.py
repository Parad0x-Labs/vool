"""The minimal plain-task prompt of a new chat carries the owner's standing instructions.

Before this fix the plain_task_minimal profile sent the model a prompt identical to a chat with no history except
for the clock line: persona, plain-task instruction and the side-effect rule, with no context message and no
history. Nothing the owner had asked VOOL to keep doing reached the model. The prompt is built here exactly as
the plain-task route builds it (an empty context result), so the profile under test is the real one.
"""
from __future__ import annotations

import pytest

from core import standing_instructions as si
from core.tiered_context_loader import empty_tiered_context_result
from tests.test_prompt_assembly_profiles import _build_request


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(si, "_store_path", lambda: tmp_path / "standing_instructions.json")


def _plain_prompt(ctx: dict):
    empty = empty_tiered_context_result(task_id="t-plain", reason="plain_task_minimal_no_context",
                                        source_context=dict(ctx))
    request, _ = _build_request("Explain what a watershed is in two sentences.", task_class="chat_conversation",
                                task_kind="normalization_assist", output_mode="plain_text", source_context=ctx,
                                context_result_override=empty)
    text = "\n".join(str(m.get("content") or "") for m in request.as_openai_messages())
    return request.metadata.get("system_prompt_profile"), text


def test_a_new_plain_task_chat_sees_the_saved_instruction_and_nothing_when_none_is_saved(store, tmp_path):
    ctx = {"surface": "chat", "platform": "chat", "_owner_local": True, "workspace_root": str(tmp_path / "atlas")}
    profile, before = _plain_prompt(dict(ctx))
    assert profile == "plain_task_minimal"
    assert si.BLOCK_HEADER not in before
    si.observe_turn("Never use emojis in your answers.", ctx, principal="owner_local")
    profile, after = _plain_prompt(dict(ctx))
    assert profile == "plain_task_minimal"
    assert si.BLOCK_HEADER in after and "Never use emojis in your answers." in after
