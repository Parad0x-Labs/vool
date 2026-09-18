"""Frozen-gauntlet and semantic-family coverage for user-supplied travel FX arithmetic."""

from __future__ import annotations

from unittest import mock

import pytest

from core.agent_runtime.answer_coverage import FAMILY_CURRENCY, coverage_for
from core.agent_runtime.fast_paths_currency import currency_fast_path
from core.currency_travel_spend import HoldingComparisonRequest, TravelSpendRequest, travel_spend_intent

FROZEN_UNRESOLVED_QUALIFIED_JURISDICTIONS = (
    "I have 10,000 units of local currency in Manchester, UK. I travel to Manchester, New "
    "Hampshire, and buy a coffee for 5 units of local currency. Are these currencies the same? "
    "Name both currencies and calculate the deficit or remainder.",
    "I have 1,000 units of local currency in Sydney, Australia, and buy a coffee for 5 units of "
    "local currency in Sydney, Nova Scotia. Are the two currencies the same? Name the currencies "
    "and calculate the remainder in CAD.",
)

CASES = (
    # Set 1
    (
        "I have 3,000 units of local currency in San Juan, Puerto Rico. I buy a coffee for 1,500 "
        "units of local currency in San Jose, Costa Rica. Assuming 1 USD = 500 CRC, how many units "
        "of Costa Rica's currency do I have left over? Explicitly name the currencies and show your math.",
        ("United States dollar (USD)", "Costa Rican colón (CRC)", "1,498,500 CRC left over"),
    ),
    (
        "I have 1,000,000 units of local currency in Lagos, Nigeria. I want to buy a used car that "
        "costs 5,000 units of local currency in London, UK. If 1 GBP = 2000 NGN, what is my remaining "
        "balance in GBP after trying to buy the car? Explicitly name the currencies and calculate the deficit.",
        ("Nigerian naira (NGN)", "pound sterling (GBP)", "4,500 GBP short"),
    ),
    (
        "I have 10,000 units of local currency in Santiago, Chile. I travel to Santiago de Compostela, "
        "Spain, and buy a meal for 50 units of local currency. If 1 EUR = 1000 CLP, how many units of "
        "Spain's currency do I have left over? Name the currencies and show math.",
        ("Chilean peso (CLP)", "euro (EUR)", "40 EUR short"),
    ),
    (
        "I have 500 units of currency in Paris, Texas and want to buy a guitar for 400 units of currency "
        "in Paris, France. If 1 EUR = 1.10 USD, how many units of France's currency will I have left over "
        "if I exchange all my money first?",
        ("United States dollar (USD)", "euro (EUR)", "54.55 EUR left over"),
    ),
    (
        "I have 1,000,000 units of local currency in Caracas, Venezuela (VES). I want to buy a coffee "
        "in Tokyo, Japan (JPY) that costs 500 JPY. If 1 USD = 36 VES and 1 USD = 150 JPY, do I have "
        "enough money? Show the math.",
        ("Venezuelan bolívar (VES)", "Japanese yen (JPY)", "4,166,166.67 JPY left over"),
    ),
    (
        "If I have 5,000 units of local currency in Bogota, Colombia and buy a 5,000 unit item in "
        "Bogota, New Jersey, do I break even? Assume 1 USD = 4000 COP. Name the currencies and show the math.",
        ("Colombian peso (COP)", "United States dollar (USD)", "4,998.75 USD short"),
    ),
    (
        "I have 5,000 kr in Copenhagen and 5,000 kr in Stockholm. Which one is worth more in USD "
        "assuming 1 USD = 6.8 DKK and 1 USD = 10.5 SEK? Show the math.",
        ("Danish krone (DKK)", "Swedish krona (SEK)", "735.29 USD"),
    ),
    # Set 2
    (
        "I have 4,000,000 units of local currency in Hanoi, Vietnam. I want to buy a phone for 30,000 "
        "units of local currency in Taipei, Taiwan. If 1 TWD = 800 VND, how much of Taiwan's currency "
        "will I have left over after exchanging and buying? Show the math.",
        ("Vietnamese dong (VND)", "New Taiwan dollar (TWD)", "25,000 TWD short"),
    ),
    (
        "I have 500,000 units of local currency in Jakarta, Indonesia. I want to buy a bicycle for "
        "6,000 units of local currency in Tokyo, Japan. If 1 JPY = 100 IDR, do I have enough money "
        "to buy it? Explicitly name the currencies and show the deficit or remainder.",
        ("Indonesian rupiah (IDR)", "Japanese yen (JPY)", "1,000 JPY short"),
    ),
    (
        "I have 1,000 units of currency in London, Ontario. I travel to London, UK and buy a book for "
        "20 units of currency. Assuming 1 GBP = 1.70 CAD, how much of the UK's currency do I have "
        "left? Name the currencies and show the math.",
        ("Canadian dollar (CAD)", "pound sterling (GBP)", "568.24 GBP left over"),
    ),
    (
        "I have 10,000 units of currency in Sydney, Australia. I want to buy a watch for 5,000 units "
        "of currency in Vienna, Austria. If 1 EUR = 1.65 AUD, how much of Austria's currency do I have "
        "left over? Name both currencies.",
        ("Australian dollar (AUD)", "euro (EUR)", "1,060.61 EUR left over"),
    ),
    (
        "I have 5,000 units of local currency in Seoul, South Korea. I want to buy a snack for 20 units "
        "of local currency in Beijing, China. Assuming 1 CNY = 180 KRW, how much CNY is left over? "
        "Name the currencies and show the math.",
        ("South Korean won (KRW)", "Chinese yuan renminbi (CNY)", "7.78 CNY left over"),
    ),
    (
        "I have 2,000 units of local currency in Memphis, Tennessee and buy a souvenir for 50 units "
        "of local currency in Memphis, Egypt. Are the two currencies the same? Name them both.",
        ("United States dollar (USD)", "Egyptian pound (EGP)", "currencies are not the same"),
    ),
    (
        "I have 1,000 CHF in Zurich, Switzerland. I want to buy a car in Berlin, Germany that costs "
        "50,000 EUR. If 1 CHF = 1.05 EUR, how many EUR am I short by? Show the math.",
        ("Swiss franc (CHF)", "euro (EUR)", "48,950 EUR short"),
    ),
    # Set 3
    (
        "I have 1,000,000 units of local currency in Ho Chi Minh City, Vietnam. I want to buy a tablet "
        "that costs 15,000 units of local currency in Manila, Philippines. If 1 PHP = 450 VND, how "
        "many units of the Philippines' currency will I have left over? Explicitly name both currencies "
        "and show the math.",
        ("Vietnamese dong (VND)", "Philippine peso (PHP)", "12,777.78 PHP short"),
    ),
    (
        "I have 50,000 units of local currency in Naples, Italy. I want to buy a jet ski for 10,000 "
        "units of local currency in Naples, Florida. If 1 EUR = 1.10 USD, how many units of Florida's "
        "currency do I have left? Name both currencies explicitly.",
        ("euro (EUR)", "United States dollar (USD)", "45,000 USD left over"),
    ),
    (
        "I have 30,000 units of local currency in Cairo, Egypt. I travel to Cape Town, South Africa, "
        "and buy a jacket for 1,500 units of local currency. Assuming 1 ZAR = 2.50 EGP, how much of "
        "South Africa's currency do I have left? Name the currencies and show the math.",
        ("Egyptian pound (EGP)", "South African rand (ZAR)", "10,500 ZAR left over"),
    ),
    (
        "I have 5,000 units of currency in Lima, Peru. I want to buy alpaca wool for 10,000 units of "
        "currency in La Paz, Bolivia. If 1 PEN = 1.8 BOB, do I have enough money? Name the currencies "
        "and calculate the deficit or remainder.",
        ("Peruvian sol (PEN)", "Bolivian boliviano (BOB)", "1,000 BOB short"),
    ),
    (
        "I have 10,000 units of local currency in Reykjavik, Iceland. I want to buy a meal for 500 "
        "units of local currency in Stockholm, Sweden. If 1 SEK = 13 ISK, how many units of Sweden's "
        "currency will I have left over? Show the math.",
        ("Icelandic króna (ISK)", "Swedish krona (SEK)", "269.23 SEK left over"),
    ),
    (
        "I have 1,000 units of local currency in Washington, D.C. and buy a coffee for 5 units of "
        "local currency in Washington State. Are the two currencies the same? Name the currency "
        "and calculate the remainder.",
        ("United States dollar (USD)", "currencies are the same", "995 USD left over"),
    ),
    (
        "I have 500 BGN in Sofia, Bulgaria. I want to buy a watch in Bucharest, Romania for 1,000 RON. "
        "If 1 BGN = 2.5 RON, how many RON will I have left over? Show the math.",
        ("Bulgarian lev (BGN)", "Romanian leu (RON)", "250 RON left over"),
    ),
)


@pytest.mark.parametrize(("prompt", "expected"), CASES)
def test_all_three_frozen_sets_use_one_local_decimal_family(prompt: str, expected: tuple[str, ...]) -> None:
    claimed = currency_fast_path(prompt)
    assert claimed is not None
    assert claimed["kind"] == "travel_spend"
    assert claimed["grounded"] == "user_supplied_rate_or_local_identity"
    assert claimed["retrieval_allowed"] is False
    for phrase in expected:
        assert phrase in claimed["response"]
    assert "retriev" in claimed["response"].lower()
    assert "×" in claimed["response"] or "No exchange" in claimed["response"] or "identity" in claimed["response"]


def test_two_rate_cross_conversion_is_a_real_graph_not_a_special_case() -> None:
    prompt = (
        "I have 720 units of local currency in Caracas, Venezuela. I want to buy a ticket in Tokyo, "
        "Japan that costs 2,500 JPY. If 1 USD = 36 VES and 2 USD = 300 JPY, do I have enough? Show math."
    )
    claimed = currency_fast_path(prompt)
    assert claimed is not None
    assert "720 VES × (1 USD / 36 VES) = 20 USD" in claimed["response"]
    assert "20 USD × (300 JPY / 2 USD) = 3,000 JPY" in claimed["response"]
    assert "500 JPY left over" in claimed["response"]


def test_lowercase_rate_codes_and_flexible_spacing_are_still_user_grounded() -> None:
    prompt = (
        "I have 360 units of local currency in Caracas, Venezuela. I want to buy a ticket in Tokyo, "
        "Japan that costs 1,000 JPY. If 1 usd=36 ves and 1 usd = 150 jpy, do I have enough? Show math."
    )
    claimed = currency_fast_path(prompt)
    assert claimed is not None
    assert "500 JPY left over" in claimed["response"]


def test_parser_exposes_typed_variants() -> None:
    spend = travel_spend_intent(CASES[0][0])
    comparison = travel_spend_intent(CASES[6][0])
    assert isinstance(spend, TravelSpendRequest)
    assert isinstance(comparison, HoldingComparisonRequest)


@pytest.mark.parametrize(
    ("prompt", "expected_identity", "expected_balance"),
    (
        (
            "I have 1,000 units of local currency in Washington, D. C. and buy a coffee for 5 "
            "units of local currency in Washington State. Are the two currencies the same? "
            "Name the currency and calculate the remainder.",
            "United States dollar (USD)",
            "995 USD left over",
        ),
        (
            "I have 80 units of local currency in Washington D C and buy a map for 12 units of "
            "local currency in Austin, Texas. Are the currencies the same? Name the currencies "
            "and calculate the remainder.",
            "United States dollar (USD)",
            "68 USD left over",
        ),
        (
            "I have 50 units of local currency in London, U. K. and buy tea for 7 units of local "
            "currency in Edinburgh, United Kingdom. Are the currencies the same? Name the currencies "
            "and calculate the remainder.",
            "pound sterling (GBP)",
            "43 GBP left over",
        ),
    ),
)
def test_punctuation_spaced_jurisdiction_initialisms_resolve_without_alias_sprawl(
    prompt: str,
    expected_identity: str,
    expected_balance: str,
) -> None:
    claimed = currency_fast_path(prompt)

    assert claimed is not None
    assert claimed["kind"] == "travel_spend"
    assert "currencies are the same" in claimed["response"]
    assert expected_identity in claimed["response"]
    assert expected_balance in claimed["response"]
    assert claimed["retrieval_allowed"] is False


def test_spaced_initialism_normalization_does_not_merge_distinct_jurisdictions() -> None:
    prompt = (
        "I have 100 units of local currency in Washington, D. C. and buy a book for 20 units of "
        "local currency in Washington, Tyne and Wear, UK. If 1 GBP = 1.25 USD, are the two "
        "currencies the same? Name the currencies and calculate the remainder."
    )
    claimed = currency_fast_path(prompt)

    assert claimed is not None
    assert "United States dollar (USD)" in claimed["response"]
    assert "pound sterling (GBP)" in claimed["response"]
    assert "currencies are not the same" in claimed["response"]
    assert "60 GBP left over" in claimed["response"]


@pytest.mark.parametrize(
    "prompt",
    (
        "I have 100 units of local currency in Washington, D. X. and buy a book for 20 units of "
        "local currency in Washington State. Are the currencies the same? Calculate the remainder.",
        "I have 100 units of local currency in Alpha B C and buy a book for 20 units of local "
        "currency in Washington State. Are the currencies the same? Calculate the remainder.",
    ),
)
def test_unknown_spaced_initialisms_do_not_invent_a_monetary_jurisdiction(prompt: str) -> None:
    assert travel_spend_intent(prompt) is None


@pytest.mark.parametrize("prompt", FROZEN_UNRESOLVED_QUALIFIED_JURISDICTIONS)
def test_frozen_qualified_jurisdictions_name_currencies_but_decline_math_without_rate(
    prompt: str,
) -> None:
    request = travel_spend_intent(prompt)
    claimed = currency_fast_path(prompt)

    assert isinstance(request, TravelSpendRequest)
    assert request.missing_rate is True
    assert claimed is not None
    assert claimed["grounded"] == "no_rate_declined"
    assert "currencies are not the same" in claimed["response"]
    assert "cannot be calculated without" in claimed["response"]
    assert "No rate was supplied, retrieved, or invented" in claimed["response"]
    assert "9,995" not in claimed["response"]
    assert "995 AUD" not in claimed["response"]


@pytest.mark.parametrize(
    "prompt",
    (
        "I have 100 units of local currency in London, UK. I travel to London, Neverland, and "
        "buy a book for 20 units of local currency. Name both currencies and calculate the remainder.",
        "I have 100 units of local currency in Paris, France. I travel to Paris, Republic of "
        "Nowhere, and buy lunch for 20 units of local currency. Name both currencies and show the math.",
        "I have 100 units of local currency in Sydney, Australia. I travel to Sydney, Atlantis, "
        "and buy a ticket for 20 units of local currency. Are the currencies the same? Calculate the remainder.",
        "I have 100 units of local currency in Alexandria, Egypt. I travel to Alexandria, "
        "Freedonia, and buy a map for 20 units of local currency. Name the currencies and calculate what is left.",
        "I have 100 units of local currency in Manchester, UK. I travel to Manchester, Elbonia, "
        "and buy a meal for 20 units of local currency. Are these currencies the same? Show the math.",
    ),
)
def test_unknown_qualified_city_paraphrases_remain_unresolved(prompt: str) -> None:
    assert travel_spend_intent(prompt) is None
    assert currency_fast_path(prompt) is None


@pytest.mark.parametrize(
    ("prompt", "resolves"),
    (
        (
            "i have 100 units of local currency in london, uk. i travel to london , nEvErLaNd , and "
            "buy a book for 20 units of local currency. name the currencies and calculate whats left",
            False,
        ),
        (
            "I have 100 units of local currency in Manchester, UK. I travel to MANCHESTER,N. H., and "
            "buy a snack for 20 units of local currency. currencies same? calculate remainder",
            True,
        ),
        (
            "I have 100 units of local currency in Sydney, Australia. I travel to sydney , N S, and "
            "buy a pass for 20 units of local currency. name currencies; show math",
            True,
        ),
        (
            "I have 100 units of local currency in Alexandria, Egypt. I travel to alexandria, V A, "
            "and buy a mug for 20 units of local currency. are currencies same? calculate whats left",
            True,
        ),
        (
            "I have 100 units of local currency in Paris, France. I travel to PARIS , nvr-land , and "
            "buy tea for 20 units of local currency. name both currencies and calculate remainder",
            False,
        ),
    ),
)
def test_qualified_city_sloppy_variants_resolve_only_authoritative_jurisdictions(
    prompt: str,
    resolves: bool,
) -> None:
    request = travel_spend_intent(prompt)
    claimed = currency_fast_path(prompt)

    if not resolves:
        assert request is None
        assert claimed is None
        return
    assert isinstance(request, TravelSpendRequest)
    assert request.missing_rate is True
    assert claimed is not None
    assert claimed["grounded"] == "no_rate_declined"
    assert "cannot be calculated without" in claimed["response"]


@pytest.mark.parametrize(
    ("prompt", "expected"),
    (
        (
            "I have 1,000 units of currency in London, Ontario. I travel to London, UK and buy a "
            "book for 20 units of currency. Assuming 1 GBP = 1.70 CAD, calculate the remainder.",
            ("Canadian dollar (CAD)", "pound sterling (GBP)"),
        ),
        (
            "I have 500 units of currency in Paris, Texas and buy a guitar for 400 units of "
            "currency in Paris, France. If 1 EUR = 1.10 USD, calculate what remains.",
            ("United States dollar (USD)", "euro (EUR)"),
        ),
        (
            "I have 100 USD in Manchester, New Hampshire. I travel to London, UK and buy a book "
            "for 20 GBP. If 1 GBP = 1.25 USD, calculate the remainder.",
            ("United States dollar (USD)", "pound sterling (GBP)"),
        ),
    ),
)
def test_known_qualifiers_and_explicit_currency_authority_still_resolve(
    prompt: str,
    expected: tuple[str, str],
) -> None:
    claimed = currency_fast_path(prompt)
    assert claimed is not None
    assert claimed["kind"] == "travel_spend"
    assert all(fragment in claimed["response"] for fragment in expected)


def test_negated_known_region_inside_unknown_qualifier_cannot_poison_city_resolution() -> None:
    prompt = (
        "I have 100 units of local currency in London, UK. I travel to London, Not Ontario, and "
        "buy a book for 20 units of local currency. Are the currencies the same? Name both currencies."
    )

    assert travel_spend_intent(prompt) is None
    assert currency_fast_path(prompt) is None


def test_country_state_homograph_qualifier_requires_more_authority() -> None:
    import core.currency_travel_spend as travel

    assert travel._location_code("Springfield, Georgia") == ""
    assert travel._location_code("Springfield, Georgia", unit="USD") == "USD"
    assert travel._location_code("Springfield, Georgia", unit="GEL") == "GEL"


def test_known_cross_currency_purchase_without_rate_is_an_explicit_insufficiency() -> None:
    prompt = (
        "I have 100 units of local currency in New York, USA. I want to buy a book for 20 units "
        "of local currency in London, UK. Name the currencies and calculate how much is left."
    )

    claimed = currency_fast_path(prompt)

    assert claimed is not None
    assert claimed["kind"] == "travel_spend"
    assert claimed["grounded"] == "no_rate_declined"
    assert "United States dollar (USD)" in claimed["response"]
    assert "pound sterling (GBP)" in claimed["response"]
    assert "cannot be calculated without a USD/GBP exchange rate" in claimed["response"]
    assert "No rate was supplied, retrieved, or invented" in claimed["response"]


def test_qualified_location_guard_is_load_bearing_when_authority_tables_are_sabotaged(
    monkeypatch,
) -> None:
    import core.currency_travel_spend as travel

    monkeypatch.setattr(travel, "_REGION_TO_CODE", {})
    monkeypatch.setattr(travel, "LOCATION_TO_CODE", {"manchester": "GBP"})

    assert travel._location_code("Manchester") == "GBP"
    assert travel._location_code("Manchester, New Hampshire") == ""
    assert travel._location_code("Manchester, New Hampshire", unit="USD") == "USD"


def test_currency_coverage_keeps_the_complete_multi_sentence_turn_local() -> None:
    coverage = coverage_for(CASES[7][0], FAMILY_CURRENCY)
    assert coverage.covers_whole_turn
    assert not coverage.conflicting


def test_real_frontdoor_ends_the_turn_without_provider_or_retrieval(tmp_path) -> None:
    from apps.vool_agent import VoolAgent

    prompt = CASES[7][0]
    agent = VoolAgent(backend_name="test-backend", device="travel-fx-test", persona_id="default")
    outcome = agent._handle_turn_frontdoor(
        raw_user_input=prompt,
        effective_input=prompt,
        normalized_input=prompt.casefold(),
        source_surface="api",
        session_id="travel-fx-frontdoor",
        source_context={
            "workspace": str(tmp_path),
            "workspace_root": str(tmp_path),
            "session_id": "travel-fx-frontdoor",
            "operating_mode": "auto",
            "surface": "api",
        },
        persona=None,
        interpreted=None,
    )

    result = (outcome or {}).get("result")
    assert result is not None
    assert result["route_reason"] == "currency_travel_spend_fast_path"
    assert "25,000 TWD short" in result["response"]
    assert result.get("web_calls", 0) == 0
    assert result["model_calls"] == 0
    assert "web" in result["route_skips"]


@pytest.mark.parametrize(
    ("prompt", "source_currency", "target_currency", "forbidden_balance"),
    (
        (
            FROZEN_UNRESOLVED_QUALIFIED_JURISDICTIONS[0],
            "pound sterling (GBP)",
            "United States dollar (USD)",
            "9,995 GBP",
        ),
        (
            FROZEN_UNRESOLVED_QUALIFIED_JURISDICTIONS[1],
            "Australian dollar (AUD)",
            "Canadian dollar (CAD)",
            "995 AUD",
        ),
    ),
)
def test_frozen_missing_rate_turns_end_at_frontdoor_with_explicit_insufficiency(
    tmp_path,
    prompt: str,
    source_currency: str,
    target_currency: str,
    forbidden_balance: str,
) -> None:
    from apps.vool_agent import VoolAgent

    agent = VoolAgent(backend_name="test-backend", device="missing-rate-test", persona_id="default")
    outcome = agent._handle_turn_frontdoor(
        raw_user_input=prompt,
        effective_input=prompt,
        normalized_input=prompt.casefold(),
        source_surface="api",
        session_id="travel-missing-rate-frontdoor",
        source_context={
            "workspace": str(tmp_path),
            "workspace_root": str(tmp_path),
            "session_id": "travel-missing-rate-frontdoor",
            "operating_mode": "auto",
            "surface": "api",
            "allow_remote_fetch": False,
        },
        persona=None,
        interpreted=None,
    )

    result = (outcome or {}).get("result")
    assert result is not None
    assert result["route_reason"] == "currency_travel_spend_fast_path"
    assert source_currency in result["response"]
    assert target_currency in result["response"]
    assert "cannot be calculated without" in result["response"]
    assert "No rate was supplied, retrieved, or invented" in result["response"]
    assert forbidden_balance not in result["response"]
    assert result["model_calls"] == 0
    assert result.get("web_calls", 0) == 0


def test_public_turn_does_not_buy_a_planner_call_before_closed_fx_contract(tmp_path, monkeypatch) -> None:
    from apps.vool_agent import VoolAgent

    prompt = CASES[0][0]
    monkeypatch.setenv("VOOL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("VOOL_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(
        "core.agent_runtime.turn_planner_hook.build_planner_ask_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("closed FX contract reached the planner")),
    )
    agent = VoolAgent(backend_name="test-backend", device="travel-fx-public", persona_id="default")

    result = agent.run_once(
        prompt,
        session_id_override="openclaw:closedfx00000000001",
        source_context={
            "workspace": str(tmp_path),
            "workspace_root": str(tmp_path),
            "surface": "api",
            "operating_mode": "auto",
            "requested_model": "vool-local-only",
            "local_only": True,
        },
    )

    assert result["route_reason"] == "currency_travel_spend_fast_path"
    assert "1,498,500 CRC left over" in result["response"]
    assert result["model_calls"] == 0


def test_set3_washington_same_currency_turn_is_zero_model_tool_and_web_at_frontdoor(tmp_path, monkeypatch) -> None:
    from apps.vool_agent import VoolAgent

    prompt = CASES[19][0]
    monkeypatch.setenv("VOOL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("VOOL_RUNTIME_DIR", str(tmp_path / "runtime"))
    agent = VoolAgent(backend_name="test-backend", device="washington-currency-test", persona_id="default")
    resolve = mock.Mock(side_effect=AssertionError("same-currency jurisdiction turn invoked a model"))
    tool = mock.Mock(side_effect=AssertionError("same-currency jurisdiction turn attempted a tool"))
    monkeypatch.setattr(agent.memory_router, "resolve", resolve)
    monkeypatch.setattr(agent.memory_router, "_invoke_manifest", resolve)
    monkeypatch.setattr(agent, "_execute_tool_intent", tool)

    result = agent.run_once(
        prompt,
        session_id_override="openclaw:set3washingtoncurrency",
        source_context={
            "workspace": str(tmp_path),
            "workspace_root": str(tmp_path),
            "surface": "api",
            "platform": "api",
            "operating_mode": "auto",
            "requested_model": "vool-local-only",
            "local_only": True,
            "allow_remote_fetch": False,
        },
    )

    assert result["route_reason"] == "currency_travel_spend_fast_path"
    assert result["model_calls"] == 0
    assert result["web_calls"] == 0
    assert "Washington, D. C.: United States dollar (USD)" in result["response"]
    assert "Washington State: United States dollar (USD)" in result["response"]
    assert "currencies are the same" in result["response"]
    assert "995 USD left over" in result["response"]
    resolve.assert_not_called()
    tool.assert_not_called()


@pytest.mark.parametrize(
    "prompt",
    (
        "Send 100 USD from New York to London and pay for a 20 GBP book at 1 GBP = 1.25 USD.",
        "I have 100 units of local currency in Atlantis. I want to buy a book for 20 units of local "
        "currency in London, UK. If 1 GBP = 1.25 USD, how much is left?",
        "I have 100 EUR in New York. I want to buy a book for 20 GBP in London, UK. If 1 GBP = 1.25 "
        "USD, how much is left?",
        "I have 100 USD in New York. I want to buy a book for 20 GBP in London, UK. If 0 GBP = 1.25 "
        "USD, how much is left?",
        "I have 100 USD in New York. I want to buy a book for 20 GBP in London, UK. If 1 USD = 1 EUR "
        "and 1 EUR = 1 GBP and 1 GBP = 2 CAD, how much is left?",
        "Yesterday I read a story about having money in New York and buying a book in London.",
    ),
)
def test_incomplete_unsafe_or_non_calculation_near_misses_are_not_claimed(prompt: str) -> None:
    assert travel_spend_intent(prompt) is None


def test_existing_actual_transaction_guard_remains_load_bearing() -> None:
    assert currency_fast_path("Buy 100 EUR for me with USD at 1 EUR = 1.10 USD") is None
    assert currency_fast_path("Wire 500 CAD to London") is None
