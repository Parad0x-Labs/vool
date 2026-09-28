from __future__ import annotations

import os
import subprocess
import sys

import pytest

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


@pytest.mark.parametrize("script", [
    "from core.incomplete_answer import inspect_answer_completeness; inspect_answer_completeness('| A | B |\\n|' + ' '*100000 + 'x')",
    "from core.grounding_publication import _trim_unsupported_spans; _trim_unsupported_spans('remove' + '\\u00a0'*100000 + 'tail', ['remove'])",
    "from core.memory.entries import is_user_correction; is_user_correction('not something '*10000 + '!')",
])
def test_remaining_guard_nonmatches_do_not_revisit_suffixes(script):
    subprocess.run([sys.executable, "-c", script], check=True, timeout=10,
                   env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


@pytest.mark.parametrize("text,expected", [
    ("not red, it is blue", True),
    ("not x\nit is blue", True),
    ("not \nit is blue", False),
    ("not x\nunrelated it is blue", False),
    ("not x; it's blue", True),
    ("not something, maybe later", False),
])
def test_correction_scanner_preserves_line_and_payload_boundaries(text, expected):
    from core.memory.entries import is_user_correction
    assert is_user_correction(text) is expected


def test_trim_squeezes_the_punctuation_a_removed_sentence_leaves_behind():
    """The squeeze is a linear scan pinned to its retired regex: a whitespace run in
    front of a surviving punctuation mark drops, and a segment that cannot be located
    still withholds the whole line."""
    from core.grounding_publication import _trim_unsupported_spans

    assert _trim_unsupported_spans(
        "keep this. remove this. tail stays", ["remove this"]
    ) == "keep this.. tail stays"
    assert _trim_unsupported_spans(
        "keep this. remove this ,  ,tail", ["remove this"]
    ) == "keep this.,tail"
    assert _trim_unsupported_spans(
        "alpha remove this beta. next stays", ["remove this"]
    ) == "alpha beta. next stays"
    assert _trim_unsupported_spans("a  ,  b", ["nothing matches here x"]) is None
