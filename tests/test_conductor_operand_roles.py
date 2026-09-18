"""Operand ROLES, not operand ORDER, decide a purchasable-amount derivation.

Measured live 2026-09-06 (r5 bundle, private profile, real quotes): "what is the price of silver?
how much of gold I can buy if I sell 1 BTC now?" planned three correct quotes and then computed
`79,678 / 66.75 = 1,193.6779 troy ounces` -- one Bitcoin divided by the SILVER price, labelled
"Gold amount". Two positional habits compounded: the planner's target fell back to "the first
alias that is not the payment asset" (silver, from the sibling ask) once `how much <word>`
captured "of", and the fallback took "the first dependency with a price" as its divisor.

Every test here asserts the OPERANDS THAT WERE SELECTED and the COMPUTED VALUE, never a label or a
successful return alone. No test names a special case for "how much of gold": the target is the
asset in the purchase's target role, resolved through the same alias tables as every other asset.
"""
from __future__ import annotations

import itertools
from types import SimpleNamespace

import pytest

LIVE = "what is the price of silver? how much of gold I can buy if I sell 1 BTC now?"
SWAPPED = "how much of gold I can buy if I sell 1 BTC now? what is the price of silver?"
SILVER, BTC, GOLD = 66.75, 79678.0, 4476.6
EXPECTED = BTC / GOLD  # ~17.7988 troy ounces


class _Ctx:
    def __init__(self, deps):
        self.dependency_results = deps


def _deps(order):
    table = {
        "silver": {"price": SILVER, "currency": "USD", "source": "Yahoo Finance"},
        "bitcoin": {"price": BTC, "currency": "USD", "source": "CoinGecko"},
        "gold": {"price": GOLD, "currency": "USD", "source": "Yahoo Finance"},
    }
    # Market results carry no entity echo; the canonical asset key rides the dependency id.
    return {f"plan:market_quote:{key}": dict(table[key]) for key in order}


def _plan(text):
    from core.conductor.planner import _deterministic_purchasable_amount_plan

    return _deterministic_purchasable_amount_plan(text)


def _quotes(plan):
    """Canonical asset keys the plan quotes; the minter writes each clause as `price of <alias>`."""
    from core.live_data_plan import _resolve_price_alias

    keys = set()
    for clause in plan:
        if clause.operation != "market_quote":
            continue
        alias = clause.request.split("price of ", 1)[-1].strip().casefold()
        resolved = _resolve_price_alias(alias)
        keys.add(resolved[0] if resolved else alias)
    return keys


def _quant(plan):
    found = [c for c in plan if c.operation == "quantitative_reasoning"]
    assert len(found) == 1, plan
    return found[0]


def _binding(text, deps):
    from core.conductor.operations import purchasable_amount_binding

    return purchasable_amount_binding(text, _Ctx(deps))


# -- the exact live failure -----------------------------------------------------------------------


def test_the_live_silver_gold_btc_turn_divides_by_the_gold_price():
    from core.conductor.operations import _purchasable_amount_fallback

    plan = _plan(LIVE)
    assert {"silver", "bitcoin", "gold"} <= _quotes(plan)
    _quant(plan)
    # Dependency order exactly as the live plan delivered it: silver, bitcoin, gold.
    out = _purchasable_amount_fallback(LIVE, _Ctx(_deps(["silver", "bitcoin", "gold"])))
    assert out is not None
    label, value, unit, expression = out
    assert value == pytest.approx(EXPECTED, rel=1e-9), expression
    assert "4,476.6" in expression and "66.75" not in expression
    assert label == "Gold amount (troy ounces)" and unit == "troy ounces"


def test_the_selected_operands_are_the_target_and_payment_assets():
    binding = _binding(LIVE, _deps(["silver", "bitcoin", "gold"]))
    assert binding.target.dep_id == "plan:market_quote:gold"
    assert binding.target.asset_key == "gold" and binding.target.price == GOLD
    assert binding.payment.dep_id == "plan:market_quote:bitcoin"
    assert binding.payment.asset_key == "bitcoin" and binding.payment.price == BTC
    assert binding.quantity == 1.0
    assert binding.value == pytest.approx(EXPECTED, rel=1e-9)
    assert binding.target.source == "Yahoo Finance" and binding.payment.source == "CoinGecko"


@pytest.mark.parametrize("order", list(itertools.permutations(["silver", "bitcoin", "gold"])))
def test_dependency_order_never_changes_the_operands(order):
    binding = _binding(LIVE, _deps(list(order)))
    assert binding.target.dep_id.endswith(":gold")
    assert binding.payment.dep_id.endswith(":bitcoin")
    assert binding.value == pytest.approx(EXPECTED, rel=1e-9)


def test_reordered_independent_clauses_bind_the_same_roles():
    plan = _plan(SWAPPED)
    assert {"silver", "bitcoin", "gold"} <= _quotes(plan)
    binding = _binding(SWAPPED, _deps(["silver", "gold", "bitcoin"]))
    assert binding.target.dep_id.endswith(":gold")
    assert binding.payment.dep_id.endswith(":bitcoin")
    assert binding.value == pytest.approx(EXPECTED, rel=1e-9)


def test_an_unrelated_sibling_asset_is_quoted_but_never_an_operand():
    text = "what is eth price? how much gold can I buy with 10 bnb"
    plan = _plan(text)
    assert {"ethereum", "binancecoin", "gold"} <= _quotes(plan)
    deps = {
        "plan:market_quote:ethereum": {"price": 4321.0, "currency": "USD"},
        "plan:market_quote:binancecoin": {"price": 693.79, "currency": "USD"},
        "plan:market_quote:gold": {"price": GOLD, "currency": "USD"},
    }
    binding = _binding(text, deps)
    assert binding.target.dep_id.endswith(":gold")
    assert binding.payment.dep_id.endswith(":binancecoin")
    assert binding.quantity == 10.0
    assert binding.value == pytest.approx(10 * 693.79 / GOLD, rel=1e-9)
    assert "ethereum" not in {binding.target.asset_key, binding.payment.asset_key}


# -- ambiguous or missing roles refuse the derivation; the quotes still stand ------------------


def test_a_missing_target_refuses_the_derivation_and_keeps_the_quotes():
    from core.conductor.operations import _purchasable_amount_fallback

    text = "what is the price of silver? how much can I buy if I sell 1 BTC now?"
    plan = _plan(text)
    assert {"silver", "bitcoin"} <= _quotes(plan)
    _quant(plan)
    with pytest.raises(ValueError) as caught:
        _purchasable_amount_fallback(text, _Ctx(_deps(["silver", "bitcoin"])))
    assert "not named" in str(caught.value)


def test_two_candidate_targets_are_ambiguous_and_refused():
    from core.conductor.operations import _purchasable_amount_fallback

    text = "how much gold or silver can I buy with 1 btc?"
    plan = _plan(text)
    assert {"gold", "silver", "bitcoin"} <= _quotes(plan)
    with pytest.raises(ValueError) as caught:
        _purchasable_amount_fallback(text, _Ctx(_deps(["gold", "silver", "bitcoin"])))
    message = str(caught.value).casefold()
    assert "gold" in message and "silver" in message


# -- the target role is grammatical, resolved through the alias tables, never a word list ------


@pytest.mark.parametrize(
    "text",
    [
        "how much gold can i buy with 1 btc",
        "how much of gold can i buy with 1 btc",
        "how many ounces of gold can i buy with 1 btc",
        "how much in gold can i get for 1 btc",
    ],
)
def test_partitive_and_unit_words_do_not_hide_the_target(text):
    from core.conductor.operations import resolve_purchase_roles

    roles = resolve_purchase_roles(text)
    assert roles is not None and roles.problem == ""
    assert roles.target.key == "gold"
    assert roles.payment.key == "bitcoin" and roles.quantity == 1.0


def test_a_non_purchase_text_is_not_this_shape():
    from core.conductor.operations import resolve_purchase_roles

    assert resolve_purchase_roles("what is the price of gold and silver?") is None


# -- the step receipt carries the validated binding; a mis-bound step is never published --------


def test_the_step_receipt_carries_validated_operands():
    binding = _binding(LIVE, _deps(["silver", "bitcoin", "gold"]))
    step = binding.as_step()
    assert step["label"] == "Gold amount (troy ounces)"
    assert step["value"] == pytest.approx(EXPECTED, rel=1e-9)
    ops = step["operands"]
    assert ops["target"]["dep_id"] == "plan:market_quote:gold"
    assert ops["target"]["entity"] == "Gold" and ops["target"]["price"] == GOLD
    assert ops["payment"]["dep_id"] == "plan:market_quote:bitcoin"
    assert ops["payment"]["quantity"] == 1.0
    assert ops["target"]["currency"] == ops["payment"]["currency"] == "USD"


def test_a_step_whose_label_disagrees_with_its_divisor_is_withheld():
    from core.conductor.operations import _quantitative_render, step_binding_problem

    binding = _binding(LIVE, _deps(["silver", "bitcoin", "gold"]))
    step = binding.as_step()
    # Corrupt the binding the way the live defect did: the label says Gold, the divisor is silver.
    step["operands"]["target"] = {
        **step["operands"]["target"],
        "dep_id": "plan:market_quote:silver",
        "entity": "Silver",
        "asset_key": "silver",
        "price": SILVER,
    }
    step["expression"] = "1 x 79,678 / 66.75"
    step["value"] = BTC / SILVER
    assert step_binding_problem(step) is not None
    node = SimpleNamespace(arguments={}, depends_on=tuple(_deps(["silver", "bitcoin", "gold"])))
    rendered = _quantitative_render(node, {"steps": [step], "cannot_determine": []})
    assert "not determined" in rendered
    assert "1,193" not in rendered and "66.75" not in rendered


def test_a_divisor_taken_from_another_assets_node_is_withheld_even_under_the_right_label():
    """The sabotage shape: label and role both say Gold, but the price came from the silver node."""
    from core.conductor.operations import _quantitative_render, step_binding_problem

    binding = _binding(LIVE, _deps(["silver", "bitcoin", "gold"]))
    step = binding.as_step()
    step["operands"]["target"] = {
        **step["operands"]["target"],
        "dep_id": "plan:market_quote:silver",
        "price": SILVER,
    }
    step["value"] = BTC / SILVER
    problem = step_binding_problem(step)
    assert problem is not None and "different asset" in problem
    node = SimpleNamespace(arguments={}, depends_on=tuple(_deps(["silver", "bitcoin", "gold"])))
    rendered = _quantitative_render(node, {"steps": [step], "cannot_determine": []})
    assert "not determined" in rendered and "1,193" not in rendered


def test_a_step_bound_to_a_dependency_the_node_never_declared_is_withheld():
    from core.conductor.operations import _quantitative_render, step_binding_problem

    binding = _binding(LIVE, _deps(["silver", "bitcoin", "gold"]))
    step = binding.as_step()
    node = SimpleNamespace(arguments={}, depends_on=("plan:market_quote:silver",))
    assert step_binding_problem(step, depends_on=node.depends_on) is not None
    rendered = _quantitative_render(node, {"steps": [step], "cannot_determine": []})
    assert "not determined" in rendered and "17.79" not in rendered


# -- the node itself: a role problem is a stated `cannot_determine`, not an internal fault ------


def _quant_node(text, deps, roles):
    from core.conductor.node import ConductorNode

    return ConductorNode(
        node_id="plan:quantitative_reasoning:q",
        operation="quantitative_reasoning",
        request_text=text,
        arguments={"clause": text, "roles": roles},
        depends_on=tuple(deps),
        required_result_fields=("steps", "values"),
        needs_generation=True,
    )


def _node_ctx(deps):
    from core.conductor.registry import NodeContext

    return NodeContext(
        dependency_results=deps,
        derived_facts={f"{k}:price": v["price"] for k, v in deps.items()},
        run_generation=lambda system, prompt: "{}",
    )


def test_the_node_computes_the_live_turn_with_bound_operands_on_its_step():
    from core.conductor.operations import _quantitative_run, resolve_purchase_roles

    deps = _deps(["silver", "bitcoin", "gold"])
    roles = resolve_purchase_roles(LIVE).as_argument()
    result = _quantitative_run(_quant_node(LIVE, deps, roles), _node_ctx(deps))
    assert result["cannot_determine"] == []
    (step,) = result["steps"]
    assert step["value"] == pytest.approx(EXPECTED, rel=1e-9)
    assert step["operands"]["target"]["dep_id"] == "plan:market_quote:gold"
    assert step["operands"]["payment"]["dep_id"] == "plan:market_quote:bitcoin"
    assert result["values"] == {"Gold amount (troy ounces)": step["value"]}


def test_the_node_states_a_missing_target_instead_of_failing():
    from core.conductor.operations import _quantitative_render, _quantitative_run, resolve_purchase_roles

    text = "what is the price of silver? how much can I buy if I sell 1 BTC now?"
    deps = _deps(["silver", "bitcoin"])
    roles = resolve_purchase_roles(text).as_argument()
    node = _quant_node(text, deps, roles)
    result = _quantitative_run(node, _node_ctx(deps))
    assert result["steps"] == [] and result["values"] == {}
    assert result["cannot_determine"] == [
        "the asset to buy is not named, so the purchasable amount cannot be computed"
    ]
    rendered = _quantitative_render(node, result)
    assert rendered == "not determined: the asset to buy is not named, so the purchasable amount cannot be computed"


def test_the_node_states_an_ambiguous_target_instead_of_failing():
    from core.conductor.operations import _quantitative_run, resolve_purchase_roles

    text = "how much gold or silver can I buy with 1 btc?"
    deps = _deps(["gold", "silver", "bitcoin"])
    roles = resolve_purchase_roles(text).as_argument()
    result = _quantitative_run(_quant_node(text, deps, roles), _node_ctx(deps))
    assert result["steps"] == []
    assert len(result["cannot_determine"]) == 1
    reason = result["cannot_determine"][0].casefold()
    assert "ambiguous" in reason and "gold" in reason and "silver" in reason


# -- a ratio over two prices is NOT this shape: no money anywhere, so the binding steps aside -----


@pytest.mark.parametrize(
    "text",
    [
        "get the price of eth and solana, then work out how many solana one eth buys",
        "calculate exactly how many ounces of gold one btc buys",
    ],
)
def test_a_ratio_with_no_money_anywhere_is_left_to_the_expression_path(text):
    """Regression caught by the conductor pack: the no-payment branch refused these with "this
    needs the amount expressed in USD" where the derived-facts expression path had served them."""
    from core.conductor.operations import purchasable_amount_binding

    deps = {
        "obl:market_quote:ethereum": {"price": 4321.0, "currency": "USD"},
        "obl:market_quote:solana": {"price": 210.0, "currency": "USD"},
        "obl:market_quote:gold": {"price": GOLD, "currency": "USD"},
        "obl:market_quote:bitcoin": {"price": BTC, "currency": "USD"},
    }
    assert purchasable_amount_binding(text, _Ctx(deps)) is None
