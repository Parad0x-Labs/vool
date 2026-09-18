from __future__ import annotations

import uuid

import pytest

from core.model_orchestration_state import ModelOrchestrationState, ModelOrchestrationStore
from core.task_event_model import build_task_event


def _run(store: ModelOrchestrationStore) -> str:
    suffix = uuid.uuid4().hex
    return store.create_run(
        task_id=f"task-{suffix}",
        turn_id=f"turn-{suffix}",
        subtask_id=f"subtask-{suffix}",
        session_id=f"session-{suffix}",
        lane="LOCAL_DAILY",
        model_id="qwen2.5:7b",
        reason="cheapest_capable_local_model",
    )


def test_local_state_flow_is_persisted_and_versioned() -> None:
    store = ModelOrchestrationStore()
    run_id = _run(store)

    running = store.transition(
        run_id,
        ModelOrchestrationState.LOCAL_RUNNING,
        source_model="qwen2.5:7b",
        target_model="qwen2.5:7b",
        reason="local_execution_started",
        expected_version=0,
    )
    store.transition(
        run_id,
        ModelOrchestrationState.LOCAL_VERIFYING,
        source_model="qwen2.5:7b",
        target_model="qwen2.5:7b",
        reason="candidate_ready",
        verification_result="pending",
        expected_version=running.version,
    )
    store.transition(
        run_id,
        ModelOrchestrationState.TASK_VERIFYING,
        source_model="qwen2.5:7b",
        target_model="qwen2.5:7b",
        reason="independent_verification",
        verification_result="passed",
    )
    final = store.transition(
        run_id,
        ModelOrchestrationState.COMPLETED,
        source_model="qwen2.5:7b",
        target_model="qwen2.5:7b",
        reason="verified_completion",
        verification_result="passed",
    )

    assert final.version == 4
    assert store.current(run_id)["state"] == ModelOrchestrationState.COMPLETED
    with pytest.raises(ValueError):
        store.transition(
            run_id,
            ModelOrchestrationState.LOCAL_RUNNING,
            source_model="qwen2.5:7b",
            target_model="qwen2.5:7b",
            reason="terminal_state_is_immutable",
        )


def test_restart_does_not_resubmit_interrupted_paid_call() -> None:
    store = ModelOrchestrationStore()
    run_id = _run(store)
    transitions = [
        (ModelOrchestrationState.LOCAL_RUNNING, "local_started"),
        (ModelOrchestrationState.ESCALATION_EVALUATING, "bounded_capability_failure"),
        (ModelOrchestrationState.ESCALATION_PROPOSED, "specialist_recommended"),
        (ModelOrchestrationState.AWAITING_USER_APPROVAL, "approval_required"),
        (ModelOrchestrationState.PAID_REQUEST_PREPARING, "approved"),
        (ModelOrchestrationState.PAID_RUNNING, "reservation_committed"),
    ]
    source = "qwen2.5:7b"
    for state, reason in transitions:
        target = "openrouter/specialist" if state.value.startswith("PAID") else source
        store.transition(run_id, state, source_model=source, target_model=target, reason=reason)
        source = target

    assert run_id in store.recover_interrupted_paid_runs()
    recovered = store.current(run_id)
    assert recovered["state"] == ModelOrchestrationState.LOCAL_RESUMED
    assert recovered["active_lane"] == "LOCAL_DAILY"
    assert recovered["active_model"] == ""


def test_terminal_paid_answer_can_verify_and_complete_without_fake_local_handoff() -> None:
    store = ModelOrchestrationStore()
    run_id = _run(store)
    for state in (
        ModelOrchestrationState.LOCAL_RUNNING,
        ModelOrchestrationState.ESCALATION_EVALUATING,
        ModelOrchestrationState.ESCALATION_PROPOSED,
        ModelOrchestrationState.PAID_REQUEST_PREPARING,
        ModelOrchestrationState.PAID_RUNNING,
        ModelOrchestrationState.PAID_VALIDATING,
        ModelOrchestrationState.TASK_VERIFYING,
        ModelOrchestrationState.COMPLETED,
    ):
        store.transition(
            run_id,
            state,
            source_model="provider/paid",
            target_model="provider/paid",
            reason="explicit_paid_answer_flow",
        )

    assert store.current(run_id)["state"] == ModelOrchestrationState.COMPLETED


def test_typed_model_event_carries_call_and_response_identity() -> None:
    typed = build_task_event(
        {
            "event_type": "model.call_completed",
            "message": "Model call completed.",
            "provider_id": "ollama-local:qwen2.5:7b",
            "model_id": "qwen2.5:7b",
            "model_call_id": "model-call-1",
            "response_id": "response-1",
            "cost_class": "free_local",
        }
    )

    assert typed is not None
    assert typed["type"] == "model.call_completed"
    assert typed["model"]["model_call_id"] == "model-call-1"
    assert typed["model"]["response_id"] == "response-1"
    assert typed["cost"]["model_call_id"] == "model-call-1"
