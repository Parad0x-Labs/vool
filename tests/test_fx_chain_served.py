"""Chained conversions served as a dependency chain — operator turn 4, measured
live 2026-08-30: "ok 10000 eur to gbp and then to ETH pls" served only the first
leg and disclosed the second as unclaimed. The chain is now planned
deterministically and leg 2 consumes leg 1's converted amount (Test Pack V2 L02:
later obligations consume earlier results rather than disappearing).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_chain_shape_detector():
    from core.conductor.operations import fx_chain_shape

    assert fx_chain_shape("ok 10000 eur to gbp and then to ETH pls")
    assert fx_chain_shape("100 usd to eur then to jpy")
    assert not fx_chain_shape("10000 eur to gbp")
    assert not fx_chain_shape("convert 100 usd to eur")


def test_fiat_chain_mints_two_linked_legs():
    from core.conductor.planner import _deterministic_fx_chain_plan

    clauses = _deterministic_fx_chain_plan("ok 10000 eur to gbp and then to jpy pls")
    assert len(clauses) == 2
    assert clauses[0].operation == "fx_quote" and clauses[1].operation == "fx_quote"
    assert clauses[1].depends_on == (0,), "leg 2 must consume leg 1"


def test_crypto_endpoint_chains_through_usd_and_market():
    from core.conductor.planner import _deterministic_fx_chain_plan

    clauses = _deterministic_fx_chain_plan("ok 10000 eur to gbp and then to eth pls")
    assert len(clauses) == 4
    assert [c.operation for c in clauses] == [
        "fx_quote", "fx_quote", "market_quote", "quantitative_reasoning",
    ]
    assert clauses[1].depends_on == (0,) and clauses[3].depends_on == (1, 2)


def test_non_chains_return_empty():
    from core.conductor.planner import _deterministic_fx_chain_plan

    assert _deterministic_fx_chain_plan("price of bnb and gold?") == ()
    assert _deterministic_fx_chain_plan("what time is it in riga?") == ()


def test_fx_run_consumes_dependency_amount():
    """Leg 2 with no amount of its own converts leg 1's converted amount."""
    from core.conductor.fresh_data_operations import _fx_run

    class _Quote:
        available = True
        base, quote = "GBP", "ETH"

        @staticmethod
        def convert(amount):
            return amount * 2

        def to_dict(self):
            return {
                "base": "GBP", "quote": "ETH", "status": "available", "rate": 2,
                "converted_amount": "", "retrieved_at": "t", "source": "test",
                "direction": "per_quote",
            }

    captured = {}

    def _fake_resolve(base, quote, providers=None, timeout_s=None):
        captured["pair"] = (base, quote)
        return _Quote()

    import core.conductor.fresh_data_operations as fdo

    node = SimpleNamespace(arguments={"base": "GBP", "quote": "ETH"}, request_text="convert gbp to eth")
    ctx = SimpleNamespace(
        dependency_results={"leg1": {"converted_amount": "8576.20", "quote": "GBP"}},
        timeout_s=10,
        shared_context=None,
        source_context={
            "fx_provider": SimpleNamespace(resolve=_fake_resolve),
        },
    )
    original = fdo.resolve_fx_quote
    original_allowed = fdo._runtime_retrieval_allowed
    fdo.resolve_fx_quote = _fake_resolve
    fdo._runtime_retrieval_allowed = lambda _ctx: True
    try:
        payload = _fx_run(node, ctx)
    finally:
        fdo.resolve_fx_quote = original
        fdo._runtime_retrieval_allowed = original_allowed
    assert captured["pair"] == ("GBP", "ETH")
    assert payload["amount"] == "8576.20"
    assert payload["converted_amount"]


def test_chain_amount_fallback_divides_grounded_operands():
    from core.conductor.operations import _fx_chain_amount_fallback

    class _Ctx:
        def __init__(self, deps):
            self.dependency_results = deps

    ctx = _Ctx(
        {
            "leg1": {"converted_amount": "8576.20", "quote": "GBP"},
            "bridge": {"converted_amount": "11650.77", "quote": "USD"},
            "eth": {"price": 2456.08, "currency": "USD"},
        }
    )
    label, value, unit, expression = _fx_chain_amount_fallback(
        "ok 10000 eur to gbp and then to eth pls", ctx
    )
    assert value == pytest.approx(11650.77 / 2456.08, rel=1e-6)
    assert "ethereum" in label.lower()


def test_chain_amount_fallback_honest_cannot_when_price_missing():
    from core.conductor.operations import _fx_chain_amount_fallback

    class _Ctx:
        def __init__(self, deps):
            self.dependency_results = deps

    with pytest.raises(ValueError):
        _fx_chain_amount_fallback(
            "ok 10000 eur to gbp and then to eth pls",
            _Ctx({"leg1": {"converted_amount": "8576.20"}}),
        )


def test_fx_expand_rejects_crypto_tickers_as_currency_codes():
    """btc/bnb are three letters but market assets — the phantom 'BTC/BNB' fx
    frame (measured live: died on a 422 and polluted the answer) must not mint."""
    from core.conductor.fresh_data_operations import _fx_expand

    assert _fx_expand("convert 1 btc to bnb") == []
    assert _fx_expand("btc to bnb rate") == []
    assert _fx_expand("convert 100 eur to gbp") != []  # real fiat still parses


# -- C2 (chain shape): "first candidate stands" divides across currencies --------------------
#
# `_fx_chain_amount_fallback` selects the converted leg to divide with
# "match wins, else the first stands". When no candidate is quoted in the price's currency the
# first one stands anyway, and a wrong-currency amount is divided by a price it has no
# relationship to. Same law as the direct shape: refuse rather than cross silently.


def test_chain_amount_fallback_refuses_when_no_leg_matches_the_price_currency():
    from core.conductor.operations import _fx_chain_amount_fallback

    class _Ctx:
        def __init__(self, deps):
            self.dependency_results = deps

    ctx = _Ctx(
        {
            "leg1": {"converted_amount": "8576.20", "quote": "GBP"},
            "eth": {"price": 2456.08, "currency": "USD"},
        }
    )
    with pytest.raises(ValueError):
        _fx_chain_amount_fallback("ok 10000 eur to gbp and then to eth pls", ctx)


def test_chain_amount_fallback_picks_the_matching_leg_even_when_it_is_not_last():
    """The matching candidate is first here on purpose.

    The committed chain test has its only USD bridge last, so both "first stands" and
    "last stands" produce the right answer there and a mutation of the selection rule
    survives it. With the match first and a non-match after it, only a rule that actually
    matches on currency passes.
    """
    from core.conductor.operations import _fx_chain_amount_fallback

    class _Ctx:
        def __init__(self, deps):
            self.dependency_results = deps

    ctx = _Ctx(
        {
            "bridge": {"converted_amount": "11650.77", "quote": "USD"},
            "leg1": {"converted_amount": "8576.20", "quote": "GBP"},
            "eth": {"price": 2456.08, "currency": "USD"},
        }
    )
    _label, value, _unit, _expression = _fx_chain_amount_fallback(
        "ok 10000 eur to gbp and then to eth pls", ctx
    )
    assert value == pytest.approx(11650.77 / 2456.08, rel=1e-6)
