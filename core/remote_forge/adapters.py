"""Forge adapters: provider semantics live HERE and nowhere else.

`ForgeAdapter` is the boundary the semantic kernel knows. GitHub-specific
concepts (REST routes, check-runs, mergeability) are translated inside
`GitHubAdapter`; `FakeGitHubAdapter` implements the same interface for tests,
including deterministic failure injection so adversarial cases are provable
without touching any real remote.

Results are three-valued ON PURPOSE:

  SUCCEEDED — evidence proves the remote state changed (SHAs returned).
  FAILED    — the forge answered "no" (rejected push, PR conflict...). The
              world is KNOWN unchanged.
  UNKNOWN   — we dispatched but cannot know whether it landed (timeout after
              send, connection dropped mid-response). Fail closed: never a
              success claim, never a blind retry. Reconciliation required.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from core.remote_forge.actions import (
    CreateBranch,
    CreatePR,
    ForgeAction,
    MergePR,
    PushBranch,
)
from core.remote_forge.identity import RepoIdentity

SUCCEEDED = "succeeded"
FAILED = "failed"
UNKNOWN = "unknown"

# Statuses an adapter may return.
RESULT_STATUSES = frozenset({SUCCEEDED, FAILED, UNKNOWN})


class UnknownOutcomeError(RuntimeError):
    """Raised by an adapter when the outcome of a dispatched mutation cannot be
    determined. Kernel Law 4 records this as EFFECT_UNKNOWN; execute.py turns it
    into an UNKNOWN receipt. Never caught-and-converted into a failure."""


@dataclass(frozen=True)
class ForgeResult:
    status: str  # SUCCEEDED | FAILED | UNKNOWN
    action_kind: str
    idempotency_key: str
    evidence: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    replayed: bool = False  # True when an idempotent retry returned the prior receipt


@dataclass(frozen=True)
class FetchOutcome:
    """A remote read. `at` lets state.py stamp fetched_at honestly."""

    identity: RepoIdentity
    branches: tuple[dict[str, Any], ...]
    pull_requests: tuple[dict[str, Any], ...]
    default_branch: str
    at: float


class ForgeAdapter(Protocol):
    """The ONLY way the kernel touches a forge. Providers implement this."""

    def fetch_snapshot(self, identity: RepoIdentity) -> FetchOutcome: ...

    def execute(self, action: ForgeAction) -> ForgeResult:
        """Execute ONE authorized mutation. Implementations must be idempotent
        on the action's idempotency key."""
        ...


# --------------------------------------------------------------------- fake forge


@dataclass
class _FakeBranch:
    head_sha: str


class FakeGitHubAdapter:
    """An in-memory GitHub stand-in for write-path tests.

    Deterministic by construction: no network, no sleeps, and injected faults.
    The idempotency ledger mirrors what a real adapter must do — a retry whose
    key already SUCCEEDED returns the original receipt marked replayed=True and
    does NOT re-execute; a retry whose prior attempt was UNKNOWN refuses with
    FAILED/ambiguous rather than re-dispatching (never blindly retry something
    that may already have succeeded).
    """

    def __init__(self) -> None:
        self.branches: dict[tuple[str, str, str], dict[str, _FakeBranch]] = {}
        self.prs: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        self._ledger: dict[tuple[str, str], ForgeResult] = {}
        # Faults keyed by action kind; each consumed once.
        self.faults: dict[str, list[Any]] = {}

    def inject_fault(self, kind: str, fault: Any) -> None:
        self.faults.setdefault(kind, []).append(fault)

    def seed_branch(self, identity: RepoIdentity, branch: str, sha: str) -> None:
        self.branches.setdefault(identity.key(), {})[branch] = _FakeBranch(sha)

    def _take_fault(self, action: ForgeAction) -> Any:
        queue = self.faults.get(action.kind) or []
        return queue.pop(0) if queue else None

    def fetch_snapshot(self, identity: RepoIdentity) -> FetchOutcome:
        store = self.branches.get(identity.key(), {})
        branches = tuple(
            {"branch": name, "head_sha": b.head_sha} for name, b in sorted(store.items())
        )
        prs = tuple(
            dict(pr) for pr in self.prs.get(identity.key(), []) if pr["state"] == "open"
        )
        default = "main" if "main" in store else (branches[0]["branch"] if branches else "")
        return FetchOutcome(
            identity=identity,
            branches=branches,
            pull_requests=prs,
            default_branch=default,
            at=time.time(),
        )

    def execute(self, action: ForgeAction) -> ForgeResult:
        ledger_key = (
            f"{action.identity.provider}:{action.identity.owner}/{action.identity.repo}",
            action.idempotency_key(),
        )
        prior = self._ledger.get(ledger_key)
        if prior is not None:
            if prior.status == SUCCEEDED:
                return ForgeResult(
                    status=SUCCEEDED,
                    action_kind=prior.action_kind,
                    idempotency_key=prior.idempotency_key,
                    evidence=dict(prior.evidence),
                    replayed=True,
                )
            if prior.status == UNKNOWN:
                return ForgeResult(
                    status=FAILED,
                    action_kind=action.kind,
                    idempotency_key=action.idempotency_key(),
                    error=(
                        "prior attempt outcome unknown; refusing to re-dispatch — "
                        "reconcile before retrying"
                    ),
                )

        fault = self._take_fault(action)
        if fault == "unknown":
            result = ForgeResult(
                status=UNKNOWN,
                action_kind=action.kind,
                idempotency_key=action.idempotency_key(),
                error="connection lost after dispatch",
            )
            self._ledger[ledger_key] = result
            raise UnknownOutcomeError("connection lost after dispatch")
        if isinstance(fault, BaseException):
            # An injected forge REJECTION ("push rejected", "PR conflict") is a
            # definitive NO from the forge: the world is known unchanged.
            result = ForgeResult(
                status=FAILED,
                action_kind=action.kind,
                idempotency_key=action.idempotency_key(),
                error=str(fault),
            )
            self._ledger[ledger_key] = result
            return result

        try:
            evidence = self._apply(action)
        except RuntimeError as exc:
            result = ForgeResult(
                status=FAILED,
                action_kind=action.kind,
                idempotency_key=action.idempotency_key(),
                error=str(exc),
            )
            self._ledger[ledger_key] = result
            return result
        result = ForgeResult(
            status=SUCCEEDED,
            action_kind=action.kind,
            idempotency_key=action.idempotency_key(),
            evidence=evidence,
        )
        self._ledger[ledger_key] = result
        return result

    def _apply(self, action: ForgeAction) -> dict[str, Any]:
        store = self.branches.setdefault(action.identity.key(), {})
        if isinstance(action, PushBranch):
            branch = store.get(action.branch)
            if branch is None:
                raise RuntimeError(f"push rejected: branch {action.branch!r} does not exist")
            if not action.force and action.head_sha.startswith(branch.head_sha):
                raise RuntimeError(
                    f"push rejected: non-fast-forward from {branch.head_sha[:8]}"
                )
            branch.head_sha = action.head_sha
            return {"branch": action.branch, "head_sha": action.head_sha}
        if isinstance(action, CreateBranch):
            if action.branch in store:
                raise RuntimeError(f"branch {action.branch!r} already exists")
            store[action.branch] = _FakeBranch(action.from_sha)
            return {"branch": action.branch, "head_sha": action.from_sha}
        if isinstance(action, CreatePR):
            prs = self.prs.setdefault(action.identity.key(), [])
            for existing in prs:
                if (
                    existing["state"] == "open"
                    and existing["head_branch"] == action.head_branch
                    and existing["base_branch"] == action.base_branch
                ):
                    raise RuntimeError(
                        "PR creation rejected: an open PR for "
                        f"{action.head_branch} -> {action.base_branch} already exists "
                        f"(#{existing['number']})"
                    )
            number = 100 + len(prs)
            prs.append({
                "number": number,
                "title": action.title,
                "head_branch": action.head_branch,
                "base_branch": action.base_branch,
                "head_sha": store.get(action.head_branch).head_sha
                if action.head_branch in store else "",
                "state": "open",
                "ci_status": "pending",
            })
            return {"number": number}
        if isinstance(action, MergePR):
            prs = self.prs.setdefault(action.identity.key(), [])
            for existing in prs:
                if existing["number"] != action.number:
                    continue
                if existing["state"] != "open":
                    raise RuntimeError(f"PR #{action.number} is {existing['state']}, not open")
                if existing["head_sha"] != action.expected_head_sha:
                    raise RuntimeError(
                        "merge rejected: PR head moved "
                        f"({existing['head_sha'][:8]} != {action.expected_head_sha[:8]})"
                    )
                existing["state"] = "merged"
                return {"number": action.number, "merged_sha": existing["head_sha"]}
            raise RuntimeError(f"PR #{action.number} not found")
        raise RuntimeError(f"fake adapter does not implement {type(action).__name__}")


# -------------------------------------------------------------------- real github
#
# REMOVED. `GitHubAdapter` lived here and opened its own socket: a raw
# `urllib.request.urlopen` to api.github.com, authenticated from `GITHUB_TOKEN` read straight out
# of the environment. It crossed no egress door, consulted no gateway, emitted no effect receipt
# and held its own credential -- four VOOL authorities duplicated inside an integration module.
#
# The one real GitHub client is now `core.kas.adapters.github.GitHubForgeAdapter`, which
# translates and nothing else: VOOL builds its transport, the transport crosses
# `core.remote_fetch_policy.open_remote`, and the credential is named as an opaque binding the
# adapter never sees. `core.kas.conformance` fails any adapter that reaches for a socket, a
# credential or an authority again.
#
# `FakeGitHubAdapter` above stays as what it always was -- an in-memory double for the two
# sandbox demos and the identity/state tests. It touches no network by construction.

