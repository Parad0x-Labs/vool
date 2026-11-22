"""A failed step never shows the person a lane id or text addressed to the model.

Blind audit of 58f60bbf3 (local qwen3:8b):
- "what colour is my tent, were does it live? check your memory from earlier chats" was answered
  "- were does it live? — machine_observation found nothing to act on in this request".
- "Atsimink šitą: mano katė vardu Birutė bijo dulkių siurblio." was answered "0 of 1 tool steps succeeded. Last
  result: `translation` is not a capability family. Ask `operator.list_tools` for the family table first."
Both fail before this change.
"""
from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

from core.agent_runtime.orchestrator import tool_loop_final_message
from core.conductor.compose import reader_facing_reason


@pytest.mark.parametrize("seed", range(4))
def test_a_lane_that_found_nothing_is_not_named(seed):
    rng = random.Random(f"lane-{seed}")
    lane = rng.choice(["machine_observation", "calculation", "weather", "live_info_lookup"])
    reason = reader_facing_reason(f"{lane} found nothing to act on in this request")
    assert lane not in reason and "act on" not in reason
    assert reason.strip()


@pytest.mark.parametrize("seed", range(4))
def test_the_models_own_tool_navigation_is_never_the_answer(seed):
    rng = random.Random(f"nav-{seed}")
    family = rng.choice(["translation", "memory", "notes_and_stuff", "weather"])
    steps = [{"tool_name": "capability.expand_family", "ok": False, "status": "unknown_family",
              "summary": f"`{family}` is not a capability family. Ask `operator.list_tools` for the family table first."}]
    if rng.random() < 0.5:
        steps.insert(0, {"tool_name": "operator.list_tools", "ok": True, "status": "executed",
                         "summary": "Families: files, web, operator, memory"})
    message = tool_loop_final_message(SimpleNamespace(structured_output=None, output_text=""), steps)
    assert "capability family" not in message and "operator.list_tools" not in message and "Families:" not in message
    assert "tool steps succeeded" not in message


def test_a_real_step_still_reports_its_result():
    steps = [{"tool_name": "capability.expand_family", "ok": True, "status": "executed", "summary": "seated notes"},
             {"tool_name": "operator.save_note", "ok": True, "status": "executed", "summary": "Saved note 'pantry list'"}]
    message = tool_loop_final_message(SimpleNamespace(structured_output=None, output_text=""), steps)
    assert "1 of 1 tool steps succeeded" in message and "pantry list" in message
