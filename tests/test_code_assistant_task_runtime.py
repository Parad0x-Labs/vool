"""CodeTaskRuntime -- the coding-assistant kernel, driven through the production tool door.

Every call in this pack goes through ``core.runtime_execution_tools.execute_runtime_tool`` --
THE door every model, skill and plugin proposal crosses. Nothing here calls the task runtime
directly, so a green row is a row a served model could reach with the same intent/arguments.

Fixture: a disposable Git repository with one real defect (``calc.add`` subtracts) and one
real pytest that fails because of it. The root-cause workflow is enforced by the runtime:

    reproduce -> identify -> propose -> preview -> approve -> mutate -> narrow_test
              -> cumulative -> inspect_diff -> report
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import threading
from pathlib import Path

import pytest

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
TEST = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"


def _git(root: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "fx",
            "GIT_AUTHOR_EMAIL": "fx@local",
            "GIT_COMMITTER_NAME": "fx",
            "GIT_COMMITTER_EMAIL": "fx@local",
        },
    )
    assert out.returncode == 0, out.stderr
    return out.stdout


@pytest.fixture
def fixture_repo(tmp_path, monkeypatch):
    from core.mode_permission_policy import reset_mode_permission_state

    reset_mode_permission_state()
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    monkeypatch.setenv("VOOL_CODE_TASK_DIR", str(tmp_path / "code_tasks"))
    root = tmp_path / "repo"
    root.mkdir()
    (root / "calc.py").write_text(BUGGY, encoding="utf-8")
    (root / "test_calc.py").write_text(TEST, encoding="utf-8")
    (root / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "seed")
    yield root
    reset_mode_permission_state()


def _ctx(root: Path, session: str = "code-task-session", **extra) -> dict:
    ctx = {"workspace": str(root), "workspace_root": str(root), "session_id": session, "operating_mode": "auto"}
    ctx.update(extra)
    return ctx


def _door(intent: str, arguments: dict, ctx: dict):
    from core.runtime_execution_tools import execute_runtime_tool

    result = execute_runtime_tool(intent, arguments, source_context=ctx)
    assert result is not None, f"{intent} is not contracted at the production door"
    return result


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Contracts: the kernel is offered like every other tool, from the one registry
# ---------------------------------------------------------------------------


def test_code_task_intents_are_contracted_in_the_one_registry() -> None:
    from core.runtime_tool_contracts import runtime_tool_contract_map

    contracts = runtime_tool_contract_map()
    for intent in (
        "code.task.open",
        "code.task.step",
        "code.task.approve",
        "code.task.cancel",
        "code.task.report",
        "code.task.rollback",
    ):
        assert intent in contracts, intent
        assert contracts[intent].handler == "runtime"
        assert contracts[intent].json_schema.get("type") == "object", intent
    assert contracts["code.task.open"].read_only
    assert contracts["code.task.report"].read_only
    assert contracts["code.task.step"].side_effect_class == "workspace_write"


# ---------------------------------------------------------------------------
# The positive journey: find why the test fails, repair the root cause, prove it
# ---------------------------------------------------------------------------


def test_root_cause_journey_through_the_production_door(fixture_repo: Path) -> None:
    ctx = _ctx(fixture_repo)
    opened = _door("code.task.open", {"objective": "Find why test_calc fails and repair the root cause"}, ctx)
    assert opened.ok, opened.response_text
    task_id = opened.details["task_id"]
    assert opened.details["stage"] == "reproduce"
    assert [s["name"] for s in opened.details["plan"]][:3] == ["reproduce", "identify", "propose"]

    # A read executes without mutating anything, and is receipted as a step.
    before_hash = _sha(BUGGY)
    read = _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "s1", "intent": "workspace.read_file", "arguments": {"path": "calc.py"}},
        ctx,
    )
    assert read.ok, read.response_text
    assert read.details["executed"] is True
    assert read.details["tool_result"]["hash"] == before_hash
    assert (fixture_repo / "calc.py").read_text(encoding="utf-8") == BUGGY

    # Mutating before the failure is reproduced is refused by the runtime, not by the model.
    early = _door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "s2",
            "intent": "workspace.write_file",
            "arguments": {"path": "calc.py", "content": FIXED},
        },
        ctx,
    )
    assert early.ok is False
    assert early.status == "stage_violation"
    assert early.details["executed"] is False
    assert (fixture_repo / "calc.py").read_text(encoding="utf-8") == BUGGY

    # Reproduce: the focused test really runs and really fails.
    repro = _door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "s3",
            "intent": "workspace.run_tests",
            "arguments": {"command": "python -m pytest -q test_calc.py"},
        },
        ctx,
    )
    assert repro.details["executed"] is True
    assert repro.details["tool_result"]["success"] is False
    assert repro.details["stage"] == "identify"

    # Identify the owning defect with evidence (path + line), then propose the repair.
    ident = _door(
        "code.task.identify", {"task_id": task_id, "path": "calc.py", "line": 2, "reason": "add subtracts"}, ctx
    )
    assert ident.ok, ident.response_text
    assert ident.details["stage"] == "propose"
    proposal = _door(
        "code.task.propose",
        {
            "task_id": task_id,
            "proposal_id": "p1",
            "intent": "workspace.write_file",
            "arguments": {"path": "calc.py", "content": FIXED, "expected_hash": before_hash},
            "rationale": "Owner calc.py: add subtracts instead of adding.",
        },
        ctx,
    )
    assert proposal.ok, proposal.response_text
    assert proposal.details["stage"] == "approve"
    preview = proposal.details["preview"]
    assert preview["paths"] == ["calc.py"]
    assert preview["content_sha256"] == _sha(FIXED)
    assert (fixture_repo / "calc.py").read_text(encoding="utf-8") == BUGGY  # preview never mutates

    # A mutation whose bytes differ from the approved preview is refused.
    approved = _door("code.task.approve", {"task_id": task_id, "proposal_id": "p1"}, ctx)
    assert approved.ok, approved.response_text
    assert approved.details["stage"] == "mutate"
    drift = _door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "s4",
            "intent": "workspace.write_file",
            "arguments": {"path": "calc.py", "content": FIXED + "# drift\n"},
        },
        ctx,
    )
    assert drift.status == "approval_mismatch"
    assert drift.details["executed"] is False
    assert (fixture_repo / "calc.py").read_text(encoding="utf-8") == BUGGY

    mutate = _door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "s5",
            "intent": "workspace.write_file",
            "arguments": {"path": "calc.py", "content": FIXED, "expected_hash": before_hash},
        },
        ctx,
    )
    assert mutate.ok, mutate.response_text
    assert mutate.details["executed"] is True
    assert mutate.details["bytes_match_approved_patch"] is True
    assert (fixture_repo / "calc.py").read_text(encoding="utf-8") == FIXED
    assert mutate.details["stage"] == "narrow_test"
    assert mutate.details["tool_result"]["blackbox"]["terminal_recorded"] is True

    # The report refuses to call it done while verification is missing.
    premature = _door("code.task.report", {"task_id": task_id}, ctx)
    assert premature.details["verdict"] == "unresolved"
    assert "narrow_test" in premature.details["unresolved"]

    narrow = _door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "s6",
            "intent": "workspace.run_tests",
            "arguments": {"command": "python -m pytest -q test_calc.py"},
        },
        ctx,
    )
    assert narrow.details["tool_result"]["success"] is True, narrow.details["tool_result"]
    assert narrow.details["stage"] == "cumulative"
    cumulative = _door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "s7",
            "intent": "workspace.run_tests",
            "arguments": {"command": "python -m pytest -q"},
        },
        ctx,
    )
    assert cumulative.details["tool_result"]["success"] is True
    assert cumulative.details["stage"] == "inspect_diff"
    diff = _door(
        "code.task.step", {"task_id": task_id, "step_id": "s8", "intent": "workspace.git_diff", "arguments": {}}, ctx
    )
    assert diff.ok, diff.response_text
    assert diff.details["stage"] == "report"

    report = _door("code.task.report", {"task_id": task_id}, ctx)
    assert report.ok, report.response_text
    ev = report.details
    assert ev["verdict"] == "completed"
    assert ev["files_changed"] == ["calc.py"]
    assert ev["git_diff_paths"] == ["calc.py"]
    assert [c["intent"] for c in ev["commands_run"]] == ["workspace.run_tests"] * 3
    assert ev["tests"]["reproduced_failure"] is True
    assert ev["tests"]["narrow"]["success"] is True and ev["tests"]["cumulative"]["success"] is True
    assert ev["refused"] and {r["step_id"] for r in ev["refused"]} == {"s2", "s4"}
    assert ev["completed_step_ids"] == ["s1", "s3", "s5", "s6", "s7", "s8"]
    assert ev["receipts"]["count"] >= 1
    assert ev["rollback"]["available"] is True and ev["rollback"]["turns"] >= 1
    assert ev["demand"] == "Find why test_calc fails and repair the root cause"
    from core.tool_offer_assembly import assemble_tool_offer

    offer = assemble_tool_offer(user_text="Repair another failing test.", task_class="debugging", source_context=ctx)
    assert "code.task.open" in offer.intents, "A completed repair must allow new work in the same session."
    assert "code.task.pr_description" in offer.intents
    assert len(offer.intents) <= 8



# ---------------------------------------------------------------------------
# Task control: cancel stops future effects, retry never duplicates a completed effect
# ---------------------------------------------------------------------------


def test_cancel_stops_future_effects_and_retry_replays_completed_steps(fixture_repo: Path) -> None:
    ctx = _ctx(fixture_repo)
    task_id = _door("code.task.open", {"objective": "x"}, ctx).details["task_id"]
    _door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "r0",
            "intent": "workspace.run_tests",
            "arguments": {"command": "python -m pytest -q test_calc.py"},
        },
        ctx,
    )
    _door("code.task.identify", {"task_id": task_id, "path": "calc.py", "line": 2, "reason": "x"}, ctx)
    _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "read", "intent": "workspace.read_file", "arguments": {"path": "calc.py"}},
        ctx,
    )
    _door(
        "code.task.propose",
        {
            "task_id": task_id,
            "proposal_id": "p",
            "intent": "workspace.write_file",
            "arguments": {"path": "calc.py", "content": FIXED},
            "rationale": "Owner calc.py: add subtracts instead of adding.",
        },
        ctx,
    )
    _door("code.task.approve", {"task_id": task_id, "proposal_id": "p"}, ctx)
    first = _door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "r1",
            "intent": "workspace.write_file",
            "arguments": {"path": "calc.py", "content": FIXED},
        },
        ctx,
    )
    assert first.ok and first.details["executed"] is True, first.response_text
    assert (fixture_repo / "calc.py").read_text(encoding="utf-8") == FIXED
    (fixture_repo / "calc.py").unlink()
    # Same step id again: replayed, NOT re-executed -- the file is not written a second time.
    again = _door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "r1",
            "intent": "workspace.write_file",
            "arguments": {"path": "calc.py", "content": FIXED},
        },
        ctx,
    )
    assert again.ok and again.details["executed"] is False and again.details["replayed"] is True
    assert not (fixture_repo / "calc.py").exists()
    (fixture_repo / "calc.py").write_text(FIXED, encoding="utf-8")

    cancelled = _door("code.task.cancel", {"task_id": task_id, "reason": "operator"}, ctx)
    assert cancelled.ok and cancelled.details["stage"] == "cancelled"
    after = _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "r2", "intent": "workspace.read_file", "arguments": {"path": "calc.py"}},
        ctx,
    )
    assert after.status == "cancelled" and after.details["executed"] is False
    assert "tool_result" in after.details and after.details["tool_result"] == {}
    late = _door(
        "code.task.propose",
        {
            "task_id": task_id,
            "proposal_id": "q",
            "intent": "workspace.write_file",
            "arguments": {"path": "calc.py", "content": BUGGY},
        },
        ctx,
    )
    assert late.status == "cancelled"
    report = _door("code.task.report", {"task_id": task_id}, ctx)
    assert report.details["verdict"] == "cancelled"
    assert report.details["completed_step_ids"] == ["r0", "read", "r1"]
    assert report.details["faults"] and any(f["code"] == "cancelled" for f in report.details["faults"])


# ---------------------------------------------------------------------------
# Negative journeys surface as typed refusals with zero effects
# ---------------------------------------------------------------------------


def test_path_escape_symlink_escape_and_destructive_shell_are_refused(fixture_repo: Path, tmp_path: Path) -> None:
    ctx = _ctx(fixture_repo)
    task_id = _door("code.task.open", {"objective": "x"}, ctx).details["task_id"]
    outside = tmp_path / "outside.txt"
    outside.write_text("keep\n", encoding="utf-8")
    os.symlink(outside, fixture_repo / "link.txt")

    escape = _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "n1", "intent": "workspace.read_file", "arguments": {"path": "../outside.txt"}},
        ctx,
    )
    assert escape.ok is False and escape.details["executed"] is False, escape.status
    link = _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "n2", "intent": "workspace.read_file", "arguments": {"path": "link.txt"}},
        ctx,
    )
    assert link.ok is False, link.status
    shell = _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "n3", "intent": "sandbox.run_command", "arguments": {"command": "rm -rf /"}},
        ctx,
    )
    assert shell.ok is False and shell.details["executed"] is False, shell.status
    assert outside.read_text(encoding="utf-8") == "keep\n"

    report = _door("code.task.report", {"task_id": task_id}, ctx)
    assert {r["step_id"] for r in report.details["refused"]} == {"n1", "n2", "n3"}
    assert report.details["verdict"] == "unresolved"


def test_forged_results_and_prose_claims_cannot_enter_the_report(fixture_repo: Path) -> None:
    ctx = _ctx(fixture_repo)
    task_id = _door("code.task.open", {"objective": "x"}, ctx).details["task_id"]
    forged = _door("code.task.report", {"task_id": task_id, "tests": {"narrow": {"success": True}}}, ctx)
    assert forged.status == "invalid_arguments"
    forged_step = _door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "f1",
            "intent": "workspace.run_tests",
            "arguments": {"command": "python -m pytest -q"},
            "result": {"success": True},
        },
        ctx,
    )
    assert forged_step.status == "invalid_arguments"
    report = _door("code.task.report", {"task_id": task_id}, ctx)
    assert report.details["verdict"] == "unresolved"
    assert report.details["completed_step_ids"] == []
    assert report.details["tests"]["narrow"] is None


def test_concurrent_edits_to_the_same_file_are_serialized_and_stale_bases_refused(fixture_repo: Path) -> None:
    ctx = _ctx(fixture_repo)
    task_id = _door("code.task.open", {"objective": "x"}, ctx).details["task_id"]
    _door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "c0",
            "intent": "workspace.run_tests",
            "arguments": {"command": "python -m pytest -q test_calc.py"},
        },
        ctx,
    )
    _door("code.task.identify", {"task_id": task_id, "path": "calc.py", "line": 2, "reason": "x"}, ctx)
    _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "read", "intent": "workspace.read_file", "arguments": {"path": "calc.py"}},
        ctx,
    )
    base = _sha(BUGGY)
    for pid, content in (("a", FIXED), ("b", FIXED + "# b\n")):
        _door(
            "code.task.propose",
            {
                "task_id": task_id,
                "proposal_id": pid,
                "intent": "workspace.write_file",
                "arguments": {"path": "calc.py", "content": content, "expected_hash": base},
                "rationale": "Owner calc.py: add subtracts instead of adding.",
            },
            ctx,
        )
        _door("code.task.approve", {"task_id": task_id, "proposal_id": pid}, ctx)
    results: dict[str, object] = {}

    def run(step_id: str, content: str) -> None:
        results[step_id] = _door(
            "code.task.step",
            {
                "task_id": task_id,
                "step_id": step_id,
                "intent": "workspace.write_file",
                "arguments": {"path": "calc.py", "content": content, "expected_hash": base},
            },
            ctx,
        )

    threads = [
        threading.Thread(target=run, args=("w1", FIXED)),
        threading.Thread(target=run, args=("w2", FIXED + "# b\n")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    statuses = sorted(r.status for r in results.values())  # type: ignore[union-attr]
    assert sum(1 for r in results.values() if r.ok) == 1, statuses  # type: ignore[union-attr]
    assert "stale_base" in statuses, statuses
    final = (fixture_repo / "calc.py").read_text(encoding="utf-8")
    assert final in (FIXED, FIXED + "# b\n")


def test_blackbox_restores_exact_pre_task_bytes(fixture_repo: Path) -> None:
    ctx = _ctx(fixture_repo)
    task_id = _door("code.task.open", {"objective": "x"}, ctx).details["task_id"]
    _door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "b0",
            "intent": "workspace.run_tests",
            "arguments": {"command": "python -m pytest -q test_calc.py"},
        },
        ctx,
    )
    _door("code.task.identify", {"task_id": task_id, "path": "calc.py", "line": 2, "reason": "x"}, ctx)
    _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "read", "intent": "workspace.read_file", "arguments": {"path": "calc.py"}},
        ctx,
    )
    _door(
        "code.task.propose",
        {
            "task_id": task_id,
            "proposal_id": "p",
            "intent": "workspace.write_file",
            "arguments": {"path": "calc.py", "content": FIXED},
            "rationale": "Owner calc.py: add subtracts instead of adding.",
        },
        ctx,
    )
    _door("code.task.approve", {"task_id": task_id, "proposal_id": "p"}, ctx)
    mutate = _door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "b1",
            "intent": "workspace.write_file",
            "arguments": {"path": "calc.py", "content": FIXED},
        },
        ctx,
    )
    assert mutate.ok, mutate.response_text
    assert (fixture_repo / "calc.py").read_bytes() == FIXED.encode("utf-8")
    # The inner Blackbox effect is delete-class; the task mints its own bounded internal
    # authority for it (the same scope the operator CLI mints, task/session/workspace/intent-bound,
    # expiring, revoked on return) once the outer rollback call itself has been admitted. The
    # SERVED gate for the outer call is proven in tests/test_code_assistant_served_gaps.py.
    rolled = _door("code.task.rollback", {"task_id": task_id}, ctx)
    assert rolled.ok, rolled.response_text
    assert (fixture_repo / "calc.py").read_bytes() == BUGGY.encode("utf-8")
    assert rolled.details["restored_paths"] == ["calc.py"]
    report = _door("code.task.report", {"task_id": task_id}, ctx)
    assert report.details["rollback"]["restored"] is True
    assert report.details["verdict"] == "rolled_back"


def test_failed_test_after_mutation_holds_the_stage_and_reports_unresolved(fixture_repo: Path) -> None:
    ctx = _ctx(fixture_repo)
    task_id = _door("code.task.open", {"objective": "x"}, ctx).details["task_id"]
    _door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "f0",
            "intent": "workspace.run_tests",
            "arguments": {"command": "python -m pytest -q test_calc.py"},
        },
        ctx,
    )
    _door("code.task.identify", {"task_id": task_id, "path": "calc.py", "line": 2, "reason": "x"}, ctx)
    _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "read", "intent": "workspace.read_file", "arguments": {"path": "calc.py"}},
        ctx,
    )
    wrong = "def add(a, b):\n    return a * b\n"
    _door(
        "code.task.propose",
        {
            "task_id": task_id,
            "proposal_id": "p",
            "intent": "workspace.write_file",
            "arguments": {"path": "calc.py", "content": wrong},
            "rationale": "Owner calc.py: add subtracts instead of adding.",
        },
        ctx,
    )
    _door("code.task.approve", {"task_id": task_id, "proposal_id": "p"}, ctx)
    mutate = _door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "f1",
            "intent": "workspace.write_file",
            "arguments": {"path": "calc.py", "content": wrong},
        },
        ctx,
    )
    assert mutate.ok and mutate.details["stage"] == "narrow_test"
    narrow = _door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "f2",
            "intent": "workspace.run_tests",
            "arguments": {"command": "python -m pytest -q test_calc.py"},
        },
        ctx,
    )
    assert narrow.details["executed"] is True and narrow.details["tool_result"]["success"] is False
    assert narrow.details["stage"] == "narrow_test"  # a failing narrow test does not advance
    report = _door("code.task.report", {"task_id": task_id}, ctx)
    assert report.details["verdict"] == "unresolved"
    assert report.details["tests"]["narrow"]["success"] is False
    assert report.details["files_changed"] == ["calc.py"]
    assert report.details["unresolved"][0] == "narrow_test"


def test_task_resumes_from_the_journal_after_a_cold_restart(fixture_repo: Path) -> None:
    from core.code_assistant.task_runtime import code_task_runtime

    ctx = _ctx(fixture_repo)
    task_id = _door("code.task.open", {"objective": "resume me"}, ctx).details["task_id"]
    _door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "q0",
            "intent": "workspace.run_tests",
            "arguments": {"command": "python -m pytest -q test_calc.py"},
        },
        ctx,
    )
    _door("code.task.identify", {"task_id": task_id, "path": "calc.py", "line": 2, "reason": "x"}, ctx)
    _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "read", "intent": "workspace.read_file", "arguments": {"path": "calc.py"}},
        ctx,
    )
    _door(
        "code.task.propose",
        {
            "task_id": task_id,
            "proposal_id": "p",
            "intent": "workspace.write_file",
            "arguments": {"path": "calc.py", "content": FIXED},
            "rationale": "Owner calc.py: add subtracts instead of adding.",
        },
        ctx,
    )
    code_task_runtime().reset()  # the process forgets everything it held in memory
    approved = _door("code.task.approve", {"task_id": task_id, "proposal_id": "p"}, ctx)
    assert approved.ok and approved.details["stage"] == "mutate", approved.response_text
    replay = _door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "q0",
            "intent": "workspace.run_tests",
            "arguments": {"command": "python -m pytest -q test_calc.py"},
        },
        ctx,
    )
    assert replay.details["replayed"] is True and replay.details["tool_result"]["success"] is False
    mutate = _door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "q1",
            "intent": "workspace.write_file",
            "arguments": {"path": "calc.py", "content": FIXED},
        },
        ctx,
    )
    assert mutate.ok and mutate.details["bytes_match_approved_patch"] is True
    report = _door("code.task.report", {"task_id": task_id}, ctx)
    assert report.details["demand"] == "resume me"
    assert report.details["completed_step_ids"] == ["q0", "read", "q1"]


def test_open_task_seats_its_stage_control_plane_in_the_offer(fixture_repo: Path) -> None:
    """Offered by task capability: while a code task is open in the session, the offer seats the
    control-plane tools its CURRENT stage needs as explicit seats (they cannot be evicted by the
    family ranking); once the task is cancelled, nothing is seated."""
    from core.tool_offer_assembly import assemble_tool_offer

    ctx = _ctx(fixture_repo, session="offer-session")
    before = assemble_tool_offer(family_hint="workspace", user_text="reproduce it", source_context=dict(ctx))
    assert "code.task.step" not in before.intents
    task_id = _door("code.task.open", {"objective": "x"}, ctx).details["task_id"]
    during = assemble_tool_offer(family_hint="workspace", user_text="reproduce it", source_context=dict(ctx))
    assert {"code.task.step", "code.task.report", "code.task.cancel"} <= set(during.intents), during.intents
    assert len(during.intents) <= 8
    other = assemble_tool_offer(
        family_hint="workspace", user_text="reproduce it", source_context={**ctx, "session_id": "someone-else"}
    )
    assert "code.task.step" not in other.intents
    _door("code.task.cancel", {"task_id": task_id}, ctx)
    after = assemble_tool_offer(family_hint="workspace", user_text="reproduce it", source_context=dict(ctx))
    assert "code.task.step" not in after.intents


# ---------------------------------------------------------------------------
# Session isolation: a task belongs to the session that opened it
# ---------------------------------------------------------------------------


def test_concurrent_sessions_are_isolated_from_each_others_tasks(fixture_repo: Path) -> None:
    ctx_a = _ctx(fixture_repo, session="session-a")
    ctx_b = _ctx(fixture_repo, session="session-b")
    task_id = _door("code.task.open", {"objective": "a's task"}, ctx_a).details["task_id"]

    for intent, arguments in (
        ("code.task.step", {"task_id": task_id, "step_id": "x1", "intent": "workspace.read_file", "arguments": {"path": "calc.py"}}),
        ("code.task.identify", {"task_id": task_id, "path": "calc.py", "line": 2}),
        ("code.task.approve", {"task_id": task_id, "proposal_id": "p"}),
        ("code.task.cancel", {"task_id": task_id}),
        ("code.task.rollback", {"task_id": task_id}),
        ("code.task.report", {"task_id": task_id}),
    ):
        refused = _door(intent, arguments, ctx_b)
        assert refused.ok is False and refused.status == "not_your_task", (intent, refused.status)
        assert refused.details["executed"] is False
    # Nothing B did landed: A's task is still open at reproduce with no steps.
    task = _door("code.task.report", {"task_id": task_id}, ctx_a)
    assert task.details["verdict"] == "unresolved"
    assert task.details["completed_step_ids"] == []
    # And B's offer seats nothing of A's task.
    from core.tool_offer_assembly import assemble_tool_offer

    offer_b = assemble_tool_offer(family_hint="workspace", user_text="continue", source_context=dict(ctx_b))
    assert not any(i.startswith("code.task.") for i in offer_b.intents), offer_b.intents
    # An anonymous caller (no session at all) is refused too: isolation fails closed.
    anonymous = _door("code.task.report", {"task_id": task_id}, {"workspace": str(fixture_repo)})
    assert anonymous.status == "not_your_task"


# ---------------------------------------------------------------------------
# Cancellation reaches INTO a running command and records terminal truth
# ---------------------------------------------------------------------------


def test_cancel_interrupts_a_running_command_and_records_terminal_truth(fixture_repo: Path) -> None:
    import threading
    import time as _time

    ctx = _ctx(fixture_repo)
    task_id = _door("code.task.open", {"objective": "x"}, ctx).details["task_id"]
    marker = fixture_repo / "completed.marker"
    # A command that would, if allowed to finish, leave a marker on disk. The sandbox forbids
    # compound shell syntax, so the sleep lives in a repo script rather than a `-c` one-liner.
    (fixture_repo / "slow_runner.py").write_text(
        "import time\n\ntime.sleep(30)\nopen('completed.marker', 'w').write('done')\n",
        encoding="utf-8",
    )
    long_command = "python3 slow_runner.py"
    outcomes: dict[str, object] = {}

    def run() -> None:
        outcomes["step"] = _door(
            "code.task.step",
            {
                "task_id": task_id,
                "step_id": "long",
                "intent": "sandbox.run_command",
                "arguments": {"command": long_command},
            },
            ctx,
        )

    thread = threading.Thread(target=run)
    thread.start()
    started = _time.monotonic()
    from core.code_assistant.task_runtime import code_task_runtime

    while _time.monotonic() - started < 30:
        journal = code_task_runtime()._load(task_id)
        step = journal.steps.get("long")
        if step is not None and step.status == "in_flight":
            break
        _time.sleep(0.2)
    assert step is not None and step.status == "in_flight", "the command never started running"
    # Cancel while the command is IN FLIGHT -- not between commands.
    cancelled = _door("code.task.cancel", {"task_id": task_id, "reason": "operator pressed stop"}, ctx)
    assert cancelled.ok, cancelled.response_text
    thread.join(timeout=60)
    assert not thread.is_alive()
    assert _time.monotonic() - started < 25, "the running command was not interrupted"
    assert not marker.exists(), "the interrupted command ran to completion"

    step = outcomes["step"]
    assert step.details["executed"] is True, step.status  # the command physically ran
    assert step.ok is False  # ...and did not complete
    assert step.details["tool_result"]["status"] == "cancelled"
    task = _door("code.task.report", {"task_id": task_id}, ctx)
    assert task.details["verdict"] == "cancelled"
    journal = code_task_runtime()._load(task_id)
    assert journal.stage == "cancelled"
    assert any(f["code"] == "cancelled" for f in task.details["faults"])
    # Terminal: nothing further executes.
    late = _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "late", "intent": "workspace.read_file", "arguments": {"path": "calc.py"}},
        ctx,
    )
    assert late.status == "cancelled" and late.details["executed"] is False


def test_active_code_task_offer_cannot_reopen_the_same_work(fixture_repo, monkeypatch):
    from core.tool_offer_assembly import assemble_tool_offer

    ctx = _ctx(fixture_repo)
    prompt = "Repair calc.py and run its failing tests."
    opened = _door("code.task.open", {"objective": prompt}, ctx)
    offer = assemble_tool_offer(user_text=prompt, task_class="debugging", source_context=ctx)
    assert "code.task.step" in offer.intents
    assert "code.task.open" not in offer.intents
    from core.tool_offer_assembly import SkillGuidance
    with monkeypatch.context() as patch:
        patch.setattr("core.tool_offer_assembly.skill_guidance_for",
                      lambda *a, **kw: SkillGuidance(allowed_tools=("respond.direct",)))
        narrowed = assemble_tool_offer(user_text=prompt, task_class="debugging", source_context=ctx)
        assert {"code.task.step", "code.task.report", "code.task.cancel"} <= set(narrowed.intents)
    _door("code.task.cancel", {"task_id": opened.details["task_id"], "reason": "finished inspection"}, ctx)
    assert "code.task.open" in assemble_tool_offer(user_text=prompt, task_class="debugging", source_context=ctx).intents


@pytest.mark.parametrize("changed_intent,changed_arguments", [
    ("workspace.write_file", {"path": "calc.py", "content": FIXED}),
    ("workspace.read_file", {"path": "README.md"}),
])
def test_reused_step_id_cannot_replay_evidence_for_a_different_action(fixture_repo, changed_intent, changed_arguments):
    ctx = _ctx(fixture_repo)
    task_id = _door("code.task.open", {"objective": "Inspect and repair the project"}, ctx).details["task_id"]
    original = {"task_id": task_id, "step_id": "inspect-source", "intent": "workspace.read_file", "arguments": {"path": "calc.py"}}
    first = _door("code.task.step", original, ctx)
    assert first.ok and first.details["executed"]
    changed = _door("code.task.step", {
        **original, "intent": changed_intent, "arguments": changed_arguments,
    }, ctx)
    assert not changed.ok
    assert changed.status == "step_id_conflict"
    assert changed.details["executed"] is False
    assert not changed.details.get("replayed")
    assert "new step_id" in changed.response_text
    assert (fixture_repo / "calc.py").read_text() == BUGGY
    # A conflicting call cannot replace the original record or poison an exact retry.
    replay = _door("code.task.step", original, ctx)
    assert replay.ok and replay.details["replayed"] and not replay.details["executed"]
    assert replay.details["tool_result"] == first.details["tool_result"]
    different_read = _door("code.task.step", {
        **original, "step_id": "inspect-readme", "arguments": {"path": "README.md"},
    }, ctx)
    assert different_read.ok and different_read.details["executed"]
    assert different_read.details["tool_result"]["path"] == "README.md"
