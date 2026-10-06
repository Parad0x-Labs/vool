from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from core.context_retrieval import (
    _session_scope_key,
    reset_retrieval_telemetry,
    update_retrieval_telemetry,
)
from core.web.api.runtime import RuntimeServices, run_agent


class _FakeAgent:
    ResponseClass = SimpleNamespace(GENERIC_CONVERSATION="generic_conversation")

    def __init__(self, *, session_id: str, sanitized_response: str | None = None) -> None:
        self.session_id = session_id
        self.sanitized_response = sanitized_response
        self.sanitized_inputs: list[str] = []

    def run_once(self, user_text: str, *, session_id_override=None, source_context=None):
        update_retrieval_telemetry(
            capsule_mode="distilled",
            selected_facts=["- exact code: RCPT-9182"],
            source_session_ids=[_session_scope_key(self.session_id)],
            model_calls=1,
        )
        return {"response": "model answer", "confidence": 0.8}

    def _sanitize_user_chat_text(self, text: str, *, response_class):
        self.sanitized_inputs.append(text)
        return self.sanitized_response if self.sanitized_response is not None else text


def test_run_agent_revalidates_capsule_override_after_chat_sanitizer(monkeypatch, tmp_path) -> None:
    session_id = "runtime-vet-session"
    agent = _FakeAgent(session_id=session_id)
    runtime = RuntimeServices(agent=agent, runtime_home=str(tmp_path))
    monkeypatch.setattr("core.web.api.runtime._memory_recall_response", lambda *args, **kwargs: None)
    monkeypatch.setattr("core.web.api.runtime.schedule_memory_extraction", lambda *args, **kwargs: None)

    result = run_agent(
        runtime,
        "what is the exact receipt code?",
        session_id=session_id,
        source_context={"surface": "api", "platform": "api", "allow_remote_fetch": False},
        workspace_root_provider=lambda: str(tmp_path),
    )

    assert agent.sanitized_inputs == ["RCPT-9182"]
    assert result["response"] == "RCPT-9182"
    assert result["response_control"]["mode"] == "capsule_exact"
    assert result["response_control"]["original_response_excerpt"] == "model answer"


def test_run_agent_keeps_model_answer_when_sanitized_override_fails_final_validator(
    monkeypatch,
    tmp_path,
) -> None:
    session_id = "runtime-reject-session"
    agent = _FakeAgent(
        session_id=session_id,
        sanitized_response="According to https://example.com, RCPT-9182",
    )
    runtime = RuntimeServices(agent=agent, runtime_home=str(tmp_path))
    monkeypatch.setattr("core.web.api.runtime._memory_recall_response", lambda *args, **kwargs: None)
    monkeypatch.setattr("core.web.api.runtime.schedule_memory_extraction", lambda *args, **kwargs: None)

    result = run_agent(
        runtime,
        "what is the exact receipt code?",
        session_id=session_id,
        source_context={"surface": "api", "platform": "api", "allow_remote_fetch": False},
        workspace_root_provider=lambda: str(tmp_path),
    )

    assert agent.sanitized_inputs == ["RCPT-9182"]
    assert result["response"] == "model answer"
    assert "response_control" not in result


def test_grounded_turn_returns_session_authorized_exact_capsule_before_model(
    make_agent,
    monkeypatch,
) -> None:
    session_id = "pre-model-exact-session"
    agent = make_agent()
    context_result = agent.context_loader.load.return_value

    def load_context(**_kwargs):
        return context_result

    def retrieve_context(**_kwargs):
        reset_retrieval_telemetry()
        update_retrieval_telemetry(
            capsule_mode="distilled",
            selected_facts=["- recalled identifier: 8829145"],
            source_session_ids=[_session_scope_key(session_id)],
            model_calls=0,
        )
        return [], "structured_dialogue_memory"

    agent.context_loader.load.side_effect = load_context
    monkeypatch.setattr(
        "core.bootstrap_context.canonical_runtime_transcript",
        retrieve_context,
    )
    agent.memory_router.resolve = mock.Mock(
        side_effect=AssertionError("authorized exact recall must not invoke the model"),
    )

    result = agent.run_once(
        "What operator node ID should I remember?",
        session_id_override=session_id,
        source_context={"surface": "api", "platform": "api", "allow_remote_fetch": False},
    )

    assert result["response"] == "8829145"
    assert result["route"] == "capsule_exact_pre_model"
    assert result["model_execution"]["used_model"] is False
    assert result["response_control"]["mode"] == "capsule_exact"
    agent.memory_router.resolve.assert_not_called()
