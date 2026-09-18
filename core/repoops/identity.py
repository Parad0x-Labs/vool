"""RepositoryWorkspace identity: WHICH checkout of WHICH repo, exactly.

A workspace is a PAIR: the local checkout (absolute root path + its git truth)
and the remote repository it tracks (full RepoRef from core.remote_forge,
including numeric id and fork parent). Neither half is derivable from the
other: two checkouts of one repo are different workspaces, and a renamed or
transferred repo is a different RepoRef even at the same URL.

No fuzzy names. Every downstream operation carries the workspace identity and
refuses on mismatch.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass

from core.remote_forge.identity import RepoIdentity


class WorkspaceError(RuntimeError):
    pass


def _git(root: str, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", root, *args], capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        raise WorkspaceError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


@dataclass(frozen=True)
class LocalState:
    """Git's own report of this checkout — never inferred."""

    head_sha: str
    branch: str | None          # None == detached HEAD
    dirty: bool
    changed_paths: tuple[str, ...] = ()

    @property
    def detached(self) -> bool:
        return self.branch is None


@dataclass(frozen=True)
class RepositoryWorkspace:
    """The exact pairing of local checkout to remote repository identity."""

    root: str                    # absolute, resolved path — never relative
    repo: RepoIdentity | None = None   # None until attached/cloned/created
    upstream_branch: str = ""    # e.g. 'origin/main'; empty when untracked

    @property
    def key(self) -> str:
        repo_key = self.repo.key() if self.repo is not None else "unbound"
        return f"{repo_key}@{self.root}"

    def read_local_state(self) -> LocalState:
        sha = _git(self.root, "rev-parse", "HEAD")
        proc = subprocess.run(
            ["git", "-C", self.root, "symbolic-ref", "--quiet", "--short", "HEAD"],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode == 1:
            symbolic = ""        # detached: exit 1, no output
        elif proc.returncode != 0:
            raise WorkspaceError(f"symbolic-ref failed: {proc.stderr.strip()}")
        else:
            symbolic = proc.stdout.strip()
        status = _git(self.root, "status", "--porcelain")
        paths = tuple(sorted(
            line.split(None, 1)[1] for line in status.splitlines() if line.strip()
        ))
        return LocalState(
            head_sha=sha,
            branch=symbolic or None,
            dirty=bool(paths),
            changed_paths=paths,
        )

    def require_same_repo(self, other: RepositoryWorkspace) -> None:
        """A second similar checkout is a DIFFERENT workspace; refuse substitution."""
        if self.key != other.key:
            raise WorkspaceError(
                f"workspace mismatch: bound to {self.key}, got {other.key}"
            )
