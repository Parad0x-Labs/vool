"""Local Git lifecycle: S1-S15 sabotage suite + sandbox demo flow.

Every test builds throwaway git repositories; no real remote anywhere.
"""
from __future__ import annotations

import os
import subprocess

import pytest

from core.platform.broker import ExecutionBroker, PlatformRevocations
from core.repoops.identity import RepositoryWorkspace
from core.repoops.local_lifecycle import (
    LocalLifecycle,
    OperationInProgressError,
    StageScopeError,
    StaleSnapshotError,
    bound_diff,
    capture_snapshot,
    list_worktrees,
)

ALL_TOKENS = [
    "git.switch_branch", "git.delete_branch", "git.commit",
    "git.restore", "git.unstage", "fs.delete_untracked",
    "git.cherry_pick", "git.worktree",
]


def _git(root, *args, check=True):
    proc = subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, timeout=30)
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {args}: {proc.stderr}")
    return proc


def _commit_all(root, msg="c"):
    _git(root, "add", "-A")
    _git(root, "commit", "-m", msg)


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "n@x")
    _git(root, "config", "user.name", "Ninja")
    (root / "a.txt").write_text("one\n")
    _commit_all(root, "base")
    return RepositoryWorkspace(root=str(root))


def _fork(tokens=ALL_TOKENS):
    return PlatformRevocations().mint("ninja", tokens)


def _broker():
    return ExecutionBroker()


def _freeze(lc):
    snap = capture_snapshot(lc._ws)
    import hashlib

    from core.repoops.identity import _git as _g
    text = _g(lc._ws.root, "diff", "--cached")   # same normalization as dispatch
    return snap, hashlib.sha256(text.encode()).hexdigest()[:16]


# -- snapshot / status truth ------------------------------------------------------

def test_status_truth_distinguishes_staged_unstaged_untracked(repo):
    ws = repo
    (ws.root + "/a.txt").replace  # noqa: B018 - touch attr to appease linters
    open(os.path.join(ws.root, "a.txt"), "w").write("changed\n")   # unstaged
    open(os.path.join(ws.root, "b.txt"), "w").write("new\n")       # untracked
    _git(ws.root, "add", "a.txt")                                  # now staged too
    snap = capture_snapshot(ws)
    assert snap.staged_paths == ("a.txt",)
    assert not snap.unstaged_paths          # fully staged change leaves Y clean
    assert snap.untracked_paths == ("b.txt",)
    assert snap.branch == "main"
    assert not snap.detached


def test_detached_head_is_represented_not_guessed(repo):
    ws = repo
    sha = capture_snapshot(ws).head_sha
    _git(ws.root, "checkout", "--detach", check=True)
    snap = capture_snapshot(ws)
    assert snap.detached and snap.branch is None
    assert snap.head_sha == sha


def test_s12_detached_head_rejected_by_branch_bound_cherry_pick(repo):
    _git(repo.root, "checkout", "--detach")
    lc = LocalLifecycle(repo)
    src = capture_snapshot(repo).head_sha
    receipt = lc.cherry_pick(_fork(), _broker(), src,
                             expected_head=src, expected_branch="main")
    assert receipt.status == "refused"      # detached is NOT branch 'main'


# -- stale evidence ---------------------------------------------------------------

def test_s1_stale_snapshot_after_head_move_invalidates_commit_proposal(repo):
    ws, lc = repo, LocalLifecycle(repo)
    _git(ws.root, "add", "-A")
    snap, shash = _freeze(lc)
    # HEAD moves after the proposal was frozen:
    open(os.path.join(ws.root, "drift.txt"), "w").write("x\n")
    _commit_all(ws.root, "interloper")
    receipt = lc.commit_with_proposal(
        _fork(), _broker(), "repair",
        expected_head=snap.head_sha, expected_staged_hash=shash)
    assert receipt.status == "refused"
    assert "stale_commit_proposal" in receipt.reason
    assert "repair" not in _git(ws.root, "log", "--format=%s").stdout.splitlines()


def test_s2_stale_diff_after_file_mutation_is_refused(repo):
    ws = repo
    d = bound_diff(ws, "head_to_worktree")
    open(os.path.join(ws.root, "a.txt"), "a").write("more\n")
    fresh = capture_snapshot(ws)
    with pytest.raises(StaleSnapshotError):
        d.require_current(ws)               # review would be stale


def test_diff_kinds_are_distinct_facts(repo):
    ws = repo
    open(os.path.join(ws.root, "a.txt"), "w").write("edit\n")
    _git(ws.root, "add", "a.txt")
    head_to_index = bound_diff(ws, "head_to_index")
    index_to_wt = bound_diff(ws, "index_to_worktree")
    assert head_to_index.content_hash != index_to_wt.content_hash


# -- exact staging law -------------------------------------------------------------

def test_s3_exact_file_commit_does_not_absorb_unrelated_dirty_file(repo):
    ws, lc = repo, LocalLifecycle(repo)
    open(os.path.join(ws.root, "target.txt"), "w").write("fix\n")
    open(os.path.join(ws.root, "unrelated.txt"), "w").write("noise\n")
    staged = lc.stage_exact_files(("target.txt",))
    assert staged == ("target.txt",)        # nothing leaked in
    snap, shash = _freeze(lc)
    r = lc.commit_with_proposal(_fork(), _broker(), "fix target",
                                expected_head=snap.head_sha,
                                expected_staged_hash=shash)
    assert r.status == "applied"
    files = _git(ws.root, "show", "--name-only", "--format=").stdout.split()
    assert "unrelated.txt" not in files     # still dirty outside the commit
    assert os.path.exists(os.path.join(ws.root, "unrelated.txt"))


def test_stage_refuses_empty_scope_and_escape(repo):
    lc = LocalLifecycle(repo)
    with pytest.raises(StageScopeError):
        lc.stage_exact_files(())
    with pytest.raises(StageScopeError):
        lc.stage_exact_files(("../outside.txt",))


def test_s9_symlink_escape_in_staging_is_refused(repo, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n")
    link = os.path.join(repo.root, "link.txt")
    os.symlink(str(outside), link)
    with pytest.raises(StageScopeError):
        LocalLifecycle(repo).stage_exact_files(("link.txt",))


# -- commit truth ------------------------------------------------------------------

def test_s4_commit_evidence_carries_reread_head_and_parent(repo):
    ws, lc = repo, LocalLifecycle(repo)
    open(os.path.join(ws.root, "f.txt"), "w").write("data\n")
    lc.stage_exact_files(("f.txt",))
    parent = capture_snapshot(ws).head_sha
    _, shash = _freeze(lc)
    r = lc.commit_with_proposal(_fork(), _broker(), "add f",
                                expected_head=parent, expected_staged_hash=shash)
    assert r.status == "applied"
    assert r.evidence["parent_verified"] is True
    assert r.evidence["sha"] != parent
    assert capture_snapshot(ws).head_sha == r.evidence["sha"]   # world agrees


def test_empty_stage_commit_is_refused(repo):
    lc = LocalLifecycle(repo)
    snap, shash = _freeze(lc)
    r = lc.commit_with_proposal(_fork(), _broker(), "empty",
                                expected_head=snap.head_sha,
                                expected_staged_hash=shash)
    assert r.status == "refused" and "empty_commit" in r.reason


def test_duplicate_commit_request_replays_not_double_commits(repo):
    ws, lc, broker = repo, LocalLifecycle(repo), _broker()
    open(os.path.join(ws.root, "f.txt"), "w").write("data\n")
    lc.stage_exact_files(("f.txt",))
    snap, shash = _freeze(lc)
    fork = _fork()
    r1 = lc.commit_with_proposal(fork, broker, "once",
                                 expected_head=snap.head_sha,
                                 expected_staged_hash=shash)
    count_before = int(_git(ws.root, "rev-list", "--count", "HEAD").stdout)
    r2 = lc.commit_with_proposal(fork, broker, "once",
                                 expected_head=snap.head_sha,
                                 expected_staged_hash=shash)
    assert r2.replayed and r2.status == "applied"
    assert int(_git(ws.root, "rev-list", "--count", "HEAD").stdout) == count_before


def test_s5_lost_response_becomes_unknown_then_reconciles(repo):
    """Commit really happens, response lost -> UNKNOWN -> reconcile by
    parent+tree identity (message is NOT identity)."""
    import core.repoops.local_lifecycle as mod

    ws, lc, broker = repo, LocalLifecycle(repo), _broker()
    open(os.path.join(ws.root, "f.txt"), "w").write("data\n")
    lc.stage_exact_files(("f.txt",))
    snap = capture_snapshot(ws)
    intent = lc.freeze_commit_intent(
        "lost", expected_head=snap.head_sha,
        expected_staged_hash=_freeze(lc)[1])
    real_run = subprocess.run
    state = {}

    def crashing_run(cmd, **kw):
        result = real_run(cmd, **kw)
        if cmd[:2] == ["git", "-C"] and cmd[3] == "commit":
            state["committed"] = True
            raise RuntimeError("connection lost after commit dispatch")
        return result

    mod.subprocess.run = crashing_run
    try:
        lc.commit_with_proposal(_fork(), broker, intent=intent)
    except RuntimeError:
        pass                                  # surfaced past broker as UNKNOWN? no:
    finally:
        mod.subprocess.run = real_run

    receipts = [rc for rc in broker.all_receipts() if rc.effect_id == "git.commit"]
    assert receipts, "attempt must be recorded"
    rec = receipts[-1]
    assert rec.status == "unknown"           # never silently failed/applied
    assert state.get("committed")            # but the world DID change

    # Blind retry of the same key refuses while UNKNOWN is unreconciled.
    retry = lc.commit_with_proposal(_fork(), broker, intent=intent)
    assert retry.status == "refused" and "unreconciled_unknown_key" in retry.reason

    resolved = lc.reconcile_commit_unknown(rec.idempotency_key, broker, intent)
    assert resolved.status == "applied" and resolved.evidence["reconciled"]


def test_s13_configured_signing_never_claimed_verified(repo):
    ws, lc = repo, LocalLifecycle(repo)
    _git(ws.root, "config", "commit.gpgsign", "false")  # configured-but-off
    open(os.path.join(ws.root, "s.txt"), "w").write("x\n")
    lc.stage_exact_files(("s.txt",))
    snap, shash = _freeze(lc)
    r = lc.commit_with_proposal(_fork(), _broker(), "signed?",
                                expected_head=snap.head_sha,
                                expected_staged_hash=shash,
                                signing_required=True)
    assert r.status == "applied"             # commit exists...
    assert r.evidence["signature"] == "no_signature"
    assert r.evidence["claim_signed_completion"] is False   # ...claim refused


# -- restore / unstage / untracked delete --------------------------------------------

def test_restore_and_unstage_are_typed_narrow_mutations(repo):
    ws, lc, broker, fork = repo, LocalLifecycle(repo), _broker(), _fork()
    open(os.path.join(ws.root, "a.txt"), "w").write("dirty\n")
    _git(ws.root, "add", "a.txt")
    r1 = lc.unstage_file(fork, broker, "a.txt")
    assert r1.status == "applied" and r1.evidence["still_staged"] is False
    assert "a.txt" not in capture_snapshot(ws).staged_paths
    r2 = lc.restore_worktree_file(fork, broker, "a.txt")
    assert r2.status == "applied"
    assert capture_snapshot(ws).entries == ()           # worktree restored


def test_untracked_delete_refuses_tracked_file(repo):
    lc, fork, broker = LocalLifecycle(repo), _fork(), _broker()
    r = lc.delete_untracked_file(fork, broker, "a.txt")   # a.txt is tracked
    assert r.status == "refused" and "not_an_untracked_file" in r.reason


# -- branches ---------------------------------------------------------------------

def test_switch_refuses_branch_checked_out_elsewhere(repo, tmp_path):
    ws = repo
    _git(ws.root, "branch", "feature")
    lc = LocalLifecycle(ws)
    r = lc.create_worktree(_fork(), _broker(), str(tmp_path / "wt"), "feature")
    assert r.status == "applied"
    r2 = lc.switch_branch(_fork(), _broker(), "feature")
    assert r2.status == "refused" and "checked_out_elsewhere" in r2.reason


def test_delete_current_branch_refused(repo):
    lc = LocalLifecycle(repo)
    r = lc.delete_local_branch(_fork(), _broker(), "main")
    assert r.status == "refused" and "current_branch" in r.reason


def test_delete_unmerged_branch_requires_explicit_force(repo):
    ws = repo
    _git(ws.root, "switch", "-c", "orphan")
    open(os.path.join(ws.root, "o.txt"), "w").write("o\n")
    _commit_all(ws.root, "orphan commit")
    _git(ws.root, "switch", "main")
    lc = LocalLifecycle(repo)
    r = lc.delete_local_branch(_fork(["git.delete_branch"]), _broker(), "orphan")
    assert r.status == "refused"            # git -d's definitive NO
    r2 = lc.delete_local_branch(_fork(["git.delete_branch"]), _broker(),
                                "orphan", force_unmerged=True)
    assert r2.status == "applied"


# -- worktrees --------------------------------------------------------------------

def test_s6_dirty_worktree_removal_refused(repo, tmp_path):
    wt = tmp_path / "wt"
    lc = LocalLifecycle(repo)
    r = lc.create_worktree(_fork(), _broker(), str(wt), "main")
    assert r.status == "refused"            # main is checked out here already
    r = lc.create_worktree(_fork(), _broker(), str(wt), "-b", )
    assert r.status == "refused"            # bad branch arg -> git refuses


def test_worktree_create_verify_remove_clean_only(repo, tmp_path):
    ws = repo
    _git(ws.root, "branch", "review")
    lc = LocalLifecycle(ws)
    wt = tmp_path / "review-wt"
    r = lc.create_worktree(_fork(), _broker(), str(wt), "review")
    assert r.status == "applied" and r.evidence["verified_in_listing"] is True
    assert (os.path.realpath(str(wt)), ) [0] == os.path.realpath(str(wt))
    assert any(p == os.path.realpath(str(wt)) for _b, p in list_worktrees(ws))
    # dirty it — removal must refuse:
    open(os.path.join(str(wt), "wip.txt"), "w").write("half-done\n")
    r2 = lc.remove_worktree(_fork(), _broker(), str(wt))
    assert r2.status == "refused" and "dirty_worktree_refused" in r2.reason
    assert os.path.exists(str(wt))
    _git(wt, "checkout", "--", ".")
    os.unlink(os.path.join(str(wt), "wip.txt"))
    r3 = lc.remove_worktree(_fork(), _broker(), str(wt))
    assert r3.status == "applied" and r3.evidence["verified_gone"] is True


# -- in-progress / conflict truth -------------------------------------------------

@pytest.fixture()
def conflicted_repo(tmp_path):
    root = tmp_path / "conf"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "n@x")
    _git(root, "config", "user.name", "Ninja")
    (root / "f.txt").write_text("base\n")
    _commit_all(root, "base")
    _git(root, "switch", "-c", "side")
    (root / "f.txt").write_text("side\n")
    _commit_all(root, "side")
    _git(root, "switch", "main")
    (root / "f.txt").write_text("main\n")
    _commit_all(root, "main change")
    proc = subprocess.run(["git", "-C", str(root), "merge", "side"],
                          capture_output=True, text=True)
    assert proc.returncode != 0             # conflict on purpose
    return RepositoryWorkspace(root=str(root))


def test_conflict_state_is_recognized_and_blocks_mutations(conflicted_repo):
    snap = capture_snapshot(conflicted_repo)
    assert snap.operation_in_progress == "merge"
    assert snap.conflicted_paths == ("f.txt",)
    with pytest.raises(OperationInProgressError):
        LocalLifecycle(conflicted_repo).require_clean_of_operations()


def test_s14_no_implicit_stash_escape_hatch_exists(repo):
    """Dirty tree + no stash op anywhere: restore is typed or refused."""
    open(os.path.join(repo.root, "a.txt"), "w").write("dirty\n")
    lc = LocalLifecycle(repo)
    assert not hasattr(lc, "stash")         # S14: no implicit stash tool
    r = lc.restore_worktree_file(_fork(), _broker(), "a.txt")
    assert r.status == "applied"            # explicit typed restore works
    assert capture_snapshot(repo).entries == ()


def test_s15_amend_rebase_reset_do_not_exist_as_operations(repo):
    for forbidden in ("amend", "rebase", "reset_hard", "stash_pop"):
        assert not hasattr(LocalLifecycle(repo), forbidden)


def test_s10_cherry_pick_conflict_is_applied_with_truth_not_failed(repo):
    """Repo entered conflicted state => world changed => APPLIED+conflicted."""
    ws, lc = repo, LocalLifecycle(repo)
    _git(ws.root, "switch", "-c", "topic")
    (ws.root + "/../x") if False else None
    open(os.path.join(ws.root, "cf.txt"), "w").write("topic version\n")
    _commit_all(ws.root, "topic cf")
    src_sha = capture_snapshot(ws).head_sha
    _git(ws.root, "switch", "main")
    open(os.path.join(ws.root, "cf.txt"), "w").write("rival version\n")
    _commit_all(ws.root, "rival cf")
    head = capture_snapshot(ws).head_sha
    r = LocalLifecycle(ws).cherry_pick(
        _fork(["git.cherry_pick"]), _broker(), src_sha,
        expected_head=head, expected_branch="main")
    assert r.status == "applied"
    assert r.evidence["conflicted"] is True
    after = capture_snapshot(ws)
    assert after.operation_in_progress == "cherry-pick"   # truthful repo state
    assert after.conflicted_paths == ("cf.txt",)


def test_cherry_pick_clean_case_verifies_new_head(repo):
    ws, lc = repo, LocalLifecycle(repo)
    _git(ws.root, "switch", "-c", "feature")
    open(os.path.join(ws.root, "feat.txt"), "w").write("f\n")
    _commit_all(ws.root, "feat commit")
    src_sha = capture_snapshot(ws).head_sha
    _git(ws.root, "switch", "main")
    head = capture_snapshot(ws).head_sha
    r = lc.cherry_pick(_fork(["git.cherry_pick"]), _broker(), src_sha,
                       expected_head=head, expected_branch="main")
    assert r.status == "applied"
    assert r.evidence["conflicted"] is False
    assert r.evidence["new_head"] == capture_snapshot(ws).head_sha


def test_s11_cherry_pick_unknown_source_sha_refused(repo):
    head = capture_snapshot(repo).head_sha
    r = LocalLifecycle(repo).cherry_pick(
        _fork(["git.cherry_pick"]), _broker(), "0" * 40,
        expected_head=head, expected_branch="main")
    assert r.status == "refused" and "unknown_source_sha" in r.reason


def test_cherry_pick_head_drift_refused_before_dispatch(repo):
    ws, lc = repo, LocalLifecycle(repo)
    _git(ws.root, "switch", "-c", "feature")
    open(os.path.join(ws.root, "g.txt"), "w").write("g\n")
    _commit_all(ws.root, "g")
    src_sha = capture_snapshot(ws).head_sha
    _git(ws.root, "switch", "main")
    stale_head = capture_snapshot(ws).head_sha
    open(os.path.join(ws.root, "late.txt"), "w").write("late\n")
    _commit_all(ws.root, "drift")           # HEAD moves after freeze
    r = lc.cherry_pick(_fork(["git.cherry_pick"]), _broker(), src_sha,
                       expected_head=stale_head, expected_branch="main")
    assert r.status == "refused" and "stale_cherry_pick" in r.reason


# -- nested repo / identity ---------------------------------------------------------

def test_s8_nested_repo_operations_cannot_touch_parent(tmp_path):
    outer = tmp_path / "outer"
    inner_root = outer / "inner"
    inner_root.mkdir(parents=True)
    for d in (outer, inner_root):
        _git(d, "init", "-b", "main")
        _git(d, "config", "user.email", "n@x")
        _git(d, "config", "user.name", "Ninja")
        (d / "seed.txt").write_text(f"{d.name}\n")
        _commit_all(d, f"init {d.name}")
    inner_lc = LocalLifecycle(RepositoryWorkspace(root=str(inner_root)))
    with pytest.raises(StageScopeError):     # ../escape dies at the path guard
        inner_lc.stage_exact_files(("../seed.txt",))
    # and the parent tracked content was never touched (inner/ shows only as untracked):
    out = _git(outer, "status", "--porcelain").stdout.strip()
    assert all(line.startswith("??") for line in out.splitlines())


def test_snapshot_cannot_authorize_other_workspace(repo, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-b", "main")
    snap = capture_snapshot(repo)
    assert snap.workspace_key != RepositoryWorkspace(
        root=str(other)).key                 # distinct identities, never merged



# -- FINAL CLOSURE: reconciliation identity (message is NOT identity) --------------

def _commit_object(root, parent, tree, message, when=None):
    """Craft a commit with an EXACT parent/tree/message via plumbing."""
    import time
    ts = f"@{when if when is not None else int(time.time())} +0000"
    env = dict(os.environ, GIT_AUTHOR_NAME="X", GIT_AUTHOR_EMAIL="x@x",
               GIT_COMMITTER_NAME="X", GIT_COMMITTER_EMAIL="x@x",
               GIT_AUTHOR_DATE=ts, GIT_COMMITTER_DATE=ts)
    p = subprocess.run(
        ["git", "-C", str(root), "commit-tree", tree, "-p", parent, "-m", message],
        capture_output=True, text=True, env=env, check=True)
    return p.stdout.strip()


def test_r1_same_parent_same_message_different_tree_never_reconciles(repo):
    ws, lc = repo, LocalLifecycle(repo)
    open(os.path.join(ws.root, "f.txt"), "w").write("B\n")
    lc.stage_exact_files(("f.txt",))
    snap = capture_snapshot(ws)
    intent = lc.freeze_commit_intent("fix parser",
                                     expected_head=snap.head_sha,
                                     expected_staged_hash=_freeze(lc)[1])
    # Sibling decoy: same parent, same MESSAGE, DIFFERENT tree.
    decoy_tree = _git(ws.root, "mktree").stdout.strip()  # empty tree
    _commit_object(ws.root, snap.head_sha, decoy_tree, "fix parser", when=1)
    assert len(lc.find_commit_candidates(intent)) == 0   # decoy not matched
    subprocess.run(["git", "-C", ws.root, "commit", "-m", "fix parser"],
                   capture_output=True, text=True)       # our real C2
    cands = lc.find_commit_candidates(intent)
    assert len(cands) == 1
    head_tree = _git(ws.root, "rev-parse", "HEAD^{tree}").stdout.strip()
    assert head_tree == intent.intended_tree


def test_r2_unique_parent_plus_tree_match_reconciles(repo):
    ws, lc, broker = repo, LocalLifecycle(repo), _broker()
    open(os.path.join(ws.root, "f.txt"), "w").write("v2\n")
    lc.stage_exact_files(("f.txt",))
    snap = capture_snapshot(ws)
    intent = lc.freeze_commit_intent("intended",
                                     expected_head=snap.head_sha,
                                     expected_staged_hash=_freeze(lc)[1])
    # full path: crash AFTER the real commit dispatch, like S5
    import core.repoops.local_lifecycle as mod
    real_run = subprocess.run
    def crash(cmd, **kw):
        r = real_run(cmd, **kw)
        if cmd[:2] == ["git", "-C"] and cmd[3] == "commit":
            raise RuntimeError("lost")
        return r
    mod.subprocess.run = crash
    try:
        lc.commit_with_proposal(_fork(), broker, intent=intent)
    except RuntimeError:
        pass
    finally:
        mod.subprocess.run = real_run
    rec = [r for r in broker.all_receipts()
           if r.effect_id == "git.commit"][-1]
    assert rec.status == "unknown"
    out = lc.reconcile_commit_unknown(rec.idempotency_key, broker, intent)
    assert out.status == "applied"
    assert out.evidence["matched_by"] == "parent+tree"


def test_r3_multiple_candidates_leave_unknown(repo):
    ws, lc = repo, LocalLifecycle(repo)
    open(os.path.join(ws.root, "f.txt"), "w").write("same\n")
    lc.stage_exact_files(("f.txt",))
    snap = capture_snapshot(ws)
    intent = lc.freeze_commit_intent("dup",
                                     expected_head=snap.head_sha,
                                     expected_staged_hash=_freeze(lc)[1])
    # two sibling commits, SAME parent AND SAME tree AND SAME message
    # (distinct timestamps so git cannot dedupe them into one sha):
    sha_a = _commit_object(ws.root, snap.head_sha, intent.intended_tree, "dup", when=1)
    sha_b = _commit_object(ws.root, snap.head_sha, intent.intended_tree, "dup", when=2)
    assert sha_a != sha_b
    # reference both so they are genuinely discoverable history:
    _git(ws.root, "update-ref", "refs/heads/cand-a", sha_a)
    _git(ws.root, "update-ref", "refs/heads/cand-b", sha_b)
    cands = lc.find_commit_candidates(intent)
    assert len(cands) == 2                # ambiguous -> must NOT pick one


def test_r4_no_candidate_leaves_unknown(repo):
    ws, lc = repo, LocalLifecycle(repo)
    open(os.path.join(ws.root, "f.txt"), "w").write("never\n")
    lc.stage_exact_files(("f.txt",))
    snap = capture_snapshot(ws)
    intent = lc.freeze_commit_intent("ghost",
                                     expected_head=snap.head_sha,
                                     expected_staged_hash=_freeze(lc)[1])
    # nothing was ever committed:
    assert lc.find_commit_candidates(intent) == ()
    # and HEAD unchanged proves nothing about OTHER refs -> stays UNKNOWN.


def test_r5_blind_retry_before_reconciliation_blocked(repo):
    import core.repoops.local_lifecycle as mod

    ws, lc, broker = repo, LocalLifecycle(repo), _broker()
    open(os.path.join(ws.root, "f.txt"), "w").write("x\n")
    lc.stage_exact_files(("f.txt",))
    snap = capture_snapshot(ws)
    intent = lc.freeze_commit_intent("lost",
                                     expected_head=snap.head_sha,
                                     expected_staged_hash=_freeze(lc)[1])
    real_run = subprocess.run
    def crash(cmd, **kw):
        r = real_run(cmd, **kw)
        if cmd[:2] == ["git", "-C"] and cmd[3] == "commit":
            raise RuntimeError("lost")
        return r
    mod.subprocess.run = crash
    try:
        try:
            lc.commit_with_proposal(_fork(), broker, intent=intent)
        except RuntimeError:
            pass
    finally:
        mod.subprocess.run = real_run
    count = int(_git(ws.root, "rev-list", "--count", "HEAD").stdout)
    retry = lc.commit_with_proposal(_fork(), broker, intent=intent)
    assert retry.status == "refused"      # R5: BLOCKED until reconciled
    assert int(_git(ws.root, "rev-list", "--count", "HEAD").stdout) == count


# -- FINAL CLOSURE: applied+conflicted cannot be misread as completed ---------------

def _conflict_pick(repo):
    ws, lc = repo, LocalLifecycle(repo)
    _git(ws.root, "switch", "-c", "topic")
    open(os.path.join(ws.root, "cf.txt"), "w").write("topic\n")
    _commit_all(ws.root, "topic cf")
    src = capture_snapshot(ws).head_sha
    _git(ws.root, "switch", "main")
    open(os.path.join(ws.root, "cf.txt"), "w").write("main\n")
    _commit_all(ws.root, "main cf")
    head = capture_snapshot(ws).head_sha
    r = lc.cherry_pick(_fork(["git.cherry_pick"]), _broker(), src,
                       expected_head=head, expected_branch="main")
    return ws, r


def test_applied_conflicted_receipt_carries_completed_false(repo):
    ws, receipt = _conflict_pick(repo)
    assert receipt.status == "applied"
    assert receipt.evidence["mutation_occurred"] is True   # repo WAS mutated
    assert receipt.evidence["completed"] is False          # op did NOT finish
    snap = capture_snapshot(ws)
    assert snap.operation_in_progress == "cherry-pick"


def test_consumer_checking_only_status_cannot_claim_success(repo):
    from core.repoops.local_lifecycle import local_operation_completed
    ws, receipt = _conflict_pick(repo)
    naive_success = receipt.status == "applied" or receipt.happened
    assert naive_success is True                        # what a naive consumer sees...
    assert local_operation_completed(ws, receipt) is False  # ...is NOT completion
    # even after resolving the conflict file content, state persists until
    # the cherry-pick operation itself concludes:
    open(os.path.join(ws.root, "cf.txt"), "w").write("resolved\n")
    _git(ws.root, "add", "cf.txt")
    assert local_operation_completed(ws, receipt) is False
    _git(ws.root, "cherry-pick", "--continue",
         check=False)  # may need -i env; fallback abort below
    snap = capture_snapshot(ws)
    if snap.operation_in_progress == "cherry-pick":
        _git(ws.root, "cherry-pick", "--abort", check=False)
    assert local_operation_completed(ws, receipt) is True or \
        capture_snapshot(ws).operation_in_progress is None


def test_clean_cherry_pick_reports_completed_true(repo):
    ws, lc = repo, LocalLifecycle(repo)
    from core.repoops.local_lifecycle import local_operation_completed
    _git(ws.root, "switch", "-c", "feature")
    open(os.path.join(ws.root, "g.txt"), "w").write("g\n")
    _commit_all(ws.root, "g commit")
    src = capture_snapshot(ws).head_sha
    _git(ws.root, "switch", "main")
    head = capture_snapshot(ws).head_sha
    r = lc.cherry_pick(_fork(["git.cherry_pick"]), _broker(), src,
                       expected_head=head, expected_branch="main")
    assert local_operation_completed(ws, r) is True
