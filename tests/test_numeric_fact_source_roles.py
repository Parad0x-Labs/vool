from pathlib import Path

import pytest

from core.conductor.shared_context import (
    UngroundedExpressionError,
    evaluate_grounded_expression,
    extract_numeric_facts,
)


def test_portfolio_labels_are_not_grounded_arithmetic_operands():
    text = (Path(__file__).parent / "fixtures/portfolio_last_run.txt").read_text()
    facts = extract_numeric_facts(text)
    assert [f.value for f in facts] == [
        12500, 25, 75, 55, 45, 1.312, 41.8, 1485, 0.8, 3, 2.2, 12500,
    ]
    assert evaluate_grounded_expression("fact_1 * fact_2 / 100", facts=facts) == 3125


@pytest.mark.parametrize("labels", [(73, 74), (91, 92)])
def test_new_kiln_question_keeps_quantities_but_cannot_spend_its_labels(labels):
    text = (
        f"Clay stock is 480 kg with a 12% firing loss.\n"
        f"{labels[0]}. Calculate the discarded mass.\n"
        f"{labels[1]}. Calculate the remaining mass."
    )
    facts = extract_numeric_facts(text)
    assert [f.value for f in facts] == [480, 12]
    assert evaluate_grounded_expression("fact_1 * fact_2 / 100", facts=facts) == 57.6
    assert evaluate_grounded_expression("fact_1 * (1 - fact_2 / 100)", facts=facts) == 422.4
    with pytest.raises(UngroundedExpressionError):
        evaluate_grounded_expression(f"fact_1 / {labels[0]}", facts=facts)


@pytest.mark.parametrize("text, values", [
    ("Calculate 2.5 * 8.", [2.5, 8]),
    ("1. Calculate 1 USD times 4.\n2. Divide 12 kg by 3.", [1, 4, 12, 3]),
    ('Read the literal "73. batch" and calculate 24 / 6.', [73, 24, 6]),
    ("A. Multiply 6 by 7.\nB. Subtract 8 from 50.", [6, 7, 8, 50]),
])
def test_real_content_numbers_keep_original_source_offsets(text, values):
    facts = extract_numeric_facts(text)
    assert [f.value for f in facts] == values
    for fact in facts:
        assert text[fact.position:].lstrip().startswith(fact.raw)
