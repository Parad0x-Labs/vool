"""A rejected expression must never change the identities of later expressions."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.conductor.node import ConductorNode
from core.conductor.operations import _quantitative_render, _quantitative_run
from core.conductor.registry import NodeContext
from core.conductor.shared_context import extract_shared_context


def run_recipe(text: str, recipe: dict, *, derived: dict | None = None) -> dict:
    return _quantitative_run(
        ConductorNode(node_id="identity", operation="quantitative_reasoning", request_text=text,
                      arguments={"clause": text}),
        NodeContext(shared_context=extract_shared_context(text), derived_facts=derived or {},
                    run_generation=lambda _s, _p: json.dumps(recipe)),
    )


@pytest.mark.parametrize("bad_step", [
    {"label": "rejected", "expression": "999 + 1", "unit": "crates"},
    {"label": "rejected", "expression": "missing + 1", "unit": "crates"},
    None,
])
def test_failed_or_malformed_step_does_not_renumber_later_steps(bad_step) -> None:
    result = run_recipe("There are 120 crates, with 30 damaged. Calculate remaining inventory.", {
        "steps": [
            {"label": "remaining", "expression": "120 - 30", "unit": "crates"},
            bad_step,
            {"label": "ratio", "expression": "120 / 30", "unit": ""},
            {"label": "valid descendant", "expression": "step_3 + step_1", "unit": ""},
            {"label": "invalid descendant", "expression": "step_2 + 1", "unit": ""},
        ],
    })
    assert result["values"]["valid descendant"] == 94
    assert [step["step_id"] for step in result["steps"]] == ["step_1", "step_3", "step_4"]
    assert "invalid descendant" not in result["values"]
    assert any("step_2" in reason for reason in result["cannot_determine"])
    rendered = _quantitative_render(ConductorNode(
        node_id="render", operation="quantitative_reasoning", request_text="inventory",
    ), result)
    assert "| valid descendant | `(ratio) + (remaining)` | 94 |" in rendered


def test_captured_nemotron_recipe_keeps_original_step_references() -> None:
    root = Path(__file__).resolve().parents[1]
    evidence = json.loads((root / "tests/fixtures/model_orchestration_boundary/portfolio-nemotron-3.5-lightning-free.json").read_text())
    recipe = json.loads(evidence["calls"][2]["output"])
    result = run_recipe((root / "tests/fixtures/portfolio_last_run.txt").read_text(), recipe)
    # The model's premium formulas remain wrong; this only proves faithful execution
    # and preservation of independent steps after the rejected unit conversions.
    assert result["values"]["step_13"] == 272.5
    assert result["values"]["step_14"] == pytest.approx(0.0218)
    assert not any("'step_12'" in reason for reason in result["cannot_determine"])


def test_all_valid_step_references_keep_their_existing_meaning() -> None:
    result = run_recipe("Calculate allocations from 120 crates and 30 damaged crates.", {
        "steps": [
            {"label": "remaining", "expression": "120 - 30"},
            {"label": "restored", "expression": "step_1 + 30"},
        ],
    })
    assert result["values"] == {"remaining": 90, "restored": 120}
    assert result["cannot_determine"] == []


def test_upstream_step_name_cannot_fill_a_rejected_local_position() -> None:
    result = run_recipe("Calculate remaining inventory from 120 crates and 30 damaged crates.", {
        "steps": [
            {"label": "rejected", "expression": "999 + 1"},
            {"label": "invalid descendant", "expression": "step_1 + 1"},
        ],
    }, derived={"step_1": 900})
    assert "invalid descendant" not in result["values"]
    assert any("step_1" in reason for reason in result["cannot_determine"])


def test_qualified_dependency_symbols_are_still_available() -> None:
    result = run_recipe("Calculate remaining inventory from 120 crates and 30 damaged crates.", {
        "steps": [{"label": "remaining", "expression": "depot_total - 30"}],
    }, derived={"depot_total": 120})
    assert result["values"] == {"remaining": 90}


def test_renderer_does_not_rescan_labels_or_replace_parts_of_dependency_names() -> None:
    result = {"steps": [
        {"step_id": "step_1", "label": "amount", "expression": "120 - 30", "value": 90},
        {"step_id": "step_3", "label": "step_1 reserve", "expression": "120 / 30", "value": 4},
        {"step_id": "step_4", "label": "total", "expression": "step_3 + depot_step_1", "value": 124},
    ]}
    rendered = _quantitative_render(ConductorNode(
        node_id="render", operation="quantitative_reasoning", request_text="inventory",
    ), result)
    assert "| total | `(step_1 reserve) + depot_step_1` | 124 |" in rendered


def test_legacy_contiguous_steps_still_render_their_original_references() -> None:
    result = {"steps": [
        {"label": "remaining", "expression": "120 - 30", "value": 90},
        {"label": "restored", "expression": "step_1 + 30", "value": 120},
    ]}
    rendered = _quantitative_render(ConductorNode(
        node_id="render", operation="quantitative_reasoning", request_text="inventory",
    ), result)
    assert "| restored | `(remaining) + 30` | 120 |" in rendered
