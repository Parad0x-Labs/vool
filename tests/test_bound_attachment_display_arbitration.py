"""Bound reference material must not be mistaken for this host's display."""
from types import SimpleNamespace

import pytest

from core.agent_runtime import fast_paths_machine
from core.execution.constants import machine_display_intent
from core.execution.planner import plan_tool_workflow

QUESTION = "What is on screen at 0:05 in the attached video?"
BOUND_CONTEXT = {
    "runtime_session_id": "display-arbitration",
    "attachment_turn_id": "display-arbitration-turn",
    "external_evidence": [{
        "origin": "chat_attachment",
        "attachment_id": "attachment-issued-by-ingress",
        "name": "clip.mp4",
        "kind": "artifact",
        "reader_format": "video",
    }],
}


def test_bound_material_defers_host_inference_without_phrase_rules():
    for question in (QUESTION, "What resolution does the screen have?", "Describe the display?"):
        assert machine_display_intent(question, source_context=BOUND_CONTEXT) is None


@pytest.mark.parametrize("context", [
    {},
    {"attachments": ["clip.mp4"]},
    {"attachment_turn_id": "unbound"},
    {"external_evidence": BOUND_CONTEXT["external_evidence"]},
    {"attachment_turn_id": "turn", "external_evidence": [{"origin": "web", "attachment_id": "x"}]},
])
def test_names_or_unbound_evidence_do_not_suppress_real_host_inspection(context):
    assert machine_display_intent("What is my screen resolution?", source_context=context) == "machine.display_inspect"


def test_direct_machine_door_does_not_execute_display_for_bound_material(monkeypatch):
    calls = []
    monkeypatch.setattr(fast_paths_machine, "_recall_machine_read", lambda _session: None)
    monkeypatch.setattr(fast_paths_machine, "execute_authorized_runtime_tool", lambda intent, *a, **kw: calls.append(intent))
    result = fast_paths_machine.maybe_handle_direct_machine_read_request(
        SimpleNamespace(), QUESTION, session_id="display-arbitration", source_surface="api",
        source_context=BOUND_CONTEXT,
    )
    assert result is None
    assert "machine.display_inspect" not in calls


def test_workflow_planner_uses_the_same_bound_material_authority():
    decision = plan_tool_workflow(
        user_text=QUESTION, task_class="chat_conversation", executed_steps=[], source_context=BOUND_CONTEXT,
    )
    assert (decision.next_payload or {}).get("intent") != "machine.display_inspect"


def test_workflow_planner_preserves_genuine_host_question():
    decision = plan_tool_workflow(
        user_text="What is my screen resolution?", task_class="chat_conversation", executed_steps=[], source_context={},
    )
    assert decision.next_payload == {"intent": "machine.display_inspect", "arguments": {}}


def test_explicit_host_question_with_unrelated_attachment_remains_actionable():
    question = "What is my screen resolution?"
    assert machine_display_intent(question, source_context=BOUND_CONTEXT, whole_turn=True) == "machine.display_inspect"
    decision = plan_tool_workflow(
        user_text=question, task_class="chat_conversation", executed_steps=[], source_context=BOUND_CONTEXT,
    )
    assert decision.next_payload == {"intent": "machine.display_inspect", "arguments": {}}


def test_mixed_material_and_host_request_retains_host_step_without_fast_answer(monkeypatch):
    question = "Describe the attached video; what is my screen resolution?"
    calls = []
    monkeypatch.setattr(fast_paths_machine, "_recall_machine_read", lambda _session: None)
    monkeypatch.setattr(fast_paths_machine, "execute_authorized_runtime_tool", lambda intent, *a, **kw: calls.append(intent))
    result = fast_paths_machine.maybe_handle_direct_machine_read_request(
        SimpleNamespace(), question, session_id="display-arbitration", source_surface="api",
        source_context=BOUND_CONTEXT,
    )
    assert result is None
    assert "machine.display_inspect" not in calls
    decision = plan_tool_workflow(
        user_text=question, task_class="chat_conversation", executed_steps=[], source_context=BOUND_CONTEXT,
    )
    assert decision.next_payload == {"intent": "machine.display_inspect", "arguments": {}}
