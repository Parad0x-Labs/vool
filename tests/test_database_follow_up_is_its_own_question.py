"""Database run 16i on d5d044b0 (Mac): a follow-up is answered from its own question, and a database turn is not
sent to the tool listing. The loop's pure helpers and the offer, on the real code."""

from __future__ import annotations

import sqlite3

import pytest

from core.agent_runtime.research_tool_loop_facade import (
    _database_join_missing,
    _database_query_carries_over_the_last_ask,
    _database_query_contradicts_the_ask,
)
from tests.test_database_plain_ask_lane import world
from tests.test_production_route_matrix import PRODUCTION_CONTEXT

SEEN = {"correction": True, "ok": False, "status": "sql_error"}


def test_a_filter_copied_from_the_last_question_is_refused_once() -> None:
    sql = "SELECT COUNT(*) FROM items WHERE opened = 0 AND shelf = 'bottom'"
    said = _database_query_carries_over_the_last_ask(sql, "how many are still unopened", [])
    assert "'bottom'" in said
    told = [{**SEEN, "observation": f"The call failed and nothing was changed: {said} Use the exact names"}]
    assert _database_query_carries_over_the_last_ask(sql, "how many are still unopened", told) == ""


@pytest.mark.parametrize(
    "sql, ask",
    [
        ("SELECT * FROM items WHERE shelf = 'bottom'", "and how many are on the bottom shelf"),
        ("SELECT * FROM items WHERE name LIKE '%honey%'", "is there any honey in there"),
        ("SELECT * FROM books WHERE title = 'Ulysses'", "who wrote ulyses"),  # a typo is not a stray filter
        ("SELECT * FROM members WHERE name = 'Yara'", "does Yara have anything checked out"),
        ("SELECT * FROM items WHERE expires < '2026-12-01'", "what expires before december"),
        ("SELECT * FROM items WHERE opened = 0", "how many are unopened"),
    ],
)
def test_filters_the_question_names_are_not_refused(sql: str, ask: str) -> None:
    assert _database_query_carries_over_the_last_ask(sql, ask, []) == ""


def test_one_top_row_the_question_never_asked_for_is_refused_once() -> None:
    sql = "SELECT name, expires FROM items WHERE shelf = 'middle' ORDER BY expires LIMIT 1"
    said = _database_query_carries_over_the_last_ask(sql, "whats on the middle shelf", [])
    assert "keeps only one row" in said
    told = [{**SEEN, "observation": f"failed and nothing was changed: {said}"}]
    assert _database_query_carries_over_the_last_ask(sql, "whats on the middle shelf", told) == ""
    assert _database_query_carries_over_the_last_ask(sql, "which item on the middle shelf expires soonest", []) == ""
    assert _database_query_carries_over_the_last_ask("SELECT * FROM t ORDER BY x LIMIT 1", "which has the largest quantity", []) == ""


def test_the_refusal_is_a_typed_sql_error_the_loop_hands_back() -> None:
    payload = {"arguments": {"sql": "SELECT * FROM items WHERE shelf = 'bottom' ORDER BY expires LIMIT 1"}}
    refused = _database_query_contradicts_the_ask("vool-database.query", payload, "how many are unopened", [])
    assert refused is not None and refused.status == "sql_error" and refused.ok is False
    assert _database_query_contradicts_the_ask("vool-database.schema", payload, "how many are unopened", []) is None


def _toolshed(tmp_path) -> None:
    from core.plugin_tools import plugin_scratch_dir
    from core.tool_demand_signals import _VOCAB_CACHE

    root = plugin_scratch_dir("vool-database") / "databases"
    root.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(root / "toolshed.sqlite")
    c.executescript(
        "create table tools(id integer primary key, name text, kind text, bought int);"
        "create table neighbours(id integer primary key, name text, street text, joined text);"
        "create table borrowings(id integer primary key, tool_id int, neighbour_id int, taken_on text, returned_on text);"
        "insert into neighbours values (2,'Pavel','Oak Ave','2021-09-01'); insert into tools values (1,'Drill','power',2019);"
    )
    c.commit()
    c.close()
    _VOCAB_CACHE.clear()


def test_a_holding_question_read_from_the_people_table_alone_is_not_answered(world, tmp_path) -> None:
    _toolshed(tmp_path)
    one = {"tool_name": "vool-database.query", "ok": True, "arguments": {"sql": "SELECT * FROM neighbours WHERE name = 'Pavel'"}}
    assert _database_join_missing("does Pavel have anything borrowed", [one]) is True
    joined = {"tool_name": "vool-database.query", "ok": True, "arguments": {"sql": "SELECT * FROM borrowings JOIN tools ON 1=1"},
              "observation": {"rows": [["Pavel", "Ladder", None]], "columns": ["name", "name", "returned_on"]}}
    assert _database_join_missing("does Pavel have anything borrowed", [one, joined]) is False
    assert _database_join_missing("who joined most recently", [one]) is False
    assert _database_join_missing("how many tools are there", []) is False


def test_a_database_turn_is_not_offered_the_tool_listing_or_expansion(world, tmp_path, monkeypatch) -> None:
    from core.database_work_state import note_database_demand
    from core.tool_offer_assembly import assemble_tool_offer

    _toolshed(tmp_path)
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    ctx = {**PRODUCTION_CONTEXT, "session_id": "s-16i", "runtime_session_id": "s-16i"}
    note_database_demand(ctx)

    def names(text: str) -> set[str]:
        return {s.get("intent") for s in assemble_tool_offer(user_text=text, task_class="unknown", source_context=dict(ctx)).specs}

    for text in ("how many tools are there", "who joined most recently", "can you add a condition column to the tools table"):
        offered = names(text)
        assert "vool-database.query" in offered, text
        assert not offered & {"operator.list_tools", "capability.expand_family"}, (text, offered)
    assert "operator.list_tools" in names("what tools do you have")


# ── the real loop: a holding question answered from the people table alone is sent back to the loans ──────────


def test_the_loop_reads_the_loans_when_the_model_answers_from_the_people_table(tmp_path, monkeypatch) -> None:
    import uuid
    from types import SimpleNamespace

    from apps.vool_agent import VoolAgent
    from core import plugin_tools, tool_registry
    from core.memory_first_router import ModelExecutionDecision
    from core.plugin_tools import plugin_scratch_dir
    from core.runtime_flags import override
    from core.tool_demand_signals import _VOCAB_CACHE
    from tests._vool_database_pack import isolated_db_world
    from tests.test_a_narrated_batch_does_not_vanish import _call

    def decision(intent: str, **arguments):
        return ModelExecutionDecision(
            source="scripted", task_hash="h", used_model=True, confidence=0.8,
            structured_output={"intent": intent, "arguments": arguments},
            tool_calls=(_call(intent, **arguments),), validation_state="validated", provider_id="p",
        )

    class Router:
        def __init__(self):
            self.calls = 0

        def resolve_tool_intent(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return decision("vool-database.query", name="library", sql="SELECT * FROM members WHERE name = 'Yara'", row_limit=20, max_seconds=5)
            return decision("respond.direct", message="Yara joined on 2025-01-09. Nothing says what she has out.")

    with override("plugin_runtime_tools", True):
        isolated_db_world(tmp_path, monkeypatch)
        plugin_tools.load_all(tmp_path)
        root = plugin_scratch_dir("vool-database") / "databases"
        root.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(root / "library.sqlite")
        c.executescript(
            "create table books(id integer primary key, title text);"
            "create table members(id integer primary key, name text, joined text);"
            "create table loans(id integer primary key, book_id int, member_id int, returned int);"
            "insert into members values (1,'Yara','2025-01-09'); insert into books values (1,'Emma');"
            "insert into loans values (1,1,1,0);"
        )
        c.commit(); c.close()
        _VOCAB_CACHE.clear()
        router = Router()
        agent = VoolAgent.__new__(VoolAgent)
        agent.memory_router = router
        agent._should_keep_ai_first_chat_lane = lambda **_kw: False
        agent._should_run_builder_controller = lambda **_kw: False
        agent._plan_tool_workflow = lambda **_kw: SimpleNamespace(handled=False, stop_after=False, next_payload=None, reason="")
        agent.hive_activity_tracker = None
        agent.public_hive_bridge = None
        from core.execution.models import ToolIntentExecution

        def fake_execute(payload, **_kw):
            sql = str(dict(payload.get("arguments") or {}).get("sql") or "")
            rows = [["Yara", "Emma", 0]] if "join" in sql.lower() else [[1, "Yara", "2025-01-09"]]
            return ToolIntentExecution(
                handled=True, ok=True, status="executed", mode="tool_executed", tool_name="vool-database.query",
                response_text="ok", user_safe_response_text="ok", details={"observation": {"rows": rows, "columns": ["a", "b", "c"]}},
            )

        agent._execute_tool_intent = fake_execute
        from core.database_work_state import note_database_demand

        sid = f"openclaw:{uuid.uuid4().hex[:20]}"
        note_database_demand({"session_id": sid, "runtime_session_id": sid, "surface": "api"})  # an earlier turn used it
        try:
            result = agent._maybe_execute_model_tool_intent(
                task=SimpleNamespace(task_id="t2"),
                effective_input="does Yara have anything checked out right now",
                classification={"task_class": "unknown"},
                interpretation=SimpleNamespace(),
                context_result=SimpleNamespace(),
                persona=SimpleNamespace(),
                session_id=sid,
                source_context={"surface": "api", "session_id": sid, "runtime_session_id": sid},
                surface="api",
            )
        finally:
            tool_registry.reset()
    steps = [str(s) for s in (result or {}).get("details", {}).get("tool_steps", [])]
    assert steps.count("vool-database.query") >= 2, steps
    assert "database_not_consulted" in str((result or {}).get("response") or ""), result  # told twice, then the ready join ran


# ── the exact queries of Mac run 16i (pantry), against the checks ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "ask, sql, refused",
    [
        ("which item expires soonest", "SELECT name, expires FROM items WHERE opened = 1 ORDER BY expires ASC LIMIT 1", True),
        ("whats on the middle shelf", "SELECT * FROM items WHERE shelf = 'middle' ORDER BY expires ASC LIMIT 1", True),
        ("whats on the middle shelf", "SELECT * FROM items WHERE shelf = 'middle' LIMIT 10", False),
        ("is there any honey in there", "SELECT COUNT(*) AS count FROM items WHERE shelf = 'bottom' AND name = 'honey';", True),
        ("which has the largest quantity", "SELECT name, quantity FROM items ORDER BY quantity DESC LIMIT 1", False),
        ("which ones are out of stock", "SELECT * FROM items WHERE quantity = 0", False),
        ("how many are still unopened", "SELECT COUNT(*) AS count FROM items WHERE opened = 0", False),
        ("which unopened item expires first", "SELECT name FROM items WHERE opened = 0 ORDER BY expires LIMIT 1", False),
        ("list the items, latest expiry first", "SELECT * FROM items ORDER BY expires DESC", False),
    ],
)
def test_the_queries_run_16i_ran(ask: str, sql: str, refused: bool) -> None:
    said = _database_query_carries_over_the_last_ask(sql, ask, [])
    assert bool(said) is refused, (ask, sql, said)


def test_a_word_given_to_a_flag_column_is_refused_with_its_real_values(tmp_path, monkeypatch) -> None:
    import importlib.machinery
    import importlib.util
    import io
    import json

    from tests.test_vool_database_handler_robustness import HANDLER

    (tmp_path / "databases").mkdir()
    c = sqlite3.connect(tmp_path / "databases" / "pantry.sqlite")
    c.executescript("create table items(id integer primary key, name text, opened int); insert into items values (1,'Rice',0),(2,'Tea',1);")
    c.commit(); c.close()
    loader = importlib.machinery.SourceFileLoader("db_handler_flag", str(HANDLER))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    monkeypatch.setattr(module, "scratch_root", lambda: str(tmp_path))

    def call(sql: str) -> dict:
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"intent": "vool-database.query", "arguments": {"name": "pantry", "sql": sql}})))
        out = io.StringIO()
        monkeypatch.setattr("sys.stdout", out)
        module.main()
        return json.loads(out.getvalue())

    refused = call("SELECT COUNT(*) FROM items WHERE opened = 'no'")
    assert refused["ok"] is False and "1 means yes and 0 means no" in json.dumps(refused)
    assert call("SELECT COUNT(*) FROM items WHERE opened = 0")["ok"] is True
    assert call("SELECT COUNT(*) FROM items WHERE name = 'no'")["ok"] is True


# ── a closing reply that denies or contradicts the rows the query read ────────────────────────────────────────

from core.agent_runtime.research_tool_loop_facade import _database_reply_contradicts_its_rows


def _query_step(sql: str, rows: list, columns: list[str]) -> dict:
    return {"tool_name": "vool-database.query", "ok": True, "arguments": {"sql": sql},
            "observation": {"rows": rows, "columns": columns, "row_count": len(rows)}}


def test_a_borrower_no_row_holds_is_replaced_by_the_rows() -> None:
    steps = [_query_step("SELECT * FROM tools WHERE name = 'Drill'", [[1, "Drill", "power", 2019]], ["id", "name", "kind", "bought"])]
    said = _database_reply_contradicts_its_rows("The Drill has been borrowed by **Pavel**.", "who has had the Drill", steps)
    assert "Drill" in said and "Pavel" not in said


def test_a_denial_of_rows_just_read_is_replaced_by_the_rows() -> None:
    steps = [_query_step("SELECT * FROM items WHERE quantity = 0", [[2, "Lentils", "top", 0]], ["id", "name", "shelf", "quantity"])]
    reply = "The database does not have a column for stock status. There is no direct way to tell."
    assert "Lentils" in _database_reply_contradicts_its_rows(reply, "which ones are out of stock", steps)


def test_an_answer_the_rows_support_stands() -> None:
    steps = [_query_step("SELECT * FROM items WHERE quantity = 0", [[2, "Lentils", "top", 0]], ["id", "name", "shelf", "quantity"])]
    assert _database_reply_contradicts_its_rows("Lentils are out of stock.", "which ones are out of stock", steps) == ""
    assert _database_reply_contradicts_its_rows("Lentils are out.", "which ones are out", []) == ""


def test_a_list_named_in_another_order_than_the_rows_is_replaced_by_the_rows() -> None:
    names = ["Honey", "Pasta", "Rice", "Lentils", "Tea", "Flour"]
    steps = [_query_step("SELECT * FROM items ORDER BY expires DESC", [[i, n] for i, n in enumerate(names, 1)], ["id", "name"])]
    wrong = "Here you go: Honey, Pasta, Tea, Rice, Lentils, Flour."
    right = "Here you go: Honey, Pasta, Rice, Lentils, Tea, Flour."
    ask = "list the items, latest expiry first"
    assert _database_reply_contradicts_its_rows(wrong, ask, steps)
    assert _database_reply_contradicts_its_rows(right, ask, steps) == ""
    assert _database_reply_contradicts_its_rows(wrong, "list the items", steps) == ""  # no order asked


# ── Mac run 16i rerun on c764848c: the rows were read wrongly, a question got the last question's query ────────

from core.agent_runtime.orchestrator import database_holding_answer
from core.agent_runtime.research_tool_loop_facade import _database_empty_on_a_literal


def test_whose_loan_is_out_is_read_from_the_returned_column_not_by_the_model() -> None:
    # library 7: returned = 1 means back, and the model said "has Kafka checked out"
    flag = _query_step("SELECT members.name, books.title, loans.returned FROM loans JOIN members JOIN books WHERE members.name='Yara'",
                       [["Yara", "Kafka on the Shore", 1]], ["name", "title", "returned"])
    said = database_holding_answer("does Yara have anything checked out right now", [flag])
    assert "nothing out" in said and "Kafka on the Shore" in said and "has out right now: Kafka" not in said
    # toolshed 7: both loans of Noor are back
    back = _query_step("select ...", [["Noor", "Saw", "2026-10-15", "2026-10-18"], ["Noor", "Drill", "2026-11-08", "2026-11-09"]],
                       ["name", "name", "taken_on", "returned_on"])
    assert "Noor has nothing out right now" in database_holding_answer("has Noor returned everything", [back])
    # toolshed 8: Gustav's hedge trimmer has no returned date
    open_ = _query_step("select ...", [["Gustav", "Hedge trimmer", "2026-11-05", None]], ["name", "name", "taken_on", "returned_on"])
    assert "Gustav has out right now: Hedge trimmer" in database_holding_answer("is Gustav holding any tools at the moment", [open_])


def test_a_question_that_is_not_about_what_is_out_is_not_answered_as_one() -> None:
    step = _query_step("select ...", [["Drill", "Marta", None]], ["name", "name", "returned_on"])
    assert database_holding_answer("who has had the Drill", [step]) == ""
    assert database_holding_answer("is Gustav holding any tools", [_query_step("select", [["x"]], ["name"])]) == ""


def test_a_count_with_no_names_does_not_satisfy_a_holding_question(world, tmp_path) -> None:
    _toolshed(tmp_path)
    counted = {"tool_name": "vool-database.query", "ok": True, "arguments": {"sql": "SELECT COUNT(*) FROM borrowings JOIN neighbours ON 1=1"},
               "observation": {"rows": [[2]], "columns": ["COUNT(*)"]}}
    assert _database_join_missing("does Pavel have anything borrowed", [counted]) is True


def test_an_empty_read_on_a_text_value_is_noticed() -> None:
    empty = {"tool_name": "vool-database.query", "ok": True, "arguments": {"sql": "SELECT * FROM tools WHERE kind = 'power tool'"},
             "observation": {"rows": [], "columns": ["id"]}}
    assert _database_empty_on_a_literal([empty]) is True
    none_at_all = {**empty, "arguments": {"sql": "SELECT * FROM tools"}}
    assert _database_empty_on_a_literal([none_at_all]) is False
    assert _database_empty_on_a_literal([{**empty, "observation": {"rows": [[1]], "columns": ["id"]}}]) is False


def test_the_query_the_last_question_ran_is_not_this_questions() -> None:
    ctx = {"session_id": "s-last-q", "runtime_session_id": "s-last-q"}
    payload = {"arguments": {"sql": "SELECT COUNT(*) FROM tools"}}
    assert _database_query_contradicts_the_ask("vool-database.query", payload, "how many tools are there", [], ctx) is None
    again = _database_query_contradicts_the_ask("vool-database.query", payload, "sorry, which neighbour lives on Oak Ave", [], ctx)
    assert again is not None and again.status == "sql_error"
    told = [{"correction": True, "observation": again.response_text}]
    assert _database_query_contradicts_the_ask("vool-database.query", payload, "sorry, which neighbour lives on Oak Ave", told, ctx) is None
    # the same question asked again may send the same query
    other = {"session_id": "s-last-q2", "runtime_session_id": "s-last-q2"}
    assert _database_query_contradicts_the_ask("vool-database.query", payload, "how many tools are there", [], other) is None
    assert _database_query_contradicts_the_ask("vool-database.query", payload, "how many tools are there again", [], other) is None


def test_a_reply_that_names_a_book_after_every_query_failed_is_not_published() -> None:
    failed = {"tool_name": "vool-database.query", "ok": False, "status": "tool_failed", "observation": {}}
    said = _database_reply_contradicts_its_rows("The oldest book is **Kafka on the Shore**, published in 2002.", "oldest book", [failed])
    assert "could not get a query" in said
    assert _database_reply_contradicts_its_rows("I could not read it. Ask again.", "oldest book", [failed]) == ""


def test_a_one_row_cap_is_not_the_size_of_the_answer() -> None:
    payload = {"arguments": {"sql": "SELECT who FROM b", "row_limit": 1}}
    assert _database_query_contradicts_the_ask("vool-database.query", payload, "who has had the Drill", [], None) is None
    assert payload["arguments"]["row_limit"] == 20
    capped = {"arguments": {"sql": "SELECT who FROM b ORDER BY id LIMIT 1", "row_limit": 1}}
    _database_query_contradicts_the_ask("vool-database.query", capped, "who is first", [], None)
    assert capped["arguments"]["row_limit"] == 1
