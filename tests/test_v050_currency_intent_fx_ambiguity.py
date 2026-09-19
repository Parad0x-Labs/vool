"""QA-050-026: a currency code has a definition; a currency rate does not.

Four defects were reported against 6a73d526, and tracing them through the real seams found ONE
root cause with four faces: this runtime had no currency domain, so nothing in it could tell a
model that TRY is the Turkish lira, and nothing could tell the runtime that a conversion needs a
live rate. Measured on the untouched base, before any of this landed:

    "ok what is TRY?"                  task_class=chat_conversation  market=False  assets=[]
    "i ment money TRY"                 task_class=unknown            market=False  assets=[]
    "1000 TRY to USD?"                 task_class=unknown            market=False  assets=[]
    "What is 500 kr worth in dollars?" task_class=research           market=True   assets=[]

Row by row against what the tester saw:

* `TRY` resolved to nothing, so the model was asked a question it had no grounding for and asked
  for context back;
* "i ment money TRY" was claimed by NOTHING, leaving "money" as the only salient token, and the
  model answered with transaction-safety text. There is no runtime money gate to blame — the one
  money guard, `core/vool_agent_brake.py`, is x402 spend and never fired. The model filled a
  vacuum;
* `market_semantics_present("1000 TRY to USD?")` was **False**, so no grounding requirement ever
  attached and the model answered from training memory: ~$590-610. That False is the mechanism
  behind the invented rate, and `test_the_reported_hallucinated_conversion_states_no_number` is
  its regression pin;
* the fourth prompt looked handled and was not. It reached the market lane on the word "worth"
  being in `MARKET_TERMS` — an accident of vocabulary, with nothing understanding `kr` at all.
  `test_the_kr_case_was_an_accident_of_the_word_worth` proves the accident by deleting the word.

The anti-overfit contract these tests are written to
-----------------------------------------------------
Every behaviour is asserted over a semantic FAMILY (many ways to ask the same thing), sloppy
variants (typos, casing, missing punctuation), negative controls (real transaction requests, which
must stay OUT), and adversarial near-misses (the homograph trap: "let me try USD mode" is not a
question about the lira). A test that pinned only the four reported strings would pass on a
four-string lookup table, which is exactly the class of non-fix this project bans by construction.

The no-hallucination guarantee is asserted TWICE, structurally and behaviourally, because a
promise in a docstring is not a mechanism:

* `test_the_module_holds_no_rate_shaped_constant` walks the module AST and requires every numeric
  literal — including numbers hidden in strings, where a `Decimal("18.5")` would live — to be a
  declared structural constant. A rate cannot be added to that file without this failing by name;
* `test_no_conversion_invents_a_number` renders EVERY conversion in the family and asserts the
  output contains no number the user did not type. That one holds even if the rate arrives by some
  route the AST scan never imagined.
"""

from __future__ import annotations

import ast
import pathlib
import re
from decimal import Decimal

import pytest

from core.currency_intent import (
    ISO_4217,
    codes_named,
    currency_definition_intent,
    currency_semantics_present,
    currency_transaction_intent,
    fx_conversion_intent,
    render_currency_definition,
    render_fx_conversion,
)

# =================================================================================================
# The four reported defects, verbatim.
# =================================================================================================

REPORTED_DEFINITION = "ok what is TRY?"
REPORTED_CORRECTION = "i ment money TRY"          # the tester's typo, kept exactly
REPORTED_CONVERSION = "1000 TRY to USD?"
REPORTED_AMBIGUITY = "What is 500 kr worth in dollars?"


def _definition_text(prompt: str) -> str:
    request = currency_definition_intent(prompt)
    assert request is not None, f"no definition reading claimed {prompt!r}"
    return render_currency_definition(request)


def _conversion_text(prompt: str, **kwargs) -> str:
    request = fx_conversion_intent(prompt)
    assert request is not None, f"no conversion reading claimed {prompt!r}"
    return render_fx_conversion(request, **kwargs)


def test_the_reported_definition_names_the_turkish_lira() -> None:
    # Defect 1: VOOL asked for more context instead of defining TRY.
    answer = _definition_text(REPORTED_DEFINITION)
    assert "Turkish lira" in answer
    assert "TRY" in answer


def test_the_reported_correction_is_currency_context_not_a_transaction() -> None:
    # Defect 2: "money TRY" was answered with money-transaction safety text. The word "money" is a
    # topic word; it must read as currency CONTEXT and never as an instruction to move funds.
    assert currency_transaction_intent(REPORTED_CORRECTION) is False
    assert "Turkish lira" in _definition_text(REPORTED_CORRECTION)


def test_the_reported_hallucinated_conversion_states_no_number() -> None:
    # Defect 3: "1000 TRY ≈ $590-610" — an invented, stale rate. The only number allowed in this
    # answer is the 1000 the user typed.
    answer = _conversion_text(REPORTED_CONVERSION)
    assert _numbers_in(answer) <= {"1000"}
    assert "590" not in answer and "610" not in answer
    assert "rate" in answer.lower()


def test_the_reported_ambiguity_names_every_kr_currency() -> None:
    # Defect 4: it recognised kr ambiguity but answered with a vague approximate range.
    answer = _conversion_text(REPORTED_AMBIGUITY)
    for code in ("SEK", "NOK", "DKK", "ISK"):
        assert code in answer, f"{code} missing from the kr ambiguity notice"
    assert _numbers_in(answer) <= {"500", "4217"}


def test_the_kr_case_was_an_accident_of_the_word_worth() -> None:
    """The fourth prompt only reached a sensible lane because "worth" is in `MARKET_TERMS`.

    Delete that one word and the old routing had nothing left. Both phrasings must now be handled
    by the currency reading, which does not care about the market vocabulary at all.
    """

    from core.market_intent import market_semantics_present

    without_worth = "What is 500 kr in dollars?"
    assert market_semantics_present(REPORTED_AMBIGUITY) is True
    assert market_semantics_present(without_worth) is False, (
        "if this becomes True the accident has been reintroduced elsewhere"
    )
    # Both are handled here regardless of what the market vocabulary happens to contain.
    for prompt in (REPORTED_AMBIGUITY, without_worth):
        assert fx_conversion_intent(prompt) is not None


# =================================================================================================
# Semantic families — many ways to ask the same thing.
# =================================================================================================

DEFINITION_FAMILY = (
    "ok what is TRY?",
    "what is TRY?",
    "what is TRY money?",
    "what is the TRY currency?",
    "i meant money TRY",
    "i ment money TRY",
    "TRY currency?",
    "TRY money?",
    "what does TRY mean?",
    "what does TRY stand for?",
    "define TRY",
    "meaning of TRY",
    "Turkish lira code?",
    "code for Turkish lira",
    "hey what is TRY",
    "so what is TRY",
)


@pytest.mark.parametrize("prompt", DEFINITION_FAMILY)
def test_every_way_of_asking_what_try_is_gets_the_lira(prompt: str) -> None:
    answer = _definition_text(prompt)
    assert "Turkish lira" in answer, f"{prompt!r} did not resolve to the lira"


@pytest.mark.parametrize(
    ("prompt", "code", "name"),
    [
        ("what is USD?", "USD", "United States dollar"),
        ("what is SEK?", "SEK", "Swedish krona"),
        ("what is NOK?", "NOK", "Norwegian krone"),
        ("what is DKK?", "DKK", "Danish krone"),
        ("what is ISK?", "ISK", "Icelandic króna"),
        ("what is Kč?", "CZK", "Czech koruna"),
        ("what is CZK?", "CZK", "Czech koruna"),
        ("what is JPY?", "JPY", "Japanese yen"),
        ("what is SGD?", "SGD", "Singapore dollar"),
    ],
)
def test_the_definition_family_resolves_each_code(prompt: str, code: str, name: str) -> None:
    answer = _definition_text(prompt)
    assert code in answer and name in answer


def test_kc_is_the_czech_koruna_and_not_the_generic_kr_family() -> None:
    """Rule A's specific ask: Kč is one currency, kr is four. They must not collapse together."""

    czech = _definition_text("what is Kč?")
    assert "Czech koruna" in czech
    for krona in ("Swedish", "Norwegian", "Danish", "Icelandic"):
        assert krona not in czech, "Kč was answered with the ambiguous kr family"


def test_bare_kr_is_reported_as_the_whole_ambiguous_family() -> None:
    answer = _definition_text("what is kr?")
    for code in ("SEK", "NOK", "DKK", "ISK"):
        assert code in answer


# =================================================================================================
# Sloppy variants — the way people actually type.
# =================================================================================================

SLOPPY_VARIANTS = (
    "ok what is TRY",          # no question mark
    "okay what is TRY?",       # spelled-out opener
    "what is  TRY ?",          # doubled and stray whitespace
    "WHAT IS TRY?",            # shouting
    "i ment money TRY",        # the reported typo
    "i mean money TRY",        # present tense
    "i meant currency TRY",    # synonym for the context word
    "  what is TRY?  ",        # surrounding whitespace
    "q what is TRY?",          # terse opener
)


@pytest.mark.parametrize("prompt", SLOPPY_VARIANTS)
def test_sloppy_phrasing_still_reaches_the_lira(prompt: str) -> None:
    assert "Turkish lira" in _definition_text(prompt)


CONVERSION_FAMILY = (
    "1000 TRY to USD?",
    "1000 TRY to USD",
    "convert 1000 TRY into dollars",
    "convert 1000 TRY to USD",
    "1000 TRY in USD",
    "500 SEK to USD",
    "500 DKK in USD",
    "What is 500 kr worth in dollars?",
    "¥5000 to dollars",
    "$300 in Singapore",
    "1000 TRY = USD",
    "1000 TRY -> USD",
)


@pytest.mark.parametrize("prompt", CONVERSION_FAMILY)
def test_every_conversion_phrasing_is_recognised_as_a_conversion(prompt: str) -> None:
    assert fx_conversion_intent(prompt) is not None, f"{prompt!r} was not read as a conversion"


@pytest.mark.parametrize("prompt", CONVERSION_FAMILY)
def test_no_conversion_is_mistaken_for_a_definition(prompt: str) -> None:
    # A conversion starting "What is ..." must not be swallowed by the definition lane and answered
    # with a stable fact when the user asked for a live one.
    assert currency_definition_intent(prompt) is None


# =================================================================================================
# THE NO-HALLUCINATION PROOF. Two independent mechanisms.
# =================================================================================================

_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers_in(text: str) -> set[str]:
    """Every number in a rendered answer, normalised so 1,000 and 1000 compare equal."""

    return {match.group(0).replace(",", "") for match in _NUMBER_RE.finditer(text)}


@pytest.mark.parametrize("prompt", CONVERSION_FAMILY)
def test_no_conversion_invents_a_number(prompt: str) -> None:
    """The behavioural half: with no rate in hand, the only number out is the number in.

    This is the direct regression pin for "1000 TRY ≈ $590-610". It holds for every phrasing in
    the family, not just the reported one, and it would fail on an approximate RANGE just as
    loudly as on a single invented figure — a range is two hallucinated numbers, not a hedge.
    """

    request = fx_conversion_intent(prompt)
    assert request is not None
    answer = render_fx_conversion(request)
    typed = _numbers_in(prompt)
    # "4217" is allowed because ISO 4217 is the standard's name, not a quantity.
    invented = _numbers_in(answer) - typed - {"4217"}
    assert not invented, f"{prompt!r} produced ungrounded number(s): {sorted(invented)}"


@pytest.mark.parametrize("prompt", CONVERSION_FAMILY)
def test_every_rateless_conversion_says_the_rate_is_missing(prompt: str) -> None:
    answer = render_fx_conversion(fx_conversion_intent(prompt)).lower()
    assert "rate" in answer
    assert "date and time" in answer


@pytest.mark.parametrize(
    "hedge", ["approximately", "roughly", "around", "about $", "≈", "circa", "ballpark"]
)
def test_no_rateless_conversion_hedges_its_way_to_a_figure(hedge: str) -> None:
    """A hedged number is still a number. The reported defect was delivered as a range."""

    for prompt in CONVERSION_FAMILY:
        answer = render_fx_conversion(fx_conversion_intent(prompt)).lower()
        assert hedge not in answer, f"{prompt!r} hedged with {hedge!r}"


#: Numeric literals `core/currency_intent.py` is allowed to contain, each with a stated structural
#: reason. Anything else — in particular anything that could be an exchange rate — fails the scan.
_ALLOWED_NUMBERS = {
    "0": "index/боundary comparisons",
    "1": "single-candidate checks, re.sub count=1",
    "2": "slice and length bounds",
    "3": "the length of an ISO 4217 code",
    "4": "the maximum word count of a definable term",
    "5": "how many ambiguity candidates to list before 'and others'",
    "30": "how far past a transfer verb its money object may sit, in characters",
    "0.01": "the two-decimal quantum used to format an amount, not a rate",
}


def test_the_module_holds_no_rate_shaped_constant() -> None:
    """The structural half: walk the AST and refuse any number that is not declared above.

    A hard-coded rate is the most attractive wrong fix in this whole area — it turns every test in
    the family green and is wrong the next day. This scan covers BOTH places one could hide: a
    bare float literal, and a number inside a string where `Decimal("18.5")` would put it.
    """

    source = pathlib.Path(ISO_4217["USD"].__class__.__module__.replace(".", "/") + ".py")
    if not source.exists():  # pragma: no cover - resolved from the import path below instead
        import core.currency_intent as module

        source = pathlib.Path(module.__file__)
    tree = ast.parse(source.read_text(encoding="utf-8"))

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant):
            continue
        value = node.value
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            if str(value) not in _ALLOWED_NUMBERS:
                offenders.append(f"line {node.lineno}: numeric literal {value!r}")
        elif isinstance(value, str):
            # A rate hidden as a string is still a rate. Only whole strings that ARE a number count;
            # prose and regex sources are not scanned for digits.
            stripped = value.strip()
            if re.fullmatch(r"\d+(?:\.\d+)?", stripped) and stripped not in _ALLOWED_NUMBERS:
                offenders.append(f"line {node.lineno}: numeric string {value!r}")
    assert not offenders, (
        "core/currency_intent.py gained a number that is not a declared structural constant. "
        "If this is an exchange rate, that is the defect QA-050-026 exists to prevent:\n  "
        + "\n  ".join(offenders)
    )


# =================================================================================================
# A rate the USER supplies is arithmetic, and arithmetic is allowed.
# =================================================================================================

@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("1000 TRY to USD at 0.031", "31"),
        ("500 SEK to USD at 0.1", "50"),
        ("200 DKK in USD using 0.15", "30"),
        ("1000 TRY to USD assume 0.02", "20"),
    ],
)
def test_a_user_supplied_rate_is_calculated_exactly(prompt: str, expected: str) -> None:
    """Rule B's last clause: given a rate, compute from it. Exactly — no rounding drift."""

    answer = _conversion_text(prompt)
    assert expected in answer.replace(",", "")
    assert "rate you gave" in answer


def test_a_supplied_rate_answer_says_it_was_not_market_checked() -> None:
    answer = _conversion_text("1000 TRY to USD at 0.031")
    assert "not checked it against a live market" in answer


def test_a_live_rate_is_used_and_attributed_when_one_is_passed_in() -> None:
    """The forward-compatible arm: when an FX source exists, the same renderer uses it.

    This is what keeps the "no live source" wording from becoming a lie the day a feed lands, and
    it is asserted now so the seam is known to work before anything depends on it.
    """

    request = fx_conversion_intent("1000 TRY to USD")
    answer = render_fx_conversion(request, live_rate=Decimal("0.025"), rate_asof="2026-08-12 09:00 UTC")
    assert "25" in answer
    assert "2026-08-12 09:00 UTC" in answer
    assert "live rate" in answer


# =================================================================================================
# Symbol ambiguity — rule C.
# =================================================================================================

@pytest.mark.parametrize(
    ("prompt", "expected_codes"),
    [
        ("500 kr worth in dollars", ("SEK", "NOK", "DKK", "ISK")),
        ("¥5000 to dollars", ("JPY", "CNY")),
        ("$300 in Singapore", ("USD", "CAD", "AUD", "SGD")),
    ],
)
def test_an_ambiguous_symbol_names_its_candidates(prompt: str, expected_codes: tuple[str, ...]) -> None:
    answer = _conversion_text(prompt)
    for code in expected_codes:
        assert code in answer, f"{prompt!r} did not name {code} as a candidate"


def test_an_ambiguous_pair_is_never_reported_as_a_specific_pair() -> None:
    """Naming "the current SEK/USD rate" for a message that said "kr" invents the missing fact.

    This one caught a real slip in the first draft of the renderer, which picked the first
    candidate of the family to build the pair label with.
    """

    answer = _conversion_text("500 kr worth in dollars")
    assert "SEK/USD" not in answer
    assert "the current rate for that pair" in answer


def test_a_pinned_pair_is_reported_as_that_pair() -> None:
    answer = _conversion_text("1000 TRY to USD")
    assert "TRY/USD" in answer


def test_singapore_pins_the_target_from_the_place_name() -> None:
    answer = _conversion_text("$300 in Singapore")
    assert "SGD" in answer and "Singapore dollar" in answer


# =================================================================================================
# NEGATIVE CONTROLS — rule D. A transaction request must stay out of this lane entirely.
# =================================================================================================

TRANSACTION_CONTROLS = (
    "send 1000 TRY to someone",
    "pay 1000 TRY",
    "buy TRY with USD",
    "transfer money",
    "send 500 USD from my wallet",
    "wire 200 EUR to my bank account",
    "withdraw 1000 TRY",
    "make a payment of 50 USD",
    "top up my wallet with 100 USD",
    "sell 1000 TRY for dollars",
)


@pytest.mark.parametrize("prompt", TRANSACTION_CONTROLS)
def test_a_real_transaction_request_is_recognised_as_one(prompt: str) -> None:
    assert currency_transaction_intent(prompt) is True


#: Transaction requests the CONVERSION PARSER would otherwise claim outright.
#:
#: This second set exists because a sabotage run found the first one proves less than it looks.
#: Deleting the transaction guard from `currency_fast_path` entirely left all 185 tests green:
#: every phrasing in `TRANSACTION_CONTROLS` is rejected further down anyway, because its target
#: ("someone", "my wallet", nothing at all) resolves to no currency. The guard was never the thing
#: keeping them out, so those tests were passing vacuously with respect to it.
#:
#: Each phrasing below names an amount and TWO resolvable currencies in a conversion frame, so the
#: parser really does claim it, and the guard is the only thing that turns it away. Removing the
#: guard fails these by name.
TRANSACTION_CONTROLS_THE_PARSER_WOULD_CLAIM = (
    "convert 1000 TRY to USD and send it to my bank account",
    "sell 1000 TRY to USD",
    "transfer 1000 TRY to USD",
    "send 1000 TRY to USD",
    "buy 1000 TRY to USD",
    "exchange 500 SEK to USD and wire it to my account",
    "withdraw 500 SEK in USD",
    "pay 300 USD in EUR to the invoice",
)


@pytest.mark.parametrize("prompt", TRANSACTION_CONTROLS)
def test_the_currency_fast_path_declines_every_transaction_request(prompt: str) -> None:
    """The lane must not answer "send 1000 TRY" with a definition of the lira."""

    from core.agent_runtime.fast_paths_currency import currency_fast_path

    assert currency_fast_path(prompt) is None


@pytest.mark.parametrize("prompt", TRANSACTION_CONTROLS_THE_PARSER_WOULD_CLAIM)
def test_the_transaction_guard_is_what_turns_away_a_claimable_transfer(prompt: str) -> None:
    """The guard is load-bearing here, and this is the test that proves it.

    Asserted in two halves so a failure says which half broke: the parser really would claim the
    turn, and the fast path refuses it anyway.
    """

    from core.agent_runtime.fast_paths_currency import currency_fast_path

    parser_would_claim = (
        fx_conversion_intent(prompt) is not None or currency_definition_intent(prompt) is not None
    )
    assert parser_would_claim, (
        f"{prompt!r} no longer reaches the parser, so it has stopped exercising the guard — "
        "replace it with a phrasing that does, or this control goes vacuous"
    )
    assert currency_transaction_intent(prompt) is True
    assert currency_fast_path(prompt) is None, (
        f"the currency lane answered {prompt!r}, which is a request to MOVE money"
    )


@pytest.mark.parametrize("prompt", DEFINITION_FAMILY + CONVERSION_FAMILY)
def test_no_definition_or_conversion_is_read_as_a_transaction(prompt: str) -> None:
    """The other direction, and the whole of defect 2: explaining money is not moving money."""

    assert currency_transaction_intent(prompt) is False, (
        f"{prompt!r} would trigger transaction safety for a question about currency"
    )


def test_talking_about_transfers_is_not_requesting_one() -> None:
    for prompt in (
        "how do i send money abroad",
        "what is the difference between a wire and a transfer",
        "explain how payment works",
    ):
        assert currency_transaction_intent(prompt) is False, prompt


# =================================================================================================
# ADVERSARIAL NEAR-MISSES — the homograph trap.
# =================================================================================================

HOMOGRAPH_NEAR_MISSES = (
    "let me try USD mode",
    "can you try again",
    "what is the best way to try a new editor?",
    "try to fix the build",
    "i will try that tomorrow",
    "please try once more",
    "we should try the other approach",
    "did you try restarting it?",
)


@pytest.mark.parametrize("prompt", HOMOGRAPH_NEAR_MISSES)
def test_the_english_verb_try_is_never_the_turkish_lira(prompt: str) -> None:
    """A lowercase "try" is a verb. Reading it as a currency code is the obvious over-fit here."""

    assert currency_definition_intent(prompt) is None, f"{prompt!r} was read as a currency question"
    assert "TRY" not in codes_named(prompt)
    assert currency_semantics_present(prompt) is False


def test_a_capitalised_code_is_evidence_but_a_shouted_verb_is_not_a_currency_question() -> None:
    """"TRY to fix the build" is caps, but it is not a "what is X" question and claims nothing."""

    assert currency_definition_intent("TRY to fix the build") is None
    assert fx_conversion_intent("TRY to fix the build") is None


@pytest.mark.parametrize(
    "prompt",
    [
        "what is JSON?",
        "what is YAML?",
        "what is a VPN?",
        "what is HTTP?",
        "what is the CPU?",
    ],
)
def test_a_non_currency_acronym_is_not_claimed(prompt: str) -> None:
    assert currency_definition_intent(prompt) is None


@pytest.mark.parametrize("prompt", ["what is ALL?", "what is TOP?", "what is BOB?", "what is GEL?"])
def test_other_homograph_codes_answer_but_name_the_everyday_reading(prompt: str) -> None:
    """The homograph set is bigger than TRY, and every member gets the same treatment."""

    answer = _definition_text(prompt)
    assert "ISO 4217" in answer
    assert "Read as a word" in answer, "the everyday reading was not disclosed"


def test_asking_by_name_does_not_get_the_homograph_warning() -> None:
    """"Turkish lira code?" already told us the domain — warning about the verb is noise."""

    assert "Read as a word" not in _definition_text("Turkish lira code?")
    assert "Read as a word" not in _definition_text("i ment money TRY")


# =================================================================================================
# Weight — rule "simple USD still no conductor/deep".
# =================================================================================================

@pytest.mark.parametrize("prompt", ["what is USD?", "ok what is TRY?", "1000 TRY to USD?"])
def test_a_currency_turn_is_answered_locally_with_no_model_call(prompt: str) -> None:
    """The fast path answers from reference data, so no provider is reached at all."""

    from core.agent_runtime.fast_paths_currency import currency_fast_path

    claimed = currency_fast_path(prompt)
    assert claimed is not None, f"{prompt!r} was not claimed by the currency fast path"
    assert claimed["grounded"] in {"local_reference_data", "no_rate_declined"}


@pytest.mark.parametrize("prompt", ["what is USD?", "ok what is TRY?", "1000 TRY to USD?"])
def test_a_currency_turn_does_not_buy_a_heavy_lane(prompt: str) -> None:
    """Even if the fast path were bypassed, none of these may reach the deep/unbounded lane."""

    import core.agent_runtime
    from core.local_inference_autopilot import _resolve_lane
    from core.memory_first_router import resolve_fallback_budget_seconds
    from core.reasoning_engine import explicit_planner_style_requested
    from core.task_router import classify, model_execution_profile

    classification = classify(prompt, {"chat_surface": True})
    profile = model_execution_profile(
        classification["task_class"],
        chat_surface=True,
        planner_style_requested=explicit_planner_style_requested(prompt),
    )
    lane = _resolve_lane(
        user_text=prompt,
        task_kind=str(profile["task_kind"]),
        output_mode=str(profile["output_mode"]),
        source_context={},
        local_available=True,
        has_tiny_lane=True,
        has_deep_lane=True,
    )
    budget = resolve_fallback_budget_seconds(
        lane, forced_cpu=False, no_usable_gpu=False, output_mode=str(profile["output_mode"])
    )
    assert lane != "deep", f"{prompt!r} reached the heavyweight lane"
    assert budget is not None, f"{prompt!r} bought an unbounded fallback loop"


# =================================================================================================
# Reference-data integrity.
# =================================================================================================

def test_every_currency_fact_is_self_consistent() -> None:
    for code, fact in ISO_4217.items():
        assert fact.code == code
        assert len(code) == 3 and code.isupper()
        assert fact.name and fact.region
        if fact.homograph:
            assert fact.homograph_sense, f"{code} is a homograph with no everyday reading stated"


def test_the_kr_family_all_share_the_kr_symbol() -> None:
    for code in ("SEK", "NOK", "DKK", "ISK"):
        assert ISO_4217[code].symbol == "kr"
    assert ISO_4217["CZK"].symbol == "Kč"
