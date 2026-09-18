from __future__ import annotations

import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest import mock

import pytest
import requests

from adapters.base_adapter import ModelRequest
from adapters.local_subprocess_adapter import LocalSubprocessAdapter
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core.normalized_provider_result import ProviderErrorClass
from core.provider_call_deadline import (
    PROVIDER_DEADLINE_KEY,
    bind_provider_deadline,
    copy_deadline_to_request_metadata,
)
from core.runtime_task_events import (
    new_runtime_event_stream_id,
    register_runtime_event_sink,
    unregister_runtime_event_sink,
)
from core.turn_model_call_ledger import (
    begin_turn,
    fail_pending_provider_calls,
    record_provider_call,
    record_provider_call_outcome,
    reset_for_tests,
    turn_call_accounting,
)
from core.web.api.runtime import RuntimeServices, run_agent


def _cloud_adapter(*, configured_timeout: float = 180.0) -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_name="openrouter-byok",
            provider_id="openrouter-byok:nvidia/ultra",
            model_name="nvidia/ultra",
            metadata={"runtime_family": "openai-compatible"},
            runtime_config={
                "base_url": "https://openrouter.ai/api/v1",
                "api_path": "/chat/completions",
                "timeout_seconds": configured_timeout,
                "supports_json_mode": True,
            },
        )
    )


#: How much wall clock provider cleanup may add BEYOND the read timeout the socket was granted.
#: Measured on an M-series laptop: 0.008-0.012s in steady state, 0.107s on the first call through
#: the adapter (one-time init, which the warm-up below removes from the measurement). Set well above
#: both so a loaded CI runner does not fail a correct implementation, and far below the 180s
#: configured provider timeout, which is the escape this test exists to prevent.
_CLEANUP_OVERHEAD_BUDGET_S = 0.35


@pytest.mark.parametrize("streaming", [False, True])
def test_blocking_transport_is_clamped_below_its_enclosing_turn_deadline(
    monkeypatch: pytest.MonkeyPatch,
    streaming: bool,
) -> None:
    """Sabotage: a 180s provider timeout may not escape a much shorter turn owner."""

    def _request() -> ModelRequest:
        """A fresh turn deadline per call.

        The deadline is an ABSOLUTE monotonic instant and is therefore single-use: reusing one
        request for the warm-up and the timed call makes the second call start already past the
        deadline, and the adapter raises ProviderCallDeadlineExceededError instead of exercising
        the transport at all -- a green-looking test that never reaches the behaviour it names.
        """

        context = bind_provider_deadline(
            {},
            turn_deadline_monotonic=time.monotonic() + 0.50,
            cleanup_margin_seconds=0.20,
            reason="sabotage conductor deadline",
        )
        return ModelRequest(
            task_kind="chat",
            prompt="block until the transport timeout",
            messages=[{"role": "user", "content": "block until the transport timeout"}],
            metadata=copy_deadline_to_request_metadata({}, context),
        )

    observed_read_timeouts: list[float] = []

    def _blocking_post(*_args: object, **kwargs: object) -> object:
        timeout = kwargs["timeout"]
        read_timeout = float(timeout[1] if isinstance(timeout, tuple) else timeout)
        observed_read_timeouts.append(read_timeout)
        # Act like a socket that remains silent until requests' read deadline.  This is deliberately
        # real wall time: a test that merely inspects kwargs would not prove a stuck call returns.
        time.sleep(read_timeout + 0.01)
        raise requests.Timeout("sabotage provider read timed out")

    monkeypatch.setattr("adapters.openai_compatible_adapter.requests.post", _blocking_post)

    def _drive() -> None:
        request = _request()
        with pytest.raises(requests.Timeout, match="sabotage provider"):
            if streaming:
                list(_cloud_adapter().stream_text_task(request))
            else:
                _cloud_adapter().run_text_task(request)

    # Warm the path before timing it. The first call through this adapter pays one-time lazy
    # initialisation, and the measurement below is about CLEANUP, not about import cost.
    _drive()
    observed_read_timeouts.clear()

    started = time.monotonic()
    _drive()
    elapsed = time.monotonic() - started

    assert observed_read_timeouts
    assert 0.001 <= observed_read_timeouts[0] < 0.34
    # Measured against the sleep this test DELIBERATELY performs, not against absolute wall clock.
    #
    # The old form asserted `elapsed < 0.50` while the clamped read timeout is ~0.30 and the fake
    # socket sleeps `read_timeout + 0.01` -- leaving ~0.19s for everything else, on any machine
    # under any load. It went red on CI at 0.742s while the clamp assertion above passed, which is
    # the tell: the transport was bounded correctly and the TEST was measuring machine speed.
    #
    # Measured on this machine: cleanup beyond the sleep is 0.008-0.012s in steady state, and
    # 0.107s on the very first call (the reason for the warm-up above). The budget is far above
    # the real cost and far below the 180s configured provider timeout this test exists to prove
    # cannot escape -- so it still fails loudly if cleanup ever starts consuming the turn window.
    overhead = elapsed - observed_read_timeouts[0]
    assert overhead < _CLEANUP_OVERHEAD_BUDGET_S, (
        "provider cleanup consumed the enclosing turn's terminal window: "
        f"{overhead:.3f}s beyond the {observed_read_timeouts[0]:.3f}s the socket was allowed"
    )


def test_provider_call_outcome_is_single_assignment_after_turn_terminalization() -> None:
    reset_for_tests()
    context: dict[str, object] = {"request_id": "terminal-race-1"}
    begin_turn(context)
    call_id = record_provider_call(
        context,
        provider_id="openrouter-byok:nvidia/ultra",
        model_id="nvidia/ultra",
        cost_class="free_cloud",
        model_call_id="model-call-terminal-race-1",
    )

    closed = fail_pending_provider_calls(
        context,
        error_class=ProviderErrorClass.PROVIDER_TIMEOUT.value,
    )

    assert closed == [
        {
            "call_id": call_id,
            "model_call_id": "model-call-terminal-race-1",
            "provider_id": "openrouter-byok:nvidia/ultra",
            "model_id": "nvidia/ultra",
            "cost_class": "free_cloud",
            "call_role": "",
        }
    ]
    assert record_provider_call_outcome(context, call_id, outcome="completed") is False
    assert fail_pending_provider_calls(
        context,
        error_class=ProviderErrorClass.PROVIDER_TIMEOUT.value,
    ) == []
    # This test owns the OUTCOME-ASSIGNMENT fields. `verification_receipts` and
    # `usage_details` aggregate whatever receipts this process already holds for the
    # provider/model (sibling tests in this file warm the nvidia/ultra lane and register
    # receipts), so exact-dict equality was order-dependent -- verified failing on a
    # pristine HEAD archive run of this file alone (2026-09-18). Compare the owned subset.
    accounting = turn_call_accounting(context)
    assert {
        key: accounting.get(key)
        for key in (
            "calls",
            "completed_calls",
            "failed_calls",
            "pending_calls",
            "failed_error_classes",
            "providers",
            "lanes",
            "models",
            "tools",
            "served_usage",
        )
    } == {
        "calls": 1,
        "completed_calls": 0,
        "failed_calls": 1,
        "pending_calls": 0,
        "failed_error_classes": [ProviderErrorClass.PROVIDER_TIMEOUT.value],
        "providers": ["openrouter-byok:nvidia/ultra"],
        "lanes": ["cloud"],
        "models": ["nvidia/ultra"],
        "tools": [],
        "served_usage": {},
    }


def test_blocking_local_subprocess_is_terminated_at_enclosing_provider_deadline() -> None:
    context = bind_provider_deadline(
        {},
        turn_deadline_monotonic=time.monotonic() + 0.45,
        cleanup_margin_seconds=0.20,
        reason="subprocess sabotage deadline",
    )
    request = ModelRequest(
        task_kind="chat",
        prompt="block",
        metadata=copy_deadline_to_request_metadata({}, context),
    )
    adapter = LocalSubprocessAdapter(
        SimpleNamespace(
            provider_name="blocking-subprocess",
            provider_id="blocking-subprocess",
            model_name="blocking-subprocess-model",
            runtime_config={
                "command": [sys.executable, "-c", "import time; time.sleep(5)"],
                "timeout_seconds": 180.0,
            },
        )
    )

    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        adapter.run_text_task(request)

    assert time.monotonic() - started < 0.70


def test_frontdoor_emits_call_failure_before_terminal_trace_and_overrides_stale_zero() -> None:
    reset_for_tests()
    events: list[dict[str, object]] = []
    stream_id = new_runtime_event_stream_id()
    register_runtime_event_sink(stream_id, lambda event: events.append(dict(event)))
    runtime = RuntimeServices(display_name="VOOL")
    runtime.agent = mock.Mock()

    def _run_once(
        _text: str,
        *,
        source_context: dict[str, object],
        **_kwargs: object,
    ) -> dict[str, object]:
        from core.runtime_task_events import emit_runtime_event

        record_provider_call(
            source_context,
            provider_id="openrouter-byok:nvidia/ultra",
            model_id="nvidia/ultra",
            cost_class="free_cloud",
            model_call_id="model-call-frontdoor-timeout-1",
        )
        emit_runtime_event(
            source_context,
            event_type="model.call_started",
            message="Model call started.",
            details={
                "request_id": "request-frontdoor-timeout-1",
                "model_call_id": "model-call-frontdoor-timeout-1",
                "provider_id": "openrouter-byok:nvidia/ultra",
                "model_id": "nvidia/ultra",
            },
        )
        # This is the historical failure shape: a fallback result claimed zero while the provider
        # worker remained pending.  finalize_turn_trace must reconcile the invocation ledger.
        return {
            "response": "Fallback answer.",
            "route": "deterministic:fallback",
            "model_calls": 0,
        }

    runtime.agent.run_once.side_effect = _run_once
    try:
        result = run_agent(
            runtime,
            "Use the provider, then fall back.",
            source_context={
                "request_id": "request-frontdoor-timeout-1",
                "runtime_event_stream_id": stream_id,
            },
            workspace_root_provider=lambda: "/tmp",
        )
    finally:
        unregister_runtime_event_sink(stream_id)

    sequence = [str(event.get("event_type") or "") for event in events]
    assert sequence.index("model.call_started") < sequence.index("model.call_failed")
    assert sequence.index("model.call_failed") < sequence.index("turn.trace_completed")
    failed = next(event for event in events if event.get("event_type") == "model.call_failed")
    trace = next(event for event in events if event.get("event_type") == "turn.trace_completed")
    assert failed["model_call_id"] == "model-call-frontdoor-timeout-1"
    assert failed["error_class"] == ProviderErrorClass.PROVIDER_TIMEOUT.value
    assert failed["terminalized_by"] == "turn_runtime"
    assert trace["model_calls"] == 1
    assert result["model_calls"] == 1
    assert result["model_call_accounting"]["pending_calls"] == 0


def test_fast_path_proof_names_timed_out_provider_attempt_not_model_not_used() -> None:
    from tests.test_agent_runtime_fast_command_surface import _build_agent

    reset_for_tests()
    agent = _build_agent()
    events: list[dict[str, object]] = []
    context: dict[str, object] = {"request_id": "fast-proof-timeout-1"}
    begin_turn(context)
    record_provider_call(
        context,
        provider_id="openrouter-byok:nvidia/ultra",
        model_id="nvidia/ultra",
        cost_class="free_cloud",
    )
    fail_pending_provider_calls(
        context,
        error_class=ProviderErrorClass.PROVIDER_TIMEOUT.value,
    )

    with mock.patch.object(
        agent,
        "_emit_runtime_event",
        side_effect=lambda source_context, **payload: events.append(dict(payload)),
    ):
        agent._fast_path_result(
            session_id="session-fast-timeout",
            user_input="fall back",
            response="Fallback answer.",
            confidence=0.8,
            source_context=context,
            reason="smalltalk_fast_path",
        )

    proof = next(event for event in events if event["event_type"] == "model_lane_proof")
    assert proof["fallback_reason"] == "model_attempt_timed_out_before_fast_path_answer"
    assert proof["provider_id"] == "runtime-fast-path"
    assert proof["model_id"] == ""
    assert proof["actual_adapter_provider_id"] == ""
    assert proof["actual_adapter_model_id"] == ""
    assert proof["attempted"] == ["openrouter-byok:nvidia/ultra"]
    assert "model_not_used" not in proof["fallback_reason"]


def test_nested_owner_can_only_shorten_provider_deadline() -> None:
    now = time.monotonic()
    outer = bind_provider_deadline(
        {}, turn_deadline_monotonic=now + 20.0, cleanup_margin_seconds=3.0
    )
    nested_later = bind_provider_deadline(
        outer, turn_deadline_monotonic=now + 40.0, cleanup_margin_seconds=3.0
    )
    nested_earlier = bind_provider_deadline(
        outer, turn_deadline_monotonic=now + 10.0, cleanup_margin_seconds=3.0
    )

    assert nested_later[PROVIDER_DEADLINE_KEY] == outer[PROVIDER_DEADLINE_KEY]
    assert nested_earlier[PROVIDER_DEADLINE_KEY] < outer[PROVIDER_DEADLINE_KEY]


def test_conductor_deadline_cancels_queued_siblings_instead_of_starting_orphans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.conductor import scheduler
    from core.conductor.graph import build_graph
    from core.conductor.node import ConductorNode
    from core.conductor.planner import ConductorPlan
    from core.conductor.registry import NodeContext

    nodes = (
        ConductorNode(
            node_id="calculation:first",
            operation="calculation",
            request_text="What is 1 + 1?",
            arguments={"expression": "1 + 1"},
        ),
        ConductorNode(
            node_id="calculation:second",
            operation="calculation",
            request_text="What is 2 + 2?",
            arguments={"expression": "2 + 2"},
        ),
    )
    plan = ConductorPlan(
        plan_id="queued-orphan-sabotage",
        original_request="What is 1 + 1? Also what is 2 + 2?",
        graph=build_graph(nodes),
        clause_count=2,
    )
    release = threading.Event()
    started_nodes: list[str] = []
    real_run_one = scheduler._run_one

    def _block_first(node: object, *args: object, **kwargs: object) -> object:
        started_nodes.append(str(getattr(node, "node_id", "")))
        if len(started_nodes) == 1:
            release.wait(timeout=1.0)
        return real_run_one(node, *args, **kwargs)

    monkeypatch.setattr(scheduler, "_run_one", _block_first)
    outcomes = scheduler.run_conductor_plan(
        plan,
        context=NodeContext(),
        max_workers=1,
        plan_deadline_s=0.04,
    )
    release.set()
    time.sleep(0.05)

    assert len(started_nodes) == 1, "a queued sibling started after its turn was terminal"
    assert len(outcomes) == 2
    assert all(outcome.failure_reason for outcome in outcomes)


@pytest.mark.parametrize("source,details,error_class,expected", [
    ("selected_model_blocked", {"model_was_attempted": True, "block_reason": "provider_timeout"}, "PROVIDER_TIMEOUT", "failed"),
    ("selected_model_blocked", {"model_was_attempted": False, "block_reason": "paid_call_not_authorized"}, "", "blocked"),
    ("model_unavailable", {"reason": "requested_model_unresolvable"}, "", "blocked"),
    ("autopilot_blocked", {"reason": "policy_denied"}, "", "blocked"),
    ("routing_identity_missing", {}, "", "blocked"),
    ("explicit_heavy_lane_failed", {"attempted": ["free-cloud:test"]}, "EMPTY_PROVIDER_RESPONSE", "failed"),
    ("provider", {}, "PROVIDER_TIMEOUT", "fulfilled"),
])
def test_pinned_provider_terminal_truth_reaches_trace_and_proof(source, details, error_class, expected):
    from core.proof_projection import STATE_INCOMPLETE, STATE_VERIFIED, build_turn_proof
    from tests.test_proof_projection import _bind_turn, _insert_finalization

    reset_for_tests()
    session = f"pin-terminal-session-{source}-{expected}"
    request = f"pin-terminal-request-{source}-{expected}"
    turn = f"pin-terminal-turn-{source}-{expected}"
    answer = "Delivered answer." if expected == "fulfilled" else "The selected model could not answer."
    runtime = RuntimeServices(display_name="VOOL")
    runtime.agent = mock.Mock()
    events = []
    stream_id = new_runtime_event_stream_id()
    register_runtime_event_sink(stream_id, lambda event: events.append(dict(event)))

    def run_once(_text, *, source_context, **_kwargs):
        if error_class:
            call_id = record_provider_call(source_context, provider_id="free-cloud:test",
                model_id="pinned-test-model", cost_class="free_cloud")
            record_provider_call_outcome(source_context, call_id, outcome="failed", error_class=error_class)
            if expected == "fulfilled":
                recovered = record_provider_call(source_context, provider_id="free-cloud:test",
                    model_id="pinned-test-model", cost_class="free_cloud")
                record_provider_call_outcome(source_context, recovered, outcome="completed")
        return {"response": answer, "model_execution": {"source": source, "details": details}}

    runtime.agent.run_once.side_effect = run_once
    _bind_turn(session, request, answer)
    _insert_finalization(request, turn, answer)
    try:
        result = run_agent(runtime, "Answer using my pinned model.", source_context={
            "request_id": request, "runtime_session_id": session, "session_id": session,
            "turn_key": turn, "runtime_event_stream_id": stream_id,
        }, workspace_root_provider=lambda: "/tmp")
    finally:
        unregister_runtime_event_sink(stream_id)
    outcome = result["fulfillment_outcome"]
    assert outcome["fulfillment_status"] == expected
    trace = next(event for event in events if event["event_type"] == "turn.trace_completed")
    assert trace["fulfillment_outcome"] == outcome
    if expected != "fulfilled":
        assert outcome["failure_stage"] == "provider_execution"
        assert outcome["retryable"] is (expected == "failed")
    proof = build_turn_proof(session_id=session, request_id=request)
    assert proof["state"] == (STATE_VERIFIED if expected == "fulfilled" else STATE_INCOMPLETE)
