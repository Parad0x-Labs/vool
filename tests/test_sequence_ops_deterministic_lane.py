"""Exactly-computable sequence-op turns never reach a stochastic model.

Measured live 2026-08-15, twice, on two different local models: "Array A starts with [1,2,3].
I append 4. Then I remove 2. Then I multiply the first element by 10. Then I reverse. Output
exactly the final array in [1,2,3] format, no spaces." came back EMPTY -- the reply "did not
contain the requested output format". The turn is exactly computable ([1,2,3] -> [1,2,3,4] ->
[1,3,4] -> [10,3,4] -> [4,3,10]); VOOL handed it to a weak model anyway.

The evaluator is all-or-nothing: any step outside the closed verb set sends the WHOLE turn to
the model. Judgment edits ("sort these by relevance") and open-ended list questions must never
enter the deterministic lane.
"""

from __future__ import annotations

from unittest import mock

import pytest

from core.task_router import (
    evaluate_direct_math_request,
    evaluate_sequence_ops_request,
)

MEASURED_TURN = (
    "Array A starts with [1,2,3]. I append 4. Then I remove 2. Then I multiply the first "
    "element by 10. Then I reverse. Output exactly the final array in [1,2,3] format, no spaces."
)


def test_measured_sequence_turn_computes_exactly_and_renders_compact() -> None:
    # [1,2,3] +4 -> [1,2,3,4]; remove 2 -> [1,3,4]; first*10 -> [10,3,4]; reverse -> [4,3,10].
    assert evaluate_sequence_ops_request(MEASURED_TURN) == "[4,3,10]"


def test_sequence_lane_is_inherited_by_every_direct_math_caller() -> None:
    # Wired the way the named-math fallback was wired: INSIDE evaluate_direct_math_request,
    # zero new call sites in the frontdoor.
    assert evaluate_direct_math_request(MEASURED_TURN) == "[4,3,10]"


@pytest.mark.parametrize(
    ("turn", "expected"),
    (
        # Unseen phrasings and declarators, prose rendering when no compact form is asked.
        ("My list is [5, 2, 9]. Sort descending. What is the list now?", "Final list: [9, 5, 2]."),
        ("list = [3,1,2]. sort ascending then append 10.", "Final list: [1, 2, 3, 10]."),
        ("Array B starts with [7]. Multiply the last element by 3. Add 4.", "Final list: [21, 4]."),
        ("Queue Q is [4,5]. delete 5. reverse it. show the final array.", "Final list: [4]."),
        (
            "The sequence begins with [2,4]. Prepend 1. Divide every element by 2. "
            "Output it in brackets, no spaces.",
            "[0.5,1,2]",
        ),
        (
            "My list is [10, -3, 7]. Sort it in descending order. Remove -3. "
            "Multiply every element by 2.",
            "Final list: [20, 14].",
        ),
        # Trailing urgency filler has disabled recognizers in this repo before ("thx"/"asap" on
        # the calculator); it must not disable an op either.
        (
            "ok so my list is [6,1,3] please sort it descending and prepend 9 asap",
            "Final list: [9, 6, 3, 1].",
        ),
    ),
)
def test_sequence_family_executes_the_closed_verb_set(turn: str, expected: str) -> None:
    assert evaluate_sequence_ops_request(turn) == expected


@pytest.mark.parametrize(
    "turn",
    (
        # Judgment edit: "sort by relevance" is a model decision, never a deterministic op.
        "My list is [3,1,2]. Sort these by relevance for a search page.",
        # Open-ended list question: no literal starting list, never enters the lane.
        "Give me a list of 5 fruits",
        # Conditional rule evaluation (the traffic-light class) stays with the model.
        "My list is [1,2,3]. If the first element is even, say BLUE, else RED.",
        # An op outside the closed set poisons the WHOLE turn, not just its own step.
        "Array A starts with [1,2,3]. Shuffle it. Reverse.",
        # Removing an absent value has no exact semantics.
        "list is [1,2]. remove 7.",
        # A bracketed literal without a sequence subject is not a list declaration.
        "My PIN is [1,2,3,4]. remove 2.",
        # A non-output trailing request outside the closed set sends the turn to the model.
        "My list is [1,2,3]. Reverse it. Give me a haiku about winter.",
        # Element-wise addition is not in the closed set; leftover words must not half-match.
        "My list is [1,2,3]. Add 4 to every element.",
        # Division by zero is not exactly computable.
        "My list is [4,8]. Divide every element by 0.",
        "Explain entropy. My list is [1,2]. Reverse it. Output the list.",
        "My list is [1,2]. Reverse it. Output the list. Explain entropy.",
        "My list is [1,2]. Reverse it. Output a list of the latest headlines.",
        "My list is [1,2]. Reverse it. Output the list, no spaces. Ukraine news.",
    ),
)
def test_sequence_negative_controls_fall_through_to_the_model(turn: str) -> None:
    assert evaluate_sequence_ops_request(turn) is None
    assert evaluate_direct_math_request(turn) is None


def test_plain_arithmetic_and_named_math_are_unchanged() -> None:
    assert evaluate_direct_math_request("what is 3 + 4") == "3 + 4 = 7."
    assert evaluate_direct_math_request("the square root of 144") == (
        "The square root of 144 is 12."
    )


def test_real_frontdoor_answers_the_measured_turn_without_a_model(tmp_path, monkeypatch) -> None:
    from apps.vool_agent import VoolAgent

    monkeypatch.setenv("VOOL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("VOOL_RUNTIME_DIR", str(tmp_path / "runtime"))
    agent = VoolAgent(backend_name="test-backend", device="sequence-ops", persona_id="default")
    model = mock.Mock(side_effect=AssertionError("sequence-op turn reached a model"))
    monkeypatch.setattr(agent.memory_router, "resolve", model)
    monkeypatch.setattr(agent.memory_router, "_invoke_manifest", model)

    result = agent.run_once(
        MEASURED_TURN,
        session_id_override="openclaw:sequenceopslane",
        source_context={
            "workspace": str(tmp_path),
            "workspace_root": str(tmp_path),
            "surface": "api",
            "platform": "api",
            "operating_mode": "auto",
            "requested_model": "vool-local-only",
            "local_only": True,
            "allow_remote_fetch": False,
        },
    )

    assert result["response"] == "[4,3,10]"
    assert result["model_calls"] == 0
    model.assert_not_called()


# ---------------------------------------------------------------------------------------------
# The OPERATOR'S phrasing, verbatim -- the lane's first corpus was its own paraphrase
# ---------------------------------------------------------------------------------------------

REAL_OPERATOR_TURN = (
    "Array A starts with [1, 2, 3]. I append the number 4. Then I remove the number 2. "
    "Then I multiply the first element by 10. Then I reverse the entire array. What is the "
    "final state of Array A? Output exactly the array with brackets and commas, but with "
    "absolutely no spaces (e.g., [10,3,4]). No punctuation at the end. No markdown. "
    "No explanation."
)


def test_the_operators_verbatim_turn_computes() -> None:
    """The lane's original corpus was a PARAPHRASE of the reported turn, and the verbatim turn
    declined on two independent gaps, measured on the live app (the model then answered [8,7]
    for [8,7,5]): "reverse the ENTIRE array" fell outside the reverse pattern, and the output
    directive's comma-split tail ("commas", "but with absolutely no spaces (e", "[example])",
    "No punctuation at the end") each read as an unrecognized step and declined the turn."""

    from core.task_router import evaluate_sequence_ops_request

    assert evaluate_sequence_ops_request(REAL_OPERATOR_TURN) == "[4,3,10]"


@pytest.mark.parametrize(
    ("turn", "expected"),
    (
        # Fresh phrasings across the two repaired gaps plus the widened declaration.
        ("List B starts as [5, 6, 7]. I append 8. Then I remove 6. Then I reverse the list. "
         "Output exactly the list with brackets and commas, no spaces.", "[8,7,5]"),
        ("Sequence S begins as [2, 4]. I prepend 1. Then I reverse the whole sequence. "
         "Output only the sequence, no spaces (e.g., [4,2,1]).", "[4,2,1]"),
        ("Array Q starts at [9, 8]. I append 7. Then I reverse the entire list. What is Q now? "
         "Output the array in brackets, no spaces, nothing else.", "[7,8,9]"),
    ),
)
def test_fresh_phrasings_across_the_repaired_gaps(turn: str, expected: str) -> None:
    from core.task_router import evaluate_sequence_ops_request

    assert evaluate_sequence_ops_request(turn) == expected


def test_a_real_step_after_the_output_directive_still_executes() -> None:
    """The tail tolerance skips only UNRECOGNIZED fragments; a genuine closed-set step placed
    after the directive still applies, so the tolerance cannot hide a real operation."""

    from core.task_router import evaluate_sequence_ops_request

    result = evaluate_sequence_ops_request(
        "List L starts with [1, 2]. Output the list with no spaces. Then I append 3."
    )

    assert result == "[1,2,3]"


def test_tail_tolerance_never_rescues_a_pre_directive_garbage_step() -> None:
    """Before the directive, the closed grammar stays strict: an unrecognized operation still
    declines the whole turn to the model."""

    from core.task_router import evaluate_sequence_ops_request

    assert (
        evaluate_sequence_ops_request(
            "List L starts with [1, 2]. I shuffle it randomly. Output the list, no spaces."
        )
        is None
    )
