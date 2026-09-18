"""Local Git lifecycle: snapshot, status truth, exact staging, commits,
restore, worktrees, cherry-pick — every mutation broker-admitted.

Laws enforced here (platform authority in core.platform.broker is consumed,
never re-implemented):

1. SNAPSHOTS EXPIRE BY SHA. A LocalSnapshot taken at SHA A authorizes nothing
   about SHA B; proposals carry expected_head and are validated immediately
   before dispatch.
2. STAGED != UNSTAGED != COMMITTED. Porcelain XY codes are parsed
   mechanically, never inferred from prose.
3. EXACT-STAGING LAW. stage_exact_files stages exactly the requested paths and
   verifies the staged set afterwards — a commit proposed for file A can never
   silently absorb a dirty file B. There is no `git add .` in this module.
4. CONFLICT IS NOT FAILURE-WITH-NO-EFFECT. A cherry-pick that begins and lands
   the repository in a conflicted state records APPLIED with
   evidence["conflicted"]=True — the world DID change; lying otherwise would
   corrupt effect truth.
5. EXIT 0 IS NOT TRUTH. Every consequential mutation re-reads repository
   state afterwards and carries the post-state as evidence.
6. CONFIGURED SIGNING != VERIFIED SIGNING. Signature claims come only from
   %G? inspection of the resulting commit; unknown stays unverifiable.

Amend/rebase/reset/stash do NOT exist as operations here: they are excluded
by design, not forgotten.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, field

from core.platform.broker import EffectOutcome, EffectRequest, ExecutionBroker
from core.repoops.identity import RepositoryWorkspace, WorkspaceError, _git


class LifecycleError(RuntimeError):
    pass


class StaleSnapshotError(LifecycleError):
    pass


class OperationInProgressError(LifecycleError):
    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(
            f"{operation} in progress — resolve it before other mutations"
        )


class StageScopeError(LifecycleError):
    pass


# -- commit intent -----------------------------------------------------------

@dataclass(frozen=True)
class CommitIntent:
    """A frozen commit proposal bound to Git-native content identity.

    HARD LAW: the human message is NOT effect identity. Reconciliation of a
    lost commit response uses expected parent + intended TREE (from
    `git write-tree` over the frozen index) — mechanical, not prose.
    """

    workspace_key: str
    message: str
    expected_head: str          # required parent of the resulting commit
    staged_hash: str            # sha256 of the frozen staged diff (review binding)
    intended_tree: str          # exact tree sha the index will commit


# -- snapshot -------------------------------------------------------------------

_CONFLICT_CODES = set("DDAUUDAAAADUAAUU") # pairs like AA/UU/DD etc.


@dataclass(frozen=True)
class FileStatus:
    path: str
    index_status: str          # X column, ' ' when unchanged in index
    worktree_status: str       # Y column

    @property
    def staged(self) -> bool:
        return self.index_status not in (" ", "?")

    @property
    def unstaged(self) -> bool:
        return self.worktree_status != " " and self.index_status != "?"

    @property
    def untracked(self) -> bool:
        return self.index_status == "?" and self.worktree_status == "?"

    @property
    def deleted(self) -> bool:
        return "D" in (self.index_status, self.worktree_status)

    @property
    def conflicted(self) -> bool:
        return (self.index_status + self.worktree_status) in {
            "DD", "AU", "UD", "UA", "DU", "AA", "UU",
        }


_IN_PROGRESS_MARKERS = {
    ".git/MERGE_HEAD": "merge",
    ".git/CHERRY_PICK_HEAD": "cherry-pick",
    ".git/REVERT_HEAD": "revert",
}


def _detect_in_progress(root: str) -> str | None:
    dotgit = _git(root, "rev-parse", "--absolute-git-dir")
    for marker, op in _IN_PROGRESS_MARKERS.items():
        if os.path.exists(os.path.join(dotgit, os.path.basename(marker))):
            return op
    for d, op in (("rebase-merge", "rebase"), ("rebase-apply", "rebase")):
        if os.path.isdir(os.path.join(dotgit, d)):
            return op
    return None


@dataclass(frozen=True)
class LocalLifecycleSnapshot:
    """Full mechanical truth of one checkout at one instant."""

    workspace_key: str
    head_sha: str
    branch: str | None         # None == detached HEAD
    upstream: str
    entries: tuple[FileStatus, ...]
    operation_in_progress: str | None
    observed_at: float = field(default_factory=lambda: __import__("time").time())

    @property
    def detached(self) -> bool:
        return self.branch is None

    @property
    def staged_paths(self) -> tuple[str, ...]:
        return tuple(e.path for e in self.entries if e.staged)

    @property
    def unstaged_paths(self) -> tuple[str, ...]:
        return tuple(e.path for e in self.entries if e.unstaged)

    @property
    def untracked_paths(self) -> tuple[str, ...]:
        return tuple(e.path for e in self.entries if e.untracked)

    @property
    def conflicted_paths(self) -> tuple[str, ...]:
        return tuple(e.path for e in self.entries if e.conflicted)

    @property
    def dirty(self) -> bool:
        return bool(self.entries)

    def require_for(self, head_sha: str) -> None:
        """A snapshot from SHA A proves nothing about SHA B."""
        if self.head_sha != head_sha:
            raise StaleSnapshotError(
                f"snapshot was taken at {self.head_sha[:12]}, "
                f"HEAD is now {head_sha[:12]} — refresh required"
            )


def capture_snapshot(ws: RepositoryWorkspace) -> LocalLifecycleSnapshot:
    # RAW stdout: porcelain's leading XY columns are significant — a global
    # strip() would eat the first line's index-status space.
    out = subprocess.run(
        ["git", "-C", ws.root, "status", "--porcelain=v1"],
        capture_output=True, text=True, timeout=30,
    ).stdout
    entries = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        x, y, path = line[0], line[1], line[3:]
        if line.startswith('"'):      # quoted path with special chars
            path = line[3:].strip('"')
        entries.append(FileStatus(path=path, index_status=x, worktree_status=y))
    proc = subprocess.run(
        ["git", "-C", ws.root, "symbolic-ref", "--quiet", "--short", "HEAD"],
        capture_output=True, text=True, timeout=30,
    )
    branch = proc.stdout.strip() if proc.returncode == 0 else None
    try:
        upstream = _git(ws.root, "rev-parse", "--abbrev-ref", "@{upstream}")
    except WorkspaceError:
        upstream = ""
    return LocalLifecycleSnapshot(
        workspace_key=ws.key,
        head_sha=_git(ws.root, "rev-parse", "HEAD"),
        branch=branch,
        upstream=upstream,
        entries=tuple(entries),
        operation_in_progress=_detect_in_progress(ws.root),
    )


# -- bound diffs -------------------------------------------------------------

_DIFF_KINDS = {
    "head_to_index": ["--cached"],
    "index_to_worktree": [],
    "head_to_worktree": ["HEAD"],
}


@dataclass(frozen=True)
class BoundDiff:
    """A local diff frozen against an exact HEAD + content hash."""

    kind: str
    head_sha: str
    content_hash: str

    def require_current(self, ws: RepositoryWorkspace) -> None:
        """Recompute NOW: stale means the head moved OR the content changed."""
        now = bound_diff(ws, self.kind)
        if now.head_sha != self.head_sha or now.content_hash != self.content_hash:
            raise StaleSnapshotError(
                f"{self.kind} diff frozen at {self.head_sha[:12]}/"
                f"{self.content_hash}; repository is now "
                f"{now.head_sha[:12]}/{now.content_hash} — review would be stale"
            )


def bound_diff(ws: RepositoryWorkspace, kind: str) -> BoundDiff:
    args = _DIFF_KINDS[kind]
    text = _git(ws.root, "diff", *args)
    return BoundDiff(
        kind=kind,
        head_sha=_git(ws.root, "rev-parse", "HEAD"),
        content_hash=hashlib.sha256(text.encode()).hexdigest()[:16],
    )


# -- the lifecycle operations ---------------------------------------------------

def _key(op: str, params: dict) -> str:
    return hashlib.sha256(
        f"{op}:{json.dumps(params, sort_keys=True)}".encode()
    ).hexdigest()[:32]


def _guarded_dispatch(dispatch):
    """Argument/precondition git errors happen BEFORE anything is dispatched:
    they are a definitive NO, not an ambiguous post-dispatch UNKNOWN."""
    def wrapped() -> EffectOutcome:
        try:
            return dispatch()
        except WorkspaceError as exc:
            return EffectOutcome.from_domain("rejected", reason=str(exc)[:200])
    return wrapped


class LocalLifecycle:
    """Typed local mutations for ONE RepositoryWorkspace."""

    def __init__(self, ws: RepositoryWorkspace) -> None:
        self._ws = ws

    # -- guards ---------------------------------------------------------------
    def require_clean_of_operations(self) -> None:
        snap = capture_snapshot(self._ws)
        if snap.operation_in_progress:
            raise OperationInProgressError(snap.operation_in_progress)

    # -- branching ------------------------------------------------------------
    def switch_branch(self, fork, broker: ExecutionBroker, branch: str):
        def dispatch() -> EffectOutcome:
            self.require_clean_of_operations()
            # A branch checked out in ANOTHER worktree cannot be switched to.
            for wt_branch, wt_path in list_worktrees(self._ws):
                if wt_branch == branch:
                    return EffectOutcome(status="refused", reason=(
                        f"branch_checked_out_elsewhere:{wt_path}"))
            proc = subprocess.run(
                ["git", "-C", self._ws.root, "switch", branch],
                capture_output=True, text=True, timeout=60)
            if proc.returncode != 0:
                return EffectOutcome.from_domain(
                    "rejected", reason=proc.stderr.strip()[:200])
            snap = capture_snapshot(self._ws)   # postcondition: actually there
            return EffectOutcome(status="applied", evidence={
                "branch": snap.branch, "head": snap.head_sha})

        request = EffectRequest(
            effect_id="git.branch.switch", required_capability="git.switch_branch",
            params={"workspace": self._ws.key, "branch": branch},
            idempotency_key=_key("switch", {"ws": self._ws.key, "b": branch}),
        )
        return broker.execute(fork, request, _guarded_dispatch(dispatch))

    def delete_local_branch(self, fork, broker: ExecutionBroker, branch: str,
                            *, force_unmerged: bool = False):
        def dispatch() -> EffectOutcome:
            self.require_clean_of_operations()
            current = capture_snapshot(self._ws)
            if current.branch == branch:
                return EffectOutcome.from_domain("rejected",
                                                 reason="cannot_delete_current_branch")
            flag = "-D" if force_unmerged else "-d"
            proc = subprocess.run(
                ["git", "-C", self._ws.root, "branch", flag, branch],
                capture_output=True, text=True, timeout=30)
            if proc.returncode != 0:
                return EffectOutcome.from_domain("rejected",
                                                 reason=proc.stderr.strip()[:200])
            remaining = [b for b in branches(self._ws) if b != branch]
            return EffectOutcome(status="applied",
                                 evidence={"deleted": branch,
                                           "remaining": list(remaining)})

        request = EffectRequest(
            effect_id="git.branch.delete", required_capability="git.delete_branch",
            params={"workspace": self._ws.key, "branch": branch,
                    "force_unmerged": force_unmerged},
            idempotency_key=_key("delbranch", {"ws": self._ws.key, "b": branch,
                                               "f": force_unmerged}),
        )
        return broker.execute(fork, request, _guarded_dispatch(dispatch))

    # -- exact staging + commit -------------------------------------------------
    def stage_exact_files(self, rel_paths: tuple[str, ...]) -> tuple[str, ...]:
        """Stage EXACTLY these paths, then PROVE the staged set equals them.

        Raises StageScopeError if anything else ends up staged — a commit
        proposed for file A must not absorb dirty file B.
        """
        if not rel_paths:
            raise StageScopeError("no paths requested — refusing implicit scope")
        for p in rel_paths:
            resolved = os.path.realpath(os.path.join(self._ws.root, p))
            root = os.path.realpath(self._ws.root)
            if not resolved.startswith(root + os.sep) and resolved != root:
                raise StageScopeError(f"path escapes workspace: {p}")
            subprocess.run(["git", "-C", self._ws.root, "add", "--", p],
                           capture_output=True, text=True, timeout=30, check=True)
        staged_now = set(staged_paths(self._ws))
        extra = sorted(staged_now - set(rel_paths))
        if extra:
            raise StageScopeError(
                f"staging leaked outside intent: {extra}"
            )
        return tuple(sorted(staged_now))

    def freeze_commit_intent(self, message: str, *, expected_head: str,
                             expected_staged_hash: str):
        """Bind the proposal to Git-native content identity BEFORE dispatch:
        parent HEAD + staged diff hash + the exact TREE the index will commit
        (`git write-tree`). This tree identity is what makes lost-response
        reconciliation mechanical — the human message is NOT effect identity.
        """
        return CommitIntent(
            workspace_key=self._ws.key,
            message=message,
            expected_head=expected_head,
            staged_hash=expected_staged_hash,
            intended_tree=_git(self._ws.root, "write-tree"),
        )

    def commit_with_proposal(self, fork, broker: ExecutionBroker,
                             message: str | None = None, *,
                             intent: CommitIntent | None = None,
                             expected_head: str = "",
                             expected_staged_hash: str = "",
                             signing_required: bool = False):
        """Commit under a FROZEN proposal. The proposal binds parent HEAD and
        the staged-content hash; drift between freeze and dispatch invalidates
        the proposal instead of committing something unreviewed."""
        if intent is None:
            intent = self.freeze_commit_intent(message or "",
                                               expected_head=expected_head,
                                               expected_staged_hash=expected_staged_hash)
        assert intent.workspace_key == self._ws.key
        message, expected_head, expected_staged_hash = (
            intent.message, intent.expected_head, intent.staged_hash)
        params = {"message": message, "parent": expected_head}
        request = EffectRequest(
            effect_id="git.commit", required_capability="git.commit",
            params={"workspace": self._ws.key, **params},
            idempotency_key=_key("commit", {
                "ws": self._ws.key, "msg": message,
                "parent": expected_head, "staged": expected_staged_hash}),
        )

        def dispatch() -> EffectOutcome:
            snap = capture_snapshot(self._ws)
            try:
                snap.require_for(expected_head)     # S1: HEAD moved since freeze
            except StaleSnapshotError as exc:
                return EffectOutcome.from_domain("rejected",
                                                 reason=f"stale_commit_proposal:{exc}")
            staged_text = _git(self._ws.root, "diff", "--cached")
            now_hash = hashlib.sha256(staged_text.encode()).hexdigest()[:16]
            if now_hash != expected_staged_hash:
                return EffectOutcome.from_domain(
                    "rejected",
                    reason="staged_content_changed_since_freeze")
            if not staged_text.strip():
                return EffectOutcome.from_domain("rejected", reason="empty_commit")

            proc = subprocess.run(
                ["git", "-C", self._ws.root, "commit", "-m", message],
                capture_output=True, text=True, timeout=60)
            committed = proc.returncode == 0
            if not committed:
                # Nothing happened OR partial? git commit is atomic per run:
                # non-zero means no commit was created.
                return EffectOutcome(status="failed",
                                     reason=proc.stderr.strip()[:200])

            # Post-mutation verification: exit 0 alone is NOT truth.
            try:
                new_head = _git(self._ws.root, "rev-parse", "HEAD")
                actual_tree = _git(self._ws.root, "rev-parse", "HEAD^{tree}")
                parent = _git(self._ws.root, "rev-parse", "HEAD~1" if
                              _git_count(self._ws) > 0 else "HEAD")
                gq = _git(self._ws.root, "log", "-1", "--format=%G?")
            except WorkspaceError as exc:
                raise RuntimeError(f"post-commit verification failed: {exc}") from exc

            signature = {"G": "verified", "N": "no_signature",
                         "U": "unverifiable", "X": "unverifiable",
                         "E": "unverifiable", "B": "bad_signature"}.get(gq.strip(),
                                                                        "unverifiable")
            if signing_required and signature != "verified":
                # The commit EXISTS; its claimed property failed verification.
                return EffectOutcome(
                    status="applied",
                    reason="commit_created_but_signature_not_verified",
                    evidence={"sha": new_head, "signature": signature,
                              "claim_signed_completion": False})

            return EffectOutcome(status="applied", evidence={
                "sha": new_head,
                "tree": actual_tree,
                "intended_tree_matched": actual_tree == intent.intended_tree,
                "parent_verified": parent == expected_head,
                "signature": signature,
            })

        return broker.execute(fork, request, _guarded_dispatch(dispatch))

    def find_commit_candidates(self, intent: CommitIntent) -> tuple[str, ...]:
        """Commits that match the intent's MECHANICAL identity:
        expected parent + intended tree. The message is deliberately NOT a
        criterion — it is prose, never effect identity."""
        out = _git(self._ws.root, "log", "--all", "--format=%H|%T|%P")
        candidates = []
        for line in out.splitlines():
            parts = line.split("|")
            if len(parts) != 3:
                continue
            sha, tree, parents = parts
            if tree == intent.intended_tree and \
                    intent.expected_head in parents.split(" "):
                candidates.append(sha)
        return tuple(candidates)

    def reconcile_commit_unknown(self, key: str, broker: ExecutionBroker,
                                 intent: CommitIntent):
        """Response lost during commit: inspect the repository BEFORE any
        retry. Reconciles ONLY on a UNIQUE parent+tree match (R1/R2); zero or
        multiple candidates leave the key UNKNOWN (R3/R4) — never guessed."""
        def resolve() -> EffectOutcome:
            candidates = self.find_commit_candidates(intent)
            if len(candidates) == 1:
                return EffectOutcome(status="applied", evidence={
                    "sha": candidates[0], "reconciled": True,
                    "matched_by": "parent+tree"})
            return EffectOutcome(
                status="unknown",
                reason=f"reconciliation_ambiguous_candidate_count:{len(candidates)}")

        return broker.reconcile(key, resolve)

    # -- restore / discard (typed, destructive-but-narrow) -----------------------
    def restore_worktree_file(self, fork, broker: ExecutionBroker, rel_path: str):
        def dispatch() -> EffectOutcome:
            _git(self._ws.root, "checkout", "--", rel_path)
            gone = rel_path not in unstaged_paths(self._ws)
            return EffectOutcome(status="applied",
                                 evidence={"restored": rel_path,
                                           "unstaged_after": gone})

        request = EffectRequest(
            effect_id="git.restore", required_capability="git.restore",
            params={"workspace": self._ws.key, "path": rel_path},
            idempotency_key=_key("restore", {"ws": self._ws.key, "p": rel_path,
                                             "h": _git(self._ws.root, "rev-parse", "HEAD")}),
        )
        return broker.execute(fork, request, _guarded_dispatch(dispatch))

    def unstage_file(self, fork, broker: ExecutionBroker, rel_path: str):
        def dispatch() -> EffectOutcome:
            _git(self._ws.root, "restore", "--staged", "--", rel_path)
            still = rel_path in staged_paths(self._ws)
            return EffectOutcome(status="applied",
                                 evidence={"unstaged": rel_path,
                                           "still_staged": still})

        request = EffectRequest(
            effect_id="git.unstage", required_capability="git.unstage",
            params={"workspace": self._ws.key, "path": rel_path},
            idempotency_key=_key("unstage", {"ws": self._ws.key, "p": rel_path,
                                             "h": _git(self._ws.root, "rev-parse", "HEAD")}),
        )
        return broker.execute(fork, request, _guarded_dispatch(dispatch))

    def delete_untracked_file(self, fork, broker: ExecutionBroker, rel_path: str):
        def dispatch() -> EffectOutcome:
            target = os.path.join(self._ws.root, rel_path)
            if not os.path.isfile(target) or _is_tracked(self._ws, rel_path):
                return EffectOutcome.from_domain(
                    "rejected", reason="not_an_untracked_file")
            os.unlink(target)
            return EffectOutcome(status="applied",
                                 evidence={"deleted_untracked": rel_path,
                                           "exists_after": os.path.exists(target)})

        request = EffectRequest(
            effect_id="fs.untracked_delete", required_capability="fs.delete_untracked",
            params={"workspace": self._ws.key, "path": rel_path},
            idempotency_key=_key("deluntracked", {"ws": self._ws.key, "p": rel_path}),
        )
        return broker.execute(fork, request, _guarded_dispatch(dispatch))

    # -- worktrees --------------------------------------------------------------
    def create_worktree(self, fork, broker: ExecutionBroker, path: str,
                        branch: str):
        def dispatch() -> EffectOutcome:
            self.require_clean_of_operations()
            real = os.path.realpath(path)
            if os.path.exists(real):
                return EffectOutcome.from_domain(
                    "rejected", reason="target_exists")
            for wt_branch, _wt_path in list_worktrees(self._ws):
                if wt_branch == branch:
                    return EffectOutcome.from_domain(
                        "rejected", reason="branch_already_checked_out")
            _git(self._ws.root, "worktree", "add", real, branch)
            listing = list_worktrees(self._ws)   # postcondition: really there
            found = any(p == real for _b, p in listing)
            return EffectOutcome(status="applied", evidence={
                "worktree": real, "branch": branch,
                "verified_in_listing": found})

        request = EffectRequest(
            effect_id="git.worktree.create", required_capability="git.worktree",
            params={"workspace": self._ws.key, "path": path, "branch": branch},
            idempotency_key=_key("wtadd", {"ws": self._ws.key, "p": path,
                                           "b": branch}),
        )
        return broker.execute(fork, request, _guarded_dispatch(dispatch))

    def remove_worktree(self, fork, broker: ExecutionBroker, path: str):
        def dispatch() -> EffectOutcome:
            real = os.path.realpath(path)
            targets = [p for _b, p in list_worktrees(self._ws)]
            if real not in targets or real == os.path.realpath(self._ws.root):
                return EffectOutcome.from_domain(
                    "rejected", reason="not_a_removable_worktree")
            # A DIRTY worktree is never silently destroyed.
            proc = subprocess.run(
                ["git", "-C", real, "status", "--porcelain"],
                capture_output=True, text=True, timeout=30)
            if proc.stdout.strip():
                return EffectOutcome.from_domain(
                    "rejected", reason="dirty_worktree_refused")
            _git(self._ws.root, "worktree", "remove", real)
            gone = all(p != real for _b, p in list_worktrees(self._ws))
            return EffectOutcome(status="applied",
                                 evidence={"removed": real,
                                           "verified_gone": gone})

        request = EffectRequest(
            effect_id="git.worktree.remove", required_capability="git.worktree",
            params={"workspace": self._ws.key, "path": path},
            idempotency_key=_key("wtrm", {"ws": self._ws.key, "p": path}),
        )
        return broker.execute(fork, request, _guarded_dispatch(dispatch))

    # -- cherry-pick --------------------------------------------------------------
    def cherry_pick(self, fork, broker: ExecutionBroker, source_sha: str, *,
                    expected_head: str, expected_branch: str):
        def dispatch() -> EffectOutcome:
            self.require_clean_of_operations()
            snap = capture_snapshot(self._ws)
            try:
                snap.require_for(expected_head)
            except StaleSnapshotError as exc:
                return EffectOutcome.from_domain("rejected",
                                                 reason=f"stale_cherry_pick:{exc}")
            if snap.branch != expected_branch:
                return EffectOutcome.from_domain(
                    "rejected",
                    reason=f"wrong_branch:{snap.branch}")
            # Source must exist in THIS repository — wrong-SHA attack dies here.
            try:
                _git(self._ws.root, "cat-file", "-e", f"{source_sha}^{{commit}}")
            except WorkspaceError:
                return EffectOutcome.from_domain("rejected",
                                                 reason="unknown_source_sha")

            proc = subprocess.run(
                ["git", "-C", self._ws.root, "cherry-pick", source_sha],
                capture_output=True, text=True, timeout=60)
            after = capture_snapshot(self._ws)
            if after.operation_in_progress == "cherry-pick":
                # The world CHANGED: repo entered conflicted state. This is
                # truthful APPLIED-with-conflict, never failed-no-effect.
                # Mechanical separation: mutation_occurred=True (the repo was
                # mutated into CHERRY_PICK_HEAD state) but completed=False —
                # the REQUESTED integration did NOT finish. Consumers must use
                # local_operation_completed(), never bare receipt status.
                return EffectOutcome(
                    status="applied",
                    reason="cherry_pick_conflicted",
                    evidence={"source": source_sha, "conflicted": True,
                              "mutation_occurred": True,
                              "completed": False,
                              "conflicted_paths": list(after.conflicted_paths)})
            if proc.returncode != 0:
                return EffectOutcome(status="failed",
                                     reason=proc.stderr.strip()[:200])
            return EffectOutcome(status="applied", evidence={
                "source": source_sha, "new_head": after.head_sha,
                "conflicted": False,
                "mutation_occurred": True,
                "completed": True})

        request = EffectRequest(
            effect_id="git.cherry_pick", required_capability="git.cherry_pick",
            params={"workspace": self._ws.key, "source": source_sha,
                    "expected_head": expected_head},
            idempotency_key=_key("cherry", {"ws": self._ws.key,
                                            "src": source_sha,
                                            "parent": expected_head}),
        )
        return broker.execute(fork, request, _guarded_dispatch(dispatch))


# -- module-level read helpers ----------------------------------------------------

def branches(ws: RepositoryWorkspace) -> tuple[str, ...]:
    return tuple(line.strip() for line in
                 _git(ws.root, "branch", "--format=%(refname:short)").splitlines()
                 if line.strip())


def staged_paths(ws: RepositoryWorkspace) -> tuple[str, ...]:
    return tuple(p for p in _git(ws.root, "diff", "--cached", "--name-only").splitlines()
                 if p.strip())


def unstaged_paths(ws: RepositoryWorkspace) -> tuple[str, ...]:
    return tuple(p for p in _git(ws.root, "diff", "--name-only").splitlines()
                 if p.strip())


def _is_tracked(ws: RepositoryWorkspace, rel_path: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", ws.root, "ls-files", "--error-unmatch", "--", rel_path],
        capture_output=True, text=True, timeout=15)
    return proc.returncode == 0


def _git_count(ws: RepositoryWorkspace) -> int:
    return int(_git(ws.root, "rev-list", "--count", "HEAD"))


def list_worktrees(ws: RepositoryWorkspace) -> tuple[tuple[str | None, str], ...]:
    """(branch_or_None_if_detached, realpath) for each worktree."""
    out = _git(ws.root, "worktree", "list", "--porcelain")
    results: list[tuple[str | None, str]] = []
    current_branch: str | None = None
    current_path: str | None = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            current_path = line.removeprefix("worktree ")
            current_branch = None
        elif line.startswith("branch "):
            ref = line.removeprefix("branch ")
            current_branch = ref.removeprefix("refs/heads/")
        elif line.strip() == "detached" or (line == "" and current_path):
            if line == "":
                results.append((current_branch, os.path.realpath(current_path)))
                current_path, current_branch = None, None
    if current_path:
        results.append((current_branch, os.path.realpath(current_path)))
    return tuple(results)


def local_operation_completed(ws: RepositoryWorkspace, receipt) -> bool:
    """The ONLY sanctioned way to claim a local operation 'completed'.

    Requires BOTH: the receipt's own completed flag AND live repository state
    that agrees (no in-progress operation, no conflicted paths). A consumer
    checking only receipt.status == 'applied' can therefore never mistake
    applied+conflicted for a finished integration — this predicate refuses
    while CHERRY_PICK_HEAD/conflict state exists.
    """
    if getattr(receipt, "status", None) != "applied":
        return False
    if receipt.evidence.get("completed") is not True:
        return False
    snap = capture_snapshot(ws)
    return (snap.operation_in_progress is None
            and not snap.conflicted_paths)


__all__ = [
    "BoundDiff", "CommitIntent", "FileStatus", "LifecycleError",
    "LocalLifecycle", "LocalLifecycleSnapshot", "OperationInProgressError",
    "StageScopeError", "StaleSnapshotError", "bound_diff", "branches",
    "capture_snapshot", "list_worktrees", "local_operation_completed",
]
