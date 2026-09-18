"""Purposeful verification in the code task runtime (mission 2026-09-15).

Three decision defects at the runtime's evidence boundary, each reproduced here before its
repair:

1. A refused validation command must not become a failed verification. The prior harness
   correction measured ``workspace.run_tests`` with a ``cwd`` outside the workspace: the
   sandbox refuses and runs nothing, yet the refusal was rendered with ``returncode: 0`` and
   journaled as a CURRENT failed narrow check -- reopening review for a check that never ran.
   A permission refusal, an unavailable sandbox, a timeout and an executed failing test are
   four different facts and must stay four different facts.

2. A repeated verification with equivalent task/source/command context and no changed
   evidence, declared purpose or user request is a loop, not progress. The runtime answers it
   with a bounded structured correction carrying the prior outcome and the lawful next action,
   instead of executing another identical campaign while the defect stays unfixed.

3. An irrelevant successful command cannot satisfy workflow verification. ``echo ok`` at
   ``narrow_test`` is green and worthless: it exercises no workspace validation surface. It
   runs, it is journaled, and it advances nothing.

Real files and real commands through the production door; no model is involved and none is
claimed.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

CALC = "def total(subtotal, tax):\n    return subtotal - tax\n"
CALC_FIXED = "def total(subtotal, tax):\n    return subtotal + tax\n"
CHECK = "from calc import total\nassert total(40, 8) == 48, total(40, 8)\nprint('calc ok')\n"


def _git(root: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "fx", "GIT_AUTHOR_EMAIL": "fx@local",
           "GIT_COMMITTER_NAME": "fx", "GIT_COMMITTER_EMAIL": "fx@local"}
    out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30, env=env)
    assert out.returncode == 0, out.stderr


def _seed_sandbox_pytest_into(target: Path) -> None:
    """Copy an importable pytest set into a sandbox workspace directory (harness repair, Goal 2
    2026-09-18): on this machine the workspace sandbox cannot import pytest from either
    interpreter's site-packages (the shared runtime delegates site-packages outside the sandbox
    read set; the system python3's user site is likewise unreadable), and PYTHONPATH is sanitized
    for sandboxed commands. Commands and argv stay exactly as the suites issue them; `cwd` is
    sys.path[0] for `-m`. Prefers the interpreter-neutral user-site pytest 8.x (3.9-compatible)
    because option-prefixed forms resolve to the system python3."""
    import shutil

    seed_root = Path.home() / "Library/Python/3.9/lib/python/site-packages"
    target.mkdir(parents=True, exist_ok=True)
    if (seed_root / "_pytest").is_dir():
        for name in ("pytest", "_pytest", "pluggy", "iniconfig", "packaging", "pygments", "exceptiongroup"):
            src = seed_root / name
            if src.is_dir():
                shutil.copytree(src, target / name, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copyfile(seed_root / "py.py", target / "py.py")
        shutil.copyfile(seed_root / "typing_extensions.py", target / "typing_extensions.py")
        return
    import _pytest
    import iniconfig
    import packaging
    import pluggy
    import py
    import pygments
    import pytest as _pytest_pkg
    for module in (_pytest_pkg, _pytest, pluggy, iniconfig, packaging, pygments):
        src = Path(module.__file__).parent
        shutil.copytree(src, target / src.name, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copyfile(Path(py.__file__), target / "py.py")


@pytest.fixture
def project(tmp_path, monkeypatch):
    from core.code_assistant.task_runtime import code_task_runtime
    from core.mode_permission_policy import reset_mode_permission_state

    reset_mode_permission_state()
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    monkeypatch.setenv("VOOL_CODE_TASK_DIR", str(tmp_path / "code_tasks"))
    code_task_runtime().reset()
    root = tmp_path / "project"
    root.mkdir()
    (root / "calc.py").write_text(CALC, encoding="utf-8")
    (root / "check.py").write_text(CHECK, encoding="utf-8")
    _seed_sandbox_pytest_into(root)
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "seed")
    yield root
    import shutil as _shutil
    _shutil.rmtree(root, ignore_errors=True)  # free the seeded import set immediately (disk floor)
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
    """A task driven to its ``narrow_test`` checkpoint with a landed repair."""

    def __init__(self, root: Path, session: str, *, content: str = CALC_FIXED) -> None:
        self.root = root
        self.ctx = {"workspace": str(root), "workspace_root": str(root), "session_id": session,
                    "runtime_session_id": session, "operating_mode": "auto"}
        self.id = _door("code.task.open", {"objective": "Repair total() so its check passes"}, self.ctx).details["task_id"]
        self.n = 0
        red = self.step("workspace.run_tests", {"command": "python3 check.py"})
        assert red.details["tool_result"]["success"] is False
        assert _door("code.task.identify", {"task_id": self.id, "path": "calc.py", "reason": "tax subtracted"}, self.ctx).ok
        assert self.step("workspace.read_file", {"path": "calc.py"}).ok
        assert _door("code.task.propose", {"task_id": self.id, "proposal_id": "fix", "intent": "workspace.write_file",
                                           "arguments": {"path": "calc.py", "content": content},
                                           "rationale": "Owner calc.py: total subtracts the tax it must add."}, self.ctx).ok
        assert _door("code.task.approve", {"task_id": self.id, "proposal_id": "fix"}, self.ctx).ok
        assert self.step("workspace.write_file", {"path": "calc.py", "content": content}).ok

    def step(self, intent: str, arguments: dict, **outer: str):
        self.n += 1
        payload = {"task_id": self.id, "step_id": f"s{self.n}", "intent": intent, "arguments": arguments, **outer}
        return _door("code.task.step", payload, self.ctx)


# ---------------------------------------------------------------------------
# 1. a refused command is not a failed verification
# ---------------------------------------------------------------------------


def test_refused_out_of_workspace_cwd_is_not_a_failed_verification(project):
    task = Task(project, "refused-cwd")
    refused = task.step("workspace.run_tests", {"command": "python3 check.py", "cwd": str(project.parent)})
    assert refused.ok is False, refused.status
    assert refused.details["executed"] is False
    journal = _journal(task.id)
    assert journal["stage"] == "narrow_test", journal["stage"]
    assert journal["narrow"] is None
    assert [v for v in journal["verifications"] if v["step_id"] == refused.details["step_id"]] == []
    # The refusal did not reopen review either: no current failed outcome exists.
    assert all(v.get("current") is not True or v.get("success") is not False for v in journal["verifications"])
    # And a lawful check still runs afterwards: the refusal consumed nothing.
    green = task.step("workspace.run_tests", {"command": "python3 check.py"})
    assert green.details["tool_result"]["success"] is True
    assert _journal(task.id)["stage"] == "cumulative"


def test_a_blocked_policy_command_is_a_refusal_not_a_failed_reproduction(project, tmp_path):
    ctx = {"workspace": str(project), "workspace_root": str(project), "session_id": "blocked",
           "runtime_session_id": "blocked", "operating_mode": "auto"}
    task_id = _door("code.task.open", {"objective": "Repair total()"}, ctx).details["task_id"]
    blocked = _door("code.task.step", {"task_id": task_id, "step_id": "b1", "intent": "sandbox.run_command",
                                       "arguments": {"command": "curl --max-time 2 http://127.0.0.1:9/"}}, ctx)
    assert blocked.ok is False, blocked.status
    assert blocked.details["executed"] is False
    journal = _journal(task_id)
    assert journal["stage"] == "reproduce"
    assert journal["reproduced_failure"] is None
    assert journal["verifications"] == []


# ---------------------------------------------------------------------------
# 2. a repeated equivalent verification is a correction, not another campaign
# ---------------------------------------------------------------------------


def _wrong_fix_task(root: Path, session: str) -> Task:
    # A first fix that is wrong: total() now doubles the tax; the check still fails.
    return Task(root, session, content="def total(subtotal, tax):\n    return subtotal * tax - subtotal + 2 * tax\n")


def test_repeated_equivalent_failed_check_returns_a_structured_correction(project):
    task = _wrong_fix_task(project, "repeat-failed")
    first = task.step("workspace.run_tests", {"command": "python3 check.py"})
    assert first.details["tool_result"]["success"] is False
    repeat = task.step("workspace.run_tests", {"command": "python3 check.py"})
    assert repeat.status == "repeated_verification", repeat.status
    assert repeat.details["executed"] is False
    assert repeat.details["prior_outcome"]["success"] is False
    assert repeat.details["prior_outcome"]["command"] == "python3 check.py"
    # The correction is bounded and actionable: it names the recovery the stage machine owes.
    assert any("re-diagnose" in action or "identify" in action for action in repeat.details.get("next", []))
    journal = _journal(task.id)
    assert len(journal["verifications"]) == 1
    assert journal["stage"] == "narrow_test"
    # A different selection is NOT a repeat: it runs.
    other = task.step("workspace.run_tests", {"command": "python3 -m py_compile calc.py"})
    assert other.details["executed"] is True


def test_declared_purpose_rerun_is_permitted(project):
    task = _wrong_fix_task(project, "declared-rerun")
    first = task.step("workspace.run_tests", {"command": "python3 check.py"})
    assert first.details["tool_result"]["success"] is False
    rerun = task.step("workspace.run_tests", {"command": "python3 check.py"},
                      rerun_reason="suspected order dependence: rerunning once to see if it flakes")
    assert rerun.details["executed"] is True
    journal = _journal(task.id)
    assert len(journal["verifications"]) == 2
    # The declared reason is journaled with the rerun, as declared -- not as verified fact.
    assert journal["verifications"][1]["rerun_reason"].startswith("suspected order dependence")


def test_changed_source_breaks_equivalence_and_the_rerun_runs(project):
    task = _wrong_fix_task(project, "changed-bytes")
    assert task.step("workspace.run_tests", {"command": "python3 check.py"}).details["tool_result"]["success"] is False
    # An external edit to the repaired file: the journal's byte binding no longer matches disk,
    # so the same command is no longer an equivalent repeat.
    (project / "calc.py").write_text("def total(subtotal, tax):\n    return subtotal + tax\n", encoding="utf-8")
    rerun = task.step("workspace.run_tests", {"command": "python3 check.py"})
    assert rerun.details["executed"] is True, rerun.status


# ---------------------------------------------------------------------------
# 3. an irrelevant success cannot satisfy verification
# ---------------------------------------------------------------------------


def test_irrelevant_successful_command_cannot_complete_the_task(project):
    task = Task(project, "irrelevant")
    noise = task.step("sandbox.run_command", {"command": "echo ok"})
    assert noise.ok is True and noise.details["executed"] is True
    journal = _journal(task.id)
    assert journal["stage"] == "narrow_test", journal["stage"]
    assert journal["narrow"] is None
    entry = [v for v in journal["verifications"] if v["step_id"] == noise.details["step_id"]]
    assert entry and entry[0]["obligation_match"] is False
    # The task cannot complete on it either.
    report = _door("code.task.report", {"task_id": task.id}, task.ctx).details
    assert report["verdict"] == "unresolved"
    # The lawful check still satisfies the stage.
    green = task.step("workspace.run_tests", {"command": "python3 check.py"})
    assert green.details["tool_result"]["success"] is True
    assert _journal(task.id)["stage"] == "cumulative"


def test_a_test_only_request_without_an_open_task_runs_top_level(project):
    """Preservation control: the repeat correction and the task plane bind only a session with
    an OPEN task. A user-directed test-only request -- no repair demanded -- keeps its ordinary
    top-level door: nothing here refuses it and no task is invented for it."""
    from core.runtime_execution_tools import execute_runtime_tool

    ctx = {"workspace": str(project), "workspace_root": str(project), "session_id": "test-only",
           "runtime_session_id": "test-only", "operating_mode": "auto"}
    result = execute_runtime_tool("workspace.run_tests", {"command": "python3 check.py"},
                                  source_context=ctx)
    assert result.ok is False or result.details.get("success") is False  # the real failing suite
    assert not list(Path(os.environ["VOOL_CODE_TASK_DIR"]).glob("ct-*.json"))
    # And a coding mutation is still not authorized by it: no proposal, no approval, no task.
    from core.code_assistant.task_runtime import active_task_control_intents

    assert active_task_control_intents(ctx) == ()


def test_changed_fixture_invalidates_the_verified_evidence(project):
    """A different fixture -- the check's own input file -- invalidates prior evidence: the
    green outcome's bytes binding covers the inputs its command named, so once they change the
    report cannot publish completion over evidence about a workspace that no longer exists."""
    task = Task(project, "changed-fixture")
    assert task.step("workspace.run_tests", {"command": "python3 check.py"}).details["stage"] == "cumulative"
    assert task.step("workspace.run_tests", {"command": "python3 check.py"}).details["stage"] == "inspect_diff"
    assert task.step("workspace.git_diff", {}).ok
    report = _door("code.task.report", {"task_id": task.id}, task.ctx).details
    assert report["verdict"] == "completed"
    # The operator edits the check fixture (a stricter assertion); the repair is untouched.
    (project / "check.py").write_text(
        "from calc import total\nassert total(40, 8) == 48, total(40, 8)\nassert total(1, 1) == 2\nprint('calc ok')\n",
        encoding="utf-8")
    report = _door("code.task.report", {"task_id": task.id}, task.ctx).details
    assert report["verdict"] == "unresolved", report["verdict"]
    assert report["bytes_changed"] == ["check.py"]
    assert report["unresolved"] == ["verified_bytes_changed"]
    # The changed fixture is also changed evidence for the repeat rule: in a task still at a
    # verification stage, the identical command is NOT a repeat once its inputs moved on disk.
    (project / "calc.py").write_text(CALC, encoding="utf-8")  # a fresh defect for a fresh task
    stuck = _wrong_fix_task(project, "changed-fixture-2")
    assert stuck.step("workspace.run_tests", {"command": "python3 check.py"}).details["tool_result"]["success"] is False
    (project / "check.py").write_text(CHECK, encoding="utf-8")  # restore, then change differently
    (project / "check.py").write_text(CHECK + "assert total(2, 1) == 3\n", encoding="utf-8")
    rerun = stuck.step("workspace.run_tests", {"command": "python3 check.py"})
    assert rerun.status != "repeated_verification", rerun.status
    assert rerun.details["executed"] is True


def test_a_quoted_example_fix_does_not_authorize_mutation(project):
    """A quoted/example repair -- "for example, calc.py could be `return subtotal + tax`" --
    carries no proposal and no approval, so no door turns it into bytes. At every stage the
    write is refused by the task's authority, typed, and nothing lands on disk."""
    from core.runtime_execution_tools import execute_runtime_tool

    quoted = {"path": "calc.py", "content": "def total(subtotal, tax):\n    return (subtotal + tax) * 1\n"}
    ctx = {"workspace": str(project), "workspace_root": str(project), "session_id": "quoted",
           "runtime_session_id": "quoted", "operating_mode": "auto"}
    # Before any task exists, a mutation has no proposal to match and no task to run in; the
    # coding lane's contract keeps it behind propose/approve, and the top-level write is not
    # the task plane. Open the task the honest way and try the quoted write at every stage.
    task_id = _door("code.task.open", {"objective": "repair total()"}, ctx).details["task_id"]
    red = _door("code.task.step", {"task_id": task_id, "step_id": "repro", "intent": "workspace.run_tests",
                                   "arguments": {"command": "python3 check.py"}}, ctx)
    assert red.details["tool_result"]["success"] is False
    attempt = _door("code.task.step", {"task_id": task_id, "step_id": "quote-1", "intent": "workspace.write_file",
                                       "arguments": quoted}, ctx)
    assert attempt.ok is False and attempt.status == "stage_violation", attempt.status
    assert (project / "calc.py").read_text(encoding="utf-8") == CALC
    # Even at the mutate stage, content nobody proposed or approved is refused: the example
    # text in a chat message is not an approval. Drive the task to mutate with a REAL
    # proposal approved, then try the quoted (different) bytes.
    assert _door("code.task.identify", {"task_id": task_id, "path": "calc.py", "reason": "subtracts"}, ctx).ok
    assert _door("code.task.step", {"task_id": task_id, "step_id": "read", "intent": "workspace.read_file",
                                    "arguments": {"path": "calc.py"}}, ctx).ok
    assert _door("code.task.propose", {"task_id": task_id, "proposal_id": "p", "intent": "workspace.write_file",
                                       "arguments": {"path": "calc.py", "content": CALC_FIXED},
                                       "rationale": "Owner calc.py: add the tax."}, ctx).ok
    assert _door("code.task.approve", {"task_id": task_id, "proposal_id": "p"}, ctx).ok
    sneaked = _door("code.task.step", {"task_id": task_id, "step_id": "quote-2", "intent": "workspace.write_file",
                                       "arguments": quoted}, ctx)
    assert sneaked.ok is False and sneaked.status == "approval_mismatch", sneaked.status
    assert (project / "calc.py").read_text(encoding="utf-8") == CALC
    assert execute_runtime_tool("code.task.report", {"task_id": task_id}, source_context=ctx).details[
        "verdict"] == "unresolved"
