from pathlib import Path

import pytest

from core.conductor.obligations import clause_residue_obligations
from core.turn_ir import parse_turn_ir

ORIGINAL = (Path(__file__).parent / "fixtures/portfolio_last_run.txt").read_text()
NOVEL = (
    "A tank holds 960 litres. Split it 35% to line A and 65% to line B.\n"
    "Line A loses 8% during filtering. Calculate:\n"
    "1. The initial volume for each line.\n"
    "2. The retained volume on line A after filtering.\n"
    "3. What percentage of the original volume remains on line A."
)


@pytest.mark.parametrize("text", [ORIGINAL, NOVEL])
def test_governing_heading_is_not_a_separate_omitted_request(text):
    turn = parse_turn_ir(text)
    gaps = clause_residue_obligations(text, [c.request_text for c in turn.clauses])
    assert not gaps


def test_unrelated_new_governing_verb_is_not_an_omitted_child():
    text = "Inspect:\n1. Check the roof.\n2. Check the foundation."
    gaps = clause_residue_obligations(text, ["Check the roof."])
    # Existing word overlap cannot establish child completion; the floor remains
    # conservative. This pin only forbids inventing an Inspect heading demand.
    assert all(gap.text != "Inspect:" for gap in gaps)


@pytest.mark.parametrize("text", [
    "Calculate revenue:\n1. Explain costs.",
    "Who wrote Hamlet?\nCalculate:\n1. The volume of each tank.",
    "Inspect:",
    "Calculate:",
    "What happened yesterday?\n1. Explain the next steps.",
])
def test_actual_preamble_requests_and_unscoped_commands_remain_obligations(text):
    turn = parse_turn_ir(text)
    carried = [c.request_text for c in turn.clauses] if turn.preamble else []
    assert clause_residue_obligations(text, carried)


def test_omitted_explicit_child_is_still_reported():
    text = "Calculate:\n1. Calculate 13 * 9.\n2. Compute 88 / 4."
    gaps = clause_residue_obligations(text, ["Calculate 13 * 9."])
    assert len(gaps) == 1
    assert "Compute 88 / 4" in gaps[0].text


@pytest.mark.parametrize("text", [ORIGINAL, NOVEL, "Inspect:\n1. Check the roof.\n2. Check the foundation."])
def test_governing_source_span_does_not_rewrite_request(text):
    turn = parse_turn_ir(text)
    start, end = turn.governing_instruction_span
    assert text[start:end] in {"Calculate:", "Inspect:"}
    assert turn.source_text == text
    assert all(text[c.request_start:c.request_end] == c.request_text for c in turn.clauses)
