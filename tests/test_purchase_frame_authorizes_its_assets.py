"""A mention the purchasing-power grammar binds to a role is a market asset, price word or not.

Measured on the built candidate 60a91da3 (isolated served drive, 2026-09-07): "If I sell half a
bitcoin, how many ounces of silver does that get me?" named no asset -- the market authority gate
looks for a price word beside the mention, and a payment-leg sentence carries none -- so the
conductor's deterministic arm had nothing to quote and the turn fell to an uncertified model lane.
The purchase shape is read in ONE place, `core.conductor.operations.resolve_purchase_roles`; the
gate now asks it. Only a fully resolved frame authorizes: a payment with no priceable target ("I have
a dash of salt, how much pepper can I get") stays an ordinary sentence.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.fast_live_info_price import price_assets_named
from core.conductor.operations import resolve_purchase_roles
from core.conductor.planner import _deterministic_purchasable_amount_plan


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("If I sell half a bitcoin, how many ounces of silver does that get me?", {"bitcoin", "silver"}),
        ("how many ounces of silver can I get for half a bitcoin", {"bitcoin", "silver"}),
        ("how much gold do I get for twenty five eth", {"eth", "gold"}),
    ],
)
def test_payment_leg_phrasings_name_their_assets(text: str, expected: set[str]) -> None:
    assert set(price_assets_named(text)) == expected


def test_a_cooking_sentence_with_a_coin_alias_stays_ordinary() -> None:
    assert price_assets_named("I have a dash of salt, how much pepper can I get") == []


def test_a_payment_leg_phrasing_is_planned_deterministically() -> None:
    plan = _deterministic_purchasable_amount_plan("If I sell half a bitcoin, how many ounces of silver does that get me?")
    prices = {clause.request for clause in plan if clause.operation == "market_quote"}
    derivations = [clause for clause in plan if clause.operation == "quantitative_reasoning"]
    assert prices == {"price of silver", "price of bitcoin"}, plan
    assert len(derivations) == 1
    roles = resolve_purchase_roles(derivations[0].request)
    assert roles is not None and not roles.problem
    assert (roles.target.entity, roles.payment.entity, roles.quantity) == ("Silver", "Bitcoin", 0.5)
