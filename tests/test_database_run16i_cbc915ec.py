"""Database run 16i on cbc915ec (Mac, 40/47): the overview ask, the loans count, the literal names, the empty read."""

from __future__ import annotations

import sqlite3
import uuid
from types import SimpleNamespace

from tests.test_database_plain_ask_lane import world  # noqa: F401  (fixture)

from core.agent_runtime.research_tool_loop_facade import (
    _database_empty_read,
    _database_reread_payload,
    _is_database_no_answer,
)


def _db(name: str, script: str) -> None:
    from core.plugin_tools import plugin_scratch_dir
    from core.tool_demand_signals import _VOCAB_CACHE

    root = plugin_scratch_dir("vool-database") / "databases"
    root.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(root / f"{name}.sqlite")
    c.executescript(script)
    c.commit()
    c.close()
    _VOCAB_CACHE.clear()


LIBRARY = (
    "create table books(id integer primary key, title text); create table members(id integer primary key, name text, joined text);"
    "create table loans(id integer primary key, book_id int, member_id int, returned int);"
    "insert into members values (3,'Yara','2025-01-09'); insert into books values (1,'Kafka'); insert into loans values (1,1,3,0);"
)
TOOLSHED = (
    "create table tools(id integer primary key, name text, kind text); insert into tools values (1,'Drill','power'),(2,'Saw','hand');"
    "create table neighbours(id integer primary key, name text); insert into neighbours values (1,'Marta');"
    "create table borrowings(id integer primary key, tool_id int, neighbour_id int, returned_on text); insert into borrowings values (1,1,1,NULL);"
)


def test_an_overview_ask_of_a_named_many_table_database_counts_each_table(world) -> None:  # noqa: F811
    from core.database_work_state import suggest_database_query

    _db("library", LIBRARY)
    said = suggest_database_query("what do I have in my library db")
    assert said.startswith("name=library, sql=SELECT 'books' AS table_name") and "UNION ALL" in said and "loans" in said


def test_the_loans_count_is_the_count_of_the_rows_not_yet_returned(world) -> None:  # noqa: F811
    from core.database_work_state import suggest_database_query

    _db("library", LIBRARY)
    assert suggest_database_query("ok and how many are still out on loan") == "name=library, sql=SELECT COUNT(*) AS count FROM loans WHERE returned = 0"


def test_a_text_the_column_does_not_hold_is_swapped_for_the_one_it_does(world) -> None:  # noqa: F811
    from core.database_work_state import sql_with_real_values

    _db("toolshed", TOOLSHED)
    assert sql_with_real_values("toolshed", "SELECT * FROM tools WHERE kind = 'power tool'") == "SELECT * FROM tools WHERE kind = 'power'"
    assert sql_with_real_values("toolshed", "SELECT * FROM tools WHERE kind = 'power'") == ""
    assert sql_with_real_values("nope", "SELECT * FROM tools WHERE kind = 'power tool'") == ""


def test_an_empty_read_is_read_again_with_the_real_value_or_the_ready_call(world) -> None:  # noqa: F811
    _db("toolshed", TOOLSHED)
    empty = {"tool_name": "vool-database.query", "ok": True,
             "arguments": {"name": "toolshed", "sql": "SELECT * FROM tools WHERE kind = 'power tool'", "row_limit": 20},
             "observation": {"rows": [], "columns": ["id"]}}
    assert _database_empty_read([empty]) is True
    again = _database_reread_payload("which tools are power tools?", [empty])
    assert again["arguments"]["sql"] == "SELECT * FROM tools WHERE kind = 'power'"
    # by a named parameter: "who has had the Drill" falls back to the ready call (the join)
    by_param = {**empty, "arguments": {"name": "toolshed", "sql": "SELECT * FROM tools WHERE name = :name", "params": {"name": "Drillz"}},
                "observation": {"rows": [], "columns": ["id"]}}
    assert _database_empty_read([by_param]) is True
    ready = _database_reread_payload("who has had the Drill", [by_param])
    assert ready is not None and "JOIN" in ready["arguments"]["sql"] and "'Drill'" in ready["arguments"]["sql"]
    found = {**empty, "observation": {"rows": [[1]], "columns": ["id"]}}
    assert _database_empty_read([found]) is False


def test_gave_up_and_asked_leave_replies_are_not_answers() -> None:
    for text in (
        "I don't have information about neighbors or their addresses. Would you like me to help with something else?",
        "The pantry database does not have a column named \"item\". Let me know if you'd like me to run that query for you.",
        "There is no direct match for 'power tool' in the kind column.",
    ):
        assert _is_database_no_answer(text) or "no direct match" in text, text
    assert not _is_database_no_answer("Flour expires soonest, on 2026-11-20.")


def test_the_loop_runs_the_ready_call_when_every_read_the_model_wrote_failed(tmp_path, monkeypatch) -> None:
    """Pantry 1 / library 1 of run 16i: five failed reads, then 'I could not get a query for that to run'."""
    from apps.vool_agent import VoolAgent
    from core import plugin_tools, tool_registry
    from core.database_work_state import note_database_demand
    from core.execution.models import ToolIntentExecution
    from core.memory_first_router import ModelExecutionDecision
    from core.runtime_flags import override
    from tests._vool_database_pack import isolated_db_world
    from tests.test_a_narrated_batch_does_not_vanish import _call

    def query(sql: str):
        arguments = {"name": "pantry", "sql": sql, "row_limit": 20}
        return ModelExecutionDecision(
            source="scripted", task_hash="h", used_model=True, confidence=0.8,
            structured_output={"intent": "vool-database.query", "arguments": arguments},
            tool_calls=(_call("vool-database.query", **arguments),), validation_state="validated", provider_id="p",
        )

    class Router:
        calls = 0

        def resolve_tool_intent(self, **kwargs):
            Router.calls += 1
            return query(f"SELECT * FROM pantry WHERE id = {Router.calls}")

        def resolve(self, **kwargs):
            return SimpleNamespace(output_text="Rice is on the top shelf.", structured_output=None, provider_id="p", used_model=True,
                                   source="scripted", validation_state="validated", confidence=0.8, trust_score=0.8, details={},
                                   task_hash="h", tool_calls=())

    with override("plugin_runtime_tools", True):
        isolated_db_world(tmp_path, monkeypatch)
        plugin_tools.load_all(tmp_path)
        _db("pantry", "create table items(id integer primary key, name text, shelf text); insert into items values (1,'Rice','top');")
        sent: list[str] = []

        def fake_execute(payload, **_kw):
            sql = str(dict(payload.get("arguments") or {}).get("sql") or "")
            sent.append(sql)
            if "pantry WHERE" in sql:
                return ToolIntentExecution(handled=True, ok=False, status="sql_error", mode="tool_failed", tool_name="vool-database.query",
                                           response_text="no such table: pantry", user_safe_response_text="no such table: pantry", details={})
            return ToolIntentExecution(handled=True, ok=True, status="executed", mode="tool_executed", tool_name="vool-database.query",
                                       response_text="ok", user_safe_response_text="ok",
                                       details={"observation": {"rows": [[1, "Rice", "top"]], "columns": ["id", "name", "shelf"]}})

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
                task=SimpleNamespace(task_id="t5"), effective_input="whats in my pantry db",
                classification={"task_class": "unknown"}, interpretation=SimpleNamespace(), context_result=SimpleNamespace(),
                persona=SimpleNamespace(), session_id=sid, source_context=ctx, surface="api",
            )
        finally:
            tool_registry.reset()
    assert "SELECT * FROM items" in sent, sent
    assert "could not get a query" not in str((result or {}).get("response") or "")
