"""Three currencies, one number, and a correction the runtime has to act on.

THE MEASURED DEFECT (QA-050-027). On the untouched base `03b04b39` this message:

    "Compare 500 kr, $500, and ¥500 in terms of value and purchasing power. Which is worth most
     today, and which would go furthest locally? If inflation is 5%, which is best to hold?"

was answered with `kr = NOK` and `¥ = JPY`, and the correction one turn later —

    "Actually the kr is in Copenhagen and the ¥ is in Shanghai."

— was answered with "Copenhagen (NOK)" and "Shanghai (JPY)". Both readings survived being told the
facts that settle them. Reproduced exactly, before the fix, by the three seam readings recorded in
`test_the_reported_turn_reached_no_currency_lane_at_all` and
`test_no_city_could_pin_anything_before_the_fix`:

    currency_transaction_intent("… purchasing power …")  -> True   (the gate that shut the lane)
    currency_semantics_present("Compare 500 kr, …")      -> False  (no grounding requirement)
    currency_fast_path("Compare 500 kr, …")              -> None   (model answered it freehand)
    any city -> currency mapping                          -> did not exist

WHAT IS INJECTED AND WHAT IS NOT. No model is called anywhere in this file: every lane under test
is deterministic. No rate and no PPP factor is injected into the RUNTIME — they are passed as
arguments the way a live feed would pass them, and `test_no_answer_states_a_number_it_was_not_given`
scans every rendered answer for a figure that traces to neither the prompt nor those arguments. The
two structural scans walk the new modules' ASTs so a stored rate cannot be smuggled back in.

THE GENERAL RULE. Copenhagen/DKK is not a currency special case. `core/entity_consistency.py` holds
the same check for cities and countries, car models and brands, products and makers, and the
`Toyota Passat` / `Berlin in France` / `Apple Galaxy` families below run through the identical code
path as `Copenhagen NOK`. `test_a_freshly_registered_authority_is_enforced_immediately` proves the
interface is real by adding a domain that did not exist when the checker was written.
"""
from __future__ import annotations

import ast
import pathlib
import re
import threading
from decimal import Decimal

import pytest

from core.agent_runtime.fast_paths_currency import (
    attach_currency_grounding,
    currency_fast_path,
    currency_grounding_observation,
    prior_comparison,
)
from core.conductor.shared_context import extract_shared_context
from core.currency_comparison import (
    ASK_HOLD,
    ASK_MARKET_VALUE,
    ASK_PURCHASING_POWER,
    comparison_followup,
    covers_whole_turn,
    currency_comparison_intent,
    invalidations,
    parse_supplied_rates,
    render_currency_comparison,
    scan_amounts,
    uncovered_residue,
)
from core.currency_intent import (
    CITY_TO_CODE,
    ISO_4217,
    currency_semantics_present,
    currency_transaction_intent,
    fx_rate_lookup_intent,
    location_bindings,
    render_fx_rate_lookup,
)
from core.entity_consistency import (
    CITY_TO_COUNTRY,
    EntityAuthority,
    entity_consistency_report,
    register_authority,
    render_entity_contradictions,
    uncovered_by_contradictions,
    unregister_authority,
)

# --------------------------------------------------------------------------------------
# The message and its families
# --------------------------------------------------------------------------------------

ORIGINAL = (
    "Compare 500 kr, $500, and ¥500 in terms of value and purchasing power. "
    "Which is worth most today, and which would go furthest locally? "
    "If inflation is 5%, which is best to hold?"
)

#: Ten families: the original, four clean paraphrases, five sloppy or user-style forms. Each must
#: reach the same three ambiguous units and the same three asks, so a fix keyed to the original's
#: phrasing fails nine of them.
VARIANTS: tuple[tuple[str, str], ...] = (
    ("original", ORIGINAL),
    (
        "paraphrase_ranked",
        "I have 500 kr, $500 and ¥500. Rank them by market value and by purchasing power — which "
        "is worth most today and which goes furthest locally? With 5% inflation, which is best to "
        "hold?",
    ),
    (
        "paraphrase_which_of",
        "Which of 500 kr, $500 or ¥500 is worth the most, and which one buys more at home? "
        "Assuming inflation of 5%, which would be best to hold onto?",
    ),
    (
        "paraphrase_side_by_side",
        "Put 500 kr, $500 and ¥500 side by side on exchange value and on purchasing power. "
        "Inflation is 5% — which is best to keep?",
    ),
    (
        "paraphrase_stack_up",
        "How do 500 kr, $500 and ¥500 stack up in value and in what they buy locally? Which is "
        "worth most today? At 5% inflation which is best to hold?",
    ),
    (
        "sloppy_lowercase",
        "compare 500 kr, $500 and ¥500 in terms of value and purchasing power. whats worth most "
        "today and which goes furthest locally. inflation 5%, best to hold?",
    ),
    (
        "sloppy_no_punctuation",
        "compare 500 kr $500 and ¥500 which is worth more and which has better purchasing power "
        "if inflation is 5% which is best to hold",
    ),
    (
        "sloppy_run_on",
        "ok so ive got 500 kr and $500 and ¥500 which is worth most today which would go furthest "
        "locally and with 5% inflation which is best to hold i cant work it out",
    ),
    (
        "sloppy_typos",
        "compair 500 kr, $500 and ¥500 on value and purchaseing power, which is worth most today, "
        "which goes further locally, 5% inflation which is best to hold",
    ),
    (
        "sloppy_shouted",
        "COMPARE 500 KR, $500 AND ¥500 — WHICH IS WORTH MOST TODAY AND WHICH GOES FURTHEST "
        "LOCALLY? 5% INFLATION, WHICH IS BEST TO HOLD?",
    ),
)

LOCATION_FOLLOWUPS: tuple[tuple[str, str, str, str], ...] = (
    ("copenhagen_shanghai", "Actually the kr is in Copenhagen and the ¥ is in Shanghai.", "DKK", "CNY"),
    ("copenhagen_tokyo", "The kr is in Copenhagen and the ¥ is in Tokyo.", "DKK", "JPY"),
    ("oslo_tokyo", "The kr is in Oslo, the ¥ is in Tokyo.", "NOK", "JPY"),
    ("stockholm_shanghai", "kr in Stockholm, ¥ in Shanghai", "SEK", "CNY"),
    ("nationality_form", "the kr is Danish and the ¥ is Chinese", "DKK", "CNY"),
    ("reykjavik_osaka", "the kr is in Reykjavik and the ¥ is in Osaka", "ISK", "JPY"),
)

HISTORY = [{"role": "user", "content": ORIGINAL}]


def _numbers(text: str) -> set[str]:
    return {match.group(0) for match in re.finditer(r"\d[\d,]*(?:\.\d+)?", str(text or ""))}


# ========================================================================================
# 1. The reported defect, at the seams it actually failed at
# ========================================================================================


def test_the_reported_turn_reached_no_currency_lane_at_all() -> None:
    """Two seams, one turn. Both must now claim it; on the base neither did.

    `currency_transaction_intent` returned True because "purchasing power" starts with a transfer
    verb, which slammed the currency door shut before any reading was attempted; and
    `currency_semantics_present` returned False, so nothing else attached a grounding requirement
    either. The turn reached a model with no constraint and came back with NOK and JPY.
    """

    assert not currency_transaction_intent(ORIGINAL), (
        "'purchasing power' is a compound noun, not an instruction to buy"
    )
    assert currency_semantics_present(ORIGINAL), "the comparison turn is not admitted as a currency turn"

    claimed = currency_fast_path(ORIGINAL)
    assert claimed is not None, "the reported turn is still unclaimed"
    assert claimed["kind"] == "comparison"
    assert claimed["grounded"] == "no_rate_declined"


def test_no_city_could_pin_anything_before_the_fix() -> None:
    """The correction turn's whole content is two city names. They have to resolve to currencies."""

    assert CITY_TO_CODE["copenhagen"] == "DKK"
    assert CITY_TO_CODE["shanghai"] == "CNY"
    assert CITY_TO_CODE["tokyo"] == "JPY"
    assert CITY_TO_CODE["oslo"] == "NOK"
    assert CITY_TO_CODE["stockholm"] == "SEK"

    bound = {binding.unit: binding.code for binding in location_bindings(LOCATION_FOLLOWUPS[0][1])}
    assert bound == {"kr": "DKK", "¥": "CNY"}


def test_the_reported_answer_never_names_nok_or_jpy_for_copenhagen_or_shanghai() -> None:
    """The exact wrong strings from the report, asserted absent from the exact turn that produced them."""

    first = currency_fast_path(ORIGINAL)
    assert first is not None
    second = currency_fast_path(LOCATION_FOLLOWUPS[0][1], conversation_history=HISTORY)
    assert second is not None

    for reply in (first["response"], second["response"]):
        assert "Copenhagen (NOK)" not in reply
        assert "Shanghai (JPY)" not in reply
    # The first answer may not assert NOK or JPY as THE reading either -- they are candidates only.
    body = first["response"]
    assert "not pinned" in body
    assert body.count("NOK") == body.count("SEK") == body.count("ISK"), (
        "one member of the kr family is being treated differently from the others"
    )


# ========================================================================================
# 2. The first turn: three ambiguous units, three asks, no invented anything
# ========================================================================================


@pytest.mark.parametrize(("name", "prompt"), VARIANTS, ids=[v[0] for v in VARIANTS])
def test_every_phrasing_reads_three_currencies_and_pins_none(name: str, prompt: str) -> None:
    request = currency_comparison_intent(prompt)
    assert request is not None, f"{name}: not recognised as a currency comparison"
    assert {item.unit for item in request.currencies} == {"kr", "$", "¥"}, name
    assert not request.pinned, f"{name}: something was pinned with no evidence"
    assert {tuple(sorted(item.candidates)) for item in request.currencies} == {
        ("DKK", "ISK", "NOK", "SEK"),
        ("JPY",) if False else ("CNY", "JPY"),
        tuple(sorted(("USD", "CAD", "AUD", "SGD", "NZD", "HKD", "MXN", "ARS", "CLP", "COP", "CUP"))),
    }, name


@pytest.mark.parametrize(("name", "prompt"), VARIANTS, ids=[v[0] for v in VARIANTS])
def test_every_phrasing_separates_market_value_from_purchasing_power(name: str, prompt: str) -> None:
    """Both questions are asked and they are read as two, not folded into one."""

    request = currency_comparison_intent(prompt)
    assert request is not None, name
    assert ASK_MARKET_VALUE in request.asks, name
    assert ASK_PURCHASING_POWER in request.asks, name
    assert ASK_HOLD in request.asks, name

    reply = render_currency_comparison(request)
    assert "Known" in reply
    assert "Cannot determine without" in reply
    assert "If using only provided/supplied rates" in reply
    assert "price-level" in reply, "purchasing power was not distinguished from exchange value"


@pytest.mark.parametrize(("name", "prompt"), VARIANTS, ids=[v[0] for v in VARIANTS])
def test_no_phrasing_produces_a_rate_or_a_ranking(name: str, prompt: str) -> None:
    """With no rate in hand there is no ordering to state, and none is stated."""

    reply = render_currency_comparison(currency_comparison_intent(prompt))
    lowered = reply.lower()
    assert "ranked by" not in lowered, f"{name}: ranked three currencies with no rate"
    for hedge in ("roughly", "approximately", "about equal", "around ", "give or take", "ballpark"):
        assert hedge not in lowered, f"{name}: hedged its way to a figure with {hedge!r}"


def test_the_single_inflation_figure_is_reported_as_unable_to_rank() -> None:
    """5% applied to three economies erodes all three equally. Saying which to hold needs more."""

    reply = render_currency_comparison(currency_comparison_intent(ORIGINAL))
    assert "5%" in reply
    assert "nominal interest rate" in reply
    assert "horizon" in reply


def test_no_location_means_no_pin_and_the_gap_is_named() -> None:
    request = currency_comparison_intent(ORIGINAL)
    assert request is not None
    assert len(request.unpinned) == 3
    reply = render_currency_comparison(request)
    for unit in ("kr", "$", "¥"):
        assert f"which currency “{unit}” is" in reply


# ========================================================================================
# 3. The correction turn: rebind, and say what that voids
# ========================================================================================


@pytest.mark.parametrize(
    ("name", "followup", "kr_code", "yen_code"),
    LOCATION_FOLLOWUPS,
    ids=[case[0] for case in LOCATION_FOLLOWUPS],
)
def test_a_location_rebinds_the_symbol_it_names(
    name: str, followup: str, kr_code: str, yen_code: str
) -> None:
    prior = currency_comparison_intent(ORIGINAL)
    assert prior is not None
    updated = comparison_followup(prior, followup)
    assert updated is not None, f"{name}: the correction was not read as one"

    pinned = {item.unit: item.code for item in updated.currencies}
    assert pinned["kr"] == kr_code, name
    assert pinned["¥"] == yen_code, name
    assert pinned["$"] == "", f"{name}: the dollar was pinned by a message that never named it"


@pytest.mark.parametrize(
    ("name", "followup", "kr_code", "yen_code"),
    LOCATION_FOLLOWUPS,
    ids=[case[0] for case in LOCATION_FOLLOWUPS],
)
def test_the_correction_invalidates_the_earlier_reading_by_name(
    name: str, followup: str, kr_code: str, yen_code: str
) -> None:
    """Rule: never silently preserve, and never silently improve either. Say what is now void."""

    prior = currency_comparison_intent(ORIGINAL)
    updated = comparison_followup(prior, followup)
    voided = invalidations(prior, updated)

    by_unit = {item.unit: item for item in voided}
    assert set(by_unit) == {"kr", "¥"}, name
    assert by_unit["kr"].now == kr_code and by_unit["¥"].now == yen_code
    assert by_unit["kr"].kind == "narrowed"
    assert set(by_unit["kr"].previous) == {"SEK", "NOK", "DKK", "ISK"}

    reply = render_currency_comparison(updated, prior=prior)
    assert reply.startswith("Original conclusions invalidated"), name
    for dropped in set(by_unit["kr"].previous) - {kr_code}:
        assert dropped in reply.split("How each amount reads")[0], (
            f"{name}: {dropped} was dropped without being named as dropped"
        )


def test_the_correction_does_not_restart_the_answer() -> None:
    """The follow-up leads with what is void, not with the opening framing of a fresh answer."""

    prior = currency_comparison_intent(ORIGINAL)
    first = render_currency_comparison(prior)
    second = render_currency_comparison(comparison_followup(prior, LOCATION_FOLLOWUPS[0][1]), prior=prior)

    opener = first.splitlines()[0]
    assert opener not in second, "the follow-up restated the opening of the first answer"
    assert second.index("Original conclusions invalidated") == 0
    assert second.index("Original conclusions invalidated") < second.index("How each amount reads")


def test_a_second_correction_replaces_rather_than_narrows() -> None:
    """Oslo then Copenhagen: a reading that was DEFINITE is retracted, and said to be retracted."""

    prior = currency_comparison_intent(ORIGINAL)
    oslo = comparison_followup(prior, "the kr is in Oslo and the ¥ is in Tokyo")
    assert {item.unit: item.code for item in oslo.currencies}["kr"] == "NOK"

    corrected = comparison_followup(oslo, "sorry, the kr is in Copenhagen and the ¥ is in Shanghai")
    voided = {item.unit: item for item in invalidations(oslo, corrected)}
    assert voided["kr"].kind == "replaced"
    assert voided["kr"].previous == ("NOK",)
    assert voided["kr"].now == "DKK"
    assert voided["¥"].previous == ("JPY",) and voided["¥"].now == "CNY"

    reply = render_currency_comparison(corrected, prior=oslo)
    assert "was read as NOK" in reply
    assert "is void" in reply


def test_the_frontdoor_lane_carries_the_correction_through_conversation_history() -> None:
    """End to end at the seam the frontdoor calls: no prior turn, no correction to make."""

    orphan = currency_fast_path(LOCATION_FOLLOWUPS[0][1])
    assert orphan is None or orphan["kind"] != "comparison_followup"

    claimed = currency_fast_path(LOCATION_FOLLOWUPS[0][1], conversation_history=HISTORY)
    assert claimed is not None
    assert claimed["kind"] == "comparison_followup"
    assert claimed["invalidated"] == ["kr", "¥"]
    assert claimed["codes"] == ["DKK", "CNY"]


def test_the_prior_comparison_is_replayed_from_the_users_own_turns() -> None:
    history = [
        {"role": "assistant", "content": "Compare 900 SEK and 900 NOK by value"},
        {"role": "user", "content": "hello"},
        {"role": "user", "content": ORIGINAL},
        {"role": "assistant", "content": "…the kr is in Oslo…"},
    ]
    replayed = prior_comparison(history)
    assert replayed is not None
    assert {item.unit for item in replayed.currencies} == {"kr", "$", "¥"}


# ========================================================================================
# 4. Supplied rates, supplied PPP, and the live seam
# ========================================================================================

PINNED = "The kr is in Copenhagen, the ¥ is in Shanghai, and the $ is USD."


def _pinned_request(extra: str = ""):
    prior = currency_comparison_intent(ORIGINAL)
    return prior, comparison_followup(prior, (PINNED + " " + extra).strip())


def test_supplied_exchange_rates_rank_value_and_still_refuse_purchasing_power() -> None:
    """The load-bearing distinction: FX answers "worth most", and does not answer "goes furthest"."""

    prior, request = _pinned_request("Use these rates: 1 USD = 6.9 DKK and 1 USD = 7.2 CNY.")
    assert request.market_rates == {("USD", "DKK"): Decimal("6.9"), ("USD", "CNY"): Decimal("7.2")}
    assert not request.ppp_rates

    reply = render_currency_comparison(request, prior=prior)
    assert "Ranked by market exchange value" in reply
    assert "Ranked by local purchasing power" not in reply
    assert "price-level data" in reply, "purchasing power was answered from exchange rates"
    # 500 USD -> 3450 DKK; 500 CNY -> 500 * (6.9/7.2) = 479.17 DKK; 500 DKK -> 500 DKK.
    assert "3,450 DKK" in reply and "479.17 DKK" in reply
    assert reply.index("$500 = 3,450") < reply.index("500 kr = 500 DKK") < reply.index("¥500 = 479.17")


def test_supplied_ppp_values_rank_purchasing_power_and_still_refuse_market_value() -> None:
    prior, request = _pinned_request(
        "PPP conversion factors: 1 USD = 6.4 DKK and 1 USD = 4.1 CNY."
    )
    assert request.ppp_rates == {("USD", "DKK"): Decimal("6.4"), ("USD", "CNY"): Decimal("4.1")}
    assert not request.market_rates

    reply = render_currency_comparison(request, prior=prior)
    assert "Ranked by local purchasing power" in reply
    assert "Ranked by market exchange value" not in reply
    assert "cross rates" in reply, "market value was answered from PPP factors"
    # 500 CNY at 6.4/4.1 DKK per CNY = 780.49 DKK.
    assert "3,200 DKK" in reply and "780.49 DKK" in reply


def test_a_ppp_marker_routes_the_pair_away_from_the_market_table() -> None:
    market, ppp = parse_supplied_rates(
        "Market: 1 USD = 6.9 DKK. Using PPP price levels, 1 USD = 6.4 DKK."
    )
    assert market == {("USD", "DKK"): Decimal("6.9")}
    assert ppp == {("USD", "DKK"): Decimal("6.4")}


def test_both_kinds_supplied_answer_both_questions_and_say_they_differ() -> None:
    prior, request = _pinned_request(
        "Rates: 1 USD = 6.9 DKK and 1 USD = 7.2 CNY. PPP conversion factors: 1 USD = 6.4 DKK and "
        "1 USD = 4.1 CNY."
    )
    reply = render_currency_comparison(request, prior=prior)
    assert "Ranked by market exchange value" in reply
    assert "Ranked by local purchasing power" in reply
    assert reply.index("market exchange value") < reply.index("local purchasing power")
    # The two orderings genuinely differ, which is the whole reason they are two answers.
    market_block = reply.split("Ranked by market exchange value")[1].split("Ranked by local")[0]
    ppp_block = reply.split("Ranked by local purchasing power")[1]
    assert market_block.index("500 kr = 500 DKK") < market_block.index("¥500")
    assert ppp_block.index("¥500") < ppp_block.index("500 kr = 500 DKK")


def test_no_live_rates_available_states_the_gap_and_converts_nothing() -> None:
    prior, request = _pinned_request()
    reply = render_currency_comparison(request, prior=prior)
    assert "No live FX source is wired up" in reply
    assert "Ranked by" not in reply
    assert "DKK/USD/CNY" in reply or "DKK/CNY" in reply


def test_a_live_rate_set_is_used_and_attributed_through_the_injected_seam() -> None:
    """The forward-compatible arm: when a feed exists, the same renderer uses it, timestamped.

    This is what keeps "no live FX source" from becoming a lie the day a feed lands — and the
    parameter is why landing one changes the CALLER and nothing in the comparison module.
    """

    prior, request = _pinned_request()
    reply = render_currency_comparison(
        request,
        prior=prior,
        live_rates={("USD", "DKK"): Decimal("6.55"), ("USD", "CNY"): Decimal("7.11")},
        rates_asof="2026-08-12T09:00Z",
    )
    assert "a live rate set as of 2026-08-12T09:00Z" in reply
    assert "3,275 DKK" in reply  # 500 USD x 6.55
    assert "No live FX source is wired up" not in reply
    # A live FX feed still does not answer purchasing power.
    assert "price-level data" in reply


def test_a_live_ppp_set_flows_through_the_same_way() -> None:
    prior, request = _pinned_request()
    reply = render_currency_comparison(
        request,
        prior=prior,
        live_ppp={("USD", "DKK"): Decimal("6.4"), ("USD", "CNY"): Decimal("4.1")},
        ppp_asof="2026-Q2",
    )
    assert "a live PPP set as of 2026-Q2" in reply
    assert "Ranked by local purchasing power" in reply


def test_a_currency_with_no_supplied_path_is_named_rather_than_dropped() -> None:
    prior, request = _pinned_request("Rate: 1 USD = 6.9 DKK.")
    reply = render_currency_comparison(request, prior=prior)
    assert "Not ranked: CNY" in reply
    assert "no supplied rate connects" in reply


def test_per_currency_inflation_is_used_and_a_single_figure_is_not() -> None:
    prior, request = _pinned_request("DKK inflation 3%, CNY inflation 1%, USD inflation 4%.")
    assert request.per_code_inflation == {
        "DKK": Decimal("3"), "CNY": Decimal("1"), "USD": Decimal("4")
    }
    reply = render_currency_comparison(request, prior=prior)
    assert "Real erosion over one year" in reply
    assert "Lowest inflation is not automatically the best to hold" in reply


# ========================================================================================
# 5. No-hallucination proof
# ========================================================================================

_SCANNED_MODULES = ("core/currency_comparison.py", "core/entity_consistency.py")

#: Numeric literals the new modules may contain, each with a stated structural reason. Anything
#: else -- in particular anything that could be an exchange rate or a price level -- fails the scan.
_ALLOWED_NUMBERS = {
    "0": "index and emptiness comparisons",
    "1": "single-candidate checks, the identity rate of an anchor, enumerate(start=1)",
    "2": "the two-element minimum for a comparison, slice bounds",
    "3": "the minimum content words that make a sentence more than filler",
    "24": "the maximum character gap between an entity and its claimed parent",
    "0.01": "the two-decimal quantum used to format an amount, not a rate",
}


@pytest.mark.parametrize("relative", _SCANNED_MODULES)
def test_the_module_holds_no_rate_or_price_level_constant(relative: str) -> None:
    """Walk the AST and refuse any number that is not declared above.

    A stored rate is the most attractive wrong fix in this area: it turns every test in the family
    green and is wrong the next morning. A stored PPP factor is worse, because nobody checks it.
    The scan covers both hiding places — a bare literal, and a number inside a string where
    `Decimal("6.9")` would put it.
    """

    source = pathlib.Path(__file__).resolve().parents[1] / relative
    tree = ast.parse(source.read_text(encoding="utf-8"))

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or isinstance(node.value, bool):
            continue
        value = node.value
        if isinstance(value, (int, float)):
            if str(value) not in _ALLOWED_NUMBERS:
                offenders.append(f"line {node.lineno}: numeric literal {value!r}")
        elif isinstance(value, str):
            stripped = value.strip()
            if re.fullmatch(r"\d+(?:\.\d+)?", stripped) and stripped not in _ALLOWED_NUMBERS:
                offenders.append(f"line {node.lineno}: numeric string {value!r}")
    assert not offenders, (
        f"{relative} gained a number that is not a declared structural constant. If it is an "
        f"exchange rate or a price level, that is the defect QA-050-027 exists to prevent:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize(("name", "prompt"), VARIANTS, ids=[v[0] for v in VARIANTS])
def test_no_answer_states_a_number_it_was_not_given(name: str, prompt: str) -> None:
    """Every figure in a rateless answer traces to the prompt or to a small enumeration.

    This is the assertion the reported defect would fail: a remembered NOK/USD rate is a number
    that appears in no input, and it is exactly what this catches.
    """

    reply = render_currency_comparison(currency_comparison_intent(prompt))
    allowed = _numbers(prompt) | {str(index) for index in range(0, 13)}
    strays = sorted(_numbers(reply) - allowed)
    assert not strays, f"{name}: answer states {strays}, which came from nowhere in the input"


def test_a_supplied_rate_answer_states_only_supplied_and_derived_numbers() -> None:
    prior, request = _pinned_request("Use these rates: 1 USD = 6.9 DKK and 1 USD = 7.2 CNY.")
    reply = render_currency_comparison(request, prior=prior)
    allowed = (
        _numbers(ORIGINAL)
        | _numbers(PINNED + " Use these rates: 1 USD = 6.9 DKK and 1 USD = 7.2 CNY.")
        | {"3,450", "479.17"}  # 500x6.9 and 500x(6.9/7.2), both computed here
        | {str(index) for index in range(0, 13)}
    )
    assert not sorted(_numbers(reply) - allowed)


# ========================================================================================
# 6. Negative controls -- the lane must not swallow these
# ========================================================================================


@pytest.mark.parametrize(
    ("prompt", "expected_codes"),
    [
        ("what is kr?", ("SEK", "NOK", "DKK", "ISK")),
        ("what is ¥?", ("JPY", "CNY")),
    ],
)
def test_a_bare_symbol_definition_is_still_a_definition(prompt: str, expected_codes: tuple[str, ...]) -> None:
    """One currency and no comparison. The definition lane keeps it; nothing here is ranked."""

    assert currency_comparison_intent(prompt) is None
    claimed = currency_fast_path(prompt)
    assert claimed is not None and claimed["kind"] == "definition"
    for code in expected_codes:
        assert code in claimed["response"]
    assert "Cannot determine without" not in claimed["response"]


@pytest.mark.parametrize(
    "prompt",
    [
        "write a README about PPP",
        "write me a README explaining purchasing power parity for the docs folder",
        "explain purchasing power parity",
        "what is purchasing power parity and why do economists use it",
        "explain the difference between nominal and real exchange rates",
    ],
)
def test_a_turn_about_the_concept_is_not_a_comparison(prompt: str) -> None:
    """No currency amounts, so nothing to compare and nothing to decline. The lane stays out."""

    assert currency_comparison_intent(prompt) is None
    claimed = currency_fast_path(prompt)
    assert claimed is None, f"{prompt!r} was claimed as {claimed and claimed['kind']}"


def test_a_bare_rate_question_requires_a_live_source_and_states_no_number() -> None:
    prompt = "current USD/DKK rate today"
    assert currency_comparison_intent(prompt) is None

    lookup = fx_rate_lookup_intent(prompt)
    assert lookup is not None
    assert (lookup.base.code, lookup.quote.code) == ("USD", "DKK")
    assert lookup.asked_for_current

    reply = render_fx_rate_lookup(lookup)
    assert "no FX source is wired up" in reply
    assert not _numbers(reply), f"a rate question was answered with the numbers {_numbers(reply)}"

    claimed = currency_fast_path(prompt)
    assert claimed is not None and claimed["kind"] == "rate_lookup"
    assert claimed["grounded"] == "no_rate_declined"


def test_a_rate_question_uses_a_live_rate_when_one_is_passed_in() -> None:
    reply = render_fx_rate_lookup(
        fx_rate_lookup_intent("what is the USD/DKK rate right now"),
        live_rate=Decimal("6.55"),
        rate_asof="2026-08-12T09:00Z",
    )
    assert "1 USD = 6.55 DKK" in reply
    assert "as of 2026-08-12T09:00Z" in reply


@pytest.mark.parametrize(
    "prompt",
    [
        "send 500 kr and $500 to my wallet",
        "buy $500 of kr and ¥500 of yen for me",
        "transfer 500 kr, $500 and ¥500 to my bank account",
    ],
)
def test_a_transfer_request_is_never_answered_with_a_comparison_table(prompt: str) -> None:
    """Moving money is a different lane with different safeguards, whatever it names."""

    assert currency_transaction_intent(prompt)
    assert currency_comparison_intent(prompt) is None
    assert currency_fast_path(prompt) is None


@pytest.mark.parametrize(
    "prompt",
    [
        "compare purchasing power across Europe",
        "how does purchasing power work",
        "purchasing power",
    ],
)
def test_the_compound_noun_fix_does_not_open_the_lane_to_topic_talk(prompt: str) -> None:
    assert not currency_transaction_intent(prompt)
    assert currency_comparison_intent(prompt) is None


def test_a_real_purchase_instruction_still_reads_as_one() -> None:
    """The compound-noun exemption must not disarm the transaction gate itself."""

    assert currency_transaction_intent("buy 500 DKK with my card")
    assert currency_transaction_intent("purchase 500 kr worth of yen")
    assert currency_transaction_intent("send $500 to my wallet")


# ========================================================================================
# 7. The general rule: entity/context compatibility beyond currency
# ========================================================================================

CONTRADICTIONS: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
    ("cars", "Toyota Passat vs Renault Golf",
     (("passat", "Volkswagen"), ("golf", "Volkswagen"))),
    ("products", "Apple Galaxy vs Samsung iPhone",
     (("galaxy", "Samsung"), ("iphone", "Apple"))),
    ("cities", "compare Paris in Germany and Berlin in France",
     (("paris", "France"), ("berlin", "Germany"))),
    ("countries", "France with London vs Germany with Warsaw",
     (("london", "the United Kingdom"), ("warsaw", "Poland"))),
    ("currency_cities", "compare Copenhagen NOK and Oslo DKK",
     (("copenhagen", "DKK"), ("oslo", "NOK"))),
    ("currency_asia", "compare Shanghai JPY and Tokyo CNY",
     (("shanghai", "CNY"), ("tokyo", "JPY"))),
)


@pytest.mark.parametrize(
    ("name", "prompt", "expected"), CONTRADICTIONS, ids=[case[0] for case in CONTRADICTIONS]
)
def test_an_impossible_pairing_is_detected_in_every_domain(
    name: str, prompt: str, expected: tuple[tuple[str, str], ...]
) -> None:
    report = entity_consistency_report(prompt)
    found = {(item.child, item.actual_parent) for item in report.contradictions}
    assert found == set(expected), f"{name}: {found}"


@pytest.mark.parametrize(
    ("name", "prompt", "expected"), CONTRADICTIONS, ids=[case[0] for case in CONTRADICTIONS]
)
def test_an_impossible_pairing_asks_instead_of_picking(
    name: str, prompt: str, expected: tuple[tuple[str, str], ...]
) -> None:
    """Rule 4: contradictory but not enough to rebind, so ask — and compute nothing meanwhile."""

    reply = render_entity_contradictions(entity_consistency_report(prompt))
    assert reply
    assert "?" in reply
    for child, actual in expected:
        assert actual in reply
        assert child in reply.lower()
    assert not _numbers(reply), f"{name}: answered a broken pairing with figures"


@pytest.mark.parametrize(
    ("name", "prompt", "expected"), CONTRADICTIONS, ids=[case[0] for case in CONTRADICTIONS]
)
def test_the_fast_path_claims_a_broken_pairing_rather_than_letting_a_model_smooth_it_over(
    name: str, prompt: str, expected: tuple[tuple[str, str], ...]
) -> None:
    claimed = currency_fast_path(prompt)
    assert claimed is not None, f"{name}: a contradictory turn was left to the model"
    assert claimed["kind"] in {"entity_contradiction", "comparison", "comparison_followup"}
    assert "does not hold" in claimed["response"]


@pytest.mark.parametrize(
    "prompt",
    [
        "I flew from Berlin to France last week",
        "I drive a Volkswagen Passat and my brother has a Toyota Corolla",
        "compare an iPhone from Apple with a Galaxy from Samsung",
        "Copenhagen uses DKK and Oslo uses NOK",
        "the Passat is a Volkswagen",
        "Warsaw is in Poland, Berlin is in Germany",
    ],
)
def test_a_consistent_or_incidental_pairing_is_silent(prompt: str) -> None:
    """Silence, not a guess. A journey is not a claim and a correct pairing is not a contradiction."""

    assert entity_consistency_report(prompt).consistent, prompt


@pytest.mark.parametrize(
    "prompt",
    [
        "compare a Peugeot Kolibri with a Renault Kolibri",
        "which is better, a Zyxel Frobnicator or a Acme Frobnicator",
        "compare Ankh-Morpork in Lancre with Genua in Klatch",
    ],
)
def test_no_authority_means_no_claim_at_all(prompt: str) -> None:
    """When no table covers it, the checker invents no certainty — it says nothing."""

    assert entity_consistency_report(prompt).consistent, prompt
    assert render_entity_contradictions(entity_consistency_report(prompt)) == ""


def test_a_freshly_registered_authority_is_enforced_immediately() -> None:
    """The reusable interface, proved: a domain that did not exist is checked with no code change."""

    authority = EntityAuthority(
        domain="test_instruments",
        child_label="instrument",
        parent_label="maker",
        relation="is made by",
        members={"stratocaster": "Fender", "les paul": "Gibson"},
        parent_aliases={},
        parent_preposition="from",
    )
    assert entity_consistency_report("a Gibson Stratocaster").consistent
    register_authority(authority)
    try:
        report = entity_consistency_report("a Gibson Stratocaster")
        assert not report.consistent
        found = report.contradictions[0]
        assert (found.child, found.actual_parent, found.named_parent) == (
            "stratocaster", "Fender", "Gibson",
        )
        assert "Fender" in render_entity_contradictions(report)
    finally:
        unregister_authority("test_instruments")
    assert entity_consistency_report("a Gibson Stratocaster").consistent


def test_every_city_agrees_across_the_geography_and_currency_tables() -> None:
    """Two tables, one world. A city whose country issues a different currency is a data defect."""

    from core.currency_intent import PLACE_TO_CODE

    country_currency = {
        "Denmark": "DKK", "Norway": "NOK", "Sweden": "SEK", "Iceland": "ISK", "China": "CNY",
        "Japan": "JPY", "the United Kingdom": "GBP", "the United States": "USD", "Canada": "CAD",
        "Australia": "AUD", "New Zealand": "NZD", "Switzerland": "CHF", "Poland": "PLN",
        "Czechia": "CZK", "Hungary": "HUF", "Türkiye": "TRY", "India": "INR",
        "South Korea": "KRW", "Thailand": "THB", "Vietnam": "VND", "Indonesia": "IDR",
        "Malaysia": "MYR", "the Philippines": "PHP", "Taiwan": "TWD", "Brazil": "BRL",
        "Mexico": "MXN", "South Africa": "ZAR", "Nigeria": "NGN", "Egypt": "EGP", "Kenya": "KES",
        "Morocco": "MAD", "Israel": "ILS", "Pakistan": "PKR", "Bangladesh": "BDT",
        "Sri Lanka": "LKR", "Nepal": "NPR", "Kazakhstan": "KZT", "Azerbaijan": "AZN",
        "Georgia": "GEL", "Russia": "RUB", "Ukraine": "UAH", "Romania": "RON",
        "Bulgaria": "EUR",  # euro since 2026-01-01 "Argentina": "ARS", "Chile": "CLP", "Colombia": "COP", "Peru": "PEN",
        "Qatar": "QAR", "Saudi Arabia": "SAR", "the United Arab Emirates": "AED",
        "France": "EUR", "Germany": "EUR", "Spain": "EUR", "Italy": "EUR", "Portugal": "EUR",
        "Ireland": "EUR", "Austria": "EUR", "Belgium": "EUR", "Greece": "EUR", "Finland": "EUR",
        "the Netherlands": "EUR",
    }
    for city, country in CITY_TO_COUNTRY.items():
        if city not in CITY_TO_CODE or country not in country_currency:
            continue
        assert CITY_TO_CODE[city] == country_currency[country], (
            f"{city} is in {country} but the currency table says {CITY_TO_CODE[city]}"
        )
    for code in set(CITY_TO_CODE.values()) | set(PLACE_TO_CODE.values()):
        assert code in ISO_4217, f"{code} is not in the ISO table"


def test_a_poisoned_binding_blocks_every_figure_in_the_comparison() -> None:
    """Rule 6, at the seam that would otherwise happily produce a ranking from good rates."""

    prior = currency_comparison_intent(ORIGINAL)
    poisoned = comparison_followup(
        prior,
        "The kr is in Copenhagen NOK and the ¥ is in Shanghai. "
        "Rates: 1 USD = 6.9 DKK and 1 USD = 7.2 CNY.",
    )
    assert poisoned.poisoned
    reply = render_currency_comparison(poisoned, prior=prior)
    assert "Contradiction in what was given" in reply
    assert "Ranked by" not in reply, "ranked from a binding an authority table says cannot hold"
    assert "3,450" not in reply

    claimed = currency_fast_path(
        "The kr is in Copenhagen NOK. Rates: 1 USD = 6.9 DKK.", conversation_history=HISTORY
    )
    assert claimed is not None and claimed["grounded"] == "contradiction_declined"


def test_a_location_outside_the_symbol_family_is_reported_not_applied() -> None:
    prior = currency_comparison_intent(ORIGINAL)
    updated = comparison_followup(prior, "The kr is in Tokyo.")
    assert updated.conflicts and not updated.conflicts[0].pins
    assert {item.unit: item.code for item in updated.currencies}["kr"] == ""
    reply = render_currency_comparison(updated, prior=prior)
    assert "Tokyo uses JPY" in reply
    assert "Nothing in that changes" not in reply


def test_evidence_that_disagrees_with_itself_pins_nothing() -> None:
    """A city and an ISO code that name different currencies leaves the unit exactly as open."""

    prior = currency_comparison_intent(ORIGINAL)
    updated = comparison_followup(prior, "The kr is in Copenhagen. Rate: 1 USD = 9.9 NOK.")
    assert {item.unit: item.code for item in updated.currencies}["kr"] == ""
    assert any(item.domain == "currency_evidence" for item in updated.contradictions)
    assert "Ranked by" not in render_currency_comparison(updated, prior=prior)


# ========================================================================================
# 8. The conductor lane reads the same policy
# ========================================================================================


def test_the_conductor_context_pins_a_bare_unit_beside_a_city() -> None:
    context = extract_shared_context(
        "I have 500 kr and 500 more kr. The kr is in Copenhagen."
    )
    ambiguity = next(item for item in context.ambiguities if item.token == "kr")
    assert ambiguity.resolved == "DKK"
    assert "Copenhagen" in ambiguity.why
    assert not any(item.kind == "ambiguous_unit" and "'kr'" in item.statement for item in context.missing)


def test_the_singapore_invariant_survives_the_city_table() -> None:
    """A dollar SPENT in Singapore is still not a Singapore dollar. The amount is what separates them."""

    context = extract_shared_context(
        "A traveler exchanged 8,500 kr for $1,240, then spent $300 in Singapore and came home "
        "with 2,100 kr."
    )
    dollar = next(item for item in context.ambiguities if item.token == "$")
    assert dollar.unresolved
    assert "SGD" in dollar.candidates and "USD" in dollar.candidates
    assert not location_bindings(
        "then spent $300 in Singapore and came home with 2,100 kr"
    )


def test_the_conductor_context_reports_a_broken_pairing_as_a_gap_in_the_premise() -> None:
    context = extract_shared_context(
        "A buyer in Berlin in France paid 500 kr. Work out what that is in euros."
    )
    broken = [item for item in context.missing if item.kind == "entity_contradiction"]
    assert broken, "a node would have been handed the poisoned binding with nothing said"
    assert "Berlin" in broken[0].statement and "Germany" in broken[0].statement
    assert "Do not answer from either reading" in broken[0].statement


# ========================================================================================
# 9. Adversarial input
# ========================================================================================


@pytest.mark.parametrize(
    "prompt",
    ["", "   ", "\n\t ", "?", "500", "kr", "¥¥¥", "compare", "compare  ,,,  and", "$" * 200],
)
def test_malformed_input_is_declined_without_raising(prompt: str) -> None:
    assert currency_comparison_intent(prompt) is None
    assert entity_consistency_report(prompt).consistent
    assert currency_fast_path(prompt) is None or currency_fast_path(prompt)["kind"] != "comparison"


def test_a_huge_amount_with_separators_is_read_exactly() -> None:
    request = currency_comparison_intent(
        "Compare 1,234,567.89 kr and $9,876,543.21 by value and purchasing power"
    )
    assert request is not None
    assert [item.amount for item in request.currencies] == [
        Decimal("1234567.89"), Decimal("9876543.21")
    ]


def test_a_rate_of_zero_is_refused_rather_than_dividing_by_it() -> None:
    market, _ppp = parse_supplied_rates("Rate: 1 USD = 0 DKK")
    assert market == {}
    prior, request = _pinned_request("Rate: 1 USD = 0 DKK.")
    reply = render_currency_comparison(request, prior=prior)
    assert "Ranked by" not in reply


def test_contradictory_locations_for_one_symbol_pin_nothing() -> None:
    prior = currency_comparison_intent(ORIGINAL)
    updated = comparison_followup(prior, "The kr is in Copenhagen Oslo Stockholm.")
    assert updated is None or {item.unit: item.code for item in updated.currencies}["kr"] == ""


def test_the_readers_are_safe_to_run_concurrently() -> None:
    """Pure functions, no shared mutable state. A side channel between calls would show up here."""

    prompts = [prompt for _name, prompt in VARIANTS] + [case[1] for case in CONTRADICTIONS]
    results: list[tuple[int, int]] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def drive(prompt: str) -> None:
        try:
            for _ in range(20):
                request = currency_comparison_intent(prompt)
                units = len(request.currencies) if request else 0
                broken = len(entity_consistency_report(prompt).contradictions)
                with lock:
                    results.append((units, broken))
        except BaseException as exc:  # pragma: no cover - only on a real race
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=drive, args=(prompt,)) for prompt in prompts * 2]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert len(results) == len(threads) * 20


# ========================================================================================
# 10. Whole-turn coverage, and what a decline still hands forward
# ========================================================================================

MIXED_TURNS: tuple[tuple[str, str], ...] = (
    (
        "currency_then_files",
        "Compare 500 kr, $500 and ¥500 by value and purchasing power. "
        "Also list the files in the reports folder and tell me which is newest.",
    ),
    (
        "files_then_currency",
        "Read the config file and summarise what it sets. "
        "Then compare 500 kr, $500 and ¥500 on purchasing power.",
    ),
    (
        "currency_then_code",
        "Which is worth most, 500 kr, $500 or ¥500? "
        "Separately, refactor the retry helper so it stops swallowing timeouts.",
    ),
)


@pytest.mark.parametrize(("name", "prompt"), MIXED_TURNS, ids=[case[0] for case in MIXED_TURNS])
def test_a_mixed_turn_is_never_swallowed_by_this_gate(name: str, prompt: str) -> None:
    """A gate that claims a turn ends it, so it may only claim what it covers entirely."""

    assert currency_comparison_intent(prompt) is not None, f"{name}: the currency half is still read"
    assert uncovered_residue(prompt), f"{name}: the non-currency half was not detected as residue"
    assert not covers_whole_turn(prompt)
    assert currency_fast_path(prompt) is None, f"{name}: the gate answered half a turn"


@pytest.mark.parametrize(("name", "prompt"), VARIANTS, ids=[v[0] for v in VARIANTS])
def test_a_pure_currency_turn_proves_whole_turn_coverage(name: str, prompt: str) -> None:
    assert covers_whole_turn(prompt), f"{name}: residue {uncovered_residue(prompt)!r}"
    assert currency_fast_path(prompt) is not None, name


@pytest.mark.parametrize(("name", "prompt"), MIXED_TURNS, ids=[case[0] for case in MIXED_TURNS])
def test_a_declined_turn_still_hands_its_iso_findings_forward(name: str, prompt: str) -> None:
    """Rule: on decline, supply the grounded ambiguity rather than leaving a model to guess."""

    observation = currency_grounding_observation(prompt)
    assert observation is not None, f"{name}: declined and discarded"
    assert observation["schema"] == "tool_observation_v1"
    assert observation["intent"] == "currency.grounding"
    assert observation["final_answer"] is False

    units = {reading["unit"]: reading for reading in observation["readings"]}
    assert set(units) == {"kr", "$", "¥"}
    assert all(not reading["resolved"] for reading in units.values())
    assert set(units["¥"]["candidates"]) == {"JPY", "CNY"}
    assert observation["unanswered_here"], "the residue was not carried with the findings"

    note = observation["instruction"]
    assert "NOT determined" in note
    assert "do not state an exchange rate" in note.lower()
    assert not _numbers(note) - _numbers(prompt)


def test_the_findings_land_on_the_channel_the_model_prompt_actually_reads() -> None:
    """`runtime_tool_observations` is the path the prompt assembler renders. Nowhere else."""

    context: dict[str, object] = {}
    assert attach_currency_grounding(context, MIXED_TURNS[0][1])
    observations = context["runtime_tool_observations"]
    assert isinstance(observations, list) and len(observations) == 1
    assert observations[0]["intent"] == "currency.grounding"

    # It appends rather than replacing, and it stays inside the channel's own bound.
    context["runtime_tool_observations"] = [{"schema": "tool_observation_v1"}] * 12
    assert attach_currency_grounding(context, MIXED_TURNS[0][1])
    assert len(context["runtime_tool_observations"]) == 12
    assert context["runtime_tool_observations"][-1]["intent"] == "currency.grounding"

    assert not attach_currency_grounding({}, "list the files in the reports folder")
    assert not attach_currency_grounding(None, MIXED_TURNS[0][1])


def test_the_grounding_note_carries_a_resolution_once_a_location_settles_it() -> None:
    observation = currency_grounding_observation(
        "The kr is in Copenhagen and the ¥ is in Shanghai. Now also open the ledger file.",
        conversation_history=HISTORY,
    )
    assert observation is not None
    units = {reading["unit"]: reading for reading in observation["readings"]}
    assert units["kr"]["resolved"] == "DKK"
    assert units["kr"]["basis"] == "location:copenhagen"
    assert units["¥"]["resolved"] == "CNY"
    assert units["$"]["resolved"] == ""


def test_a_mixed_turn_carrying_a_broken_pairing_asks_nowhere_and_reports_everywhere() -> None:
    """The clarification must not eat the other request either — same rule, other domain."""

    prompt = "Toyota Passat vs Renault Golf. Also list the files in the reports folder."
    report = entity_consistency_report(prompt)
    assert len(report.contradictions) == 2
    assert uncovered_by_contradictions(prompt, report) == (
        "Also list the files in the reports folder",
    )
    assert currency_fast_path(prompt) is None, "the clarification swallowed the whole turn"

    observation = currency_grounding_observation(prompt)
    assert observation is not None
    assert observation["intent"] == "entity.contradiction"
    assert {item["child"] for item in observation["pairings"]} == {"passat", "golf"}
    assert "Do not rank, calculate or explain" in observation["instruction"]
    assert observation["unanswered_here"] == ["Also list the files in the reports folder"]


def test_a_single_intent_broken_pairing_is_still_answered_here() -> None:
    prompt = "compare Copenhagen NOK and Oslo DKK"
    assert uncovered_by_contradictions(prompt, entity_consistency_report(prompt)) == ()
    claimed = currency_fast_path(prompt)
    assert claimed is not None and claimed["kind"] == "entity_contradiction"


def test_a_turn_with_no_currency_in_it_hands_nothing_forward() -> None:
    assert currency_grounding_observation("list the files in the reports folder") is None
    assert currency_grounding_observation("") is None
    assert currency_grounding_observation("send $500 to my wallet and list the files") is None


# ========================================================================================
# 11. Case and chat context -- the currency case/context branch's API, not a second copy
# ========================================================================================


def test_the_turn_is_never_uppercased_to_find_a_code() -> None:
    """A lowercase homograph is not an ISO code, and pinning through one would prove it was.

    CUP is a real code inside the dollar family, so an implementation that uppercased the turn to
    go looking for codes would read "the cup is on the table" as pinning the dollar to the Cuban
    peso. The codes come from `codes_named`, which holds the homograph rule, and this is the test
    that says so.
    """

    prior = currency_comparison_intent(ORIGINAL)
    updated = comparison_followup(prior, "the cup is on the table, all of it, try again")
    assert updated is None or {item.unit: item.code for item in updated.currencies}["$"] == ""

    shouted = comparison_followup(prior, "the $ is CUP")
    assert {item.unit: item.code for item in shouted.currencies}["$"] == "CUP"


@pytest.mark.parametrize(
    ("followup", "unit", "code"),
    [
        ("the ¥ is RMB", "¥", "CNY"),
        ("the ¥ is renminbi", "¥", "CNY"),
        ("the ¥ is the Chinese yuan", "¥", "CNY"),
        ("the kr is the Danish krone", "kr", "DKK"),
        ("the $ is the US dollar", "$", "USD"),
    ],
)
def test_a_currency_named_in_full_pins_exactly_as_an_iso_code_does(
    followup: str, unit: str, code: str
) -> None:
    """"RMB" is how people write it. The name index already knows it, so this lane reads that one."""

    prior = currency_comparison_intent(ORIGINAL)
    updated = comparison_followup(prior, followup)
    assert updated is not None, followup
    assert {item.unit: item.code for item in updated.currencies}[unit] == code


def test_a_code_the_chat_already_settled_pins_a_symbol_the_turn_leaves_open() -> None:
    """Settled-code context is carried, ranked below anything the turn itself says, and labelled."""

    prior = currency_comparison_intent(ORIGINAL)
    carried = comparison_followup(prior, "the kr is in Copenhagen", chat_codes=("USD",))
    by_unit = {item.unit: item for item in carried.currencies}
    assert by_unit["$"].code == "USD"
    assert by_unit["$"].basis == "chat_context"
    assert by_unit["kr"].code == "DKK"
    assert "a code this chat had already settled on" in render_currency_comparison(carried, prior=prior)


def test_the_turn_outranks_the_chat_when_they_disagree() -> None:
    prior = currency_comparison_intent(ORIGINAL)
    carried = comparison_followup(prior, "the $ is SGD", chat_codes=("USD",))
    by_unit = {item.unit: item for item in carried.currencies}
    assert by_unit["$"].code == "SGD"
    assert by_unit["$"].basis == "explicit_code"


def test_this_lane_holds_no_second_currency_resolver() -> None:
    """One authority for what a literal means. A fork here is how two lanes start disagreeing."""

    source = (
        pathlib.Path(__file__).resolve().parents[1] / "core/currency_comparison.py"
    ).read_text(encoding="utf-8")
    assert "resolve_currency_literal" in source
    for forked in ("ISO_4217 = {", "SYMBOL_FAMILIES = {", "UNIT_WORD_FAMILIES = {", "CITY_TO_CODE = {"):
        assert forked not in source, f"{forked!r} was re-declared instead of imported"
    assert ".upper()" in source  # single-token lookups only, asserted by the test above


# ========================================================================================
# 12. Sabotage -- revert a fix, and a test that NAMES the cause must go red
# ========================================================================================


def test_sabotage_removing_the_city_table_loses_the_correction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty the city table — the base's state — and `Copenhagen` stops pinning anything."""

    import core.currency_intent as intents

    monkeypatch.setattr(intents, "LOCATION_TO_CODE", {}, raising=True)
    monkeypatch.setattr(
        intents,
        "_LOCATION_RE",
        re.compile(r"(?!x)x"),
        raising=True,
    )
    assert not location_bindings("Actually the kr is in Copenhagen and the ¥ is in Shanghai.")

    prior = currency_comparison_intent(ORIGINAL)
    updated = comparison_followup(prior, "Actually the kr is in Copenhagen and the ¥ is in Shanghai.")
    assert updated is None or not invalidations(prior, updated), (
        "the correction still landed with no city table, so the city table is not what carries it"
    )


def test_sabotage_reopening_the_purchasing_verb_shuts_the_lane_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Undo the compound-noun exemption and a purchasing-power question is a purchase again.

    Stated with an AMOUNT after the phrase, because that is where the exemption is load-bearing:
    "purchasing power" alone is now also turned away by the object rule below, and a sabotage the
    object rule absorbs would prove nothing about this one.
    """

    import core.currency_intent as intents

    prompt = "Compare the purchasing power of 500 kr and $500 and ¥500"
    assert not currency_transaction_intent(prompt)
    monkeypatch.setattr(intents, "_TOPIC_COMPOUND_RE", re.compile(r"(?!x)x"), raising=True)
    assert currency_transaction_intent(prompt), "the sabotage did not reach the gate"
    assert currency_comparison_intent(prompt) is None
    assert currency_fast_path(prompt) is None


def test_sabotage_dropping_the_money_object_rule_shuts_the_lane_on_the_reported_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transfer verb with no object is not an instruction. Undo that and the reported turn dies.

    "in what they actually buy locally" is a purchasing-power question with a bare transfer verb
    in it, and no compound covers it — `_TOPIC_COMPOUND_RE` matches "purchasing power", not this.
    The object rule is the only thing keeping the phrasing reachable, and this is where that shows.
    """

    import core.currency_intent as intents

    prompt = (
        "How do 500 kr, $500 and ¥500 stack up in value and in what they actually buy locally?"
    )
    assert not currency_transaction_intent(prompt)
    assert currency_comparison_intent(prompt) is not None

    monkeypatch.setattr(
        intents, "_transfer_verb_takes_money",
        lambda lowered: bool(intents._TRANSACTION_VERB_RE.search(lowered)), raising=True,
    )
    assert currency_transaction_intent(prompt), "the sabotage did not reach the gate"
    assert currency_comparison_intent(prompt) is None
    assert currency_fast_path(prompt) is None


def test_sabotage_ignoring_the_contradiction_produces_the_ranking_it_should_refuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blind the poisoned-binding guard and the figures come straight back."""

    import core.currency_comparison as comparison

    monkeypatch.setattr(
        comparison.CurrencyComparisonRequest, "poisoned", property(lambda self: False), raising=True
    )
    prior = currency_comparison_intent(ORIGINAL)
    poisoned = comparison_followup(
        prior,
        "The kr is in Copenhagen NOK and the ¥ is in Shanghai. "
        "Rates: 1 USD = 6.9 DKK and 1 USD = 7.2 CNY.",
    )
    reply = render_currency_comparison(poisoned, prior=prior)
    assert "Ranked by market exchange value" in reply, (
        "the guard is not what stops the ranking, so the control proves nothing"
    )


def test_sabotage_dropping_the_rate_span_exclusion_re_reads_the_rates_as_amounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the span exclusion, "1 USD = 6.4 DKK" is scanned as two amounts to compare."""

    import core.currency_comparison as comparison

    text = PINNED + " PPP conversion factors: 1 USD = 6.4 DKK and 1 USD = 4.1 CNY."
    assert len(scan_amounts(text, exclude=comparison.rate_spans(text))) == 0
    assert len(scan_amounts(text)) >= 4

    monkeypatch.setattr(comparison, "rate_spans", lambda _text: (), raising=True)
    request = currency_comparison_intent(text)
    assert request is not None and len(request.currencies) >= 4, (
        "the rate literals are no longer being scanned as amounts, so the exclusion is dead code"
    )
