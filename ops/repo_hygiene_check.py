from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.release_channel import release_manifest_warnings

_IGNORED_DIRS = {
    ".git",
    ".github",
    ".vool_local",
    ".pytest_cache",
    "__pycache__",
    "build",
    "dist",
}

_IGNORED_KEY_ARTIFACT_ROOTS = (
    Path("artifacts/acceptance_runs"),
    Path("reports/greenloop"),
    Path(".vool-eval"),
    Path("local_evidence"),
)

_LEGACY_ROOT_DOCS = {
    "CURSOR_AUDIT_REPORT.md",
    "Cursor_Claude_Handover.md",
    "IDENTITY.md",
}


def _license_placeholder_hints() -> list[str]:
    candidates = [
        PROJECT_ROOT / "LICENSE",
        PROJECT_ROOT / "LICENSES" / "BSL-1.1.txt",
        PROJECT_ROOT / "LICENSES" / "Apache-2.0.txt",
    ]
    hints: list[str] = []
    for path in candidates:
        try:
            body = path.read_text(encoding="utf-8").lower()
        except Exception:
            continue
        if "placeholder" in body or "replace this file" in body:
            hints.append(path.relative_to(PROJECT_ROOT).as_posix())
    return hints


def _repo_key_artifacts() -> list[str]:
    matches: list[str] = []
    for pattern in ("node_signing_key.b64", "node_signing_key.json", "node_signing_key.keyring.json"):
        for path in PROJECT_ROOT.rglob(pattern):
            if any(part in _IGNORED_DIRS for part in path.parts):
                continue
            relative = path.relative_to(PROJECT_ROOT)
            if any(relative == root or root in relative.parents for root in _IGNORED_KEY_ARTIFACT_ROOTS):
                continue
            matches.append(relative.as_posix())
    return sorted(matches)


def _git(*args: str) -> int:
    try:
        return subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), *args],
            capture_output=True,
            check=False,
        ).returncode
    except OSError:
        # No git binary: the caller must fall back to flagging the path.
        return 1


def _is_developer_local(relative_path: str) -> bool:
    """True only when git BOTH ignores `relative_path` and does not track it.

    Repo hygiene is about what the repository carries, so a path that .gitignore
    excludes and git never tracked is a developer's local scratch file: it cannot
    reach a clean checkout, so flagging it can only ever fail on a dev machine while
    CI stays green. Tracked paths are real repo content and stay flagged even when an
    ignore rule matches them, mirroring git's own precedence. Anything git cannot
    answer for (no git binary, no checkout) is treated as repo content, so the check
    fails closed rather than silently passing.
    """
    if _git("ls-files", "--error-unmatch", "--", relative_path) == 0:
        return False
    return _git("check-ignore", "-q", "--", relative_path) == 0


def _legacy_root_clutter() -> list[str]:
    issues: list[str] = []
    for name in sorted(_LEGACY_ROOT_DOCS):
        if (PROJECT_ROOT / name).exists() and not _is_developer_local(name):
            issues.append(name)
    for path in sorted(PROJECT_ROOT.glob("test_*.py")):
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        if _is_developer_local(relative):
            continue
        issues.append(relative)
    return issues


def build_report() -> dict[str, object]:
    issues: list[str] = []
    key_artifacts = _repo_key_artifacts()
    if key_artifacts:
        issues.append(f"repo-local signing key artifacts present: {', '.join(key_artifacts)}")

    license_hints = _license_placeholder_hints()
    if license_hints:
        issues.append(f"license placeholders still present: {', '.join(license_hints)}")

    release_warnings = release_manifest_warnings()
    # A beta channel with manual update strategy deliberately declares no artifacts
    # (nothing is published to the update feed while the release is private). The
    # warning stays visible in the report; it is not a repo-cleanliness failure.
    blocking = [w for w in release_warnings if w != "release manifest does not declare any artifacts"]
    if blocking:
        issues.extend(blocking)

    root_clutter = _legacy_root_clutter()
    if root_clutter:
        issues.append(f"legacy root clutter present: {', '.join(root_clutter)}")

    return {
        "status": "CLEAN" if not issues else "FAIL",
        "repo_key_artifacts": key_artifacts,
        "license_placeholders": license_hints,
        "release_warnings": release_warnings,
        "legacy_root_clutter": root_clutter,
        "issues": issues,
    }


def main() -> int:
    report = build_report()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "CLEAN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
