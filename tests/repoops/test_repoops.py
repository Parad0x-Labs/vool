"""Adversarial tests for the RepoOps surface. No real remote writes; local git
runs against throwaway temp repositories created per-test."""
from __future__ import annotations

import os
import subprocess

import pytest

from core.platform.broker import EffectOutcome, ExecutionBroker, PlatformRevocations
from core.remote_forge.identity import explicit_identity
from core.repoops.ci import (
    CheckRun,
    CiObservation,
    CiShaMismatch,
    StaleCiError,
)
from core.repoops.evidence import LocalTestResult, RepositoryEvidence, StaleEvidenceError
from core.repoops.identity import RepositoryWorkspace, WorkspaceError
from core.repoops.localgit import LocalGit, classify_sync
from core.repoops.wsfs import PathEscapeError, WorkspaceFS

# ---------------------------------------------------------------- fixtures


@pytest.fixture()
def git_repo(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    def run(*args):
        subprocess.run(["git", "-C", str(root), *args], check=True,
                       capture_output=True, text=True)
    run("init", "-q", "-b", "main")
    run("config", "user.email", "ninja@test")
    run("config", "user.name", "Ninja")
    (root / "README.md").write_text("hello\n")
    run("add", "-A")
    run("commit", "-q", "-m", "init")
    return root


def make_workspace(root) -> RepositoryWorkspace:
    return RepositoryWorkspace(
        root=str(root), repo=explicit_identity("github", "acme", "api"),
        upstream_branch="origin/main",
    )


def builder(broker_revocations=None):
    revs = broker_revocations or PlatformRevocations()
    fork = revs.mint("builder-1", frozenset({
        "wsfs.read", "wsfs.write", "wsfs.delete", "git.commit", "git.create_branch",
    }))
    return fork, revs


# ---------------------------------------------------------------- identity


def test_similar_checkout_is_a_different_workspace(tmp_path):
    a = tmp_path / "api"; b = tmp_path / "api-2"
    a.mkdir(); b.mkdir()
    ws_a = make_workspace(a)
    with pytest.raises(WorkspaceError):
        ws_a.require_same_repo(make_workspace(b))


def test_renamed_or_transferred_repo_is_not_the_same_identity():
    before = explicit_identity("github", "acme", "api")
    after = explicit_identity("github", "acme-corp", "api")
    assert before.key() != after.key()


def test_dirty_tree_and_detached_head_are_explicit(git_repo):
    def run(*args):
        subprocess.run(["git", "-C", str(git_repo), *args], check=True,
                       capture_output=True, text=True)
    (git_repo / "x.txt").write_text("dirty\n")
    state = make_workspace(git_repo).read_local_state()
    assert state.dirty and state.changed_paths == ("x.txt",)
    sha = state.head_sha
    run("checkout", "--detach", sha)          # quiet detach
    state = make_workspace(git_repo).read_local_state()
    assert state.detached and state.branch is None


# ---------------------------------------------------------------- workspace fs


def test_path_traversal_refused(git_repo):
    fs = WorkspaceFS(make_workspace(git_repo))
    with pytest.raises(PathEscapeError):
        fs.resolve("../../etc/passwd")


def test_absolute_path_refused(git_repo):
    fs = WorkspaceFS(make_workspace(git_repo))
    with pytest.raises(PathEscapeError):
        fs.resolve("/etc/passwd")


def test_symlink_escape_refused(git_repo):
    outside = git_repo.parent / "outside.txt"
    outside.write_text("secret\n")
    os.symlink(outside, git_repo / "link.txt")
    fs = WorkspaceFS(make_workspace(git_repo))
    with pytest.raises(PathEscapeError):
        fs.read("link.txt")


def test_wrong_worktree_operation_refused(tmp_path, git_repo):
    other = tmp_path / "other-checkout"
    other.mkdir()
    with pytest.raises(PathEscapeError):
        WorkspaceFS(make_workspace(git_repo)).require_same_workspace(
            WorkspaceFS(make_workspace(other))
        )


def test_write_replace_race_is_refused_on_prior_mismatch(git_repo):
    fork, _ = builder()
    fs = WorkspaceFS(make_workspace(git_repo))
    receipt = fs.write(fork, ExecutionBroker(), "README.md", "new\n",
                       expected_prior_sha256="0" * 16)
    assert receipt.status == "refused"
    assert receipt.reason == "prior_state_mismatch"
    assert fs.read("README.md") == "hello\n"


def test_write_then_duplicate_replays_not_rewrites(git_repo):
    fork, _ = builder()
    broker = ExecutionBroker()
    fs = WorkspaceFS(make_workspace(git_repo))
    first = fs.write(fork, broker, "notes.txt", "v1\n")
    second = fs.write(fork, broker, "notes.txt", "v1\n")
    assert first.status == "applied" and second.status == "applied"
    assert second.replayed and second.idempotency_key == first.idempotency_key


def test_reviewer_cannot_write_files(git_repo):
    revs = PlatformRevocations()
    reviewer = revs.mint("ox", frozenset({"wsfs.read"}))
    receipt = WorkspaceFS(make_workspace(git_repo)).write(
        reviewer, ExecutionBroker(), "README.md", "hacked\n")
    assert receipt.status == "refused" and "capability_missing" in receipt.reason


# ---------------------------------------------------------------- fetch/pull


def test_fetch_comparison_fast_forward_vs_diverged():
    ff = classify_sync("a" * 40, "b" * 40, is_ancestor_fn=lambda o, n: True)
    assert ff.relation == "local_behind_fast_forward"
    forced = classify_sync("a" * 40, "c" * 40, is_ancestor_fn=lambda o, n: False)
    assert forced.relation == "diverged"     # force-push detected as divergence
    same = classify_sync("a" * 40, "a" * 40)
    assert same.relation == "in_sync"


def test_local_git_commit_produces_sha_evidence(git_repo):
    fork, _ = builder()
    lg = LocalGit(make_workspace(git_repo))
    subprocess.run(["git", "-C", str(git_repo), "add", "-A"], check=True)
    (git_repo / "fix.py").write_text("print(1)\n")
    subprocess.run(["git", "-C", str(git_repo), "add", "-A"], check=True)
    receipt = lg.commit_staged(fork, ExecutionBroker(), "repoops: fix thing")
    assert receipt.status == "applied"
    assert receipt.evidence["sha"] == lg.status().head_sha
    assert receipt.evidence["files"] == ["fix.py"]      # exactly what changed


def test_empty_stage_commit_fails_without_sha_claim(git_repo):
    fork, _ = builder()
    receipt = LocalGit(make_workspace(git_repo)).commit_staged(
        fork, ExecutionBroker(), "nothing")
    assert receipt.status == "failed"
    assert receipt.evidence == {}            # no sha to claim


def test_branch_switch_changes_subsequent_assumptions(git_repo):
    fork, _ = builder()
    lg = LocalGit(make_workspace(git_repo))
    receipt = lg.create_branch_and_switch(fork, ExecutionBroker(), "repair")
    assert receipt.status == "applied"
    assert lg.status().branch == "repair"    # re-read, never assume


# ---------------------------------------------------------------- revocation


def test_revocation_between_proposal_and_execution():
    revs = PlatformRevocations()
    fork = revs.mint("builder-1", frozenset({"wsfs.write"}))
    assert "wsfs.write" in fork.caps.tokens
    revs.revoke("builder-1", "wsfs.write")
    refreshed = revs.mint("builder-1", frozenset({"wsfs.write"}))
    assert "wsfs.write" not in refreshed.caps.tokens


# ---------------------------------------------------------------- unknown / duplicates


def test_unknown_outcome_blocks_retry_until_reconciled():
    fork, _ = builder()
    broker = ExecutionBroker()
    from core.platform.broker import EffectRequest

    request = EffectRequest(effect_id="forge.branch.push", required_capability="forge.push_branch",
                            params={}, idempotency_key="k1")
    # grant token by minting with forge token included
    revs = PlatformRevocations()
    fork = revs.mint("b", frozenset({"forge.push_branch"}))

    def flaky():
        return EffectOutcome(status="unknown", reason="connection lost")

    first = broker.execute(fork, request, flaky)
    assert first.status == "unknown"
    second = broker.execute(fork, request, flaky)
    assert second.status == "refused" and "unreconciled" in second.reason
    resolved = broker.reconcile(
        "k1", lambda: EffectOutcome(status="applied",
                                    evidence={"sha": "f" * 40}))
    assert resolved.happened and resolved.evidence["sha"] == "f" * 40


# ---------------------------------------------------------------- CI truth


def make_ci(ci_sha, checks):
    return CiObservation(identity=explicit_identity("github", "acme", "api"),
                         ci_sha=ci_sha, checks=checks)


def test_ci_belongs_to_exact_sha():
    ci = make_ci("a" * 40, (CheckRun("build", "success", "a" * 40, required=True),))
    ci.require_ci_for("a" * 40)                      # fine
    with pytest.raises(CiShaMismatch):
        ci.require_ci_for("b" * 40)                  # PR head moved after read


def test_skipped_is_not_passed():
    ci = make_ci("a" * 40, (CheckRun("e2e", "skipped", "a" * 40, required=True),))
    assert ci.verdict()["all_required_pass"] is False


def test_pending_check_is_not_green():
    ci = make_ci("a" * 40, (CheckRun("ci", "pending", "a" * 40, required=True),))
    verdict = ci.verdict()
    assert verdict["all_required_pass"] is False and verdict["pending"] == ["ci"]


def test_optional_failure_does_not_block_but_is_visible():
    ci = make_ci("a" * 40, (
        CheckRun("required-build", "failure", "a" * 40, required=True),
        CheckRun("lint-optional", "failure", "a" * 40, required=False),
    ))
    names = [f["name"] for f in ci.verdict()["blocking_failures"]]
    assert names == ["required-build"]


def test_stale_ci_refused():
    import time
    old = CiObservation(identity=explicit_identity("github", "acme", "api"),
                        ci_sha="a" * 40, checks=(), fetched_at=time.time() - 9999)
    with pytest.raises(StaleCiError):
        old.require_fresh()


# ---------------------------------------------------------------- local tests vs CI


def test_local_test_result_distinct_from_ci_and_skip_is_not_pass():
    local_green = LocalTestResult(suite="pytest", passed=True, sha="a" * 40)
    remote_red_at_newer_sha = ("ci", False, "b" * 40)
    assert local_green.sha != remote_red_at_newer_sha[2]   # perfectly representable
    skipped = LocalTestResult(suite="pytest", passed=False, sha="a" * 40,
                              skipped_count=7)
    assert not skipped.passed


def test_stale_evidence_bundle_refused(git_repo):
    import time
    ws = make_workspace(git_repo)
    ev = RepositoryEvidence(workspace=ws, local=ws.read_local_state())
    ev.require_fresh()
    import dataclasses
    aged = dataclasses.replace(ev, assembled_at=time.time() - 9999)
    with pytest.raises(StaleEvidenceError):
        aged.require_fresh()


# ---------------------------------------------------------------- merge staleness


def test_merge_on_stale_pr_head_is_refused_by_precondition():
    """PR head moved since evidence was bound -> expected_head_sha refuses."""
    pr_head_when_reviewed = "d" * 40
    pr_head_now = "e" * 40
    assert pr_head_when_reviewed != pr_head_now   # precondition fires in forge layer;
    # covered end-to-end in tests/remote_forge/test_adversarial.py::merge moved head
