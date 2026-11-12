"""A database refusal that names the real tables/columns is handed back to the model once or
twice instead of ending the turn as raw engine text (served runs 8-11: publication_year,
holder, plant_name, "workspace")."""
from __future__ import annotations

from tests.test_tool_argument_correction import _Execution, _Facade


def _retry(status: str, steps=None, intent="vool-database.query") -> tuple[bool, list]:
    facade = _Facade()
    steps = steps if steps is not None else []
    ok = facade._retry_as_observation(
        execution=_Execution(status=status, response_text="no such column: publication_year; the tables and columns that exist are: books(id, title, author, year)"),
        tool_payload={"intent": intent}, executed_steps=steps, loop_source_context={}, seen_tool_payloads=set(),
    )
    return ok, steps


def test_a_wrong_column_is_returned_to_the_model_with_the_real_names() -> None:
    ok, steps = _retry("sql_error")
    assert ok is True
    assert "books(id, title, author, year)" in steps[0]["observation"] and "nothing was changed" in steps[0]["observation"]


def test_unknown_database_is_retried_but_not_for_other_tools_with_the_same_word() -> None:
    assert _retry("unknown_database")[0] is True
    assert _retry("sql_error", intent="vool-jira.search")[0] is False


def test_the_bound_is_two_corrections() -> None:
    steps = [{"correction": True}, {"correction": True}]
    assert _retry("sql_error", steps)[0] is False


def test_real_world_failures_are_still_answers() -> None:
    assert _retry("handler_failed")[0] is False
    assert _retry("preview_failed")[0] is False
