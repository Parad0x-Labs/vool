"""Downstream computation sees executed derivations without importing their namespaces."""
import json
from pathlib import Path

import pytest

from core.conductor.node import ConductorNode, NodeLifecycle, NodeOutcome
from core.conductor.operations import _quantitative_run
from core.conductor.registry import NodeContext, computed_dependency_bindings
from core.conductor.scheduler import _context_for
from core.conductor.shared_context import extract_shared_context

ROOT = Path(__file__).resolve().parents[1]
LIVE = json.loads((ROOT / "tests/fixtures/model_orchestration_boundary/portfolio-ling-3.0-flash-scheduled-3ea7f5e1.json").read_text())


def test_original_platinum_derivation_preserves_net_base_and_original_step_scope():
    prior = next(o["result"] for o in LIVE["outcomes"] if "Platinum net grams" in (o.get("result") or {}).get("values", {}))
    bindings = computed_dependency_bindings({"platinum": prior})
    net = next(b for b in bindings.values() if b["label"] == "Platinum net troy ounces after premium")
    assert net["derivation"]["expression"] == "step_1 / (1 + fact_11_share)"
    assert net["derivation"]["source_scope_bindings"]["step_1"] == pytest.approx(4218.75 / 1485)
    assert net["derivation"]["source_scope_bindings"]["fact_11_share"] == pytest.approx(.022)
    assert "step_1" not in bindings


def test_novel_included_service_charge_derivation_reaches_real_generation_briefing():
    text = ("A workshop pays 924 EUR including a 10% service charge. "
            "Calculate the base cost and the included service charge.")
    previous = {"steps": [
        {"step_id": "step_1", "label": "Base cost", "expression": "fact_1 / (1 + fact_2_share)",
         "value": 840.0, "unit_authority": "source_expression", "unit_dimensions": {"EUR": 1}},
        {"step_id": "step_3", "label": "Base cost crosscheck", "expression": "step_1 * 1",
         "value": 840.0, "unit_authority": "source_expression", "unit_dimensions": {"EUR": 1}}],
        "expression_bindings": {"fact_2_share": .1}}
    parent = ConductorNode("base", "quantitative_reasoning", "Calculate base cost")
    target = ConductorNode("charge", "quantitative_reasoning", "Calculate the included service charge.",
                           depends_on=("base",))
    calls = []

    def generate(_system, briefing):
        calls.append(briefing)
        assert "Executed dependency derivations" in briefing
        assert "fact_1 / (1 + fact_2_share)" in briefing
        assert "source_scope_bindings" in briefing
        return json.dumps({"steps": [{"label": "Included charge", "expression": "fact_1 - dependency_1_value_1"}]})

    context = NodeContext(shared_context=extract_shared_context(text), run_generation=generate)
    result = _quantitative_run(target, _context_for(target, context, {
        "base": NodeOutcome(parent, state=NodeLifecycle.SUCCEEDED, result=previous)}))
    assert len(calls) == 2
    assert result["steps"][0]["value"] == 84
    assert "step_3" not in result["expression_bindings"]


def test_missing_original_step_is_not_rebound_by_list_position():
    result = {"steps": [
        {"step_id": "step_3", "label": "Retained", "value": 7,
         "unit_authority": "source_expression", "unit_dimensions": {"kg": 1}},
        {"step_id": "step_4", "label": "Double", "value": 14, "expression": "step_3 * 2",
         "unit_authority": "source_expression", "unit_dimensions": {"kg": 1}}]}
    bindings = computed_dependency_bindings({"stock": result})
    assert bindings["dependency_1_value_2"]["derivation"]["source_scope_bindings"] == {"step_3": 7}
    assert "step_1" not in str(bindings)


def test_nonfinite_and_untrusted_derivations_are_not_promoted():
    result = {"steps": [{"label": "Unknown", "value": 12, "expression": "secret * 3"}]}
    assert "derivation" not in computed_dependency_bindings({"x": result})["dependency_1_value_1"]
    result["steps"][0].update(unit_authority="source_expression", unit_dimensions={"kg": 1})
    result["expression_bindings"] = {"secret": float("inf"), "step_1": 99}
    binding = computed_dependency_bindings({"x": result})["dependency_1_value_1"]
    assert binding["derivation"]["source_scope_bindings"] == {}
