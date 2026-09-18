"""The deterministic purchase arm plans the operator's own phrasing, not only the fixture's.

Measured live 2026-09-07 (packaged app, typed live-data lane): "what is the price of oil now and
how much of oil i can buy if i have 1 btc or 1 eth?" produced three quotes and NO purchasable
amount -- the derivation never entered the plan. Two causes, both in the arm's own scoping and
grammar: the purchase span rejected the first unit because "oil" is not in the fast alias table
the belonging test reads (the roles resolver knows it), and a payment leg of two payers joined
by "or" was quoted but derived only for the first.
"""
from __future__ import annotations

from core.conductor.planner import _deterministic_purchasable_amount_plan, _purchase_span_text

OPERATOR = "what is the price of oil now and how much of oil i can buy if i have 1 btc or 1 eth?"


def _shape(plan):
    return [(c.request, c.operation) for c in plan]


def test_the_mixed_oil_turn_is_claimed_whole_and_derived_for_each_payer() -> None:
    assert _purchase_span_text(OPERATOR), "the price-of-oil unit belongs to a purchase whose target is oil"
    shape = _shape(_deterministic_purchasable_amount_plan(OPERATOR))
    quotes = {req for req, op in shape if op == "market_quote"}
    derivations = [req for req, op in shape if op == "quantitative_reasoning"]
    assert {"price of oil", "price of btc", "price of eth"} <= quotes, shape
    assert derivations == ["how much oil can I buy with 1 btc", "how much oil can I buy with 1 eth"], shape


def test_two_payers_joined_by_or_each_get_a_derivation() -> None:
    shape = _shape(_deterministic_purchasable_amount_plan("how much gold can i buy if i have 1 btc or 1 eth"))
    derivations = [req for req, op in shape if op == "quantitative_reasoning"]
    assert derivations == ["how much gold can I buy with 1 btc", "how much gold can I buy with 1 eth"], shape


def test_a_single_payer_and_the_distributed_targets_are_unchanged() -> None:
    single = _shape(_deterministic_purchasable_amount_plan("how much gold can i buy with 1 btc"))
    assert [req for req, op in single if op == "quantitative_reasoning"] == ["how much gold can i buy with 1 btc"], single
    distributed = _shape(_deterministic_purchasable_amount_plan("how much gold and how much silver can I buy with one bitcoin"))
    assert [req for req, op in distributed if op == "quantitative_reasoning"] == [
        "how much gold can I buy with 1 bitcoin",
        "how much silver can I buy with 1 bitcoin",
    ], distributed


def test_a_unit_no_purchase_serves_still_leaves_the_turn_unclaimed() -> None:
    assert _purchase_span_text("how much gold can I buy with 10 bnb and what is the weather in Rome") == ""
