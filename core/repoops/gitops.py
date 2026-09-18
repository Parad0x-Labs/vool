"""Typed local git operations for RepoOps.

This module runs git and reports what git said. It decides nothing: whether an operation MAY
run is settled above it by ``core.mode_permission_policy`` through the tool contract's declared
permission actions, and whether it is RECORDED is settled by the effect gateway and the flight
recorder at the door. What lives here is the part that is genuinely git's: which argv expresses
the operation, which exit conditions mean conflict rather than failure, and how to read the
result back as data instead of prose.

Two operations are DEFAULT-DENIED and are not reachable from this module at all -- there is no
force-push argv and no branch-delete argv here that a caller could reach without the runtime
first getting an explicit, non-ALLOW-by-default permission decision. That is deliberate: a
default-deny that lives only in an ``if`` above the call is one refactor away from being gone.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

#: A bounded git call. An unbounded git on a pathological repo hangs the tool call and the turn,
#: and the dispatch layer cannot cancel an in-flight subprocess.
GIT_TIMEOUT_SECONDS = 30

#: Operations this module knows how to express. `restore` is the conflict escape hatch: it aborts
#: an in-progress merge/cherry-pick/revert, which is the only safe generic recovery.
OPERATIONS = ("branch", "commit", "cherry_pick", "revert", "merge", "tag", "restore")

#: Named here so the refusal reads as a policy fact, not an omission.
DEFAULT_DENIED = {
    "force_push": "a force push rewrites published history; it needs an explicit operator grant",
    "branch_delete": "deleting a branch discards work that may exist nowhere else; it needs an explicit operator grant",
}


@dataclass(frozen=True)
class GitResult:
    operation: str
    ok: bool
    status: str
    argv: tuple[str, ...]
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""
    head: str = ""
    conflicted_paths: tuple[str, ...] = ()
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "operation": self.operation,
            "ok": self.ok,
            "status": self.status,
            "command": " ".join(self.argv),
            "returncode": self.returncode,
            "stdout": self.stdout[-4000:],
            "stderr": self.stderr[-4000:],
            "head": self.head,
            "conflicted_paths": list(self.conflicted_paths),
            "detail": self.detail,
        }


@dataclass
class GitRunner:
    """Every git invocation RepoOps makes, in one place, bounded and captured."""

    root: Path
    timeout: int = GIT_TIMEOUT_SECONDS
    log: list[dict[str, object]] = field(default_factory=list)

    def run(self, *argv: str) -> tuple[int, str, str]:
        command = ["git", "-C", str(self.root), *argv]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            self.log.append({"argv": list(argv), "returncode": -1, "timed_out": True})
            return -1, "", f"git {' '.join(argv[:2])} timed out after {self.timeout}s"
        except FileNotFoundError:
            return -1, "", "git is not installed on this machine"
        self.log.append({"argv": list(argv), "returncode": completed.returncode})
        return completed.returncode, completed.stdout or "", completed.stderr or ""

    # -- observations -------------------------------------------------------------------

    def is_repo(self) -> bool:
        code, _, _ = self.run("rev-parse", "--git-dir")
        return code == 0

    def head(self) -> str:
        code, out, _ = self.run("rev-parse", "HEAD")
        return out.strip() if code == 0 else ""

    def branch(self) -> str:
        code, out, _ = self.run("rev-parse", "--abbrev-ref", "HEAD")
        return out.strip() if code == 0 else ""

    def remote_url(self, remote: str = "origin") -> str:
        code, out, _ = self.run("remote", "get-url", remote)
        return out.strip() if code == 0 else ""

    def dirty_paths(self) -> list[str]:
        code, out, _ = self.run("status", "--porcelain")
        if code != 0:
            return []
        return [line[3:].strip() for line in out.splitlines() if line.strip()]

    def resolve(self, ref: str) -> str:
        """The exact 40-hex SHA `ref` names locally, or "" when it names nothing."""

        clean = str(ref or "").strip()
        if not clean:
            return ""
        code, out, _ = self.run("rev-parse", "--verify", "--quiet", f"{clean}^{{commit}}")
        return out.strip() if code == 0 else ""

    def merge_base(self, a: str, b: str) -> str:
        code, out, _ = self.run("merge-base", str(a), str(b))
        return out.strip() if code == 0 else ""

    def conflicted_paths(self) -> tuple[str, ...]:
        code, out, _ = self.run("diff", "--name-only", "--diff-filter=U")
        if code != 0:
            return ()
        return tuple(line.strip() for line in out.splitlines() if line.strip())

    def in_progress(self) -> str:
        """Which multi-step operation, if any, the repository is currently mid-way through."""

        git_dir = self.root / ".git"
        for marker, label in (
            ("MERGE_HEAD", "merge"),
            ("CHERRY_PICK_HEAD", "cherry_pick"),
            ("REVERT_HEAD", "revert"),
            ("rebase-merge", "rebase"),
            ("rebase-apply", "rebase"),
        ):
            if (git_dir / marker).exists():
                return label
        return ""

    # -- operations ---------------------------------------------------------------------

    def _result(self, operation: str, argv: tuple[str, ...], code: int, out: str, err: str) -> GitResult:
        conflicted = self.conflicted_paths()
        if conflicted:
            return GitResult(
                operation=operation,
                ok=False,
                status="conflict",
                argv=argv,
                returncode=code,
                stdout=out,
                stderr=err,
                head=self.head(),
                conflicted_paths=conflicted,
                detail=(
                    "the operation stopped on a conflict and the tree is left in the conflicted "
                    "state; resolve the named paths or run the `restore` operation to abort"
                ),
            )
        if code != 0:
            return GitResult(
                operation=operation,
                ok=False,
                status="failed",
                argv=argv,
                returncode=code,
                stdout=out,
                stderr=err,
                head=self.head(),
            )
        return GitResult(
            operation=operation, ok=True, status="executed", argv=argv, returncode=code, stdout=out, stderr=err, head=self.head()
        )

    def create_branch(self, name: str, *, start_point: str = "") -> GitResult:
        clean = str(name or "").strip()
        if not clean:
            return GitResult("branch", False, "invalid_arguments", (), detail="a branch needs a name")
        argv = ("switch", "-c", clean) + ((str(start_point).strip(),) if str(start_point).strip() else ())
        code, out, err = self.run(*argv)
        return self._result("branch", argv, code, out, err)

    def commit(self, *, message: str, paths: tuple[str, ...] = ()) -> GitResult:
        """Stage and commit. The vertical cannot reach a push without one, so it is typed here
        rather than left to a shell command that no contract describes."""

        text = str(message or "").strip()
        if not text:
            return GitResult("commit", False, "invalid_arguments", (), detail="a commit needs a message")
        stage = ("add", "--", *paths) if paths else ("add", "-A")
        code, out, err = self.run(*stage)
        if code != 0:
            return self._result("commit", stage, code, out, err)
        argv = ("commit", "-m", text)
        code, out, err = self.run(*argv)
        if code != 0 and "nothing to commit" in f"{out}{err}".lower():
            return GitResult(
                "commit", False, "nothing_to_commit", argv, returncode=code, stdout=out, stderr=err,
                head=self.head(), detail="the working tree matches HEAD; there is nothing to commit",
            )
        return self._result("commit", argv, code, out, err)

    def cherry_pick(self, commit: str) -> GitResult:
        clean = str(commit or "").strip()
        if not clean:
            return GitResult("cherry_pick", False, "invalid_arguments", (), detail="a cherry-pick needs a commit")
        argv = ("cherry-pick", "--no-rerere-autoupdate", clean)
        code, out, err = self.run(*argv)
        return self._result("cherry_pick", argv, code, out, err)

    def revert(self, commit: str) -> GitResult:
        clean = str(commit or "").strip()
        if not clean:
            return GitResult("revert", False, "invalid_arguments", (), detail="a revert needs a commit")
        argv = ("revert", "--no-edit", clean)
        code, out, err = self.run(*argv)
        return self._result("revert", argv, code, out, err)

    def merge(self, onto: str, *, message: str = "") -> GitResult:
        clean = str(onto or "").strip()
        if not clean:
            return GitResult("merge", False, "invalid_arguments", (), detail="a merge needs something to merge")
        argv = ("merge", "--no-ff", "--no-edit") + (("-m", str(message).strip()) if str(message).strip() else ()) + (clean,)
        code, out, err = self.run(*argv)
        return self._result("merge", argv, code, out, err)

    def tag(self, name: str, *, commit: str = "", message: str = "") -> GitResult:
        clean = str(name or "").strip()
        if not clean:
            return GitResult("tag", False, "invalid_arguments", (), detail="a tag needs a name")
        argv = ("tag", "-a", clean, "-m", str(message).strip() or clean) + (
            (str(commit).strip(),) if str(commit).strip() else ()
        )
        code, out, err = self.run(*argv)
        return self._result("tag", argv, code, out, err)

    def restore(self) -> GitResult:
        """Abort whatever multi-step operation is in progress. Refuses when none is."""

        pending = self.in_progress()
        if not pending:
            return GitResult(
                "restore", False, "nothing_in_progress", (), head=self.head(),
                detail="no merge, cherry-pick, revert or rebase is in progress; there is nothing to abort",
            )
        argv = {"merge": ("merge", "--abort"), "cherry_pick": ("cherry-pick", "--abort"),
                "revert": ("revert", "--abort"), "rebase": ("rebase", "--abort")}[pending]
        code, out, err = self.run(*argv)
        return self._result("restore", argv, code, out, err)

    def push_argv(self, *, remote: str, ref: str, sha: str) -> tuple[str, ...]:
        """The exact argv a push would run. Built, never executed, by this module.

        Note what it is NOT: there is no `--force`, no `+` refspec and no `--delete`. The
        default-denied operations have no expression here to reach.
        """

        return ("push", str(remote), f"{sha!s}:refs/heads/{ref!s}")


__all__ = ["DEFAULT_DENIED", "GIT_TIMEOUT_SECONDS", "OPERATIONS", "GitResult", "GitRunner"]
