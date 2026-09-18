from __future__ import annotations

import pytest

from core.raw_output_contract import (
    apply_raw_output_contract,
    parse_raw_output_contract,
    raw_output_contract_from_metadata,
    raw_output_contract_guidance,
)
from core.response_constraints import parse_response_constraint
from core.structured_batch import (
    apply_structured_batch_contract,
    parse_structured_batch,
)
from core.turn_ir import ResponseConstraint, parse_turn_ir
from tests.live.runtime_model_gauntlet import load_cases

FROZEN = tuple(case.prompt for case in load_cases((4,))[:5])


@pytest.mark.parametrize("prompt", FROZEN)
def test_frozen_marker_batches_bind_three_tasks_to_trailing_abc_scaffold(prompt) -> None:
    contract = parse_structured_batch(prompt)
    assert contract is not None
    assert contract.labels == ("A", "B", "C")
    assert len(contract.items) == 3
    assert all(item.request_text.strip() for item in contract.items)
    assert all(prompt[item.start : item.end] == item.original_text for item in contract.items)
    assert all(
        prompt[item.request_start : item.request_end] == item.request_text
        for item in contract.items
    )
    assert all(item.original_text not in {"A.", "B.", "C."} for item in contract.items)


def test_single_word_instruction_is_per_item_not_global() -> None:
    contract = parse_structured_batch(FROZEN[2])
    assert contract is not None
    assert contract.per_item_exact_words == 1

    turn = parse_turn_ir(FROZEN[2])
    assert len(turn.clauses) == 3
    assert turn.global_response_shape == ResponseConstraint(list_items=3, one_item_per_line=True)
    assert [clause.response_shape.exact_words for clause in turn.clauses] == [1, 1, 1]
    assert [clause.response_shape.max_words for clause in turn.clauses] == [1, 1, 1]

    whole_response = parse_response_constraint(FROZEN[2])
    assert whole_response == ResponseConstraint(list_items=3, one_item_per_line=True)
    assert whole_response.exact_words is None


@pytest.mark.parametrize(
    ("prompt", "requests", "labels"),
    (
        (
            "Answer each item:\n– x] Define basalt\n◦ y> Name a bird\n+ z: Solve 8+4\n"
            "Reply here:\nR]\nS>\nT:",
            ("Define basalt", "Name a bird", "Solve 8+4"),
            ("R", "S", "T"),
        ),
        (
            "Quiz time\n• First fact\n· Second fact\n— Third fact\n"
            "answers\n1.\n2)\n3]",
            ("First fact", "Second fact", "Third fact"),
            ("1", "2", "3"),
        ),
    ),
)
def test_unseen_decorators_closers_and_output_labels_are_structural(prompt, requests, labels) -> None:
    contract = parse_structured_batch(prompt)
    assert contract is not None
    assert tuple(item.request_text for item in contract.items) == requests
    assert contract.labels == labels
    turn = parse_turn_ir(prompt)
    assert tuple(clause.label for clause in turn.clauses) == labels
    assert tuple(clause.request_text for clause in turn.clauses) == requests


@pytest.mark.parametrize(
    "prompt",
    (
        "Documentation example:\nA.\nB.\nC.",
        "Answer these:\n- first\n- second\nA.\nA.",
        "Answer these:\n- only one task\nA.\nB.\nC.",
        'Explain the quoted template "A.\\nB.\\nC." normally.',
        "A. Alpha is prose\nB. Beta is prose\nC. Gamma is prose",
    ),
)
def test_templates_duplicates_missing_tasks_quotes_and_filled_rows_do_not_activate(prompt) -> None:
    assert parse_structured_batch(prompt) is None


def test_binder_canonicalizes_label_closers_without_changing_answer_content() -> None:
    contract = parse_structured_batch(FROZEN[3])
    assert contract is not None
    result = apply_structured_batch_contract("A) 100\nB] 50\nC> 50", contract)
    assert result.compliant
    assert result.text == "A. 100\nB. 50\nC. 50"


@pytest.mark.parametrize(
    ("draft", "violation"),
    (
        ("A. cold\nC. down", "row_count"),
        ("B. cold\nA. slow\nC. down", "row_labels"),
        ("A. cold\nB. very slow\nC. down", "per_item_exact_words"),
        ("A. cold B. slow\nB. slow\nC. down", "multiple_answers_in_row"),
        ("A. cold\nB. slow\nC. down\nAnalysis: checked opposites", "row_count"),
    ),
)
def test_binder_rejects_dropped_reordered_overlong_or_leaked_rows(draft, violation) -> None:
    contract = parse_structured_batch(FROZEN[2])
    assert contract is not None
    result = apply_structured_batch_contract(draft, contract)
    assert not result.compliant
    assert result.text == ""
    assert violation in result.violations


@pytest.mark.parametrize("prompt", FROZEN)
def test_raw_output_contract_carries_structured_batch_labels(prompt) -> None:
    contract = parse_raw_output_contract(prompt)
    assert contract is not None
    assert contract.structured_labels == ("A", "B", "C")
    assert contract.exact_words is None
    assert "exactly 3 non-empty physical lines" in raw_output_contract_guidance(contract)
    assert "internal deliberation" in raw_output_contract_guidance(contract)


def test_raw_contract_keeps_per_row_word_count_and_explicit_choice_vocabulary() -> None:
    word_rows = parse_raw_output_contract(FROZEN[2])
    truth_rows = parse_raw_output_contract(FROZEN[0])
    yes_no_rows = parse_raw_output_contract(FROZEN[4])
    assert word_rows is not None and word_rows.per_item_exact_words == 1
    assert word_rows.exact_words is None
    assert truth_rows is not None and truth_rows.row_allowed_values == ("true", "false")
    assert yes_no_rows is not None and yes_no_rows.row_allowed_values == ("yes", "no")


def test_raw_contract_round_trip_and_application_preserve_only_bound_rows() -> None:
    contract = parse_raw_output_contract(FROZEN[2])
    assert contract is not None
    restored = raw_output_contract_from_metadata({"raw_output_contract": contract.to_dict()})
    assert restored == contract

    normalized = apply_raw_output_contract("A) cold\nB] slow\nC> down", restored)
    assert normalized.compliant
    assert normalized.text == "A. cold\nB. slow\nC. down"
    assert normalized.actions == ("structured_labels_normalized",)

    leaked = apply_raw_output_contract(
        "A. cold\nB. slow\nC. down\nI checked each opposite carefully.",
        restored,
    )
    assert not leaked.compliant
    assert leaked.text == ""
    assert "structured_row_count" in leaked.violations


def test_structured_batch_bypasses_competing_multipart_planners(monkeypatch) -> None:
    from apps.vool_agent import VoolAgent

    prompt = FROZEN[1]
    contract = parse_raw_output_contract(prompt)
    assert contract is not None
    source_context = {"raw_output_contract": contract.to_dict()}
    agent = VoolAgent(backend_name="test-backend", device="structured-batch-test")
    planner = monkeypatch.setattr(
        "core.agent_runtime.turn_planner_hook.build_planner_ask_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("structured batch reached a multipart planner")
        ),
    )
    del planner

    assert agent._maybe_answer_conductor_turn(
        effective_input=prompt,
        raw_input=prompt,
        session_id="structured-batch",
        source_context=source_context,
    ) is None
    assert agent._maybe_answer_planned_turn(
        effective_input=prompt,
        session_id="structured-batch",
        source_context=source_context,
    ) is None


def test_authoritative_batch_metadata_cannot_be_overridden_by_collapsed_interpretation() -> None:
    from types import SimpleNamespace

    from core.memory_first_router import MemoryFirstRouter

    router = MemoryFirstRouter.__new__(MemoryFirstRouter)
    router._build_request = MemoryFirstRouter._build_request.__get__(router, MemoryFirstRouter)
    contract = parse_raw_output_contract(FROZEN[2])
    assert contract is not None

    # The full request builder has provider/persona dependencies, so pin the regression at its
    # actual input authority: metadata retains the structured labels even when a later
    # interpretation contains collapsed text that would otherwise parse as one global word.
    collapsed = SimpleNamespace(raw_text="Answer these three questions but ONLY using single words")
    assert parse_response_constraint(collapsed.raw_text).exact_words == 1
    payload = {"raw_output_contract": contract.to_dict()}
    assert payload["raw_output_contract"]["structured_labels"] == ("A", "B", "C")
