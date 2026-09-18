from __future__ import annotations

import uuid
from unittest import mock

from core.normalized_provider_result import ProviderErrorClass
from core.runtime_task_events import (
    new_runtime_event_stream_id,
    register_runtime_event_sink,
    unregister_runtime_event_sink,
)
from core.turn_model_call_ledger import (
    record_provider_call,
    record_provider_call_outcome,
    reset_for_tests,
)
from core.web.api.runtime import RuntimeServices, run_agent


def _frontdoor(result_factory: object) -> tuple[dict[str, object], list[dict[str, object]], mock.Mock]:
    reset_for_tests()
    identity = uuid.uuid4().hex
    events: list[dict[str, object]] = []
    stream_id = new_runtime_event_stream_id()
    register_runtime_event_sink(stream_id, lambda event: events.append(dict(event)))
    runtime = RuntimeServices(display_name="VOOL")
    runtime.agent = mock.Mock()
    runtime.agent.run_once.side_effect = result_factory
    try:
        result = run_agent(
            runtime,
            "Explain the requested concept.",
            source_context={
                "request_id": f"request-terminal-truth-{identity}",
                "runtime_session_id": f"session-terminal-truth-{identity}",
                "runtime_checkpoint_id": "runtime-terminal-truth",
                "runtime_event_stream_id": stream_id,
                "turn_id": f"turn-terminal-truth-{identity}",
            },
            workspace_root_provider=lambda: "/tmp",
        )
    finally:
        unregister_runtime_event_sink(stream_id)
    return result, events, runtime.agent


def test_successful_answer_keeps_completed_trace_without_reclosing_checkpoint() -> None:
    result, events, agent = _frontdoor(
        lambda *_args, **_kwargs: {
            "response": "Water boils when its vapor pressure reaches ambient pressure.",
            "model_execution": {
                "source": "provider_response",
                "used_model": True,
            },
        }
    )

    trace = next(event for event in events if event.get("event_type") == "turn.trace_completed")
    assert trace["outcome"] == "completed"
    assert trace["fulfillment_outcome"]["fulfillment_status"] == "fulfilled"
    assert result["fulfillment_outcome"]["fulfillment_status"] == "fulfilled"
    agent._finalize_runtime_checkpoint.assert_not_called()


def test_all_provider_timeouts_are_failed_retryable_in_trace_and_checkpoint() -> None:
    def _timed_out(
        _text: str,
        *,
        source_context: dict[str, object],
        **_kwargs: object,
    ) -> dict[str, object]:
        for index, model in enumerate(("qwen2.5:7b", "qwen3:4b"), start=1):
            call_id = record_provider_call(
                source_context,
                provider_id=f"ollama-local:{model}",
                model_id=model,
                cost_class="local",
                model_call_id=f"model-call-timeout-{index}",
            )
            record_provider_call_outcome(
                source_context,
                call_id,
                outcome="failed",
                error_class=ProviderErrorClass.PROVIDER_TIMEOUT.value,
            )
        return {
            "response": (
                "Both selected local models timed out before producing a usable answer. "
                "No cached text was substituted. Retry the turn."
            ),
            "route": "model_minimal:local",
            "model_execution": {
                "source": "no_provider_available",
                "used_model": False,
            },
        }

    result, events, agent = _frontdoor(_timed_out)

    trace = next(event for event in events if event.get("event_type") == "turn.trace_completed")
    assert trace["outcome"] == "failed"
    assert trace["fulfillment_outcome"] == {
        "fulfillment_status": "failed",
        "failure_stage": "provider_execution",
        "failure_codes": [ProviderErrorClass.PROVIDER_TIMEOUT.value.lower()],
        "retryable": True,
        "origin_task_id": "",
        "origin_checkpoint_id": "",
        "original_request_hash": "",
    }
    assert result["response"].startswith("Both selected local models timed out")
    agent._finalize_runtime_checkpoint.assert_called_once_with(
        mock.ANY,
        status="completed",
        final_response=result["response"],
        outcome=trace["fulfillment_outcome"],
    )


def test_empty_canonical_response_is_failed_retryable_not_completed() -> None:
    result, events, agent = _frontdoor(
        lambda *_args, **_kwargs: {
            "response": "",
            "route": "model_minimal:qwen2.5:7b",
            "model_execution": {
                "source": "provider_response",
                "used_model": True,
            },
        }
    )

    trace = next(event for event in events if event.get("event_type") == "turn.trace_completed")
    assert result["response"] == ""
    assert trace["outcome"] == "failed"
    assert trace["fulfillment_outcome"]["failure_codes"] == ["empty_canonical_output"]
    agent._finalize_runtime_checkpoint.assert_called_once()


def test_conductor_success_plus_unavailable_and_failed_siblings_is_partial() -> None:
    def _partial_conductor(
        _text: str,
        *,
        source_context: dict[str, object],
        **_kwargs: object,
    ) -> dict[str, object]:
        from core.runtime_task_events import emit_runtime_event

        emit_runtime_event(
            source_context,
            event_type="conductor_plan_completed",
            message="Conductor plan completed with partial fulfillment.",
            details={
                "turn_id": str(source_context.get("turn_id") or ""),
                "receipt": {
                    "schema": "conductor_plan_receipt_v1",
                    "node_count": 3,
                    "succeeded_count": 1,
                    "nodes": [
                        {"state": "succeeded"},
                        {"state": "unavailable"},
                        {"state": "failed"},
                    ],
                }
            },
        )
        return {"response": "One clause was answered; two named clauses were not."}

    result, events, agent = _frontdoor(
        _partial_conductor
    )

    trace = next(event for event in events if event.get("event_type") == "turn.trace_completed")
    assert trace["outcome"] == "partially_fulfilled"
    assert result["fulfillment_outcome"]["failure_stage"] == "conductor_execution"
    assert result["fulfillment_outcome"]["retryable"] is True
    agent._finalize_runtime_checkpoint.assert_called_once()


def test_honest_deterministic_refusal_is_failed_but_not_retryable_provider_failure() -> None:
    result, events, agent = _frontdoor(
        lambda *_args, **_kwargs: {
            "response": "I did not delete the file. No tool was run.",
            "task_outcome": "failed",
            "response_class": "task_failed_user_safe",
            "details": {"failure_code": "tool_not_available", "retryable": False},
        }
    )

    trace = next(event for event in events if event.get("event_type") == "turn.trace_completed")
    assert trace["outcome"] == "failed"
    assert result["fulfillment_outcome"]["failure_stage"] == "task_execution"
    assert result["fulfillment_outcome"]["failure_codes"] == ["tool_not_available"]
    assert result["fulfillment_outcome"]["retryable"] is False
    # The action lane already closed this checkpoint as failed.  The front door must not replace
    # that transport status merely to persist the same task truth a second time.
    agent._finalize_runtime_checkpoint.assert_not_called()


# --- Node accounting describes the conductor, not the turn -----------------------------------
#
# Live evidence (set5-15, 2026-08-13, cumulative Sets 5+6+7 on the converged candidate): a plan
# reported `conductor_no_nodes_served` while the turn committed a complete, correct answer --
# every semantic contract passed and ONLY the terminal trace said `failed`. Another lane had
# answered. Reporting a total failure over a delivered answer is the same untruth as reporting
# success over a broken turn, pointed the other way.


def _conductor_payload(node_count: int, succeeded: int, response: str) -> dict[str, object]:
    return {
        "response": response,
        "conductor_receipt": {"node_count": node_count, "succeeded_count": succeeded},
    }


def test_an_unserved_plan_behind_a_real_answer_is_not_a_total_failure() -> None:
    from core.runtime_task_outcome import FulfillmentStatus, terminal_fulfillment_outcome

    outcome = terminal_fulfillment_outcome(
        _conductor_payload(2, 0, "The currency of North Korea is the North Korean won.")
    )

    assert outcome.fulfillment_status is FulfillmentStatus.FULFILLED


def test_an_unserved_plan_with_no_answer_is_still_a_failure() -> None:
    """The guard must not become a way for an empty turn to claim success."""

    from core.runtime_task_outcome import FulfillmentStatus, terminal_fulfillment_outcome

    outcome = terminal_fulfillment_outcome(_conductor_payload(2, 0, "   "))

    assert outcome.fulfillment_status is FulfillmentStatus.FAILED
    assert outcome.failure_stage == "conductor_execution"


def test_a_partial_plan_still_reports_partial_even_with_an_answer() -> None:
    """A partial plan is the conductor saying part of the request is genuinely missing."""

    from core.runtime_task_outcome import FulfillmentStatus, terminal_fulfillment_outcome

    outcome = terminal_fulfillment_outcome(_conductor_payload(3, 1, "Only one clause was answered."))

    assert outcome.fulfillment_status is FulfillmentStatus.PARTIALLY_FULFILLED
    assert tuple(outcome.failure_codes) == ("conductor_nodes_unserved",)
