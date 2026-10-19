"""Prompt-cache breakpoints (core.prompt_cache): the wire and the wallet must agree.

A cache write is billed at 1.25x the input rate, so a call that may carry ``cache_control`` has to
be reserved at that price, whatever provider string reaches the reservation. With the flag off the
wire must be exactly what it was before breakpoints existed.
"""
from __future__ import annotations

import copy

import pytest

from core import runtime_flags
from core.internal_message_schema import TURN_DIRECTIVES_HEADER
from core.prompt_cache import (
    apply_cache_breakpoints,
    cache_breakpoint_plan,
    cache_write_prompt_tokens,
)

CLAUDE = "anthropic/claude-sonnet-4.5"


def _messages() -> list[dict]:
    return [
        {"role": "system", "content": "You are VOOL. Stable rules."},
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
        {"role": "system", "content": TURN_DIRECTIVES_HEADER + "\nThe time is 20:41."},
        {"role": "user", "content": "second question"},
    ]


@pytest.fixture
def flag_on():
    with runtime_flags.override("prompt_cache_breakpoints", True):
        yield


def test_flag_off_leaves_the_wire_untouched() -> None:
    messages = _messages()
    plan = cache_breakpoint_plan(openrouter_lane=True, model_id=CLAUDE)
    out, placed = apply_cache_breakpoints(messages, plan)
    assert plan.enabled is False and plan.reason == "flag_off"
    assert out is messages and placed == 0


def test_only_claude_on_openrouter_is_marked(flag_on) -> None:
    assert cache_breakpoint_plan(openrouter_lane=True, model_id=CLAUDE).enabled
    assert not cache_breakpoint_plan(openrouter_lane=False, model_id=CLAUDE).enabled
    assert not cache_breakpoint_plan(openrouter_lane=True, model_id="openai/gpt-4.1-mini").enabled
    assert not cache_breakpoint_plan(openrouter_lane=True, model_id=CLAUDE, local_runtime=True).enabled


def test_system_placement_marks_the_stable_opening_and_keeps_the_text(flag_on) -> None:
    messages = _messages()
    before = copy.deepcopy(messages)
    out, placed = apply_cache_breakpoints(messages, cache_breakpoint_plan(openrouter_lane=True, model_id=CLAUDE))
    assert placed == 1
    assert messages == before  # the caller's list is never mutated
    assert out[0]["content"] == [
        {"type": "text", "text": "You are VOOL. Stable rules.", "cache_control": {"type": "ephemeral"}}
    ]
    assert out[1:] == before[1:]


def test_only_the_system_opening_is_ever_marked(flag_on, monkeypatch) -> None:
    """The history variant is gone: an old placement setting cannot bring a history mark back."""
    monkeypatch.setenv("VOOL_PROMPT_CACHE_PLACEMENT", "system_history")
    messages = _messages()
    out, placed = apply_cache_breakpoints(messages, cache_breakpoint_plan(openrouter_lane=True, model_id=CLAUDE))
    assert placed == 1
    assert out[1:] == messages[1:]
    assert out[3]["content"].startswith(TURN_DIRECTIVES_HEADER)  # the per-turn block is never marked


def test_reservation_holds_the_write_price_on_every_route_that_could_mark(flag_on) -> None:
    """A custom endpoint at openrouter.ai is marked by the adapter; its reservation must cover that."""
    from core.model_pricing import estimate_call_usd

    for provider_id in ("openrouter-byok:" + CLAUDE, "custom-byok:" + CLAUDE, ""):
        plain = estimate_call_usd(
            provider_id=provider_id, model_id=CLAUDE, prompt_tokens=100_000, completion_tokens=0, include_cache_write=False
        )
        held = estimate_call_usd(provider_id=provider_id, model_id=CLAUDE, prompt_tokens=100_000, completion_tokens=0)
        assert held.usd == pytest.approx(plain.usd * 1.25), provider_id


def test_reservation_is_unchanged_when_the_flag_is_off() -> None:
    from core.model_pricing import estimate_call_usd

    a = estimate_call_usd(provider_id="openrouter-byok:" + CLAUDE, model_id=CLAUDE, prompt_tokens=50_000, completion_tokens=100)
    b = estimate_call_usd(
        provider_id="openrouter-byok:" + CLAUDE, model_id=CLAUDE, prompt_tokens=50_000, completion_tokens=100, include_cache_write=False
    )
    assert a.usd == b.usd


def test_settlement_prefers_the_reported_cost(flag_on) -> None:
    from core.model_pricing import BASIS_PROVIDER_REPORTED, settlement_usd

    usage = {"prompt_tokens": 9000, "completion_tokens": 50, "cost": 0.0042, "prompt_tokens_details": {"cache_write_tokens": 8000}}
    estimate = settlement_usd(provider_id="openrouter-byok:" + CLAUDE, model_id=CLAUDE, usage=usage)
    assert estimate.basis == BASIS_PROVIDER_REPORTED and estimate.usd == pytest.approx(0.0042)


def test_settlement_without_a_cost_charges_only_the_reported_writes_at_the_write_rate(flag_on) -> None:
    from core.model_pricing import estimate_call_usd, settlement_usd

    provider = "openrouter-byok:" + CLAUDE
    plain = estimate_call_usd(provider_id=provider, model_id=CLAUDE, prompt_tokens=10_000, completion_tokens=0, include_cache_write=False)
    per_token = plain.usd / 10_000
    written = settlement_usd(provider_id=provider, model_id=CLAUDE, usage={"prompt_tokens": 10_000, "prompt_tokens_details": {"cache_write_tokens": 4_000}})
    none_written = settlement_usd(provider_id=provider, model_id=CLAUDE, usage={"prompt_tokens": 10_000, "prompt_tokens_details": {"cache_write_tokens": 0}})
    unknown = settlement_usd(provider_id=provider, model_id=CLAUDE, usage={"prompt_tokens": 10_000})
    assert written.usd == pytest.approx(plain.usd + 4_000 * per_token * 0.25)
    assert none_written.usd == pytest.approx(plain.usd)
    assert unknown.usd == pytest.approx(plain.usd * 1.25)  # nothing reported: the worst case held


def test_cache_write_tokens_are_read_across_dialects() -> None:
    assert cache_write_prompt_tokens({"prompt_tokens_details": {"cache_write_tokens": 12}}) == 12
    assert cache_write_prompt_tokens({"cache_creation_input_tokens": 0}) == 0
    assert cache_write_prompt_tokens({"prompt_tokens": 5}) is None
    assert cache_write_prompt_tokens(None) is None


def test_the_usage_meter_records_cache_writes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    from core.usage_meter import COST_PAID_CLOUD, record_usage
    from storage.db import get_connection

    assert record_usage(
        provider_id="openrouter-byok:" + CLAUDE, model_id=CLAUDE, cost_class=COST_PAID_CLOUD,
        prompt_tokens=900, output_tokens=10, cached_prompt_tokens=0, cache_write_prompt_tokens=800,
    )
    conn = get_connection()
    try:
        row = conn.execute("SELECT cache_write_prompt_tokens FROM token_usage ORDER BY created_ts DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    assert row[0] == 800


def test_anthropic_native_usage_settles_the_cached_tokens_too(flag_on) -> None:
    """Anthropic counts cache reads and writes outside ``input_tokens``; leaving them out undercharges."""
    from core.model_pricing import settlement_usd

    provider = "openrouter-byok:" + CLAUDE
    native = settlement_usd(
        provider_id=provider, model_id=CLAUDE,
        usage={"input_tokens": 50, "output_tokens": 10, "cache_creation_input_tokens": 8_000, "cache_read_input_tokens": 2_000},
    )
    same = settlement_usd(
        provider_id=provider, model_id=CLAUDE,
        usage={"prompt_tokens": 10_050, "completion_tokens": 10, "prompt_tokens_details": {"cache_write_tokens": 8_000}},
    )
    assert native.prompt_tokens == 10_050
    assert native.usd == pytest.approx(same.usd)


def test_a_searched_claude_call_without_a_cost_counts_the_write_and_the_search(flag_on) -> None:
    from core.model_own_tools import provider_search_fee_usd, searched_call_cost_estimate
    from core.model_pricing import settlement_usd

    provider = "openrouter-byok:" + CLAUDE
    usage = {
        "prompt_tokens": 20_000, "completion_tokens": 100,
        "prompt_tokens_details": {"cache_write_tokens": 20_000}, "server_tool_use": {"web_search_requests": 2},
    }
    tokens = settlement_usd(provider_id=provider, model_id=CLAUDE, usage=usage)
    counted = searched_call_cost_estimate(provider_id=provider, model_id=CLAUDE, usage=usage)
    assert counted["usd"] == pytest.approx(tokens.usd + provider_search_fee_usd(usage))
