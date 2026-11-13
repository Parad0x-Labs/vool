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


def _q(rows, columns=("name", "book_id", "returned")):
    return {"tool_name": "vool-database.query", "ok": True, "observation": {"columns": list(columns), "rows": rows}}


def test_an_answer_naming_a_value_no_row_holds_is_unsupported() -> None:
    from core.agent_runtime.orchestrator import database_claims_unsupported

    steps = [_q([["Yara", 5, 1]])]
    wrong = 'Yes, Yara has one book checked out, which is **"Beloved"** (book ID 5). The `returned` column is `0`.'
    assert database_claims_unsupported(wrong, steps, "does Yara have anything checked out right now")
    right = "Yara has nothing out: her loan of book 5 was returned (returned = 1)."
    assert not database_claims_unsupported(right, steps, "does Yara have anything checked out right now")


def test_a_count_of_the_rows_and_list_numbers_are_not_unsupported() -> None:
    from core.agent_runtime.orchestrator import database_claims_unsupported

    steps = [_q([["Emma"], ["Ulysses"]], columns=("title",))]
    assert not database_claims_unsupported("Two books: \n1. **Emma**\n2. **Ulysses**", steps, "before 1950?")
    assert database_claims_unsupported("Two books: **Emma** and **Beloved**", steps, "before 1950?")
    assert not database_claims_unsupported("anything", [{"tool_name": "web.search", "ok": True}], "q")


def test_database_rows_answer_only_when_the_last_query_succeeded() -> None:
    from core.agent_runtime.orchestrator import database_rows_answer

    assert database_rows_answer([_q([[3]], columns=("count",))]) == "count: 3"
    bad = {"tool_name": "vool-database.query", "ok": False, "status": "sql_error", "summary": "no such column"}
    assert database_rows_answer([_q([[3]], columns=("count",)), bad]) == ""


def test_the_names_block_has_a_who_holds_what_example(tmp_path, monkeypatch) -> None:
    from core.database_work_state import database_catalog_block
    from core.plugin_tools import plugin_scratch_dir

    monkeypatch.setenv("VOOL_PLUGIN_SCRATCH_ROOT", str(tmp_path))
    root = plugin_scratch_dir("vool-database") / "databases"
    root.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(root / "library.sqlite")
    c.executescript(
        "create table books(id integer primary key, title text);"
        "create table members(id integer primary key, name text);"
        "create table loans(id integer primary key, book_id int, member_id int, returned int);"
    )
    c.commit(); c.close()
    block = database_catalog_block()
    assert "JOIN members ON loans.member_id = members.id" in block and "WHERE members.name = '<name>'" in block


def test_a_database_turn_does_not_carry_the_process_tool() -> None:
    from core.tool_offer_assembly import _PROCESS_WORDS_RE

    assert not _PROCESS_WORDS_RE.search("list the members, newest first")
    assert _PROCESS_WORDS_RE.search("which process uses the most memory usage")
