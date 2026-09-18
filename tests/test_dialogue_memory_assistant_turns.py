from __future__ import annotations

import uuid

from core.human_input_adapter import adapt_user_input
from core.persistent_memory import append_conversation_event
from storage.dialogue_memory import recent_dialogue_turns, recent_dialogue_turns_any


def _session_id(label: str) -> str:
    return f"openclaw:{label}:{uuid.uuid4().hex}"


def test_append_conversation_event_persists_assistant_turn_in_structured_dialogue_memory() -> None:
    session_id = _session_id("assistant-persist")

    append_conversation_event(
        session_id=session_id,
        user_input="Do you think boredom is useful?",
        assistant_output="Boredom can be useful when it shows your environment is too flat.",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assistant_turns = recent_dialogue_turns(session_id, speaker_roles=("assistant",), limit=5)
    all_assistant_turns = recent_dialogue_turns_any(speaker_roles=("assistant",), limit=10)

    assert len(assistant_turns) == 1
    assert assistant_turns[0]["speaker_role"] == "assistant"
    assert assistant_turns[0]["reconstructed_input"] == "Boredom can be useful when it shows your environment is too flat."
    assert any(turn["turn_id"] == assistant_turns[0]["turn_id"] for turn in all_assistant_turns)


def test_recent_dialogue_turns_default_stays_user_only_when_assistant_turns_exist() -> None:
    session_id = _session_id("user-only-default")

    user_turn = adapt_user_input("Do you think boredom is useful?", session_id=session_id)
    append_conversation_event(
        session_id=session_id,
        user_input="Do you think boredom is useful?",
        assistant_output="Boredom can be useful when it shows your environment is too flat.",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    default_turns = recent_dialogue_turns(session_id, limit=5)
    all_turns = recent_dialogue_turns(session_id, speaker_roles=("user", "assistant"), limit=5)

    assert len(default_turns) == 1
    assert default_turns[0]["speaker_role"] == "user"
    assert default_turns[0]["turn_id"] == user_turn.turn_id
    assert [turn["speaker_role"] for turn in all_turns] == ["assistant", "user"]


def test_next_user_turn_does_not_duplicate_previous_assistant_turn() -> None:
    session_id = _session_id("no-assistant-dup")

    first_turn = adapt_user_input("Do you think boredom is useful?", session_id=session_id)
    append_conversation_event(
        session_id=session_id,
        user_input="Do you think boredom is useful?",
        assistant_output="Boredom can be useful when it shows your environment is too flat.",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )
    first_assistant_rows = recent_dialogue_turns(session_id, speaker_roles=("assistant",), limit=5)

    second_turn = adapt_user_input("What do you mean by that?", session_id=session_id)
    second_assistant_rows = recent_dialogue_turns(session_id, speaker_roles=("assistant",), limit=5)
    user_rows = recent_dialogue_turns(session_id, speaker_roles=("user",), limit=5)

    assert first_turn.turn_id is not None
    assert second_turn.turn_id is not None
    assert len(first_assistant_rows) == 1
    assert len(second_assistant_rows) == 1
    assert second_assistant_rows[0]["turn_id"] == first_assistant_rows[0]["turn_id"]
    assert [row["turn_id"] for row in user_rows[:2]] == [second_turn.turn_id, first_turn.turn_id]


def test_second_assistant_reply_adds_one_new_assistant_turn_not_replay() -> None:
    session_id = _session_id("assistant-sequence")

    adapt_user_input("Do you think boredom is useful?", session_id=session_id)
    append_conversation_event(
        session_id=session_id,
        user_input="Do you think boredom is useful?",
        assistant_output="Boredom can be useful when it shows your environment is too flat.",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )
    adapt_user_input("What do you mean by that?", session_id=session_id)
    append_conversation_event(
        session_id=session_id,
        user_input="What do you mean by that?",
        assistant_output="I mean boredom can reveal that your current constraints are too weak for your attention.",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assistant_rows = recent_dialogue_turns(session_id, speaker_roles=("assistant",), limit=5)

    assert len(assistant_rows) == 2
    assert assistant_rows[0]["reconstructed_input"] == (
        "I mean boredom can reveal that your current constraints are too weak for your attention."
    )
    assert assistant_rows[1]["reconstructed_input"] == (
        "Boredom can be useful when it shows your environment is too flat."
    )
