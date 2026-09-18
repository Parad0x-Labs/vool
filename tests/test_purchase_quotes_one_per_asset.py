"""One priced asset, one quote node -- whatever spellings the message uses for it.

Measured on the c5b8483a rig (controls G1/G7) and identical at 772256a7: "Brent price? And: how
much oil can I buy if I sell 1 eth!" minted `market_quote:brent_crude` from "price of oil" and
`market_quote:brent_crude_0` from "price of brent", and the answer printed the Brent quote twice.
The arm deduplicated its alias list by spelling; the identity that matters is the canonical asset
key the alias tables resolve to.
"""
from __future__ import annotations

from core.conductor.planner import _deterministic_purchasable_amount_plan, _one_alias_per_asset


def _quote_keys(plan):
    from core.agent_runtime.live_data_plan import _resolve_price_alias

    keys = []
    for clause in plan:
        if clause.operation != "market_quote":
            continue
        alias = clause.request.split("price of ", 1)[-1].strip().casefold()
        resolved = _resolve_price_alias(alias)
        keys.append(resolved[0] if resolved else alias)
    return keys


def test_three_spellings_of_brent_mint_one_quote():
    plan = _deterministic_purchasable_amount_plan(
        "what does brent trade at? how much crude could I get if I sell 2 sol? and how much silver as well"
    )
    keys = _quote_keys(plan)
    assert keys.count("brent_crude") == 1, keys
    assert sorted(keys) == ["brent_crude", "silver", "solana"]
    assert len([c for c in plan if c.operation == "quantitative_reasoning"]) == 2


def test_the_punctuated_owner_class_prompt_quotes_brent_once():
    plan = _deterministic_purchasable_amount_plan(
        "Brent price? And: how much oil can I buy if I sell 1kg of silver! thx"
    )
    keys = _quote_keys(plan)
    assert keys.count("brent_crude") == 1, keys
    assert "silver" in keys


def test_an_unresolvable_alias_is_kept_so_its_refusal_row_survives():
    assert _one_alias_per_asset(["oil", "brent", "copper", "crude", "eth", "ethereum"]) == (
        "oil",
        "copper",
        "eth",
    )
