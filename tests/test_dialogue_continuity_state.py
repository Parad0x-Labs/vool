from __future__ import annotations

import uuid

from core.bootstrap_context import build_bootstrap_context
from core.human_input_adapter import adapt_user_input
from core.identity_manager import load_active_persona
from core.persistent_memory import append_conversation_event
from core.task_router import create_task_record
from storage.dialogue_memory import get_dialogue_session, recent_archived_dialogue_topics


def _session_id(label: str) -> str:
    return f"openclaw:{label}:{uuid.uuid4().hex}"


def test_followup_turn_persists_continuity_state_from_recent_assistant_commitment() -> None:
    session_id = _session_id("continuity-persist")

    adapt_user_input(
        "I'm stuck deciding whether to keep Python or Go for this Telegram bot.",
        session_id=session_id,
    )
    append_conversation_event(
        session_id=session_id,
        user_input="I'm stuck deciding whether to keep Python or Go for this Telegram bot.",
        assistant_output="I'll compare the tradeoffs and sketch a cleaner plan next.",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    followup = adapt_user_input("ok do that", session_id=session_id)
    session = get_dialogue_session(session_id)

    assert followup.reference_targets
    assert "telegram bot" in " ".join(list(session.get("topic_hints") or [])).lower()
    assert "telegram bot" in str(session.get("current_user_goal") or "").lower()
    assert session["last_intent_mode"] == followup.intent_mode
    assert session["assistant_commitments"] == ["compare the tradeoffs and sketch a cleaner plan next"]
    assert session["unresolved_followups"] == ["compare the tradeoffs and sketch a cleaner plan next"]
    assert session["emotional_tone"] == "frustrated"
    assert session["user_stance"] == "goal_driven"


def test_bootstrap_context_loads_persisted_continuity_state_for_followup() -> None:
    session_id = _session_id("continuity-bootstrap")
    persona = load_active_persona("default")

    adapt_user_input(
        "I'm stuck deciding whether to keep Python or Go for this Telegram bot.",
        session_id=session_id,
    )
    append_conversation_event(
        session_id=session_id,
        user_input="I'm stuck deciding whether to keep Python or Go for this Telegram bot.",
        assistant_output="I'll compare the tradeoffs and sketch a cleaner plan next.",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )
    interpretation = adapt_user_input("what do you mean by that?", session_id=session_id)

    items = build_bootstrap_context(
        persona=persona,
        task=create_task_record("what do you mean by that?"),
        classification={"task_class": "chat_conversation", "risk_flags": [], "confidence_hint": 0.84},
        interpretation=interpretation,
        session_id=session_id,
    )

    continuity_items = [item for item in items if item.source_type == "dialogue_continuity"]

    assert len(continuity_items) == 1
    assert "current user goal:" in continuity_items[0].content.lower()
    assert "assistant commitments:" in continuity_items[0].content.lower()
    assert "unresolved followups:" in continuity_items[0].content.lower()
    assert "compare the tradeoffs and sketch a cleaner plan next" in continuity_items[0].content.lower()
    assert "telegram bot" in continuity_items[0].content.lower()


def test_unrelated_turn_clears_stale_continuity_state() -> None:
    session_id = _session_id("continuity-clear")
    persona = load_active_persona("default")

    adapt_user_input(
        "I'm stuck deciding whether to keep Python or Go for this Telegram bot.",
        session_id=session_id,
    )
    append_conversation_event(
        session_id=session_id,
        user_input="I'm stuck deciding whether to keep Python or Go for this Telegram bot.",
        assistant_output="I'll compare the tradeoffs and sketch a cleaner plan next.",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )
    adapt_user_input("ok do that", session_id=session_id)

    new_turn = adapt_user_input("What should I eat after lifting?", session_id=session_id)
    session = get_dialogue_session(session_id)
    items = build_bootstrap_context(
        persona=persona,
        task=create_task_record("What should I eat after lifting?"),
        classification={"task_class": "food_nutrition", "risk_flags": [], "confidence_hint": 0.84},
        interpretation=new_turn,
        session_id=session_id,
    )
    continuity_items = [item for item in items if item.source_type == "dialogue_continuity"]
    archived = recent_archived_dialogue_topics(session_id, limit=3)

    assert "eat after lifting" in str(session.get("current_user_goal") or "").lower()
    assert session["assistant_commitments"] == []
    assert session["unresolved_followups"] == []
    assert archived
    assert archived[0]["closure_reason"] == "topic_shift"
    assert archived[0]["closure_status"] == "unresolved"
    assert "telegram bot" in str(archived[0]["summary"] or "").lower()
    if continuity_items:
        assert "compare the tradeoffs and sketch a cleaner plan next" not in continuity_items[0].content.lower()


def test_failed_assistant_turn_closes_dialogue_topic_into_archive() -> None:
    session_id = _session_id("continuity-failure-close")

    adapt_user_input("what is the btc price now?", session_id=session_id)
    append_conversation_event(
        session_id=session_id,
        user_input="what is the btc price now?",
        assistant_output="I checked, but I couldn't ground a confident answer from the evidence I found.",
        source_context={"surface": "openclaw", "platform": "openclaw"},
        response_class="task_failed_user_safe",
    )

    session = get_dialogue_session(session_id)
    archived = recent_archived_dialogue_topics(session_id, limit=3)

    assert session["current_user_goal"] is None
    assert session["assistant_commitments"] == []
    assert session["unresolved_followups"] == []
    assert archived
    assert archived[0]["closure_reason"] == "assistant_failure"
    assert archived[0]["closure_status"] == "unresolved"
    assert "btc price" in str(archived[0]["summary"] or "").lower()


def test_failed_assistant_turn_with_cannot_prefix_closes_dialogue_topic_into_archive() -> None:
    session_id = _session_id("continuity-failure-cannot-close")

    adapt_user_input("read /private/tmp/secret.txt exactly", session_id=session_id)
    append_conversation_event(
        session_id=session_id,
        user_input="read /private/tmp/secret.txt exactly",
        assistant_output="I cannot read that path in this lane. I can only read files inside: ~/Desktop, ~/Downloads, ~/Documents.",
        source_context={"surface": "openclaw", "platform": "openclaw"},
        response_class="utility_answer",
    )

    session = get_dialogue_session(session_id)
    archived = recent_archived_dialogue_topics(session_id, limit=3)

    assert session["current_user_goal"] is None
    assert session["assistant_commitments"] == []
    assert session["unresolved_followups"] == []
    assert archived
    assert archived[0]["closure_reason"] == "assistant_failure"
    assert archived[0]["closure_status"] == "unresolved"
    assert "secret" in str(archived[0]["summary"] or "").lower()
