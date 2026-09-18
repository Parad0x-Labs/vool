from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from apps.vool_agent import VoolAgent, maybe_handle_memory_command
from core.agent_runtime import memory_runtime


def _build_agent() -> VoolAgent:
    return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")


def test_maybe_handle_memory_fast_path_facade_delegates_to_extracted_module() -> None:
    agent = _build_agent()

    with mock.patch(
        "core.agent_runtime.memory_runtime.maybe_handle_memory_fast_path",
        return_value={"response": "memory reply"},
    ) as maybe_handle_memory_fast_path:
        result = agent._maybe_handle_memory_fast_path(
            "remember this",
            session_id="memory-session",
            source_context={"surface": "openclaw"},
        )

    assert result == {"response": "memory reply"}
    maybe_handle_memory_fast_path.assert_called_once_with(
        agent,
        "remember this",
        session_id="memory-session",
        source_context={"surface": "openclaw"},
        access_policy=None,
        maybe_handle_memory_command_fn=maybe_handle_memory_command,
    )


def test_maybe_handle_memory_fast_path_uses_app_level_memory_command_override() -> None:
    agent = _build_agent()
    access_policy = mock.sentinel.access_policy

    with mock.patch("core.agent_runtime.agent.maybe_handle_memory_command", return_value=(True, "remembered")) as memory_command_mock, mock.patch.object(
        agent,
        "_fast_path_result",
        return_value={"response": "remembered", "response_class": "utility_answer"},
    ) as fast_path_result:
        result = agent._maybe_handle_memory_fast_path(
            "remember this",
            session_id="memory-session",
            source_context={"surface": "openclaw"},
            access_policy=access_policy,
        )

    assert result == {"response": "remembered", "response_class": "utility_answer"}
    memory_command_mock.assert_called_once_with(
        "remember this",
        session_id="memory-session",
        access_policy=access_policy,
        source_context={"surface": "openclaw"},
    )
    fast_path_result.assert_called_once_with(
        session_id="memory-session",
        user_input="remember this",
        response="remembered",
        confidence=0.93,
        source_context={"surface": "openclaw"},
        reason="memory_command",
    )


def test_maybe_handle_memory_fast_path_uses_app_level_companion_override() -> None:
    agent = _build_agent()

    with mock.patch("core.agent_runtime.agent.maybe_handle_memory_command", return_value=(False, "")), mock.patch.object(
        agent,
        "_maybe_handle_companion_memory_fast_path",
        return_value={"response": "companion reply"},
    ) as companion_memory_fast_path:
        result = agent._maybe_handle_memory_fast_path(
            "continue that project we talked about",
            session_id="memory-session",
            source_context={"surface": "openclaw"},
        )

    assert result == {"response": "companion reply"}
    companion_memory_fast_path.assert_called_once_with(
        "continue that project we talked about",
        session_id="memory-session",
        source_context={"surface": "openclaw"},
    )


def test_companion_memory_fast_path_defers_web0_to_canonical_grounding() -> None:
    agent = _build_agent()

    result = agent._maybe_handle_companion_memory_fast_path(
        "tell me about web0",
        session_id="memory-session",
        source_context={"surface": "api", "allow_remote_fetch": False},
    )

    assert result is None


def test_web0_builder_fast_path_returns_local_builder_url() -> None:
    agent = _build_agent()

    result = agent._maybe_handle_web0_builder_fast_path(
        "build a website on web0",
        session_id="web0-builder-session",
        source_context={"surface": "openclaw"},
    )

    assert result is not None
    assert "/templates/editor/?" in result["response"]
    assert "payload=" in result["response"]
    assert "save this as" not in result["response"].lower()
    assert "arweave/mainnet" in result["response"].lower()
    assert result["model_execution"] == {"source": "fast_path", "used_model": False}


def test_model_final_response_text_facade_matches_extracted_module() -> None:
    agent = _build_agent()
    model_execution = SimpleNamespace(output_text="", structured_output={"summary": "summary from structure"})

    assert agent._model_final_response_text(model_execution) == memory_runtime.model_final_response_text(model_execution)


def test_chat_surface_model_final_text_hides_cache_and_memory_hits() -> None:
    agent = _build_agent()

    assert agent._chat_surface_model_final_text(SimpleNamespace(source="exact_cache_hit", output_text="cached")) == ""
    assert agent._chat_surface_model_final_text(SimpleNamespace(source="memory_hit", output_text="remembered")) == ""


def test_model_unavailable_source_names_the_requested_model_and_refuses_to_substitute() -> None:
    agent = _build_agent()
    model_execution = SimpleNamespace(
        source="model_unavailable",
        details={"requested_model": "totally-unknown-model-xyz", "reason": "requested_model_unresolvable"},
    )

    response = memory_runtime.chat_surface_honest_degraded_response(agent, model_execution, user_input="")

    assert "totally-unknown-model-xyz" in response
    assert "different model" in response


def test_set5_repeated_length_failure_names_model_and_bounded_repair() -> None:
    agent = _build_agent()
    model_execution = SimpleNamespace(
        source="provider_execution",
        model_name="qwen3:4b",
        validation_state="contract_failed",
        details={
            "response_constraint": {
                "response_control": {
                    "retry_attempted": True,
                    "provider_completion": {
                        "initial": {
                            "at_output_limit": True,
                            "reasons": ["provider_finish_reason:length"],
                        },
                        "final": {
                            "at_output_limit": True,
                            "reasons": ["provider_finish_reason:length", "output_cap:dangling_tail"],
                        },
                    },
                }
            }
        },
    )

    response = memory_runtime.chat_surface_honest_degraded_response(
        agent,
        model_execution,
        user_input='Define RUB, BAM, and CAD. Do not trigger forex tools.',
    )

    assert "qwen3:4b" in response
    assert "output limit" in response
    assert "bounded repair" in response
    assert "usable model response" not in response


def test_set5_timeout_chain_is_typed_model_specific_and_secret_safe() -> None:
    agent = _build_agent()
    secret = "sk-should-never-appear"
    model_execution = SimpleNamespace(
        source="no_provider_available",
        details={
            "attempted": ["ollama-local:qwen3:4b", "ollama-local:qwen2.5:7b"],
            "attempted_error_reasons": [
                f"HTTPConnectionPool 127.0.0.1 timed out Authorization={secret}",
                "ollama-local:qwen2.5:7b: model_load_gated_low_memory -- 4.3 GB free",
            ],
            "attempt_timings": [
                {
                    "model_id": "qwen3:4b",
                    "error": f"Read timed out Authorization={secret}",
                },
                {
                    "model_id": "qwen2.5:7b",
                    "error": "model_load_gated_low_memory -- 4.3 GB free",
                },
            ],
        },
    )

    response = memory_runtime.chat_surface_honest_degraded_response(
        agent, model_execution, user_input="Explain how to boil water."
    )

    assert "qwen3:4b" in response and "timed out" in response
    assert "qwen2.5:7b" in response and "memory-safety" in response
    assert secret not in response
    assert "127.0.0.1" not in response
    assert "live model response" not in response


def test_unknown_failure_shape_keeps_existing_honest_fallback() -> None:
    agent = _build_agent()
    response = memory_runtime.chat_surface_honest_degraded_response(
        agent,
        SimpleNamespace(source="no_provider_available", details={"attempted": []}),
        user_input="hello",
    )

    assert "live model response" in response


def test_chat_surface_honest_degraded_response_facade_delegates_to_extracted_module() -> None:
    agent = _build_agent()

    with mock.patch(
        "core.agent_runtime.memory_runtime.chat_surface_honest_degraded_response",
        return_value="delegated degraded response",
    ) as chat_surface_honest_degraded_response:
        result = agent._chat_surface_honest_degraded_response(
            SimpleNamespace(source="memory_hit"),
            user_input="what do you remember about this",
        )

    assert result == "delegated degraded response"
    chat_surface_honest_degraded_response.assert_called_once_with(
        agent,
        mock.ANY,
        user_input="what do you remember about this",
        interpretation=None,
    )


def test_a_wallet_paid_call_whose_answer_never_arrived_is_reported_as_paid_with_an_unknown_result() -> None:
    agent = _build_agent()
    reason = "usepod_x402_paid_result_unknown:transfer_ended_without_response dispatch=sent_outcome_unknown"
    model_execution = SimpleNamespace(
        source="selected_model_blocked",
        details={"attempted": ["usepod:meridian-synth-chat"], "reason": reason, "block_reason": reason, "model_was_attempted": True, "requested_model": "usepod:meridian-synth-chat", "ranked_candidates": []},
    )

    response = memory_runtime.chat_surface_honest_degraded_response(agent, model_execution, user_input="")

    assert "`usepod:meridian-synth-chat` was paid from your wallet" in response
    assert "result is unknown" in response and "no refund is assumed" in response and "Resume" in response
    # neither the single-candidate sentence nor the could-not-run sentence: the model ran and was paid
    assert "only model this turn was allowed" not in response and "could not run" not in response


def test_an_ordinary_usepod_transport_failure_keeps_the_selected_model_wording() -> None:
    agent = _build_agent()
    reason = "usepod_transport:transfer_ended_without_response dispatch=sent_outcome_unknown"
    model_execution = SimpleNamespace(
        source="selected_model_blocked",
        details={"attempted": [], "reason": reason, "block_reason": reason, "model_was_attempted": True, "requested_model": "usepod:meridian-synth-chat", "ranked_candidates": []},
    )

    response = memory_runtime.chat_surface_honest_degraded_response(agent, model_execution, user_input="")

    assert "paid from your wallet" not in response and "could not run this turn" in response


def test_provider_rate_limit_is_visible_without_exposing_raw_error() -> None:
    reason = "429 Client Error: Provider returned error for url: https://example.invalid/?secret=hidden"
    execution = SimpleNamespace(source="selected_model_blocked", details={
        "requested_model": "openrouter:example:free", "model_was_attempted": True,
        "block_reason": reason, "attempted": ["openrouter:example:free"],
    })
    response = memory_runtime.chat_surface_honest_degraded_response(_build_agent(), execution, user_input="")
    assert "HTTP 429" in response and "rate limit" in response
    assert "hidden" not in response and "example.invalid" not in response
    assert "nothing was charged" not in response
    execution.details["model_was_attempted"] = False
    response = memory_runtime.chat_surface_honest_degraded_response(_build_agent(), execution, user_input="")
    assert "HTTP 429" not in response


def test_fallback_rate_limit_is_visible_and_other_failure_is_not_mislabelled() -> None:
    execution = SimpleNamespace(source="no_provider_available", details={
        "attempted": ["openrouter:example:free"],
        "attempted_error_reasons": ["HTTP 429 Provider returned error"],
    })
    response = memory_runtime.chat_surface_honest_degraded_response(_build_agent(), execution, user_input="")
    assert "HTTP 429" in response
    execution.details["attempted_error_reasons"] = ["empty response"]
    response = memory_runtime.chat_surface_honest_degraded_response(_build_agent(), execution, user_input="")
    assert "429" not in response
