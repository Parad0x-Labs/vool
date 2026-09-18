"""A stated length or long-form structure lifts the ordinary-chat brevity ceiling.

Measured live 2026-08-15 10:26 (MF-12): "write me a 500-word article: age, history, funny facts,
institutional data, forecast" was answered with TWO sentences and no disclosure. Root cause
(pipeline audit): ordinary_chat_output_policy caps every plain answer at 64 words unless a detail
KEYWORD appears -- and a stated word count, paragraph count, or long-form noun (article, essay,
comprehensive) matched none of them. A stated length is an explicit output contract, and contracts
outrank heuristics everywhere else in this runtime.
"""

from __future__ import annotations

import pytest

from core.ordinary_chat_response_guard import ordinary_chat_output_policy


def _max_words_for(user_text: str) -> str:
    policy = ordinary_chat_output_policy(
        prompt_profile="chat",
        output_mode="plain_text",
        user_text=user_text,
    )
    return str(policy.get("max_words") or "")


@pytest.mark.parametrize(
    "turn",
    (
        # The measured reproduction and its family.
        "write me a 500-word article about Vilnius: age, history, funny facts, forecast",
        "give me a 300 word summary of the French Revolution",
        "draft a blog post about local-first AI runtimes",
        "write an essay on why rivers meander",
        "explain transformer attention in 3 paragraphs",
        "i want a comprehensive breakdown of the deploy pipeline",
        "write at least 200 words on battery chemistry",
        "give me an in-depth explanation of DNS resolution",
        # The prior detail-keyword channel is unchanged.
        "explain step-by-step how to mount a volume",
        "compare postgres and sqlite in detail",
    ),
)
def test_stated_length_or_long_form_lifts_the_ceiling(turn: str) -> None:
    assert _max_words_for(turn) == "", turn


@pytest.mark.parametrize(
    "turn",
    (
        # Ordinary short asks keep the ceiling.
        "what is the capital of France",
        "hey how are you",
        "what's 25 + 20",
        "is redis single threaded",
        # A SHORT exact word contract is not a length lift -- it has its own handling.
        "answer in two words: is water wet",
        "one word: capital of Japan",
    ),
)
def test_ordinary_and_short_exact_turns_keep_the_ceiling(turn: str) -> None:
    assert _max_words_for(turn) != "", turn


def test_non_ordinary_modes_are_untouched() -> None:
    policy = ordinary_chat_output_policy(
        prompt_profile="chat",
        output_mode="json_object",
        user_text="what is the capital of France",
    )
    assert str(policy.get("max_words") or "") == ""
