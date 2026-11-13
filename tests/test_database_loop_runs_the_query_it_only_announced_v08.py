"""Mac answer-quality row 3 on d5d044b0 (real qwen3:8b, loop off and on): "show my freezer items, the ones expiring
last at the top" queried a guessed table `freezer_items`, read the error naming the real table `items`, and ended
on "Let me show you the items sorted by expiry" with no rows. An announcement is no answer: the model is told to
look, and then the ready read-only call, sorted latest expiry first, is made for it. Driven through the real tool
loop, scripted at the provider seam only."""
from __future__ import annotations

import sqlite3
import uuid
from types import SimpleNamespace

import pytest

from apps.vool_agent import VoolAgent
from core.memory_first_router import ModelExecutionDecision
from tests._vool_database_pack import isolated_db_world
from tests.test_a_narrated_batch_does_not_vanish import _call

ASK = "show my freezer items, the ones expiring last at the top"
GUESS = {"name": "freezer", "sql": "SELECT * FROM freezer_items ORDER BY expiration_date DESC", "row_limit": 100}
ANNOUNCES = ("The table `freezer_items` doesn't exist. The available table is `items`, which contains the columns "
             "`id`, `name`, `quantity`, and `expiry`. Let me show you the items sorted by expiry.")


ANSWER = "Latest expiry first: Chicken thighs (2027-05-01), Peas (2027-02-10), Fish fingers (2026-12-05)."


class _Announces:
    def __init__(self):
        self.calls = 0

    def resolve_tool_intent(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            output = {"intent": "vool-database.query", "arguments": GUESS}
            call = _call("vool-database.query", **GUESS)
        elif "Chicken thighs" in repr(kwargs):
            # The rows are in front of the model now: it answers from them.
            output = {"intent": "respond.direct", "arguments": {"message": ANSWER}}
            call = _call("respond.direct", message=ANSWER)
        else:
            output = {"intent": "respond.direct", "arguments": {"message": ANNOUNCES}}
            call = _call("respond.direct", message=ANNOUNCES)
        return ModelExecutionDecision(
            source="scripted", task_hash="h", used_model=True, confidence=0.8, structured_output=output,
            tool_calls=(call,), validation_state="validated", provider_id="p",
        )


@pytest.fixture()
def world(tmp_path, monkeypatch):
    from core import plugin_tools, tool_registry
    from core.plugin_tools import plugin_scratch_dir
    from core.runtime_flags import override
    from core.tool_demand_signals import _VOCAB_CACHE

    with override("plugin_runtime_tools", True):
        isolated_db_world(tmp_path, monkeypatch)
        plugin_tools.load_all(tmp_path)
        root = plugin_scratch_dir("vool-database") / "databases"
        root.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(root / "freezer.sqlite")
        c.executescript(
            "create table items(id integer primary key, name text, quantity int, expiry text);"
            "insert into items values (1,'Peas',2,'2027-02-10'),(2,'Fish fingers',1,'2026-12-05'),"
            "(3,'Chicken thighs',4,'2027-05-01');"
        )
        c.commit(); c.close()
        _VOCAB_CACHE.clear()
        try:
            yield
        finally:
            tool_registry.reset()


def test_the_announced_query_runs_latest_expiry_first(world) -> None:
    router = _Announces()
    agent = VoolAgent.__new__(VoolAgent)
    agent.memory_router = router
    agent._should_keep_ai_first_chat_lane = lambda **_kw: False
    agent._should_run_builder_controller = lambda **_kw: False
    agent._plan_tool_workflow = lambda **_kw: SimpleNamespace(handled=False, stop_after=False, next_payload=None, reason="")
    agent.hive_activity_tracker = None
    agent.public_hive_bridge = None
    result = agent._maybe_execute_model_tool_intent(
        task=SimpleNamespace(task_id="t1"),
        effective_input=ASK,
        classification={"task_class": "unknown"},
        interpretation=SimpleNamespace(),
        context_result=SimpleNamespace(),
        persona=SimpleNamespace(),
        session_id=f"openclaw:{uuid.uuid4().hex[:20]}",
        source_context={"surface": "api", "_owner_local": True},
        surface="api",
    )
    details = (result or {}).get("details", {})
    text = str((result or {}).get("response") or "")
    assert "Let me show you" not in text, text
    assert details.get("tool_steps", [])[-1] == "vool-database.query", details
    assert ANSWER in text, text


def test_the_ready_call_sorts_latest_expiry_first(world) -> None:
    from core.database_work_state import suggest_database_query

    assert suggest_database_query(ASK) == "name=freezer, sql=SELECT * FROM items ORDER BY expiry DESC"
    assert suggest_database_query("which freezer items expire soonest first?").endswith("ORDER BY expiry ASC")
