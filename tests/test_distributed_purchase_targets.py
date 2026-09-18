"""Two targets coordinated by "and" under one payment are TWO derivations, not an ambiguity.

Measured on the built candidate 60a91da3 (isolated bundle drive, 2026-09-07): once the coordination
is read as one demand, "How much gold and how much silver can I buy with one bitcoin" resolved to
"the asset to buy is ambiguous (Gold, Silver)". It is not ambiguous: "and" distributes the shared
predicate over both objects. "or" still asks for one of them and stays a refusal with its reason.

An ELLIPTICAL additive ask across a sentence boundary -- "... if I sell 1 eth? also how much of
gold" -- distributes the same way (owner turn, 2026-09-10: it was read as one exclusive choice and
neither conversion was computed). The tests below use fresh wording and fresh assets on purpose:
the contract is the coordination shape, not any one phrasing of it.
"""
from __future__ import annotations

from core.conductor.operations import resolve_purchase_roles
from core.conductor.planner import _deterministic_purchasable_amount_plan


def _by_operation(plan, operation: str):
    return [clause for clause in plan if clause.operation == operation]


def test_and_coordinated_targets_mint_one_derivation_per_target() -> None:
    plan = _deterministic_purchasable_amount_plan(
        "How much gold and how much silver can I buy with one bitcoin right now?"
    )
    prices = _by_operation(plan, "market_quote")
    derivations = _by_operation(plan, "quantitative_reasoning")
    assert {clause.request for clause in prices} == {"price of gold", "price of silver", "price of bitcoin"}
    assert len(derivations) == 2
    targets = []
    for clause in derivations:
        roles = resolve_purchase_roles(clause.request)
        assert roles is not None and not roles.problem, (clause.request, roles)
        assert roles.payment is not None and roles.payment.entity == "Bitcoin"
        assert roles.quantity == 1.0
        targets.append(roles.target.entity)
        # every derivation declares the quotes it divides
        assert set(clause.depends_on) == {price.index for price in prices}
    assert sorted(targets) == ["Gold", "Silver"]


def test_or_coordinated_targets_stay_an_ambiguity_with_its_reason() -> None:
    plan = _deterministic_purchasable_amount_plan("How much gold or silver can I buy with one bitcoin?")
    derivations = _by_operation(plan, "quantitative_reasoning")
    assert len(derivations) == 1
    roles = resolve_purchase_roles(derivations[0].request)
    assert roles is not None and "ambiguous" in (roles.problem or "")


def test_a_single_target_plan_is_unchanged() -> None:
    plan = _deterministic_purchasable_amount_plan("how much gold can I buy with 1 btc")
    assert len(_by_operation(plan, "quantitative_reasoning")) == 1
    assert {clause.request for clause in _by_operation(plan, "market_quote")} == {"price of gold", "price of btc"}


def test_sentence_additive_asks_distribute_over_the_stated_payment() -> None:
    """"? also how much of gold" is a SECOND conversion of the same proceeds, not an exclusive
    choice between the targets. Fresh wording and fresh assets (sol/brent/gold, not the owner's
    eth/silver phrasing): the shape is the contract."""
    plan = _deterministic_purchasable_amount_plan(
        "what is a barrel of brent going for and how much brent could I grab if I sell 12 sol? also how much of gold"
    )
    prices = _by_operation(plan, "market_quote")
    derivations = _by_operation(plan, "quantitative_reasoning")
    assert {clause.request for clause in prices} == {
        "price of brent",
        "price of gold",
        "price of sol",
    }
    assert len(derivations) == 2
    for clause in derivations:
        roles = resolve_purchase_roles(clause.request)
        assert roles is not None and not roles.problem, (clause.request, roles)
        assert roles.target is not None and roles.payment is not None
        assert roles.payment.entity == "Solana"
        assert roles.quantity == 12.0
        assert set(clause.depends_on) == {price.index for price in prices}
    assert sorted(resolve_purchase_roles(c.request).target.entity for c in derivations) == [
        "Brent crude",
        "Gold",
    ]


def test_sentence_additive_order_does_not_decide_the_roles() -> None:
    """The elliptical ask may come FIRST and the stated-payment ask second; positions sort, they
    never rank."""
    plan = _deterministic_purchasable_amount_plan(
        "how much of gold would I end up holding? also, how much silver would I be able to buy for 2 btc"
    )
    derivations = _by_operation(plan, "quantitative_reasoning")
    assert len(derivations) == 2
    entities = sorted(
        resolve_purchase_roles(clause.request).target.entity  # type: ignore[union-attr]
        for clause in derivations
    )
    assert entities == ["Gold", "Silver"]


def test_a_divergent_second_payment_is_not_folded_into_one_distribution() -> None:
    """A second ask that states its OWN different sum is a different purchase; folding it into the
    first payment's fan-out would divide the wrong proceeds. The shape stays unclaimed here."""
    from core.conductor.operations import distributed_purchase_roles

    assert (
        distributed_purchase_roles(
            "how much silver can I buy if I sell 1 eth? also how much gold can I buy if I sell 2 eth"
        )
        == ()
    )


def test_sentence_alternative_stays_an_ambiguity() -> None:
    """"? or how much gold" is still an alternative between the targets, whatever the sentence
    boundary looks like."""
    plan = _deterministic_purchasable_amount_plan(
        "how much silver can I buy if I sell 1 eth? or how much of gold"
    )
    derivations = _by_operation(plan, "quantitative_reasoning")
    assert len(derivations) == 1
    roles = resolve_purchase_roles(derivations[0].request)
    assert roles is not None and "ambiguous" in (roles.problem or "")
