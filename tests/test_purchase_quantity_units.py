"""A payment leg stated in a physical unit is a QUANTITY of the asset, never the asset itself.

Measured on the owner's turn (2026-09-10, build 772256a7): "what is the Brent oil price? how much i
can buy of oil if i sell 1kg of silver? and how much eth as well" read "kg" as the payment ASSET
("the payment leg 'kg' is not an asset or currency this runtime can price"), the deterministic
purchase arm stood down, a model planner labelled both purchase clauses as explanations, and the
turn shipped one quote plus two "ran out of time" rows. The grammar now reads an optional unit
between the amount and the asset, the roles carry it, and the binding converts it into the asset's
own price unit -- troy ounces for metals, barrels for crude -- with the working shown.

Every figure asserted below is computed from the stated operands, never matched as a label. The
owner's phrasing appears exactly once (the served class); every other test uses fresh wording.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from core.conductor.operations import (
    _quantitative_render,
    distributed_purchase_roles,
    purchasable_amount_binding,
    purchasable_amount_leg,
    purchasable_amount_operands,
    resolve_purchase_roles,
    step_binding_problem,
)

TROY_OZ_PER_KG = 1000.0 / 31.1034768
TROY_OZ_PER_G = 1.0 / 31.1034768
TROY_OZ_PER_LB = 453.59237 / 31.1034768
BARRELS_PER_LITRE = 1.0 / 158.987294928


class _Ctx:
    def __init__(self, deps):
        self.dependency_results = deps
        self.derived_facts = {}


def _deps(**prices):
    return {
        f"plan:market_quote:{key}": {"price": price, "currency": "USD", "source": "test feed"}
        for key, price in prices.items()
    }


# -- the grammar ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "leg, quantity, unit, asset",
    [
        ("if i sell 1kg of silver", 1.0, "kg", "silver"),
        ("if I sell 1 kg of silver", 1.0, "kg", "silver"),
        ("with 2 ounces of gold", 2.0, "troy oz", "gold"),
        ("with 1.5oz gold", 1.5, "troy oz", "gold"),
        ("using 500 grams of silver", 500.0, "g", "silver"),
        ("with one kilogram of silver", 1.0, "kg", "silver"),
        ("if i have 10 barrels of oil", 10.0, "barrel", "oil"),
        ("with 3 lb of silver", 3.0, "lb", "silver"),
        ("with 40 litres of crude", 40.0, "litre", "crude"),
        ("with 1 eth", 1.0, "", "eth"),
        ("with 10 bnb", 10.0, "", "bnb"),
    ],
)
def test_the_amount_grammar_reads_an_optional_unit(leg, quantity, unit, asset):
    assert purchasable_amount_leg(leg) == (quantity, unit, asset)
    # The unit-blind view every existing caller reads is unchanged in shape.
    assert purchasable_amount_operands(leg) == (quantity, asset)


def test_a_bare_asset_word_is_never_split_into_a_unit():
    """"1 gold" is one gold, not the unit "g" and the asset "old"; "2 sol" is Solana."""
    assert purchasable_amount_leg("with 1 gold") == (1.0, "", "gold")
    assert purchasable_amount_leg("with 2 sol") == (2.0, "", "sol")
    assert purchasable_amount_leg("with 5 tons of silver") == (5.0, "tonne", "silver")


def test_the_owner_prompt_resolves_silver_by_the_kilogram_as_the_payment():
    roles = resolve_purchase_roles("how much i can buy of oil if i sell 1kg of silver?")
    assert roles is not None and not roles.problem, roles
    assert roles.payment is not None and roles.payment.key == "silver"
    assert roles.quantity == 1.0 and roles.quantity_unit == "kg"
    assert roles.target is not None and roles.target.key == "brent_crude"


def test_the_elliptical_second_target_after_the_kilogram_leg_distributes():
    """"... if i sell 1kg of silver? and how much eth as well" is two derivations over the same
    kilogram of silver -- the shape `distributed_purchase_roles` already served for "and"."""
    fanned = distributed_purchase_roles(
        "what does brent trade at? how much crude could I get if I sell 2 kg of silver? and how much sol as well"
    )
    assert sorted(item.target.key for item in fanned) == ["brent_crude", "solana"]
    assert {(item.payment.key, item.quantity, item.quantity_unit) for item in fanned} == {
        ("silver", 2.0, "kg")
    }


# -- the dimensional arithmetic -------------------------------------------------------------------


def test_kilograms_of_a_metal_are_converted_to_troy_ounces_before_the_price_applies():
    clause = "how much oil can I buy with 1 kg silver"
    roles = resolve_purchase_roles(clause)
    binding = purchasable_amount_binding(
        clause, _Ctx(_deps(silver=64.67, brent_crude=106.78)), roles=roles
    )
    assert binding is not None
    assert binding.quantity == pytest.approx(TROY_OZ_PER_KG)
    assert binding.stated_quantity == 1.0 and binding.stated_unit == "kg"
    assert binding.amount == pytest.approx(TROY_OZ_PER_KG * 64.67)
    assert binding.value == pytest.approx(TROY_OZ_PER_KG * 64.67 / 106.78)
    assert binding.unit == "barrels" and binding.label == "Brent crude amount (barrels)"
    assert binding.expression.startswith("1 kg = 32.1507 troy ounces; 32.1507 x 64.67 / 106.78")
    # The published step still passes the defence-in-depth check over its own operands.
    step = binding.as_step()
    assert step_binding_problem(step, depends_on=list(_deps(silver=1, brent_crude=1))) is None
    assert step["operands"]["stated_unit"] == "kg" and step["operands"]["stated_quantity"] == 1.0


@pytest.mark.parametrize(
    "clause, factor",
    [
        ("how much btc can I get for 500 grams of gold", 500.0 * TROY_OZ_PER_G),
        ("how much btc can I get for 3 pounds of gold", 3.0 * TROY_OZ_PER_LB),
        ("how much btc can I get for 2 troy ounces of gold", 2.0),
    ],
)
def test_each_mass_unit_converts_by_its_own_factor(clause, factor):
    binding = purchasable_amount_binding(clause, _Ctx(_deps(gold=4402.0, bitcoin=78497.0)))
    assert binding is not None
    assert binding.quantity == pytest.approx(factor)
    assert binding.value == pytest.approx(factor * 4402.0 / 78497.0)


def test_a_quantity_already_in_the_price_unit_is_not_rewritten():
    binding = purchasable_amount_binding(
        "how much btc can I buy with 2 oz of gold", _Ctx(_deps(gold=4402.0, bitcoin=78497.0))
    )
    assert binding is not None
    assert binding.quantity == 2.0 and binding.stated_quantity is None and binding.stated_unit == ""
    assert ";" not in binding.expression


def test_litres_of_crude_convert_to_barrels():
    binding = purchasable_amount_binding(
        "how much silver can I buy with 500 litres of brent",
        _Ctx(_deps(brent_crude=106.78, silver=64.67)),
    )
    assert binding is not None
    assert binding.quantity == pytest.approx(500.0 * BARRELS_PER_LITRE)
    assert binding.unit == "troy ounces"


@pytest.mark.parametrize(
    "clause",
    [
        "how much gold can I buy with 1 kg of eth",
        "how much oil can I buy with 2 barrels of gold",
        "how much gold can I buy with 3 litres of silver",
    ],
)
def test_a_unit_that_does_not_measure_the_asset_refuses_with_its_reason(clause):
    roles = resolve_purchase_roles(clause)
    assert roles is not None and roles.problem, (clause, roles)
    assert "unit of" in roles.problem and "cannot be valued" in roles.problem
    assert distributed_purchase_roles(clause.replace("how much", "how much silver and how much")) == ()


def test_an_unpriceable_named_target_is_reported_by_name_and_an_unnamed_one_as_unnamed():
    named = resolve_purchase_roles("how much copper can I buy with 10 bnb")
    assert named is not None and named.problem.startswith(
        "'copper' is not an asset this runtime can price"
    )
    unnamed = resolve_purchase_roles("how much can I buy with 10 bnb")
    assert unnamed is not None and unnamed.problem == (
        "the asset to buy is not named, so the purchasable amount cannot be computed"
    )


# -- the plan and the run -------------------------------------------------------------------------


def test_the_deterministic_plan_carries_the_unit_into_each_derivation():
    from core.conductor.planner import _deterministic_purchasable_amount_plan

    plan = _deterministic_purchasable_amount_plan(
        "what is the Brent oil price? how much i can buy of oil if i sell 1kg of silver? and how much eth as well"
    )
    assert {c.request for c in plan if c.operation == "market_quote"} == {
        "price of oil",
        "price of eth",
        "price of silver",
    }
    derivations = [c for c in plan if c.operation == "quantitative_reasoning"]
    assert len(derivations) == 2
    for clause in derivations:
        roles = resolve_purchase_roles(clause.request)
        assert roles is not None and not roles.problem, (clause.request, roles)
        assert roles.payment.key == "silver" and roles.quantity == 1.0 and roles.quantity_unit == "kg"
    assert sorted(resolve_purchase_roles(c.request).target.key for c in derivations) == [
        "brent_crude",
        "ethereum",
    ]


_QUOTES = {
    "brent_crude": {"price": 106.78, "currency": "USD", "source": "Yahoo Finance"},
    "silver": {"price": 64.67, "currency": "USD", "source": "Yahoo Finance"},
    "ethereum": {"price": 2433.8, "currency": "USD", "source": "CoinGecko"},
}


def _fake_market(subtask, timeout_s=None):
    key = str(
        getattr(subtask, "asset_key", "") or getattr(subtask, "entity", "") or ""
    ).casefold().replace(" ", "_")
    assert key, "the market subtask names no asset"
    for name, quote in _QUOTES.items():
        if name in key or key in name:
            return SimpleNamespace(result=dict(quote, asset_key=name), failure_reason="")
    return SimpleNamespace(result=None, failure_reason=f"no quote for {key}")


def _never(_system, prompt):
    raise AssertionError("a model was consulted: " + prompt[:80])


def test_the_owner_turn_computes_both_amounts_with_no_model_and_no_generation_seam():
    """The served class end to end: plan, run, compose -- with every model seam raising."""
    from core.conductor.planner import plan_conductor_turn
    from core.conductor.registry import NodeContext
    from core.conductor.scheduler import run_conductor_plan
    from tests.conductor_product import compose_product

    text = "what is the Brent oil price? how much i can buy of oil if i sell 1kg of silver? and how much eth as well"
    plan = plan_conductor_turn(text, ask_model=_never, propose_semantics=_never, plan_id="units")
    assert plan is not None and len(plan.nodes) == 5, plan and [n.node_id for n in plan.nodes]
    derivations = [n for n in plan.nodes if n.operation == "quantitative_reasoning"]
    assert derivations and all(not n.needs_generation for n in derivations)
    with mock.patch("core.agent_runtime.live_data_runner._run_market_subtask", _fake_market):
        outcomes = run_conductor_plan(
            plan, context=NodeContext(run_generation=None, timeout_s=5.0), plan_deadline_s=10.0
        )
    assert all(outcome.succeeded for outcome in outcomes), [
        (o.node.node_id, o.failure_reason) for o in outcomes if not o.succeeded
    ]
    product = compose_product(plan, outcomes)
    assert product.disposition.value == "fulfilled"
    oil = TROY_OZ_PER_KG * 64.67 / 106.78
    eth = TROY_OZ_PER_KG * 64.67 / 2433.8
    assert f"= {oil:,.4f} barrels" in product.text, product.text
    assert f"= {eth:,.4f}" in product.text, product.text
    assert "1 kg = 32.1507 troy ounces" in product.text
    assert product.text.count("benchmark conversion") == 2
    assert "Could not be answered" not in product.text


def test_the_benchmark_note_is_stated_for_a_commodity_and_not_for_a_pure_crypto_pair():
    from core.conductor.node import ConductorNode

    node = ConductorNode(node_id="n", operation="quantitative_reasoning", request_text="x")
    commodity = purchasable_amount_binding(
        "how much oil can I buy with 1 kg silver", _Ctx(_deps(silver=64.67, brent_crude=106.78))
    )
    crypto = purchasable_amount_binding(
        "how much sol can I buy with 1 eth", _Ctx(_deps(ethereum=2433.8, solana=140.0))
    )
    rendered_commodity = _quantitative_render(
        node, {"steps": [commodity.as_step()], "values": {}, "cannot_determine": []}
    )
    rendered_crypto = _quantitative_render(
        node, {"steps": [crypto.as_step()], "values": {}, "cannot_determine": []}
    )
    assert "benchmark conversion" in rendered_commodity
    assert "not a retail purchase quote" in rendered_commodity
    assert "benchmark conversion" not in rendered_crypto
