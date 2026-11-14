"""Database run 16r on db8a859a (Mac, library 13/18): a column named wrongly, a database named wrongly, the oldest
book without its title."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import sqlite3
import uuid
from pathlib import Path
from types import SimpleNamespace

from core.agent_runtime.research_tool_loop_facade import _database_query_carries_over_the_last_ask
from tests.test_database_plain_ask_lane import world
from tests.test_database_run16i_cbc915ec import LIBRARY, _db

HANDLER = Path(__file__).resolve().parent.parent / "plugins" / "vool-database" / "bin" / "db_handler"
BOOKS = "create table books(id integer primary key, title text, author text, year int); insert into books values (1,'Emma','Austen',1815),(2,'Ulysses','Joyce',1922);"


def _handler(tmp_path, monkeypatch, databases: dict[str, str]):
    (tmp_path / "databases").mkdir(exist_ok=True)
    for name, script in databases.items():
        c = sqlite3.connect(tmp_path / "databases" / f"{name}.sqlite")
        c.executescript(script)
        c.commit()
        c.close()
    loader = importlib.machinery.SourceFileLoader("db_handler_16r", str(HANDLER))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    monkeypatch.setattr(module, "scratch_root", lambda: str(tmp_path))

    def call(intent: str, **arguments) -> dict:
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"intent": intent, "arguments": arguments})))
        out = io.StringIO()
        monkeypatch.setattr("sys.stdout", out)
        module.main()
        return json.loads(out.getvalue())

    return call


def test_the_name_the_person_used_for_a_database_that_exists_is_accepted(tmp_path, monkeypatch) -> None:
    call = _handler(tmp_path, monkeypatch, {"library": BOOKS})
    assert call("vool-database.query", name="library_db", sql="SELECT COUNT(*) FROM books")["ok"] is True  # contains it
    assert call("vool-database.schema", name="workspace")["ok"] is True  # the only database there is
    two = _handler(tmp_path, monkeypatch, {"pantry": BOOKS})
    refused = two("vool-database.query", name="workspace", sql="SELECT COUNT(*) FROM books")
    assert refused["ok"] is False and "library" in json.dumps(refused)  # two to choose from: the names are listed
    assert two("vool-database.migrate.apply", name="library_db", statements=["ALTER TABLE books ADD COLUMN x TEXT;"])["ok"] is False


def test_a_column_named_wrongly_is_replaced_by_the_real_one(world) -> None:
    from core.database_work_state import sql_with_real_columns

    _db("library", BOOKS)
    error = "no such column: publication_year"
    assert sql_with_real_columns("library_db", "SELECT title, publication_year FROM books ORDER BY publication_year ASC LIMIT 1", error) == (
        "library", "SELECT title, year FROM books ORDER BY year ASC LIMIT 1")
    assert sql_with_real_columns("library", "SELECT COUNT(*) FROM books WHERE status = 'out'", "no such column: status") == ("", "")
    assert sql_with_real_columns("library", "SELECT year FROM books", "no such column: year") == ("", "")


def test_the_oldest_row_is_read_whole_not_as_a_lone_minimum() -> None:
    said = _database_query_carries_over_the_last_ask("SELECT MIN(year) AS oldest_year FROM books;", "hey what's the oldest book in there", [])
    assert "ORDER BY year ASC LIMIT 1" in said
    told = [{"correction": True, "observation": f"failed and nothing was changed: {said}"}]
    assert _database_query_carries_over_the_last_ask("SELECT MIN(year) FROM books", "hey what's the oldest book in there", told) == ""
    assert _database_query_carries_over_the_last_ask("SELECT MIN(year) FROM books", "how many years back do the books go", []) == ""


def test_the_loop_reads_again_with_the_real_column_before_the_model_is_asked(tmp_path, monkeypatch) -> None:
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

    class Router:
        def resolve_tool_intent(self, **kwargs):
            return query("SELECT title, publication_year FROM books WHERE publication_year < 1950")

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
            if "publication_year" in sql:
                return ToolIntentExecution(handled=True, ok=False, status="sql_error", mode="tool_failed", tool_name="vool-database.query",
                                           response_text="no such column: publication_year", user_safe_response_text="no such column: publication_year", details={})
            return ToolIntentExecution(handled=True, ok=True, status="executed", mode="tool_executed", tool_name="vool-database.query",
                                       response_text="ok", user_safe_response_text="ok",
                                       details={"observation": {"rows": [["Emma", 1815], ["Ulysses", 1922]], "columns": ["title", "year"]}})

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
            agent._maybe_execute_model_tool_intent(
                task=SimpleNamespace(task_id="t6"), effective_input="which books came out before 1950?",
                classification={"task_class": "unknown"}, interpretation=SimpleNamespace(), context_result=SimpleNamespace(),
                persona=SimpleNamespace(), session_id=sid, source_context=ctx, surface="api",
            )
        finally:
            tool_registry.reset()
    assert "SELECT title, year FROM books WHERE year < 1950" in sent, sent


# ── the toolshed world of the same run ──────────────────────────────────────────────────────────────────────

from tests.test_database_run16i_cbc915ec import TOOLSHED  # noqa: E402

TOOLSHED2 = TOOLSHED + "insert into neighbours values (2,'Pavel'); insert into tools values (3,'Ladder','hand');"


def test_how_many_are_out_counts_the_rows_not_yet_returned_in_the_table_that_holds_them(world) -> None:  # noqa: F811
    from core.database_work_state import suggest_database_query

    _db("toolshed", TOOLSHED2)
    assert suggest_database_query("ok and how many are out right now") == "name=toolshed, sql=SELECT COUNT(*) AS count FROM borrowings WHERE returned_on IS NULL"


def test_a_case_only_difference_is_swapped_for_the_stored_spelling(world) -> None:  # noqa: F811
    from core.database_work_state import sql_with_real_values

    _db("toolshed", TOOLSHED2)
    assert sql_with_real_values("toolshed", "SELECT * FROM tools WHERE name = 'ladder'") == "SELECT * FROM tools WHERE name = 'Ladder'"
    assert sql_with_real_values("toolshed", "SELECT * FROM tools WHERE name = 'Ladder'") == ""


def test_who_has_had_it_is_not_limited_to_what_is_still_out() -> None:
    sql = "SELECT * FROM borrowings WHERE tool_id = 1 AND returned_on IS NULL"
    said = _database_query_carries_over_the_last_ask(sql, "who has had the Drill", [])
    assert "EVER" in said
    told = [{"correction": True, "observation": f"failed and nothing was changed: {said}"}]
    assert _database_query_carries_over_the_last_ask(sql, "who has had the Drill", told) == ""
    assert _database_query_carries_over_the_last_ask(sql, "who has the Drill right now", []) == ""
    assert _database_query_carries_over_the_last_ask(sql, "is anything still out", []) == ""


def test_a_count_that_never_read_the_borrowings_does_not_answer_how_many_are_out(world) -> None:  # noqa: F811
    from core.agent_runtime.research_tool_loop_facade import _database_join_missing

    _db("toolshed", TOOLSHED2)
    tools = {"tool_name": "vool-database.query", "ok": True, "arguments": {"sql": "SELECT COUNT(*) FROM tools"},
             "observation": {"rows": [[6]], "columns": ["COUNT(*)"]}}
    assert _database_join_missing("ok and how many are out right now", [tools]) is True
    borrowings = {**tools, "arguments": {"sql": "SELECT COUNT(*) FROM borrowings WHERE returned_on IS NULL"}}
    assert _database_join_missing("ok and how many are out right now", [tools, borrowings]) is False


def test_the_loop_reads_the_rows_when_the_model_answered_after_a_schema_read_alone(tmp_path, monkeypatch) -> None:
    """Toolshed 12 of run 16r: schema(name='shed_db'), no query, and a neighbour 'named Alice' who is not there."""
    from apps.vool_agent import VoolAgent
    from core import plugin_tools, tool_registry
    from core.database_work_state import note_database_demand
    from core.execution.models import ToolIntentExecution
    from core.memory_first_router import ModelExecutionDecision
    from core.runtime_flags import override
    from tests._vool_database_pack import isolated_db_world
    from tests.test_a_narrated_batch_does_not_vanish import _call

    def decision(intent: str, **arguments):
        return ModelExecutionDecision(
            source="scripted", task_hash="h", used_model=True, confidence=0.8,
            structured_output={"intent": intent, "arguments": arguments},
            tool_calls=(_call(intent, **arguments),), validation_state="validated", provider_id="p",
        )

    class Router:
        calls = 0

        def resolve_tool_intent(self, **kwargs):
            Router.calls += 1
            if Router.calls == 1:
                return decision("vool-database.schema", name="shed_db", table="tools")
            if Router.calls == 2:
                return decision("respond.direct", message="The neighbour who lives on Oak Ave is named Alice.")
            return decision("respond.direct", message="Pavel lives on Oak Ave.")

        def resolve(self, **kwargs):
            return SimpleNamespace(output_text="Pavel lives on Oak Ave.", structured_output=None, provider_id="p", used_model=True,
                                   source="scripted", validation_state="validated", confidence=0.8, trust_score=0.8, details={},
                                   task_hash="h", tool_calls=())

    with override("plugin_runtime_tools", True):
        isolated_db_world(tmp_path, monkeypatch)
        plugin_tools.load_all(tmp_path)
        _db("toolshed", TOOLSHED2)
        sent: list[str] = []

        def fake_execute(payload, **_kw):
            sent.append(str(payload.get("intent")) + ":" + str(dict(payload.get("arguments") or {}).get("sql") or ""))
            if payload.get("intent") == "vool-database.schema":
                return ToolIntentExecution(handled=True, ok=True, status="executed", mode="tool_executed", tool_name="vool-database.schema",
                                           response_text="ok", user_safe_response_text="ok", details={"observation": {"tables": ["tools"]}})
            return ToolIntentExecution(handled=True, ok=True, status="executed", mode="tool_executed", tool_name="vool-database.query",
                                       response_text="ok", user_safe_response_text="ok",
                                       details={"observation": {"rows": [[1, "Pavel"]], "columns": ["id", "name"]}})

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
                task=SimpleNamespace(task_id="t7"), effective_input="sorry, which neighbour lives on Oak Ave",
                classification={"task_class": "unknown"}, interpretation=SimpleNamespace(), context_result=SimpleNamespace(),
                persona=SimpleNamespace(), session_id=sid, source_context=ctx, surface="api",
            )
        finally:
            tool_registry.reset()
    assert any(call.startswith("vool-database.query:SELECT * FROM neighbours") for call in sent), sent
    assert "Alice" not in str((result or {}).get("response") or "")
