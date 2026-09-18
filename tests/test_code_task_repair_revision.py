"""Verification belongs to the bytes it checked (coding revision 3, P1-2).

The independent review proved that a failed check of a FIRST repair kept reopening diagnosis
after a corrected repair had landed -- even across a runtime restart -- so the next repair cycle
never had to verify the new bytes. Here every narrow/cumulative outcome carries the workspace
revision it verified; a landed mutation retires the current evidence (the history keeps every
outcome); only a failed check of the CURRENT revision reopens review; a result that finishes
after the bytes changed is recorded stale and moves nothing; and no green evidence, report
verdict or PR description survives a change to the bytes it verified.

Novel data (a Python temperature converter with a focused and a full check) beside the review's
JavaScript cases. Real files and real commands through the production door; no model is involved
and none is claimed.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LEGACY = REPO / "tests" / "fixtures" / "code_task_journals"
BUGGY = "def to_fahrenheit(celsius):\n    return celsius * 9 / 5 - 32\n"
WRONG_1 = "def to_fahrenheit(celsius):\n    return celsius * 5 / 9 + 32\n"
WRONG_2 = "def to_fahrenheit(celsius):\n    return (celsius + 32) * 9 / 5\n"
APPROX = "def to_fahrenheit(celsius):\n    return celsius * 2 + 12\n"  # right at 100 C, wrong elsewhere
FIXED = "def to_fahrenheit(celsius):\n    return celsius * 9 / 5 + 32\n"
FOCUSED = "from convert import to_fahrenheit\nassert to_fahrenheit(100) == 212, to_fahrenheit(100)\nprint('boiling ok')\n"
FULL = (
    "from convert import to_fahrenheit\n"
    "assert to_fahrenheit(100) == 212, to_fahrenheit(100)\n"
    "assert to_fahrenheit(0) == 32, to_fahrenheit(0)\n"
    "assert to_fahrenheit(-40) == -40, to_fahrenheit(-40)\n"
    "print('all conversions ok')\n"
)
NOTES = "field notes: keep\n"
FOCUSED_CMD = "python3 check_boiling.py"
FULL_CMD = "python3 check_all.py"


def _git(root: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "fx", "GIT_AUTHOR_EMAIL": "fx@local",
           "GIT_COMMITTER_NAME": "fx", "GIT_COMMITTER_EMAIL": "fx@local"}
    out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30, env=env)
    assert out.returncode == 0, out.stderr


@pytest.fixture
def converter(tmp_path, monkeypatch):
    from core.code_assistant.task_runtime import code_task_runtime
    from core.mode_permission_policy import reset_mode_permission_state

    reset_mode_permission_state()
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    monkeypatch.setenv("VOOL_CODE_TASK_DIR", str(tmp_path / "code_tasks"))
    code_task_runtime().reset()
    root = tmp_path / "converter"
    root.mkdir()
    for name, text in (("convert.py", BUGGY), ("check_boiling.py", FOCUSED), ("check_all.py", FULL), ("notes.txt", NOTES)):
        (root / name).write_text(text, encoding="utf-8")
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
    """One task through the door; each helper is one real control-plane call."""

    def __init__(self, root: Path, session: str) -> None:
        self.root = root
        self.ctx = {"workspace": str(root), "workspace_root": str(root), "session_id": session,
                    "runtime_session_id": session, "operating_mode": "auto"}
        self.id = _door("code.task.open", {"objective": "Repair to_fahrenheit so every conversion check passes"},
                        self.ctx).details["task_id"]
        self.counter = 0

    def _sid(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}-{self.counter}"

    def step(self, intent: str, arguments: dict, step_id: str | None = None, **outer: str):
        return _door("code.task.step", {"task_id": self.id, "step_id": step_id or self._sid(intent.split(".")[-1]),
                                        "intent": intent, "arguments": arguments, **outer}, self.ctx)

    def check(self, command: str, step_id: str | None = None, **outer: str):
        result = self.step("workspace.run_tests", {"command": command}, step_id, **outer)
        assert result.details["executed"], (result.status, result.response_text)
        return result

    def identify(self, reason: str):
        return _door("code.task.identify", {"task_id": self.id, "path": "convert.py", "line": 2, "reason": reason}, self.ctx)

    def repair(self, content: str):
        """read -> propose (reviewed base from that read) -> approve -> execute, all through the door."""
        assert self.step("workspace.read_file", {"path": "convert.py"}).ok
        pid = self._sid("proposal")
        proposed = _door("code.task.propose", {"task_id": self.id, "proposal_id": pid, "intent": "workspace.write_file",
                                               "arguments": {"path": "convert.py", "content": content},
                                               "rationale": "Owner convert.py: the Fahrenheit formula is wrong."}, self.ctx)
        assert proposed.ok, proposed.response_text
        assert _door("code.task.approve", {"task_id": self.id, "proposal_id": pid}, self.ctx).ok
        landed = self.step("workspace.write_file", {"path": "convert.py", "content": content})
        assert landed.ok, (landed.status, landed.response_text)
        return landed

    def report(self):
        return _door("code.task.report", {"task_id": self.id}, self.ctx)

    def pr(self):
        return _door("code.task.pr_description", {"task_id": self.id}, self.ctx)


def _reproduced(root: Path, session: str) -> Task:
    task = Task(root, session)
    assert task.check(FULL_CMD).details["tool_result"]["success"] is False
    assert task.identify("to_fahrenheit subtracts 32 instead of adding it").ok
    return task


def _history(task: Task) -> list[tuple[str, bool, int, bool]]:
    return [(v["stage"], v["success"], v["revision"], v["current"]) for v in _journal(task.id)["verifications"]]


# ---------------------------------------------------------------------------
# The failure class: a stale failure cannot reopen review over new bytes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("variant", ["narrow", "cumulative"])
def test_a_corrected_repair_must_be_verified_before_review_reopens(converter, variant):
    from core.code_assistant.task_runtime import code_task_runtime

    task = _reproduced(converter, f"stale-{variant}")
    if variant == "narrow":
        task.repair(WRONG_1)
        assert task.check(FOCUSED_CMD).details["tool_result"]["success"] is False
    else:
        task.repair(APPROX)
        assert task.check(FOCUSED_CMD).details["tool_result"]["success"] is True
        assert task.check(FULL_CMD).details["tool_result"]["success"] is False
    assert task.identify("the first repair is wrong").ok  # a CURRENT failure reopens review
    task.repair(FIXED)
    for restarted in (False, True):
        if restarted:
            code_task_runtime().reset()
        refused = task.identify("try another repair without checking the corrected bytes")
        assert refused.ok is False and refused.status == "stage_violation", (restarted, refused.status)
        assert "not been verified" in refused.response_text
        assert not refused.details.get("verification_failed")
    report = task.report().details
    assert report["revision"] == 2 and not report.get("verification_failed")
    assert report["tests"]["narrow"] is None and report["tests"]["cumulative"] is None
    earlier = _history(task)
    assert earlier and all(rev == 1 for _stage, _ok, rev, _current in earlier)
    assert task.pr().status == "insufficient_evidence"
    assert task.check(FOCUSED_CMD).details["tool_result"]["success"] is True
    assert _journal(task.id)["stage"] == "cumulative"
    assert _history(task)[: len(earlier)] == earlier  # history is kept verbatim
    assert _history(task)[-1] == ("narrow_test", True, 2, True)


def test_a_check_that_finishes_after_a_repair_landed_is_recorded_stale(converter, monkeypatch):
    from core.code_assistant.task_runtime import code_task_runtime

    task = _reproduced(converter, "late-result")
    task.repair(WRONG_1)
    assert task.check(FOCUSED_CMD).details["tool_result"]["success"] is False
    runtime = code_task_runtime()
    original = runtime._dispatch
    state = {"hooked": False}

    def late(intent, arguments, context):
        outcome = original(intent, arguments, context)  # the check verifies WRONG_1's bytes...
        if intent == "workspace.run_tests" and not state["hooked"]:
            state["hooked"] = True
            assert task.identify("first repair wrong").ok  # ...while recovery lands new bytes
            task.repair(FIXED)
        return outcome

    monkeypatch.setattr(runtime, "_dispatch", late)
    # An intentional rerun of an equivalent journaled check declares its purpose; the runtime's
    # repeat correction would otherwise answer it (see test_code_task_purposeful_verification).
    result = task.check(FOCUSED_CMD, step_id="late-check",
                        rerun_reason="race probe: verifying while a recovery repair lands")
    assert result.details["tool_result"]["success"] is False
    verification = result.details["verification"]
    assert verification["current"] is False and "landed" in verification["stale_reason"], verification
    journal = _journal(task.id)
    assert journal["revision"] == 2 and journal["narrow"] is None and journal["stage"] == "narrow_test"
    late_record = next(v for v in journal["verifications"] if v["step_id"] == "late-check")
    assert late_record["revision"] == 1 and late_record["current"] is False
    refused = task.identify("the late failure must not reopen review")
    assert refused.ok is False and refused.status == "stage_violation"


def test_an_outcome_recorded_for_another_revision_is_never_current(converter):
    """Pins the revision comparison itself: a journal whose narrow outcome belongs to an earlier
    revision (hand-edited, or written by a runtime that did not retire evidence) can neither reopen
    review nor count toward completion."""
    from core.code_assistant.task_runtime import code_task_runtime

    task = _reproduced(converter, "foreign-outcome")
    task.repair(WRONG_1)
    assert task.check(FOCUSED_CMD).details["tool_result"]["success"] is False
    assert task.identify("first repair wrong").ok
    task.repair(FIXED)
    path = Path(os.environ["VOOL_CODE_TASK_DIR"]) / f"{task.id}.json"
    journal = json.loads(path.read_text(encoding="utf-8"))
    earlier = next(v for v in journal["verifications"] if v["stage"] == "narrow_test")
    journal["narrow"] = {key: earlier[key] for key in ("step_id", "command", "success", "returncode", "revision", "bytes")}
    path.write_text(json.dumps(journal), encoding="utf-8")
    code_task_runtime().reset()
    refused = task.identify("an outcome of revision 1 must not reopen review at revision 2")
    assert refused.ok is False and refused.status == "stage_violation", refused.status
    assert not refused.details.get("verification_failed")
    assert task.report().details["verdict"] == "unresolved"

def test_repair_bytes_changed_while_a_check_ran_are_not_verified(converter, monkeypatch):
    from core.code_assistant.task_runtime import code_task_runtime

    task = _reproduced(converter, "bytes-moved")
    task.repair(FIXED)
    runtime = code_task_runtime()
    original = runtime._dispatch
    state = {"hooked": False}

    def edit_during_check(intent, arguments, context):
        outcome = original(intent, arguments, context)
        if intent == "workspace.run_tests" and not state["hooked"]:
            state["hooked"] = True
            (converter / "convert.py").write_text(FIXED + "# reformatted\n", encoding="utf-8")
        return outcome

    monkeypatch.setattr(runtime, "_dispatch", edit_during_check)
    moved = task.check(FOCUSED_CMD, step_id="moving-check")
    assert moved.details["tool_result"]["success"] is True
    assert moved.details["verification"]["current"] is False
    assert "changed while this check ran" in moved.details["verification"]["stale_reason"], moved.details["verification"]
    journal = _journal(task.id)
    assert journal["narrow"] is None and journal["stage"] == "narrow_test"
    # Checking again without re-reading still verifies bytes the task never reviewed.
    unreviewed = task.check(FOCUSED_CMD)
    assert unreviewed.details["verification"]["current"] is False
    assert "differs" in unreviewed.details["verification"]["stale_reason"]
    assert task.step("workspace.read_file", {"path": "convert.py"}).ok  # review the changed bytes
    reviewed = task.check(FOCUSED_CMD)
    assert reviewed.details["verification"]["current"] is True
    assert _journal(task.id)["stage"] == "cumulative"


def test_an_edit_after_green_evidence_retires_the_report_verdict_and_pr_description(converter):
    task = _reproduced(converter, "green-then-edited")
    task.repair(FIXED)
    assert task.check(FOCUSED_CMD).details["tool_result"]["success"] is True
    assert task.check(FULL_CMD).details["tool_result"]["success"] is True
    assert task.step("workspace.git_diff", {}).ok
    green = task.report().details
    assert green["verdict"] == "completed" and green["bytes_current"] is True
    assert task.pr().ok
    (converter / "convert.py").write_text(BUGGY, encoding="utf-8")  # someone reverts the repair
    stale = task.report().details
    assert stale["verdict"] == "unresolved" and stale["bytes_current"] is False
    assert "verified_bytes_changed" in stale["unresolved"]
    pr = task.pr()
    assert pr.ok is False and pr.status == "insufficient_evidence"
    assert any("verified" in item and "changed" in item for item in pr.details["missing"]), pr.details["missing"]
    assert (converter / "notes.txt").read_text(encoding="utf-8") == NOTES


def test_a_prepared_pr_description_names_its_bytes_and_a_rollback_retires_it(converter):
    """A surface that drafts a pull request reads the task's prepared description from the journal. The
    description records the revision and bytes it describes; after a rollback those bytes are gone, so it
    moves to history and is never the task's current description."""
    task = _reproduced(converter, "pr-rollback")
    task.repair(FIXED)
    assert task.check(FOCUSED_CMD).details["tool_result"]["success"] is True
    assert task.check(FULL_CMD).details["tool_result"]["success"] is True
    assert task.step("workspace.git_diff", {}).ok
    assert task.report().details["verdict"] == "completed"
    assert task.pr().ok
    prepared = _journal(task.id)["pr_description"]
    assert prepared.get("revision") == 1, prepared
    # The description's bytes cover the repaired file AND the check inputs its verification
    # named (the full-suite entry point), so a changed check file retires it too.
    assert prepared.get("bytes") == {
        "convert.py": "file:" + hashlib.sha256(FIXED.encode("utf-8")).hexdigest(),
        "check_all.py": "file:" + hashlib.sha256(FULL.encode("utf-8")).hexdigest(),
    }, prepared
    rolled = _door("code.task.rollback", {"task_id": task.id}, task.ctx)
    assert rolled.ok, (rolled.status, rolled.response_text)
    assert (converter / "convert.py").read_text(encoding="utf-8") == BUGGY
    journal = _journal(task.id)
    assert journal["pr_description"] == {}, journal["pr_description"]
    retired = journal["pr_description_history"][-1]
    assert retired["title"] == prepared["title"] and retired["revision"] == 1, retired
    assert "rolled back" in retired["stale_reason"], retired
    assert task.pr().status == "rolled_back"
    assert (converter / "notes.txt").read_text(encoding="utf-8") == NOTES


def test_repeated_cycles_keep_every_failure_and_complete_only_on_current_green(converter):
    task = _reproduced(converter, "cycles")
    task.repair(WRONG_1)
    assert task.check(FOCUSED_CMD).details["tool_result"]["success"] is False
    assert task.identify("second attempt").ok
    task.repair(WRONG_2)
    assert task.identify("skipping verification").status == "stage_violation"
    assert task.check(FOCUSED_CMD).details["tool_result"]["success"] is False
    assert task.identify("third attempt").ok
    task.repair(APPROX)
    assert task.check(FOCUSED_CMD).details["tool_result"]["success"] is True
    assert task.check(FULL_CMD).details["tool_result"]["success"] is False
    assert task.identify("fourth attempt").ok
    task.repair(FIXED)
    assert task.check(FOCUSED_CMD).details["tool_result"]["success"] is True
    assert task.check(FULL_CMD).details["tool_result"]["success"] is True
    assert task.step("workspace.git_diff", {}).ok
    assert _history(task) == [
        ("narrow_test", False, 1, True),
        ("narrow_test", False, 2, True),
        ("narrow_test", True, 3, True),
        ("cumulative", False, 3, True),
        ("narrow_test", True, 4, True),
        ("cumulative", True, 4, True),
    ]
    journal = _journal(task.id)
    # four accepted diagnoses: three superseded ones stay as history beside the current defect
    assert journal["revision"] == 4 and len(journal["diagnoses"]) == 3
    assert journal["defect"]["reason"] == "fourth attempt"
    assert task.report().details["verdict"] == "completed"
    assert (converter / "convert.py").read_text(encoding="utf-8") == FIXED


def test_restart_and_cancellation_keep_the_revision_law(converter):
    from core.code_assistant.task_runtime import code_task_runtime

    task = _reproduced(converter, "restart-cancel")
    task.repair(WRONG_1)
    assert task.check(FOCUSED_CMD).details["tool_result"]["success"] is False
    code_task_runtime().reset()
    assert task.identify("the current failure survives a restart").ok
    task.repair(FIXED)
    code_task_runtime().reset()
    assert task.identify("the superseded failure does not").status == "stage_violation"
    assert _door("code.task.cancel", {"task_id": task.id, "reason": "operator stopped"}, task.ctx).ok
    assert task.identify("after cancel").status == "cancelled"
    assert _history(task) == [("narrow_test", False, 1, True)]


def _install_legacy(label: str, scenario: str, tmp_path: Path):
    source = LEGACY / label / scenario
    recorded = json.loads((source / "journal.json").read_text(encoding="utf-8"))
    workspace = tmp_path / f"legacy-{label}-{scenario}"
    shutil.copytree(source / "workspace", workspace)
    _git(workspace, "init", "-q")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-q", "-m", "recorded")
    target = Path(os.environ["VOOL_CODE_TASK_DIR"])
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{recorded['task_id']}.json").write_text(
        json.dumps({**recorded, "workspace_root": str(workspace.resolve())}), encoding="utf-8")
    ctx = {"workspace": str(workspace), "workspace_root": str(workspace), "session_id": recorded["session_id"],
           "runtime_session_id": recorded["session_id"], "operating_mode": "auto"}
    return recorded, ctx


def test_a_recorded_revision_2_stale_failure_is_retired_by_migration(converter, tmp_path):
    recorded, ctx = _install_legacy("v2_e064", "stale_failure_after_corrected_mutation", tmp_path)
    assert recorded["narrow"]["success"] is False and recorded["stage"] == "narrow_test"
    refused = _door("code.task.identify", {"task_id": recorded["task_id"], "path": "math.js", "reason": "stale"}, ctx)
    assert refused.ok is False and refused.status == "stage_violation"
    assert _journal(recorded["task_id"])["narrow"]["success"] is False  # a refusal writes nothing back
    report = _door("code.task.report", {"task_id": recorded["task_id"]}, ctx).details  # a write persists schema 3
    assert report["tests"]["narrow"] is None and report["revision"] == 2
    migrated = _journal(recorded["task_id"])
    assert migrated["narrow"] is None and migrated["revision"] == 2
    assert [(v["success"], v["revision"]) for v in migrated["verifications"]] == [(False, 1)]
    check = _door("code.task.step", {"task_id": recorded["task_id"], "step_id": "verify-migrated",
                                     "intent": "workspace.run_tests", "arguments": {"command": "node check.js"}}, ctx)
    assert check.details["tool_result"]["success"] is True and check.details["stage"] == "cumulative"


def test_a_recorded_base_failure_of_the_current_bytes_still_reopens_review(converter, tmp_path):
    recorded, ctx = _install_legacy("v1_df49", "stale_failure_after_corrected_mutation", tmp_path)
    reopened = _door("code.task.identify", {"task_id": recorded["task_id"], "path": "math.js",
                                            "reason": "the recorded failure checked these bytes"}, ctx)
    assert reopened.ok, (reopened.status, reopened.response_text)
