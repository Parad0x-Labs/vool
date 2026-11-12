"""Conductor briefings carry this chat's recent turns.

Measured 2026-11-03 (build 8af687ca, served chat, turn conductor-c05c26cee8ed): the node
`factual_explanation:why_cant_you` answered "The question 'why cant you?' is incomplete and lacks
context" — the previous turn's refusal notice was in the chat ledger, the memory store and the
tiered context, and in none of the node's prompt. `SharedTurnContext` carried the message and its
typed numbers only, so every conductor answer to a reference into the previous turn was
constructed blind. These tests pin the repair: the conversation rides the shared context, lands
in every node briefing that renders one, and stays out of everything when the chat has no
readable history.
"""

from __future__ import annotations

import dataclasses
import uuid

from core.conductor.planner import _shared_with_conversation, plan_conductor_turn
from core.conductor.shared_context import SharedTurnContext, extract_shared_context, render_briefing


def _session_id(label: str) -> str:
    return f"openclaw:{label}:{uuid.uuid4().hex}"


def test_shared_turn_context_defaults_to_no_conversation() -> None:
    shared = extract_shared_context("why cant you?")
    assert shared.conversation_context == ""


def test_render_briefing_without_conversation_is_unchanged() -> None:
    """No chat history in reach: the briefing is byte-identical to what it shipped before."""
    text = "Convert 100 USD to RUB."
    briefing = render_briefing(extract_shared_context(text))
    assert "Recent conversation" not in briefing
    assert text in briefing


def test_render_briefing_shows_conversation_after_the_message() -> None:
    shared = SharedTurnContext(
        original_request="why cant you?",
        conversation_context=(
            "user: check my email, tell me if anything important is there\n"
            "assistant: I can't publish this answer: nothing this turn retrieved, computed or "
            "observed backs it."
        ),
    )
    briefing = render_briefing(shared)
    assert "The user's full message:" in briefing
    assert "why cant you?" in briefing
    assert "Recent conversation in this chat" in briefing
    assert "check my email" in briefing
    assert briefing.index("why cant you?") < briefing.index("Recent conversation in this chat")


def test_shared_with_conversation_sets_the_field_and_empty_keeps_extraction() -> None:
    text = "Split 300 between 3 people and explain the math."
    plain = extract_shared_context(text)
    carried = _shared_with_conversation(text, "user: earlier turn\nassistant: earlier answer")
    assert carried.conversation_context == "user: earlier turn\nassistant: earlier answer"
    assert carried.original_request == plain.original_request
    assert carried.facts == plain.facts
    assert _shared_with_conversation(text, "   ") == plain


def test_shared_context_is_still_frozen() -> None:
    shared = _shared_with_conversation("why cant you?", "user: prior")
    assert dataclasses.is_dataclass(shared)
    try:
        shared.conversation_context = "mutated"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("SharedTurnContext must stay frozen; scheduler relies on it")


def test_plan_shared_context_carries_conversation_into_node_briefings() -> None:
    """The shared context a plan is built with holds the conversation, so node briefings show it.

    `build_plan_from_clauses` is the one seam every plan path (deterministic, research,
    planner-model) funnels through; `plan_conductor_turn` hands it the conversation via
    `_shared_with_conversation`. Asserting at this seam covers all three plan origins without
    depending on which text shape each classifier admits.
    """
    from core.conductor.planner import ProposedClause, build_plan_from_clauses

    conversation = "user: what time is it in NY\nassistant: 14:19 EST"
    plan = build_plan_from_clauses(
        (
            ProposedClause(0, "why cant you answer the last question?", "factual_explanation", ()),
        ),
        original_request="why cant you answer the last question?",
        plan_id="conductor-test-conv",
        shared_context=_shared_with_conversation("why cant you answer the last question?", conversation),
    )
    assert plan.nodes
    assert plan.shared_context.conversation_context == conversation
    briefing = render_briefing(plan.shared_context, clause=str(plan.nodes[0].request_text or ""))
    assert "Recent conversation in this chat" in briefing
    assert "what time is it in NY" in briefing


def test_plan_from_clauses_without_conversation_keeps_briefings_unchanged() -> None:
    from core.conductor.planner import ProposedClause, build_plan_from_clauses

    request = "why cant you answer the last question?"
    plan = build_plan_from_clauses(
        (ProposedClause(0, request, "factual_explanation", ()),),
        original_request=request,
        plan_id="conductor-test-plain",
    )
    assert plan.nodes
    briefing = render_briefing(plan.shared_context, clause=str(plan.nodes[0].request_text or ""))
    assert "Recent conversation" not in briefing


def test_plan_conductor_turn_accepts_the_kwarg_on_a_declining_text() -> None:
    """A single-request message still declines (None) with the kwarg present — no TypeError."""
    plan = plan_conductor_turn(
        "What is the capital of France?",
        ask_model=lambda system, request: (_ for _ in ()).throw(AssertionError("planner call")),
        conversation_context="user: prior\nassistant: prior answer",
    )
    assert plan is None


def test_conductor_conversation_context_reads_this_chat_only() -> None:
    """The helper reads the same store the tiered loader reads, scoped to this chat, both roles."""
    from core.agent_runtime.agent import _conductor_conversation_context
    from core.context_namespace import ensure_chat_namespace
    from core.human_input_adapter import adapt_user_input
    from core.persistent_memory import append_conversation_event

    session_id = _session_id("conductor-conv")
    ensure_chat_namespace(session_id)

    adapt_user_input("check my email, tell me if anything important is there", session_id=session_id)
    append_conversation_event(
        session_id=session_id,
        user_input="check my email, tell me if anything important is there",
        assistant_output=(
            "I can't publish this answer: nothing this turn retrieved, computed or observed "
            "backs it."
        ),
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )
    context = _conductor_conversation_context(session_id, source_context=None)
    assert "check my email" in context
    assert "user: check my email" in context
    assert "assistant: I can't publish this answer" in context

    other = _conductor_conversation_context(_session_id("other-chat"), source_context=None)
    assert "check my email" not in other


def test_conductor_conversation_context_empty_without_session() -> None:
    from core.agent_runtime.agent import _conductor_conversation_context

    assert _conductor_conversation_context("", source_context=None) == ""
    assert _conductor_conversation_context("   ", source_context=None) == ""
