#!/usr/bin/env python3
"""30-SECOND WOW DEMO — "Toolbelt, prepare this machine for Git Ninja."

Read-only. No mutations, no pushes, no installs, no secrets printed.
Run with the project venv: .venv/bin/python demo/toolbelt_git_ninja_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from core.toolbelt.inventory import ProbeRunner, build_inventory, resolve_runtime  # noqa: E402
from core.toolbelt.models import CapabilityNeed, RepoRef  # noqa: E402
from core.toolbelt.resolve import resolve  # noqa: E402


def main() -> int:
    print("TOOLBELT NINJA — prepare this machine for Git Ninja")
    print(f"repo: {REPO}\n")

    runner = ProbeRunner()
    runtime = resolve_runtime(REPO, runner)
    inventory = build_inventory(REPO, runner)
    print(f"RUNTIME: {runtime.kind} -> {runtime.version} ({runtime.reason})")

    inventory = build_inventory(REPO, runner)
    interesting = ("git", "gh", "python3", "pytest", "ruff", "docker")
    print("\nTOOL REGISTRY:")
    for tc in inventory:
        if tc.tool_id in interesting:
            p = tc.presence
            line = f"  {tc.tool_id:<8} {p.presence.value}"
            if p.version:
                line += f" {p.version}"
            line += f" [{p.scope.value}]"
            if p.notes:
                line += f"  ({p.notes[0][:60]})"
            print(line)

    needs = [
        CapabilityNeed("github.repo.read", RepoRef(str(REPO))),
        CapabilityNeed("python.test.pytest", RepoRef(str(REPO))),
        CapabilityNeed("git.commit.sign"),
        CapabilityNeed("github.push"),           # implementation only; authorization NOT ours
        CapabilityNeed("fakectl.warp-drive"),    # deliberately missing tool
    ]
    print("\nCAPABILITY PLAN:")
    for need in needs:
        plan = resolve(need)
        line = f"  {need.capability:<20} {plan.status.value}"
        if plan.identity:
            line += f"  identity={plan.identity}"
        if plan.install_plan:
            line += f"\n      install proposal (NOT executed): {plan.install_plan.command_hint}"
        for b in plan.blockers[:1]:
            line += f"\n      blocker: {b[:100]}"
        print(line)

    print(f"\nAUTHORIZATION: {plan.authorization}")
    print("SECRETS EXPOSED TO MODEL: NO — handles only; see tests/test_toolbelt.py leak canary")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
