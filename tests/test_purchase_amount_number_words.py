"""A payment amount written as a number word is an amount.

Measured on the built candidate 60a91da3 (isolated bundle drive, 2026-09-07): "How much silver can
I buy with one bitcoin right now?" produced no payment operand -- the amount grammar accepted digits
only -- so the conductor declined, the quote lane served a silver price, and the question went
unanswered under a complete-looking turn. Cardinal words are folded to digits at the grammar
boundary; nothing else about role resolution changes.
"""
from __future__ import annotations

import pytest

from core.conductor.operations import (
    purchasable_amount_operands,
    purchasable_target_operands,
    resolve_purchase_roles,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("How much silver can I buy with one bitcoin right now?", (1.0, "bitcoin")),
        ("how much gold can i get for twenty five eth", (25.0, "eth")),
        ("how much gold can i buy with two hundred sol", (200.0, "sol")),
        ("if I spend half a bitcoin how much gold is that", (0.5, "bitcoin")),
        ("how much silver can I buy with a bitcoin", (1.0, "bitcoin")),
        ("how much gold can I buy with 1 btc", (1.0, "btc")),
    ],
)
def test_number_word_amounts_are_payment_operands(text: str, expected: tuple[float, str]) -> None:
    assert purchasable_amount_operands(text) == expected


def test_an_article_before_a_non_asset_is_not_an_amount() -> None:
    # "a friend" is not a payment leg; the article folds to 1 only before something priceable.
    assert purchasable_amount_operands("how much gold can I buy for a friend") is None


def test_inverse_shape_accepts_number_words() -> None:
    assert purchasable_target_operands("how much gold do I need to sell to buy one bitcoin") == (1.0, "bitcoin")


def test_roles_resolve_with_a_number_word_payment() -> None:
    roles = resolve_purchase_roles("How much silver can I buy with one bitcoin right now?")
    assert roles is not None and not roles.problem
    assert roles.target is not None and roles.target.entity == "Silver"
    assert roles.payment is not None and roles.payment.entity == "Bitcoin"
    assert roles.quantity == 1.0
