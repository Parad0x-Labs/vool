"""A fragment between two requests rides the request whose clause it opens, when that request's family binds it.

THE MEASURED DEFECT (grain probe over the production catalog, identical at 4f18cfdb and 1d58a641; served through
`VoolAgent.run_once` with a model stand-in and the synthetic Notes runner at 1d58a641). The look-back law rode a
preposition-led fragment into the request before it on its shape alone, even when the fragment fronted the request
right after it:

* 'check the gold price, and in the Personal folder, open my Apple note "Plan"' made the execution units
  'check the gold price, and in the Personal folder' | 'open my Apple note "Plan"';
* 'What is the gold price? In the Personal folder, open my Apple note "Plan".' made 'What is the gold price? In the
  Personal folder' | 'open my Apple note "Plan".' -- the scope crossed its sentence backwards.

Served, the Notes owner received 'open my Apple note "Plan"' without its folder, and with three notes titled "Plan" the
turn answered 'open my Apple note "Plan" (could not be completed)'.

THE LAW UNDER TEST (`demand_ownership._opens_the_clause_of_the_request_after_it`). Between two requests the preposition
shape reads both ways -- a continuation of the request before it ('weather in Rome, in celsius please') or a fronted
adjunct of the request after it -- so the shape does not decide. A run of fragments rides the request after it when:
it opens a clause of that request (its sentence, read by `answer_coverage.turn_slices`, or a coordinated conjunct, read
by the mint's own connectors; a sequencing leader opens a step of the request before it instead); the forward law
carries it into the request; the family that serves the request binds it over the whole text; and the group before it
holds a request of its own. Otherwise the look-back law decides, as before.

In-process through the production catalog: no model, no socket. The served proof is
tests/pa_beta_gate/test_served_notes_scope_between_requests.py.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.answer_coverage import interpret_request
from core.agent_runtime.demand_ownership import (
    LANE_LIVE_DATA,
    demand_coverage,
    execution_unit_spans,
    lane_may_claim_whole_turn,
)

LANE_OPERATOR = "operator_action_dispatch"
LANE_CLOCK = "date_time_fast_path"
LANE_CURRENCY = "currency_frontdoor"

#: (case, wording, the fragment, the request it fronts, the lane of the request BEFORE it or None).
BETWEEN_FAMILY = [
    # the reported wordings
    ("original-coordinated", 'check the gold price, and in the Personal folder, open my Apple note "Plan"',
     "in the Personal folder", 'open my Apple note "Plan"', LANE_LIVE_DATA),
    ("original-next-sentence", 'What is the gold price? In the Personal folder, open my Apple note "Plan".',
     "In the Personal folder", 'open my Apple note "Plan"', LANE_LIVE_DATA),
    # clean paraphrases: other first requests and lanes, other scopes, verbs, titles and sentence shapes
    ("paraphrase-weather-inside-travel", 'what is the weather in Lisbon, and inside the Travel folder, show my Apple note "Packing list"',
     "inside the Travel folder", 'show my Apple note "Packing list"', LANE_LIVE_DATA),
    ("paraphrase-prose-from-work", 'explain how tides work in two sentences, and from my Work folder, open the Apple note "Plan"',
     "from my Work folder", 'open the Apple note "Plan"', None),
    ("paraphrase-conversion-then-folder-of-account", 'Convert 40 GBP to JPY. From the Work folder of the On My Mac account, display my Apple note "Plan".',
     "From the Work folder of the On My Mac account", 'display my Apple note "Plan"', LANE_CURRENCY),
    ("paraphrase-clock-account-then-folder", 'what time is it in Nairobi? In the Gmail account, in the Taxes 2026 folder, open my Apple note "Receipts".',
     "In the Gmail account, in the Taxes 2026 folder", 'open my Apple note "Receipts"', LANE_CLOCK),
    ("paraphrase-second-notes-request", 'open my Apple note "Groceries", and in the Personal folder, open my Apple note "Plan"',
     "in the Personal folder", 'open my Apple note "Plan"', LANE_OPERATOR),
    ("paraphrase-plus-account", 'check the bitcoin price, plus in the On My Mac account, show my Apple note "Plan"',
     "in the On My Mac account", 'show my Apple note "Plan"', LANE_LIVE_DATA),
    # sloppy, user-typed
    ("sloppy-typos", 'check teh gold price, and in teh personal folder, open my apple note "Plan"',
     "in teh personal folder", 'open my apple note "Plan"', LANE_LIVE_DATA),
    ("sloppy-shouted", 'CHECK THE GOLD PRICE, AND IN THE PERSONAL FOLDER, OPEN MY APPLE NOTE "Plan"',
     "IN THE PERSONAL FOLDER", 'OPEN MY APPLE NOTE "Plan"', LANE_LIVE_DATA),
    ("sloppy-spaced-commas", 'check the gold price , and in the Personal folder , open my Apple note "Plan"',
     "in the Personal folder", 'open my Apple note "Plan"', LANE_LIVE_DATA),
    ("sloppy-no-space-after-question", 'what is the gold price?in the personal folder, open my apple note "Plan"',
     "in the personal folder", 'open my apple note "Plan"', LANE_LIVE_DATA),
    ("sloppy-no-comma-before-and", 'check the gold price and in the Travel folder, show my Apple note "Packing list"',
     "in the Travel folder", 'show my Apple note "Packing list"', LANE_LIVE_DATA),
    ("sloppy-casual-filler", 'gold price pls, and in the personal folder, show me my apple note "Plan" thx',
     "in the personal folder", 'show me my apple note "Plan" thx', LANE_LIVE_DATA),
    ("sloppy-dropped-preposition", 'check the gold price, and Work folder, open my Apple note "Plan"',
     "Work folder", 'open my Apple note "Plan"', LANE_LIVE_DATA),
    ("sloppy-trailing-dots", 'what is the gold price... In the Personal folder, open my Apple note "Plan"',
     "In the Personal folder", 'open my Apple note "Plan"', LANE_LIVE_DATA),
]

#: (case, wording, the execution units exactly as they must stand). Each keeps the look-back reading.
STAYS_WITH_THE_REQUEST_BEFORE = [
    # the look-back law's own trailing adjuncts (also tests/test_two_intent_conjunction_grain.py)
    ("control-trailing-unit", "weather in Rome, in celsius please", ["weather in Rome, in celsius please"]),
    ("control-dependent-step", "convert 100 usd to eur and then to gold", ["convert 100 usd to eur and then to gold"]),
    ("control-unit-before-a-coordinated-quote", "what is the weather in Rome, in celsius please, tell me also the gold price",
     ["what is the weather in Rome, in celsius please, tell me also the gold price"]),
    # a fragment in the middle of a clause opens no clause of the request after it
    ("control-mid-clause-unit-before-a-question", "what is the weather in Rome, in celsius please, what is the gold price",
     ["what is the weather in Rome, in celsius please", "what is the gold price"]),
    ("control-mid-clause-unit-before-a-notes-request", 'what is the weather in Rome, in celsius please, open my Apple note "Plan"',
     ["what is the weather in Rome, in celsius please", 'open my Apple note "Plan"']),
    # the request after it opens as a coordinate of it: the fragment is a conjunct of its own
    ("control-next-sentence-unit-before-a-coordinated-question", "What is the weather in Rome? In celsius please, and what is the gold price?",
     ["What is the weather in Rome? In celsius please", "and what is the gold price?"]),
    # a fragment that is a sentence of its own fronts no request
    ("control-fragment-that-is-its-own-sentence", 'What is the gold price? In euros. Open my Apple note "Plan".',
     ["What is the gold price? In euros.", 'Open my Apple note "Plan".']),
    # the two clauses split where they should: the unit stays with the weather, the scope rides the note
    ("control-unit-stays-and-scope-rides", 'what is the weather in Rome, in celsius please, and in the Personal folder, open my Apple note "Plan"',
     ["what is the weather in Rome, in celsius please", 'and in the Personal folder, open my Apple note "Plan"']),
    # adversarial near-misses: a coordinated clause opening, but the family of the request after it binds nothing, or the
    # connector sequences a step of the request before it
    ("near-miss-coordinated-place-before-a-clock-question", "what is the weather in Rome and in Paris, what time is it in Denver",
     ["what is the weather in Rome and in Paris", "what time is it in Denver"]),
    ("near-miss-coordinated-leg-before-a-clock-question", "convert 100 usd to eur and to gbp, what time is it in Tokyo",
     ["convert 100 usd to eur and to gbp", "what time is it in Tokyo"]),
    ("near-miss-coordinated-currency-before-a-clock-question", "price of gold in euros and in dollars, what time is it",
     ["price of gold in euros and in dollars", "what time is it"]),
    ("near-miss-sequenced-step-before-a-notes-request", 'convert 100 usd to eur and then to gold, open my Apple note "Plan"',
     ["convert 100 usd to eur and then to gold", 'open my Apple note "Plan"']),
    ("near-miss-sequenced-currency-before-a-forecast", "check the gold price and then in euros, what is the weather in Rome",
     ["check the gold price and then in euros", "what is the weather in Rome"]),
    # the operator binder reads only what the Notes request reads (request_reading), so a unit of measure
    # or a conversion leg the Notes request never reads stays with the request before it -- moved here from
    # OVER_BOUND_NEAR_MISSES when that binder learned its grain (strict markers tripped as designed)
    ("coarse-operator-binder-coordinated-unit", 'what is the weather in Rome, and in celsius, open my Apple note "Plan"',
     ["what is the weather in Rome, and in celsius", 'open my Apple note "Plan"']),
    ("coarse-operator-binder-next-sentence-unit", 'What is the weather in Rome? In celsius please, open my Apple note "Plan".',
     ["What is the weather in Rome? In celsius please", 'open my Apple note "Plan".']),
    ("coarse-operator-binder-coordinated-leg", 'convert 100 usd to eur and to gbp, open my Apple note "Plan"',
     ["convert 100 usd to eur and to gbp", 'open my Apple note "Plan"']),
]

#: Wordings of the same class this law leaves where the look-back law puts them, each with the reader that is missing.
#: Strict: when a reader learns to decide one, the marker fails and the case moves into BETWEEN_FAMILY.
OPEN_BETWEEN_CASES = [
    ("open-no-clause-opening", 'check the gold price, in the Personal folder, open my Apple note "Plan"',
     "in the Personal folder", 'open my Apple note "Plan"',
     "no clause opening marks the fragment, and no family reader separates a scope of the Notes request from an adjunct "
     "of the gold request: the operator family's binder binds every unit of a turn that holds a Notes request"),
    ("open-sequenced-scope", 'check the gold price, and then in the Personal folder, open my Apple note "Plan"',
     "in the Personal folder", 'open my Apple note "Plan"',
     "the interpretation reads a sequencing leader as opening a step of the request before it ('and then to gold')"),
    ("open-mistyped-coordinator", 'check the gold price, annd in the Personal folder, open my Apple note "Plan"',
     "in the Personal folder", 'open my Apple note "Plan"',
     "the mint's one-typo budget reads only its four-letter connectors, so 'annd' opens no conjunct"),
    ("open-fronted-offset-without-a-binder", "convert 5 USD to EUR, and in 2 hours, what time is it in Nairobi",
     "in 2 hours", "what time is it in Nairobi",
     "the clock family has no whole-text binder, so nothing confirms that the offset belongs to the clock request"),
]

#: Near-misses the grain once moved because the operator family's binder claimed more than its request
#: reads. That binder now reads only the fragments the lane's own reading depends on
#: (`core.operator.request_reading`), so these stay with the request before them -- asserted plainly in
#: STAYS_WITH_THE_REQUEST_BEFORE. The table stays as the record of the over-bound reading.
OVER_BOUND_NEAR_MISSES: list[tuple[str, str, list[str]]] = []

_OVER_BOUND_REASON = (
    "the operator family's binder binds every unit of a turn that holds a Notes request (it reads no unit boundary), so it "
    "confirms the clause opening for a fragment the Notes request never reads"
)


def _unit_holding(spans, text):
    holding = [span for span in spans if text in span.text]
    assert len(holding) == 1, f"{text!r} is in {len(holding)} execution units: {[span.text for span in spans]}"
    return holding[0]


@pytest.mark.parametrize(("case", "wording", "fragment", "fronted", "before_lane"), BETWEEN_FAMILY,
                         ids=[row[0] for row in BETWEEN_FAMILY])
def test_a_fragment_between_requests_rides_the_request_whose_clause_it_opens(case, wording, fragment, fronted, before_lane):
    # The mint cuts the fragment as a unit of its own between two requests: the case exercises the grain.
    requests = interpret_request(wording).requests
    assert any(unit.text.strip(" ,.?").endswith(fragment.split(",")[-1].strip()) for unit in requests), (
        case, [unit.text for unit in requests])
    spans = execution_unit_spans(wording)
    assert len(spans) == 2, f"{case}: {[span.text for span in spans]}"
    carrier = _unit_holding(spans, fronted)
    assert fragment in carrier.text, f"{case}: the fragment did not ride the request it fronts: {[span.text for span in spans]}"
    assert carrier is spans[1] and fragment not in spans[0].text, (case, [span.text for span in spans])
    assert len(carrier.member_unit_ids) >= 2, (case, carrier.member_unit_ids)
    coverage = demand_coverage(wording)
    assert coverage.unit_count == 2 and coverage.mixed, (case, coverage.units, coverage.per_unit_lanes)
    # THE AUTHORITY SEAM: the Notes lane reads the unit that carries the scope, the lane of the request before it does not,
    # and no lane may end the turn alone.
    assert LANE_OPERATOR in coverage.per_unit_lanes[1], (case, coverage.per_unit_lanes)
    if before_lane is not None and before_lane != LANE_OPERATOR:
        assert before_lane in coverage.per_unit_lanes[0] and before_lane not in coverage.per_unit_lanes[1], (
            case, coverage.per_unit_lanes)
        assert not lane_may_claim_whole_turn(wording, before_lane), (case, coverage.per_unit_lanes)
    assert not lane_may_claim_whole_turn(wording, LANE_OPERATOR), (case, coverage.per_unit_lanes)


@pytest.mark.parametrize(("case", "wording", "units"), STAYS_WITH_THE_REQUEST_BEFORE,
                         ids=[row[0] for row in STAYS_WITH_THE_REQUEST_BEFORE])
def test_a_fragment_that_opens_no_clause_of_the_request_after_it_stays_with_the_request_before_it(case, wording, units):
    assert [span.text for span in execution_unit_spans(wording)] == units, case


def test_the_unit_of_measure_stays_with_the_forecast_it_modifies():
    wording = "what is the weather in Rome, in celsius please, tell me also the gold price"
    coverage = demand_coverage(wording)
    assert coverage.unit_count == 1, coverage.units
    assert "in celsius please" in coverage.units[0][1] and "weather in Rome" in coverage.units[0][1]
    assert LANE_LIVE_DATA in coverage.per_unit_lanes[0], coverage.per_unit_lanes


@pytest.mark.parametrize(
    ("case", "wording", "fragment", "fronted", "reason"),
    [pytest.param(*row, marks=pytest.mark.xfail(strict=True, reason=row[4])) for row in OPEN_BETWEEN_CASES],
    ids=[row[0] for row in OPEN_BETWEEN_CASES],
)
def test_open_between_cases_keep_the_look_back_reading(case, wording, fragment, fronted, reason):
    carrier = _unit_holding(execution_unit_spans(wording), fronted)
    assert fragment in carrier.text, f"{case}: {reason}"


@pytest.mark.parametrize(
    ("case", "wording", "units"),
    [pytest.param(*row, marks=pytest.mark.xfail(strict=True, reason=_OVER_BOUND_REASON)) for row in OVER_BOUND_NEAR_MISSES],
    ids=[row[0] for row in OVER_BOUND_NEAR_MISSES],
)
def test_a_binder_that_binds_every_unit_moves_a_near_miss(case, wording, units):
    assert [span.text for span in execution_unit_spans(wording)] == units, case
