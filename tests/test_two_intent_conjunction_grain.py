"""The execution grain must not fuse a live-data fragment into a request no lane serves.

THE MEASURED DEFECT (served, f8937f80 -- validation-logs/two-intent-routing-20260907/
TWO_INTENT_FINDING_origin.md). "get me latest on Iran and oil prices pls" mints two demand units
(`get me latest on Iran` / `and oil prices pls`), but `execution_unit_spans` fused the headless
"oil prices" fragment into the Iran request: the fusion rule read every fragment without a demand
head as a continuation. Coverage then saw ONE unit, `lane_may_claim_whole_turn` let the live-data
lane end the turn, the typed plan bound only u2, and the news half surfaced as
"get me latest on Iran -- not dispatched" under the Brent quote. The identical intent with a
question-shaped second clause split and served both halves -- a class defect in the grain, not a
wording.

THE LAW UNDER TEST. A headless fragment rides the request before it only when it is a
continuation OF that request: a rider (names no thing of its own), an elliptical continuation
(opens with a preposition, or points back with a pronoun), a fragment the SAME family serves
(read through the family's own binder over the whole text -- "gold" + "and silver price" is one
typed plan), or general prose beside general prose. A fragment one family serves beside a request
that family does not serve is a second request, whichever side of the conjunction it sits on.

Everything here is the mint/grain layer in-process; the served proof of the same class is
tests/test_two_intent_conjunction_served.py.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.demand_ownership import (
    LANE_LIVE_DATA,
    demand_coverage,
    execution_units,
    lane_may_claim_whole_turn,
)

OPERATOR_WORDING = "get me latest on Iran and oil prices pls"

#: (wording, a token of the half no lane serves, a token of the live-data half).
TWO_INTENT_FAMILY = [
    # the reported wording
    (OPERATOR_WORDING, "iran", "oil"),
    # clean paraphrases: different vocabulary and sentence shape, unrelated places and assets
    ("tell me the latest about Mali and the gold price", "mali", "gold"),
    ("brief me on the Sahel and oil prices", "sahel", "oil"),
    ("news from Bolivia and the bitcoin price", "bolivia", "bitcoin"),
    ("tell me about Sudan and the silver price", "sudan", "silver"),
    ("fill me in on the Taiwan strait and the silver price", "taiwan", "silver"),
    ("headlines about the Panama canal and gold prices", "panama", "gold"),
    # sloppy / user-typed: typos, dropped words, casual phrasing, no capitals
    ("latest on iran and oil prices pls", "iran", "oil"),
    ("latst on iran and oil prices", "iran", "oil"),
    ("get me latest on iran and oil prices plz", "iran", "oil"),
    ("iran latest and brent price now", "iran", "brent"),
    ("gimme the latest on lebanon and gold price", "lebanon", "gold"),
    ("update me on Ukraine plus bitcoin price", "ukraine", "bitcoin"),
    # the conjunction reversed: the live-data half first, the unserved half riding behind it
    ("oil prices and the latest on Iran", "iran", "oil"),
    ("bitcoin price plus update me on Ukraine", "ukraine", "bitcoin"),
    ("silver price and news on Chile", "chile", "silver"),
    ("gold price and whats happening in Gaza", "gaza", "gold"),
]

#: Coordinations that are ONE request: a shared predicate over several assets, an elliptical
#: continuation, a rider, or general prose beside general prose. Must stay one execution unit.
ONE_REQUEST_CONTROLS = [
    "gold and silver price",
    "gold, silver and bitcoin price",
    "get me the price of gold and silver please",
    "price of gold and silver right now",
    "weather in Rome and gold price",
    "convert 100 usd to eur and then to gold",
    "weather in Rome, in celsius please",
    "1000 EUR to RUB, gold with it",
    "explain osmosis simply and draft a limerick about copper",
    # adversarial near-misses: read like the family, belong to one lane
    "oil prices and the gold price",
    "latest oil prices and brent",
]

#: The class boundary: NEITHER half is read by a coverage lane, so nothing can pre-empt the turn
#: and the grain may keep it whole for one answering lane. Not the defect -- a control that the
#: repair does not shred general conjunctions.
NO_LANE_CONTROLS = [
    "latest on Nvidia and the euro rate",
    "any update on Red Sea shipping and brent crude",
]

#: Already-split shapes ("price" is a demand head, so the second clause opened a fresh request
#: before this repair). They must keep splitting exactly as they did.
HEAD_SPLIT_CONTROLS = [
    ("give me an update on Sudan and the price of oil", "sudan", "oil"),
    ("what's going on in Haiti and the price of silver", "haiti", "silver"),
]


def _unit_holding(units, token: str) -> tuple[str, str]:
    for unit_id, text in units:
        if token in text.lower():
            return unit_id, text
    raise AssertionError(f"no execution unit carries {token!r}: {units}")


@pytest.mark.parametrize("wording,unserved,live", TWO_INTENT_FAMILY, ids=[row[0] for row in TWO_INTENT_FAMILY])
def test_a_live_data_fragment_does_not_ride_a_request_its_family_does_not_serve(wording, unserved, live):
    units = execution_units(wording)
    assert len(units) >= 2, f"fused into one execution unit: {units}"
    unserved_id, unserved_text = _unit_holding(units, unserved)
    live_id, live_text = _unit_holding(units, live)
    assert unserved_id != live_id, f"both halves share one execution unit: {units}"
    assert live not in unserved_text.lower() and unserved not in live_text.lower(), units

    coverage = demand_coverage(wording)
    lanes = dict(zip((unit_id for unit_id, _t in coverage.units), coverage.per_unit_lanes, strict=True))
    assert LANE_LIVE_DATA in lanes[live_id], f"the live half is not read by the live family: {lanes}"
    assert LANE_LIVE_DATA not in lanes[unserved_id], f"the live family claims the {unserved!r} half: {lanes}"
    # THE AUTHORITY SEAM: the turn is mixed, so the demand-owned plan executes every unit through
    # its owning lane, and the live-data lane may not end the external turn on its own.
    assert coverage.mixed, f"coverage did not read the turn as mixed: {lanes}"
    assert not lane_may_claim_whole_turn(wording, LANE_LIVE_DATA), lanes


@pytest.mark.parametrize("wording", ONE_REQUEST_CONTROLS)
def test_a_coordination_one_family_serves_whole_stays_one_execution_unit(wording):
    units = execution_units(wording)
    assert len(units) == 1, f"a single request was shredded: {units}"
    assert not demand_coverage(wording).mixed


@pytest.mark.parametrize("wording", NO_LANE_CONTROLS)
def test_a_conjunction_no_lane_reads_stays_whole_and_nothing_can_preempt_it(wording):
    coverage = demand_coverage(wording)
    assert coverage.unit_count == 1, coverage.units
    assert not any(coverage.per_unit_lanes), coverage.per_unit_lanes


@pytest.mark.parametrize("wording,unserved,live", HEAD_SPLIT_CONTROLS, ids=[row[0] for row in HEAD_SPLIT_CONTROLS])
def test_a_second_clause_that_opens_with_a_demand_head_keeps_splitting(wording, unserved, live):
    units = execution_units(wording)
    assert len(units) == 2, units
    assert _unit_holding(units, unserved)[0] != _unit_holding(units, live)[0]
    assert demand_coverage(wording).mixed
