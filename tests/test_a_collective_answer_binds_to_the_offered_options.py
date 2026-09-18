"""Answering an offered choice with "both" selects the options, not nothing.

Measured live on c6eed761, served UI::

    U: what car is better Vw laguna or Toyota passat?
    A: "Passat" is a Volkswagen model, not Toyota. "Laguna" was a Renault... did you mean...
    U: yes
    A: Which pair do you want to compare?
       1. VW Passat vs. Toyota Camry / Avensis
       2. Renault Laguna vs. VW Passat / Toyota Avensis
       3. Something else
    U: both
    A: Understood. The USDC x402 spend lane is DISABLED in this build. Registration of
       `.null` names requires a live, OS-consented transaction.

"both" carries no pronoun, no ordinal and no correction opening, so
`_resolve_reference_targets` matched none of its three entry gates and returned NO targets. The turn
reached the model with no subject at all, and it answered from whatever was nearest in context --
here, unrelated project facts.

The ordinal path already reads the assistant's offered list (`_latest_enumerated_items`). What was
missing is the COLLECTIVE selector: a word that picks every option rather than one of them. These
tests pin that distinction and its boundaries, not the car wording.
"""

from __future__ import annotations

import pytest

from core.human_input_adapter import _resolve_reference_targets

_OFFERED = [
    {
        "speaker_role": "assistant",
        "raw_input": (
            "Which pair do you want to compare?\n"
            "1. VW Passat vs. Toyota Camry\n"
            "2. Renault Laguna vs. VW Passat\n"
            "3. Something else"
        ),
    }
]

_TWO_OPTIONS = [
    {
        "speaker_role": "assistant",
        "raw_input": "Postgres or SQLite for this?\n- postgres\n- sqlite",
    }
]


def _resolve(text: str, turns=None):
    return _resolve_reference_targets(
        text,
        current_topics=[],
        session_state={},
        recent_turns=[],
        context_turns=list(_OFFERED if turns is None else turns),
    )


# ---------------------------------------------------------------------------------------------
# G1 -- the reproduction
# ---------------------------------------------------------------------------------------------


def test_both_binds_to_the_offered_options() -> None:
    targets, _flags = _resolve("both")

    assert targets, "'both' resolved to nothing, which is what let the model free-associate"
    assert "VW Passat vs. Toyota Camry" in targets


# ---------------------------------------------------------------------------------------------
# CLEAN -- other collective selectors, other offered lists
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    ("both", "both please", "both of them", "all", "all of them", "either", "any of these", "both thanks"),
)
def test_every_collective_selector_binds(answer: str) -> None:
    targets, _flags = _resolve(answer)

    assert targets, answer


def test_a_two_item_bullet_list_binds_the_same_way() -> None:
    """Bullets, not numbers, and a different domain entirely."""

    targets, _flags = _resolve("both", _TWO_OPTIONS)

    assert targets == ["postgres", "sqlite"]


# ---------------------------------------------------------------------------------------------
# NEGATIVE CONTROLS
# ---------------------------------------------------------------------------------------------


def test_a_statement_containing_both_is_not_a_selection() -> None:
    """Anchored to the whole utterance: the selection has to BE the turn."""

    targets, _flags = _resolve("both of these boxes are on the same rack")

    assert targets == []


def test_neither_rejects_and_must_not_resurrect_the_options() -> None:
    """A rejection is the contamination shape the negated-reference guard exists to prevent."""

    targets, _flags = _resolve("neither")

    assert targets == []


def test_a_bare_yes_is_not_a_collective_selection() -> None:
    """"yes" answers a yes/no question, not a pick-from-these one. Out of scope, deliberately."""

    targets, _flags = _resolve("yes")

    assert targets == []


def test_an_ordinal_still_selects_exactly_one() -> None:
    """The existing path must be untouched: 'the first one' is not 'all of them'."""

    targets, _flags = _resolve("the first one")

    assert targets == ["VW Passat vs. Toyota Camry"]


def test_an_ordinary_question_after_a_list_is_unaffected() -> None:
    targets, _flags = _resolve("how do I bake sourdough bread")

    assert targets == []


# ---------------------------------------------------------------------------------------------
# ADVERSARIAL
# ---------------------------------------------------------------------------------------------


def test_a_collective_answer_with_no_list_declines_and_flags_ambiguity() -> None:
    """Declining is the point: a bare "both" with nothing to select from must ask, not invent."""

    targets, flags = _resolve("both", [])

    assert targets == []
    assert "ambiguous_reference" in flags


def test_a_collective_answer_binds_to_the_MOST_RECENT_list() -> None:
    """A stale list from earlier in the conversation must not win over the one just offered."""

    turns = [
        {"speaker_role": "assistant", "raw_input": "Pick one:\n- alpha\n- beta"},
        {"speaker_role": "assistant", "raw_input": "Earlier:\n- gamma\n- delta"},
    ]
    targets, _flags = _resolve("both", turns)

    assert targets == ["alpha", "beta"]


def test_the_binding_is_bounded_so_a_long_list_cannot_flood_the_turn() -> None:
    turns = [
        {
            "speaker_role": "assistant",
            "raw_input": "Options:\n" + "\n".join(f"{n}. item {n}" for n in range(1, 12)),
        }
    ]
    targets, _flags = _resolve("all of them", turns)

    assert 0 < len(targets) <= 3


def test_both_offered_three_options_is_a_stated_boundary() -> None:
    """"both" means two, and the reproduction offered three.

    Binding to the offered list is still strictly better than binding to nothing -- the turn gets a
    subject and the model can ask which two. Recorded as a boundary rather than left implicit.
    """

    targets, _flags = _resolve("both")

    assert len(targets) > 1
