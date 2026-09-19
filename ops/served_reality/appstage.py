"""Exact-SHA staging of the product under test.

The bench never tests a working tree; it tests an immutable staged copy of
EXACTLY one commit:

1. ``stage_app`` verifies the source repo is clean for the product paths,
   writes ``git archive <sha>`` into ``<run_dir>/app`` and proves the staging
   by hashing every staged byte (census + aggregate archive hash).
2. The staged tree is made read-only. The daemon's runtime state is forced
   out of the tree (isolated VOOL_HOME + VOOL_WORKSPACE_ROOT +
   PYTHONDONTWRITEBYTECODE) so read-only is a real constraint the product can
   violate loudly, not a silent-corruption risk.
3. ``verify_app_unchanged`` re-censuses after the run; any drift means the
   product wrote into its own code tree — a VOOL-class finding.

Mutations for the sensitivity controls copy the pristine staging to a
throwaway sibling and patch THAT copy — the pristine staging is never
touched.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path

from ops.served_reality.classify import BenchError

# Paths whose dirtiness would falsify "we tested exactly this SHA".
PRODUCT_PATH_PREFIXES = (
    "apps/",
    "core/",
    "storage/",
    "adapters/",
    "network/",
    "retrieval/",
    "relay/",
    "sandbox/",
    "bootstrap/",
    "channels/",
    "config/",
    "skills/",
    "tools/",
    "installer/",
    "infra/",
    "scripts/",
)


@dataclass
class StagedApp:
    path: Path
    sha: str
    sha_full: str
    branch: str
    archive_sha256: str
    census: dict[str, str]  # relative path -> sha256 of bytes
    file_count: int

    def verify_unchanged(self) -> tuple[bool, list[str]]:
        """Re-census (excluding the legacy state root); returns (unchanged, drifted)."""
        current = census_tree(self.path)
        drifted = []
        for rel, digest in self.census.items():
            if current.get(rel) != digest:
                drifted.append(rel)
        for rel in current:
            if rel not in self.census:
                drifted.append(f"{rel} (new)")
        return (not drifted), drifted

    def legacy_state_files(self) -> list[str]:
        """What the product wrote into its own tree (the SHA's state-in-tree fact)."""
        root = self.path / ".vool_local"
        if not root.is_dir():
            return []
        return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def census_tree(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(root))
            if rel.startswith(LEGACY_STATE_DIR):
                # The product's legacy in-tree state root (storage/db.py's
                # DEFAULT_DB_PATH mkdir): pre-created writable by the bench,
                # excluded from the immutability census, and reported in the
                # manifest as observed state-in-tree at this SHA.
                continue
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


#: The product's own designated in-tree state directory at this SHA
#: (storage/db.py eagerly mkdir's <repo>/.vool_local/data on every
#: connection). The bench pre-creates it writable so read-only staging can
#: still boot, and the census measures everything OUTSIDE it.
LEGACY_STATE_DIR = ".vool_local/"


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise BenchError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:400]}")
    return proc.stdout


def resolve_sha(repo: Path, sha: str) -> tuple[str, str, str]:
    """Returns (short, full, branch-if-any)."""
    full = _git(repo, "rev-parse", f"{sha}^{{commit}}").strip()
    try:
        branch = _git(repo, "rev-parse", "--symbolic-full-name", "--abbrev-ref", full).strip()
    except BenchError:
        branch = ""
    if branch == "HEAD" or not branch:
        branch = ""
    return full[:9], full, branch


def check_clean_tree(repo: Path) -> tuple[bool, list[str]]:
    """True when no tracked product file is modified/deleted.

    Untracked files outside the product path prefixes are tolerated (bench
    scratch); anything else falsifies the exact-SHA claim and refuses the run.
    """
    status = _git(repo, "status", "--porcelain")
    offending: list[str] = []
    for line in status.splitlines():
        if not line.strip():
            continue
        state, _, path = line[:2], 2, line[3:]
        path = path.strip().strip('"')
        if state.endswith("?"):
            # Untracked: tolerated only outside product paths.
            if any(path.startswith(prefix) for prefix in PRODUCT_PATH_PREFIXES):
                offending.append(f"{line} (untracked inside product paths)")
            continue
        offending.append(line)
    return (not offending), offending


def stage_app(repo: Path, sha: str, run_dir: Path, *, provenance_branch: str = "") -> StagedApp:
    """Materialize the exact commit into <run_dir>/app, read-only.

    The staged app has no .git, so the product's own packaged-install
    provenance (config/build-source.json — the same path a real installer
    writes) carries the exact full SHA. healthz then reports the staged
    commit and the boot gate verifies it.
    """
    short, full, branch = resolve_sha(repo, sha)
    clean, offending = check_clean_tree(repo)
    if not clean:
        raise BenchError(
            "source tree is not clean; refusing to stage (exact-SHA claim would be false)",
            detail="; ".join(offending[:10]),
        )
    app_dir = run_dir / "app"
    if app_dir.exists():
        # A prior run's staging is read-only and bench-owned; reclaim it.
        _make_writable(app_dir)
        shutil.rmtree(app_dir)
    app_dir.mkdir(parents=True)
    archive_bytes = subprocess.run(
        ["git", "-C", str(repo), "archive", full],
        capture_output=True,
        timeout=600,
    )
    if archive_bytes.returncode != 0:
        raise BenchError(
            f"git archive {full} failed: {archive_bytes.stderr.decode('utf-8', 'replace')[:400]}"
        )
    blob = archive_bytes.stdout
    archive_sha256 = hashlib.sha256(blob).hexdigest()
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:") as tar:
        try:
            tar.extractall(app_dir, filter="data")
        except TypeError:  # python < 3.12 without filter kwarg
            tar.extractall(app_dir)
    census = census_tree(app_dir)
    # Packaged-install provenance: the product reads this when no .git exists
    # (core/web/api/runtime.py build_source_metadata) — the same mechanism a
    # real installer uses, so healthz truthfully reports the staged SHA.
    provenance = {
        "ref": provenance_branch or branch or "served-reality-bench",
        "branch": provenance_branch or branch or "",
        "commit": full,
        "commit_full": full,
        "dirty_state": False,
        "source_kind": "served-reality-bench-staging",
    }
    (app_dir / "config" / "build-source.json").write_text(
        json.dumps(provenance, indent=2), encoding="utf-8"
    )
    census = census_tree(app_dir)
    # The legacy in-tree state root must exist BEFORE the read-only pass (the
    # app root itself becomes non-writable) and stay writable afterwards.
    legacy = app_dir / ".vool_local" / "data"
    legacy.mkdir(parents=True, exist_ok=True)
    _make_readonly(app_dir)
    os.chmod(app_dir / ".vool_local", 0o755)
    os.chmod(legacy, 0o755)
    return StagedApp(
        path=app_dir,
        sha=short,
        sha_full=full,
        branch=branch,
        archive_sha256=archive_sha256,
        census=census,
        file_count=len(census),
    )


def clone_staging(staged: StagedApp, target: Path) -> Path:
    """Copy a staged app for mutation controls. Target is writable."""
    if target.exists():
        raise BenchError(f"mutation clone target already exists: {target}")
    shutil.copytree(staged.path, target)
    _make_writable(target)
    return target


def _make_readonly(root: Path) -> None:
    os.chmod(root, 0o555)
    for path in root.rglob("*"):
        if path.is_symlink():
            continue
        if path.is_dir():
            os.chmod(path, 0o555)
        elif path.is_file():
            os.chmod(path, 0o444)


def _make_writable(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            continue
        if path.is_dir():
            os.chmod(path, 0o755)
        elif path.is_file():
            os.chmod(path, 0o644)
    os.chmod(root, 0o755)
