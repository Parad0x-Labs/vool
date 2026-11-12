#!/usr/bin/env python3
"""Pass #2 DEMO — "Toolbelt, prepare Git Ninja to repair and push this repo."

Act 1: real machine — fresh plan frozen into a ToolPlanRef, resource permission
       proven via read-only provider metadata.
Act 2: scripted world — GitHub account switches while Git Ninja holds the old
       plan; revalidation returns IDENTITY_CHANGED, boundary refuses, re-resolve.
Act 3: untrusted plugin receives ONE scoped handle — no enumeration, no widening.

Read-only. No pushes, no writes, no secret bytes printed.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from core.toolbelt.evidence import DriftKind, ToolPlanRef, build_exec_argv, revalidate  # noqa: E402
from core.toolbelt.identities import resolve_for_resource  # noqa: E402
from core.toolbelt.models import CapabilityNeed, RepoRef  # noqa: E402
from core.toolbelt.probes import ProbeResult  # noqa: E402
from core.toolbelt.resolve import resolve  # noqa: E402

RESOURCE = "github.com/Parad0x-Labs/nulla"


def act1() -> None:
    print("ACT 1 — fresh Git Ninja handoff on THIS machine")
    runner = None
    needs = ["github.repo.read", "git.commit", "python.test.pytest", "github.push"]
    refs = []
    for cap in needs:
        plan = resolve(CapabilityNeed(cap, RepoRef(str(REPO))))
        ref, _facts = ToolPlanRef.freeze(plan, __import__(
            "core.toolbelt.probes", fromlist=["ProbeRunner"]).ProbeRunner())
        refs.append((cap, ref))
        perm_note = ""
        if cap.startswith("github."):
            rr = resolve_for_resource(cap, RESOURCE, __import__(
                "core.toolbelt.probes", fromlist=["ProbeRunner"]).ProbeRunner())
            perm_note = f"  permission={rr.resource_permission.value} identity={rr.selected.identity if rr.selected else None}"
        print(f"  {cap:<20} {plan.status.value}{perm_note}")
        if plan.blockers:
            print(f"      note: {plan.blockers[0][:80]}")
    print("  -> Git Ninja holds ToolPlanRefs only (ids:",
          ", ".join(r.plan_id for _, r in refs) + ")")


class FakeRunner:
    def __init__(self, run_map, which_map=None):
        self.run_map = run_map or {}
        self.which_map = which_map or {}

    def which(self, name):
        return self.which_map.get(name)

    def run(self, argv, *, cwd=None):
        for n in (2, 1):
            if tuple(argv[:n]) in self.run_map:
                return self.run_map[tuple(argv[:n])]
        return ProbeResult(127, "", "nf")


def fake_world(account):
    status = f"Logged in to github.com account {account}\nToken scopes: 'repo'"
    return FakeRunner(
        which_map={"gh": "/usr/local/bin/gh"},
        run_map={
            ("gh", "--version"): ProbeResult(0, "gh version 2.88.1", ""),
            ("gh", "auth"): ProbeResult(0, status, ""),
            ("gh", "repo"): ProbeResult(
                0, '{"nameWithOwner":"o/r","viewerPermission":"WRITE"}', ""),
        })


def act2() -> None:
    print("\nACT 2 — plan goes stale under Git Ninja's feet")
    before = fake_world("work-user")
    plan = resolve(CapabilityNeed("github.repo.read", RepoRef(str(REPO))), before)
    ref, facts = ToolPlanRef.freeze(plan, before)
    print(f"  frozen plan {ref.plan_id} at {ref.observed_at}")

    after_switch = fake_world("personal-user")     # time passes; active account flips
    verdict = revalidate(ref, facts, after_switch)
    print(f"  revalidate -> {verdict.status.value}")
    for d in verdict.drifted:
        print(f"      drift: {d[:70]}")
    print(f"  execution boundary may proceed: {verdict.may_proceed_to_gate}")

    fresh_plan = resolve(CapabilityNeed("github.repo.read", RepoRef(str(REPO))),
                         after_switch)
    new_ref, new_facts = ToolPlanRef.freeze(fresh_plan, after_switch)
    ok = revalidate(new_ref, new_facts, after_switch)
    print(f"  re-resolved -> {new_ref.plan_id}, revalidate -> {ok.status.value}")


def act3() -> None:
    print("\nACT 3 — untrusted plugin gets one scoped handle")
    runner = fake_world("work-user")
    plan = resolve(CapabilityNeed("github.repo.read"), runner)
    ref, _ = ToolPlanRef.freeze(plan, runner)
    print(f"  plugin receives: ToolPlanRef({ref.plan_id}, capability={ref.capability})")
    print(f"  ref fields exposed: {sorted(vars(ref))}")
    print("  plugin CANNOT: enumerate credentials, see PATH/env, read secrets, widen scope")
    print("  execution stays with ExecutionGate; argv construction example:")
    print(f"    {build_exec_argv('/usr/local/bin/gh', ('repo', 'view'))}")


if __name__ == "__main__":
    act1()
    act2()
    act3()
