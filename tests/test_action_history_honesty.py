from __future__ import annotations

from unittest import mock

from core.agent_runtime.fast_paths_utility import action_history_honesty_fast_path
from core.persistent_memory import append_conversation_event

_PROMPT = "Have you created or edited any files in this conversation so far?"


def test_fresh_session_action_history_is_deterministic_and_model_free(make_agent) -> None:
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("action history should not load context")  # type: ignore[attr-defined]
    agent.memory_router.resolve = mock.Mock(
        side_effect=AssertionError("provably empty action history should not invoke the model"),
    )

    result = agent.run_once(
        _PROMPT,
        session_id_override="fresh-action-history",
        source_context={"surface": "api", "platform": "api", "allow_remote_fetch": False},
    )

    assert result["route"] == "action_history_honesty"
    assert result["model_execution"]["used_model"] is False
    assert result["fast_path_hit"] is True
    assert result["route_reason"] == "empty_session_action_history"
    assert result["prompt_eval_count"] == 0
    assert result["model_calls"] == 0
    assert result["web_calls"] == 0
    assert "no file operation has been recorded" in result["response"].lower()
    agent.memory_router.resolve.assert_not_called()


def test_public_did_you_edit_files_wording_is_deterministic_and_model_free(make_agent) -> None:
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("action history should not load context")  # type: ignore[attr-defined]
    agent.memory_router.resolve = mock.Mock(
        side_effect=AssertionError("provably empty action history should not invoke the model"),
    )

    result = agent.run_once(
        "Did you edit any files in this conversation?",
        session_id_override="fresh-action-history-did-edit",
        source_context={"surface": "api", "platform": "api", "allow_remote_fetch": False},
    )

    assert result["route"] == "action_history_honesty"
    assert result["model_calls"] == 0
    assert "no file operation has been recorded" in result["response"].lower()
    agent.memory_router.resolve.assert_not_called()


def test_prior_session_activity_prevents_deterministic_no() -> None:
    session_id = "prior-action-history"
    append_conversation_event(
        session_id=session_id,
        user_input="Create notes.txt.",
        assistant_output="Created file `notes.txt`.",
        source_context={"surface": "api", "platform": "api"},
    )

    assert action_history_honesty_fast_path(
        _PROMPT,
        source_surface="api",
        session_id=session_id,
        source_context={},
    ) is None


def test_attached_assistant_history_prevents_deterministic_no() -> None:
    assert action_history_honesty_fast_path(
        _PROMPT,
        source_surface="api",
        session_id="attached-action-history",
        source_context={
            "conversation_history": [
                {"role": "assistant", "content": "Created file `notes.txt`."},
            ]
        },
    ) is None


def test_unrelated_capability_question_does_not_use_action_history_fast_path() -> None:
    assert action_history_honesty_fast_path(
        "Can you create or edit files?",
        source_surface="api",
        session_id="capability-question",
        source_context={},
    ) is None
