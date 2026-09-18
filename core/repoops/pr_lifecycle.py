"""PR lifecycle: inspect -> diff -> review evidence -> merge -> verify.

Laws this module enforces (consuming, never re-implementing, the platform
authority in core.platform.broker):

1. EVERY DECISION NAMES ITS SNAPSHOT. Merge proposals bind expected head AND
   base SHAs; a stale snapshot is refused before any dispatch.
2. PR TEXT IS UNTRUSTED DATA. Descriptions/comments/review bodies are ingested
   as kernel TaintedValue — instruction-shaped text keeps provenance and can
   never become platform authority. (Known limit: explicit str() laundering is
   NOT solved here; that is generic kernel taint work owned elsewhere.)
3. A PROVIDER APPROVAL IS NOT VOOL PERMISSION. Review observations are
   evidence; only the platform AuthorizationDecision admits the merge effect.
4. MERGED MEANS VERIFIED. Provider success alone never earns the word; a
   post-merge provider re-read must agree with the receipt.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field

from core.kernel.capabilities import TaintedValue, is_tainted
from core.platform.broker import EffectOutcome, EffectRequest, ExecutionBroker
from core.remote_forge.identity import RepoIdentity

MERGE_STRATEGIES = ("merge", "squash", "rebase")


# ---------------------------------------------------------------- snapshots


@dataclass(frozen=True)
class ReviewObservation:
    """One review as OBSERVED from the provider. Evidence, not authority."""

    author: str
    state: str                    # approved | changes_requested | commented | dismissed | pending
    commit_sha: str               # the head SHA this review was OF
    submitted_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class PRSnapshot:
    identity: RepoIdentity
    number: int
    head_repo_key: str            # differs from base for fork-head PRs; "" when deleted
    head_branch: str              # "" when the fork/head branch was deleted
    head_sha: str
    base_branch: str
    base_sha: str
    merge_base: str = ""
    draft: bool = False
    author: str = ""
    state: str = "open"           # open | closed | merged
    reviews: tuple[ReviewObservation, ...] = ()
    requested_reviewers: tuple[str, ...] = ()
    required_checks: tuple[str, ...] = ()
    fetched_at: float = field(default_factory=time.time)

    def require_for(self, *, head_sha: str, base_sha: str) -> PRSnapshot:
        if self.head_sha != head_sha or self.base_sha != base_sha:
            raise StalePrEvidence(
                f"snapshot of PR #{self.number} is head={self.head_sha[:12]}/"
                f"base={self.base_sha[:12]}, asked head={head_sha[:12]}/base={base_sha[:12]}"
                f"{' (BASE_MOVED)' if self.head_sha == head_sha else ''}"
                f"{' (HEAD_MOVED)' if self.base_sha == base_sha else ''}"
                " — REVALIDATION_REQUIRED"
            )
        return self

    def require_fresh(self, *, max_age_seconds: float = 120.0) -> PRSnapshot:
        if time.time() - self.fetched_at > max_age_seconds:
            raise StalePrEvidence(f"PR #{self.number} snapshot is stale; re-fetch")
        return self


@dataclass(frozen=True)
class FileChange:
    path: str
    status: str                   # added | deleted | modified | renamed
    is_binary: bool = False
    is_generated: bool = False


@dataclass(frozen=True)
class PRDiff:
    identity: RepoIdentity
    pr_number: int
    head_sha: str
    base_sha: str
    files: tuple[FileChange, ...]
    patch_text: TaintedValue | None = None   # provider text stays untrusted data
    fetched_at: float = field(default_factory=time.time)

    @property
    def diff_hash(self) -> str:
        canonical = json.dumps(
            [(f.path, f.status, f.is_binary) for f in self.files], sort_keys=True)
        return hashlib.sha256(
            f"{self.head_sha}:{self.base_sha}:{canonical}".encode()).hexdigest()[:16]

    def require_for_head(self, head_sha: str) -> PRDiff:
        if self.head_sha != head_sha:
            raise StalePrEvidence(
                f"diff of PR #{self.pr_number} is for head {self.head_sha[:12]}, "
                f"not {head_sha[:12]} — never review B using A's diff"
            )
        return self


class StalePrEvidence(RuntimeError):
    pass


# ---------------------------------------------------------------- untrusted text


def untrusted_pr_text(text: str, origin: str) -> TaintedValue:
    """Ingest ANY provider text (description/comment/review body) as tainted."""
    value = TaintedValue(str(text or ""), origin)
    assert is_tainted(value)      # structural guarantee at ingestion
    return value


# ---------------------------------------------------------------- mergeability


@dataclass(frozen=True)
class MergeReadiness:
    """Explicit evidence checklist. RepoOps invents NO organization policy;
    every requirement below is observed provider/platform fact."""

    pr_number: int
    expected_head_sha: str
    expected_base_sha: str
    strategy: str
    checks: dict[str, bool] = field(default_factory=dict)
    blockers: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.blockers


def assess_mergeability(
    *,
    snapshot: PRSnapshot,
    ci_green_for_head: bool,
    unresolved_changes_requested_against_head: bool,
    provider_mergeable: bool,
    expected_head_sha: str,
    expected_base_sha: str,
    strategy: str,
) -> MergeReadiness:
    if strategy not in MERGE_STRATEGIES:
        raise ValueError(f"unknown merge strategy {strategy!r}")
    checks = {
        "snapshot_matches_expected": True,
        "head_is_current": snapshot.head_sha == expected_head_sha,
        "base_is_current": snapshot.base_sha == expected_base_sha,
        "required_ci_green_for_this_head": ci_green_for_head,
        "no_open_changes_requested": not unresolved_changes_requested_against_head,
        "not_draft": not snapshot.draft,
        "provider_mergeable_no_conflicts": provider_mergeable,
    }
    blockers = tuple(name for name, ok in checks.items() if not ok)
    return MergeReadiness(pr_number=snapshot.number,
                          expected_head_sha=expected_head_sha,
                          expected_base_sha=expected_base_sha,
                          strategy=strategy, checks=checks, blockers=blockers)


def unresolved_changes_requested(snapshot: PRSnapshot) -> bool:
    """A changes_requested review counts while it targets the CURRENT head and
    was not superseded by a later approval from the same author."""
    latest: dict[str, ReviewObservation] = {}
    for r in sorted(snapshot.reviews, key=lambda r: r.submitted_at):
        if r.commit_sha == snapshot.head_sha:
            latest[r.author] = r
    return any(r.state == "changes_requested" for r in latest.values())


def review_from_obsolete_commit(snapshot: PRSnapshot) -> bool:
    return any(r.commit_sha != snapshot.head_sha
               for r in snapshot.reviews if r.state == "approved")


# ---------------------------------------------------------------- typed mutations


@dataclass(frozen=True)
class RequestReviewer:
    number: int
    reviewer: str
    permission: str = "review"


@dataclass(frozen=True)
class SubmitReview:
    number: int
    verdict: str                  # approve | request_changes | comment
    head_sha_reviewed: str        # reviews bind to the commit they inspected
    body: str = ""
    permission: str = "review"


@dataclass(frozen=True)
class CommentOnPR:
    number: int
    body: str
    permission: str = "comment"


@dataclass(frozen=True)
class UpdatePRMeta:
    number: int
    new_title: str = ""
    new_body: str = ""
    draft: bool | None = None     # draft<->ready transitions ride here
    permission: str = "update_pr"


@dataclass(frozen=True)
class ClosePR:
    number: int
    permission: str = "update_pr"


@dataclass(frozen=True)
class ReopenPR:
    number: int
    permission: str = "update_pr"


@dataclass(frozen=True)
class MergeProposal:
    """Binds repo + PR + expected head/base + strategy. Changing the strategy
    changes the mutation identity (idempotency key), never silently."""

    number: int
    expected_head_sha: str
    expected_base_sha: str
    strategy: str                 # merge | squash | rebase
    permission: str = "merge"

    def idempotency_key(self) -> str:
        payload = json.dumps({
            "op": "merge_pr", "strategy": self.strategy,
            "head": self.expected_head_sha, "base": self.expected_base_sha,
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:32]


# ---------------------------------------------------------------- sandbox provider


@dataclass
class _FakePR:
    number: int
    head_branch: str
    head_sha: str
    base_branch: str
    base_sha: str
    state: str = "open"
    draft: bool = False
    mergeable: bool = True
    merge_commit_sha: str = ""
    head_deleted: bool = False


class FakePRProvider:
    """Sandbox GitHub stand-in. Deterministic faults; honest post-merge reads."""

    def __init__(self) -> None:
        self.prs: dict[int, _FakePR] = {}
        self.faults: dict[str, list] = {}

    def seed(self, pr: _FakePR) -> _FakePR:
        self.prs[pr.number] = pr
        return pr

    def inject_fault(self, op: str, fault) -> None:
        self.faults.setdefault(op, []).append(fault)

    def get_snapshot(self, identity: RepoIdentity, number: int,
                     required_checks: tuple[str, ...] = ()) -> PRSnapshot:
        pr = self.prs[number]
        return PRSnapshot(
            identity=identity, number=number,
            head_repo_key="" if pr.head_deleted else f"{identity.slug()}+head",
            head_branch="" if pr.head_deleted else pr.head_branch,
            head_sha=pr.head_sha, base_branch=pr.base_branch,
            base_sha=pr.base_sha, draft=pr.draft, state=pr.state,
            required_checks=required_checks)

    def get_merge_state(self, number: int) -> tuple[str, str]:
        """(state, merge_commit_sha) — the post-merge re-read surface."""
        pr = self.prs[number]
        return pr.state, pr.merge_commit_sha

    def move_head(self, number: int, new_sha: str) -> None:
        self.prs[number].head_sha = new_sha

    def move_base(self, number: int, new_sha: str) -> None:
        self.prs[number].base_sha = new_sha

    # -- mutations -----------------------------------------------------------------
    def execute(self, action) -> EffectOutcome:
        fault = (self.faults.get("merge" if isinstance(action, MergeProposal)
                                 else type(action).__name__.lower()) or [None])[0]
        op = "merge" if isinstance(action, MergeProposal) else "pr_mutation"
        if fault == "unknown":
            self.faults[op].pop(0)
            return EffectOutcome(status="unknown",
                                 reason="dispatched; outcome unknowable")

        pr = self.prs[action.number]
        if isinstance(action, MergeProposal):
            if pr.head_sha != action.expected_head_sha:
                return EffectOutcome.from_domain(
                    "rejected", reason="stale_head",
                    evidence={"current": pr.head_sha,
                              "expected": action.expected_head_sha})
            if pr.base_sha != action.expected_base_sha:
                return EffectOutcome.from_domain(
                    "rejected", reason="base_moved",
                    evidence={"current": pr.base_sha,
                              "expected": action.expected_base_sha})
            if not pr.mergeable:
                return EffectOutcome.from_domain("rejected", reason="conflicts")
            pr.state = "merged"
            pr.merge_commit_sha = hashlib.sha256(
                f"{action.strategy}:{pr.head_sha}".encode()).hexdigest() * 2
            return EffectOutcome.from_domain(
                "created", reason="merged",
                evidence={"merge_commit_sha": pr.merge_commit_sha,
                          "strategy": action.strategy,
                          "merged_head": pr.head_sha})

        if isinstance(action, SubmitReview):
            return EffectOutcome.from_domain(
                "created", reason="review_submitted",
                evidence={"verdict": action.verdict,
                          "of_commit": action.head_sha_reviewed})
        if isinstance(action, CommentOnPR):
            return EffectOutcome.from_domain("created", reason="commented")
        if isinstance(action, RequestReviewer):
            return EffectOutcome.from_domain("created", reason="reviewer_requested")
        if isinstance(action, ClosePR):
            if pr.state == "open":
                pr.state = "closed"
            return EffectOutcome.from_domain("ok", reason="closed",
                                             evidence={"state": pr.state})
        if isinstance(action, ReopenPR):
            if pr.state == "closed":
                pr.state = "open"
            return EffectOutcome.from_domain("ok", reason="reopened",
                                             evidence={"state": pr.state})
        if isinstance(action, UpdatePRMeta):
            if action.draft is not None:
                pr.draft = action.draft
            return EffectOutcome.from_domain("ok", reason="updated")
        raise ValueError(f"unhandled PR action {type(action).__name__}")


# ---------------------------------------------------------------- authorized mutations


def propose_merge(actor_fork, broker: ExecutionBroker, provider: FakePRProvider,
                  proposal: MergeProposal):
    """One door: assess nothing here — callers pass current evidence via the
    proposal's bound SHAs; the provider refuses on mismatch, the broker records."""
    request = EffectRequest(effect_id="forge.pr.merge", required_capability="merge",
                            params={"number": proposal.number,
                                    "expected_head": proposal.expected_head_sha,
                                    "expected_base": proposal.expected_base_sha,
                                    "strategy": proposal.strategy},
                            idempotency_key=proposal.idempotency_key())
    return broker.execute(actor_fork, request,
                          lambda: provider.execute(proposal))


def pr_mutation(actor_fork, broker: ExecutionBroker, provider: FakePRProvider,
                token: str, action) :
    request = EffectRequest(
        effect_id=f"forge.pr.{type(action).__name__.lower()}",
        required_capability=token, params={"number": action.number},
        idempotency_key=hashlib.sha256(json.dumps({
            "kind": type(action).__name__, **{k: getattr(action, k)
            for k in ("number", "verdict", "body", "reviewer", "new_title",
                      "draft") if hasattr(action, k)},
        }, sort_keys=True).encode()).hexdigest()[:32])
    return broker.execute(actor_fork, request,
                          lambda: provider.execute(action))


# ---------------------------------------------------------------- post-merge verification


MERGED = "merged"


@dataclass(frozen=True)
class PostMergeVerification:
    verified_merged: bool
    discrepancy: str = ""
    merge_commit_sha: str = ""
    base_state_note: str = ""


def verify_merge(provider: FakePRProvider, identity: RepoIdentity, number: int,
                 receipt, strategy: str, base_branch: str,
                 base_branch_current_sha: str) -> PostMergeVerification:
    """Provider said applied? PROVE IT by reading provider state again.

    Receipt truth is historical and untouched; verification either agrees or
    surfaces a DISCREPANCY — it never rewrites what the tape recorded.
    """
    if receipt.status == "unknown":
        return PostMergeVerification(False,
                                     discrepancy="UNKNOWN outcome; reconcile first")
    if receipt.status != "applied":
        return PostMergeVerification(False,
                                     discrepancy=f"receipt says {receipt.status}")

    fresh_state, provider_merge_sha = provider.get_merge_state(number)
    evidence_sha = str(receipt.evidence.get("merge_commit_sha", ""))
    if fresh_state != MERGED:
        return PostMergeVerification(
            False, discrepancy=(
                f"DISCREPANCY: receipt says applied but provider shows "
                f"PR #{number} in state {fresh_state!r}; tape unchanged"),
            merge_commit_sha=evidence_sha)
    if provider_merge_sha != evidence_sha:
        return PostMergeVerification(
            False, discrepancy=(
                "DISCREPANCY: receipt sha != provider merge commit"),
            merge_commit_sha=evidence_sha)
    note = {
        "merge": "merge commit on base contains head history",
        "squash": f"squash commit {provider_merge_sha[:12]} is a NEW commit; "
                  f"head history flattened",
        "rebase": "rebased commits rewritten onto base; original head SHAs differ",
    }[strategy]
    del base_branch_current_sha  # base movement is a separate observation
    return PostMergeVerification(True, merge_commit_sha=evidence_sha,
                                 base_state_note=note)


def fresh_sha_of(pr) -> str:
    return pr.head_sha


__all__ = [
    "MERGED",
    "MERGE_STRATEGIES",
    "ClosePR",
    "CommentOnPR",
    "FakePRProvider",
    "FileChange",
    "MergeProposal",
    "MergeReadiness",
    "PRDiff",
    "PRSnapshot",
    "PostMergeVerification",
    "ReopenPR",
    "RequestReviewer",
    "ReviewObservation",
    "StalePrEvidence",
    "SubmitReview",
    "UpdatePRMeta",
    "assess_mergeability",
    "pr_mutation",
    "propose_merge",
    "review_from_obsolete_commit",
    "unresolved_changes_requested",
    "untrusted_pr_text",
    "verify_merge",
]
