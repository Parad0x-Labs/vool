"""Paid reasoning models must come back with the artifact, not a dead turn.

Two measured incidents on the live daemon (2026-09-17, pinned `z-ai/glm-5.3-flash` on the
OpenRouter byok lane, build 4acb7596):

1. Every generation of a pinned BUILD 400'd with the provider's own typed error -- "Reasoning
   is mandatory for this endpoint and cannot be disabled" -- because the builder's artifact
   policy sends `reasoning_mode="disabled"`. The build refused, and the actionable first line
   of the refusal (which quotes the provider error) was then rewritten by the evidence binder
   into a non-sequitur about model attestation, because "provider said" matched its
   attestation pattern.

2. A pinned CHAT turn on a longer prompt came back empty: the model spent the whole output
   ceiling reasoning (3,848-token ceiling, 3,964 reasoning tokens, finish "length"), and the
   empty-response retry was FREE-LANE ONLY -- a paid pin had no recovery at all.

The repairs, proven here against the real router seam (the paid gate, the invoke loop and the
settle/release paths all real; the wire is the only stand-in):

* the builder re-asks ONCE with reasoning permitted and a widened ceiling when the provider's
  own error says reasoning cannot be disabled;
* the invoke loop retries an exhaustion-shaped empty ONCE with a widened ceiling on ANY cost
  class when the manifest declares reasoning -- unknown empties on paid lanes still refuse;
* the evidence binder leaves the runtime's own typed refusals alone.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest


@pytest.fixture()
def paid_home(tmp_path, monkeypatch):
    import os

    from storage.db import configure_default_db_path

    configure_default_db_path(os.path.join(tmp_path, "effect_budget.db"))
    from core import effect_budget

    effect_budget.reset_effect_budget_process_state()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    from core import runtime_paths

    runtime_paths.configure_runtime_home(home)
    from storage.migrations import run_migrations

    run_migrations()
    yield home
    effect_budget.reset_effect_budget_process_state()
    configure_default_db_path(None)


PAID_MODEL = "synth/reasoning-mandatory-model"

REASONING_MANDATORY_400 = (
    '400 Client Error: None for url: https://openrouter.ai/api/v1/chat/completions — provider '
    'said: {"error":{"message":"Reasoning is mandatory for this endpoint and cannot be '
    'disabled.","code":400,"metadata":{"provider_name":null}}}'
)


def _paid_manifest() -> SimpleNamespace:
    return SimpleNamespace(
        provider_id="openrouter-byok:" + PAID_MODEL,
        model_name=PAID_MODEL,
        adapter_type="openai_compatible",
        source_type="http",
        runtime_config={"base_url": "https://synth-paid-lane.invalid/v1"},
        metadata={
            "cost_class": "paid_cloud",
            "supported_parameters": ["reasoning"],
        },
        provider_name="openrouter-byok",
        enabled=True,
    )


def _pinned_ctx(workspace: str) -> dict[str, object]:
    return {
        "workspace": workspace,
        "workspace_root": workspace,
        "operating_mode": "auto",
        "surface": "api",
        "_owner_local": True,
        "session_id": "reasoning-recovery",
        "runtime_session_id": "reasoning-recovery",
        "turn_id": "turn-reasoning-" + uuid.uuid4().hex[:8],
        "requested_model": PAID_MODEL,
        "model_selection": "pin",
    }


class _GenerationRecorder:
    """Stands in for the build: one generation in, an honest file report out."""

    def __init__(self) -> None:
        self.generated: list[str] = []

    def __call__(self, *, request, target_rel, source_context, generate_fn, run_tool_fn, **kwargs):
        text = generate_fn("write banner.py")
        self.generated.append(text)
        return SimpleNamespace(
            target_dir=target_rel,
            files_written=["banner.py"] if text.strip() else [],
            files_skipped=[],
            file_lines={},
            tests_ran=False,
            tests_passed=False,
            fix_rounds=0,
            expected_test_outcome="unspecified",
            subject_paths=(),
            test_returncode=None,
            test_command="",
            proof_failed=False,
            test_output="",
            handled=True,
            error="",
            scope_kind="open",
            authorized_paths=(),
            paths_refused=[],
            commands_refused=[],
        )


def _run_build(agent, manifest, tmp_path):
    from core.agent_runtime.builder import controller as agent_builder_controller

    task = SimpleNamespace(task_id="task-reasoning-build", prompt_tokens=100, max_output_tokens=512)
    recorder = _GenerationRecorder()
    with (
        mock.patch.object(agent.memory_router, "_requested_model_manifest", return_value=manifest),
        mock.patch.object(agent.memory_router, "_dispatch_binding_is_current", return_value=True),
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch(
            "core.agent_runtime.builder.app_builder.build_app_from_spec",
            side_effect=recorder,
        ),
        mock.patch(
            "core.agent_runtime.builder.app_builder.render_app_build_response",
            return_value="Built the file.",
        ),
    ):
        return (
            agent_builder_controller._run_model_build(
                agent,
                task=task,
                effective_input="build me a single-file python tool that prints the generated banner",
                classification={"task_class": "system_design"},
                interpretation=None,
                session_id="reasoning-recovery",
                source_context=_pinned_ctx(str(tmp_path / "ws")),
                target={"root_dir": "generated/tool", "platform": "generic", "language": "python"},
                load_active_persona_fn=lambda *a, **k: None,
            ),
            recorder,
        )


def _agent(agent_invoke: Any) -> Any:
    """A real VoolAgent whose router delegates `_invoke_manifest` to the test double."""
    from apps.vool_agent import VoolAgent

    agent = VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")
    agent.memory_router._invoke_manifest = agent_invoke  # type: ignore[method-assign]
    return agent


def test_the_builder_reasks_once_with_reasoning_permitted(paid_home, tmp_path) -> None:
    """The 400 that killed every generation of the incident build: the provider's own error
    says reasoning cannot be disabled, so the SAME generation is re-asked once with reasoning
    permitted and a widened ceiling -- and the artifact lands."""
    from core.agent_runtime.builder import pinned_generation

    requests_seen: list[Any] = []

    def invoke(*, manifest, request, output_mode, task, source_context):
        requests_seen.append(request)
        if len(requests_seen) == 1:
            return None, None, REASONING_MANDATORY_400
        return None, SimpleNamespace(output_text="def banner(): return 'BANNER'"), None

    agent = _agent(invoke)
    manifest = _paid_manifest()
    result, recorder = _run_build(agent, manifest, tmp_path)

    assert len(requests_seen) == 2, "the generation was not re-asked after the 400"
    first, retry = requests_seen
    assert str(first.reasoning_mode) == "disabled"
    assert str(retry.reasoning_mode) != "disabled", "the re-ask still disabled reasoning"
    assert retry.max_output_tokens > first.max_output_tokens, "the re-ask did not widen the ceiling"
    assert recorder.generated == ["def banner(): return 'BANNER'"]
    assert PAID_MODEL in str(result.get("response") or "")
    assert result.get("success") is not False


def test_a_provider_400_that_is_not_reasoning_mandatory_still_refuses(paid_home, tmp_path) -> None:
    """Refusal control: an unrelated 400 does not trigger the re-ask -- the typed retry is for
    the provider's reasoning-mandate error, not a generic failure absorber."""
    requests_seen: list[Any] = []

    def invoke(*, manifest, request, output_mode, task, source_context):
        requests_seen.append(request)
        return None, None, "400 Client Error: invalid model id"

    agent = _agent(invoke)
    result, recorder = _run_build(agent, _paid_manifest(), tmp_path)
    assert len(requests_seen) == 1
    assert recorder.generated == [""]
    assert result.get("success") is False


def _exhaustion_error() -> Any:
    from core.normalized_provider_result import EmptyProviderResponseError, empty_reply_diagnostics

    body = {
        "id": "gen-exhaustion",
        "model": PAID_MODEL,
        "choices": [
            {
                "finish_reason": "length",
                "message": {"content": "", "reasoning": "thinking " * 500},
            }
        ],
        "usage": {
            "prompt_tokens": 1789,
            "completion_tokens": 3848,
            "total_tokens": 5637,
            "completion_tokens_details": {"reasoning_tokens": 3964},
        },
    }
    error = EmptyProviderResponseError("provider response has no usable text and no tool call")
    error.diagnostics = empty_reply_diagnostics(body, max_tokens_sent=3848)  # type: ignore[attr-defined]
    return error


def _agent_with_adapter_lane(paid_home, adapter_behavior) -> Any:
    """A real VoolAgent whose router serves the synthetic paid lane from a stub ADAPTER, so
    the real invoke loop -- paid gate, empty-retry closure, settle/release -- runs."""
    from adapters.base_adapter import ModelResponse
    from apps.vool_agent import VoolAgent

    agent = VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")
    manifest = _paid_manifest()
    wire: list[int] = []

    def fake_build_adapter(built_manifest):
        class _Adapter(SimpleNamespace):
            pass

        adapter = _Adapter(provider_id=manifest.provider_id, model_name=manifest.model_name)

        def run_text_task(request, **kwargs):
            wire.append(int(getattr(request, "max_output_tokens", 0) or 0))
            outcome = adapter_behavior(len(wire))
            if isinstance(outcome, Exception):
                raise outcome
            return ModelResponse(
                output_text=str(outcome),
                provider_id=manifest.provider_id,
                model_name=manifest.model_name,
                finish_reason="stop",
                usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            )

        adapter.run_text_task = run_text_task
        return adapter

    agent.memory_router.registry.build_adapter = fake_build_adapter  # type: ignore[method-assign]
    return agent, manifest, wire


def _invoke_chat(agent, manifest, tmp_path):
    """Reserve like the router's own pick lane does, then cross the real invoke loop."""
    from core.paid_call_reservation import reserve_owner_pick_paid_call

    task = SimpleNamespace(task_id="task-chat-retry", prompt_tokens=100, max_output_tokens=512)
    base_ctx = _pinned_ctx(str(tmp_path / "ws"))
    authorization = reserve_owner_pick_paid_call(
        manifest=manifest,
        task=task,
        source_context={**base_ctx, "subtask_id": "chat_pick"},
        task_kind="chat_conversation",
        call_role="answer_generation",
        denial={},
    )
    assert authorization is not None, "the chat reservation itself failed"
    ctx = {
        **base_ctx,
        "authorized_paid_call": authorization,
        "model_call_role": "answer_generation",
    }
    with (
        mock.patch.object(agent.memory_router, "_requested_model_manifest", return_value=manifest),
        mock.patch.object(agent.memory_router, "_dispatch_binding_is_current", return_value=True),
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
    ):
        return agent.memory_router._invoke_manifest(
            manifest=manifest,
            request=_chat_request(),
            output_mode="plain_text",
            task=task,
            source_context=ctx,
        )


def _chat_request():
    from adapters.base_adapter import ModelRequest

    return ModelRequest(
        task_kind="chat_conversation",
        prompt="answer the question",
        max_output_tokens=3848,
        output_mode="plain_text",
    )


def test_an_exhausted_paid_reasoning_model_gets_one_widened_retry(paid_home, tmp_path) -> None:
    """The incident's chat shape, through the real invoke loop: the first attempt spends the
    whole ceiling reasoning; the retry carries a WIDER ceiling on the PAID lane and lands."""
    agent, manifest, wire = _agent_with_adapter_lane(
        paid_home,
        lambda index: _exhaustion_error() if index == 1 else "The table with the answer.",
    )
    with mock.patch(
        "core.model_pricing.estimate_call_usd",
        return_value=SimpleNamespace(usd=0.001),
    ):
        _adapter, response, error = _invoke_chat(agent, manifest, tmp_path)
    assert response is not None and error is None, error
    assert "The table with the answer." in str(getattr(response, "output_text", ""))
    assert len(wire) == 2, "the exhausted paid call was not retried"
    assert wire[1] == wire[0] + 2048, f"the retry did not widen the ceiling: {wire}"


def test_an_unknown_empty_on_a_paid_lane_still_refuses(paid_home, tmp_path) -> None:
    """Money-discipline control: an empty reply with NO exhaustion evidence on a paid lane is
    not retried -- unknown empties do not buy a second spend."""
    from core.normalized_provider_result import EmptyProviderResponseError

    agent, manifest, wire = _agent_with_adapter_lane(
        paid_home,
        lambda index: EmptyProviderResponseError(
            "provider response has no usable text and no tool call"
        ),
    )
    _adapter, response, error = _invoke_chat(agent, manifest, tmp_path)
    assert response is None and error is not None
    assert len(wire) == 1, "an unexplained paid empty was retried"


def test_the_binder_leaves_typed_refusals_alone() -> None:
    """The copy corruption: the pinned-build refusal quotes the provider's error, and the
    binder's attestation pattern read "provider said" as a model attestation claim."""
    from core.agent_runtime.evidence_claim_binder import bind_response_to_evidence
    from core.grounding_publication import is_typed_refusal_text

    refusal = (
        "I can't build with `z-ai/glm-5.3-flash` on this turn: it returned nothing usable for "
        f"this build ({REASONING_MANDATORY_400}).\n\nNothing was written."
    )
    assert is_typed_refusal_text(refusal)

    class _Evidence(SimpleNamespace):
        pass

    evidence = _Evidence(
        turn_id="turn-1",
        provenance=SimpleNamespace(has_provider_attestation=False, runtime_selected_model="x", requested_model="x"),
    )
    outcome = bind_response_to_evidence(refusal, evidence)  # type: ignore[arg-type]
    assert outcome.checked is False
    assert outcome.response == refusal


def test_a_streamed_exhaustion_is_typed_and_widenable() -> None:
    """The streamed twin of the incident: `_consume_provider_stream` raises its empty with
    diagnostics built from the stream's own terminal facts, so the exhaustion classifier —
    and with it the widened re-ask — can see a streamed exhaustion exactly as a buffered one."""
    from core.memory_first_router import MemoryFirstRouter
    from core.normalized_provider_result import (
        EMPTY_REPLY_OUTPUT_BUDGET_EXHAUSTED,
        EmptyProviderResponseError,
        classify_empty_reply,
    )

    class _Stream:
        def __iter__(self):
            class _Chunk(SimpleNamespace):
                pass

            yield _Chunk(delta_text="", usage={"prompt_tokens": 100, "completion_tokens": 3848,
                                               "total_tokens": 3948,
                                               "completion_tokens_details": {"reasoning_tokens": 3964}},
                        raw_event=None, finish_reason="length")
            yield _Chunk(delta_text="", usage=None, raw_event=None, finish_reason="length")

    class _Adapter(SimpleNamespace):
        pass

    class _Manifest(SimpleNamespace):
        pass

    manifest = _Manifest(
        provider_id="openrouter-byok:m",
        model_name="m",
        metadata={"confidence_baseline": 0.65},
    )
    router = object.__new__(MemoryFirstRouter)
    request = _chat_request()
    with pytest.raises(EmptyProviderResponseError) as caught:
        router._stream_response(
            adapter=_Adapter(stream_text_task=lambda r: _Stream()),
            manifest=manifest,
            request=request,
            source_context={},
        )
    diagnostics = getattr(caught.value, "diagnostics", None)
    assert isinstance(diagnostics, dict)
    assert classify_empty_reply(diagnostics) == EMPTY_REPLY_OUTPUT_BUDGET_EXHAUSTED
    assert diagnostics["reasoning_present"] is True


def test_observed_reasoning_teaches_the_next_sizing_without_invention() -> None:
    """The UsePod-lane repair: a feed that publishes no capability still teaches the shared
    owner from the provider's own terminal facts. The first exhausted call records the
    observation; the SECOND sizing for that exact lane/model carries the thinking reserve —
    and a DIFFERENT model on the same lane is untouched (no blind ceiling growth)."""
    from core.output_budget_policy import (
        LaneCapability,
        manifest_declares_reasoning,
        note_observed_reasoning,
        observed_reasoning,
        reset_observed_reasoning,
        resolve_output_budget,
        OutputBudgetIntent,
    )

    reset_observed_reasoning()
    try:
        lane = LaneCapability(
            cost_class="paid_cloud",
            context_window=131072,
            max_output_tokens=32768,
            thinking_capable=False,  # the feed publishes nothing: sizing cannot know yet
            runtime_family="openai-compatible",
        )
        manifest = _paid_manifest()
        # An undeclared lane is the UsePod shape: no supported_parameters, no marker name.
        manifest.metadata = {"cost_class": "paid_cloud"}
        manifest.model_name = "gemini-3-8-flash"
        assert manifest_declares_reasoning(manifest) is False

        intent = OutputBudgetIntent(
            output_mode="plain_text", base_tokens=1800, floor=1800, ceiling=0, reason="t"
        )
        first = resolve_output_budget(intent, lane)
        assert first.reserve_applied == 0, "no capability published: no reserve invented"

        # The provider's terminal fact arrives (the invoke loop records it on the typed
        # exhaustion; the builder on the typed mandate refusal).
        note_observed_reasoning(manifest.provider_id, manifest.model_name)
        assert observed_reasoning(manifest) is True
        assert manifest_declares_reasoning(manifest) is True

        capable_lane = LaneCapability(
            cost_class="paid_cloud",
            context_window=131072,
            max_output_tokens=32768,
            thinking_capable=True,  # manifest_declares_reasoning now feeds the capability read
            runtime_family="openai-compatible",
        )
        second = resolve_output_budget(intent, capable_lane)
        assert second.tokens == first.tokens + 2048
        assert second.reserve_applied == 2048

        # A different model on the same lane is untouched.
        other = _paid_manifest()
        other.metadata = {"cost_class": "paid_cloud"}
        other.model_name = "another-plain-model"
        assert manifest_declares_reasoning(other) is False
    finally:
        reset_observed_reasoning()


def test_an_undeclared_lane_still_gets_the_widened_retry_on_typed_exhaustion(paid_home, tmp_path) -> None:
    """The gate no longer depends on a capability DECLARATION: the exhaustion's own typed
    diagnostics (reasoning present) are the evidence — the UsePod marketplace publishes no
    supported_parameters, and its first exhausted chat turn must still recover."""
    agent, manifest, wire = _agent_with_adapter_lane(
        paid_home,
        lambda index: _exhaustion_error() if index == 1 else "Recovered answer.",
    )
    # Simulate the undeclared lane: strip any reasoning declaration the fixture manifest carries.
    manifest.metadata = {k: v for k, v in dict(manifest.metadata or {}).items() if k != "supported_parameters"}
    with mock.patch(
        "core.model_pricing.estimate_call_usd",
        return_value=SimpleNamespace(usd=0.001),
    ):
        _adapter, response, error = _invoke_chat(agent, manifest, tmp_path)
    assert response is not None and error is None, error
    assert "Recovered answer." in str(getattr(response, "output_text", ""))
    assert len(wire) == 2 and wire[1] == wire[0] + 2048, wire


def test_a_widening_that_exceeds_the_held_reservation_refuses_before_dispatch(paid_home, tmp_path) -> None:
    """Money-discipline control for the re-ask itself: the widened ceiling is projected against
    the reservation the turn actually holds, and a re-ask that would spend beyond it never
    dispatches — the typed exhaustion surfaces instead of an unauthorized spend."""
    agent, manifest, wire = _agent_with_adapter_lane(
        paid_home,
        lambda index: _exhaustion_error() if index == 1 else "should not be reached",
    )
    # The RESERVATION is made at cheap pricing so it succeeds; only the widened re-ask's
    # projection is dear — exactly the authority gap under test.
    pricing = {"calls": 0}

    def _estimate(*args, **kwargs):
        pricing["calls"] += 1
        return SimpleNamespace(usd=0.001 if pricing["calls"] == 1 else 99.0)

    with mock.patch("core.model_pricing.estimate_call_usd", side_effect=_estimate):
        _adapter, response, error = _invoke_chat(agent, manifest, tmp_path)
    assert response is None and error is not None
    assert len(wire) == 1, "an unauthorized widened re-ask was dispatched anyway"
