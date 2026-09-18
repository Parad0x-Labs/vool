"""The footer builder must render or return nothing -- it must never raise mid-stream.

`format_provenance_footer` is called generator-side (`core/web/api/runtime.py` `_response_commit`
and the stream tail), which is AFTER the response headers are committed. An exception there does not
become an error the client can render; it truncates the body, and the browser reports a failed load
with nothing to show.

Reproduced at c6eed761: `parts` was assigned only inside `if provenance.model_ran:` and
`elif provenance.answer_source in {"tool","deterministic"}:`. A turn that is RECORDED, ran no model,
and whose answer_source is neither -- "none" is a real value, set by the module itself -- matched
neither arm, and the file-count append immediately below raised::

    UnboundLocalError: cannot access local variable 'parts' where it is not associated with a value

The same lines hid a second, quieter bug: the two branches further down REPLACE `parts` wholesale, so
on a turn that wrote files and then took the no-model arm, the count appended just above was
discarded. Files written are the least droppable thing on a provenance line.
"""

from __future__ import annotations

import pytest

from core import response_provenance as rp

_BASE = dict(
    lane="runtime",
    model_label="",
    model_ran=False,
    tokens=0,
    usd=0.0,
    actions=0,
    route="",
    build="0.5.0",
    recorded=True,
    lane_recorded=False,
    tool="",
    extra_tools=0,
    routing_calls=0,
    routing_lane="",
    local_only=False,
    answer_source="none",
)


def _footer(**overrides) -> str:
    """Drive the real formatter with a fully-specified provenance object."""

    provenance = rp.TurnProvenance(**{**_BASE, **overrides})
    original = rp.read_turn_provenance
    rp.read_turn_provenance = lambda *args, **kwargs: provenance
    try:
        return rp.format_provenance_footer({}, {})
    finally:
        rp.read_turn_provenance = original


# ---------------------------------------------------------------------------------------------
# G1 -- the reproduction
# ---------------------------------------------------------------------------------------------


def test_a_recorded_turn_with_no_model_and_files_does_not_raise() -> None:
    """The exact shape that raised UnboundLocalError."""

    assert _footer(actions=1)


def test_the_file_count_survives_the_no_model_branch() -> None:
    """The quieter half: the count was appended and then thrown away by a wholesale reassignment."""

    assert "1 file" in _footer(actions=1)
    assert "3 files" in _footer(actions=3)


# ---------------------------------------------------------------------------------------------
# The property, across every combination that reaches this function
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("answer_source", ("none", "tool", "deterministic", "model", "", "unexpected"))
@pytest.mark.parametrize("model_ran", (True, False))
@pytest.mark.parametrize("actions", (0, 1, 2))
def test_the_footer_never_raises_for_any_combination(answer_source: str, model_ran: bool, actions: int) -> None:
    """Render or return "" -- the one thing it may not do post-header is raise."""

    try:
        result = _footer(answer_source=answer_source, model_ran=model_ran, actions=actions)
    except Exception as exc:
        pytest.fail(f"footer raised {type(exc).__name__}: {exc}")
    assert isinstance(result, str)


@pytest.mark.parametrize("participation", ("none", "routed_only", "attempted_failed_then_tool_answer", ""))
def test_every_participation_shape_renders(participation: str) -> None:
    try:
        _footer(actions=1, model_participation=participation)
    except TypeError:
        pytest.skip("model_participation is derived, not a constructor field")
    except Exception as exc:
        pytest.fail(f"footer raised {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------------------------
# NEGATIVE CONTROLS -- established footers must be unchanged
# ---------------------------------------------------------------------------------------------


def test_an_unrecorded_turn_still_gets_no_footer_at_all() -> None:
    """Silence, not `runtime | no model`, is the documented answer when nothing was recorded."""

    assert _footer(recorded=False) == ""


def test_the_no_model_line_is_unchanged_when_no_files_were_written() -> None:
    assert _footer(actions=0) == "`runtime | no model | build 0.5.0`"


def test_a_deterministic_tool_footer_keeps_its_existing_shape_and_order() -> None:
    """The file segment must stay where it was for the branches that already worked."""

    assert (
        _footer(answer_source="tool", tool="workspace.write_file", actions=2)
        == "`runtime | workspace.write_file | 2 files | no model | build 0.5.0`"
    )


def test_local_only_disclosure_still_lands_last() -> None:
    footer = _footer(actions=1, local_only=True)

    assert footer.rstrip("`").endswith("local only · cloud blocked")
