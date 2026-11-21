""""Show me the schema ... Read only, do not create anything" keeps the look and withdraws only the writes.

Release walk on the real app at 36033c05 (local qwen3:8b, the database plugin installed and on): "Using the VOOL
Database plugin, show me the schema of my database named demo. Read only, do not create anything." was routed as
database work, but "do not create anything" set the turn's action policy to forbidden. No tool was offered
(tool_offer_made false), no database tool ran, and the model wrote a refusal in the plugin's own shape:
{"status": "unknown_database", ...}, which reads as if the plugin had answered.

The rider now withdraws writes, not the turn: the policy stays allowed, and every intent whose declared side effect is
not read-only is refused at the tool boundary. The controls: a turn that only talks ("Explain indexes. Do not create
anything.", "Do not write any files, just explain") is still a no-action turn, and "do not create anything else"
after a create keeps its create.
"""
from __future__ import annotations

from unittest import mock

import pytest

from core.agent_runtime.intent_claims import (
    NO_WRITES_CONTEXT_KEY,
    ActionPolicy,
    turn_action_constraints,
    writes_forbidden_in_context,
)
from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.tool_intent_executor import execute_tool_intent

WALK = ("Using the VOOL Database plugin, show me the schema of my database named demo. "
        "Read only, do not create anything.")


def test_the_walk_turn_keeps_its_tools_and_forbids_writes():
    constraints = turn_action_constraints(WALK)
    assert constraints.policy is ActionPolicy.ALLOWED
    assert constraints.forbid_writes is True
    assert constraints.forbid_commands is True
    assert turn_action_constraints("show me my files. do not change anything").forbid_writes is True


@pytest.mark.parametrize("text", [
    "Explain indexes. Do not create anything.",
    "Do not write any files, just explain",
    "Do not create anything, just explain what a schema is.",
    "Do not run a search. Conceptually, why are indexes useful?",
])
def test_a_turn_that_only_talks_is_still_a_no_action_turn(text):
    assert turn_action_constraints(text).policy is ActionPolicy.FORBIDDEN


def test_a_bounded_create_keeps_its_create():
    constraints = turn_action_constraints("Create a file a.txt with hi. Do not create anything else.")
    assert constraints.policy is ActionPolicy.ALLOWED and constraints.forbid_writes is False


def _tracker() -> HiveActivityTracker:
    return HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))


def _run(intent: str, arguments: dict) -> object:
    with mock.patch("core.tool_intent_executor.decide_tool_call") as decide:
        result = execute_tool_intent(
            {"intent": intent, "arguments": arguments},
            task_id="read-only-task",
            session_id="read-only-session",
            source_context={"surface": "api", NO_WRITES_CONTEXT_KEY: True},
            hive_activity_tracker=_tracker(),
        )
    return result, decide


def test_a_write_is_refused_at_the_tool_boundary_when_the_turn_said_read_only():
    assert writes_forbidden_in_context({NO_WRITES_CONTEXT_KEY: True})
    result, decide = _run("workspace.write_file", {"path": "notes.txt", "content": "x"})
    assert result.status == "blocked_by_read_only_constraint"
    assert result.details["executed"] is False
    decide.assert_not_called()


def test_a_read_passes_the_read_only_gate():
    result, _decide = _run("workspace.read_file", {"path": "notes.txt"})
    assert result.status != "blocked_by_read_only_constraint"


def test_with_the_database_plugin_the_schema_passes_and_a_create_is_refused(tmp_path, monkeypatch):
    """The walk's own plugin: its read tools pass the read-only gate, its writes do not, and the walk's words still
    ask for database work (so the tool lane is opened, now that the policy no longer closes it)."""
    from core import plugin_tools, tool_registry
    from core.runtime_flags import override
    from core.tool_argument_aliases import side_effect_class_for_intent
    from core.tool_demand_signals import _VOCAB_CACHE, asks_for_database_work
    from tests._vool_database_pack import isolated_db_world

    with override("plugin_runtime_tools", True):
        isolated_db_world(tmp_path, monkeypatch)
        plugin_tools.load_all(tmp_path)
        _VOCAB_CACHE.clear()
        try:
            assert side_effect_class_for_intent("vool-database.schema") == "read_only"
            assert asks_for_database_work(WALK)
            created, _ = _run("vool-database.db.create", {"name": "demo"})
            assert created.status == "blocked_by_read_only_constraint"
            looked, _ = _run("vool-database.schema", {"name": "demo"})
            assert looked.status != "blocked_by_read_only_constraint"
        finally:
            tool_registry.reset()
            _VOCAB_CACHE.clear()
