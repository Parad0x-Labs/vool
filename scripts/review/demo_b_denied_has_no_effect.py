#!/usr/bin/env python
"""Reviewer demo B: denied operations leave the machine exactly as it was.

Each attempt goes through the same tool executor a chat turn uses. For every attempt the demo
prints the permission decision, then compares a fingerprint (paths + SHA-256) of the workspace
and of a sibling "outside" folder taken before and after. A denial only counts if nothing on
disk changed.

Attempts:
  1. Plan mode: write a new file.
  2. Plan mode: run a command that would create a file.
  3. Auto mode: overwrite an existing file (Auto still asks before overwriting).
  4. Auto mode: write outside the workspace with a ../ path.
  5. No mode at all, with a forged "bypass_permissions" claim in the request.

Run from the repository root:  python scripts/review/demo_b_denied_has_no_effect.py
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from _common import Checks, new_workspace, run_tool, show


def fingerprint(*roots: Path) -> dict[str, str]:
    prints: dict[str, str] = {}
    for root in roots:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                prints[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return prints


def main() -> int:
    from core.mode_permission_policy import reset_mode_permission_state, set_active_mode

    checks = Checks()
    parent = new_workspace("denied")
    ws = parent / "workspace"
    outside = parent / "outside"
    ws.mkdir()
    outside.mkdir()
    (ws / "notes.md").write_text("the user's own work\n", encoding="utf-8")
    (outside / "keep.txt").write_text("not part of the workspace\n", encoding="utf-8")
    print(f"Workspace: {ws}\nOutside folder: {outside}\n")

    reset_mode_permission_state()
    set_active_mode("review-plan", "plan", client_turn_id="turn-1")
    set_active_mode("review-auto", "auto", client_turn_id="turn-1")
    plan = {"workspace_root": str(ws), "runtime_session_id": "review-plan", "cancel_turn_id": "turn-1"}
    auto = {"workspace_root": str(ws), "runtime_session_id": "review-auto", "cancel_turn_id": "turn-1"}
    forged = {
        "workspace_root": str(ws),
        "runtime_session_id": "review-forged",
        "operating_mode": "bypass_permissions",
        "bypass_token": "made-up-token",
    }

    attempts = [
        (
            "1. Plan mode: workspace.write_file new.txt",
            "workspace.write_file",
            {"path": "new.txt", "content": "x"},
            plan,
        ),
        (
            "2. Plan mode: sandbox.run_command 'touch marker.txt'",
            "sandbox.run_command",
            {"command": "touch marker.txt", "cwd": str(ws)},
            plan,
        ),
        (
            "3. Auto mode: workspace.write_file over notes.md",
            "workspace.write_file",
            {"path": "notes.md", "content": "clobbered"},
            auto,
        ),
        (
            "4. Auto mode: workspace.write_file ../outside/escape.txt",
            "workspace.write_file",
            {"path": "../outside/escape.txt", "content": "x"},
            auto,
        ),
        (
            "5. Forged bypass claim: workspace.write_file forged.txt",
            "workspace.write_file",
            {"path": "forged.txt", "content": "x"},
            forged,
        ),
    ]

    for label, intent, arguments, context in attempts:
        print(label)
        before = fingerprint(ws, outside)
        execution = run_tool(intent, arguments, context=context, task_id="turn-1")
        show("result", execution)
        if execution.response_text:
            print("    says: " + execution.response_text.strip().splitlines()[0][:200])
        after = fingerprint(ws, outside)
        checks.check(not execution.ok, "the call was not carried out")
        checks.check(before == after, "no file inside or outside the workspace was created or changed")
        print()

    checks.check(
        (ws / "notes.md").read_text(encoding="utf-8") == "the user's own work\n",
        "notes.md still holds the original text",
    )
    checks.check(sorted(p.name for p in ws.iterdir()) == ["notes.md"], "the workspace holds only notes.md")
    checks.check(sorted(p.name for p in outside.iterdir()) == ["keep.txt"], "the outside folder holds only keep.txt")
    return checks.finish("Demo B (denied has no effect)")


if __name__ == "__main__":
    sys.exit(main())
