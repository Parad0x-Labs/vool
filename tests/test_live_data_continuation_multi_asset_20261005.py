"""Continuation preserves every authorized subject and refuses domain changes."""
from __future__ import annotations

import uuid

import pytest

from core.agent_runtime.live_data_plan import build_live_data_plan
from core.live_data_continuation import continuation_inherits_live_data
from core.runtime_continuity import remember_live_data_obligation


def _context(text, *, operation="market_quote", asset="Silver", request="silver price"):
    sid = "continuation-multi-" + uuid.uuid4().hex
    remember_live_data_obligation(sid, operation=operation, slots=[asset],
                                  request_text=request, absorbed_text=request)
    return {"session_id": sid, "runtime_session_id": sid,
            "conversation_history": [{"role": "user", "content": request},
                                     {"role": "user", "content": text}]}


@pytest.mark.parametrize("text,assets", [
    ("gold and silver prices", {"gold", "silver"}),
    ("gold and silver price", {"gold", "silver"}),
    ("btc and eth prices", {"bitcoin", "ethereum"}),
    ("btc and eth price", {"bitcoin", "ethereum"}),
    ("and btc and eth?", {"bitcoin", "ethereum"}),
    ("and gold, silver, btc?", {"gold", "silver", "bitcoin"}),
    ("and BTC, ETH, SOL?", {"bitcoin", "ethereum", "solana"}),
    ("gold & btc price", {"gold", "bitcoin"}),
])
def test_every_current_subject_reaches_the_plan(text, assets):
    context = _context(text)
    inherited = continuation_inherits_live_data(text, source_context=context)
    plan = build_live_data_plan(inherited or text, plan_id="p", attempt_id="a")
    assert plan is not None, (text, inherited)
    actual = {task.arguments["asset_key"] for task in plan.market_subtasks()
              if task.operation == "market_quote"}
    assert actual == assets, (text, inherited, actual)


@pytest.mark.parametrize("text,asset", [
    ("and silver?", "silver"), ("what about ethereum?", "ethereum"),
    ("or solana?", "solana"), ("not gold, silver?", "silver"),
])
def test_one_subject_still_rebinds_and_negated_subjects_do_not(text, asset):
    inherited = continuation_inherits_live_data(text, source_context=_context(
        text, asset="Gold", request="gold price"))
    plan = build_live_data_plan(inherited, plan_id="p", attempt_id="a")
    assert plan is not None, (text, inherited)
    assert {t.arguments["asset_key"] for t in plan.market_subtasks()} == {asset}


@pytest.mark.parametrize("text", ["Gold and Silver price", "BTC and ETH prices",
                                      "and the gold price?"])
def test_price_request_does_not_rebind_a_weather_obligation(text):
    context = _context(text, operation="weather_lookup", asset="Porto",
                       request="weather in Porto")
    assert not continuation_inherits_live_data(text, source_context=context)


@pytest.mark.parametrize("text", ["write a poem about gold", "gold mining history",
                                      "and the weather there?", "and unknownsubject?"])
def test_an_unrelated_request_does_not_inherit_the_quote(text):
    assert not continuation_inherits_live_data(text, source_context=_context(text))


def test_set_reference_reasks_the_grounded_set_without_inventing_an_asset():
    text = "and those?"
    context = _context(text, asset="Gold", request="gold and silver price")
    remember_live_data_obligation(context["session_id"], operation="market_quote",
                                  slots=["Gold", "Silver"], request_text="gold and silver price",
                                  absorbed_text="gold and silver price")
    inherited = continuation_inherits_live_data(text, source_context=context)
    plan = build_live_data_plan(inherited, plan_id="p", attempt_id="a")
    assert plan is not None
    assert {t.arguments["asset_key"] for t in plan.market_subtasks()} == {"gold", "silver"}
