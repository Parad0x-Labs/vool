"""The plain lane's per-unit publication support (F48/F11, final-pack turn 14).

MEASURED. Frozen build 3e1bf797, turn 18 served by the plain lane after a conductor decline:
"5 + 5 = 10" and "1989" refused WHOLE beside an invented Emperor middle name. Final pack on
6c661fff, turn 14: "Compare electric cars and gasoline cars on purchase cost, range and
emissions." -- stable by the requirements authority's own reading -- refused whole after an
optional enrichment search found nothing. Neither channel the gate reads (computed values, stable
knowledge) had been fed, so nothing could survive.

These pins hold the three anchors, adversarially: a current clause records nothing; an
ineligible author exempts nothing; the runtime's own arithmetic supports a right "5+5 = 10" and
withholds a wrong "5+5 = 11"; an introduced non-year figure in a stable line is withheld; a
single current unit (turn 17's shape) is untouched; the world-facts comparison publishes its
prose and drops its invented figures (the pin `test_stable_knowledge_publishes_when_research_
finds_nothing_served` asserts exactly that).
"""

from __future__ import annotations

import pytest

from core.grounding_lifecycle import GroundingLifecycle, TurnIdentity
from core.grounding_publication import publication_verdict
from core.plain_lane_stable_units import derive_plain_lane_unit_entries, unit_is_stable

TURN_18 = (
    "Answer all three: What is 5+5? What is the exact middle name of the current Emperor of "
    "Japan? In what year did the Berlin Wall fall?"
)
ANSWER_18 = (
    "5+5 = 10.\n"
    "The exact middle name of the current Emperor of Japan is Hirofumi.\n"
    "The Berlin Wall fell in 1989."
)
TURN_14 = "Compare electric cars and gasoline cars on purchase cost, range and emissions."
ANSWER_14 = (
    "Electric cars and gasoline cars differ on purchase cost, range and emissions.\n"
    "Purchase cost: electric cars usually cost more to buy, while gasoline cars are cheaper up front.\n"
    "Range: gasoline cars generally travel farther per fill than electric cars do per charge.\n"
    "Emissions: electric cars produce no tailpipe emissions; gasoline cars emit CO2 and pollutants.\n"
    "In 1974 the average gasoline car cost 37 million in today's money."
)


def _lifecycle(request: str, stable, computed) -> GroundingLifecycle:
    return GroundingLifecycle(
        lifecycle_id="plain-lane-units",
        identity=TurnIdentity(),
        request_text=request,
        model_authored=True,
        retrieval_outcome="unavailable",
        stable_knowledge=tuple(stable),
        computed_values=tuple(computed),
    )


def _publish(request: str, answer: str, *, eligible: bool):
    stable, computed = derive_plain_lane_unit_entries(
        request_text=request, answer_text=answer, author_eligible=eligible, serving_model="m"
    )
    return publication_verdict(_lifecycle(request, stable, computed), answer), stable, computed


def _body(verdict) -> str:
    """The published bytes ABOVE the withheld-work notice (the notice names what was removed)."""
    from core.grounding_publication import UNSUPPORTED_WORK_NOTICE_LEAD

    return str(verdict.content).split(UNSUPPORTED_WORK_NOTICE_LEAD, 1)[0]


def test_the_units_are_read_by_the_authority_not_by_the_turn() -> None:
    assert unit_is_stable("In what year did the Berlin Wall fall?")
    assert unit_is_stable("What is 5+5?")
    assert not unit_is_stable("What is the exact middle name of the current Emperor of Japan?")
    assert not unit_is_stable("What is the latest stable version of Python?")
    assert not unit_is_stable("What is the exact number of grains of sand on Earth right now?")


def test_the_mixed_turn_publishes_its_stable_clauses_and_withholds_the_current_one() -> None:
    verdict, stable, computed = _publish(TURN_18, ANSWER_18, eligible=True)
    assert [e["demand_text"] for e in stable] == ["In what year did the Berlin Wall fall?"]
    assert computed and "10" in computed[0]["summary"], computed
    body = _body(verdict)
    assert "1989" in body
    assert "5+5 = 10" in body
    assert "Hirofumi" not in body, body
    assert verdict.state != "refused", verdict.state
    assert any("Hirofumi" in claim for claim in verdict.withheld_claims)


def test_an_ineligible_author_exempts_nothing_but_the_runtime_arithmetic_still_stands() -> None:
    verdict, stable, computed = _publish(TURN_18, ANSWER_18, eligible=False)
    assert stable and stable[0]["author_eligible"] is False
    body = _body(verdict)
    assert "1989" not in body, "no policy verdict, no exemption"
    assert "5+5 = 10" in body, "a runtime computation supports the line regardless of author"


def test_a_wrong_arithmetic_line_is_withheld_by_the_runtime_value() -> None:
    wrong = ANSWER_18.replace("5+5 = 10.", "5+5 = 11.")
    verdict, _stable, computed = _publish(TURN_18, wrong, eligible=True)
    assert computed and "10" in computed[0]["summary"]
    body = _body(verdict)
    assert "5+5 = 11" not in body
    assert "1989" in body


def test_an_introduced_non_year_figure_in_a_stable_line_is_withheld() -> None:
    padded = ANSWER_18.replace(
        "The Berlin Wall fell in 1989.", "The Berlin Wall fell in 1989, when 2,300 people crossed."
    )
    verdict, stable, _computed = _publish(TURN_18, padded, eligible=True)
    assert stable, "the unit still binds its line"
    assert "2,300" not in _body(verdict)


def test_a_single_current_unit_records_nothing_and_stays_refused() -> None:
    request = "What is the exact number of grains of sand on Earth right now?"
    answer = "The exact number of grains of sand on Earth is unknown; estimates are around 7.5 quintillion."
    verdict, stable, computed = _publish(request, answer, eligible=True)
    assert stable == [] and computed == []
    assert verdict.state == "refused"


def test_the_world_facts_comparison_publishes_its_prose_and_drops_its_figures() -> None:
    verdict, stable, _computed = _publish(TURN_14, ANSWER_14, eligible=True)
    assert len(stable) == 1 and stable[0]["author_eligible"] is True
    body = _body(verdict)
    assert "tailpipe" in body and "cost more to buy" in body
    assert "37 million" not in body and "1974" not in body
    assert verdict.state != "refused"


def test_a_stable_unit_with_no_answering_line_records_nothing() -> None:
    _verdict, stable, computed = _publish(
        "In what year did the Berlin Wall fall? What is 5+5?", "I am not sure about either.", eligible=True
    )
    assert stable == []
    assert computed and "10" in computed[0]["summary"], "the runtime still computes what it can"


def test_markdown_emphasis_and_list_markers_do_not_defeat_the_demand_join() -> None:
    """SERVED (c5463c47, forced-decline turn 18 and the turn-14 comparison): the model wrote
    "3. **The Berlin Wall fell in 1989**." and "- **Electric Cars**: Higher upfront cost ...";
    the claims (markers stripped) were not found inside the raw recorded render, so every stable
    line was withheld on formatting alone."""
    answer = (
        "1. **5 + 5 = 10**.\n"
        "2. The current Emperor of Japan is Emperor Naruhito. His exact middle name is Kunihiko.\n"
        "3. **The Berlin Wall fell in 1989**."
    )
    verdict, stable, _computed = _publish(TURN_18, answer, eligible=True)
    assert stable and "1989" in stable[0]["summary"]
    body = _body(verdict)
    assert "1989" in body and "5 + 5 = 10" in body
    assert "Kunihiko" not in body
    answer_14 = (
        "### **Purchase Cost**\n"
        "- **Electric Cars**: Higher upfront cost due to battery technology, but lower long-term expenses.\n"
        "- **Gasoline Cars**: Lower initial price, but higher ongoing costs from fuel and maintenance.\n"
        "### **Emissions**\n"
        "- **Electric Cars**: Zero tailpipe emissions; gasoline cars emit CO2 and other pollutants.\n"
        "- Electric Cars: Typically 200-300 miles on a full charge."
    )
    verdict_14, stable_14, _c = _publish(TURN_14, answer_14, eligible=True)
    assert stable_14
    body_14 = _body(verdict_14)
    assert "Higher upfront cost" in body_14 and "Zero tailpipe emissions" in body_14
    assert "200-300" not in body_14
