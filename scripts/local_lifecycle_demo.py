#!/usr/bin/env python
"""Git Ninja — "Prepare a clean repair commit for review." sandbox demo.

Flow: identify repo -> dirty state inspected -> exactly TWO requested files
changed -> unrelated dirty file DETECTED AND EXCLUDED -> three diff kinds
shown -> exact staging -> commit proposal FROZEN -> simulated HEAD drift
invalidates it -> refresh -> commit ONCE with re-read SHA evidence ->
duplicate invocation replays (no second commit) -> isolated review worktree
created+verified -> cherry-pick into integration branch: one CLEAN case and
one CONFLICT case whose truthful conflicted state is preserved.

No remote is touched anywhere.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, "/Users/example-user/vool/worktrees/exp-remote-forge")

from core.platform.broker import ExecutionBroker, PlatformRevocations
from core.repoops.identity import RepositoryWorkspace
from core.repoops.local_lifecycle import (
    LocalLifecycle,
    OperationInProgressError,
    bound_diff,
    capture_snapshot,
    list_worktrees,
)

FORK = PlatformRevocations().mint(
    "ninja", ["git.commit", "git.switch_branch", "git.cherry_pick",
              "git.worktree", "git.restore"])


def git(root, *args, check=True):
    p = subprocess.run(["git", "-C", str(root), *args],
                       capture_output=True, text=True)
    if check and p.returncode:
        raise RuntimeError(p.stderr)
    return p


def seed_repo(root):
    os.makedirs(root)
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "n@x")
    git(root, "config", "user.name", "Ninja")
    for name in ("app.py", "util.py"):
        open(os.path.join(root, name), "w").write(f"# {name} v1\n")
    git(root, "add", "-A")
    git(root, "commit", "-m", "base")


def freeze_staged(lc):
    from core.repoops.identity import _git
    return hashlib.sha256(
        _git(lc._ws.root, "diff", "--cached").encode()).hexdigest()[:16]


def _git_parent(root):
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD~1"],
                          capture_output=True, text=True).stdout.strip()


def main():
    tmp = tempfile.mkdtemp(prefix="ninja-local-")
    root = os.path.join(tmp, "checkout")
    seed_repo(root)
    ws = RepositoryWorkspace(root=str(root))
    lc, broker = LocalLifecycle(ws), ExecutionBroker()

    print("=" * 72)
    print('"Git Ninja, prepare a clean repair commit for review."')
    print("=" * 72)

    # 1 identify + dirty state
    snap = capture_snapshot(ws)
    print(f"1. workspace {ws.key}\n   HEAD {snap.head_sha[:12]} branch={snap.branch} dirty={snap.dirty}")

    # 2 make the two requested changes PLUS an unrelated dirty file
    open(f"{root}/app.py", "w").write("# app.py v2 — repaired\n")
    open(f"{root}/util.py", "w").write("# util.py v2 — repaired\n")
    open(f"{root}/notes.txt", "w").write("unrelated scratch\n")
    snap = capture_snapshot(ws)
    print(f"2. dirty paths now: {snap.unstaged_paths}")

    # 3 diffs are typed facts
    h2w = bound_diff(ws, "head_to_worktree")
    print(f"3. head->worktree diff bound to {h2w.head_sha[:12]} hash={h2w.content_hash}")

    # 4 EXACT staging of only the two requested files
    staged = lc.stage_exact_files(("app.py", "util.py"))
    print(f"4. staged exactly: {staged}  (notes.txt excluded)")
    assert capture_snapshot(ws).untracked_paths == ("notes.txt",)

    # 5 freeze proposal, then simulate HEAD drift -> refusal, zero commit
    parent = capture_snapshot(ws).head_sha
    shash = freeze_staged(lc)
    git(root, "commit", "--allow-empty", "-m", "unrelated interloper")
    r = lc.commit_with_proposal(FORK, broker, "repair app+util",
                                expected_head=parent, expected_staged_hash=shash)
    print(f"5. drifted proposal -> {r.status}: {r.reason}")

    # 6 refresh evidence, commit once, verify by re-read
    git(root, "reset", "--soft", "HEAD~1")
    snap = capture_snapshot(ws)
    r = lc.commit_with_proposal(FORK, broker, "repair app+util",
                                expected_head=snap.head_sha,
                                expected_staged_hash=freeze_staged(lc))
    print(f"6. commit -> {r.status} sha={r.evidence['sha'][:12]} "
          f"parent_verified={r.evidence['parent_verified']}")
    assert capture_snapshot(ws).head_sha == r.evidence["sha"]

    # 7 duplicate request REPLAYS — no second commit
    count_before = int(git(root, "rev-list", "--count", "HEAD").stdout)
    r2 = lc.commit_with_proposal(FORK, broker, "repair app+util",
                                 expected_head=parent, expected_staged_hash=shash)
    print(f"7. duplicate -> replayed={r2.replayed}; commit count unchanged="
          f"{int(git(root, 'rev-list', '--count', 'HEAD').stdout) == count_before}")

    # 8 isolated review worktree
    wt = os.path.join(tmp, "review-wt")
    git(root, "branch", "review")
    rw = lc.create_worktree(FORK, broker, wt, "review")
    print(f"8. review worktree -> {rw.status} verified={rw.evidence['verified_in_listing']} "
          f"({len(list_worktrees(ws))} worktrees)")

    # 9 cherry-pick into integration branch: CLEAN case
    # (branch from PRE-repair history so the pick carries real content)
    pre_repair = _git_parent(root)
    git(root, "branch", "integration", pre_repair)
    git(root, "switch", "integration")
    integ_head = capture_snapshot(ws).head_sha
    rc = lc.cherry_pick(FORK, broker, r.evidence["sha"],
                        expected_head=integ_head, expected_branch="integration")
    assert rc.status == "applied" and rc.evidence["conflicted"] is False, rc
    print(f"9. cherry-pick clean -> {rc.status} new_head={rc.evidence['new_head'][:12]} "
          f"conflicted={rc.evidence['conflicted']}")

    # 10 cherry-pick CONFLICT case — truthful state, no lying about effect
    git(root, "branch", "rival", pre_repair)
    git(root, "switch", "rival")
    open(f"{root}/util.py", "w").write("# RIVAL edit\n")
    git(root, "commit", "-am", "rival edit")
    rival_head = capture_snapshot(ws).head_sha
    git(root, "switch", "integration")      # util.py differs here -> conflict
    integ_head = capture_snapshot(ws).head_sha
    rc2 = lc.cherry_pick(FORK, broker, rival_head,
                         expected_head=integ_head, expected_branch="integration")
    snap = capture_snapshot(ws)
    print(f"10. conflict pick -> {rc2.status} conflicted={rc2.evidence['conflicted']} "
          f"paths={rc2.evidence.get('conflicted_paths')}")
    print(f"    repo truth preserved: operation_in_progress={snap.operation_in_progress} "
          f"conflicted={snap.conflicted_paths}")

    print("=" * 72)
    print("Demo complete: identity, exact staging, frozen proposals, replayed")
    print("duplicates, verified worktree, and honest conflict state.")


if __name__ == "__main__":
    main()
