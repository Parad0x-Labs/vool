"""A historically-anchored ask does not require a current observation.

On the frozen base e821457d, the tenure question "How long have I been
working before I started my current job at Google?" classified GROUNDED with
``current_information_required=True``, and the unsourced-current guard
rewrote a correct memory-backed "about 6 years" into "I could not obtain a
current reading" (LME q7db408b). Historical market/weather asks ("What was
the BTC price at its 2021 peak?") classified LIVE_DATA and were answered by
the live lane with the CURRENT reading.

REGRESSION rows restate the saved shapes. FRESH ACCEPTANCE rows use different
wording, facts, domains and dates and were frozen before first execution.
"""

from core.execution_requirements import requirements_for

# ---------------------------------------------------------------- regression


def test_tenure_question_requires_no_current_observation() -> None:
    req = requirements_for(
        "How long have I been working before I started my current job at Google?"
    )
    assert req.answer_mode == "GROUNDED"
    assert req.current_information_required is False
    assert "historically_anchored_not_current" in req.reason_codes


def test_historical_market_ask_is_not_live_data() -> None:
    req = requirements_for("What was the BTC price at its 2021 peak?")
    assert req.answer_mode != "LIVE_DATA"
    assert req.current_information_required is False


def test_historical_weather_ask_is_not_live_data() -> None:
    req = requirements_for("What was the weather in Vilnius on my birthday in 2019?")
    assert req.answer_mode != "LIVE_DATA"
    assert req.current_information_required is False


# ------------------------------------------------------- positive controls


def test_current_market_ask_still_requires_current_observation() -> None:
    req = requirements_for("What is the current price of BTC?")
    assert req.answer_mode == "LIVE_DATA"
    assert req.current_information_required is True


def test_current_weather_ask_still_requires_current_observation() -> None:
    req = requirements_for("Weather in Vilnius now")
    assert req.answer_mode == "LIVE_DATA"
    assert req.current_information_required is True


def test_unscoped_grounded_ask_keeps_current_requirement() -> None:
    # No past anchor at all: the conservative default (current required) must
    # not move just because the GROUNDED branch learned about scope.
    req = requirements_for("What does the documentation say about the export limit? cite sources")
    assert req.answer_mode == "GROUNDED"
    assert req.current_information_required is True


# ---------------------------------------------------------- fresh acceptance


def test_fresh_historical_bakery_price_is_not_current() -> None:
    req = requirements_for("What did a loaf of sourdough cost at the bakery in 2019?")
    assert req.current_information_required is False
    assert req.answer_mode != "LIVE_DATA"


def test_fresh_historical_deposit_amount_is_not_current() -> None:
    req = requirements_for("How much did our flat deposit come to when we signed?")
    assert req.current_information_required is False


def test_fresh_historical_sea_temperature_is_not_live_weather() -> None:
    req = requirements_for("What was the sea temperature at Palanga last July?")
    assert req.answer_mode != "LIVE_DATA"
    assert req.current_information_required is False


def test_fresh_mixed_ask_keeps_current_requirement() -> None:
    # The past half is history; the "today" half genuinely needs a live read.
    # A mixed question must stay current-required so its current half keeps
    # its evidence contract.
    req = requirements_for(
        "What did I pay for the tandem, and what does a new one cost today? cite sources"
    )
    assert req.answer_mode == "GROUNDED"
    assert req.current_information_required is True


def test_fresh_historical_rate_close_is_not_live_data() -> None:
    req = requirements_for("Where did the CHF rate finish at the end of 2022?")
    assert req.answer_mode != "LIVE_DATA"
    assert req.current_information_required is False


def test_fresh_prohibited_historical_ask_is_answerable_from_history() -> None:
    # Prohibition + past anchor: there is no current value to invent, so the
    # turn must not carry a current-observation requirement that no lane can
    # satisfy -- the model may answer from supplied history.
    req = requirements_for(
        "Do not search the web. How much was the workshop fee when I registered?"
    )
    assert req.current_information_required is False


def test_fresh_prohibited_current_ask_keeps_the_conflict_contract() -> None:
    req = requirements_for("Do not search the web. What is the current price of ETH?")
    assert req.current_information_required is True
    assert "live_data_toolset_prohibited" in req.reason_codes
