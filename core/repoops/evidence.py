"""RepositoryEvidence: ONE frozen bundle every council member reasons over.

The point is anti-fragmentation: builder, reviewer, challenger and judge all
receive THIS object (or a strictly newer one) — never private copies of remote
truth. It references receipts rather than duplicating effect data.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from core.remote_forge.state import RepoSnapshot
from core.repoops.ci import CiObservation
from core.repoops.identity import LocalState, RepositoryWorkspace


@dataclass(frozen=True)
class LocalTestResult:
    """LOCAL pytest/lint/build truth — a DIFFERENT fact from remote CI."""

    suite: str
    passed: bool                 # exit status owns this; skip != pass
    sha: str                     # the tree the tests ran against
    ran_at: float = field(default_factory=time.time)
    failed_tests: tuple[str, ...] = ()
    skipped_count: int = 0


@dataclass(frozen=True)
class DiffIdentity:
    """A diff bound by hash — commit messages and reviews cite THIS."""

    files: tuple[str, ...]
    diff_hash: str               # sha256 over the canonical patch text


@dataclass(frozen=True)
class RepositoryEvidence:
    """The shared reality of one repository at one moment."""

    workspace: RepositoryWorkspace
    local: LocalState
    snapshot: RepoSnapshot | None = None          # fetched remote truth (forge A model)
    ci: CiObservation | None = None
    local_tests: tuple[LocalTestResult, ...] = ()
    diff: DiffIdentity | None = None
    receipt_keys: tuple[str, ...] = ()            # refs into the platform journal
    assembled_at: float = field(default_factory=time.time)

    @property
    def key(self) -> str:
        return self.workspace.key

    def require_fresh(self, *, max_age_seconds: float = 120.0) -> RepositoryEvidence:
        if time.time() - self.assembled_at > max_age_seconds:
            raise StaleEvidenceError(
                f"evidence for {self.key} is stale; re-fetch before acting"
            )
        return self

    def newer_than(self, other: RepositoryEvidence) -> bool:
        return self.assembled_at > other.assembled_at


class StaleEvidenceError(RuntimeError):
    pass
