"""A disposable repository with a real defect, a real failing pytest, and a real remote.

The remote is a local bare repository. A push against it is a REAL push -- the same argv, the
same git, the same ref motion, verifiable by reading the bare repo's refs afterwards -- and it
reaches no network and no third party. That is what lets these tests prove the push path without
ever performing a remote mutation.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
TEST = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"


def git(root: Path, *args: str, check: bool = True) -> str:
    out = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=60,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "fx",
            "GIT_AUTHOR_EMAIL": "fx@local",
            "GIT_COMMITTER_NAME": "fx",
            "GIT_COMMITTER_EMAIL": "fx@local",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
        },
    )
    if check:
        assert out.returncode == 0, f"git {' '.join(args)} failed: {out.stderr}"
    return out.stdout


def build_repo(tmp_path: Path, *, branch: str = "feature") -> tuple[Path, Path]:
    """Return ``(worktree, bare_remote)`` with one commit on main and one on ``branch``."""

    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True, timeout=60)
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "fx@local")
    git(root, "config", "user.name", "fx")
    (root / "calc.py").write_text(BUGGY, encoding="utf-8")
    (root / "test_calc.py").write_text(TEST, encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "seed")
    git(root, "remote", "add", "origin", str(bare))
    git(root, "push", "-q", "origin", "main")
    git(root, "switch", "-q", "-c", branch)
    (root / "NOTES.md").write_text("work in progress\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "start the repair")
    return root, bare


def head(root: Path) -> str:
    return git(root, "rev-parse", "HEAD").strip()


def remote_ref(bare: Path, ref: str) -> str:
    out = git(bare, "rev-parse", "--verify", "--quiet", f"refs/heads/{ref}", check=False).strip()
    return out


def context(root: Path, session: str = "repoops-session", **extra) -> dict:
    ctx = {
        "workspace": str(root),
        "workspace_root": str(root),
        "session_id": session,
        "operating_mode": "auto",
    }
    ctx.update(extra)
    return ctx


def door(intent: str, arguments: dict, ctx: dict):
    """Every call in these packs crosses THE production door, never the runtime directly."""

    from core.runtime_execution_tools import execute_runtime_tool

    result = execute_runtime_tool(intent, arguments, source_context=ctx)
    assert result is not None, f"{intent} is not contracted at the production door"
    return result


__all__ = ["BUGGY", "FIXED", "TEST", "build_repo", "context", "door", "git", "head", "remote_ref"]
