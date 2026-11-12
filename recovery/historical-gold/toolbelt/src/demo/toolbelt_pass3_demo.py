#!/usr/bin/env python3
"""Pass #3 DEMO — layered executable integrity + operation-specific capability.

Act 1: same-path sabotage — bytes replaced, version output identical; the old
       plan does NOT stay READY (fast fingerprint catches it).
Act 2: layered Git Ninja handoff for github.branch.push — role vs operation
       evidence vs platform authorization, never flattened to one boolean.
Act 3: force-push distinction + TOCTOU remainder stated honestly.

Read-only. No pushes. No secrets.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from core.toolbelt.evidence import DriftKind, ToolPlanRef, revalidate  # noqa: E402
from core.toolbelt.models import CapabilityNeed  # noqa: E402
from core.toolbelt.operations import OperationStatus, preflight  # noqa: E402
from core.toolbelt.probes import ProbeResult  # noqa: E402
from core.toolbelt.resolve import resolve  # noqa: E402

TMP = Path("/tmp/toolbelt_pass3_demo")


class FakeRunner:
    def __init__(self, which_map=None, run_map=None):
        self.which_map = which_map or {}
        self.run_map = run_map or {}

    def which(self, name):
        return self.which_map.get(name)

    def run(self, argv, *, cwd=None):
        if tuple(argv[:2]) in self.run_map:
            return self.run_map[tuple(argv[:2])]
        return ProbeResult(127, "", "nf")


def act1() -> None:
    print("ACT 1 — same-path replacement sabotage")
    TMP.mkdir(exist_ok=True)
    tool = TMP / "git"
    tool.write_text("#!/bin/sh\necho v1-bytes\n")
    tool.chmod(0o755)
    runner = FakeRunner({tool.name: str(tool)},
                        {("git", "--version"): ProbeResult(0, "git version 2.50.1", "")})
    plan = resolve(CapabilityNeed(capability="git.commit"), runner)
    ref, facts = ToolPlanRef.freeze(plan, runner)
    print(f"  frozen: plan={ref.plan_id}  exec facts={[f.key() for f in facts if f.subject.startswith('execid')][:3]}")

    tool.write_text("#!/bin/sh\necho MALICIOUS-BYTES\n")   # SAME path, SAME --version output
    res = revalidate(ref, facts, runner)
    print(f"  revalidate after byte swap -> {res.status.value}")
    for d in res.drifted[:3]:
        print(f"    drift: {d[:70]}")


class ApiRunner(FakeRunner):
    def __init__(self, branch_body=None, protection_body=None):
        super().__init__(which_map={"gh": "/usr/local/bin/gh"})
        self.bodies = {"branch": branch_body, "protection": protection_body}

    def run(self, argv, *, cwd=None):
        if tuple(argv[:2]) == ("gh", "api"):
            args = " ".join(argv[2:])
            if "/protection" in args:
                b, rc = self.bodies["protection"], bool(self.bodies["protection"])
            elif "/branches/" in args:
                b, rc = self.bodies["branch"], bool(self.bodies["branch"])
            else:
                b, rc = None, False
            return ProbeResult(0 if rc else 1, b or "", "" if rc else "HTTP 404")
        return ProbeResult(127, "", "nf")


def act2() -> None:
    print("\nACT 2 — layered handoff: github.branch.push @ o/r#feat")
    r = ApiRunner(branch_body='{"name":"feat","protected":false}')
    ev = preflight("github.branch.push", "o/r", r,
                   __import__("core.toolbelt.operations", fromlist=["parse_viewer_permission"])
                   .parse_viewer_permission("WRITE"), branch="feat")
    print(f"  IMPLEMENTATION:        READY (gh resolved elsewhere)")
    print(f"  IDENTITY:              @work-user (from credential layer)")
    print(f"  REPOSITORY ROLE:       {ev.repository_role.value}")
    print(f"  OPERATION EVIDENCE:    {ev.status.value}  {[e[:30] for e in ev.evidence]}")
    print(f"  PLATFORM AUTHORIZATION: NOT DECIDED HERE")
    print(f"  note: {ev.full_note()[:100]}...")

    prot = ApiRunner(branch_body='{"name":"main","protected":true}')
    ev2 = preflight("github.branch.push", "o/r", prot, ev.repository_role, branch="main")
    print(f"\n  main (protected):      {ev2.status.value}")


def act3() -> None:
    print("\nACT 3 — normal push ≠ force push")
    both = ApiRunner(
        branch_body='{"name":"main","protected":true}',
        protection_body='{"allow_force_pushes":{"enabled":false}}')
    push = preflight("github.branch.push", "o/r", both,
                     __import__("core.toolbelt.operations", fromlist=["parse_viewer_permission"])
                     .parse_viewer_permission("WRITE"), branch="main")
    force = preflight("github.branch.force_push", "o/r", both,
                      __import__("core.toolbelt.operations", fromlist=["parse_viewer_permission"])
                      .parse_viewer_permission("WRITE"), branch="main")
    print(f"  direct push : {push.status.value}  ({push.evidence[-1]})")
    print(f"  force push  : {force.status.value}  ({force.evidence[0]})")
    permissive = ApiRunner(
        branch_body='{"name":"main","protected":true}',
        protection_body='{"allow_force_pushes":{"enabled":true}}')
    fp = preflight("github.branch.force_push", "o/r", permissive,
                   __import__("core.toolbelt.operations", fromlist=["parse_viewer_permission"])
                   .parse_viewer_permission("WRITE"), branch="main")
    dp = preflight("github.branch.push", "o/r", permissive,
                   __import__("core.toolbelt.operations", fromlist=["parse_viewer_permission"])
                   .parse_viewer_permission("WRITE"), branch="main")
    print(f"  with allow_force_pushes=true:  force={fp.status.value}  direct={dp.status.value}")


if __name__ == "__main__":
    act1()
    act2()
    act3()
