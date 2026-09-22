"""Typed clause boundaries are the shared truth for response-shape scope.

The parser stays deliberately structural: it preserves what the user wrote and classifies only
request heads whose kind is unambiguous.  It does not try to become a second planner.
"""

from __future__ import annotations

import pytest

from core.response_constraints import parse_response_constraint
from core.turn_ir import ClauseKind, parse_turn_ir

ALPHABETIC_INCIDENT = (
    "A) Explain in one sentence why soap helps remove grease. "
    "B) Calculate 46 x 19. "
    "C) Give a 6-word title for a QA note about local routing."
)


def test_alphabetic_incident_preserves_clause_identity_spans_kinds_and_shapes() -> None:
    turn = parse_turn_ir(ALPHABETIC_INCIDENT)

    assert [clause.clause_id for clause in turn.clauses] == [
        "clause-1",
        "clause-2",
        "clause-3",
    ]
    assert [clause.label for clause in turn.clauses] == ["A", "B", "C"]
    assert [clause.kind for clause in turn.clauses] == [
        ClauseKind.KNOW,
        ClauseKind.COMPUTE,
        ClauseKind.CREATE,
    ]
    assert [clause.request_text for clause in turn.clauses] == [
        "Explain in one sentence why soap helps remove grease.",
        "Calculate 46 x 19.",
        "Give a 6-word title for a QA note about local routing.",
    ]
    assert [ALPHABETIC_INCIDENT[clause.start : clause.end] for clause in turn.clauses] == [
        clause.original_text for clause in turn.clauses
    ]
    assert turn.clauses[0].response_shape is not None
    assert turn.clauses[0].response_shape.exact_sentences == 1
    assert turn.clauses[1].response_shape is None
    assert turn.clauses[2].response_shape is not None
    assert turn.clauses[2].response_shape.exact_words == 6
    assert parse_response_constraint(ALPHABETIC_INCIDENT) is None


@pytest.mark.parametrize(
    ("prompt", "labels"),
    [
        (
            "1) Explain in one sentence why ice floats. "
            "2) Calculate 44 x 3. 3) Give a five-word title.",
            ["1", "2", "3"],
        ),
        (
            "a. Explain in one sentence why ice floats.\n"
            "b. Calculate 44 x 3.\n"
            "c. Give a five-word title.",
            ["a", "b", "c"],
        ),
        (
            "- Explain in one sentence why ice floats.\n"
            "- Calculate 44 x 3.\n"
            "- Give a five-word title.",
            [None, None, None],
        ),
        (
            "Explain in one sentence why ice floats; "
            "Calculate 44 x 3; Give a five-word title",
            [None, None, None],
        ),
        (
            "Explain in one sentence why ice floats\n"
            "Calculate 44 x 3\n"
            "Give a five-word title",
            [None, None, None],
        ),
        (
            "Explain in one sentence why ice floats and then "
            "calculate 44 x 3, plus give a five-word title",
            [None, None, None],
        ),
    ],
)
def test_marker_and_boundary_matrix_keeps_local_shapes_out_of_global_contract(
    prompt: str,
    labels: list[str | None],
) -> None:
    turn = parse_turn_ir(prompt)

    assert len(turn.clauses) == 3
    assert [clause.label for clause in turn.clauses] == labels
    assert turn.clauses[0].response_shape is not None
    assert turn.clauses[0].response_shape.exact_sentences == 1
    assert turn.clauses[1].response_shape is None
    assert turn.clauses[2].response_shape is not None
    assert turn.clauses[2].response_shape.exact_words == 5
    assert parse_response_constraint(prompt) is None


@pytest.mark.parametrize(
    ("prompt", "exact_words", "exact_sentences"),
    [
        ("Answer in exactly six words.", 6, None),
        ("Explain the result in one sentence.", None, 1),
        (
            "Use exactly twenty words for the whole response: "
            "A) Explain why ice floats. B) Calculate 44 x 3.",
            20,
            None,
        ),
    ],
)
def test_single_clause_and_explicit_whole_response_constraints_remain_global(
    prompt: str,
    exact_words: int | None,
    exact_sentences: int | None,
) -> None:
    constraint = parse_response_constraint(prompt)

    assert constraint is not None
    assert constraint.exact_words == exact_words
    assert constraint.exact_sentences == exact_sentences


def test_markers_inside_quotes_do_not_create_user_clauses() -> None:
    prompt = 'Critique the fixture "A) Explain caching. B) Calculate 8 x 9." in one sentence.'

    turn = parse_turn_ir(prompt)

    assert len(turn.clauses) == 1
    assert turn.clauses[0].label is None
    assert turn.clauses[0].original_text == prompt
    assert parse_response_constraint(prompt) is not None


def test_turn_ir_serialization_is_machine_readable_and_preserves_source_offsets() -> None:
    turn = parse_turn_ir("A) Explain rain. B) Calculate 7 x 8.")

    payload = turn.to_dict()

    assert payload["source_text"] == turn.source_text
    assert payload["clauses"][0]["clause_id"] == "clause-1"
    assert payload["clauses"][0]["kind"] == "know"
    assert payload["clauses"][0]["start"] == 0
    assert payload["clauses"][0]["response_shape"] is None


def test_a_prohibition_constrains_the_answer_and_is_not_a_demand() -> None:
    """Measured on build 7de469ba, acceptance turn 7. The served reply read:

        10% of 250 = 25.

        Could not be answered:
        - what is the boiling point of water at sea level in Celsius? - ...
        - Do NOT search the web for this. - no part of the plan covered this request

    The runtime reported an instruction it had OBEYED as an unanswered demand. `classify_clause_kind`
    read the head verb and never consulted the negation governing it, so "Do NOT search ..." headed
    on "do" and landed in `_KNOW_HEADS`.

    The verdict is delegated to `core.retrieval_constraints`, which already owns prohibition
    recognition here -- `core.conductor.planner._deterministic_unavailable_action_plan` had grown
    its OWN local regex for exactly this ("An answer/tool constraint, not another question"), which
    is the duplicate authority this consolidates.
    """
    from core.conductor.obligations import _is_content_request
    from core.turn_ir import ClauseKind, classify_clause_kind

    for prohibition in (
        "Do NOT search the web for this.",
        "Never use the internet for this.",
        "Do NOT search the web for flight tickets or bakeries.",
    ):
        assert classify_clause_kind(prohibition) is ClauseKind.CONSTRAINT, prohibition
        assert not _is_content_request(prohibition), (
            f"the obligation floor still treats a prohibition as a demand: {prohibition!r}"
        )

    # NOT `UNKNOWN`: that kind is PERMISSIVE -- it appears in `accepted_kinds` for most operations
    # in core.conductor.operations, so a prohibition classified UNKNOWN gets claimed and served.
    assert classify_clause_kind("Do NOT search the web for this.") is not ClauseKind.UNKNOWN


def test_a_demand_beside_a_prohibition_stays_a_demand() -> None:
    """The test is "is this NOTHING BUT a constraint", never "does it contain one".

    The first version of this fix failed here: `core.retrieval_constraints`'s negative span is
    greedy across a comma, so `Do NOT search the web for this, what is 2+2?` came back as ONE
    negative clause with `eligible_text='?'` and the whole sentence read as a constraint -- the
    demand would have been dropped silently. That greediness is right for retrieval eligibility
    (it removes MORE from the search text) and wrong here, so the authority is asked per segment
    instead of having its span changed.
    """
    from core.turn_ir import ClauseKind, classify_clause_kind

    for carries_a_demand in (
        "Do NOT search the web for this, what is 2+2?",
        "Do NOT search the web for this. What is 2+2?",
        "Without searching, what is the capital of France?",
        "Get the current TRY/EUR exchange rate without using the web",
        "Explain photosynthesis without using the internet",
        "Calculate 19 times 7 without using tools",
    ):
        assert classify_clause_kind(carries_a_demand) is not ClauseKind.CONSTRAINT, carries_a_demand


def test_a_framing_prefix_does_not_hide_the_request_behind_it() -> None:
    """An independently answerable clause must not disappear.

    "From memory: what is the boiling point of water at sea level in Celsius?" heads on "From",
    which no head set claims, so the clause read UNKNOWN and the composer answered a perfectly
    answerable question with "the available answer path does not safely match this request"
    (acceptance turn 7 on BOTH 7fe94596 and 7de469ba -- the same text answers normally once the
    prefix is gone). The retry is a FALLBACK, reached only where the answer would be UNKNOWN, so
    it can add a classification but never change one already made.
    """
    from core.turn_ir import ClauseKind, classify_clause_kind

    assert classify_clause_kind(
        "From memory: what is the boiling point of water at sea level in Celsius?"
    ) is ClauseKind.KNOW
    # Control: the same question with no prefix was always KNOW, and the sibling still is.
    assert classify_clause_kind("what is the boiling point of water at sea level in Celsius?") is ClauseKind.KNOW
    assert classify_clause_kind("Also, what is 10 percent of 250?") is ClauseKind.KNOW
    # Control: a prefix in front of nothing answerable stays UNKNOWN rather than being invented into
    # a request.
    assert classify_clause_kind("Thanks!") is ClauseKind.UNKNOWN


@pytest.mark.parametrize("cities", ["Kaunas and Tallinn", "Oslo and Tromso"])
def test_a_later_comparison_does_not_split_an_observed_list(cities) -> None:
    text = (f"Get the current weather for {cities}, tell me which city is warmer, "
            "and explain what a 6 degree difference means for choosing a coat.")
    clauses = parse_turn_ir(text).clauses
    assert [clause.request_text for clause in clauses] == [
        f"Get the current weather for {cities}",
        "tell me which city is warmer,",
        "explain what a 6 degree difference means for choosing a coat.",
    ]


def test_constraint_detection_never_reshapes_clause_boundaries() -> None:
    """`_starts_request` asks `classify_clause_kind` about every SUFFIX of the turn.

    So a constraint verdict taken over a multi-sentence span answers a different question than the
    one asked, and it showed: a prohibition in the LAST sentence made the whole tail read as a
    constraint, which split "Fly to Paris right now and bring me back a croissant." in two and cost
    the deterministic unavailable-action plan its turn. Caught by
    tests/test_followup_unavailable_effect_siblings.py, pinned here at the cause.
    """
    from core.turn_ir import ClauseKind, parse_turn_ir

    text = (
        "Fly to Paris right now and bring me back a physical physical physical croissant. "
        "Then explain the biological process of yeast fermentation. "
        "Do NOT search the web for flight tickets or bakeries."
    )
    clauses = parse_turn_ir(text).clauses
    kinds = [clause.kind for clause in clauses]
    assert kinds == [ClauseKind.ACT, ClauseKind.KNOW, ClauseKind.CONSTRAINT], (
        f"clause boundaries moved: {[(c.kind.value, c.request_text) for c in clauses]}"
    )
    assert "croissant" in clauses[0].request_text, "the action sentence was split in two"

    # A head that lives in NO head set but is still special-cased downstream must survive the
    # framing-prefix retry: "bake" reaches ACT through the physical-appliance check.
    assert parse_turn_ir(
        "Bake a chocolate cake in my physical oven right now."
    ).clauses[0].kind is ClauseKind.ACT
