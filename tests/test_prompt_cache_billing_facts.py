"""Finding #9 (0.7.3 item 6): a cache write is billed from the call's own facts, not today's flag.

Settlement priced reported cache writes by reading the ``prompt_cache_breakpoints`` flag at
settlement time. The same 10,000 written tokens settled at the write rate with the flag on and at
the plain input rate with it off, so flipping the flag between reserve and settle changed what a
past call was charged. Settlement now prices from what the provider reported, and holds the worst
case when a markable model reports no write count.

NO REAL CALL IS MADE ANYWHERE HERE: ``requests.post`` inside the adapter is a recorder.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core import runtime_flags
from core.model_pricing import BASIS_NO_USAGE, BASIS_PROVIDER_REPORTED, estimate_call_usd, settlement_usd

CLAUDE = "anthropic/claude-sonnet-4.5"
PROVIDER = "openrouter-byok:" + CLAUDE
PROMPT = 10_000
FLAG = "prompt_cache_breakpoints"


def _plain() -> float:
    return estimate_call_usd(
        provider_id=PROVIDER, model_id=CLAUDE, prompt_tokens=PROMPT, completion_tokens=0, include_cache_write=False
    ).usd


def _per_token() -> float:
    return _plain() / PROMPT


def _settle_both_ways(usage: dict) -> tuple[float, float]:
    with runtime_flags.override(FLAG, True):
        on = settlement_usd(provider_id=PROVIDER, model_id=CLAUDE, usage=usage).usd
    with runtime_flags.override(FLAG, False):
        off = settlement_usd(provider_id=PROVIDER, model_id=CLAUDE, usage=usage).usd
    return on, off


# --- the flag no longer moves a past call's price ------------------------------------------------


def test_reported_writes_cost_the_same_whatever_the_flag_says_now():
    usage = {"prompt_tokens": PROMPT, "prompt_tokens_details": {"cache_write_tokens": PROMPT}}
    on, off = _settle_both_ways(usage)
    assert on == off == pytest.approx(_plain() * 1.25)


def test_a_reported_zero_write_pays_no_write_even_if_the_flag_is_on_now():
    on, off = _settle_both_ways({"prompt_tokens": PROMPT, "prompt_tokens_details": {"cache_write_tokens": 0}})
    assert on == off == pytest.approx(_plain())


def test_only_the_reported_writes_carry_the_surcharge():
    on, off = _settle_both_ways(
        {"prompt_tokens": PROMPT, "prompt_tokens_details": {"cache_write_tokens": 4_000}}
    )
    assert on == off == pytest.approx(_plain() + 4_000 * _per_token() * 0.25)


def test_an_unreported_write_on_a_markable_model_is_held_at_the_worst_case():
    """No write count reported: an unknown write is a liability, not a zero, whatever the flag."""
    on, off = _settle_both_ways({"prompt_tokens": PROMPT})
    assert on == off == pytest.approx(_plain() * 1.25)


def test_the_provider_reported_cost_wins_over_every_estimate():
    usage = {"prompt_tokens": PROMPT, "cost": 0.0042, "prompt_tokens_details": {"cache_write_tokens": PROMPT}}
    with runtime_flags.override(FLAG, False):
        estimate = settlement_usd(provider_id=PROVIDER, model_id=CLAUDE, usage=usage)
    assert estimate.basis == BASIS_PROVIDER_REPORTED and estimate.usd == pytest.approx(0.0042)


def test_no_usage_stays_unknown_so_the_reservation_stays_held():
    with runtime_flags.override(FLAG, True):
        estimate = settlement_usd(provider_id=PROVIDER, model_id=CLAUDE, usage={})
    assert estimate.basis == BASIS_NO_USAGE


def test_a_model_without_a_write_premium_is_never_surcharged():
    usage = {"prompt_tokens": PROMPT, "prompt_tokens_details": {"cache_write_tokens": PROMPT}}
    model = "openai/gpt-4.1-mini"
    plain = estimate_call_usd(
        provider_id="openrouter-byok:" + model, model_id=model, prompt_tokens=PROMPT, completion_tokens=0,
        include_cache_write=False,
    ).usd
    with runtime_flags.override(FLAG, True):
        assert settlement_usd(provider_id="openrouter-byok:" + model, model_id=model, usage=usage).usd == pytest.approx(plain)


# --- the real adapter path ------------------------------------------------------------------------


def _manifest(provider_name: str, *, base_url: str, model_name: str):
    return SimpleNamespace(
        provider_name=provider_name,
        provider_id=f"{provider_name}:{model_name}",
        model_name=model_name,
        adapter_type="openai_compatible",
        source_type="http",
        metadata={"cost_class": "paid_cloud", "runtime_family": "openai-compatible"},
        runtime_config={"base_url": base_url, "api_path": "/chat/completions", "timeout_seconds": 5.0},
    )


def _request() -> ModelRequest:
    return ModelRequest(
        task_kind="chat",
        prompt="q",
        messages=[{"role": "system", "content": "You are VOOL. Stable rules."}, {"role": "user", "content": "q"}],
    )


def _send(manifest, *, usage: dict):
    body = {"choices": [{"message": {"content": "Answer."}, "finish_reason": "stop"}], "usage": usage}
    response = mock.Mock(status_code=200, headers={})
    response.raise_for_status.return_value = None
    response.json.return_value = body
    with mock.patch("adapters.openai_compatible_adapter.requests.post", return_value=response) as post:
        result = OpenAICompatibleAdapter(manifest).run_text_task(_request())
    return post.call_args.kwargs["json"], result


OPENROUTER_CLAUDE = _manifest("openrouter-byok", base_url="https://openrouter.ai/api/v1", model_name=CLAUDE)


def test_a_marked_call_settles_at_the_write_price_after_the_flag_is_turned_off():
    from core.memory_first_router import _response_actual_usd

    usage = {"prompt_tokens": PROMPT, "completion_tokens": 0, "prompt_tokens_details": {"cache_write_tokens": PROMPT}}
    with runtime_flags.override(FLAG, True):
        sent, result = _send(OPENROUTER_CLAUDE, usage=usage)
    assert sent["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    with runtime_flags.override(FLAG, False):
        charged = _response_actual_usd(OPENROUTER_CLAUDE, result)
    assert charged == pytest.approx(_plain() * 1.25)


def test_an_unmarked_call_settles_plain_after_the_flag_is_turned_on():
    from core.memory_first_router import _response_actual_usd

    usage = {"prompt_tokens": PROMPT, "completion_tokens": 0, "prompt_tokens_details": {"cache_write_tokens": 0}}
    with runtime_flags.override(FLAG, False):
        sent, result = _send(OPENROUTER_CLAUDE, usage=usage)
    assert sent["messages"][0]["content"] == "You are VOOL. Stable rules."  # the wire is untouched
    with runtime_flags.override(FLAG, True):
        charged = _response_actual_usd(OPENROUTER_CLAUDE, result)
    assert charged == pytest.approx(_plain())
