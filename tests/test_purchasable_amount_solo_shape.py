"""Solo purchasable-amount derivation — the turn the operator measured as broken.

"how much of gold can i buy with 10 bnb?" served two bare quotes and no figure:
the conductor declined the single-request shape at the several-requests boundary,
and the typed live-data lane cannot express a computation. These tests pin the
deterministic fix: the shape mints a two-quote + calculation plan the conductor
MUST claim, and the calculation's fallback derives the figure model-free from the
two grounded prices (Test Pack V2 L02: later obligations consume earlier results).
"""
from __future__ import annotations

import pytest


def test_operand_parser_extracts_quantity_and_payment_asset():
    from core.conductor.operations import purchasable_amount_operands

    assert purchasable_amount_operands("how much gold can I buy with 10 bnb?") == (10.0, "bnb")
    assert purchasable_amount_operands("how much gold for 2.5 btc please") == (2.5, "btc")
    assert purchasable_amount_operands("what is the price of gold") is None
    assert purchasable_amount_operands("buy gold with zero bnb") is None


def test_solo_shape_mints_a_conductor_plan():
    from core.conductor.planner import plan_conductor_turn

    plan = plan_conductor_turn(
        "how much of gold i can buy with 10 bnb?", ask_model=lambda *_: (_ for _ in ()).throw(AssertionError("no model on this path"))
    )
    assert plan is not None, "the solo derivation shape must not fall through to a quotes-only lane"
    ops = plan.operations
    assert "quantitative_reasoning" in ops and "market_quote" in ops
    assert plan.requires_conductor(), "a plan carrying quantitative_reasoning is not lane-served vocabulary"
    calc_nodes = [n for n in plan.nodes if n.operation == "quantitative_reasoning"]
    assert calc_nodes and calc_nodes[0].depends_on, "the computation must declare its price dependencies"


def test_non_shapes_still_decline_deterministically():
    from core.conductor.planner import _deterministic_purchasable_amount_plan

    assert _deterministic_purchasable_amount_plan("price of bnb and gold?") == ()
    assert _deterministic_purchasable_amount_plan("what is the weather in oslo?") == ()
    assert _deterministic_purchasable_amount_plan("how much gold can I buy?") == ()  # no payment leg


class _Ctx:
    """Minimal NodeContext stand-in: dependency_results keyed by node id."""

    def __init__(self, dependency_results):
        self.dependency_results = dependency_results


def test_fallback_derives_from_two_grounded_prices():
    from core.conductor.operations import _purchasable_amount_fallback

    ctx = _Ctx(
        {
            "n-gold": {"price": 4529.90, "currency": "USD", "entity": "Gold"},
            "n-bnb": {"price": 693.79, "currency": "USD", "entity": "Binancecoin"},
        }
    )
    out = _purchasable_amount_fallback("how much of gold i can buy with 10 bnb?", ctx)
    assert out is not None
    label, value, unit, expression = out
    assert value == pytest.approx((10 * 693.79) / 4529.90, rel=1e-6)
    assert unit == "troy ounces" and "gold" in label.lower()
    assert "6" in expression or "6,937" in expression  # the numerator reads as the user's own numbers


def test_fallback_keeps_the_honest_cannot_when_a_leg_is_missing():
    from core.conductor.operations import _purchasable_amount_fallback

    ctx = _Ctx({"n-gold": {"price": 4529.90, "currency": "USD", "entity": "Gold"}})
    with pytest.raises(ValueError):
        _purchasable_amount_fallback("how much of gold i can buy with 10 bnb?", ctx)


def test_fallback_refuses_cross_currency_payment_leg():
    from core.conductor.operations import _purchasable_amount_fallback

    ctx = _Ctx(
        {
            "n-gold": {"price": 4529.90, "currency": "USD", "entity": "Gold"},
            "n-x": {"price": 0.85, "currency": "GBP", "entity": "Binancecoin"},
        }
    )
    with pytest.raises(ValueError):
        _purchasable_amount_fallback("how much of gold i can buy with 10 bnb?", ctx)


# -- mixed multi-request messages (operator-measured, 2026-08-30 evening) -------------------


def test_have_sell_phrasing_parses_as_payment_leg():
    from core.conductor.operations import purchasable_amount_operands

    assert purchasable_amount_operands(
        "if i have 1 btc how much bnb i can buy if i sell it?"
    ) == (1.0, "btc")
    assert purchasable_amount_operands("selling 0.5 eth, how much sol gets me?") == (0.5, "eth")


def test_mixed_message_plans_every_ask_plus_the_figure():
    """'price of btc? also <derivation>' must serve BOTH asks, model-free."""
    from core.conductor.planner import plan_conductor_turn

    plan = plan_conductor_turn(
        "price of btc? also if i have 1 btc how much bnb i can buy if i sell it?",
        ask_model=lambda *_: (_ for _ in ()).throw(AssertionError("no model on this path")),
    )
    assert plan is not None, "the mixed quote+derivation message must not fall to a quotes-only lane"
    ops = plan.operations
    assert "quantitative_reasoning" in ops and "market_quote" in ops
    market_nodes = [n for n in plan.nodes if n.operation == "market_quote"]
    quoted = {str(n.arguments.get("asset_key") or "") for n in market_nodes}
    assert {"binancecoin", "bitcoin"} <= quoted, "both the explicit ask and the derivation legs quote"
    calc = next(n for n in plan.nodes if n.operation == "quantitative_reasoning")
    assert len(calc.depends_on) >= 2


def test_mixed_message_fallback_computes_bnb_amount():
    from core.conductor.operations import _purchasable_amount_fallback

    class _Ctx:
        def __init__(self, deps):
            self.dependency_results = deps

    # Dep order mirrors clause order: target (bnb) FIRST, then payment (btc).
    ctx = _Ctx(
        {
            "n-bnb": {"price": 694.99, "currency": "USD", "entity": "Binancecoin"},
            "n-btc": {"price": 78591.00, "currency": "USD", "entity": "Bitcoin"},
        }
    )
    out = _purchasable_amount_fallback(
        "if i have 1 btc how much bnb i can buy if i sell it?", ctx
    )
    assert out is not None
    label, value, unit, _ = out
    assert value == pytest.approx(78591.00 / 694.99, rel=1e-6)
    assert "binancecoin" in label.lower()


def test_crypto_target_label_names_the_asset():
    from core.conductor.operations import _purchasable_amount_fallback

    class _Ctx:
        def __init__(self, deps):
            self.dependency_results = deps

    # Dep ids mirror the scheduler's canonical node ids (asset key rides the id).
    ctx = _Ctx(
        {
            "plan:market_quote:binancecoin": {"price": 695.02, "currency": "USD"},
            "plan:market_quote:bitcoin": {"price": 78589.0, "currency": "USD"},
        }
    )
    label, value, _, _ = _purchasable_amount_fallback(
        "if i have 1 btc how much bnb i can buy if i sell it?", ctx
    )
    assert "binancecoin" in label.lower()


# -- the INVERSE shape (operator-measured, 2026-08-30 night 3) ------------------------------


def test_inverse_target_operands_parse():
    from core.conductor.operations import purchasable_target_operands

    assert purchasable_target_operands(
        "i want to buy 1 btc, but i must sell gold first. how much gold i need to sell to buy 1 btc?"
    ) == (1.0, "btc")


def test_inverse_shape_plans_quotes_plus_figure():
    from core.conductor.planner import plan_conductor_turn

    plan = plan_conductor_turn(
        "i want to buy 1 btc, but i must sell gold first. how much gold i need to sell to buy 1 btc?",
        ask_model=lambda *_: (_ for _ in ()).throw(AssertionError("no model on this path")),
    )
    assert plan is not None, "the inverse derivation must not fall to a quotes-only lane"
    market = [n for n in plan.nodes if n.operation == "market_quote"]
    quoted = {str(n.arguments.get("asset_key") or "") for n in market}
    assert {"bitcoin", "gold"} <= quoted
    calc = next(n for n in plan.nodes if n.operation == "quantitative_reasoning")
    assert len(calc.depends_on) >= 2


def test_inverse_fallback_computes_gold_to_sell():
    from core.conductor.operations import _purchasable_amount_fallback

    class _Ctx:
        def __init__(self, deps):
            self.dependency_results = deps

    ctx = _Ctx(
        {
            "plan:market_quote:bitcoin": {"price": 78625.0, "currency": "USD"},
            "plan:market_quote:gold": {"price": 4529.90, "currency": "USD"},
        }
    )
    label, value, unit, expression = _purchasable_amount_fallback(
        "how much gold i need to sell to buy 1 btc?", ctx
    )
    assert value == pytest.approx(78625.0 / 4529.90, rel=1e-6)
    assert "gold" in label.lower() and unit == "troy ounces"


def test_direct_shape_keeps_precedence_over_inverse():
    """'with 10 bnb' phrasing must keep its existing arithmetic even though a
    'buy' word also appears."""
    from core.conductor.operations import _purchasable_amount_fallback

    class _Ctx:
        def __init__(self, deps):
            self.dependency_results = deps

    ctx = _Ctx(
        {
            "plan:market_quote:gold": {"price": 4529.90, "currency": "USD"},
            "plan:market_quote:binancecoin": {"price": 694.99, "currency": "USD"},
        }
    )
    label, value, _, _ = _purchasable_amount_fallback(
        "how much of gold i can buy with 10 bnb?", ctx
    )
    assert value == pytest.approx((10 * 694.99) / 4529.90, rel=1e-6)


def test_glued_amount_and_missing_alias_leg_still_plan():
    """Watch-round turn 1: '1eth' glued, cloud planner dropped the eth quote.
    The deterministic minter must be self-sufficient here."""
    from core.conductor.planner import plan_conductor_turn

    plan = plan_conductor_turn(
        "I have 1eth, i want to sell it and buy silver. How much silver will i get?",
        ask_model=lambda *_: (_ for _ in ()).throw(AssertionError("no model on this path")),
    )
    assert plan is not None
    quoted = {
        str(n.arguments.get("asset_key"))
        for n in plan.nodes
        if n.operation == "market_quote"
    }
    assert {"silver", "ethereum"} <= quoted, "the payment leg must be quoted even when glued"


# -- C2: the operand must be stated in the price's own currency ------------------------------
#
# The measured breach (FINAL_SIGNOFF C2, "gold amount uses current gold price, states
# unit/assumption"): the repro turn "…I have 100 US convert to RUB and tell me how much gold
# I can buy" schedules the FX leg before the market leg. The FX result carries a converted
# amount of 8,000 RUB; the gold price is 2,400 USD. Dict order decides which binds first, so
# `price_currency` is still unbound when the RUB amount is accepted, the mismatch guard never
# fires, and the turn serves 8000 / 2400 = 3.3333 troy ounces of gold for 100 USD.
#
# The law these pin: an amount is a valid operand for this division only when it is stated in
# the currency the price is quoted in. The FX leg carries both — `converted_amount` in `quote`
# and the untouched `amount` in `base` — so the right operand is present and grounded; nothing
# here needs a model, a rate guess, or a second conversion.


def test_fallback_refuses_a_converted_amount_the_price_is_not_quoted_in():
    """8,000 RUB is not an operand for a price quoted in USD — refuse, never divide."""
    from core.conductor.operations import _purchasable_amount_fallback

    ctx = _Ctx(
        {
            "n-fx": {"converted_amount": 8000.0, "quote": "RUB"},
            "plan:market_quote:gold": {"price": 2400.0, "currency": "USD"},
        }
    )
    with pytest.raises(ValueError):
        _purchasable_amount_fallback("tell me how much gold I can buy", ctx)


def test_fallback_refuses_it_regardless_of_dependency_order():
    """The market leg first must refuse identically — the guard is not order luck."""
    from core.conductor.operations import _purchasable_amount_fallback

    ctx = _Ctx(
        {
            "plan:market_quote:gold": {"price": 2400.0, "currency": "USD"},
            "n-fx": {"converted_amount": 8000.0, "quote": "RUB"},
        }
    )
    with pytest.raises(ValueError):
        _purchasable_amount_fallback("tell me how much gold I can buy", ctx)


def test_fallback_takes_the_fx_legs_base_amount_when_the_conversion_left_the_price_currency():
    """The repro's true figure: the FX leg's own 100 USD over the 2,400 USD price.

    The conversion to RUB answers a different clause. The amount the buyer actually holds in
    the price's currency is the FX leg's `amount` in its `base` — a grounded dependency value,
    not a re-conversion and not a model number.
    """
    from core.conductor.operations import _purchasable_amount_fallback

    ctx = _Ctx(
        {
            "n-fx": {
                "base": "USD",
                "quote": "RUB",
                "amount": "100",
                "converted_amount": "8000.0",
                "rate": "80.0",
            },
            "plan:market_quote:gold": {"price": 2400.0, "currency": "USD", "entity": "Gold"},
        }
    )
    label, value, unit, expression = _purchasable_amount_fallback(
        "tell me how much gold I can buy", ctx
    )
    assert value == pytest.approx(100 / 2400, rel=1e-6)
    assert unit == "troy ounces" and "gold" in label.lower()
    assert "8,000" not in expression and "8000" not in expression, (
        "the RUB conversion must not appear in the working"
    )


def test_fallback_still_uses_a_converted_amount_that_does_match_the_price_currency():
    """The ordinary chained shape is untouched: a USD conversion divides a USD price."""
    from core.conductor.operations import _purchasable_amount_fallback

    ctx = _Ctx(
        {
            "n-fx": {"base": "EUR", "quote": "USD", "amount": "90", "converted_amount": "100.0"},
            "plan:market_quote:gold": {"price": 2400.0, "currency": "USD", "entity": "Gold"},
        }
    )
    _label, value, _unit, _expression = _purchasable_amount_fallback(
        "tell me how much gold I can buy", ctx
    )
    assert value == pytest.approx(100 / 2400, rel=1e-6)


# -- C2: "with it" is the amount the same request just named -----------------------------------
#
# Measured on the canonical four-slot turn at BOTH entrances, with retrieval allowed: the gold
# slot came back as a PRICE QUOTE -- "Gold: USD 4,476.60 per troy ounce" -- for a question that
# asked an AMOUNT, and the certificate reported the slot satisfied. FINAL_SIGNOFF's own C2 note
# says the same thing about the solo control: "still returns a price quote, never a purchasable
# amount".
#
# The cause is not the arithmetic, which this file already pins. It is that the payment leg is
# ANAPHORIC. "how much gold can I buy with it" names no number, so the operand parser returns
# None, no derivation is planned, and the lane falls back to quoting a price. The number is
# right there in the same sentence the operator wrote.


def test_an_anaphoric_payment_binds_to_the_amount_named_earlier():
    """"with it" is the amount this request just named -- the same "later obligations consume
    earlier results" law the chained conversion already runs on."""
    from core.conductor.operations import purchasable_amount_operands

    assert purchasable_amount_operands(
        "What is 1000 EUR to RUB, how much gold can I buy with it, "
        "what is the weather in Rome?"
    ) == (1000.0, "eur")
    assert purchasable_amount_operands("I have 1500 usd. how much gold can I buy with that?") == (
        1500.0,
        "usd",
    )


def test_the_anaphora_never_runs_when_an_explicit_leg_exists():
    """The anaphora is a FALLBACK, never an override.

    Stated as the law the parser actually implements: the anaphora path runs only when no
    explicit "with N <asset>" is found anywhere. Which of several explicit legs wins when a
    request states more than one is a separate disambiguation this parser has never claimed --
    "have" introduces a payment leg too, which the have/sell pins in this file depend on -- and
    it is deliberately not asserted here.
    """
    from core.conductor.operations import purchasable_amount_operands

    # An explicit leg, with an anaphora ALSO present: the explicit one is what comes back.
    assert purchasable_amount_operands("how much gold can I buy with 10 bnb, using it all?") == (
        10.0,
        "bnb",
    )
    # And the anaphora resolves to an amount only when there is no explicit leg to find.
    assert purchasable_amount_operands("1000 eur — how much gold can I buy with it?") == (
        1000.0,
        "eur",
    )


def test_an_anaphora_with_nothing_to_bind_to_stays_unresolved():
    """No earlier amount, no operand -- the honest cannot, never an invented quantity."""
    from core.conductor.operations import purchasable_amount_operands

    assert purchasable_amount_operands("how much gold can I buy with it?") is None
    assert purchasable_amount_operands("tell me how much gold I can buy") is None



# RECORDED, NOT PINNED — the fiat payment leg.
#
# "how much gold can I buy with 1000 EUR" is the most natural phrasing of this question and is
# NOT planned: `_deterministic_purchasable_amount_plan` validates the payment leg through the
# PRICE-ASSET alias table, a currency resolves to nothing, and the shape declines. The lane then
# answers an AMOUNT question with a gold PRICE, and the certificate calls the slot satisfied --
# measured at both entrances on the canonical four-slot prompt.
#
# A fix was written and REVERTED (see the revert commit for the measurement). Admitting a
# currency payment made the deterministic planner claim MULTI-CLAUSE turns whose other clauses
# it does not represent: the repro turn collapsed to a bare gold quote with Rome and the FX
# conversion both disclosed as unanswerable, and 5 tests in
# test_multi_intent_served_coverage.py went red. The deterministic planner returns a COMPLETE
# clause list for the whole turn, so widening what it claims narrows what the turn can answer.
#
# The gap is therefore real and its fix is not a gate widening: the purchasable shape has to be
# expressible as ONE clause inside a mixed plan rather than as a whole-turn claim. Left here as
# a statement of the defect, deliberately not as a red test.
# THE FIAT PAYMENT LEG — landed on attempt 3, and what it does NOT buy.
#
# "how much gold can I buy with 1000 EUR" is the most natural phrasing of this question and was
# never planned at all: the payment leg was validated through the PRICE-ASSET alias table only,
# so a fiat currency resolved to nothing, the shape declined, and the lane answered an AMOUNT
# question with a gold PRICE.
#
# ATTEMPT 1 (9c5afb44, reverted in 36b429f3) broke 5 tests: this arm returns a COMPLETE clause
# list for the whole turn, so widening its claim swallowed the sibling clauses.
# ATTEMPT 2 (recorded in bf7faa24, dropped) re-landed it on the per-unit coverage guard
# (fad9c621) and got 5 breakages down to 1. The survivor was clean-reversed-order:
# "How much gold can I buy with 100 USD in RUB? Also weather in Rome" is ONE execution unit,
# so a unit-based guard had nothing to refuse.
# ATTEMPT 3 lands it on the MINT-grain sub-check (e62e909c), which reads that sibling. Zero
# breakages: 14 failed / 614 passed across 16 grain and obligation suites, the identical
# failure set to BASE.
#
# WHAT IT DOES NOT BUY, pinned below rather than left as a hope: the canonical FOUR-SLOT prompt
# still gets no gold amount from this arm. Its FX leg, its weather ask and its Baltic ask belong
# to no purchase node here, so the guard declines the turn -- which is the guard working, not a
# gap in the fiat leg. That turn is the model planner's, and whether the amount is served there
# is a separate measurement against a separate lane.


def test_a_fiat_payment_leg_is_planned_like_any_other():
    """C2. The money IS the amount: a fiat payment needs no price lookup, and the division is
    amount / price once the currencies match, which this file already pins."""
    from core.conductor.planner import _deterministic_purchasable_amount_plan

    clauses = _deterministic_purchasable_amount_plan("how much gold can I buy with 1000 EUR?")
    ops = [getattr(c, "operation", "") for c in clauses]
    assert clauses, "the fiat payment shape was not planned"
    assert "market_quote" in ops and "quantitative_reasoning" in ops, ops
    # Only the TARGET is quoted: a currency has no market price to look up, and quoting one as a
    # market asset would be the second defect this change could introduce.
    quoted = [
        str(getattr(c, "request", ""))
        for c in clauses
        if getattr(c, "operation", "") == "market_quote"
    ]
    assert len(quoted) == 1, quoted
    assert "gold" in quoted[0].lower(), quoted


def test_the_canonical_four_slot_prompt_is_left_to_the_model_planner():
    """The honest boundary, pinned so nobody reads the fiat leg as more than it is.

    ATTEMPT 1 asserted this arm PLANS the four-slot prompt. It should not: the arm has no FX
    node, no weather node and no Baltic node, so claiming the turn is the swallow the coverage
    guard exists to stop. Declining hands it to the model planner, which can mint a MIXED plan.
    """
    from core.conductor.planner import _deterministic_purchasable_amount_plan

    assert _deterministic_purchasable_amount_plan(
        "What is 1000 EUR to RUB, how much gold can I buy with it, "
        "what is the weather in Rome, and what is the water temperature in the Baltic Sea?"
    ) == ()


def test_an_unknown_payment_token_still_declines():
    """The gate still refuses what it cannot ground: neither a priced asset nor a currency."""
    from core.conductor.planner import _deterministic_purchasable_amount_plan

    assert _deterministic_purchasable_amount_plan("how much gold can I buy with 10 zzz?") == ()


# ---------------------------------------------------------------------------------------
# The cross-currency bridge: the turn holds the money, just not in the price's currency.
# ---------------------------------------------------------------------------------------


class _BridgeCtx:
    """A NodeContext stand-in that CAN authorize retrieval, with a scripted FX provider.

    No network: the provider is handed in through ``source_context["fx_providers"]``, the same
    injection point ``_configured_fx_providers`` reads in production.
    """

    def __init__(self, dependency_results, provider, *, allow: bool = True):
        self.dependency_results = dependency_results
        self.timeout_s = 8.0
        self.source_context = {"allow_remote_fetch": allow, "fx_providers": [provider]}


class _ScriptedFx:
    """One rate, and a count of how many times it was asked for."""

    def __init__(self, rate, *, available=True):
        self._rate = rate
        self.available = available
        self.calls = []

    def quote(self, base, quote, *, timeout_s=8.0):
        from core.fresh_data.fx import unavailable_fx_quote

        self.calls.append((base, quote))
        if not self.available:
            return unavailable_fx_quote(base, quote, "scripted provider is down")
        from decimal import Decimal

        from core.fresh_data.fx import FxQuote, FxQuoteStatus

        # `available` is a property over ALL of these -- a quote missing its provenance is not
        # usable, which is the same law the production providers obey.
        return FxQuote(
            base=base,
            quote=quote,
            rate=Decimal(str(self._rate)),
            status=FxQuoteStatus.AVAILABLE,
            observed_at="2026-09-05T00:00:00+00:00",
            retrieved_at="2026-09-05T00:00:00+00:00",
            source="scripted",
        )


def _gold_deps():
    """The canonical four-slot turn's shape: EUR->RUB done, gold priced in USD."""
    return {
        "n-fx": {"base": "EUR", "quote": "RUB", "amount": 1000.0, "converted_amount": 100700.0},
        "n-gold": {"price": 4476.60, "currency": "USD", "entity": "Gold"},
    }


def test_the_money_is_bridged_into_the_price_currency_and_the_rate_is_stated(monkeypatch):
    """RC: "1000 EUR to RUB ... how much gold can I buy with it" held EUR and RUB while the
    gold price was USD, so the clause refused and the reader got a price quote instead of an
    amount. One live conversion makes the division legal -- and the rate must be IN the
    expression, because an unstated assumption is the thing this codebase refuses."""
    from core import policy_engine
    from core.conductor.operations import _purchasable_amount_fallback

    monkeypatch.setattr(policy_engine, "allow_web_fallback", lambda: True)
    provider = _ScriptedFx(1.10)
    ctx = _BridgeCtx(_gold_deps(), provider)

    label, value, unit, expression = _purchasable_amount_fallback(
        "how much gold can I buy with it", ctx
    )

    assert unit == "troy ounces"
    assert value == pytest.approx((1000.0 * 1.10) / 4476.60)
    assert ("EUR", "USD") in provider.calls
    # The stated assumption: the money, the rate, and the currency it was taken into.
    assert "EUR" in expression and "USD/EUR" in expression
    assert "4,476.6" in expression, expression


def test_an_unavailable_bridge_keeps_the_honest_cannot(monkeypatch):
    """Fail closed. A bridge that cannot be made must not become a guess, and must not become
    an error of its own either -- the pre-existing refusal is the correct answer."""
    from core import policy_engine
    from core.conductor.operations import _purchasable_amount_fallback

    monkeypatch.setattr(policy_engine, "allow_web_fallback", lambda: True)
    ctx = _BridgeCtx(_gold_deps(), _ScriptedFx(1.10, available=False))

    with pytest.raises(ValueError) as caught:
        _purchasable_amount_fallback("how much gold can I buy with it", ctx)
    assert "USD" in str(caught.value)


def test_an_unauthorized_turn_never_reaches_a_provider(monkeypatch):
    """The bridge is retrieval. A turn that forbids remote fetch must not perform one, and the
    refusal must be the same refusal it always was."""
    from core import policy_engine
    from core.conductor.operations import _purchasable_amount_fallback

    monkeypatch.setattr(policy_engine, "allow_web_fallback", lambda: True)
    provider = _ScriptedFx(1.10)
    ctx = _BridgeCtx(_gold_deps(), provider, allow=False)

    with pytest.raises(ValueError):
        _purchasable_amount_fallback("how much gold can I buy with it", ctx)
    assert provider.calls == [], "an unauthorized turn reached the FX provider anyway"


def test_a_same_currency_turn_is_byte_identical_and_never_bridges(monkeypatch):
    """No bridge, no change: when the turn already holds the money in the price's currency the
    expression must stay exactly what it was, and no provider call may happen."""
    from core import policy_engine
    from core.conductor.operations import _purchasable_amount_fallback

    monkeypatch.setattr(policy_engine, "allow_web_fallback", lambda: True)
    provider = _ScriptedFx(1.10)
    deps = {
        "n-fx": {"base": "EUR", "quote": "USD", "amount": 1000.0, "converted_amount": 1100.0},
        "n-gold": {"price": 4476.60, "currency": "USD", "entity": "Gold"},
    }
    _label, value, _unit, expression = _purchasable_amount_fallback(
        "how much gold can I buy with it", _BridgeCtx(deps, provider)
    )
    assert value == pytest.approx(1100.0 / 4476.60)
    assert provider.calls == []
    assert expression.startswith("1,100")
# -- The deterministic minter must not claim a turn it does not cover -------------------------
#
# Measured at HEAD: "how much gold can I buy with 10 bnb and what is the weather in Rome" mints
# two market quotes and ONE quantitative_reasoning clause whose request is the WHOLE MESSAGE --
# Rome included. There is no weather node anywhere in the plan. The Rome ask is absorbed into
# the calculation clause's text and can never be served, while the span makes the plan look
# like it covers everything.
#
# That is the same defect the reverted fiat-payment change ran into from the other side: this
# arm returns a COMPLETE clause list for the whole turn, so whatever it claims, it owes. The two
# laws below are what make widening it safe -- the clause carries only its own span, and a unit
# the plan does not cover means the turn is not claimed at all.


def test_the_calculation_clause_carries_only_its_own_span():
    from core.conductor.planner import _deterministic_purchasable_amount_plan

    clauses = _deterministic_purchasable_amount_plan(
        "how much gold can I buy with 10 bnb and what is the weather in Rome"
    )
    calc = [c for c in clauses if getattr(c, "operation", "") == "quantitative_reasoning"]
    for clause in calc:
        assert "weather" not in str(clause.request).lower(), clause.request
        assert "rome" not in str(clause.request).lower(), clause.request


def test_a_turn_with_an_uncovered_unit_is_not_claimed():
    """The plan has no weather node, so it must not claim a turn that asks for weather."""
    from core.conductor.planner import _deterministic_purchasable_amount_plan

    assert _deterministic_purchasable_amount_plan(
        "how much gold can I buy with 10 bnb and what is the weather in Rome"
    ) == ()


def test_a_turn_whose_every_unit_serves_the_purchase_is_still_claimed():
    """CONTROL, and the one this must not break: the glued shape splits into two execution
    units that BOTH belong to the purchase, so the plan covers the turn and keeps it."""
    from core.conductor.planner import _deterministic_purchasable_amount_plan

    assert _deterministic_purchasable_amount_plan(
        "I have 1eth, i want to sell it and buy silver. How much silver will i get?"
    ), "the glued purchase shape must still be claimed"
    assert _deterministic_purchasable_amount_plan("how much of gold i can buy with 10 bnb?")


# --- The MINT-grain sub-check (Option D) ------------------------------------------------
# The execution grain FUSES a headless sibling ask: "...in RUB? Also weather in Rome" is ONE
# execution unit, because after the leader 'also' the next token is a SUBJECT, not a demand
# head -- so `_purchase_span_text` reading only that grain has nothing to refuse. Widening the
# execution split was measured to shred conjoined asks ("btc and eth price" -> "btc" + "and eth
# price"), stripping lane_may_claim_whole_turn from the lane that serves those turns today.
# So the disagreement between the two grains is READ at this claimant, not removed at the grain.


def test_a_minted_unit_a_registered_lane_claims_is_not_scoped_into_the_purchase():
    """'Also weather in Rome' mints as its own unit and a registered live-data lane claims it.
    This arm has no weather node, so claiming the turn would swallow the ask."""
    from core.conductor.planner import _purchase_span_text

    assert _purchase_span_text(
        "How much gold can I buy with 100 USD in RUB? Also weather in Rome"
    ) == ""


def test_a_continuation_no_lane_claims_still_rides_the_purchase():
    """CONTROL: 'I have 1eth' / 'i want to sell it' mint as units too, but NO registered lane
    claims them -- they are continuations of the purchase, not sibling demands."""
    from core.conductor.planner import _purchase_span_text

    assert _purchase_span_text(
        "I have 1eth, i want to sell it and buy silver. How much silver will i get?"
    ) != ""


def test_a_second_asset_ask_inside_one_purchase_turn_is_still_claimed():
    """CONTROL: a second asset the purchase names is served by the same plan."""
    from core.conductor.planner import _purchase_span_text

    assert _purchase_span_text("how much silver can I get for 2 eth, price of gold too") != ""
