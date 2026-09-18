"""Machine scaffolding must never be shown to a person as an answer.

Measured 2026-08-01. An operator asked for a regression test and the reply began:

    ["security.py", "test_security.py", "README.md"]import hmac
    import secrets
    from typing import Optional

That is one raw model completion. The build lane's file-list prompt asks for "ONLY a JSON array";
the model produced the array and kept generating the file bodies in the same completion. Path
extraction failed on it, the build reported `handled=False`, and the turn fell out of the build lane
into a passthrough point that printed the completion verbatim.

The parser is fixed (see tests/test_app_builder.py). This is the backstop for whatever goes wrong
next: whatever the upstream failure, internal payload is not an answer.

Deliberately narrow -- each shape below is something a human answer never opens with, and the cost
of a false positive is one failover to another model, never a wrong answer delivered.
"""
from __future__ import annotations

import pytest

from core.tool_call_dialects import looks_like_internal_payload

# Verbatim shape from the live failure.
LEAKED_BUILD_PAYLOAD = (
    '["security.py", "test_security.py", "README.md"]import hmac\n'
    "import secrets\n"
    "from typing import Optional\n"
)


@pytest.mark.parametrize(
    "text",
    [
        LEAKED_BUILD_PAYLOAD,
        # A reasoning block the model opened and never closed: the whole reply is monologue and the
        # answer never arrives. This is the 7,786-token turn that was cut off mid-sentence.
        "<think>Okay, so the user wants me to audit this. Let me look at the decompress loop. But wait",
        # A model narrating a tool result it never received.
        '<function_results>File: api/apache/x.py</function_results><result>{"content": []}</result>',
    ],
)
def test_internal_payload_is_recognised(text: str) -> None:
    assert looks_like_internal_payload(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "Here is your answer: the bug is the bare except at line 145.",
        # A reply that IS only a clean list -- some turns legitimately answer with one.
        '["a.py", "b.py"]',
        # Reasoning correctly delimited, with the real answer after it.
        "<think>weighing the options</think>\n\nThe silent truncation is the highest-risk bug.",
        # Prose that merely mentions the machinery.
        "The build returns a JSON array of filenames, then writes each file.",
        "",
    ],
)
def test_a_real_answer_survives(text: str) -> None:
    """A false positive costs the operator an answer they should have had."""

    assert looks_like_internal_payload(text) is False


def test_the_chat_surface_refuses_it() -> None:
    """Wired, not merely callable -- the guard has to sit on the path that printed it."""

    import inspect

    from core.agent_runtime import chat_surface

    source = inspect.getsource(chat_surface)
    assert "looks_like_internal_payload" in source, "the guard is not wired into the chat surface"


# ---------------------------------------------------------------------------------------
# The guard branches, driven -- not grepped for. A source-string assertion proves the call
# exists; these prove the branch actually changes what the operator receives.
# ---------------------------------------------------------------------------------------

from types import SimpleNamespace
from unittest import mock

# A monologue the reasoning-lead guard condemns (same shape as the live 2026-08-01 failure;
# asserted against `suppress_internal_reasoning_leak` in tests/test_reasoning_monologue_leak.py).
OKAY_SO_THE_USER = (
    "Okay, so the user wants me to explain how the memory-first router picks a model. Let me think "
    "about what I know about this. There is a ranking step and then a candidate loop. Hmm, but "
    "wait, I should check whether the mux path is separate. Let me re-read their message once more "
    "to be sure I have the"
)


def test_the_shared_wrapper_refuses_both_shapes() -> None:
    """`internal_payload_or_monologue` is the one question every passthrough point asks: the
    payload shapes AND the reasoning-monologue shapes, so no lane can drift to checking only one."""

    from core.agent_runtime.response import internal_payload_or_monologue

    assert internal_payload_or_monologue(LEAKED_BUILD_PAYLOAD) is True
    assert internal_payload_or_monologue(OKAY_SO_THE_USER) is True
    assert internal_payload_or_monologue("The bug is the bare except at line 145.") is False
    assert internal_payload_or_monologue("") is False


def test_a_monologue_in_a_structured_summary_is_not_a_synthesis() -> None:
    """The structured summary/bullets branch of `tool_loop_final_message` returned the envelope
    contents unguarded -- a model that stuffs its monologue into `summary` shipped it verbatim
    while the free-text branch below was protected."""

    from core.agent_runtime.orchestrator import tool_loop_final_message

    synthesis = SimpleNamespace(
        structured_output={"summary": OKAY_SO_THE_USER, "bullets": ["ranking step", "candidate loop"]},
        output_text="",
    )
    steps = [{"tool_name": "workspace.list_files", "summary": "listed 42 files"}]
    message = tool_loop_final_message(synthesis, steps)
    assert "the user wants me to explain" not in message
    assert "listed 42 files" in message  # fell through to the grounded step summary


def test_a_clean_structured_summary_still_comes_through_composed() -> None:
    from core.agent_runtime.orchestrator import tool_loop_final_message

    synthesis = SimpleNamespace(
        structured_output={"summary": "Two files changed.", "bullets": ["a.py", "b.py"]},
        output_text="",
    )
    message = tool_loop_final_message(synthesis, [])
    assert message == "Two files changed.\n- a.py\n- b.py"


def test_internal_output_text_falls_through_to_the_step_summary() -> None:
    """The free-text branch, behaviourally: an unclosed reasoning block is not a synthesis."""

    from core.agent_runtime.orchestrator import tool_loop_final_message

    synthesis = SimpleNamespace(
        structured_output=None,
        output_text="<think>Okay the user wants the audit. Let me look at",
    )
    steps = [{"tool_name": "workspace.read_file", "summary": "read api/x.py"}]
    message = tool_loop_final_message(synthesis, steps)
    assert "<think>" not in message
    assert "read api/x.py" in message


def test_a_respond_direct_monologue_is_refused_like_a_placeholder() -> None:
    """`respond.direct` renders its message verbatim as the final answer -- an unguarded
    passthrough point until now. A monologue routes to None (the caller keeps working the turn),
    a real direct reply still comes through."""

    from core.agent_runtime.response_policy_classification import tool_intent_direct_message

    def direct(message: str):
        return tool_intent_direct_message({"intent": "respond.direct", "arguments": {"message": message}})

    assert direct(OKAY_SO_THE_USER) is None
    assert direct(LEAKED_BUILD_PAYLOAD) is None
    assert direct("The relay listens on 8787.") == "The relay listens on 8787."


def test_the_grounded_turn_replaces_a_monologue_final_text(make_agent) -> None:
    """The real `execute_grounded_turn`, with a model whose final text is raw monologue -- the
    lane where the 2026-08-01 leak reached the operator. The reply must be the honest degraded
    message, never the monologue."""

    from core.memory_first_router import ModelExecutionDecision

    agent = make_agent()
    decision = ModelExecutionDecision(
        source="provider",
        task_hash="h",
        provider_id="openrouter-byok",
        model_name="nvidia/nemotron-3-nano:free",
        used_model=True,
        output_text=OKAY_SO_THE_USER,
    )
    response = _drive_grounded_turn(agent, decision=decision, asked="explain how the router picks a model")
    assert "the user wants me to explain" not in response, "the monologue reached the operator"
    assert response.strip(), "the turn returned an empty reply instead of the degraded message"


def test_the_grounded_turn_still_delivers_a_clean_final_text(make_agent) -> None:
    """The false-positive control for the branch above: an ordinary answer is untouched."""

    from core.memory_first_router import ModelExecutionDecision

    agent = make_agent()
    decision = ModelExecutionDecision(
        source="provider",
        task_hash="h",
        provider_id="openrouter-byok",
        model_name="nvidia/nemotron-3-nano:free",
        used_model=True,
        output_text="The router ranks by cost class first, then locality.",
    )
    response = _drive_grounded_turn(agent, decision=decision, asked="explain how the router picks a model")
    assert "The router ranks by cost class first" in response


def _drive_grounded_turn(agent, *, decision, asked: str) -> str:
    """Run the real grounded turn, stubbing only what needs a network or a database.

    Same seam as tests/test_token_usage_is_measured_not_guessed.py -- the branch under test
    (`model_final_answer_hit and _internal_payload(model_final_text)`) runs for real.
    """

    from core.human_input_adapter import adapt_user_input
    from core.identity_manager import load_active_persona

    adaptive = SimpleNamespace(
        enabled=False, tool_gap_note="", admitted_uncertainty=False, notes=[],
        reason="not_needed", strategy="none", actions_taken=[],
        to_dict=lambda: {"enabled": False, "reason": "not_needed"},
    )
    task = SimpleNamespace(
        task_id="task-internal-payload", task_summary=asked, environment_os="darwin",
        environment_shell="zsh", environment_runtime="python", environment_version_hint="3.12",
    )
    classification = {"task_class": "research"}

    agent._should_frontload_curiosity = mock.Mock(return_value=False)
    agent._maybe_execute_model_tool_intent = mock.Mock(return_value=None)
    agent._model_routing_profile = mock.Mock(return_value=(classification, {"output_mode": ""}))
    agent._collect_adaptive_research = mock.Mock(return_value=adaptive)
    agent.memory_router.resolve = mock.Mock(return_value=decision)
    agent.media_pipeline.analyze = mock.Mock(
        return_value=SimpleNamespace(
            used_provider=False, provider_id="", candidate_id="", reason="no_media",
            evidence_items=[], analysis_text="",
        )
    )
    agent._collect_live_web_notes = mock.Mock(return_value=[])
    agent._web_note_plan_candidates = mock.Mock(return_value=[])
    agent._default_gate = mock.Mock(
        return_value=SimpleNamespace(mode="advice_only", requires_user_approval=False)
    )
    agent._maybe_publish_public_task = mock.Mock(return_value={})
    agent._store_local_shard = mock.Mock()
    agent.hive_activity_tracker.note_watched_topic = mock.Mock()
    agent._decorate_chat_response = mock.Mock(side_effect=lambda result, **_: str(result.text or ""))

    with mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
        "core.agent_runtime.agent.ingest_media_evidence", return_value=[]
    ), mock.patch("core.agent_runtime.agent.build_media_context_snippets", return_value=[]), mock.patch(
        "core.agent_runtime.agent.build_plan", return_value=SimpleNamespace(confidence=0.72, evidence_sources=[])
    ), mock.patch(
        "core.agent_runtime.agent.should_use_planner_renderer", return_value=False
    ), mock.patch(
        "core.agent_runtime.agent.explicit_planner_style_requested", return_value=False
    ), mock.patch(
        "core.agent_runtime.agent.feedback_engine.evaluate_outcome",
        return_value=SimpleNamespace(is_success=False, is_durable=False),
    ), mock.patch("core.agent_runtime.agent.feedback_engine.apply", return_value=None):
        result = agent._execute_grounded_turn(
            task=task,
            effective_input=asked,
            classification=classification,
            interpreted=adapt_user_input(asked, session_id="internal-payload-session"),
            persona=load_active_persona(agent.persona_id),
            session_id="internal-payload-session",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
    return str(result["response"])
