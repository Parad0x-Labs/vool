"""LOCAL/REMOTE truth: what the forge last confirmed, bound to SHAs and time.

The failures this closes (each a measured agent failure mode):
- assuming local HEAD == GitHub HEAD            -> LOCAL_HEAD and REMOTE_BRANCH_HEAD
                                                   are distinct fields, never merged;
- reviewing stale PR content / stale CI         -> every remote read carries
                                                   `fetched_at` and `head_sha`;
                                                   `require_fresh` refuses a read
                                                   older than its max age;
- acting on a repo with a similar name          -> the snapshot carries the full
                                                   RepoIdentity and actions must
                                                   match it exactly.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from core.remote_forge.identity import RepoIdentity

# Distinct truth slots. A caller that wants "the branch state" must NAME the slot;
# there is no accessor that silently falls back from one to another.
LOCAL_HEAD = "local_head"
LOCAL_WORKTREE = "local_worktree"
REMOTE_BRANCH_HEAD = "remote_branch_head"
PR_HEAD = "pr_head"
PR_BASE = "pr_base"
FETCHED_REMOTE_STATE = "fetched_remote_state"


class StaleStateError(RuntimeError):
    """A read is too old to act on. Fail closed — refresh, never assume."""


class IdentityMismatchError(RuntimeError):
    """A state object does not belong to the repository the action targets."""


@dataclass(frozen=True)
class LocalTruth:
    """The local working copy as git itself reports it — never inferred."""

    head_sha: str
    branch: str | None  # None == detached HEAD
    dirty: bool
    ahead: int = 0
    behind: int = 0

    @property
    def detached(self) -> bool:
        return self.branch is None


@dataclass(frozen=True)
class RemoteBranch:
    branch: str
    head_sha: str
    fetched_at: float


@dataclass(frozen=True)
class PullRequest:
    number: int
    title: str
    base_branch: str
    base_sha: str
    head_branch: str
    head_sha: str
    state: str  # open | closed | merged
    ci_status: str  # pending | passing | failing
    fetched_at: float
    upstream_owner: str = ""  # set when head lives on a fork
    fork_owner: str = ""


@dataclass(frozen=True)
class RepoSnapshot:
    """One canonical view of local + remote truth at a moment in time.

    Council members share THIS object; none of them maintains a private
    version of remote truth (a conflicting private version is unrepresentable
    here — the snapshot is frozen).
    """

    identity: RepoIdentity
    fetched_at: float = field(default_factory=time.time)
    local: LocalTruth | None = None
    branches: tuple[RemoteBranch, ...] = ()
    pull_requests: tuple[PullRequest, ...] = ()
    default_branch: str = ""

    def branch(self, name: str) -> RemoteBranch:
        for b in self.branches:
            if b.branch == name:
                return b
        raise KeyError(f"branch {name!r} not in snapshot of {self.identity.slug()}")

    def pr(self, number: int) -> PullRequest:
        for pr in self.pull_requests:
            if pr.number == number:
                return pr
        raise KeyError(f"PR #{number} not in snapshot of {self.identity.slug()}")

    def require_fresh(self, *, max_age_seconds: float = 120.0) -> RepoSnapshot:
        """Refuse to serve stale truth. Returns an equivalent snapshot or raises."""
        if time.time() - self.fetched_at > max_age_seconds:
            raise StaleStateError(
                f"snapshot of {self.identity.slug()} fetched "
                f"{time.time() - self.fetched_at:.0f}s ago (> {max_age_seconds:.0f}s); refresh it"
            )
        return self


def assert_same_repo(snapshot: RepoSnapshot, identity: RepoIdentity) -> None:
    """Every action passes through this: state must belong to the exact repo."""
    if snapshot.identity.key() != identity.key():
        raise IdentityMismatchError(
            f"state belongs to {snapshot.identity.slug()} "
            f"({snapshot.identity.remote_url}) but action targets "
            f"{identity.slug()} ({identity.remote_url})"
        )


def compare_local_to_remote(local: LocalTruth, remote: RemoteBranch) -> dict[str, str]:
    """Honest divergence statement between two named slots. Never mutates."""
    if local.head_sha == remote.head_sha:
        relation = "in_sync"
    elif remote.head_sha.startswith(local.head_sha) and local.ahead == 0:
        relation = "local_behind_remote"
    else:
        relation = "diverged" if (local.ahead and local.behind) else "differs"
    return {
        "relation": relation,
        "local_head": local.head_sha,
        "remote_head": remote.head_sha,
        "dirty_worktree": str(local.dirty).lower(),
    }
