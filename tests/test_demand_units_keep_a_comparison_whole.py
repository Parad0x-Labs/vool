"""One enumerated comparison is ONE demand, not three.

Measured 2026-09-06 (served comparison, fixture transport): the closure sweep listed
"Now compare the VW Passat", "and the VW Golf in detail: production periods, sales, regions,
engines" and "and prices." as three things that could not be answered -- the request had been cut
at each "and" into pseudo-demands, and every one of them was reported unanswered beneath an answer
that had just published six supported claims about exactly that comparison. The subjects a
comparison coordinates, and the facet list a colon introduces, are one request.
"""
from __future__ import annotations

from core.agent_runtime.answer_coverage import demand_units

COMPARISON = "Now compare the VW Passat and the VW Golf in detail: production periods, sales, regions, engines and prices."


def test_an_enumerated_comparison_mints_one_demand() -> None:
    units = demand_units(COMPARISON)
    assert len(units) == 1, [u.text for u in units]
    assert "prices" in units[0].text and "Passat" in units[0].text


def test_two_real_requests_still_mint_two_demands() -> None:
    units = demand_units("convert 100 eur to usd and tell me the weather in vilnius")
    assert len(units) == 2, [u.text for u in units]


def test_a_comparison_of_two_subjects_without_a_facet_list_is_one_demand() -> None:
    units = demand_units("compare python and rust for a small cli tool")
    assert len(units) == 1, [u.text for u in units]


def test_a_comparison_followed_by_a_separate_request_splits_once() -> None:
    units = demand_units("compare python and rust for a small cli tool, and what is the gold price right now?")
    assert len(units) == 2, [u.text for u in units]


def test_a_colon_list_of_instructions_still_splits() -> None:
    units = demand_units("do two things: define liquidity and summarize the gold standard")
    assert len(units) == 2, [u.text for u in units]
