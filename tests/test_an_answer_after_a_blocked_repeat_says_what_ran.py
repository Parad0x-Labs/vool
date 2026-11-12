"""The answer written after the tool loop ends says what ran; it is never told to keep running tools.

App bugs 2026-11-12 (real app, 247124ed, F1c): "run the command: sed -n 2p orders/week.csv" ran the
command, the model asked to run the same command again, the repeat guard ended the loop, and the
call that writes the answer (offered no tools) was told "Do not repeat an identical tool call.
Continue the tool actions needed to fulfill the user's request". qwen3.5:4b answered:

    I can't run that command.

    That specific instruction (sed -n 2p orders/week.csv) was already executed ...

The command had run, and its output was in the same answer. The answer-writing call now reads that
the actions already ran for the person's request and that its reply is the final answer; every call
inside the loop keeps the instruction to continue. The served journey on the real page is
tests/test_an_answer_after_a_blocked_repeat_says_what_ran_served.py.
"""

from __future__ import annotations

import pathlib
import tempfile
import uuid
from types import SimpleNamespace

import pytest

from apps.vool_agent import VoolAgent
from core.memory_first_router import ModelExecutionDecision
from core.prompt_normalizer import _runtime_tool_observation_message
from core.request_trust import RESERVED_TRUST_KEYS, strip_reserved_trust_keys

OBSERVATION_HEADER = "Real tool observations from this same turn follow."
LOOP_WORDING = ("Continue the tool actions", "respond.direct", "Do not repeat an identical tool call")
COMMAND_OBSERVATION = {
    "intent": "sandbox.run_command",
    "command": "sed -n 2p orders/week.csv",
    "ok": True,
    "status": "executed",
    "returncode": 0,
    "stdout_excerpt": "receipt-51ab-cobalt,125",
}


def _block(source_context: dict) -> list[str]:
    message = _runtime_tool_observation_message(source_context)
    assert message is not None
    return str(message.content).splitlines()


# ------------------------------------------------------------------------------ the prompt itself


def test_a_call_inside_the_loop_is_still_told_to_continue_the_tool_actions() -> None:
    header = _block({"runtime_tool_observations": [COMMAND_OBSERVATION]})[0]

    assert header.startswith(OBSERVATION_HEADER)
    for wording in LOOP_WORDING:
        assert wording in header, f"{wording!r} left the loop's own instruction: {header!r}"


@pytest.mark.parametrize("why", ["repeated_tool_request", "step_budget_exhausted", "completed"])
def test_the_answer_writing_call_reads_that_the_actions_already_ran(why: str) -> None:
    header = _block({"runtime_tool_observations": [COMMAND_OBSERVATION], "tool_loop_terminal_synthesis": why})[0]

    # The opening sentence stays: the served rigs and the history recognizer key on it.
    assert header.startswith(OBSERVATION_HEADER), header
    assert "These actions already ran for the user's request in this turn" in header, header
    assert "this reply is the final answer" in header, header
    assert "say plainly if an action failed" in header, header
    assert "Do not say that an action listed here cannot be run" in header, header
    for wording in LOOP_WORDING:
        assert wording not in header, f"{wording!r} told a call with no tools to keep running tools: {header!r}"


def test_the_observations_themselves_are_the_same_for_both_calls() -> None:
    inside = _block({"runtime_tool_observations": [COMMAND_OBSERVATION]})
    final = _block({"runtime_tool_observations": [COMMAND_OBSERVATION], "tool_loop_terminal_synthesis": "completed"})

    assert inside[1:] == final[1:]
    assert any("receipt-51ab-cobalt,125" in line for line in final[1:])


def test_a_caller_cannot_mark_its_own_turn_as_the_final_answer() -> None:
    """Only the tool loop writes the mark; an inbound body carrying it is stripped at the door."""
    assert "tool_loop_terminal_synthesis" in RESERVED_TRUST_KEYS
    kept = strip_reserved_trust_keys({"tool_loop_terminal_synthesis": "completed", "chat_note": "hello"})
    assert "tool_loop_terminal_synthesis" not in kept
    assert kept.get("chat_note") == "hello"


# ------------------------------------------------------------------- the real loop marks the call


class _Router:
    """Scripted at the provider seam only: the model reads two files, then asks for the first again.

    Two different reads keep the turn off the one-read direct render, so the loop ends the way the
    real app's did: the repeat guard stops it and one more call writes the answer.
    """

    def __init__(self, paths: list[str]) -> None:
        self.paths = list(paths)
        self.loop_contexts: list[dict] = []
        self.answer_contexts: list[dict] = []

    def resolve_tool_intent(self, **kwargs):
        self.loop_contexts.append(dict(kwargs.get("source_context") or {}))
        path = self.paths[min(len(self.loop_contexts), len(self.paths)) - 1]
        call = SimpleNamespace(intent="workspace.read_file", arguments={"path": path}, call_id=f"c-read-{len(self.loop_contexts)}", name="workspace.read_file")
        return ModelExecutionDecision(
            source="scripted", task_hash="h", used_model=True, confidence=0.8,
            structured_output={"intent": call.intent, "arguments": dict(call.arguments)},
            tool_calls=(call,), validation_state="validated", provider_id="p",
        )

    def resolve(self, **kwargs):
        self.answer_contexts.append(dict(kwargs.get("source_context") or {}))
        return SimpleNamespace(
            output_text="I read orders.csv and notes.txt: receipt-51ab-cobalt (125) settled on Tuesday.",
            provider_id="p", used_model=True, confidence=0.8, trust_score=0.8,
            validation_state="validated", details={}, source="provider_execution",
            model_name="m", provider_name="p", cache_hit=False, candidate_id=None,
            failover_used=False, structured_output=None, task_hash="h",
        )


def test_the_loop_marks_only_the_call_that_writes_the_answer_after_a_blocked_repeat() -> None:
    workspace = pathlib.Path(tempfile.mkdtemp())
    (workspace / "orders.csv").write_text("id,total\nreceipt-51ab-cobalt,125\n", encoding="utf-8")
    (workspace / "notes.txt").write_text("cobalt order settled on Tuesday\n", encoding="utf-8")
    router = _Router(["orders.csv", "notes.txt", "orders.csv"])
    agent = VoolAgent.__new__(VoolAgent)
    agent.memory_router = router
    # Pre-loop gates owned by other lanes, each with its own tests.
    agent._should_keep_ai_first_chat_lane = lambda **_kw: False
    agent._should_run_builder_controller = lambda **_kw: False
    agent._plan_tool_workflow = lambda **_kw: SimpleNamespace(handled=False, stop_after=False, next_payload=None, reason="")
    agent.hive_activity_tracker = None
    agent.public_hive_bridge = None
    events: list[dict] = []
    real_emit = agent._emit_runtime_event

    def _spy(context, **payload):
        events.append(payload)
        return real_emit(context, **payload)

    agent._emit_runtime_event = _spy
    context = {
        "surface": "api", "workspace": str(workspace), "workspace_root": str(workspace),
        "workspace_binding": "project", "project_id": workspace.name,
    }

    result = agent._maybe_execute_model_tool_intent(
        task=SimpleNamespace(task_id="t1"),
        effective_input="read orders.csv and notes.txt and tell me about the cobalt order",
        classification={"task_class": "debugging"},
        interpretation=SimpleNamespace(),
        context_result=SimpleNamespace(),
        persona=SimpleNamespace(),
        session_id=f"openclaw:{uuid.uuid4().hex[:20]}",
        source_context=context,
        surface="api",
    ) or {}

    assert any(event.get("event_type") == "tool_repeat_blocked" for event in events), [e.get("event_type") for e in events]
    assert (result.get("details") or {}).get("loop_stop_reason") == "repeated_tool_request"
    assert len(router.loop_contexts) == 3
    for loop_context in router.loop_contexts:
        assert "tool_loop_terminal_synthesis" not in loop_context
    assert len(router.answer_contexts) == 1
    answer_context = router.answer_contexts[0]
    assert answer_context.get("tool_loop_terminal_synthesis") == "repeated_tool_request"
    # What that call's prompt then says, built from the very context the loop handed it.
    header = _block(answer_context)[0]
    assert "These actions already ran" in header and "Continue the tool actions" not in header, header
    # The caller's own context is not marked: a later turn's loop starts unmarked.
    assert "tool_loop_terminal_synthesis" not in context
