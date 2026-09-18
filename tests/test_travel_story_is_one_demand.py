"""A travel-spend story the currency contract parses whole is ONE demand, so its lane may end the turn.

Measured 2026-09-07 (`test_currency_travel_spend_fast_path`, five reds): the mint split the story into
five sentence units, the per-unit currency probe claimed none of them alone, and the whole-turn claim law
refused the lane that had fully answered the story -- the front door returned nothing. New stories,
not the fixture's.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.answer_coverage import demand_units
from core.agent_runtime.demand_ownership import LANE_CURRENCY, lane_may_claim_whole_turn

STORIES = [
    "I have 900,000 units of local currency in Seoul, South Korea. I want to buy a camera for 1,200 units of local currency in Toronto, Canada. If 1 CAD = 1,000 KRW, how much of Canada's currency is left after exchanging and buying? Show the math.",
    "I hold 2,500 units of local currency in Lisbon, Portugal and fly to Bangkok, Thailand to buy a suit for 30,000 units of local currency. With 1 EUR = 38 THB, what remains in Thailand's currency? Show the steps.",
    "I am holding 750 units of local currency in Cork, Ireland and head to Aberdeen, Scotland to buy boots for 150 units of local currency. Taking 1 EUR = 0.84 GBP, what remains in Scotland's currency? Show me all the working.",
    # Round 4 (fresh wording of the holes measured on 2026-09-07: "I've got", "and then I travel to",
    # "then I head to ... and buy", a purchase sentence naming only the city half, "same money",
    # "balance", and closing lines that are not "show the math").
    "I've got 2,200 units of local currency in Graz, Austria and then I travel to Gdansk, Poland to buy a rain jacket for 310 units of local currency. Taking 1 EUR = 4.3 PLN, what's my balance after that? Take me through the steps.",
    "Suppose I hold 95,000 units of local currency in Nagoya, Japan, then I head to Busan, South Korea and buy a rail pass for 60,000 units of local currency. With 1 JPY = 9 KRW, how much is left in Korean money? Break down the calculation.",
    "I am carrying 480 units of local currency in Tallinn, Estonia. I want to buy a wool scarf in Riga, Latvia for 45 units of local currency. Do the two cities use the same money? What remains afterwards?",
]


@pytest.mark.parametrize("story", STORIES)
def test_a_travel_story_mints_one_unit_and_the_currency_lane_may_end_it(story):
    """Contract 2026-09-08: the story's narration is CONTEXT of its question(s); every request the
    story carries ("Do the two cities use the same money? What remains afterwards?" is two) is the
    currency lane's, so the lane may end the turn and nothing reads the turn as mixed."""
    from core.agent_runtime.answer_coverage import interpret_request
    from core.agent_runtime.demand_ownership import demand_coverage, execution_units

    interpretation = interpret_request(story)
    units = demand_units(story)
    assert 1 <= len(units) <= 2, [u.text for u in units]
    assert all(unit.text.rstrip().endswith("?") or "remain" in unit.text.lower() or "left" in unit.text.lower() for unit in units), [u.text for u in units]
    assert interpretation.context, "the story's narration must be minted as context, not as demands"
    # Two head-bearing questions stay two execution units of ONE lane; a single question is one.
    assert 1 <= len(execution_units(story)) <= 2
    coverage = demand_coverage(story)
    assert coverage.mixed is False
    assert all("currency" in lane for lanes in coverage.per_unit_lanes for lane in lanes), coverage.per_unit_lanes
    # The id production passes (turn_frontdoor: `lane_may_claim_whole_turn(raw, LANE_CURRENCY)`); an
    # unregistered id can never end a multi-request turn by law.
    assert lane_may_claim_whole_turn(story, LANE_CURRENCY)
    assert not lane_may_claim_whole_turn(story, "currency_fast_path") or len(demand_units(story)) < 2


def test_a_travel_story_beside_an_unrelated_ask_still_splits():
    text = STORIES[0] + " Also, what is the weather in Toronto right now?"
    assert len(demand_units(text)) >= 2


def test_a_side_ask_naming_the_story_city_is_still_its_own_demand():
    # The city half rule must not swallow a different request that happens to name the same city.
    text = STORIES[-1] + " Also, what is the weather in Riga right now?"
    assert len(demand_units(text)) >= 2


def test_a_closing_line_with_a_real_object_is_not_the_story_s_presentation_line():
    text = STORIES[3] + " Also explain the steps of photosynthesis."
    assert len(demand_units(text)) >= 2
