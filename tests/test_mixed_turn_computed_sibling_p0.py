"""P0 — a mixed turn's deterministic computations survive the grounding publication gate.

THE MEASURED DEFECT (acceptance 2026-09-08, turn 18; reproduced on the isolated daemon)
--------------------------------------------------------------------------------------------
"Answer all three: What is 5+5? What is the exact middle name of the current Emperor of Japan?
In what year did the Berlin Wall fall?" served the WHOLE-TURN grounding refusal. One clause
needed current information, retrieval returned nothing, and the composed answer -- which held a
runtime-computed "5 + 5 = 10" and honest unavailability lines -- was replaced wholesale. Two
seams owned it:

1. The computation was never support: `_support_rows` offered only bound evidence and typed
   observations, and a calculation is deliberately NOT an observation (`conductor_observations`:
   "a calculation that succeeded is not evidence that anything was looked up"). So the
   no-rows branch refused before claim matching, and one sibling's ungrounded clause took the
   arithmetic down with it.
2. The planner kept the user's enumerating lead-in attached to the first clause
   ("Answer all three: What is 5+5?"); `_arithmetic_fragment` only split on sentence
   boundaries, the evaluator declined the prefixed clause, and the value fell to a dead
   generation lane as "not determined".

THE CONTRACT UNDER TEST
-----------------------
A succeeded `COMPUTED_VALUE` node publishes its rendered line on its OWN channel; the gate
offers those rows as support; the computed claim publishes while the fabricated sibling is
withheld. A gated turn with NOTHING computed and no rows still refuses -- the union may not
launder an empty turn. And a lead-in-prefixed arithmetic clause evaluates like a bare one.
"""

from __future__ import annotations

from types import SimpleNamespace

from core.conductor.evidence import (
    RUNTIME_COMPUTED_VALUES_CHANNEL,
    conductor_computations,
    publish_conductor_computations,
)
from core.conductor.operations import _arithmetic_fragment
from core.grounding_lifecycle import (
    ORIGIN_COMPUTED_VALUE,
    GroundingLifecycle,
    TurnIdentity,
    record_computed_values,
)
from core.grounding_publication import _support_rows, publication_verdict


def _computed_outcome(node_id: str = "calc-1", rendered: str = "5 + 5 = 10.") -> SimpleNamespace:
    return SimpleNamespace(
        node=SimpleNamespace(
            node_id=node_id,
            operation="calculation",
            request_text="Answer all three: What is 5+5?",
        ),
        succeeded=True,
        rendered=rendered,
        result={"statement": rendered, "value": "10"},
    )


# --------------------------------------------------------------------------- the fragment seam


def test_an_enumerating_lead_in_does_not_lose_the_arithmetic() -> None:
    assert _arithmetic_fragment("Answer all three: What is 5+5?") == "What is 5+5?"
    assert _arithmetic_fragment("Two things: calculate 137 x 29. Then stop.") in (
        "calculate 137 x 29.",
        "calculate 137 x 29",
    )


def test_fragment_precedence_is_unchanged_for_existing_shapes() -> None:
    assert _arithmetic_fragment("What is 137 x 29?") == "What is 137 x 29?"
    assert _arithmetic_fragment("What is 137 x 29? Explain the calculation briefly.") == "What is 137 x 29?"
    assert _arithmetic_fragment("What is the capital of France?") is None
    # A colon inside a time is not a clause boundary, and its fragments simply do not evaluate.
    assert _arithmetic_fragment("The train leaves at 10:30, what time is that in minutes?") is None


# ---------------------------------------------------------------------- the computations channel


def test_succeeded_computed_value_nodes_publish_their_rendered_line() -> None:
    rows = conductor_computations([_computed_outcome()])
    assert [row["summary"] for row in rows] == ["5 + 5 = 10."]
    assert rows[0]["ok"] is True


def test_failed_or_non_computing_nodes_publish_nothing() -> None:
    failed = SimpleNamespace(
        node=SimpleNamespace(node_id="q1", operation="calculation", request_text="x"),
        succeeded=False,
        rendered="",
        result={},
    )
    observed = SimpleNamespace(
        node=SimpleNamespace(node_id="w1", operation="weather_lookup", request_text="weather rome"),
        succeeded=True,
        rendered="Rome: 21 C",
        result={"temperature_c": 21},
    )
    assert conductor_computations([failed, observed]) == []


def test_publish_is_idempotent_by_node_id() -> None:
    context: dict = {}
    assert publish_conductor_computations(context, [_computed_outcome()]) != []
    assert publish_conductor_computations(context, [_computed_outcome()]) == []
    assert len(context[RUNTIME_COMPUTED_VALUES_CHANNEL]) == 1


# ------------------------------------------------------------ the lifecycle and the publication gate


def _gated_lifecycle(computed: tuple[dict, ...]) -> GroundingLifecycle:
    return GroundingLifecycle(
        lifecycle_id="mixed-turn-1",
        identity=TurnIdentity(),
        request_text=(
            "Answer all three: What is 5+5? What is the exact middle name of the current "
            "Emperor of Japan? In what year did the Berlin Wall fall?"
        ),
        retrieval_outcome="partial_no_sources",
        model_authored=True,
        computed_values=computed,
    )


def test_record_computed_values_writes_only_the_computed_channel() -> None:
    context: dict = {"turn_id": "t-cv-1", "request_id": "r-cv-1"}
    from core.grounding_lifecycle import register_required

    register_required(context, request_text="answer all three", reason_codes=("current_information",))
    added = record_computed_values(
        context, entries=[{"node_id": "c1", "summary": "5 + 5 = 10.", "ok": True}]
    )
    assert added == 1
    lifecycle = _gated_lifecycle(())
    rows, origin = _support_rows(_gated_lifecycle(({"summary": "5 + 5 = 10."},)))
    assert rows and rows[0]["summary"] == "5 + 5 = 10."
    assert origin == ORIGIN_COMPUTED_VALUE


def test_the_computed_sibling_publishes_while_the_fabricated_one_is_withheld() -> None:
    content = (
        "5 + 5 = 10.\n"
        "The exact middle name of the current Emperor of Japan is Fumihito.\n"
        "The Berlin Wall fell in 1989."
    )
    verdict = publication_verdict(_gated_lifecycle(({"summary": "5 + 5 = 10."},)), content)
    assert verdict.state == "partial", verdict
    # The computed line is the PUBLISHED body; the fabricated siblings appear only inside the
    # withheld-work notice, which names what was removed -- never as published claims.
    assert verdict.content.startswith("5 + 5 = 10."), verdict.content
    notice_at = verdict.content.find("Withheld from this answer:")
    assert notice_at != -1, verdict.content
    assert "Fumihito" in verdict.content[notice_at:]
    assert "1989" in verdict.content[notice_at:]
    assert "Fumihito" not in verdict.content[:notice_at]
    assert "1989" not in verdict.content[:notice_at]


def test_a_gated_turn_with_nothing_computed_and_no_rows_still_refuses() -> None:
    """The union may not launder an empty turn: nothing computed, nothing retrieved, refused."""
    content = "5 + 5 = 10.\nThe exact middle name of the current Emperor of Japan is Fumihito."
    verdict = publication_verdict(_gated_lifecycle(()), content)
    assert verdict.state == "refused", verdict
    assert "5 + 5" not in verdict.content


def test_a_preamble_tail_is_carved_but_an_unanswered_question_tail_is_not() -> None:
    """The suffix exemption's boundary, pinned directly (2026-09-09 reconciliation).

    A leftover head that still contains an interrogative is an unanswered ask, not a
    preamble: "what is the water temperature in the | baltic sea" must NOT count as a
    lead-in carve (the AUD-20260829-003 measured pin regressed on exactly this), while
    "answer all three | what is 5 5" must (the case 90143c8b fixed live).
    """
    from core.conductor.compose import _suffix_carved_from

    assert _suffix_carved_from("what is 5 5", "answer all three what is 5 5") is True
    assert _suffix_carved_from("baltic sea", "what is the water temperature in the baltic sea") is False
