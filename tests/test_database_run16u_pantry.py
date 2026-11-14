import sqlite3

from core.database_work_state import sql_with_real_columns


def _make(tmp_path, monkeypatch):
    import core.plugin_tools as pt

    root = tmp_path / "databases"
    root.mkdir()
    monkeypatch.setattr(pt, "plugin_scratch_dir", lambda _n: tmp_path)
    for stem, ddl in (("kitchen", "create table items(id integer, name text, expires text, shelf text)"), ("tools", "create table t(id integer, kind text)")):
        c = sqlite3.connect(root / f"{stem}.sqlite")
        c.execute(ddl)
        c.commit()
        c.close()


def test_wrong_database_name_and_expiry_column_are_repaired(tmp_path, monkeypatch):
    _make(tmp_path, monkeypatch)
    db, sql = sql_with_real_columns("workspace", "SELECT name, expiration_date FROM items ORDER BY expiration_date", "no such column: expiration_date")
    assert db == "kitchen"
    assert "expires" in sql and "expiration_date" not in sql


def test_item_column_becomes_name(tmp_path, monkeypatch):
    _make(tmp_path, monkeypatch)
    db, sql = sql_with_real_columns("kitchen", "SELECT item FROM items", "no such column: item")
    assert (db, sql) == ("kitchen", "SELECT name FROM items")


import uuid
from types import SimpleNamespace

from tests.test_database_run16i_cbc915ec import _db
from tests.test_database_run16r_db8a859a import BOOKS


def test_a_failed_read_after_a_good_one_never_replaces_it(tmp_path, monkeypatch) -> None:
    from apps.vool_agent import VoolAgent
    from core import plugin_tools, tool_registry
    from core.database_work_state import note_database_demand
    from core.execution.models import ToolIntentExecution
    from core.memory_first_router import ModelExecutionDecision
    from core.runtime_flags import override
    from tests._vool_database_pack import isolated_db_world
    from tests.test_a_narrated_batch_does_not_vanish import _call

    def query(sql: str):
        arguments = {"name": "library_db", "sql": sql, "row_limit": 20}
        return ModelExecutionDecision(
            source="scripted", task_hash="h", used_model=True, confidence=0.8,
            structured_output={"intent": "vool-database.query", "arguments": arguments},
            tool_calls=(_call("vool-database.query", **arguments),), validation_state="validated", provider_id="p",
        )

    calls: list[int] = []

    class Router:
        def resolve_tool_intent(self, **kwargs):
            calls.append(1)
            return query("SELECT COUNT(*) FROM books WHERE year < 1950" if len(calls) == 1 else "SELECT * FROM pantry")

        def resolve(self, **kwargs):
            return SimpleNamespace(output_text="Emma (1815) and Ulysses (1922).", structured_output=None, provider_id="p", used_model=True,
                                   source="scripted", validation_state="validated", confidence=0.8, trust_score=0.8, details={},
                                   task_hash="h", tool_calls=())

    with override("plugin_runtime_tools", True):
        isolated_db_world(tmp_path, monkeypatch)
        plugin_tools.load_all(tmp_path)
        _db("library", BOOKS)
        sent: list[str] = []

        def fake_execute(payload, **_kw):
            sql = str(dict(payload.get("arguments") or {}).get("sql") or "")
            sent.append(sql)
            if "pantry" in sql:
                return ToolIntentExecution(handled=True, ok=False, status="sql_error", mode="tool_failed", tool_name="vool-database.query",
                                           response_text="no such table: pantry", user_safe_response_text="no such table: pantry", details={})
            return ToolIntentExecution(handled=True, ok=True, status="executed", mode="tool_executed", tool_name="vool-database.query",
                                       response_text="ok", user_safe_response_text="ok",
                                       details={"observation": {"rows": [[2]], "columns": ["COUNT(*)"]}})

        agent = VoolAgent.__new__(VoolAgent)
        agent.memory_router = Router()
        agent._should_keep_ai_first_chat_lane = lambda **_kw: False
        agent._should_run_builder_controller = lambda **_kw: False
        agent._plan_tool_workflow = lambda **_kw: SimpleNamespace(handled=False, stop_after=False, next_payload=None, reason="")
        agent.hive_activity_tracker = None
        agent.public_hive_bridge = None
        agent._execute_tool_intent = fake_execute
        sid = f"openclaw:{uuid.uuid4().hex[:20]}"
        ctx = {"surface": "api", "session_id": sid, "runtime_session_id": sid}
        note_database_demand(ctx)
        try:
            result = agent._maybe_execute_model_tool_intent(
                task=SimpleNamespace(task_id="t6"), effective_input="and how many books came out before 1950",
                classification={"task_class": "unknown"}, interpretation=SimpleNamespace(), context_result=SimpleNamespace(),
                persona=SimpleNamespace(), session_id=sid, source_context=ctx, surface="api",
            )
        finally:
            tool_registry.reset()
    text = str((result or {}).get("response") or "")
    assert "could not find" not in text.lower(), text


def test_a_failed_later_read_does_not_make_the_good_earlier_read_empty() -> None:
    from core.agent_runtime.research_tool_loop_facade import _database_last_result_empty

    good = {"tool_name": "vool-database.query", "ok": True, "observation": {"rows": [[2]]}}
    bad = {"tool_name": "vool-database.query", "ok": False, "observation": "no such table: pantry"}
    assert _database_last_result_empty([good, bad]) is False
    assert _database_last_result_empty([bad]) is True


def test_a_preview_only_reply_says_nothing_was_applied(monkeypatch) -> None:
    import core.final_answer_authorship as fa
    import core.turn_model_call_ledger as ledger

    monkeypatch.setattr(ledger, "turn_call_accounting", lambda _ctx: {"tools": ["vool-database.migrate.preview"]})
    monkeypatch.setattr(fa, "_runtime_tool_names", lambda: {"vool database migrate preview": "vool-database.migrate.preview"})
    out = fa._preview_only_stated("Let me preview the change first.", {})
    assert out is not None and "nothing was applied" in out[0]
    assert fa._preview_only_stated(out[0], {}) is None


def test_the_database_rows_support_the_count_the_model_wrote() -> None:
    from core.agent_runtime.response_policy_tool_history import tool_history_observation_payload
    from core.claim_support import match_claims

    execution = SimpleNamespace(ok=True, status="executed", mode="tool_executed", tool_name="vool-database.query", response_text="x",
                                details={"observation": {"rows": [[5]], "columns": ["count"], "ok": True}})
    note = tool_history_observation_payload(execution=execution, tool_name="vool-database.query")
    claims = match_claims(answer="There are 5 books.", notes=[note], request_text="how many books are there").claims
    assert claims and all(c.status == "supported" for c in claims)


def test_the_read_of_the_returns_answers_how_many_are_out() -> None:
    from core.agent_runtime.research_tool_loop_facade import _database_step_answering

    tools = {"tool_name": "vool-database.query", "ok": True, "arguments": {"sql": "SELECT COUNT(*) FROM tools WHERE bought IS NOT NULL"}, "observation": {"rows": [[6]]}}
    out = {"tool_name": "vool-database.query", "ok": True, "arguments": {"sql": "SELECT COUNT(*) FROM tools WHERE x"}, "observation": {"rows": [[3]]}}
    out["arguments"]["sql"] = "SELECT COUNT(*) FROM borrowings WHERE returned_on IS NULL"
    assert _database_step_answering("how many are out right now", [tools, out]) == 1
    assert _database_step_answering("how many are out right now", [out, tools]) == 0


def test_a_case_only_swap_finds_the_database_when_the_name_given_is_wrong(tmp_path, monkeypatch) -> None:
    from core.database_work_state import sql_with_real_values

    _make(tmp_path, monkeypatch)
    c = sqlite3.connect(tmp_path / "databases" / "kitchen.sqlite")
    c.execute("insert into items values (1,'Flour','2026-11-20','top')")
    c.commit()
    c.close()
    from core.database_work_state import database_named_by_tables

    sql = "SELECT * FROM items WHERE name = 'flour'"
    assert database_named_by_tables("workspace", sql) == "kitchen"
    assert "'Flour'" in sql_with_real_values("kitchen", sql)


def test_a_reply_that_says_there_is_none_after_a_read_that_found_rows_is_the_rows() -> None:
    from core.agent_runtime.research_tool_loop_facade import _database_reply_contradicts_its_rows

    step = {"tool_name": "vool-database.query", "ok": True, "arguments": {"name": "pantry", "sql": "SELECT name FROM items WHERE quantity = 0"},
            "observation": {"columns": ["name"], "rows": [["Lentils"]]}}
    out = _database_reply_contradicts_its_rows("There are no items listed as out of stock.", "which ones are out of stock", [step])
    assert "Lentils" in out
    assert _database_reply_contradicts_its_rows("Lentils is out of stock.", "which ones are out of stock", [step]) == ""


def test_how_many_is_counted_in_rows_not_summed_over_a_quantity_column() -> None:
    from core.agent_runtime.research_tool_loop_facade import _database_query_contradicts_the_ask_checks

    payload = {"arguments": {"name": "pantry", "sql": "SELECT SUM(quantity) FROM items WHERE shelf = 'bottom'"}}
    assert _database_query_contradicts_the_ask_checks("vool-database.query", payload, "how many are on the bottom shelf", []) is not None
    assert _database_query_contradicts_the_ask_checks("vool-database.query", payload, "what is the total quantity on the bottom shelf", []) is None


def test_a_count_of_zero_on_an_is_null_filter_is_an_empty_read_and_not_evidence() -> None:
    from core.agent_runtime.research_tool_loop_facade import _database_empty_read, _database_rows_are_evidence

    zero = {"tool_name": "vool-database.query", "ok": True, "arguments": {"sql": "SELECT COUNT(*) FROM loans WHERE returned IS NULL"},
            "observation": {"rows": [[0]], "columns": ["count"]}}
    assert _database_empty_read([zero]) is True
    assert _database_rows_are_evidence(zero) is False
    assert _database_rows_are_evidence({**zero, "observation": {"rows": [[3]]}}) is True


def test_a_schema_observation_supports_a_sentence_about_the_tables() -> None:
    from core.agent_runtime.response_policy_tool_history import tool_history_observation_payload

    execution = SimpleNamespace(ok=True, status="executed", mode="tool_executed", tool_name="vool-database.schema", response_text="x",
                                details={"observation": {"intent": "vool-database.schema", "tables": ["books", "members", "loans"], "ok": True}})
    note = tool_history_observation_payload(execution=execution, tool_name="vool-database.schema")
    assert "members" in str(note.get("summary"))


def test_the_column_that_holds_the_matched_value_is_the_column_meant(tmp_path, monkeypatch) -> None:
    import core.plugin_tools as pt
    from core.database_work_state import sql_with_real_columns

    root = tmp_path / "databases"
    root.mkdir()
    monkeypatch.setattr(pt, "plugin_scratch_dir", lambda _n: tmp_path)
    c = sqlite3.connect(root / "toolshed.sqlite")
    c.execute("create table tools(id integer, name text, kind text)")
    c.execute("insert into tools values (1,'Drill','power'),(2,'Saw','hand')")
    c.commit()
    c.close()
    db, sql = sql_with_real_columns("workspace", "SELECT * FROM tools WHERE category = 'power tools'", "no such column: category")
    assert db == "toolshed" and "kind = 'power tools'" in sql


def test_a_reply_that_names_only_some_of_the_rows_is_the_rows() -> None:
    from core.agent_runtime.research_tool_loop_facade import _reply_leaves_out_a_row

    step = {"tool_name": "vool-database.query", "ok": True, "observation": {"columns": ["name"], "rows": [["Marta"], ["Noor"]]}}
    assert _reply_leaves_out_a_row("The Drill was had by Noor.", [step]) is True
    assert _reply_leaves_out_a_row("Marta and Noor.", [step]) is False
    assert _reply_leaves_out_a_row("Nobody.", [step]) is False


def test_the_person_the_question_names_is_the_subject_of_what_is_out() -> None:
    from core.agent_runtime.orchestrator import database_holding_answer

    step = {"tool_name": "vool-database.query", "ok": True,
            "observation": {"columns": ["name", "returned_on"], "rows": [["Ladder", None], ["Sander", None]]}}
    text = database_holding_answer("what does Noor have out right now", [step])
    assert text.startswith("Noor has out right now: Ladder, Sander")
    assert database_holding_answer("what is out right now", [step]).startswith("They have")


def test_the_database_name_used_as_a_table_is_replaced_by_the_table_that_holds_the_columns(tmp_path, monkeypatch) -> None:
    from core.database_work_state import sql_with_real_table

    _make(tmp_path, monkeypatch)
    db, sql = sql_with_real_table("pantry", "SELECT name, expires FROM kitchen ORDER BY expires", "no such table: kitchen")
    assert (db, sql) == ("kitchen", "SELECT name, expires FROM items ORDER BY expires")
    assert sql_with_real_table("workspace", "SELECT * FROM pantry WHERE id = 1", "no such table: pantry") == ("", "")  # nothing to tell tables apart by


def _q(columns, rows):
    return {"tool_name": "vool-database.query", "ok": True, "observation": {"columns": columns, "rows": rows}}


def test_counts_overviews_and_lists_are_said_in_words() -> None:
    from core.agent_runtime.orchestrator import database_plain_answer

    assert database_plain_answer("how many books are there", [_q(["COUNT(*)"], [[5]])]) == "There are 5 books."
    assert database_plain_answer("how many members are there", [_q(["COUNT(*)"], [[1]])]) == "There is 1 member."
    assert database_plain_answer("count the loans", [_q(["COUNT(*)"], [[3]])]) == "The answer is 3."
    assert database_plain_answer("what do I have in my library db", [_q(["table_name", "row_count"], [["books", 5], ["members", 3]])]) == (
        "Your database has these tables: books (5 rows), members (3 rows)."
    )
    listed = database_plain_answer("how many are still out on loan", [_q(["id", "title"], [[1, "Dune"], [2, "Emma"], [3, "Ulysses"]])])
    assert listed.startswith("3 in all:") and "- Dune" in listed and "id:" not in listed


def test_an_overview_is_not_answered_from_one_table_alone(tmp_path, monkeypatch) -> None:
    import core.agent_runtime.research_tool_loop_facade as facade
    from core.agent_runtime.research_tool_loop_facade import _database_join_missing

    monkeypatch.setattr(facade, "_database_call_suggestion", lambda _q: "name=kitchen, sql=SELECT 'items' AS table_name, COUNT(*) AS row_count FROM items UNION ALL SELECT 't', COUNT(*) FROM t")
    one = {"tool_name": "vool-database.query", "ok": True, "arguments": {"sql": "SELECT * FROM items"}, "observation": {"rows": [[1]]}}
    union = {**one, "arguments": {"sql": "SELECT 'items' AS table_name, COUNT(*) AS row_count FROM items UNION ALL SELECT 't', COUNT(*) FROM t"}}
    assert _database_join_missing("what do I have in my kitchen db", [one]) is True
    assert _database_join_missing("what do I have in my kitchen db", [one, union]) is False


def test_a_who_had_it_reply_with_no_read_of_the_loans_is_not_sent_as_a_fact(monkeypatch) -> None:
    import core.agent_runtime.research_tool_loop_facade as facade

    monkeypatch.setattr(facade, "_database_call_suggestion", lambda _q: "name=toolshed, sql=SELECT n.name FROM borrowings b JOIN neighbours n ON n.id = b.neighbour_id WHERE b.tool_id = 1")
    step = {"tool_name": "vool-database.query", "ok": True, "arguments": {"sql": "SELECT * FROM tools"}, "observation": {"columns": ["id", "name"], "rows": [[1, "Drill"]]}}
    out = facade._database_reply_contradicts_its_rows("Noor has had the Drill.", "who has had the Drill", [step], told=True)
    assert "will not guess" in out and "Noor" not in out


def test_a_database_turn_does_not_carry_the_workspace_file_tools() -> None:
    from core.tool_offer_assembly import _FILE_WORDS_RE

    assert not _FILE_WORDS_RE.search("whos got the ladder")
    assert _FILE_WORDS_RE.search("find the file with my tools list")
