"""A response-shape contract comes from what the request asks for, not from the verb that opens it.

Live defect. These two prompts are the same request:

    A: "Answer in one sentence: give me the three primary colours as a numbered list
        with each item on its own line."
    B: "Tell me in one sentence: give me the three primary colours as a numbered list
        with each item on its own line."

A was rejected over and over -- "I couldn't produce a complete response within the requested format"
-- on qwen2.5:7b and on Nemotron 3 Ultra alike, burning 1,200-1,600 tokens per failed attempt. B was
answered instantly and correctly. Nothing about the two requests differs except the opening verb.

Two causes, both in `core/response_constraints.py`, and the first is the reason it looked
model-specific when it never was:

* `_SHORT_SENTENCE_SHAPE_RE` gated sentence-count detection on a CLOSED VERB LIST --
  answer|respond|reply|say|return|give|use|write|end|confirm|explain. "Tell" is not in it. So the
  literal trigger verb, and nothing else, decided whether a shape contract existed at all.
* Nothing reconciled the two shapes the request states. "One sentence" AND "a numbered list with
  each item on its own line" cannot both bind the whole answer, and the sentence count won: a
  correct three-line answer counted three sentences, failed `exact_sentences`, exhausted its one
  repair, and shipped the format apology instead.

The rule these tests pin: **an explicit structural layout is the binding shape, and a sentence
budget alongside it applies per item, never to the whole answer.** Semantically equivalent wording
resolves to the same contract, whichever verb introduced it.
"""
from __future__ import annotations

import pytest

from core.incomplete_answer import answer_looks_incomplete
from core.response_constraints import (
    check_response_constraint,
    enforce_response_constraint,
    parse_response_constraint,
    response_constraint_from_metadata,
)

# The answer a competent model gives to prompts 1-3. It is correct, complete and exactly what was
# asked for, and it is what the runtime was throwing away.
_GOOD_NUMBERED_ANSWER = "1. Red\n2. Blue\n3. Yellow"

# Same request, four openings. The verb is the only difference between them.
_EQUIVALENT_LIST_REQUESTS = (
    "Answer in one sentence: give me the three primary colours as a numbered list with each item on its own line.",
    "Tell me in one sentence: give me the three primary colours as a numbered list with each item on its own line.",
    "Give me in one sentence the three primary colours as a numbered list with each item on its own line.",
    "Respond in one sentence: list three colours, one per line.",
)


# ---------------------------------------------------------------------------------------------
# The equivalence itself
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("request_text", _EQUIVALENT_LIST_REQUESTS)
def test_a_structural_layout_is_never_collapsed_by_a_sentence_budget(request_text: str) -> None:
    """The measured failure: a correct numbered answer rejected for having three sentences."""
    constraint = parse_response_constraint(request_text)

    assert constraint is not None, "the request states a shape; the runtime found none"
    assert constraint.max_sentences is None, (
        "a sentence budget bound the whole answer even though a list layout was requested"
    )
    assert constraint.exact_sentences is None

    check = check_response_constraint(_GOOD_NUMBERED_ANSWER, constraint)
    assert check.compliant, check.violations


def test_the_opening_verb_is_not_the_authority() -> None:
    """`Answer ...` and `Tell me ...` are the same request and must produce the same contract."""
    answer_form = parse_response_constraint(_EQUIVALENT_LIST_REQUESTS[0])
    tell_form = parse_response_constraint(_EQUIVALENT_LIST_REQUESTS[1])

    assert answer_form == tell_form


@pytest.mark.parametrize("request_text", _EQUIVALENT_LIST_REQUESTS)
def test_the_good_answer_survives_enforcement_untouched(request_text: str) -> None:
    """Enforcement may shorten an answer; it may never mangle the layout that was requested."""
    constraint = parse_response_constraint(request_text)
    assert constraint is not None

    application = enforce_response_constraint(_GOOD_NUMBERED_ANSWER, constraint)

    assert application.compliant, application.violations
    assert application.text == _GOOD_NUMBERED_ANSWER
    assert not application.structurally_trimmed


@pytest.mark.parametrize("request_text", _EQUIVALENT_LIST_REQUESTS)
def test_the_contract_survives_the_metadata_round_trip(request_text: str) -> None:
    """The router ships the contract through request metadata and rebuilds it on the way back."""
    constraint = parse_response_constraint(request_text)
    assert constraint is not None

    rebuilt = response_constraint_from_metadata({"response_constraint": constraint.to_dict()})

    assert rebuilt == constraint


# ---------------------------------------------------------------------------------------------
# The two contracts that must NOT change
# ---------------------------------------------------------------------------------------------


def test_a_genuine_one_sentence_request_is_still_one_sentence() -> None:
    """No list is asked for here, so the sentence budget is the whole shape and still binds."""
    constraint = parse_response_constraint(
        "Give me exactly one sentence explaining why the sky is blue."
    )

    assert constraint is not None
    assert constraint.exact_sentences == 1
    assert constraint.max_sentences == 1
    assert constraint.list_items is None

    two_sentences = "The sky scatters blue light. That is Rayleigh scattering."
    assert not check_response_constraint(two_sentences, constraint).compliant
    assert check_response_constraint(
        "The sky is blue because air scatters short wavelengths most.", constraint
    ).compliant


def test_a_numbered_list_request_stays_a_numbered_list_contract() -> None:
    constraint = parse_response_constraint("Give me exactly three numbered items, one per line.")

    assert constraint is not None
    assert constraint.list_items == 3
    assert constraint.one_item_per_line is True
    assert constraint.exact_sentences is None

    assert check_response_constraint(_GOOD_NUMBERED_ANSWER, constraint).compliant
    # Wrong count, and the same three items crammed onto one line: both are real violations.
    assert not check_response_constraint("1. Red\n2. Blue", constraint).compliant
    assert not check_response_constraint("1. Red 2. Blue 3. Yellow", constraint).compliant


def test_one_per_line_is_checked_even_when_no_count_was_stated() -> None:
    """Isolates the layout rule from the count rule.

    With a count in the contract, "1. Red 2. Blue 3. Yellow" already fails on the count -- it reads
    as one item, not three -- so a broken one-per-line check would still look green. This request
    states the layout and no count, which is the only shape where that check is the thing deciding.
    """
    constraint = parse_response_constraint("Give me the primary colours, one per line.")

    assert constraint is not None
    assert constraint.one_item_per_line is True
    assert constraint.list_items is None

    assert check_response_constraint(_GOOD_NUMBERED_ANSWER, constraint).compliant
    crammed = check_response_constraint("1. Red 2. Blue 3. Yellow", constraint)
    assert not crammed.compliant
    assert "one_item_per_line" in crammed.violations


@pytest.mark.parametrize(
    "prose",
    [
        "Answer in one sentence: why is the sky blue?",
        "Tell me in one sentence why the sky is blue.",
        "Respond in one sentence: what is a monad?",
    ],
)
def test_a_sentence_budget_without_a_layout_binds_whatever_verb_opens_it(prose: str) -> None:
    """The equivalence cuts both ways: every opening verb now yields the sentence budget too."""
    constraint = parse_response_constraint(prose)

    assert constraint is not None
    assert constraint.max_sentences == 1
    assert constraint.list_items is None


# ---------------------------------------------------------------------------------------------
# Honesty guarantees that must survive the repair
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("request_text", _EQUIVALENT_LIST_REQUESTS)
def test_a_bare_marker_is_still_never_a_successful_answer(request_text: str) -> None:
    """QA-050-016/017: `1.` must stay unshippable under the new contract too."""
    constraint = parse_response_constraint(request_text)
    assert constraint is not None

    for degenerate in ("1.", "1.\n2.\n3.", "", "- \n- "):
        assert not check_response_constraint(degenerate, constraint).compliant, degenerate
        assert answer_looks_incomplete(degenerate), degenerate


def test_an_unfinished_list_is_still_reported_incomplete() -> None:
    constraint = parse_response_constraint(_EQUIVALENT_LIST_REQUESTS[0])
    assert constraint is not None

    unfinished = "1. Red\n2. Blue\n3."

    assert answer_looks_incomplete(unfinished)
    assert not check_response_constraint(unfinished, constraint).compliant
