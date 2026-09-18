from __future__ import annotations

from core.bootstrap_context import _with_authoritative_corrections
from core.memory.entries import derive_fact_key, is_user_correction


def test_latest_preference_correction_supersedes_stale_transcript_context() -> None:
    transcript = [
        {"role": "user", "content": "For this chat, I prefer loose notes to notebooks."},
        {"role": "assistant", "content": "You prefer loose notes."},
        {
            "role": "user",
            "content": "I changed my mind: I prefer a short notebook after all.",
        },
    ]

    result = _with_authoritative_corrections(
        transcript,
        current_user_text="What is my current preference?",
    )

    assert result == [
        {
        "role": "system",
        "content": (
            "Latest explicit user corrections for this chat are authoritative. "
            "They supersede older conflicting statements: "
            "preference = a short notebook after all"
        ),
        },
        {
            "role": "user",
            "content": "I changed my mind: I prefer a short notebook after all.",
        },
    ]


def test_changed_mind_preference_is_persisted_as_a_superseding_user_correction() -> None:
    text = "I changed my mind: I prefer a short notebook after all."

    assert is_user_correction(text) is True
    assert derive_fact_key(text, category="preference") == "preference"


def test_unrelated_turn_does_not_reinject_an_old_correction_as_the_topic() -> None:
    transcript = [
        {"role": "user", "content": "I prefer paper sketches to digital notes."},
        {"role": "assistant", "content": "You prefer paper sketches."},
        {
            "role": "user",
            "content": "I changed my mind: I prefer a compact notebook after all.",
        },
        {"role": "assistant", "content": "You prefer a compact notebook."},
        {
            "role": "user",
            "content": "Why can a five-minute walk help when someone feels stuck?",
        },
        {"role": "assistant", "content": "It can reset attention."},
    ]

    result = _with_authoritative_corrections(
        transcript,
        current_user_text="In one sentence, what part of that explanation matters most?",
    )

    assert all(item["role"] != "system" for item in result)
    assert all("paper sketches" not in item["content"] for item in result)
    assert any("five-minute walk" in item["content"] for item in result)
