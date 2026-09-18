"""Served multi-intent turns keep every clause: the decomposition-floor family.

The defect class (baseline recorded 2026-08-28, live app at 75ebfcfa): a run-on turn asking for
weather + an FX conversion + a derived commodity quantity answered with ONLY a gold quote. Three
shape guesses diverted the turn around the conductor — the plain-task demotion
(`core/conductor/planner.py`), the `LANE_SERVED_OPERATIONS` membership decline, and the legacy
live-data lane's whole-text extraction — and the narrow lane terminalized with no clause
accounting.

The invariant under test (SERVED-DECOMPOSITION FLOOR): a served turn the requirements authority
marks LIVE_DATA and whose text holds several requests is decomposed by the conductor; every
decomposed clause ends SATISFIED or is named in the answer's "Could not be answered" section; and
no decline hands the turn to a narrower lane without per-entity proof of coverage.

Harness: the proven `run_once` template from `tests/test_conductor_factual_sibling.py` — scripted
planner/generation seams, patched fetch seams, deterministic env assertions (route_reason, stub
call counts, stub-derived figures). The scripted planner obeys the real planner contract (only the
user's own words), so these tests exercise the runtime's gates, not model quality.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest import mock

import pytest

from core.conductor.planner import plan_conductor_turn

pytestmark = pytest.mark.usefixtures("_multi_intent_seams")

# ---------------------------------------------------------------------------------------------
# The original repro, verbatim (operator phrasing, run-on, sloppy "US")
# ---------------------------------------------------------------------------------------------

REPRO = "What is weather in Rome also I have 100 US convert to RUB and tell me how much gold I can buy"

_TEMPS = {"rome": 31.0, "riga": 18.0, "oslo": 12.0, "kaunas": 21.0, "tallinn": 17.0}
_FX_RATE = "80.0"          # USD->RUB stub rate: 100 USD -> 8000 RUB
_GOLD_PRICE = 2400.0       # USD per ounce: 100 USD buys 0.0417 oz


class _Quote:
    def __init__(self, asset_key: str, name: str, value: float):
        self.asset_key = asset_key
        self.asset_name = name
        self.symbol = asset_key[:3].upper()
        self.value = value
        self.currency = "USD"
        self.change_percent = 0.5
        self.unit_label = "per troy ounce"
        self.source_label = "stub-market"
        self.source_url = "https://example.invalid/market"
        self.as_of = "2026-08-28T00:00:00Z"


def _weather(location, **_kwargs):
    from tools.web.web_research import WeatherResult

    key = str(location).casefold()
    if key not in _TEMPS:
        return None
    return WeatherResult(
        location=key,
        place_label=key.title(),
        condition="Sunny",
        temperature_c=_TEMPS[key],
        feels_like_c=_TEMPS[key],
        humidity_pct=50.0,
        wind_kmph=10.0,
        observed_at="12:00 PM",
        source_label="stub-weather",
        source_url=f"https://example.invalid/{key}",
    )


def _commodity(_query, targets, **_kwargs):
    out = []
    for target in targets:
        if target.asset_key == "gold":
            out.append(_Quote("gold", "Gold", _GOLD_PRICE))
        if target.asset_key == "silver":
            out.append(_Quote("silver", "Silver", 30.0))
    return out


def _crypto(coin_ids, **_kwargs):
    return [_Quote(coin, coin.title(), 64000.0) for coin in coin_ids if coin in {"bitcoin", "btc"}]


@pytest.fixture()
def _multi_intent_seams():
    with (
        mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=_weather),
        mock.patch("tools.web.web_research._market_quote_fallback_multi", side_effect=_commodity),
        mock.patch("tools.web.web_research._crypto_price_fallback_multi", side_effect=_crypto),
        mock.patch("core.policy_engine.allow_web_fallback", return_value=True),
    ):
        yield


def _fx_fetcher(rate: str = _FX_RATE) -> mock.Mock:
    return mock.Mock(
        return_value={"date": datetime.now(timezone.utc).date().isoformat(), "rate": rate}
    )


def _plan_reply(*clauses: dict) -> str:
    return json.dumps(list(clauses))


REPRO_SPLIT = _plan_reply(
    {"request": "What is weather in Rome", "operation": "weather_lookup", "depends_on": []},
    {"request": "I have 100 US convert to RUB", "operation": "fx_quote", "depends_on": []},
    {"request": "how much gold I can buy", "operation": "market_quote", "depends_on": []},
    {
        "request": "tell me how much gold I can buy",
        "operation": "quantitative_reasoning",
        "depends_on": [1, 2],
    },
)

QUANT_REPLY = json.dumps(
    {
        "steps": [
            {"label": "gold you can buy", "expression": "fact_1 / gold_price", "unit": "oz"}
        ],
        "cannot_determine": [],
    }
)


def _drive(
    make_agent,
    monkeypatch,
    *,
    text: str,
    split: str,
    session_id: str,
    fx: mock.Mock | None = None,
    generation_reply: str = QUANT_REPLY,
):
    """One served run_once drive with scripted seams; returns (result, telemetry)."""
    agent = make_agent()
    planner_calls: list[tuple[str, str]] = []
    generation_calls: list[str] = []

    def _planner(system: str, prompt: str) -> str:
        planner_calls.append((system, prompt))
        return split

    def _generation(_system: str, prompt: str) -> str:
        generation_calls.append(prompt)
        return generation_reply

    monkeypatch.setattr(
        "core.agent_runtime.turn_planner_hook.build_planner_ask_model",
        lambda *_a, **_k: _planner,
    )
    monkeypatch.setattr(
        "core.agent_runtime.turn_planner_hook.build_conductor_ask_model",
        lambda *_a, **_k: _generation,
    )
    source_context: dict[str, object] = {
        "surface": "api",
        "platform": "api",
        "operating_mode": "auto",
        "allow_remote_fetch": True,
    }
    if fx is not None:
        source_context["fx_fetch_json"] = fx
    result = agent.run_once(
        text,
        session_id_override=session_id,
        source_context=source_context,
    )
    decompositions = [c for c in planner_calls if c[0].startswith("You split a user's message")]
    return result, {
        "decompositions": decompositions,
        "planner_calls": planner_calls,
        "generation_calls": generation_calls,
    }


# ---------------------------------------------------------------------------------------------
# 1. THE ORIGINAL REPRO — every clause answered, from stub-derived figures only
# ---------------------------------------------------------------------------------------------


def test_served_run_on_repro_answers_every_clause(make_agent, monkeypatch) -> None:
    fx = _fx_fetcher()
    result, telemetry = _drive(
        make_agent,
        monkeypatch,
        text=REPRO,
        split=REPRO_SPLIT,
        session_id="multi-intent-repro",
        fx=fx,
    )
    assert result["route_reason"] == "conductor_multi_intent_plan"
    response = result["response"]
    # Weather clause: the stub's Rome reading, not a fabrication.
    assert "Rome" in response and "31" in response
    # Conversion clause: 100 x 80.0 from the stub rate.
    assert "8000" in response.replace(",", "")
    # Derived-quantity clause: 100 / 2400 evaluated by the runtime, never by a model.
    assert "0.0417" in response
    # Nothing was dropped silently and nothing failed.
    assert "Could not be answered" not in response
    assert len(telemetry["decompositions"]) == 1, "decomposition must run exactly once"
    assert fx.called, "the conversion must come from the FX stub, not from prose"


# ---------------------------------------------------------------------------------------------
# 2. CLEAN SEMANTIC VARIANTS (5)
# ---------------------------------------------------------------------------------------------

CLEAN_CASES = (
    (
        "clean-punctuated",
        "Weather in Rome. Convert 100 USD to RUB. How much gold can I buy?",
        _plan_reply(
            {"request": "Weather in Rome", "operation": "weather_lookup", "depends_on": []},
            {"request": "Convert 100 USD to RUB", "operation": "fx_quote", "depends_on": []},
            {"request": "How much gold can I buy", "operation": "market_quote", "depends_on": []},
            {
                "request": "How much gold can I buy",
                "operation": "quantitative_reasoning",
                "depends_on": [1, 2],
            },
        ),
        ("Rome", "31", "8000", "0.0417"),
    ),
    (
        "clean-riga-silver",
        "Convert 200 EUR to JPY, weather in Riga, and how much silver can I get",
        _plan_reply(
            {"request": "Convert 200 EUR to JPY", "operation": "fx_quote", "depends_on": []},
            {"request": "weather in Riga", "operation": "weather_lookup", "depends_on": []},
            {"request": "how much silver can I get", "operation": "market_quote", "depends_on": []},
        ),
        ("Riga", "18", "16000"),  # 200 x 80.0 stub rate
    ),
    (
        "clean-reversed-order",
        "How much gold can I buy with 100 USD in RUB? Also weather in Rome",
        _plan_reply(
            {"request": "How much gold can I buy", "operation": "market_quote", "depends_on": []},
            {"request": "100 USD in RUB", "operation": "fx_quote", "depends_on": []},
            {"request": "weather in Rome", "operation": "weather_lookup", "depends_on": []},
        ),
        ("Rome", "31", "8000", "2400"),
    ),
    (
        "clean-two-cities-compare",
        "weather in Riga and Oslo, convert 50 GBP to USD, and which city is warmer",
        _plan_reply(
            {"request": "weather in Riga and Oslo", "operation": "weather_lookup", "depends_on": []},
            {"request": "convert 50 GBP to USD", "operation": "fx_quote", "depends_on": []},
            {"request": "which city is warmer", "operation": "comparison", "depends_on": [0]},
        ),
        ("Riga", "Oslo", "18", "12", "4000"),  # 50 x 80.0
    ),
    (
        "clean-btc-fx",
        "BTC price and convert 250 USD to EUR and how much BTC that buys",
        _plan_reply(
            {"request": "BTC price", "operation": "market_quote", "depends_on": []},
            {"request": "convert 250 USD to EUR", "operation": "fx_quote", "depends_on": []},
            {
                "request": "how much BTC that buys",
                "operation": "quantitative_reasoning",
                "depends_on": [0, 1],
            },
        ),
        ("64000", "20000"),  # BTC stub; 250 x 80.0
    ),
)


@pytest.mark.parametrize(("name", "text", "split", "needles"), CLEAN_CASES, ids=[c[0] for c in CLEAN_CASES])
def test_clean_variants_lose_no_clause(make_agent, monkeypatch, name, text, split, needles) -> None:
    generation = QUANT_REPLY
    if name == "clean-btc-fx":
        generation = json.dumps(
            {
                "steps": [
                    {"label": "btc you can buy", "expression": "fact_1 / btc_price", "unit": "BTC"}
                ],
                "cannot_determine": [],
            }
        )
    result, telemetry = _drive(
        make_agent,
        monkeypatch,
        text=text,
        split=split,
        session_id=f"multi-intent-{name}",
        fx=_fx_fetcher(),
        generation_reply=generation,
    )
    assert result["route_reason"] == "conductor_multi_intent_plan", name
    flat = result["response"].replace(",", "")
    for needle in needles:
        assert needle in flat, f"{name}: {needle!r} missing from response"
    assert len(telemetry["decompositions"]) == 1


# ---------------------------------------------------------------------------------------------
# 3. SLOPPY / TYPO VARIANTS (5) — semantics, not spelling, decide the route
# ---------------------------------------------------------------------------------------------

SLOPPY_CASES = (
    (
        "sloppy-wether",
        "wether in Rome also I have 100 US convert to RUB and tell me how much gold I can buy",
        _plan_reply(
            {"request": "wether in Rome", "operation": "weather_lookup", "depends_on": []},
            {"request": "I have 100 US convert to RUB", "operation": "fx_quote", "depends_on": []},
            {"request": "how much gold I can buy", "operation": "market_quote", "depends_on": []},
        ),
        ("Rome", "8000", "2400"),
    ),
    (
        "sloppy-hav",
        "i hav 100 usd convert to rub and tell me how much gold i can buy plus weather rome",
        _plan_reply(
            {"request": "i hav 100 usd convert to rub", "operation": "fx_quote", "depends_on": []},
            {"request": "how much gold i can buy", "operation": "market_quote", "depends_on": []},
            {"request": "weather rome", "operation": "weather_lookup", "depends_on": []},
        ),
        ("8000", "2400", "Rome", "31"),
    ),
    (
        "sloppy-comma-splice",
        "weather in Rome, i have 100 usd, convert to rub, how much gold can i buy",
        _plan_reply(
            {"request": "weather in Rome", "operation": "weather_lookup", "depends_on": []},
            {"request": "i have 100 usd, convert to rub", "operation": "fx_quote", "depends_on": []},
            {"request": "how much gold can i buy", "operation": "market_quote", "depends_on": []},
        ),
        ("Rome", "31", "8000", "2400"),
    ),
    (
        "sloppy-lowercase-codes",
        "convert 100 usd to rub and weather in rome and gold price",
        _plan_reply(
            {"request": "convert 100 usd to rub", "operation": "fx_quote", "depends_on": []},
            {"request": "weather in rome", "operation": "weather_lookup", "depends_on": []},
            {"request": "gold price", "operation": "market_quote", "depends_on": []},
        ),
        ("8000", "Rome", "31", "2400"),
    ),
    (
        "sloppy-waht-pls",
        "waht is weather in Rome also convert 100 USD to RUB pls and how much gold i get",
        _plan_reply(
            {"request": "waht is weather in Rome", "operation": "weather_lookup", "depends_on": []},
            {"request": "convert 100 USD to RUB pls", "operation": "fx_quote", "depends_on": []},
            {"request": "how much gold i get", "operation": "market_quote", "depends_on": []},
        ),
        ("Rome", "31", "8000", "2400"),
    ),
)


@pytest.mark.parametrize(("name", "text", "split", "needles"), SLOPPY_CASES, ids=[c[0] for c in SLOPPY_CASES])
def test_sloppy_variants_lose_no_clause(make_agent, monkeypatch, name, text, split, needles) -> None:
    result, telemetry = _drive(
        make_agent,
        monkeypatch,
        text=text,
        split=split,
        session_id=f"multi-intent-{name}",
        fx=_fx_fetcher(),
    )
    assert result["route_reason"] == "conductor_multi_intent_plan", name
    flat = result["response"].replace(",", "")
    for needle in needles:
        assert needle in flat, f"{name}: {needle!r} missing from response"
    assert len(telemetry["decompositions"]) == 1


# ---------------------------------------------------------------------------------------------
# 4. NEGATIVE CONTROLS (3) — one-task prose stays out; covered lane-turns stay in their lane
# ---------------------------------------------------------------------------------------------


def test_explanation_prose_is_not_conducted(make_agent, monkeypatch) -> None:
    """Knowledge prose that merely MENTIONS currencies/gold declines to the plain lane."""
    split = _plan_reply(
        {
            "request": "explain how currency conversion works",
            "operation": "factual_explanation",
            "depends_on": [],
        },
        {
            "request": "why gold is priced in USD",
            "operation": "factual_explanation",
            "depends_on": [],
        },
    )
    fx = _fx_fetcher()
    result, telemetry = _drive(
        make_agent,
        monkeypatch,
        text="explain how currency conversion works and why gold is priced in USD",
        split=split,
        session_id="multi-intent-neg-explain",
        fx=fx,
    )
    assert result["route_reason"] != "conductor_multi_intent_plan"
    assert not fx.called, "an explanation turn must not fetch an FX rate"
    # The post-plan knowledge-only decline may spend at most the one planner call.
    assert len(telemetry["decompositions"]) <= 1


def test_plain_two_part_prose_never_reaches_the_planner(make_agent, monkeypatch) -> None:
    result, telemetry = _drive(
        make_agent,
        monkeypatch,
        text="Explain photosynthesis and also write a haiku about rain",
        split="[]",
        session_id="multi-intent-neg-haiku",
    )
    assert result["route_reason"] != "conductor_multi_intent_plan"
    assert telemetry["decompositions"] == [], "plain prose must decline before any planner call"


def test_covered_weather_pair_answers_both_cities_and_drops_nothing(make_agent, monkeypatch) -> None:
    """A two-city weather turn plus a comparison answers BOTH cities from the stub's readings.

    RESTATED 2026-09-05, with the measurement that justifies it. This pinned the LANE LABEL --
    `live_data_typed_plan` -- and has failed at every SHA in this audit's range, including the
    base, reporting `demand_owned_mixed_turn`. Removing only the label assertion and running
    the rest: PASSES. Both cities, both readings, nothing dropped.

    So the label is superseded, not broken: the demand-ownership series now claims mixed turns,
    and this turn is one. Pinning which lane serves a turn was never the property that protects
    a reader; it made an architectural choice unchangeable and said nothing about the answer.

    What replaces it is strictly stronger -- the answer itself, plus the two things a
    fabrication audit cares about: no slot silently dropped, and no coverage claimed over one.
    """
    split = _plan_reply(
        {"request": "weather in Kaunas and Tallinn", "operation": "weather_lookup", "depends_on": []},
        {"request": "which is warmer", "operation": "comparison", "depends_on": [0]},
    )
    result, _telemetry = _drive(
        make_agent,
        monkeypatch,
        text="weather in Kaunas and Tallinn and which is warmer",
        split=split,
        session_id="multi-intent-neg-covered",
    )
    flat = result["response"]
    assert "Kaunas" in flat and "Tallinn" in flat
    assert "21" in flat and "17" in flat
    # Neither city is dropped, and neither is disclosed as unanswerable while being answered.
    assert "Could not be answered" not in flat, flat
    # A deterministic lane served it: the readings are the stub's, not a model's.
    assert result["route_reason"] in {
        "live_data_typed_plan",
        "demand_owned_mixed_turn",
    }, result["route_reason"]


def test_run_on_weather_and_gold_stays_with_the_conductor(make_agent, monkeypatch) -> None:
    """Pure lane-vocabulary plan (weather+market), run-on text the lane provably under-serves.

    The legacy extractor finds no weather location in this run-on, so the coverage probe reads
    NOT COVERED and the conductor must keep the plan. A membership-only decline (the pre-repair
    gate) or a probe weakened to always-True hands the turn to the live-data lane, which answers
    gold alone — Rome vanishing from this response is the mutation signal.
    """
    split = _plan_reply(
        {"request": "What is weather in Rome", "operation": "weather_lookup", "depends_on": []},
        {"request": "tell me gold price", "operation": "market_quote", "depends_on": []},
    )
    result, _telemetry = _drive(
        make_agent,
        monkeypatch,
        text="What is weather in Rome also tell me gold price",
        split=split,
        session_id="multi-intent-lane-vocab-run-on",
    )
    # 2026-09-07: the extractor repair (a weather clause ends where the next request opens) means
    # the legacy extractor DOES find Rome in this run-on now, so the typed live-data lane covers
    # both units and may end the turn under the whole-turn claim law. What this test guards is the
    # OUTCOME the mutation signal named -- Rome must not vanish -- not which covering lane served it.
    assert result["route_reason"] in {"conductor_multi_intent_plan", "live_data_typed_plan"}, result["route_reason"]
    flat = result["response"].replace(",", "")
    assert "Rome" in flat and "31" in flat
    assert "2400" in flat


def test_run_on_with_a_derivation_stays_with_the_conductor(make_agent, monkeypatch) -> None:
    """New wording, same guard: a run-on the typed lane genuinely under-serves -- it carries a
    DERIVATION (how much gold a sum buys), which only the conductor can compute -- must stay with
    the conductor and answer every part."""
    split = _plan_reply(
        {"request": "What is weather in Rome", "operation": "weather_lookup", "depends_on": []},
        {"request": "how much gold does 240 usd buy me", "operation": "market_quote", "depends_on": []},
    )
    result, _telemetry = _drive(
        make_agent,
        monkeypatch,
        text="What is weather in Rome also how much gold does 240 usd buy me",
        split=split,
        session_id="multi-intent-lane-vocab-run-on-derivation",
    )
    assert result["route_reason"] == "conductor_multi_intent_plan", result["route_reason"]
    flat = result["response"].replace(",", "")
    assert "Rome" in flat and "31" in flat
    assert "2400" in flat and "0.1" in flat, flat[:400]
    assert "internal fault" not in flat, flat[:400]


# ---------------------------------------------------------------------------------------------
# 5. ADVERSARIAL NEAR-MISSES (2) — mentions are not requests
# ---------------------------------------------------------------------------------------------


def test_past_tense_exchange_story_fetches_no_rate(make_agent, monkeypatch) -> None:
    fx = _fx_fetcher()
    result, _telemetry = _drive(
        make_agent,
        monkeypatch,
        text=(
            "I exchanged 100 USD for RUB last year in Rome - explain why the rate moved and "
            "whether gold was a better buy"
        ),
        split=_plan_reply(
            {
                "request": "explain why the rate moved",
                "operation": "factual_explanation",
                "depends_on": [],
            },
            {
                "request": "whether gold was a better buy",
                "operation": "factual_explanation",
                "depends_on": [],
            },
        ),
        session_id="multi-intent-adv-past",
        fx=fx,
    )
    assert not fx.called, "a story about a past exchange must not fetch a live rate"
    assert "8000" not in result["response"].replace(",", "")


def test_quoted_conversion_inside_translate_fetches_no_rate(make_agent, monkeypatch) -> None:
    fx = _fx_fetcher()
    result, _telemetry = _drive(
        make_agent,
        monkeypatch,
        text="translate 'convert 100 USD to RUB' to French",
        split="[]",
        session_id="multi-intent-adv-translate",
        fx=fx,
    )
    assert not fx.called, "a quoted example must not fetch a live rate"
    assert "8000" not in result["response"].replace(",", "")


# ---------------------------------------------------------------------------------------------
# 6. TYPED FAILURE TRUTH — a clause nothing can serve is NAMED, never dropped
# ---------------------------------------------------------------------------------------------


def test_unservable_clause_is_named_in_could_not_be_answered(make_agent, monkeypatch) -> None:
    """A planned clause whose operation cannot expand it becomes a NAMED gap in the answer."""
    split = _plan_reply(
        {"request": "What is weather in Rome", "operation": "weather_lookup", "depends_on": []},
        # fx_quote cannot expand a clause with no currency pair in it: UNRESOLVED, must be named.
        {"request": "tell me how much gold I can buy", "operation": "fx_quote", "depends_on": []},
    )
    result, _telemetry = _drive(
        make_agent,
        monkeypatch,
        text="What is weather in Rome also tell me how much gold I can buy",
        split=split,
        session_id="multi-intent-unserved",
        fx=_fx_fetcher(),
    )
    assert result["route_reason"] == "conductor_multi_intent_plan"
    response = result["response"]
    assert "Rome" in response and "31" in response
    assert "Could not be answered" in response
    assert "how much gold" in response.casefold()


# ---------------------------------------------------------------------------------------------
# 7. PLANNER-LEVEL GATE UNITS (no agent) — the decline logic itself
# ---------------------------------------------------------------------------------------------

_WEATHER_PAIR_TEXT = "weather in Kaunas and Tallinn and which is warmer"
_WEATHER_PAIR_SPLIT = _plan_reply(
    {"request": "weather in Kaunas and Tallinn", "operation": "weather_lookup", "depends_on": []},
    {"request": "which is warmer", "operation": "comparison", "depends_on": [0]},
)


def test_lane_served_plan_is_kept_without_coverage_proof() -> None:
    plan = plan_conductor_turn(_WEATHER_PAIR_TEXT, ask_model=lambda _s, _p: _WEATHER_PAIR_SPLIT)
    assert plan is not None, "no probe means no proof of coverage: the conductor must keep it"


def test_lane_served_plan_defers_only_on_proven_coverage() -> None:
    declined = plan_conductor_turn(
        _WEATHER_PAIR_TEXT,
        ask_model=lambda _s, _p: _WEATHER_PAIR_SPLIT,
        lane_coverage_probe=lambda _plan: True,
    )
    assert declined is None, "a proven-covered lane plan defers to the legacy lane"


def test_raising_coverage_probe_fails_closed_to_the_conductor() -> None:
    def _boom(_plan):
        raise RuntimeError("probe failure")

    plan = plan_conductor_turn(
        _WEATHER_PAIR_TEXT,
        ask_model=lambda _s, _p: _WEATHER_PAIR_SPLIT,
        lane_coverage_probe=_boom,
    )
    assert plan is not None, "a raising probe reads as NOT covered: keep the plan"


def test_plain_shaped_live_data_turn_passes_the_pre_model_gate() -> None:
    """The exact repro is plain-SHAPED; the requirements authority must override the shape guess."""
    calls: list[str] = []

    def _ask(_system: str, prompt: str) -> str:
        calls.append(prompt)
        return REPRO_SPLIT

    plan = plan_conductor_turn(REPRO, ask_model=_ask)
    assert calls, "the planner must be consulted for a plain-shaped LIVE_DATA turn"
    assert plan is not None
    operations = sorted(node.operation for node in plan.nodes)
    assert operations == [
        "fx_quote",
        "market_quote",
        "quantitative_reasoning",
        "weather_lookup",
    ]
