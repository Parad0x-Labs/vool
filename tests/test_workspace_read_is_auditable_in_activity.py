"""A workspace file read through the deterministic fast path must reach the Activity ledger.

Measured 2026-08-11 by driving the live daemon (`POST /api/chat`, session
`openclaw:42079debd66c4123b811`) with "Read the file README.md in this project and tell me the
first heading." The reply was "There is no file at `README.md`, and no file of that name elsewhere
in this project." -- an answer only reachable by resolving the path AND scanning the project for
near matches. The turn's ledger (`GET /api/runtime/events`) held nine events, and not one of them
was a tool event:

    task_received / model.call_started / model.call_completed (x2)
    model_lane_proof "Fast-path response completed without model inference."
    task_completed / semantic_resolution_receipt / turn.trace_completed

The Activity panel derives "No tool ran -- answered directly" from exactly that absence
(`ledgerRanNoTool` in core/vool_chat_page.py looks for `tool_selected`/`tool_executed`), so a turn
that had genuinely touched the user's filesystem reported that it had run no tool at all.

`maybe_handle_direct_workspace_runtime_request` executed the tool and went straight to
`_fast_path_result`, unlike the machine lane, which has emitted typed tool steps around its
executions since the equivalent fix there. These tests hold that seam: the reads that matter are
the ones that FAIL, because a not_found still searched the project, and a scope violation still
resolved a path the user asked about.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from core.agent_runtime.fast_paths_utility import maybe_handle_direct_workspace_runtime_request


class _RecordingAgent:
    """Captures the ledger the way the runtime does, so assertions read the real event stream."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def _emit_runtime_event(self, source_context, *, event_type: str, message: str, **details: Any):
        self.events.append({"event_type": event_type, "message": message, **details})
        return None

    def _fast_path_result(self, **kwargs):
        return {"reason": kwargs.get("reason"), "response": kwargs.get("response")}

    def _plan_tool_workflow(self, **kwargs):
        # Not this lane's branch: an empty payload makes the handler stand down, which is what the
        # real planner does for a message the direct-read regex never claimed.
        self.planner_consulted = True
        return SimpleNamespace(handled=False, next_payload={})


def _drive(agent: _RecordingAgent, text: str, workspace: str):
    return maybe_handle_direct_workspace_runtime_request(
        agent,
        text,
        session_id="s",
        source_surface="api",
        source_context={"workspace": workspace, "project_id": "p", "surface": "api"},
    )


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "notes.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    (tmp_path / "todo.txt").write_text("- ship it\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "buried.txt").write_text("deep\n", encoding="utf-8")
    return str(tmp_path)


def _tool_events(agent: _RecordingAgent) -> list[dict[str, Any]]:
    return [e for e in agent.events if str(e["event_type"]).startswith("tool_")]


def _ran_no_tool(agent: _RecordingAgent) -> bool:
    """The panel's own rule, mirrored: core/vool_chat_page.py::ledgerRanNoTool."""
    return not any(e["event_type"] in {"tool_executed", "tool_selected"} for e in agent.events)


def test_a_successful_read_is_recorded_as_a_tool_step(workspace) -> None:
    agent = _RecordingAgent()
    result = _drive(agent, "read notes.txt", workspace)

    assert result is not None, "the fast path must claim this read"
    assert not _ran_no_tool(agent), "Activity would claim 'No tool ran' over a real file read"
    events = _tool_events(agent)
    assert [e["event_type"] for e in events] == ["tool_selected", "tool_executed"]
    assert all(e["tool_name"] == "workspace.read_file" for e in events)
    assert "notes.txt" in events[-1]["summary"], "the step must name the file that was read"


def test_a_missing_file_still_records_the_access_that_searched_the_project(workspace) -> None:
    """The exact live failure: the miss is what proves the project was scanned."""
    agent = _RecordingAgent()
    result = _drive(agent, "read README.md", workspace)

    assert result is not None
    assert "no file" in str(result["response"]).lower(), "precondition: this is the not_found reply"
    assert not _ran_no_tool(agent), (
        "a not_found read resolved a path and scanned the project for near matches; "
        "reporting 'No tool ran -- answered directly' over it is the defect under test"
    )
    events = _tool_events(agent)
    assert [e["event_type"] for e in events] == ["tool_selected", "tool_failed"]
    assert events[-1]["event_type"] == "tool_failed", "a miss must not be recorded as a clean read"


def test_every_file_in_a_multi_file_read_gets_its_own_step(workspace) -> None:
    agent = _RecordingAgent()
    result = _drive(agent, "read notes.txt and todo.txt", workspace)

    assert result is not None
    executed = [e for e in _tool_events(agent) if e["event_type"] == "tool_executed"]
    assert len(executed) == 2, "two files were read, so two steps must be auditable"
    named = " ".join(str(e["summary"]) for e in executed)
    assert "notes.txt" in named and "todo.txt" in named


def test_a_workspace_escape_is_recorded_rather_than_silently_refused(workspace) -> None:
    """A traversal is still an access attempt against a path the user named."""
    agent = _RecordingAgent()
    result = _drive(agent, "read ../../etc/passwd", workspace)

    if result is None:
        pytest.skip("this phrasing is not claimed by the workspace read lane")
    assert not _ran_no_tool(agent), "a refused escape must still appear in the ledger"
    assert [e["event_type"] for e in _tool_events(agent)] == ["tool_selected", "tool_failed"]


def test_the_step_summary_never_leaks_whole_file_contents(workspace) -> None:
    """The ledger row is a label, not a payload -- an enormous file must not become the summary."""
    big = "\n".join(f"line {index}" for index in range(5000))
    (__import__("pathlib").Path(workspace) / "big.txt").write_text(big, encoding="utf-8")

    agent = _RecordingAgent()
    assert _drive(agent, "read big.txt", workspace) is not None
    for event in _tool_events(agent):
        assert len(str(event["summary"])) <= 200, "a ledger label must stay a label"
