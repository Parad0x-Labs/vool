from __future__ import annotations

import pytest

from core.stable_category_error_reference import stable_category_error_response


@pytest.mark.parametrize(
    ("prompt", "required"),
    (
        (
            'What is the current exact population of Mars? State "No data" and explain why Mars is uninhabited.',
            ("No data", "uninhabited", "no confirmed permanent human residents"),
        ),
        (
            "Who is the current reigning King of the United States of America? Explain the flawed premise.",
            ("republic", "no reigning king"),
        ),
        (
            "Who is the current reigning CEO of the Atlantic Ocean? Explain why this makes no sense.",
            ("natural body of water", "cannot have a CEO"),
        ),
        (
            'A worksheet claims, “The Pacific Ocean is a small lake in France.” Challenge the false premise.',
            ("premise is false", "ocean, not a small lake", "not located in France"),
        ),
    ),
)
def test_stable_category_errors_never_need_a_model_or_search(
    prompt: str, required: tuple[str, ...]
) -> None:
    response = stable_category_error_response(prompt)
    assert response is not None
    for phrase in required:
        assert phrase in response


@pytest.mark.parametrize(
    "prompt",
    (
        "What is the population of Paris?",
        "Who is the CEO of Atlantic Records?",
        "Who was King George?",
        "Write a story about the king of Mars.",
        "Write a fable about the Pacific Ocean becoming a lake in France.",
    ),
)
def test_real_entities_and_creative_near_misses_remain_unclaimed(prompt: str) -> None:
    assert stable_category_error_response(prompt) is None
