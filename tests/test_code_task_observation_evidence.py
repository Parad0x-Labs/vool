"""A failing check's own evidence reaches the model (coding revision 3, observation evidence).

Measured in the served second-defect journey (evidence `dev-p3-13-served-units`, prompts 009/010): the failed full
check's observation line was cut at ~900 of ~7000 characters. Its keys render sorted, so the task wrapper's
scaffolding (`next`, `plan`, `receipt_id`) filled the line's share and the step's evidence -- nested under
`tool_result`, which sorts last -- never reached the model. The wrapper had also discarded the inner tool's own
bounded observation. And the evidence was not actionable for a Node assertion: the failure summary was the first
stderr line, `node:assert:150`, and the diagnostic query `:150`.

Here the failure line is chosen by what it states, not by where it sits (stack frames, runtime-internal locations,
echoed source and traceback headers are skipped); the task keeps the inner tool's observation; the code-task
observation carries a bounded `evidence` summary and declares the fields that lead when it has to be clipped; the
renderer honours that declaration for clipped lines only, with identical length, share and notice.

Real files and real commands through the production door (Python checks, so no extra runtime is needed); the Node
case uses stderr recorded from node 22. No model is involved and none is claimed.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

FEES = "def fee(amount):\n    return amount * 2 // 100\n"
FEES_FIXED = "def fee(amount):\n    return amount * 3 // 100\n"
CHECK = "from billing.fees import fee\nassert fee(200) == 6, 'fee(200) must be 6'\nprint('fees ok')\n"
NOTES = "billing notes: operator owned\n"
#: Recorded from `node check_format.js` (node v22.23.1); the workspace path is replaced by `/workspace`.
NODE_ASSERTION_STDERR = """node:assert:150
  throw new AssertionError(obj);
  ^

AssertionError [ERR_ASSERTION]: format.js formatPrice(5) must be $5.00

'$5' !== '$5.00'

    at Object.<anonymous> (/workspace/check_format.js:3:8)
    at Module._compile (node:internal/modules/cjs/loader:1781:14)
    at Object..js (node:internal/modules/cjs/loader:1913:10)
    at Module.load (node:internal/modules/cjs/loader:1505:32)
    at Function._load (node:internal/modules/cjs/loader:1309:12)
    at wrapModuleLoad (node:internal/modules/cjs/loader:254:19)
    at Function.executeUserEntryPoint [as runMain] (node:internal/modules/run_main:171:5)
    at node:internal/main/run_main_module:36:49 {
  generatedMessage: false,
  code: 'ERR_ASSERTION',
  actual: '$5',
  expected: '$5.00',
  operator: 'strictEqual',
  diff: 'simple'
}

Node.js v22.23.1
"""


def _git(root: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "fx", "GIT_AUTHOR_EMAIL": "fx@local",
           "GIT_COMMITTER_NAME": "fx", "GIT_COMMITTER_EMAIL": "fx@local"}
    out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30, env=env)
    assert out.returncode == 0, out.stderr


@pytest.fixture
def billing(tmp_path, monkeypatch):
    from core.code_assistant.task_runtime import code_task_runtime
    from core.mode_permission_policy import reset_mode_permission_state

    reset_mode_permission_state()
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    monkeypatch.setenv("VOOL_CODE_TASK_DIR", str(tmp_path / "code_tasks"))
    code_task_runtime().reset()
    root = tmp_path / "billing-project"
    (root / "billing").mkdir(parents=True)
    (root / "billing" / "__init__.py").write_text("", encoding="utf-8")
    (root / "billing" / "fees.py").write_text(FEES, encoding="utf-8")
    (root / "check_fees.py").write_text(CHECK, encoding="utf-8")
    (root / "NOTES.md").write_text(NOTES, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "seed")
    yield root
    code_task_runtime().reset()
    reset_mode_permission_state()


def _door(intent: str, arguments: dict, ctx: dict):
    from core.runtime_execution_tools import execute_runtime_tool

    result = execute_runtime_tool(intent, arguments, source_context=ctx)
    assert result is not None, intent
    return result


def _journal(task_id: str) -> dict:
    return json.loads((Path(os.environ["VOOL_CODE_TASK_DIR"]) / f"{task_id}.json").read_text(encoding="utf-8"))


class Task:
    def __init__(self, root: Path, session: str) -> None:
        self.ctx = {"workspace": str(root), "workspace_root": str(root), "session_id": session,
                    "runtime_session_id": session, "operating_mode": "auto"}
        self.id = _door("code.task.open", {"objective": "Repair the billing fee check"}, self.ctx).details["task_id"]
        self.n = 0

    def step(self, intent: str, arguments: dict):
        self.n += 1
        return _door("code.task.step", {"task_id": self.id, "step_id": f"s{self.n}", "intent": intent,
                                        "arguments": arguments}, self.ctx)


def _observation(result) -> dict:
    from core.agent_runtime.response_policy_tool_history import tool_history_observation_payload

    return tool_history_observation_payload(execution=result, tool_name="code.task.step")


def _render(observations: list[dict]):
    from core.prompt_normalizer import _runtime_tool_observation_message

    return _runtime_tool_observation_message({"runtime_tool_observations": observations})


def _bulky(index: int) -> dict:
    return {"intent": "workspace.read_file", "path": f"module_{index:02d}.py", "ok": True, "status": "executed",
            "response_preview": f"MODULE_{index:02d} " + "x" * 3000}


# ---------------------------------------------------------------------------
# The failure line is chosen by what it states
# ---------------------------------------------------------------------------


def test_a_node_assertion_is_summarized_by_its_message_not_its_first_line():
    from core.execution.validation_tools import render_validation_result
    from core.runtime_execution_tools import _extract_command_failure_followup, _extract_failure_summary

    rendered = render_validation_result("workspace.run_tests", command="node check_format.js", cwd=".", runner_result={
        "status": "executed", "stdout": "", "stderr": NODE_ASSERTION_STDERR, "returncode": 1, "success": False},
        label="Validation test run")
    expected = "AssertionError [ERR_ASSERTION]: format.js formatPrice(5) must be $5.00"
    assert rendered["details"]["failure_summary"] == expected
    assert _extract_failure_summary(command="node check_format.js", stdout="", stderr=NODE_ASSERTION_STDERR,
                                    returncode=1) == expected
    followup = _extract_command_failure_followup(stdout="", stderr=NODE_ASSERTION_STDERR)
    assert followup["diagnostic_query"] == "formatPrice(", followup
    assert followup["error_path"] == "/workspace/check_format.js" and followup["error_line"] == 3


def test_a_python_traceback_is_summarized_by_its_error_line(billing):
    task = Task(billing, "python-traceback")
    red = task.step("workspace.run_tests", {"command": "python3 check_fees.py"})
    observation = red.details["tool_result"]["observation"]
    assert observation["success"] is False
    assert observation["failure_summary"] == "AssertionError: fee(200) must be 6", observation
    assert observation["diagnostic_query"] == "fee(", observation


@pytest.mark.parametrize(("stderr", "validation", "sandbox"), [
    ("FAILED test_example\n", "FAILED test_example", "FAILED test_example"),
    ("node:assert:150\n", "node:assert:150", "node:assert:150"),
    ("", "", "`make check` exited with code 1"),
], ids=["marked-line", "only-non-message-lines", "no-output"])
def test_what_a_summary_falls_back_to_is_unchanged(stderr, validation, sandbox):
    from core.execution.validation_tools import render_validation_result
    from core.runtime_execution_tools import _extract_failure_summary

    rendered = render_validation_result("workspace.run_tests", command="make check", cwd=".", runner_result={
        "status": "executed", "stdout": "", "stderr": stderr, "returncode": 1, "success": False},
        label="Validation test run")
    assert rendered["details"]["failure_summary"] == validation
    assert _extract_failure_summary(command="make check", stdout="", stderr=stderr, returncode=1) == sandbox


# ---------------------------------------------------------------------------
# The step keeps the inner tool's evidence, and the model sees it even when the line is clipped
# ---------------------------------------------------------------------------


def test_the_task_keeps_the_inner_observation_with_its_step(billing):
    task = Task(billing, "kept-observation")
    task.step("workspace.run_tests", {"command": "python3 check_fees.py"})
    stored = _journal(task.id)["steps"]["s1"]["result"]["observation"]
    assert stored["failure_summary"] == "AssertionError: fee(200) must be 6"
    assert stored["intent"] == "workspace.run_tests"


def test_the_code_task_observation_carries_bounded_evidence_and_its_priority(billing):
    task = Task(billing, "bounded-evidence")
    red = task.step("workspace.run_tests", {"command": "python3 check_fees.py"})
    payload = _observation(red)
    evidence = payload["evidence"]
    assert evidence["failure_summary"] == "AssertionError: fee(200) must be 6"
    assert evidence["diagnostic_query"] == "fee(" and evidence["returncode"] == 1 and evidence["success"] is False
    assert not {"stdout", "stderr", "stdout_excerpt", "stderr_excerpt", "lines", "content"} & set(evidence)
    assert payload["observation_priority"][:4] == ["intent", "ok", "status", "stage"]
    assert payload["tool_result"].get("stderr_excerpt")  # the nested result keeps its own output budget


def test_a_clipped_code_task_observation_still_shows_its_evidence_and_waiting_repairs(billing):
    task = Task(billing, "clipped-evidence")
    red = task.step("workspace.run_tests", {"command": "python3 check_fees.py"})
    payload = _observation(red)
    crowd = [_bulky(index) for index in range(24)]
    message = _render([*crowd, payload])
    # The renderer labels a line by `tool`/`tool_name`, which this payload does not carry (it names its tool under
    # `intent`), and key order is what is under test, so the task's line is found by position: header, crowd, task.
    line = message.content.splitlines()[len(crowd) + 1]
    assert line.startswith("- {") and "module_" not in line and " …[clipped here;" in line, line[-200:]
    assert "fee(200) must be 6" in message.content
    assert '"pending_repairs": []' in message.content and '"stage": "identify"' in message.content
    undeclared = {key: value for key, value in payload.items() if key != "observation_priority"}
    assert '"stage": "identify"' not in _render([*crowd, undeclared]).content  # sorted, the task's stage is cut off
    before = {key: value for key, value in payload.items() if key not in {"observation_priority", "evidence"}}
    assert "fee(200) must be 6" not in _render([*crowd, before]).content  # the evidence only lived in `tool_result`


def test_an_observation_that_fits_renders_exactly_as_before():
    small = {"intent": "code.task.report", "ok": True, "status": "ok", "stage": "report",
             "observation_priority": ["intent", "ok", "status", "stage"]}
    message = _render([small])
    expected = json.dumps({key: value for key, value in small.items() if key != "observation_priority"},
                          ensure_ascii=False, sort_keys=True, default=str)
    assert f"- {expected}" in message.content
    assert "observation_priority" not in message.content


def test_declared_order_changes_the_order_and_nothing_else():
    from core.prompt_normalizer import _priority_first_json

    item = {"zeta": {"b": 2, "a": "ü"}, "alpha": [1, "two"], "status": "failed", "evidence": {"k": "v"}}
    reordered = _priority_first_json(item, ("status", "evidence", "missing"))
    assert reordered.startswith('{"status": "failed", "evidence": {"k": "v"}, "alpha"')
    assert len(reordered) == len(json.dumps(item, ensure_ascii=False, sort_keys=True, default=str))
    assert json.loads(reordered) == item


def test_search_and_read_evidence_name_what_they_found_without_file_bytes(billing):
    task = Task(billing, "search-read-evidence")
    search = _observation(task.step("workspace.search_text", {"query": "def fee", "path": "."}))
    assert [row["path"] for row in search["evidence"]["matches"]] == ["billing/fees.py"], search["evidence"]
    read = _observation(task.step("workspace.read_file", {"path": "billing/fees.py"}))
    assert read["evidence"]["path"] == "billing/fees.py" and read["evidence"]["hash"]
    assert "lines" not in read["evidence"] and "content" not in read["evidence"]


def test_evidence_is_bounded_whatever_the_tool_reports():
    from core.agent_runtime.response_policy_tool_history import _bounded_evidence

    evidence = _bounded_evidence({"failure_summary": "x" * 5000, "matches": [{"path": f"f{i}.py"} for i in range(30)],
                                  "stdout": "y" * 9000, "returncode": 1})
    assert len(evidence["failure_summary"]) == 241 and evidence["failure_summary"].endswith("…")
    assert len(evidence["matches"]) == 9 and evidence["matches"][-1] == "…22 more"
    assert "stdout" not in evidence and evidence["returncode"] == 1


def test_a_plain_execution_without_details_is_not_given_evidence():
    from core.agent_runtime.response_policy_tool_history import tool_history_observation_payload

    payload = tool_history_observation_payload(
        execution=SimpleNamespace(ok=True, status="ok", response_text="opened", details={"task_id": "ct-000000000000"}),
        tool_name="code.task.open",
    )
    assert "evidence" not in payload and payload["observation_priority"][0] == "intent"
