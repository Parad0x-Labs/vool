"""A table that stops part-way through a row is a cut-off answer, and must be reported as one.

Measured live in the served UI, repeatedly. A request for full specs on five cars returned:

    | Model         | Max Speed | Engine Size   | Horsepower | Torque |
    |---------------|-----------|---------------|------------|--------|
    | Toyota Corolla| 124       | 1,598         | 131        | 106    |
    | Honda Civic   | 127       | 1,601         | 158        | 130    |
    | Ford F-Series | Varies    | 4,600 - 6,200 | 495+       |

-- four values under five headers, three cars out of five, and the reply ended there. Another turn
ended `| Honda Civic 2025 | 2.`, cut inside a number. Both shipped as finished answers.

`inspect_answer_completeness` exists to ask "does this have the shape of an answer that was cut
off". It knew about bare list markers, empty bullets, unfinished enumerations and dangling
prepositions -- and reported every one of these tables as complete.

A table is the one structure where truncation is unambiguous from shape alone: the header fixes the
column count, so a final row that is short, or that never closes its last cell, was cut. Only the
LAST row is judged -- a short row mid-table is a formatting decision somebody made on purpose.
"""

from __future__ import annotations

import pytest

from core.incomplete_answer import inspect_answer_completeness

_COMPLETE = "| Model | HP |\n|---|---|\n| Corolla | 169 |\n| Civic | 158 |"


def _incomplete(text: str) -> bool:
    return inspect_answer_completeness(text).incomplete


# ---------------------------------------------------------------------------------------------
# G1 -- the measured reproductions
# ---------------------------------------------------------------------------------------------


def test_a_row_cut_inside_a_cell_is_incomplete() -> None:
    verdict = inspect_answer_completeness("| Model | HP |\n|---|---|\n| Corolla | 169 |\n| Civic | 2.")

    assert verdict.incomplete
    assert "table_cut_mid_row" in verdict.reasons


def test_a_final_row_shorter_than_its_header_is_incomplete() -> None:
    table = (
        "| Model | Max Speed | Engine | HP | Torque |\n"
        "|---|---|---|---|---|\n"
        "| Corolla | 124 | 1598 | 131 | 106 |\n"
        "| Ford F-Series | Varies | 4,600 - 6,200 | 495+ |"
    )

    assert _incomplete(table)


# ---------------------------------------------------------------------------------------------
# CLEAN -- other shapes of the same cut
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "table",
    (
        "| A | B | C |\n|---|---|---|\n| 1 | 2 | 3 |\n| 4 | 5",
        "| Name | Role |\n|---|---|\n| Ada | Engineer |\n| Grace",
        "| K | V |\n|---|---|\n| one | 1 |\n| two |",
    ),
)
def test_any_severed_final_row_is_incomplete(table: str) -> None:
    assert _incomplete(table), table


# ---------------------------------------------------------------------------------------------
# NEGATIVE CONTROLS
# ---------------------------------------------------------------------------------------------


def test_a_complete_table_is_untouched() -> None:
    assert not _incomplete(_COMPLETE)


def test_a_table_followed_by_prose_is_untouched() -> None:
    """Prose after the table means the table finished and the reply moved on."""

    assert not _incomplete(_COMPLETE + "\n\nLet me know if you want trim-specific data.")


def test_a_short_row_in_the_MIDDLE_is_a_formatting_choice() -> None:
    """Only the last row is judged. A ragged row mid-table is deliberate, not a cut."""

    table = "| A | B | C |\n|---|---|---|\n| 1 | 2 |\n| 4 | 5 | 6 |"

    assert not _incomplete(table)


@pytest.mark.parametrize(
    "text",
    (
        "Water freezes at 0 C at standard pressure.",
        "| just one |",
        "Here is a list:\n- one\n- two",
    ),
)
def test_text_without_a_table_is_unaffected(text: str) -> None:
    assert not _incomplete(text)


def test_an_empty_answer_is_still_empty_not_a_table_problem() -> None:
    """Pre-existing and correct: "" is incomplete for its own reason, which the new rule must not
    displace or claim credit for."""

    verdict = inspect_answer_completeness("")

    assert verdict.incomplete
    assert "empty_answer" in verdict.reasons
    assert "table_cut_mid_row" not in verdict.reasons


def test_the_existing_reasons_still_fire(  ) -> None:
    """The new rule must not displace what the inspector already caught."""

    assert "enumeration_without_content" in inspect_answer_completeness("1.\n2.\n3.").reasons
    assert "dangling_tail" in inspect_answer_completeness("It depends on the season and the").reasons


# ---------------------------------------------------------------------------------------------
# ADVERSARIAL
# ---------------------------------------------------------------------------------------------


def test_a_separator_row_is_not_mistaken_for_content() -> None:
    """A table with a header and separator but no body rows yet is not a severed row."""

    assert not _incomplete("| Model | HP |\n|---|---|")


def test_a_single_column_table_is_not_flagged_on_cell_count() -> None:
    """One column means every row has one cell; only an unclosed row can be a cut there."""

    assert not _incomplete("| Model |\n|---|\n| Corolla |\n| Civic |")
    assert _incomplete("| Model |\n|---|\n| Corolla |\n| Civ")


def test_alignment_markers_in_the_separator_are_handled() -> None:
    assert not _incomplete("| A | B |\n|:--|--:|\n| 1 | 2 |")


def test_a_severed_table_reaches_the_fulfilment_translator() -> None:
    """End of the chain: an incomplete answer with content must become a PARTIAL turn.

    The inspector alone changes nothing; `output_validation_outcome` is what turns its verdict into
    fulfilment truth, and that is where a cut answer stops being reported as fulfilled.
    """

    from core.runtime_task_outcome import output_validation_outcome

    verdict = inspect_answer_completeness("| A | B |\n|---|---|\n| 1 | 2 |\n| 3")
    outcome = output_validation_outcome({"final_ui": {"answer_completeness": verdict.as_dict()}})

    assert outcome is not None
    assert outcome["fulfillment_status"] == "partially_fulfilled"
    assert outcome["retryable"] is True


def test_a_short_final_row_followed_by_prose_is_a_choice_not_a_cut() -> None:
    """The tail check, exercised. Added because sabotaging it left the family green.

    A ragged last row is only evidence of truncation when the text STOPS there. If prose follows,
    the table finished and the author moved on -- flagging it would call a deliberate layout a cut.
    The earlier prose test used a COMPLETE table, so it never reached this guard.
    """

    ragged_then_prose = (
        "| A | B | C |\n|---|---|---|\n| 1 | 2 | 3 |\n| 4 | 5 |\n\nTotals omitted for the last row."
    )

    assert not _incomplete(ragged_then_prose)
