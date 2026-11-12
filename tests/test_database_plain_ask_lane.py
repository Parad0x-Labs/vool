"""A plain database ask must leave the tools-less chat lane (served run3: route
``ordinary_plain_text_chat``, the model only promised "Let's connect to your database...").

Both lane gates read the same recogniser that seats the tools in the offer. With the pack's
tools registered, database work is tool work; without them nothing changes.
"""
from __future__ import annotations

import pytest

from tests._vool_database_pack import isolated_db_world
from tests.test_production_route_matrix import PRODUCTION_CONTEXT, _lane_probe_agent

ASKS = [
    "whats in my orders database?",
    "how many rows r in the orders table",
    "show me my databse tables",
    "which city is Cleo from in the customers table",
    "list the customers tabel for me",
]
NOT_WORK = ["what is a database index?", "do not touch my database, just describe what a schema is"]


def _gates(text: str) -> tuple[bool, bool]:
    from core.agent_runtime.runtime_checkpoint_lane_policy import should_keep_ai_first_chat_lane
    from core.execution.planner import should_attempt_tool_intent

    kept = should_keep_ai_first_chat_lane(
        _lane_probe_agent(), user_input=text, classification={"task_class": "unknown"},
        interpretation=None, source_context=dict(PRODUCTION_CONTEXT), checkpoint_state=None,
    )
    opened = should_attempt_tool_intent(text, task_class="unknown", source_context=dict(PRODUCTION_CONTEXT))
    return kept, opened


@pytest.fixture()
def world(tmp_path, monkeypatch):
    from core import plugin_tools, tool_registry
    from core.runtime_flags import override

    with override("plugin_runtime_tools", True):
        isolated_db_world(tmp_path, monkeypatch)
        plugin_tools.load_all(tmp_path)
        try:
            yield
        finally:
            tool_registry.reset()


@pytest.mark.parametrize("text", ASKS)
def test_plain_database_ask_leaves_the_toolless_lane(world, text: str) -> None:
    kept, opened = _gates(text)
    assert kept is False and opened is True, (text, kept, opened)


@pytest.mark.parametrize("text", NOT_WORK)
def test_talking_about_databases_stays_conversation(world, text: str) -> None:
    kept, opened = _gates(text)
    assert opened is False, (text, kept, opened)


def test_without_the_pack_tools_database_work_is_not_claimed() -> None:
    from core import tool_registry
    from core.tool_demand_signals import asks_for_database_work

    tool_registry.reset()
    assert asks_for_database_work("show me my databse tables") is False


# ── follow-ups inside a database conversation ────────────────────────────────

FOLLOW_UPS = ["how many orders are shipped", "and how many are pending?", "which city is Cleo from"]


def test_followups_stay_with_the_tools_once_the_session_used_the_database(world, tmp_path, monkeypatch) -> None:
    from core.database_work_state import active_database_work_intents
    from core.execution_truth import KIND_TOOL, record_execution

    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    context = {"session_id": "s-db-1", "runtime_session_id": "s-db-1"}
    assert active_database_work_intents(context) == ()
    # Without session state the bare follow-up is NOT released (the failure this closes).
    from core.execution.planner import should_attempt_tool_intent as _gate

    assert _gate("and how many are pending?", task_class="unknown", source_context=dict({**PRODUCTION_CONTEXT, **context})) is False
    record_execution(session_id="s-db-1", turn_key="t1", kind=KIND_TOOL, name="vool-database.schema", ok=True)
    seats = active_database_work_intents(context)
    assert "vool-database.query" in seats and "vool-database.schema" in seats
    assert not any(s.endswith(("apply", "restore", "backup", "db.create")) for s in seats)
    assert active_database_work_intents({"session_id": "other"}) == ()
    from core.agent_runtime.runtime_checkpoint_lane_policy import should_keep_ai_first_chat_lane
    from core.execution.planner import should_attempt_tool_intent

    ctx = {**PRODUCTION_CONTEXT, **context}
    for text in FOLLOW_UPS:
        kept = should_keep_ai_first_chat_lane(
            _lane_probe_agent(), user_input=text, classification={"task_class": "unknown"},
            interpretation=None, source_context=dict(ctx), checkpoint_state=None,
        )
        assert kept is False, text
        assert should_attempt_tool_intent(text, task_class="unknown", source_context=dict(ctx)) is True, text


# ── asks that use only the names in the person's own database ────────────────

def test_data_questions_using_the_persons_table_names_are_database_work(world, tmp_path) -> None:
    import sqlite3

    from core.plugin_tools import plugin_scratch_dir
    from core.tool_demand_signals import _VOCAB_CACHE, asks_for_database_work

    root = plugin_scratch_dir("vool-database") / "databases"
    root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(root / "library.sqlite")
    conn.executescript("create table books(id integer primary key, title text); create table members(id integer, name text);")
    conn.commit(); conn.close()
    _VOCAB_CACHE.clear()
    for text in [
        "how many books are there",
        "which books came out before 1950?",
        "list the members, newest first",
        "what do I have in my library db",
        "ok and how many members joined this year",
        "sorry, what was the oldest book again?",
        "which member joined most recently",
    ]:
        assert asks_for_database_work(text), text
    # plural stems ("classes" -> "class") and non-generic column words ("capacity", "returned")
    conn = sqlite3.connect(root / "gym.sqlite")
    conn.executescript("create table classes(id integer primary key, title text, capacity int); create table loans(id, returned int);")
    conn.commit(); conn.close()
    _VOCAB_CACHE.clear()
    for text in ["which class has the most space", "how many classes are there", "what is the biggest capacity", "how many are still out on loan"]:
        assert asks_for_database_work(text), text
    for text in ["what is your name", "what year is it", "what is a library?", "tell me a joke", "recommend a novel", "do not touch my books, just describe them"]:
        assert not asks_for_database_work(text), text


def test_the_database_skill_carries_the_real_names(world, tmp_path) -> None:
    import sqlite3

    from core.database_work_state import database_catalog_block
    from core.plugin_tools import plugin_scratch_dir

    root = plugin_scratch_dir("vool-database") / "databases"
    root.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(root / "garden.sqlite")
    c.executescript("create table plants(id integer primary key, name text, room text);")
    c.commit(); c.close()
    block = database_catalog_block()
    assert "database `garden`" in block and "plants(id, name, room)" in block and "COUNT(*)" in block
    from core.native_skill_library import guidance_for_selection, select_native_skills

    selection = select_native_skills(task_class="", user_text="how many plants do I have in my garden db")
    text, _prov, _perm = guidance_for_selection(selection.selected)
    assert "plants(id, name, room)" in text


def test_a_turn_that_asked_for_database_work_keeps_the_session_in_the_database_lane(world) -> None:
    """Run9: the first database turn died without a tool call, and the follow-up
    "does Yara have anything checked out" (no table named) got no tool."""
    from core.agent_runtime.runtime_checkpoint_lane_policy import should_keep_ai_first_chat_lane
    from core.database_work_state import _ASKED
    from core.execution.planner import should_attempt_tool_intent

    _ASKED.clear()
    follow_up = "does Yara have anything checked out"

    def gates(session: str) -> tuple[bool, bool]:
        ctx = {**PRODUCTION_CONTEXT, "session_id": session, "runtime_session_id": session}
        kept = should_keep_ai_first_chat_lane(
            _lane_probe_agent(), user_input=follow_up, classification={"task_class": "unknown"},
            interpretation=None, source_context=dict(ctx), checkpoint_state=None,
        )
        return kept, should_attempt_tool_intent(follow_up, task_class="unknown", source_context=dict(ctx))

    assert gates("s-asked") == (True, False)
    ctx = {**PRODUCTION_CONTEXT, "session_id": "s-asked", "runtime_session_id": "s-asked"}
    assert should_attempt_tool_intent("show me my databse tables", task_class="unknown", source_context=dict(ctx)) is True
    assert gates("s-asked") == (False, True)
    assert gates("s-other") == (True, False)


# ── the clean local loop does not take database questions (run14) ────────────

def test_the_clean_loop_leaves_database_questions_to_the_database_tools(world, tmp_path) -> None:
    import sqlite3

    from core.clean_loop import route
    from core.database_work_state import _ASKED, note_database_demand
    from core.plugin_tools import plugin_scratch_dir
    from core.tool_demand_signals import _VOCAB_CACHE

    root = plugin_scratch_dir("vool-database") / "databases"
    root.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(root / "library.sqlite")
    c.executescript("create table books(id integer primary key, title text); create table loans(id, returned int);")
    c.commit(); c.close()
    _VOCAB_CACHE.clear(); _ASKED.clear()
    brain = object()
    ctx = {"surface": "cli", "session_id": "s-clean-1", "runtime_session_id": "s-clean-1"}

    def take(text: str) -> route.RouteDecision:
        return route.decide(text, source_context=dict(ctx), session_id="s-clean-1",
                            resolve_brain=lambda: brain, document_match=lambda _t: True, env={})

    assert take("is Ines holding any books at the moment").reason == "database_work"
    assert take("has Tomas given everything back").reason != "database_work"  # no database word yet
    note_database_demand(ctx)
    assert take("has Tomas given everything back").reason == "database_work"
    # the owner's files stay the clean loop's even inside a database chat
    assert take("find the invoice file on my desktop").reason != "database_work"
