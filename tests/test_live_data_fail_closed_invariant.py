"""Checkpoint 6.5: a LIVE_DATA-classified turn must never fall through to ordinary, ungrounded
model prose.

Found live: "Market data only for Bitcoin and gold." was classified LIVE_DATA by
`requirements_for()` (via `_live_data_classification`, which recognizes a named asset with no
"price"/"cost" keyword required) but `build_live_data_plan()` independently gated its own
extraction on a keyword list first and built ZERO subtasks -- a discrepancy between the classifier
and the plan builder's own recognizers. `_maybe_answer_live_data_turn` used to `return None` in
that case, and the turn fell through to the ordinary model path, which fabricated a Bitcoin price
with no real data behind it.

Fixing that one specific discrepancy (`_resolve_price_alias`, already covered elsewhere) closes
this exact input, but is not a general safeguard: any FUTURE discrepancy between the classifier and
the plan builder -- or any exception raised while building or executing the plan, or every subtask
failing -- would reproduce the same fallthrough. This file proves the general invariant instead:
once `requirements_for()` says LIVE_DATA, `_maybe_answer_live_data_turn` never returns None again,
under any of those failure shapes, and always renders a deterministic answer that preserves the
requested entities without ever inventing a number.

Every test here drives the REAL production entry point, `VoolAgent.run_once()` -- the same method
`apps/vool_api_server.py` calls -- not `_maybe_answer_live_data_turn()` directly, and proves no
provider/model call occurred via `result["model_calls"] == 0` and a tripwire on
`_handle_turn_frontdoor` (the first place after the LIVE_DATA/planned-turn gates that could reach a
real model).
"""

from __future__ import annotations

from unittest import mock

import pytest

from apps.vool_agent import VoolAgent


def _tripwire(*_args, **_kwargs):
    raise AssertionError(
        "ordinary turn frontdoor was reached -- the LIVE_DATA fail-closed invariant was violated"
    )


@pytest.fixture
def agent() -> VoolAgent:
    return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")


# --- plan construction returns None despite LIVE_DATA classification ---


def test_plan_is_none_never_reaches_the_ordinary_turn(agent: VoolAgent) -> None:
    with mock.patch("core.agent_runtime.agent.VoolAgent._handle_turn_frontdoor", side_effect=_tripwire), \
         mock.patch("core.agent_runtime.live_data_plan.build_live_data_plan", return_value=None):
        result = agent.run_once(
            "Market data only for Bitcoin and gold.",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
    assert result.get("model_calls") == 0
    assert result.get("web_calls") == 0
    assert result.get("model_execution", {}).get("used_model") is False
    assert result.get("route") == "deterministic:live_data_plan_unavailable"


def test_plan_is_none_preserves_the_requested_entities_without_inventing_a_number(agent: VoolAgent) -> None:
    with mock.patch("core.agent_runtime.live_data_plan.build_live_data_plan", return_value=None):
        result = agent.run_once(
            "Market data only for Bitcoin and gold.",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
    response = str(result.get("response") or "")
    assert "Bitcoin" in response
    assert "Gold" in response
    assert "could not be retrieved" in response
    # No fabricated price: no digit run that could read as a dollar figure.
    assert not any(ch.isdigit() for ch in response)


def test_plan_is_none_with_a_weather_request_names_the_cities(agent: VoolAgent) -> None:
    with mock.patch("core.agent_runtime.live_data_plan.build_live_data_plan", return_value=None):
        result = agent.run_once(
            "Weather only for Berlin and Copenhagen.",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
    response = str(result.get("response") or "")
    assert "Berlin" in response and "Copenhagen" in response
    assert "could not be retrieved" in response


# --- an exception raised while building or executing the plan ---


def test_an_exception_building_the_plan_never_reaches_the_ordinary_turn(agent: VoolAgent) -> None:
    with mock.patch("core.agent_runtime.agent.VoolAgent._handle_turn_frontdoor", side_effect=_tripwire), \
         mock.patch("core.agent_runtime.live_data_plan.build_live_data_plan", side_effect=RuntimeError("boom")):
        result = agent.run_once(
            "price for bitcoin and ethereum please?",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
    assert result.get("model_calls") == 0
    assert result.get("route") == "deterministic:live_data_plan_unavailable"
    assert "could not be retrieved" in str(result.get("response") or "")


# --- every subtask fails: still a deterministic answer, never a fallthrough ---


def test_every_subtask_failing_still_renders_a_deterministic_answer(agent: VoolAgent) -> None:
    def _all_fail(coin_ids, **_kwargs):
        raise RuntimeError("network down")

    with mock.patch("core.agent_runtime.agent.VoolAgent._handle_turn_frontdoor", side_effect=_tripwire), \
         mock.patch("tools.web.web_research._crypto_price_fallback_multi", side_effect=_all_fail):
        result = agent.run_once(
            "price for bitcoin and ethereum please?",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
    response = str(result.get("response") or "")
    assert result.get("model_calls") == 0
    assert "Bitcoin" in response and "Ethereum" in response
    # Two asset rows plus the derived comparison line, all correctly unavailable -- no fabricated
    # price and no entity silently dropped.
    assert response.lower().count("unavailable") == 3
    assert "network down" in response


# --- the control: a non-LIVE_DATA turn is completely unaffected ---


def test_a_non_live_data_turn_is_unaffected_and_still_reaches_the_ordinary_path() -> None:
    from apps.vool_agent import VoolAgent as _Agent

    agent = _Agent(backend_name="test-backend", device="channel-test", persona_id="default")
    reached: dict[str, bool] = {"frontdoor": False}

    def _mark_reached(*_args, **_kwargs):
        reached["frontdoor"] = True
        return {"response": "ok", "route": "test", "session_id": "x", "source_context": {}}

    with mock.patch("core.agent_runtime.agent.VoolAgent._handle_turn_frontdoor", side_effect=_mark_reached):
        agent.run_once(
            "what is the capital of France",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
    assert reached["frontdoor"] is True


# --- sabotage: remove the fail-closed guard, prove the original fallthrough reproduces ---


def test_sabotage_removing_the_guard_reproduces_the_original_fallthrough(agent: VoolAgent) -> None:
    """Proves the fix is load-bearing: with `_live_data_plan_unavailable_result` forced to behave
    like the old code (returning None instead of a deterministic result), the exact same LIVE_DATA
    turn that item 2 protects now DOES fall through to the ordinary ungrounded turn path --
    reproducing the original incident's mechanism -- confirming the guard is what prevents it."""
    reached: dict[str, bool] = {"frontdoor": False}

    def _mark_reached(*_args, **_kwargs):
        reached["frontdoor"] = True
        return {"response": "ok", "route": "test", "session_id": "x", "source_context": {}}

    with mock.patch("core.agent_runtime.agent.VoolAgent._handle_turn_frontdoor", side_effect=_mark_reached), \
         mock.patch("core.agent_runtime.agent.VoolAgent._live_data_plan_unavailable_result", return_value=None), \
         mock.patch("core.agent_runtime.live_data_plan.build_live_data_plan", return_value=None):
        agent.run_once(
            "Market data only for Bitcoin and gold.",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
    assert reached["frontdoor"] is True
