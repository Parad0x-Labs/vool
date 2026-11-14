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
