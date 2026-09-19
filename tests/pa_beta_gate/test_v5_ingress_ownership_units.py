"""pa_beta_gate -- revision-5 ingress ownership seams, unit level (no model, no provider, no native app).

Two seams decide what reaches the notes/calendar lanes on the served path:

* ``turn_dispatch._operator_intent_in_user_words`` -- an operator effect carries the words the user
  typed, never the routing normalizer's rewrite of them, and never words the routed text did not come
  from;
* ``persistent_memory.maybe_handle_memory_command`` -- a storage request whose object the operator lane
  claims as an effect is not kept as a memory, while statements stay memories;
* ``operator.models.operator_step_executed`` -- both served doors file an operator step as executed when
  it succeeded OR its lane declared a write, so a refused delivery that wrote its fallback keeps its answer.

``test_v5_served_conversational_journeys.py`` drives the same seams end to end.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.agent_runtime import turn_dispatch
from core.agent_runtime.turn_dispatch import _operator_intent_in_user_words
from core.context_namespace import ensure_chat_namespace
from core.input_normalizer import normalize_user_text
from core.operator.models import READ_ONLY_OPERATOR_KINDS, OperatorActionResult, operator_step_executed
from core.operator.parser import parse_operator_action_intent
from core.persistent_memory import maybe_handle_memory_command
from core.runtime_paths import configure_runtime_home


def _interpretation(typed: str) -> SimpleNamespace:
    return SimpleNamespace(raw_text=typed, normalized_text=normalize_user_text(typed).normalized_text)


def _choose(routed, *, effective_input: str, interpreted, parse=parse_operator_action_intent):
    return _operator_intent_in_user_words(
        routed, effective_input=effective_input, interpreted=interpreted, parse_operator_action_intent_fn=parse,
    )


@pytest.fixture
def memory_home(tmp_path):
    configure_runtime_home(tmp_path / "runtime-home")
    try:
        yield
    finally:
        configure_runtime_home(None)


def test_note_effect_carries_the_typed_words_not_the_normalizer_rewrite():
    typed = 'save a note titled "Crane lift plan" with: [action] ask u to bring the north star gauge Friday at 11:00 Europe/Athens'
    interpreted = _interpretation(typed)
    routed = parse_operator_action_intent(interpreted.normalized_text)
    assert routed is not None and routed.raw_text != typed, "precondition: routing reads a rewritten text"
    assert "[action ]" in routed.raw_text and "you" in routed.raw_text.split(), routed.raw_text
    chosen = _choose(routed, effective_input=interpreted.normalized_text, interpreted=interpreted)
    assert (chosen.kind, chosen.raw_text) == ("save_note", typed)


def test_routed_text_rewritten_after_interpretation_is_never_swapped_for_other_words():
    interpreted = _interpretation('save a note titled "Gate" with: [action] read the gate code from the card to u')
    remainder = 'save a note titled "Gate" with: [action] read the gate code'  # e.g. a redacted remainder
    routed = parse_operator_action_intent(remainder)
    chosen = _choose(routed, effective_input=remainder, interpreted=interpreted)
    assert chosen is routed and "card" not in chosen.raw_text


def test_typed_words_that_parse_as_another_action_leave_routing_in_place():
    interpreted = SimpleNamespace(raw_text="typed words", normalized_text="routed words")
    routed = parse_operator_action_intent('save a note titled "X" with: y')
    other = parse_operator_action_intent("check Tuesday afternoon for a free 30-minute slot")
    assert (routed.kind, other.kind) == ("save_note", "check_availability")
    calls: list[str] = []

    def parse(text):
        calls.append(text)
        return other

    assert _choose(routed, effective_input="routed words", interpreted=interpreted, parse=parse) is routed
    assert calls == ["typed words"]


@pytest.mark.parametrize("interpreted", [
    SimpleNamespace(),
    SimpleNamespace(raw_text="", normalized_text=""),
    SimpleNamespace(raw_text="the routed words", normalized_text="the routed words"),
])
def test_no_typed_words_or_identical_words_cost_no_second_parse(interpreted):
    routed = parse_operator_action_intent('save a note titled "X" with: y')

    def parse(text):
        raise AssertionError(f"unexpected second parse of {text!r}")

    assert _choose(routed, effective_input="the routed words", interpreted=interpreted, parse=parse) is routed


def test_served_door_and_memory_lane_share_one_read_only_classification():
    assert turn_dispatch._READ_ONLY_OPERATOR_KINDS is READ_ONLY_OPERATOR_KINDS
    assert {"check_availability", "find_notes", "show_note", "list_reminders"} <= READ_ONLY_OPERATOR_KINDS
    assert not {"save_note", "propose_calendar_event", "cancel_calendar_event", "schedule_reminder"} & READ_ONLY_OPERATOR_KINDS


@pytest.mark.parametrize("text", [
    'save a note to Apple Notes titled "Night shift handover" with: boiler pressure steady at 1.4 bar',
    'store a note titled "Pump room" with: [check] valve {B} torque 45 Nm',
    "please save meeting notes with: berth 4 reassigned to the ferry",
])
def test_storage_request_the_operator_lane_claims_as_an_effect_is_not_a_memory(memory_home, text):
    intent = parse_operator_action_intent(text)
    assert intent is not None and intent.kind not in READ_ONLY_OPERATOR_KINDS, "precondition: an effect claim"
    chat = "v5-ingress-memory-effects"
    ensure_chat_namespace(chat)
    assert maybe_handle_memory_command(text, session_id=chat) == (False, "")


@pytest.mark.parametrize(("text", "operator_kind"), [
    ("remember that I save a note for the landlord every Friday", "save_note"),  # a statement, not an object
    ("keep in mind I am usually free Tuesday afternoon for a slot", "check_availability"),  # a read's words
    ("save this: my bike rack is spot 12 on level 2", None),  # nothing another lane claims
])
def test_statements_stay_memories_even_when_their_words_parse_as_operator_requests(memory_home, text, operator_kind):
    intent = parse_operator_action_intent(text)
    assert (intent.kind if intent else None) == operator_kind, "precondition: the control exercises its limit"
    chat = "v5-ingress-memory-statements"
    ensure_chat_namespace(chat)
    handled, answer = maybe_handle_memory_command(text, session_id=chat)
    assert handled and answer.startswith(("Locked in.", "I already had that in memory.")), answer


def _result(ok: bool, status: str, details) -> OperatorActionResult:
    return OperatorActionResult(ok=ok, status=status, response_text="", details=details)


@pytest.mark.parametrize(("result", "ran"), [
    (_result(True, "executed", {}), True),
    (_result(True, "reported", {}), True),
    (_result(False, "os_permission_denied", {"files_modified": True, "destination": "workspace_fallback"}), True),
    (_result(False, "os_permission_denied", {"files_modified": False, "reused": True}), False),
    (_result(False, "delivery_in_progress", {"destination_partial": True}), False),
    (_result(False, "invalid_request", {"files_modified": "yes"}), False),  # a declaration is exactly True
    (SimpleNamespace(ok=False, details=None), False),
])
def test_operator_step_ran_only_when_it_succeeded_or_its_lane_declared_a_write(result, ran):
    assert operator_step_executed(result) is ran


def test_model_tool_call_door_files_the_partial_step_as_executed_and_keeps_the_request_failed(monkeypatch):
    from core import execution_truth
    from core.execution.operator_tools import execute_operator_tool

    filed: list[dict] = []
    monkeypatch.setattr(execution_truth, "resolve_turn_key", lambda *_args, **_kwargs: "turn-v5-partial")
    monkeypatch.setattr(execution_truth, "record_execution", lambda **kwargs: filed.append(kwargs) or "fact")
    partial = _result(False, "os_permission_denied", {"files_modified": True, "destination": "workspace_fallback"})
    nothing_written = _result(False, "invalid_request", {})
    for dispatched, ran in ((partial, True), (nothing_written, False)):
        execution = execute_operator_tool(
            "operator.save_note", {}, task_id="task-v5", session_id="session-v5",
            dispatch_operator_action_fn=lambda *_args, _result=dispatched, **_kwargs: _result, source_context={},
        )
        assert (filed[-1]["ok"], filed[-1]["status"], filed[-1]["name"]) == (ran, dispatched.status, "operator.save_note")
        assert execution.ok is False and execution.mode == "tool_failed"
