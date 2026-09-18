"""The deterministic machine reads (disk / find_largest / display) must surface as REAL
typed tool steps (``tool_selected`` -> ``tool_executed``) so the /chat status card derives
a truthful "Tool-confirmed" label instead of the plain-answer "Answered". Before this the
fast path executed the tool but emitted no ``tool.*`` events, so the UI saw zero steps and
labelled a measured host read the same as an ungrounded chat reply.
"""
from __future__ import annotations

import types

from core.agent_runtime.fast_paths_machine import _machine_tool_fast_path_result


class _FakeAgent:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def _emit_runtime_event(self, source_context, *, event_type, message, **details):
        self.events.append({"event_type": event_type, "message": message, **details})

    def _fast_path_result(self, **kwargs):
        return {"response": kwargs.get("response", ""), "task_id": "t1", "mode": "advice_only"}


def _execution(ok=True, response_text="Drive C: 25.0 GB free of 100.0 GB (75% used)."):
    return types.SimpleNamespace(
        ok=ok,
        response_text=response_text,
        status="ok" if ok else "failed",
        details={"observation": {"mount": "C:\\"}},
    )


def _run(agent, execution, intent="machine.disk_usage"):
    return _machine_tool_fast_path_result(
        agent,
        user_input="free space on C:",
        session_id="s1",
        source_context={"session_id": "s1"},
        intent=intent,
        execution=execution,
        reason="machine_read_fast_path",
    )


def test_machine_read_emits_start_then_complete_tool_steps() -> None:
    agent = _FakeAgent()
    _run(agent, _execution())
    kinds = [e["event_type"] for e in agent.events]
    assert "tool_selected" in kinds and "tool_executed" in kinds
    # start precedes completion so the UI builds a running -> completed step, not a bare done
    assert kinds.index("tool_selected") < kinds.index("tool_executed")


def test_tool_step_carries_tool_name_and_real_summary() -> None:
    agent = _FakeAgent()
    _run(agent, _execution())
    executed = next(e for e in agent.events if e["event_type"] == "tool_executed")
    # tool_name lets build_task_event map it to tool.completed; summary is the real output line
    assert executed["tool_name"] == "machine.disk_usage"
    assert "C:" in executed["summary"] and "GB" in executed["summary"]


def test_failed_machine_read_emits_tool_failed_not_completed() -> None:
    agent = _FakeAgent()
    _run(agent, _execution(ok=False, response_text="Could not read the drive."))
    kinds = [e["event_type"] for e in agent.events]
    assert "tool_selected" in kinds and "tool_failed" in kinds
    assert "tool_executed" not in kinds


def test_step_summary_falls_back_to_intent_when_no_output() -> None:
    agent = _FakeAgent()
    _run(agent, _execution(response_text="   "))
    executed = next(e for e in agent.events if e["event_type"] == "tool_executed")
    assert executed["summary"] == "machine.disk_usage"
