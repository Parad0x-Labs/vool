"""Possessive quantity recalls keep their session context (plain/mixed routing).

Measured on head 9174b42c (q90-routing, V corpus F15-05's mixed shape, 2026-09-29):
"How much is my copay and how much is my deductible?" -- every clause is a
possessive quantity recall, none carries the inverted-auxiliary shape
("do I", "did I"), and the possessive recall frame's head list read
what/which/when/where/who/whose/why without the quantity interrogatives. The
turn classed ``multi_part_qa`` whose minimal route loads NO session history and
NO capsule, so stored facts could not reach the answering route -- the same
misroute the module's own paid-calibration note documents for the auxiliary
shape. ``how much``/``how many`` join the head class; bare ``how`` stays
excluded (its "how ... my X" reading is a how-to), and world QA without a
possessive stays a plain task.
"""

from __future__ import annotations

import pytest

from core.plain_task_routing import plain_task_kind


@pytest.mark.parametrize(
    "question",
    (
        "How much is my copay and how much is my deductible?",
        "How much gravel was in my yard in 2019, and how much is in my yard at the moment?",
        "How many pallets are in my warehouse and how many are in my container?",
        "How much did my roof repair cost and how much is my deductible?",
    ),
)
def test_possessive_quantity_questions_are_recall_not_plain_tasks(question: str) -> None:
    assert plain_task_kind(question) == "", question


@pytest.mark.parametrize(
    "question",
    (
        "How much is a coffee in Rome and how much is a croissant?",  # world QA, no possessive
        "How much should I tip?",  # advice modal
        "Translate my shopping list into Lithuanian.",  # translation keeps its plain kind
    ),
)
def test_non_possessive_and_instructional_shapes_keep_their_plain_kinds(question: str) -> None:
    if question.startswith("How much should"):
        assert plain_task_kind(question) == ""  # single advice question: not a plain TASK kind
    else:
        assert plain_task_kind(question) != "", question


def test_bare_how_possessive_is_still_a_howto_not_recall() -> None:
    # The deliberate exclusion the module documents: "how ... my X" asks to be taught.
    from core.plain_task_routing import _HOWTO_MANNER_RE, _clause_requests_recall

    assert _HOWTO_MANNER_RE.search("how do I renew my passport")
    # A bare-how possessive clause without an auxiliary frame is not claimed as recall:
    # "how I store my seeds" (statement-shaped) has no inverted auxiliary and no
    # interrogative possessive head, so the recall decision is left to its other shapes.
    assert not _clause_requests_recall("how my seeds are stored")


# ---------------------------------------------------------------------------------------------
# The multi-part answer-format contract is stated BEFORE the first answer
# ---------------------------------------------------------------------------------------------

def test_the_numbered_parts_instruction_is_available_for_the_first_request() -> None:
    from core.ordinary_chat_response_guard import numbered_parts_instruction

    text = numbered_parts_instruction(2)
    assert "number them 1 through 2" in text


def test_first_request_carries_the_parts_contract_for_multi_part_questions() -> None:
    """Source-level pin: memory_first_router's request-build seam appends the same
    instruction the retry uses, so the model sees the format demand before answering
    (served proof: S3 in the routing lane matrix -- first serialized request carries
    'Answer format for this request')."""

    import inspect

    import core.memory_first_router as mfr

    source = inspect.getsource(mfr)
    assert "numbered_parts_instruction" in source
    assert "Answer format for this request" in source
