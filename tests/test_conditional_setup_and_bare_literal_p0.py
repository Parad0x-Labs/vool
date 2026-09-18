"""P0 — the question form of a conditional setup is the same demand; a bare literal is not a step.

THE MEASURED DEFECTS (acceptance 2026-09-08 turns 10 and 7; reproduced on the isolated daemon
2026-09-09)
------------------------------------------------------------------------------------------------
Turn 10: "If I exchange 100 EUR to USD, roughly how much USD do I get?" minted TWO demands --
the conditional setup and its own question -- the second dispatched, failed, and was reported
"Could not be answered: roughly how much USD do I get?" directly under the line that had just
answered exactly that, wrapped in a withheld-notice fragment ("- Retry the turn.)").

Turn 7: "From memory: what is the boiling point of water at sea level in Celsius? Also, what is
10 percent of 250?" -- a quantitative node claimed the boiling-point KNOW clause on the
message's sibling numbers, and the model's plan shipped the bare literal `100` (a NEUTRAL
constant, therefore "grounded") rendered as "Boiling point of water at sea level: 100 = 100 °C"
with a crossed-wires "not determined" note about the sibling. World knowledge laundered through
the arithmetic lane.
"""

from __future__ import annotations

import pytest

from core.agent_runtime.answer_coverage import interpret_request
from core.conductor.shared_context import (
    NumericFact,
    UngroundedExpressionError,
    evaluate_grounded_expression,
)


def _units(text: str) -> list[str]:
    return [unit.text for unit in interpret_request(text).requests]


def test_a_conditional_setup_and_its_question_are_one_demand() -> None:
    assert _units("If I exchange 100 EUR to USD, roughly how much USD do I get?") == [
        "If I exchange 100 EUR to USD, roughly how much USD do I get?"
    ]
    assert _units("When I sell 2 BTC, how much EUR do I receive?") == [
        "When I sell 2 BTC, how much EUR do I receive?"
    ]


def test_an_imperative_continuation_of_a_conditional_still_splits() -> None:
    """The merge is for the question form; a command continuation keeps today's behaviour."""

    units = _units("If you find the file, delete it")
    assert "If you find the file" in units and "delete it" in units


def test_a_question_after_a_conditional_does_not_swallow_its_neighbours() -> None:
    units = _units("If it rains tomorrow, what should I wear? Also, what time is it?")
    assert "If it rains tomorrow, what should I wear?" in units
    assert "what time is it?" in units


def test_plain_coordination_still_mints_per_request() -> None:
    units = _units("Convert 100 EUR to USD. Also tell me the time.")
    assert len(units) == 2


# ------------------------------------------------------------------ the bare-literal expression


def _facts(*values: float) -> list[NumericFact]:
    return [
        NumericFact(
            label=f"v{index}",
            raw=str(value),
            value=value,
            unit="",
            unit_kind="count",
            sentence="",
            position=index,
        )
        for index, value in enumerate(values)
    ]


def test_a_bare_literal_is_not_a_calculation() -> None:
    """`100` as a whole expression asserts a value while computing nothing.

    100 is a neutral constant (the percentage basis), which is why it passed the operand
    grounding and shipped as a derived step for a knowledge question. Neutrality exists for
    operands inside real arithmetic, not for a whole expression that references nothing.
    """

    with pytest.raises(UngroundedExpressionError):
        evaluate_grounded_expression("100", facts=_facts(10.0, 250.0))
    with pytest.raises(UngroundedExpressionError):
        evaluate_grounded_expression("0", facts=_facts(10.0))
    with pytest.raises(UngroundedExpressionError):
        evaluate_grounded_expression("6.85", facts=_facts(10.0))


def test_neutral_constants_still_work_as_operands() -> None:
    assert evaluate_grounded_expression("250 * 10 / 100", facts=_facts(250.0, 10.0)) == pytest.approx(25.0)
    assert evaluate_grounded_expression("10 + 0", facts=_facts(10.0)) == pytest.approx(10.0)


def test_stated_facts_and_symbols_still_evaluate() -> None:
    assert evaluate_grounded_expression("v0 + v1", facts=_facts(10.0, 250.0)) == pytest.approx(260.0)
    assert (
        evaluate_grounded_expression("step_1 / 2", facts=_facts(2.0), symbols={"step_1": 96.0})
        == pytest.approx(48.0)
    )


def test_a_bare_operand_name_is_not_a_calculation_either() -> None:
    """The same assertion wearing an operand's name -- the shape refusing only literals left open.

    Measured live 2026-09-09 on the SAME question, on the rebuilt app: the plan came back with
    `label="boiling_point", expression="fact_2"`. `fact_2` is a stated fact -- 250, from the
    SIBLING clause "10 percent of 250" -- so it grounded cleanly and shipped as

        boiling_point: fact_2 = 250 Celsius

    The runtime asserted in its own voice that water boils at 250 °C. A name restates one operand
    under a new label, which reads MORE derived than a literal, not less. A step that references
    nothing and computes nothing establishes nothing, whichever kind of leaf it is.
    """
    facts = _facts(10.0, 250.0)
    for expression in ("v1", "(v1)", " v0 "):
        with pytest.raises(UngroundedExpressionError):
            evaluate_grounded_expression(expression, facts=facts)
    # Including a name that resolves to an already-computed step: carrying a value forward under a
    # new label is not a derivation of anything.
    with pytest.raises(UngroundedExpressionError):
        evaluate_grounded_expression("step_1", facts=facts, symbols={"step_1": 96.0})


def test_the_refusal_does_not_touch_arithmetic_that_uses_those_operands() -> None:
    """Guard against fixing the leak by closing the lane: real arithmetic over the same names."""
    facts = _facts(10.0, 250.0)
    assert evaluate_grounded_expression("v0 / 100 * v1", facts=facts) == pytest.approx(25.0)
    assert evaluate_grounded_expression("v1 - v0", facts=facts) == pytest.approx(240.0)
    assert (
        evaluate_grounded_expression("step_1 / 2", facts=_facts(2.0), symbols={"step_1": 96.0})
        == pytest.approx(48.0)
    )
