"""A fragment that leads a request rides the request after it; a second request never does.

THE MEASURED DEFECT (served at d6a398af and 4f18cfdb through `VoolAgent.run_once`, model stand-in,
synthetic Notes runner). 'in the Personal folder, open my Apple note "Plan"' ended as
`deterministic:demand_owned_mixed_turn`: 'I could not answer this part of your message: - open my Apple
note "Plan" (could not be completed)'. The interpretation mints two request units and "open" is a demand
head, so `execution_unit_spans` opened a boundary before it. The folder fragment opens no demand and no
lane serves it on its own, yet it became an execution unit of its own and the Notes owner was never
reached. The merge law for a headless fragment (`_rides_the_request_before_it`) only looks back. The same
scope in front of a verb that is not a head ('in the Work folder, rename ...') rode that law and reached
Notes.

THE LAW UNDER TEST (`demand_ownership._leads_into_the_request_after_it`). A group that holds no request of
its own rides the request that opens right after it in the same sentence -- unless that request opens as a
coordinate of it, or a lane serves a member on its own -- when every member fronts the request with a
preposition, or a family that serves the request binds the group over the whole text.

In-process through the production catalog: no model, no socket. The served proof of the same class is
tests/pa_beta_gate/test_served_notes_scope_reading.py.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.answer_coverage import interpret_request
from core.agent_runtime.demand_ownership import (
    LANE_CURRENCY,
    LANE_LIVE_DATA,
    demand_coverage,
    execution_unit_spans,
    lane_may_claim_whole_turn,
)

LANE_OPERATOR = "operator_action_dispatch"
LANE_CLOCK = "date_time_fast_path"

#: (case, wording, the lane that must read the ONE execution unit -- None when no lane reads it).
LEADING_FRAGMENT_FAMILY = [
    # the reported wordings
    ("original-open", 'in the Personal folder, open my Apple note "Plan"', LANE_OPERATOR),
    ("original-show", 'in the Personal folder, show my Apple note "Plan"', LANE_OPERATOR),
    # clean paraphrases: other scopes, titles and verbs, and other lanes and domains
    ("paraphrase-inside-possessive", 'Inside my Errands folder, show the Apple note "Groceries"', LANE_OPERATOR),
    ("paraphrase-from", 'From the Travel folder, open my Apple note "Packing list"', LANE_OPERATOR),
    ("paraphrase-account-then-folder", 'In the Gmail account, in the Taxes 2026 folder, open my Apple note "Receipts"', LANE_OPERATOR),
    ("paraphrase-create", 'In my Travel folder, create an Apple note titled "Trip" with: pack chargers', LANE_OPERATOR),
    ("paraphrase-forecast-day", "For tomorrow, what is the weather in Lisbon", LANE_LIVE_DATA),
    ("paraphrase-clock-offset", "In 2 hours, what time is it in Nairobi", LANE_CLOCK),
    ("paraphrase-conversion-purpose", "For my trip to Oslo, convert 250 USD to NOK", LANE_CURRENCY),
    ("paraphrase-general-prose", "In simple words, explain how tidal energy works", None),
    # sloppy, user-typed
    ("sloppy-article-typo", 'in teh Personal folder, open my apple note "Plan"', LANE_OPERATOR),
    ("sloppy-dropped-preposition", 'Personal folder, open my Apple note "Plan"', LANE_OPERATOR),
    ("sloppy-polite-leader", 'in the Personal folder, please open my Apple note "Plan"', LANE_OPERATOR),
    ("sloppy-casual-opener-filler", 'and in the personal folder, show me my apple note "Plan" pls', LANE_OPERATOR),
    ("sloppy-shouted", 'IN THE PERSONAL FOLDER, OPEN MY APPLE NOTE "Plan"', LANE_OPERATOR),
    ("sloppy-spaced-comma", 'in the Personal folder , open my Apple note "Plan"', LANE_OPERATOR),
]

#: (case, wording, the execution units that must stand apart, a lane that may NOT end the turn alone).
APART_CONTROLS = [
    # the real second request still splits; the scope in front rides only the request it leads
    ("control-scope-then-second-request", "in Berlin, what is the weather, and convert 5 USD to EUR",
     ["in Berlin, what is the weather", "and convert 5 USD to EUR"], LANE_CURRENCY),
    ("control-notes-then-weather", 'in the Personal folder, open my Apple note "Plan", and what is the weather in Rome',
     ['in the Personal folder, open my Apple note "Plan"', "and what is the weather in Rome"], LANE_OPERATOR),
    # a topic in front of a request and coordinated with it is a request of its own
    ("control-coordinated-topic", "Ukraine situation, and what is the bitcoin price",
     ["Ukraine situation", "and what is the bitcoin price"], LANE_LIVE_DATA),
    ("control-coordinated-prepositional-topic", "about the Iran situation, and what is the gold price",
     ["about the Iran situation", "and what is the gold price"], LANE_LIVE_DATA),
    # a bare topic the request's family does not bind stays apart without a connector
    ("control-bare-topic", "Ukraine situation, what is the bitcoin price",
     ["Ukraine situation", "what is the bitcoin price"], LANE_LIVE_DATA),
    # a lane serves the leading fragment on its own
    ("control-leading-conversion", "for 100 EUR in USD, what is the gold price",
     ["for 100 EUR in USD", "what is the gold price"], LANE_LIVE_DATA),
    ("control-leading-quote", "at the silver price, explain entropy",
     ["at the silver price", "explain entropy"], LANE_LIVE_DATA),
    # a group that already holds a request never rides the request after it
    ("control-two-general-requests", "explain entropy, describe osmosis in one line",
     ["explain entropy", "describe osmosis in one line"], None),
    # a sentence that ends before the request is no fronted part of it (found by tests/execution_grain_census.py)
    ("control-question-sentence-before-a-question", "If it rains tomorrow, what should I wear? Also, what time is it?",
     ["If it rains tomorrow, what should I wear?", "what time is it?"], LANE_CLOCK),
    ("control-finished-sentence-before-a-forecast", "That forecast looked wrong. What is the weather in Oslo?",
     ["That forecast looked wrong.", "What is the weather in Oslo?"], LANE_LIVE_DATA),
    ("control-finished-sentence-before-a-clock-question", "That one broke again. What time is it in Denver?",
     ["That one broke again.", "What time is it in Denver?"], LANE_CLOCK),
    # adversarial near-misses
    ("near-miss-mistyped-connector", "about the Iran situation, thne what is the gold price",
     ["about the Iran situation", "thne what is the gold price"], LANE_LIVE_DATA),
    ("near-miss-topic-inside-an-adjunct-run", "in Berlin, Ukraine situation, what is the bitcoin price",
     ["in Berlin, Ukraine situation", "what is the bitcoin price"], LANE_LIVE_DATA),
    ("near-miss-topic-sentence-before-the-request", "About the Iran deal. What is the gold price?",
     ["About the Iran deal.", "What is the gold price?"], LANE_LIVE_DATA),
    ("near-miss-pronoun-clause-before-a-quote", "draft a limerick about it, what is the gold price",
     ["draft a limerick about it", "what is the gold price"], LANE_LIVE_DATA),
    ("control-two-complete-notes-actions", 'show my Apple note "Plan", open my Apple note "Groceries"',
     ['show my Apple note "Plan"', 'open my Apple note "Groceries"'], None),
    # a leading request no lane serves on its own, which the Notes lane's whole-text binder binds anyway: it
    # holds a demand of its own, so it never rides into the Notes request (only this clause keeps them apart)
    ("control-leading-request-the-binder-binds", 'describe the plan, open my Apple note "Plan"',
     ["describe the plan", 'open my Apple note "Plan"'], None),
    ("control-leading-pronoun-request-the-binder-binds", 'explain that, open my Apple note "Budget"',
     ["explain that", 'open my Apple note "Budget"'], None),
]


@pytest.mark.parametrize(("case", "wording", "lane"), LEADING_FRAGMENT_FAMILY, ids=[row[0] for row in LEADING_FRAGMENT_FAMILY])
def test_a_leading_fragment_rides_the_request_after_it(case, wording, lane):
    interpretation = interpret_request(wording)
    requests = interpretation.requests
    # A leading style constraint is a separate unit, but is not another request.
    assert len(interpretation.units) >= 2, f"{case}: the mint did not cut the leading fragment"
    spans = execution_unit_spans(wording)
    assert [span.text for span in spans] == [wording.strip()], f"{case}: the leading fragment stood apart"
    assert len(spans[0].member_unit_ids) == len(requests), (case, spans[0].member_unit_ids)
    coverage = demand_coverage(wording)
    assert coverage.unit_count == 1 and not coverage.mixed, (case, coverage.units, coverage.per_unit_lanes)
    if lane is None:
        assert coverage.per_unit_lanes == ((),), (case, coverage.per_unit_lanes)
        return
    # THE AUTHORITY SEAM: the lane that owns the request may end the turn, and it receives the scope.
    assert lane in coverage.per_unit_lanes[0], (case, coverage.per_unit_lanes)
    assert lane_may_claim_whole_turn(wording, lane), (case, coverage.per_unit_lanes)


@pytest.mark.parametrize(("case", "wording", "units", "lane"), APART_CONTROLS, ids=[row[0] for row in APART_CONTROLS])
def test_a_second_request_never_rides_as_a_leading_fragment(case, wording, units, lane):
    assert [span.text for span in execution_unit_spans(wording)] == units, case
    if lane is None:
        return
    coverage = demand_coverage(wording)
    assert coverage.mixed, (case, coverage.per_unit_lanes)
    assert not lane_may_claim_whole_turn(wording, lane), (case, coverage.per_unit_lanes)


def test_only_the_mints_own_connectors_open_a_coordinate():
    from core.agent_runtime.demand_ownership import _opens_as_a_coordinate

    for fragment in ("and what is the gold price", "also open my note", "plus the silver price", "then open it", "thne open it"):
        assert _opens_as_a_coordinate(fragment), fragment
    for fragment in ("please open my note", "just open my note", "open my note", "what is the gold price", "okay show it"):
        assert not _opens_as_a_coordinate(fragment), fragment
