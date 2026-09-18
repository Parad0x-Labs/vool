"""Ambiguous computed operands get bounded correction, then ordinary validation."""
import json
from pathlib import Path

import pytest

from core.conductor.node import ConductorNode, NodeLifecycle, NodeOutcome
from core.conductor.operations import _quantitative_run
from core.conductor.registry import NodeContext
from core.conductor.scheduler import _context_for
from core.conductor.shared_context import extract_shared_context

ROOT = Path(__file__).resolve().parents[1]
LIVE = json.loads((ROOT / "tests/fixtures/model_orchestration_boundary/portfolio-ling-3.0-flash-scheduled-74a272a1.json").read_text())
NOVEL = ("A depot receives 840 cartons. Allocate 40% to restoration and the rest to storage. "
         "Restoration rejects 10% of its allocation. Calculate the usable restoration cartons.")


def run_case(text, clause, previous, recipes):
    calls = []

    def generate(system, briefing):
        calls.append((system, briefing))
        assert len(calls) <= 2, "correction must be bounded"
        return recipes[len(calls) - 1]

    source = ConductorNode("allocation", "quantitative_reasoning", "Calculate allocations")
    prior = NodeOutcome(source, state=NodeLifecycle.SUCCEEDED, result=previous)
    target = ConductorNode("quantity", "quantitative_reasoning", clause,
                           arguments={"clause": clause}, depends_on=("allocation",))
    base = NodeContext(shared_context=extract_shared_context(text), run_generation=generate)
    return _quantitative_run(target, _context_for(target, base, {"allocation": prior})), calls


def depot_prior():
    return {"values": {"Restoration allocation": 336, "Storage allocation": 504},
            "steps": [{"label": label, "value": value, "unit_authority": "source_expression",
                       "unit_dimensions": {"carton": 1}}
                      for label, value in [("Restoration allocation", 336), ("Storage allocation", 504)]]}


def recipe(expression):
    return json.dumps({"steps": [{"label": "Requested result", "expression": expression}]})


@pytest.mark.parametrize("owner", [True, False], ids=["owner-verbatim", "new-depot"])
def test_wrong_operand_is_corrected_before_execution(owner):
    if owner:
        text, clause = LIVE["calls"][0]["prompt"], "How many British pounds I receive after the FX fee."
        prior, bad = LIVE["outcomes"][0]["result"], LIVE["calls"][2]["output"]
        correct, expected = "dependency_1_value_1 * (1 - fact_9_share) / fact_6", 2362.80487804878
    else:
        text, clause, prior = NOVEL, "Calculate the usable restoration cartons.", depot_prior()
        bad = recipe("dependency_1_value_2 * (1 - fact_3_share)")
        correct, expected = "dependency_1_value_1 * (1 - fact_3_share)", 302.4
    result, calls = run_case(text, clause, prior, [bad, recipe(correct)])
    assert len(calls) == 2
    assert "Proposed recipe (untrusted; not yet executed)" in calls[1][1]
    assert text in calls[1][1]
    assert result["steps"][-1]["value"] == pytest.approx(expected)
    assert result["recipe_review"] == "model_reconciled"
    assert not result["cannot_determine"]


@pytest.mark.parametrize("review", ["", '{"approved":true}', recipe("remembered_price * 7")])
def test_review_failure_or_invention_does_not_publish_original(review):
    result, calls = run_case(NOVEL, "Calculate the usable restoration cartons.", depot_prior(),
                            [recipe("dependency_1_value_2 * (1 - fact_3_share)"), review])
    assert len(calls) == 2
    assert not result["steps"]
    assert result["cannot_determine"]


def test_correct_recipe_survives_and_review_is_not_numeric_authority():
    correct = recipe("dependency_1_value_1 * (1 - fact_3_share)")
    result, calls = run_case(NOVEL, "Calculate the usable restoration cartons.", depot_prior(), [correct, correct])
    assert len(calls) == 2
    assert result["steps"][0]["value"] == pytest.approx(302.4)
    assert result["steps"][0]["unit_authority"] == "source_expression"


def test_single_dependency_value_buys_no_review():
    prior = depot_prior()
    prior["steps"] = prior["steps"][:1]
    prior["values"] = {"Restoration allocation": 336}
    result, calls = run_case(NOVEL, "Calculate the usable restoration cartons.", prior,
                            [recipe("dependency_1_value_1 * (1 - fact_3_share)")])
    assert len(calls) == 1
    assert result["steps"][0]["value"] == pytest.approx(302.4)
