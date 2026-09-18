#!/usr/bin/env python
"""Git Ninja — 'take over this repo' 30-second demo.

Isolated by construction: the repository is a throwaway temp checkout with a
local bare remote; the only real-network step is an OPTIONAL read-only GitHub
identity resolution. No real remote is ever written.

Flow: identify -> local state -> fetch+compare -> CI bound to SHA -> failed
check -> repair via WorkspaceFS (broker-admitted) -> tests -> commit (SHA
evidence) -> push REFUSED without permission -> granted -> push receipt ->
duplicate replays without re-executing.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.platform.broker import EffectOutcome, ExecutionBroker, PlatformRevocations  # noqa: E402
from core.remote_forge.adapters import FakeGitHubAdapter  # noqa: E402
from core.remote_forge.actions import PushBranch  # noqa: E402
from core.remote_forge.identity import explicit_identity  # noqa: E402
from core.repoops.ci import CheckRun, CiObservation  # noqa: E402
from core.repoops.evidence import LocalTestResult  # noqa: E402
from core.repoops.identity import RepositoryWorkspace  # noqa: E402
from core.repoops.localgit import LocalGit  # noqa: E402
from core.repoops.wsfs import WorkspaceFS  # noqa: E402


def hr(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def build_sandbox(base: str):
    """A throwaway checkout + its own bare origin — fully isolated."""
    root = os.path.join(base, "checkout")
    bare = os.path.join(base, "origin.git")

    def run(target, *args, **kw):
        subprocess.run(["git", "-C", target, *args], check=True,
                       capture_output=True, text=True, **kw)

    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", bare], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", root], check=True)
    run(root, "config", "user.email", "ninja@test")
    run(root, "config", "user.name", "Git Ninja")
    with open(os.path.join(root, "app.py"), "w") as f:
        f.write("def divide(a, b):\n    return a / b\n")   # the bug
    run(root, "add", "-A")
    run(root, "commit", "-q", "-m", "feat: divide")
    subprocess.run(["git", "-C", root, "branch", "-M", "fix/divide-by-zero"], check=False)
    run(root, "remote", "add", "origin", bare)
    run(root, "push", "-q", "origin", "fix/divide-by-zero")
    return root


def main() -> int:
    hr('"Git Ninja, take over this repo."')
    base = tempfile.mkdtemp(prefix="git-ninja-demo-")
    root = build_sandbox(base)

    # 1. IDENTIFY — exact workspace + exact repo identity.
    ws = RepositoryWorkspace(
        root=root, repo=explicit_identity("github", "acme", "api"),
        upstream_branch="origin/fix/divide-by-zero",
    )
    ninja = LocalGit(ws)
    fs = WorkspaceFS(ws)
    print(f"workspace  : {ws.key}")

    # 2. LOCAL STATE — git's own report.
    local = ws.read_local_state()
    print(f"local      : HEAD {local.head_sha[:12]} branch={local.branch} "
          f"dirty={local.dirty}")

    # 3. FETCH + COMPARE — observation, never an opaque pull.
    comparison = ninja.compare_after_fetch()
    print(f"fetch      : {comparison.relation} "
          f"(remote {comparison.fetched_remote_head[:12]})")

    # 4. CI BOUND TO EXACT SHA — a failing required check at THIS head.
    ci = CiObservation(
        identity=ws.repo, ci_sha=local.head_sha,
        checks=(CheckRun("pytest", "failure", local.head_sha, required=True),
                CheckRun("lint", "success", local.head_sha)),
    )
    verdict = ci.require_ci_for(local.head_sha).require_fresh().verdict()
    failing = [f["name"] for f in verdict["blocking_failures"]]
    print(f"ci         : sha {ci.ci_sha[:12]} blocking_failures={failing}")

    # 5. REPAIR — broker-admitted write inside workspace authority.
    revs = PlatformRevocations()
    builder = revs.mint("deepseek", {"wsfs.write", "git.commit", "forge.push_branch"})
    broker = ExecutionBroker()
    receipt = fs.write(builder, broker, "app.py",
                       "def divide(a, b):\n    if b == 0:\n        raise ValueError('b == 0')\n"
                       "    return a / b\n")
    print(f"repair     : wsfs.write -> {receipt.status} ({receipt.evidence.get('path')})")

    # 6. TESTS — local fact, exit-status owned, distinct from CI.
    proc = subprocess.run([sys.executable, "-B", "-c",
                           "import importlib.util,sys; spec=importlib.util.spec_from_file_location("
                           "'app', r'" + os.path.join(root, "app.py") + "'); m=importlib.util."
                           "module_from_spec(spec); spec.loader.exec_module(m);"
                           "assert m.divide(6,3)==2"], capture_output=True)
    tests = LocalTestResult(suite="smoke", passed=proc.returncode == 0,
                            sha=local.head_sha)
    print(f"tests      : local {tests.suite} passed={tests.passed} @ {tests.sha[:12]} "
          "(LOCAL fact; remote CI is a DIFFERENT fact)")

    # 7. COMMIT — staged diff bound by hash; resulting SHA is the evidence.
    subprocess.run(["git", "-C", root, "add", "-A"], check=True)
    msg = "fix: guard divide-by-zero\n\nRepairs required check 'pytest' at " + local.head_sha[:12]
    commit = ninja.commit_staged(builder, broker, msg)
    new_sha = commit.evidence["sha"]
    print(f"commit     : {commit.status} sha={new_sha[:12]} "
          f"files={commit.evidence['files']} diff_hash={commit.evidence['diff_hash']}")

    # 8. PUSH — refused while unauthorized, then executed under authorization.
    challenger = revs.mint("ling", {"read_repo"})            # no write tokens
    forged_broker = ExecutionBroker()
    fake_forge = FakeGitHubAdapter()
    fake_forge.seed_branch(ws.repo, local.branch, local.head_sha)
    push = PushBranch(identity=ws.repo, branch=local.branch, head_sha=new_sha)

    denied = forged_broker.execute(challenger, type("R", (), {
        "effect_id": "forge.branch.push", "required_capability": "forge.push_branch",
        "params": dict(push.payload()), "idempotency_key": push.idempotency_key(),
    })(), lambda: EffectOutcome(status="applied", evidence={"sha": new_sha}))
    print(f"push       : challenger -> {denied.status} ({denied.reason}) — REFUSED")

    def dispatch():
        result = fake_forge.execute(push)
        # Translate forge vocabulary -> platform effect vocabulary BEFORE the
        # broker sees it; a mismatch here must be impossible by construction.
        mapped = {"succeeded": "applied", "failed": "failed", "unknown": "unknown"}[
            result.status
        ]
        return EffectOutcome(status=mapped,
                             reason=result.error,
                             evidence=dict(result.evidence))

    from core.platform.broker import EffectRequest
    request = EffectRequest(effect_id="forge.branch.push",
                            required_capability="forge.push_branch",
                            params=dict(push.payload()),
                            idempotency_key=push.idempotency_key())
    ok = forged_broker.execute(builder, request, dispatch)
    print(f"push       : authorized -> {ok.status}; REMOTE head now "
          f"{fake_forge.branches[ws.repo.key()][local.branch].head_sha[:12]} "
          f"(receipt evidence: {ok.evidence})")
    duplicate = forged_broker.execute(builder, request, dispatch)
    print(f"duplicate  : replayed={duplicate.replayed} — executed exactly once")

    hr("DONE — isolated sandbox only; no real remote was written")
    print(f"sandbox    : {base}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
