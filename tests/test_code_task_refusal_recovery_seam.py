"""A code task's own refusals go back to the model; a completed report is not called incomplete.

Measured in the served coding journeys of revision 3: a `stale_base` refusal after an operator's
Allow, and a `checkpoint_required` refusal for a repair attempted before the previous one was
validated, each ENDED the turn -- the recovery the refusal named (re-read and propose again; run the
checks) never ran. And a repair whose closing narration round fell just past the round budget
published its completed report under "Incomplete ... this is not a completed audit".

These are unit checks of the tool loop's two seams (served proof lives in
`tests/test_code_task_served_units.py`). They pin both directions: the task-plane refusal set is
returned only for `code.task.*` intents and within the SAME correction budget, terminal task states
stay terminal, and the budget-stop reply changes only when the last real step is a completed report
and no coding task in the turn is unfinished.
"""
from __future__ import annotations

import os
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from core.agent_runtime.research_tool_loop_facade import (
    ResearchToolLoopFacadeMixin,
    _code_task_report_completed_the_turn,
)


@dataclass
class _Execution:
    status: str
    mode: str = "tool_failed"
    response_text: str = ""
    details: dict[str, Any] = field(default_factory=dict)


class _Facade(ResearchToolLoopFacadeMixin):
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def _emit_runtime_event(self, source_context, **kwargs):  # type: ignore[override]
        self.events.append(kwargs)


def _retry(facade: _Facade, intent: str, status: str, steps: list[dict[str, Any]], text: str = "") -> bool:
    return facade._retry_as_observation(
        execution=_Execution(status=status, response_text=text),
        tool_payload={"intent": intent},
        executed_steps=steps,
        loop_source_context={},
        seen_tool_payloads=set(),
    )


RECOVERABLE = [
    ("stale_base", "`ledger.py` changed after this repair was reviewed; re-read it and propose again."),
    ("checkpoint_required", "`fees` landed at revision 1 and has not been validated: run the focused check first."),
    ("unit_in_progress", "repair unit `rename` is part-applied: execute its remaining approved change `callers`."),
    ("unit_not_approved", "repair unit `rename` starts only when every change in it is approved."),
    ("approval_consumed", "approval `fees` was already executed by step `w-3`."),
    ("proposal_id_conflict", "proposal `fees` already records a different request; use a new proposal_id."),
    ("insufficient_evidence", "no successful cumulative test run is journaled."),
]


@pytest.mark.parametrize(("status", "text"), RECOVERABLE)
def test_a_task_plane_refusal_is_returned_to_the_model_with_its_next_action(status: str, text: str) -> None:
    facade = _Facade()
    steps: list[dict[str, Any]] = []
    assert _retry(facade, "code.task.step" if status != "proposal_id_conflict" else "code.task.propose",
                  status, steps, text) is True
    assert steps[0]["correction"] is True and steps[0]["ok"] is False and steps[0]["status"] == status
    assert text in steps[0]["observation"] and "lawful next action" in steps[0]["observation"]
    assert "call the tool again" not in steps[0]["observation"].lower()
    assert facade.events[0]["event_type"] == "code_task_refusal_returned" and facade.events[0]["status"] == status


@pytest.mark.parametrize("status", ["stale_base", "checkpoint_required", "approval_consumed", "insufficient_evidence"])
@pytest.mark.parametrize("intent", ["workspace.write_file", "vool-jira.update_issue", "machine.write_file"])
def test_the_same_status_from_any_other_tool_keeps_its_meaning(intent: str, status: str) -> None:
    assert _retry(_Facade(), intent, status, []) is False


@pytest.mark.parametrize("status", ["cancelled", "not_your_task", "unknown_task", "rolled_back", "bytes_diverged",
                                    "journal_unavailable", "permission_denied", "timeout"])
def test_terminal_code_task_states_still_end_the_turn(status: str) -> None:
    assert _retry(_Facade(), "code.task.step", status, []) is False


def test_task_plane_refusals_share_the_existing_correction_budget() -> None:
    facade = _Facade()
    steps: list[dict[str, Any]] = []
    assert _retry(facade, "vool-jira.search_issues", "invalid_arguments", steps) is True
    assert _retry(facade, "code.task.step", "checkpoint_required", steps) is True
    assert _retry(facade, "code.task.step", "stale_base", steps) is False  # the third correction is refused
    assert sum(1 for step in steps if step.get("correction")) == 2


def test_an_argument_correction_keeps_its_original_wording() -> None:
    steps: list[dict[str, Any]] = []
    assert _retry(_Facade(), "code.task.step", "invalid_arguments", steps, "arguments must be an object") is True
    assert "call the tool again" in steps[0]["observation"].lower()


def _step(tool: str, **details: Any) -> dict[str, Any]:
    return {"tool_name": tool, "ok": True, "details": details}


def test_a_budget_stop_after_a_completed_report_is_the_reports_answer() -> None:
    steps = [
        _step("code.task.step", task_id="ct-fees", stage="inspect_diff", code_task_verdict="unresolved"),
        _step("code.task.report", task_id="ct-fees", stage="report", verdict="completed", code_task_verdict="completed"),
    ]
    assert _code_task_report_completed_the_turn(steps) is True
    # a correction recorded after the report does not change what the last real step was
    assert _code_task_report_completed_the_turn([*steps, {"tool_name": "code.task.step", "correction": True}]) is True


@pytest.mark.parametrize("shape", ["diff-last", "report-unresolved", "other-task-unfinished", "no-code-task"])
def test_every_other_budget_stop_stays_incomplete(shape: str) -> None:
    report_done = _step("code.task.report", task_id="ct-fees", stage="report", verdict="completed", code_task_verdict="completed")
    if shape == "diff-last":
        steps = [_step("code.task.step", task_id="ct-fees", stage="report", code_task_verdict="unresolved")]
    elif shape == "report-unresolved":
        steps = [_step("code.task.report", task_id="ct-fees", stage="narrow_test", verdict="unresolved",
                       code_task_verdict="unresolved")]
    elif shape == "other-task-unfinished":
        steps = [_step("code.task.step", task_id="ct-labels", stage="narrow_test", code_task_verdict="unresolved"),
                 report_done]
    else:
        steps = [_step("workspace.read_file"), _step("workspace.search_text")]
    assert _code_task_report_completed_the_turn(steps) is False


def _code_task_through_the_door(root: Path, session: str, *, finish: bool) -> str:
    """A small repair through the production door, taken to `report` (finish) or left at `inspect_diff`."""
    from core.runtime_execution_tools import execute_runtime_tool

    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (root / "check_calc.py").write_text("from calc import add\nassert add(2, 3) == 5, add(2, 3)\n", encoding="utf-8")
    env = {**os.environ, "GIT_AUTHOR_NAME": "fx", "GIT_AUTHOR_EMAIL": "fx@local",
           "GIT_COMMITTER_NAME": "fx", "GIT_COMMITTER_EMAIL": "fx@local"}
    for command in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "seed"]):
        subprocess.run(["git", "-C", str(root), *command], check=True, capture_output=True, env=env)
    ctx = {"workspace": str(root), "workspace_root": str(root), "session_id": session,
           "runtime_session_id": session, "operating_mode": "auto"}

    def door(intent: str, arguments: dict):
        result = execute_runtime_tool(intent, arguments, source_context=ctx)
        assert result is not None, intent
        return result

    task_id = door("code.task.open", {"objective": "Repair add so the calc check passes"}).details["task_id"]
    counter = iter(range(1, 100))

    def step(intent: str, arguments: dict):
        return door("code.task.step", {"task_id": task_id, "step_id": f"s{next(counter)}", "intent": intent,
                                       "arguments": arguments})

    assert step("workspace.run_tests", {"command": "python3 check_calc.py"}).details["tool_result"]["success"] is False
    assert door("code.task.identify", {"task_id": task_id, "path": "calc.py", "line": 2, "reason": "add subtracts"}).ok
    assert step("workspace.read_file", {"path": "calc.py"}).ok
    fixed = {"path": "calc.py", "content": "def add(a, b):\n    return a + b\n"}
    assert door("code.task.propose", {"task_id": task_id, "proposal_id": "add", "intent": "workspace.write_file",
                                      "arguments": fixed, "rationale": "Owner calc.py: add must add."}).ok
    assert door("code.task.approve", {"task_id": task_id, "proposal_id": "add"}).ok
    assert step("workspace.write_file", fixed).ok
    assert step("workspace.run_tests", {"command": "python3 check_calc.py"}).details["tool_result"]["success"] is True
    assert step("workspace.run_tests", {"command": "python3 check_calc.py"}).details["tool_result"]["success"] is True
    if finish:
        assert step("workspace.git_diff", {}).ok
    return task_id


@pytest.mark.parametrize("finish", [True, False], ids=["completed-report", "unfinished-task"])
def test_the_loop_answers_a_budget_stop_on_a_completed_report_with_that_report(tmp_path, monkeypatch, finish):
    """Through the real tool loop (scripted at the provider seam): the round budget runs out on the round that
    published the code task's report. A completed report is the answer; any other budget stop stays incomplete."""
    import core.agent_runtime.research_tool_loop_facade as loop
    from apps.vool_agent import VoolAgent
    from core.code_assistant.task_runtime import code_task_runtime
    from core.mode_permission_policy import reset_mode_permission_state
    from tests.test_a_narrated_batch_does_not_vanish import _call, _Router

    reset_mode_permission_state()
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    monkeypatch.setenv("VOOL_CODE_TASK_DIR", str(tmp_path / "code_tasks"))
    code_task_runtime().reset()
    root = tmp_path / "calc"
    root.mkdir()
    session = f"openclaw:{uuid.uuid4().hex[:20]}"
    try:
        task_id = _code_task_through_the_door(root, session, finish=finish)
        monkeypatch.setattr(loop, "_MAX_MODEL_ROUNDS_PER_TURN", 1)
        router = _Router([_call("code.task.report", task_id=task_id)])
        agent = VoolAgent.__new__(VoolAgent)
        agent.memory_router = router
        agent._should_keep_ai_first_chat_lane = lambda **_kw: False
        agent._should_run_builder_controller = lambda **_kw: False
        agent._plan_tool_workflow = lambda **_kw: SimpleNamespace(handled=False, stop_after=False, next_payload=None,
                                                                   reason="")
        agent.hive_activity_tracker = None
        agent.public_hive_bridge = None
        result = agent._maybe_execute_model_tool_intent(
            task=SimpleNamespace(task_id="budget-report"),
            effective_input="Publish the report for the calc repair.",
            classification={"task_class": "debugging"},
            interpretation=SimpleNamespace(),
            context_result=SimpleNamespace(),
            persona=SimpleNamespace(),
            session_id=session,
            source_context={"surface": "api", "workspace": str(root), "workspace_root": str(root),
                            "workspace_binding": "project", "project_id": root.name,
                            "session_id": session, "runtime_session_id": session},
            surface="api",
        ) or {}
    finally:
        code_task_runtime().reset()
        reset_mode_permission_state()
    details = result.get("details") or {}
    observed = (result.get("status"), str(result.get("response") or "")[:300], sorted(details), router.calls)
    assert "code.task.report" in (details.get("tool_steps") or []), observed
    assert details.get("loop_stop_reason") == "step_budget_exhausted", (
        details.get("loop_stop_reason"), result.get("status"), result.get("mode"), str(result.get("response") or "")[:400])
    response = str(result.get("response") or "")
    if finish:
        assert "incomplete" not in response.lower(), response[:600]
        assert task_id in response or "completed" in response.lower(), response[:600]
    else:
        assert "incomplete" in response.lower(), response[:600]
