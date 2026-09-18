"""Provider completion evidence must survive API finalization and proof projection."""

from pathlib import Path
from unittest import mock

import pytest

from core.proof_projection import STATE_INCOMPLETE, STATE_VERIFIED, build_turn_proof
from core.runtime_task_events import emit_runtime_event
from core.runtime_task_outcome import output_validation_outcome, terminal_fulfillment_outcome
from core.web.api.runtime import RuntimeServices, run_agent
from tests.test_proof_projection import _bind_turn, _emit_turn_event, _insert_finalization


def control(*, incomplete=True, has_content=True):
    return {"provider_completion": {
        "initial": {"incomplete": True, "has_content": True, "reasons": ["provider_finish_reason:length"]},
        "final": {"incomplete": incomplete, "has_content": has_content,
                  "reasons": ["provider_finish_reason:length"] if incomplete else []},
    }}


@pytest.mark.parametrize("text", [
    "Platinum | 2.780 oz / 8\n\n(Incomplete: this answer stopped before it finished. Ask again for the rest.)",
    "Warehouse A has 17 crates.",
])
def test_terminal_provider_evidence_overrules_complete_looking_text_and_stale_success(text):
    result = terminal_fulfillment_outcome({
        "response": text, "response_control": control(),
        "fulfillment_outcome": {"fulfillment_status": "fulfilled"},
    })
    assert result.fulfillment_status.value == "partially_fulfilled"
    assert result.retryable


def test_no_answer_is_failed_and_successful_repair_is_not_penalized():
    assert output_validation_outcome(control(has_content=False))["fulfillment_status"] == "failed"
    assert output_validation_outcome(control(incomplete=False)) is None


@pytest.mark.parametrize("incomplete", [True, False])
def test_real_proof_store_uses_terminal_provider_state_not_just_content_hash(incomplete):
    suffix = str(incomplete)
    session, request, turn = "completion-s-" + suffix, "completion-r-" + suffix, "completion-t-" + suffix
    answer = "Warehouse A has 17 crates."
    _bind_turn(session, request, answer)
    _insert_finalization(request, turn, answer)
    _emit_turn_event(session, turn, request, "turn.trace_completed", {"response_control": control(incomplete=incomplete)})
    proof = build_turn_proof(session_id=session, request_id=request)
    assert proof["compact"]["state"] == (STATE_INCOMPLETE if incomplete else STATE_VERIFIED)


@pytest.mark.parametrize("text", ["Platinum | 2.780 oz / 8", "The first delivery contains 17 crates."])
def test_frontdoor_recovers_control_from_receipt_before_deciding_fulfillment(text):
    runtime = RuntimeServices(display_name="VOOL")
    runtime.agent = mock.Mock()
    suffix = "metal" if text.startswith("Platinum") else "delivery"

    def execute(_text, *, source_context, **kwargs):
        emit_runtime_event(source_context, event_type="model.call_completed", message="Model answered.", details={
            "request_id": source_context["request_id"],
            "provider_manifest_id": "manifest-" + suffix,
            "response_control": control(),
        })
        # The copied model context was lost; the durable receipt still owns the verdict.
        return {"response": text, "model_calls": 1}

    runtime.agent.run_once.side_effect = execute
    question = (
        (Path(__file__).parent / "fixtures/portfolio_owner_format.txt").read_text()
        if suffix == "metal" else
        "Two depots receive 170 crates. Allocate 10% to depot A and the remainder to depot B. Report both rows."
    )
    result = run_agent(runtime, question, source_context={
        "request_id": "completion-frontdoor-" + suffix,
        "runtime_session_id": "completion-frontdoor-session-" + suffix,
        "client_turn_id": "completion-frontdoor-turn-" + suffix,
    }, workspace_root_provider=lambda: "/tmp")
    assert result["fulfillment_outcome"]["fulfillment_status"] == "partially_fulfilled"
