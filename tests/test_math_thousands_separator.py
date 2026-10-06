"""Numbers written with thousands separators are numbers, and an ambiguous comma is not a guess.

Measured on 9adff83: "What is 17% of 2,340?" was answered "17% of 2 = 0.34" at 0.99 confidence --
the deterministic calculator read the separator as the end of the operand. The calculator exists so
arithmetic is never settled by a guess; an operand it cannot read exactly must go to the model.
"""

from __future__ import annotations

import pytest

from core.task_router import evaluate_direct_math_request


@pytest.mark.parametrize(
    ("question", "answer"),
    [
        ("What is 17% of 2,340?", "17% of 2340 = 397.8."),
        ("what is 1,234,567.5 + 2?", "1234567.5 + 2 = 1234569.5."),
        ("2,340 * 3", "2340 * 3 = 7020."),
        ("what is 12,345 / 5", "12345 / 5 = 2469."),
        ("square root of 1,444", "The square root of 1444 is 38."),
        ("1,200 squared", "1200^2 = 1440000."),
        # Six significant digits used to round a correct answer away: "123457".
        ("10% of 1,234,567.5", "10% of 1234567.5 = 123456.75."),
        ("5% of 200", "5% of 200 = 10."),
    ],
)
def test_grouped_numbers_are_read_whole(question, answer):
    assert evaluate_direct_math_request(question) == answer


@pytest.mark.parametrize(
    "question",
    ["what is 2,34 + 1?", "17% of 2,34", "what is 3,5 * 2", "what is 1,000,00 + 1", "square root of 1,44", "2,3456 squared"],
)
def test_an_ambiguous_comma_fails_closed_to_the_model(question):
    assert evaluate_direct_math_request(question) is None


def test_a_comma_that_separates_items_is_not_a_separator():
    # "200, 300" is a list, not 200300; the first operand is complete and exact.
    assert evaluate_direct_math_request("5% of 200, 300 and 400") == "5% of 200 = 10."
