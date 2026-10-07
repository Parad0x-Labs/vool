#!/usr/bin/env python
"""Reviewer demo A: a small code fix done with VOOL's real workspace tools.

A throwaway project has a failing test. The demo drives the same tool executor a chat turn
uses, in Auto mode: search the code, read the file, edit it, and run the tests. The model's
part (deciding what to change) is scripted, so the demo needs no model, account or network.
What it proves is the tool path: the tools act inside the workspace, the permission check runs
on every call, and the edit is real (the test goes from failing to passing).

Run from the repository root:  python scripts/review/demo_a_code_fix.py
"""

from __future__ import annotations

import subprocess
import sys

import _common
from _common import Checks, new_workspace, run_tool, show

BUGGY = "def average(values):\n    return sum(values) / (len(values) + 1)\n"
TEST = "from stats import average\n\n\ndef test_average():\n    assert average([2, 4, 6]) == 4\n"


def pytest_passes(root) -> bool:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_stats.py"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def main() -> int:
    from core.mode_permission_policy import reset_mode_permission_state, set_active_mode

    checks = Checks()
    ws = new_workspace("code-fix")
    (ws / "stats.py").write_text(BUGGY, encoding="utf-8")
    (ws / "test_stats.py").write_text(TEST, encoding="utf-8")
    print(f"Workspace: {ws}  (VOOL_HOME={_common.DEMO_HOME})\n")

    checks.check(not pytest_passes(ws), "before: the project's test fails")

    reset_mode_permission_state()
    session = "review-demo-a"
    set_active_mode(session, "auto", client_turn_id="turn-1")
    ctx = {"workspace_root": str(ws), "runtime_session_id": session, "cancel_turn_id": "turn-1"}

    print("\n1. workspace.search_text  'len(values)'")
    found = run_tool("workspace.search_text", {"query": "len(values)"}, context=ctx, task_id="turn-1")
    show("search", found)
    checks.check(found.ok and "stats.py" in (found.response_text or ""), "search finds stats.py")

    print("\n2. workspace.read_file  stats.py")
    read = run_tool("workspace.read_file", {"path": "stats.py"}, context=ctx, task_id="turn-1")
    show("read", read)
    checks.check(read.ok and "len(values) + 1" in (read.response_text or ""), "read returns the buggy line")

    print("\n3. workspace.replace_in_file  fix the off-by-one")
    edit = run_tool(
        "workspace.replace_in_file",
        {"path": "stats.py", "old_text": "(len(values) + 1)", "new_text": "len(values)"},
        context=ctx,
        task_id="turn-1",
    )
    show("edit", edit)
    checks.check(edit.ok, "Auto mode lets an in-workspace edit run")
    checks.check("len(values) + 1" not in (ws / "stats.py").read_text(encoding="utf-8"), "the file on disk changed")

    print("\n4. workspace.run_tests")
    tests = run_tool(
        "workspace.run_tests",
        {"command": "python -m pytest -q -p no:cacheprovider test_stats.py"},
        context=ctx,
        task_id="turn-1",
    )
    show("tests", tests)
    print("    " + (tests.response_text or "").strip().replace("\n", "\n    ")[:600])
    if tests.status == "sandbox_unavailable":
        # Command tools refuse to run without OS-level confinement (bwrap on Linux,
        # sandbox-exec on macOS). That refusal is the designed behavior, not a demo failure.
        print("  [NOT RUN] workspace.run_tests: no usable sandbox backend on this machine (fails closed)")
    else:
        checks.check(tests.ok, "workspace.run_tests ran inside the sandbox and passed")

    checks.check(pytest_passes(ws), "after: the test passes when re-run independently")
    return checks.finish("Demo A (code fix)")


if __name__ == "__main__":
    sys.exit(main())
