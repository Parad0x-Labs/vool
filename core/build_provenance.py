"""Build-time source provenance.

A packaged VOOL install ships no ``.git`` directory, so at runtime
``core/web/api/runtime.py::git_checkout_state`` returns invalid and the version stamp falls back
to ``build_source_metadata`` — which reads ``config/build-source.json``. Nothing was writing that
file, so every bundled install reported ``commit:""`` and a stale build was indistinguishable from
a fresh one (this is what let a pre-fix build masquerade as current). This module captures the git
SHA of the source tree AT BUILD TIME and writes ``config/build-source.json`` so the bundle's
``/api/runtime/version`` and the ``/chat`` footer show the exact commit it was built from.

Field names match what ``build_source_metadata`` extracts: ref / branch / commit / source_url /
source_kind / dirty_state / diff_sha256 (plus an informational ``built_at``). The same file makes a
plain source copy (no ``.git``) identifiable: ``ops/build_identity.py stamp-copy`` writes it into
the copy so the running copy reports the commit, and the edits, it was copied from.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _git(root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except Exception:
        return ""
    return str(completed.stdout or "").strip()


# An untracked file bigger than this is fingerprinted by name and size only, so one stray model or
# video in a test tree cannot stall the boot stamp.
_UNTRACKED_CONTENT_LIMIT = 32 * 1024 * 1024
_UNTRACKED_FILE_LIMIT = 5000


def worktree_diff_sha256(project_root: Path) -> str:
    """Fingerprint of everything in ``project_root`` that differs from its HEAD commit.

    ``dirty`` alone is one bit: two test trees on the same commit with different uncommitted edits
    both read ``<sha>.dirty`` and cannot be told apart, so a test cannot prove it is talking to the
    edited tree it means. This hashes the binary diff of tracked files against HEAD plus the name and
    content of every untracked, non-ignored file. Returns "" for a clean tree or a non-git folder."""
    root = Path(project_root)
    try:
        diff = subprocess.run(
            ["git", "-C", str(root), "diff", "HEAD", "--binary", "--no-ext-diff", "--no-color", "--no-renames"],
            capture_output=True,
            timeout=20,
            check=True,
        ).stdout
        untracked = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--others", "--exclude-standard"],
            capture_output=True,
            timeout=20,
            check=True,
        ).stdout
    except Exception:
        return ""
    names = sorted(name for name in untracked.decode("utf-8", "surrogateescape").split("\0") if name)
    if not diff and not names:
        return ""
    digest = hashlib.sha256()
    digest.update(b"tracked-diff\0")
    digest.update(diff)
    for name in names[:_UNTRACKED_FILE_LIMIT]:
        path = root / name
        digest.update(b"\0untracked\0" + name.encode("utf-8", "surrogateescape") + b"\0")
        try:
            size = path.stat().st_size
            if size > _UNTRACKED_CONTENT_LIMIT:
                digest.update(f"size:{size}".encode())
            else:
                digest.update(hashlib.sha256(path.read_bytes()).digest())
        except OSError:
            digest.update(b"unreadable")
    if len(names) > _UNTRACKED_FILE_LIMIT:
        digest.update(f"\0more-untracked:{len(names) - _UNTRACKED_FILE_LIMIT}".encode())
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def capture_build_source(project_root: Path, *, now: str | None = None) -> dict[str, Any]:
    """Capture the git provenance of ``project_root``. Returns a dict with source_kind='git' and a
    real short and full commit when the tree is a git checkout, else source_kind='unknown'
    (never raises)."""
    root = Path(project_root)
    commit_full = _git(root, "rev-parse", "HEAD")
    built_at = now or _utc_now()
    if not re.fullmatch(r"[0-9a-f]{40}", commit_full):
        return {"source_kind": "unknown", "built_at": built_at}
    commit = _git(root, "rev-parse", "--short=12", "HEAD") or commit_full[:12]
    branch = _git(root, "branch", "--show-current")
    dirty = bool(_git(root, "status", "--short"))
    source: dict[str, Any] = {
        "source_kind": "git",
        "commit": commit,
        "commit_full": commit_full,
        "dirty_state": dirty,
        "built_at": built_at,
    }
    if dirty:
        diff_sha256 = worktree_diff_sha256(root)
        if diff_sha256:
            source["diff_sha256"] = diff_sha256
    if branch:
        source["branch"] = branch
        source["ref"] = branch
    return source


def write_build_source(project_root: Path, out_path: Path | None = None, *, now: str | None = None) -> Path:
    """Write ``config/build-source.json`` (or ``out_path``) for ``project_root``. Returns the path."""
    root = Path(project_root)
    target = Path(out_path) if out_path is not None else (root / "config" / "build-source.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(capture_build_source(root, now=now), indent=2) + "\n", encoding="utf-8")
    return target
