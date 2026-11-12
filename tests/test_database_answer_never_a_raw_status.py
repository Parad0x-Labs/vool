"""Database run 16d: a query that ran must never end as "1 of 3 tool steps succeeded"; a database ask answered
"I don't have access" with no call is nudged once; the names block carries the flag columns' real values."""
from __future__ import annotations

import sqlite3

from core.agent_runtime.orchestrator import tool_loop_final_message


class _Empty:
    structured_output = None
    output_text = ""


def _step(**kw):
    return {"tool_name": "vool-database.query", "ok": True, "summary": "Plugin `vool-database.query` returned output", **kw}


def test_rows_are_rendered_from_the_observation_when_no_model_wrote_the_answer() -> None:
    steps = [_step(observation={"columns": ["id", "returned"], "rows": [[2, 0], [3, 0]], "truncated": False})]
    text = tool_loop_final_message(_Empty(), steps)
    assert "tool steps succeeded" not in text and "id: 2, returned: 0" in text and "2 row(s)" in text


def test_a_count_and_an_empty_result_read_plainly() -> None:
    assert tool_loop_final_message(_Empty(), [_step(observation={"columns": ["count"], "rows": [[3]]})]) == "count: 3"
    assert "no matching rows" in tool_loop_final_message(_Empty(), [_step(observation={"columns": ["id"], "rows": []})])


def test_a_failed_database_step_is_said_plainly() -> None:
    step = {"tool_name": "vool-database.query", "ok": False, "status": "sql_error", "summary": "no such column: foo"}
    text = tool_loop_final_message(_Empty(), [step])
    assert "did not work" in text and "no such column: foo" in text and "tool steps succeeded" not in text


def test_other_tools_keep_their_summary() -> None:
    step = {"tool_name": "web.search", "ok": True, "summary": "3 results"}
    assert "1 of 1 tool steps succeeded" in tool_loop_final_message(_Empty(), [step])


def test_the_no_answer_detector_catches_giving_up_not_answers() -> None:
    from core.agent_runtime.research_tool_loop_facade import _is_database_no_answer

    for text in [
        "I don't have access to a list of members.",
        "The information provided does not mention anything about Tomas.",
        "Could you please clarify which list you mean?",
    ]:
        assert _is_database_no_answer(text), text
    for text in ["There are 3 books out on loan.", "Tomas has returned everything."]:
        assert not _is_database_no_answer(text), text


def test_the_names_block_shows_the_real_values_of_a_flag_column(tmp_path, monkeypatch) -> None:
    from core.database_work_state import database_catalog_block
    from core.plugin_tools import plugin_scratch_dir

    monkeypatch.setenv("VOOL_PLUGIN_SCRATCH_ROOT", str(tmp_path))
    root = plugin_scratch_dir("vool-database") / "databases"
    root.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(root / "library.sqlite")
    c.executescript("create table loans(id integer primary key, book_id int, returned int); insert into loans values (1,1,1),(2,3,0);")
    c.commit(); c.close()
    assert "returned in {0,1}" in database_catalog_block()
