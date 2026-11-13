"""Database run 16g: a model that answers "I don't have access" to a question about the person's own tables, told
twice to look, has the ready read-only call made for it. Driven through the real tool loop, scripted at the
provider seam only."""
from __future__ import annotations

import sqlite3
import uuid
from types import SimpleNamespace

import pytest

from apps.vool_agent import VoolAgent
from core.memory_first_router import ModelExecutionDecision
from tests._vool_database_pack import isolated_db_world
from tests.test_a_narrated_batch_does_not_vanish import _call

GIVING_UP = "I don't have access to a list of members or a database of people to list."


class _GivesUp:
    def __init__(self):
        self.calls = 0
        self.seen_feedback = []

    def resolve_tool_intent(self, **kwargs):
        self.calls += 1
        return ModelExecutionDecision(
            source="scripted", task_hash="h", used_model=True, confidence=0.8,
            structured_output={"intent": "respond.direct", "arguments": {"message": GIVING_UP}},
            tool_calls=(_call("respond.direct", message=GIVING_UP),), validation_state="validated", provider_id="p",
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
        c = sqlite3.connect(root / "library.sqlite")
        c.executescript("create table members(id integer primary key, name text, joined text); insert into members values (1,'Ines','2024-03-02');")
        c.commit(); c.close()
        _VOCAB_CACHE.clear()
        try:
            yield
        finally:
            tool_registry.reset()


def test_the_ready_call_is_made_after_two_refused_replies(world) -> None:
    router = _GivesUp()
    agent = VoolAgent.__new__(VoolAgent)
    agent.memory_router = router
    agent._should_keep_ai_first_chat_lane = lambda **_kw: False
    agent._should_run_builder_controller = lambda **_kw: False
    agent._plan_tool_workflow = lambda **_kw: SimpleNamespace(handled=False, stop_after=False, next_payload=None, reason="")
    agent.hive_activity_tracker = None
    agent.public_hive_bridge = None
    result = agent._maybe_execute_model_tool_intent(
        task=SimpleNamespace(task_id="t1"),
        effective_input="list the members, newest first",
        classification={"task_class": "unknown"},
        interpretation=SimpleNamespace(),
        context_result=SimpleNamespace(),
        persona=SimpleNamespace(),
        session_id=f"openclaw:{uuid.uuid4().hex[:20]}",
        source_context={"surface": "api"},
        surface="api",
    )
    steps = [str(s) for s in (result or {}).get("details", {}).get("tool_steps", [])]
    assert router.calls >= 3
    assert "vool-database.query" in steps, steps
