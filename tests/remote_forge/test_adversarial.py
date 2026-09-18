"""Repository IDENTITY and STATE truth — the vocabulary half of the old remote-forge package.

The authority half of this pack is gone with the modules it tested. `core/remote_forge/gate.py`
and `core/remote_forge/execute.py` were a second permission authority and a second
effect/receipt/finality authority, and `GitHubAdapter` was a raw socket to api.github.com holding
an environment credential. All three are deleted; VOOL answers those questions once.

WHERE EACH DELETED PROPERTY IS PROVEN NOW — nothing was dropped, it moved:

  credential is not permission            -> tests/kas/test_kas_law.py
                                             (an adapter naming a binding never holds a secret;
                                              one setting its own Authorization header is refused)
  an unauthorized mutation never runs     -> tests/repoops/test_repoops_adversarial.py
                                             (push without an authorization; force-push and branch
                                              deletion denied by default; the remote does not move)
  a failure is recorded as failed         -> test_the_unknown_push_reconciles_from_what_the_remote_says
  UNKNOWN is not FAILED                   -> tests/repoops/test_repoops_unknown_effect.py
  a retry after UNKNOWN is refused        -> test_a_severed_push_is_unknown_not_failed_and_blocks_an_identical_retry
  a duplicate request does not re-execute -> the same test (a second session is blocked by the
                                             durable reservation, not by process memory)
  a mutation against a moved head is      -> test_a_repository_that_moved_underneath_the_session...
    rejected                                 and test_a_plan_that_changed_after_consent...
  no claim of a push without a receipt    -> test_a_simulated_push_never_moves_the_remote_and_claims_nothing
                                             and repo.verify_remote reading the remote itself

What remains here is what still exists: identities that never collide, and local-versus-remote
truth that fails closed when it is stale.
"""

from __future__ import annotations

import pytest

from core.remote_forge.identity import (
    IdentityError,
    RepoIdentity,
    explicit_identity,
    parse_remote_url,
)
from core.remote_forge.state import (
    IdentityMismatchError,
    LocalTruth,
    RemoteBranch,
    RepoSnapshot,
    StaleStateError,
    compare_local_to_remote,
)


def make_identity(owner: str = "acme", repo: str = "api") -> RepoIdentity:
    return explicit_identity("github", owner, repo)


def make_snapshot(
    identity: RepoIdentity | None = None,
    *,
    local_sha: str = "a" * 40,
    branch: str = "main",
    remote_sha: str = "b" * 40,
    dirty: bool = False,
    detached: bool = False,
) -> RepoSnapshot:
    ident = identity or make_identity()
    return RepoSnapshot(
        identity=ident,
        local=LocalTruth(head_sha=local_sha, branch=None if detached else branch, dirty=dirty),
        branches=(RemoteBranch(branch=branch, head_sha=remote_sha, fetched_at=1.0),),
        default_branch=branch,
    )


# ------------------------------------------------------------------ identity


def test_similar_name_is_a_different_repo():
    """Wrong repo with similar name: identities never collide."""
    api = make_identity("acme", "api")
    api_internal = make_identity("acme", "api-internal")
    snapshot = make_snapshot(api)
    with pytest.raises(IdentityMismatchError):
        from core.remote_forge.state import assert_same_repo

        assert_same_repo(snapshot, api_internal)


def test_ssh_and_https_urls_resolve_to_same_key():
    ssh = parse_remote_url("git@github.com:acme/api.git")
    https = parse_remote_url("https://github.com/acme/api")
    assert ssh.key() == https.key()
    assert ssh.provider == "github"


def test_garbage_url_refused():
    with pytest.raises(IdentityError):
        parse_remote_url("acme api maybe?")


def test_fork_is_not_upstream():
    upstream = make_identity("acme", "api")
    fork = make_identity("contributor", "api")
    assert upstream.key() != fork.key()


# ------------------------------------------------------------- local/remote truth


def test_local_head_is_not_remote_head():
    snapshot = make_snapshot()
    assert snapshot.local.head_sha != snapshot.branch("main").head_sha


def test_detached_head_represented():
    snapshot = make_snapshot(detached=True)
    assert snapshot.local.detached
    assert snapshot.local.branch is None


def test_dirty_worktree_flagged():
    assert make_snapshot(dirty=True).local.dirty is True


def test_stale_snapshot_fails_closed():
    import time as _time

    stale = RepoSnapshot(
        identity=make_identity(),
        fetched_at=_time.time() - 9999,
        branches=(RemoteBranch(branch="main", head_sha="b" * 40, fetched_at=0.0),),
    )
    with pytest.raises(StaleStateError):
        stale.require_fresh(max_age_seconds=120.0)


def test_divergence_statement_names_both_shas():
    local = LocalTruth(head_sha="a" * 40, branch="feature", dirty=False, ahead=2, behind=3)
    remote = RemoteBranch(branch="main", head_sha="b" * 40, fetched_at=1.0)
    report = compare_local_to_remote(local, remote)
    assert report["relation"] == "diverged"
    assert report["local_head"] == "a" * 40 and report["remote_head"] == "b" * 40
