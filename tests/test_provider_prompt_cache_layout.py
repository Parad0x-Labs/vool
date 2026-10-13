"""Provider prompt caches reuse only an unchanged request prefix.

Measured on the wire 2026-10-05 (OpenRouter, z-ai/glm-5.3-flashx): VOOL's answer requests got
13.2% of prompt tokens from the provider cache against 74-87% for agents that resend a stable
prefix. The minute clock, the per-turn skill pick and the recalled memory sat inside the leading
system message, so it changed on almost every turn and the history behind it was re-billed. The
leading system message must now be byte-identical from turn to turn while every per-turn fact still
reaches the model, after the history.
"""
from __future__ import annotations

from datetime import datetime as real_datetime

import pytest

import core.prompt_normalizer as prompt_normalizer
from core.internal_message_schema import TURN_DIRECTIVES_HEADER, is_turn_directives_message
from core.memory_prompt_builder import apply_memory_prefix_to_messages
from core.model_pricing import cached_prompt_tokens
from core.prompt_budget import fit_messages_to_context_window
from tests.test_prompt_assembly_profiles import _build_request


def _clock_at(monkeypatch: pytest.MonkeyPatch, hour: int, minute: int) -> None:
    class _Clock(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2026, 10, 5, hour, minute, tzinfo=tz)

    monkeypatch.setattr(prompt_normalizer, "datetime", _Clock)


def _turn(monkeypatch: pytest.MonkeyPatch, prompt: str, *, minute: int):
    _clock_at(monkeypatch, 14, minute)
    request, _ = _build_request(
        prompt,
        task_class="general_chat",
        task_kind="summarization",
        output_mode="plain_text",
        source_context={
            "surface": "openclaw",
            "platform": "openclaw",
            "requested_model": "z-ai/glm-5.3-flashx",
            "workspace": "/data/example/garden-notes",
        },
    )
    return request.as_openai_messages()


def test_the_leading_system_message_is_identical_across_turns_and_minutes(monkeypatch) -> None:
    first = _turn(monkeypatch, "What should I plant on a sunny balcony?", minute=3)
    second = _turn(monkeypatch, "And how often should I water it?", minute=41)

    assert first[0]["role"] == "system"
    assert first[0]["content"] == second[0]["content"]
    for messages in (first, second):
        assert "The current date and time" not in messages[0]["content"]
        assert "explicitly selected model" not in messages[0]["content"]


def test_every_per_turn_fact_still_reaches_the_model_after_the_leading_block(monkeypatch) -> None:
    messages = _turn(monkeypatch, "What time is it, and which model is answering?", minute=27)

    turn = [index for index, message in enumerate(messages) if is_turn_directives_message(message)]
    assert len(turn) == 1 and turn[0] > 0
    body = messages[turn[0]]["content"]
    assert body.startswith(TURN_DIRECTIVES_HEADER)
    assert "14:27" in body
    assert "explicitly selected model `z-ai/glm-5.3-flashx`" in body
    assert "garden-notes" in body
    assert "/data/example" not in "".join(str(message["content"]) for message in messages)
    # The current user turn stays last; the per-turn facts precede it.
    assert messages[-1]["role"] == "user"
    assert turn[0] < len(messages) - 1


def test_recalled_memory_joins_the_turn_message_not_byte_zero() -> None:
    messages = [
        {"role": "system", "content": "Stable rules."},
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "earlier answer"},
        {"role": "system", "content": f"{TURN_DIRECTIVES_HEADER}\nclock 14:27"},
        {"role": "user", "content": "now"},
    ]
    out = apply_memory_prefix_to_messages(messages, request=None, prefix="User prefers metric units.")

    assert out[0] == messages[0]
    assert out[3]["content"].startswith(f"{TURN_DIRECTIVES_HEADER}\nUser prefers metric units.")
    assert "clock 14:27" in out[3]["content"]
    # Without a turn message the old placement (leading system message) still applies.
    legacy = apply_memory_prefix_to_messages(messages[:3], request=None, prefix="User prefers metric units.")
    assert legacy[0]["content"].startswith("User prefers metric units.")


def test_a_tight_local_window_sheds_history_but_never_the_turn_facts() -> None:
    turn = {"role": "system", "content": f"{TURN_DIRECTIVES_HEADER}\nThe current date and time is now."}
    messages = [
        {"role": "system", "content": "Stable rules."},
        *(
            message
            for index in range(12)
            for message in (
                {"role": "user", "content": f"old question {index} " + "word " * 60},
                {"role": "assistant", "content": f"old answer {index} " + "word " * 60},
            )
        ),
        turn,
        {"role": "user", "content": "What time is it?"},
    ]
    result = fit_messages_to_context_window(
        messages, num_ctx=700, output_reserve_tokens=100, protected_system_prompt="Stable rules."
    )

    assert result.telemetry["dropped_history_messages"] > 0
    assert turn in result.messages
    assert result.messages[-1]["content"] == "What time is it?"


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        ({"prompt_tokens": 900, "prompt_tokens_details": {"cached_tokens": 896}}, 896),
        ({"prompt_tokens": 900, "prompt_tokens_details": {"cached_tokens": 0}}, 0),
        ({"input_tokens": 10, "cache_read_input_tokens": 5}, 5),
        ({"prompt_tokens": 50, "prompt_cache_hit_tokens": 32}, 32),
        ({"prompt_tokens": 900}, None),
        (None, None),
    ],
)
def test_cached_prompt_tokens_keeps_a_reported_zero_apart_from_unknown(usage, expected) -> None:
    assert cached_prompt_tokens(usage) == expected


def test_the_usage_ledger_records_cache_hits_per_answer_and_unknown_as_unknown() -> None:
    import core.usage_meter as um
    from storage.db import get_connection

    um._SCHEMA_READY_PATHS.clear()
    conn = get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS token_usage")
        conn.commit()
    finally:
        conn.close()
    um.record_usage(provider_id="openrouter", model_id="z-ai/glm-5.3-flashx", cost_class=um.COST_PAID_CLOUD,
                    prompt_tokens=900, output_tokens=40, usd_actual=0.0001, cached_prompt_tokens=896)
    um.record_usage(provider_id="openrouter", model_id="z-ai/glm-5.3-flashx", cost_class=um.COST_PAID_CLOUD,
                    prompt_tokens=950, output_tokens=40, usd_actual=0.0003)

    conn = get_connection()
    try:
        stored = sorted(
            conn.execute("SELECT prompt_tokens, cached_prompt_tokens FROM token_usage").fetchall(),
            key=lambda row: row[0],
        )
    finally:
        conn.close()
    assert [tuple(row) for row in stored] == [(900, 896), (950, None)]
    (row,) = um.usage_by_model()
    assert row["cached_prompt_tokens"] == 896
    assert row["cache_reported_responses"] == 1
