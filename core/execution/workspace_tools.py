from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .artifacts import (
    DestinationChangedError,
    ReviewedBaseChangedError,
    atomic_write_text,
    build_file_diff_artifact,
    pinned_atomic_write_text,
    pinned_remove_created_file,
    record_workspace_mutation,
)

# Enumeration ceilings. These bound pathological input, not ordinary projects. The old values (240
# for a tree, 100 for a symbol sweep) bound on almost every real repository, and because both walks
# are `sorted()` the cut removed the alphabetical tail rather than sampling — so the model saw a
# confident, complete-looking view of `a`..`p` and no signal that the rest of the tree existed.
_TREE_CEILING = 5000
_TREE_SCAN_CEILING = 50000
_SYMBOL_SEARCH_CEILING = 1000

_SYMBOL_DEFINITION_TEMPLATES = (
    ("function_definition", r"^\s*def\s+{symbol}\b"),
    ("class_definition", r"^\s*class\s+{symbol}\b"),
    ("assignment", r"^\s*{symbol}\s*="),
    ("javascript_function", r"^\s*(?:export\s+)?function\s+{symbol}\b"),
    ("javascript_const", r"^\s*(?:export\s+)?(?:const|let|var)\s+{symbol}\b"),
)
_PATCH_TARGET_RE = re.compile(r"^(?:\+\+\+|---)\s+(?P<path>.+)$", re.MULTILINE)
_HUNK_HEADER_RE = re.compile(
    r"^@@\s+\-(?P<old_start>\d+)(?:,(?P<old_count>\d+))?\s+\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))?\s+@@"
)
_WORKSPACE_IGNORED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".venv",
        "venv",
        "build",
        "dist",
        "node_modules",
    }
)
_WORKSPACE_IGNORED_SUFFIXES = (".pyc", ".pyo")
_MAX_WORKSPACE_SCAN_LIMIT = 100_000


def resolve_workspace_path(raw_path: str | None, *, workspace_root: Path, write: bool = False) -> Path:
    raw = str(raw_path or "").strip()
    if not raw:
        return _unprotected(workspace_root, write=write)
    candidate = Path(raw).expanduser()
    candidate = (workspace_root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    if candidate != workspace_root and workspace_root not in candidate.parents:
        # Confinement is a hard boundary (prompt-injection gauntlet). Fail HONESTLY rather than
        # silently re-rooting to some in-project file the caller did not name: name the bound project
        # and point at the correct next step, so a legitimate caller can retry with an in-project path
        # (or bind a chat to the other folder) instead of hitting a cryptic dead-end. The leading
        # phrase is load-bearing -- callers and containment tests match "escapes the active workspace".
        project = workspace_root.name or str(workspace_root)
        raise ValueError(
            f"Path escapes the active workspace ({project}). "
            "I can only read or change files inside the bound project folder — "
            "use a path relative to it, or open a chat bound to the other folder."
        )
    return _unprotected(candidate, write=write)


def _unprotected(path: Path, *, write: bool) -> Path:
    """The path, unless it is this runtime's own protected state (or, for a write, its running code): workspace tools
    never reach those even when a workspace folder contains them (core.runtime_state_protection)."""
    from core.runtime_state_protection import refusal

    reason = refusal(path, write=write)
    if reason:
        raise ValueError(reason)
    return path


def relative_path(path: Path, *, workspace_root: Path) -> str:
    try:
        return path.relative_to(workspace_root).as_posix() or "."
    except Exception:
        return path.as_posix()


def is_probably_text(path: Path) -> bool:
    try:
        sample = path.read_bytes()[:4096]
    except Exception:
        return False
    return b"\x00" not in sample


def force_fresh_source_timestamp(path: Path, *, minimum_mtime_ns: int = 0) -> None:
    if not path.exists() or not path.is_file():
        return
    stat_result = path.stat()
    target_ns = max(
        int(stat_result.st_mtime_ns) + 1,
        int(minimum_mtime_ns or 0) + 1_000_000_000,
        time.time_ns() + 1_000_000_000,
    )
    os.utime(path, ns=(target_ns, target_ns))


# The runtime's own control plane (`core/control_plane_workspace.py`) writes telemetry -- including
# the verbatim `final_response` of every past turn -- into `<workspace_root>/control/`. When the
# fallback workspace IS the search root, that directory is part of the corpus a user's code search
# greps, and the effect measured 2026-07-31 is a feedback loop: a turn fabricated
# `workspace/process_inspector.py`, the answer was persisted to `control/runs/`, and a later search
# for that symbol returned the runtime's own earlier fabrication as evidence -- 122 copies of the
# invented path across `control/runs/` and `control/metrics/`, growing with each probe.
#
# Keyed on the control-plane MARKER, not on the name: a user project with a real `control/` source
# directory has no `control/metrics/runtime_truth.json`, so its code stays searchable.
_CONTROL_PLANE_MARKER = Path("control") / "metrics" / "runtime_truth.json"


def _is_runtime_control_plane_dir(relative: Path, *, workspace_root: Path) -> bool:
    if not relative.parts or relative.parts[0] != "control":
        return False
    return (workspace_root / _CONTROL_PLANE_MARKER).is_file()


def _workspace_file_is_ignored(path: Path, *, workspace_root: Path) -> bool:
    relative = Path(relative_path(path, workspace_root=workspace_root))
    if path.suffix.lower() in _WORKSPACE_IGNORED_SUFFIXES:
        return True
    if _is_runtime_control_plane_dir(relative, workspace_root=workspace_root):
        return True
    return any(part in _WORKSPACE_IGNORED_DIRS or part.startswith(".") for part in relative.parts)


def workspace_scan_limit(arguments: dict[str, Any]) -> int | None:
    """Return an optional caller-requested scan bound.

    Workspace search is complete by default. Callers that need a bounded scan can
    provide ``scan_limit``; the result then reports truncation instead of implying
    that the unvisited files were checked.
    """
    raw = arguments.get("scan_limit")
    if raw is None or str(raw).strip() == "":
        return None
    return max(1, min(int(raw), _MAX_WORKSPACE_SCAN_LIMIT))


def iter_workspace_files_with_status(
    target: Path,
    *,
    workspace_root: Path,
    glob_pattern: str,
    limit: int | None,
) -> tuple[list[Path], bool]:
    """Return eligible files and whether an optional scan budget was reached."""
    if target.is_file():
        return ([] if _workspace_file_is_ignored(target, workspace_root=workspace_root) else [target]), False
    safe_limit = None if limit is None else max(1, int(limit or 1))
    matches: list[Path] = []
    for path in sorted(target.rglob("*")):
        if not path.is_file() or _workspace_file_is_ignored(path, workspace_root=workspace_root):
            continue
        relative = relative_path(path, workspace_root=workspace_root)
        if glob_pattern not in {"", "*", "**", "**/*"} and not fnmatch.fnmatch(relative, glob_pattern) and not fnmatch.fnmatch(path.name, glob_pattern):
            continue
        if safe_limit is not None and len(matches) >= safe_limit:
            return matches, True
        matches.append(path)
    return matches, False


def iter_workspace_files(
    target: Path,
    *,
    workspace_root: Path,
    glob_pattern: str,
    limit: int | None,
) -> list[Path]:
    matches, _truncated = iter_workspace_files_with_status(
        target,
        workspace_root=workspace_root,
        glob_pattern=glob_pattern,
        limit=limit,
    )
    return matches


def list_tree_workspace(arguments: dict[str, Any], *, workspace_root: Path) -> dict[str, Any]:
    target = resolve_workspace_path(arguments.get("path"), workspace_root=workspace_root)
    limit = max(1, min(int(arguments.get("limit") or 240), _TREE_CEILING))
    matched: list[dict[str, Any]] = []
    if target.is_file():
        matched.append({"path": relative_path(target, workspace_root=workspace_root), "kind": "file"})
    else:
        root_relative = relative_path(target, workspace_root=workspace_root)
        # Collect everything, then page. Breaking out of a `sorted()` walk at the limit does not
        # sample the tree, it amputates its tail — and the old `truncated` flag was inferred from
        # `len(rows) >= limit` rather than from a real total, so nothing could say how much was cut.
        for path in sorted(target.rglob("*")):
            if len(matched) >= _TREE_SCAN_CEILING:
                break
            rel = relative_path(path, workspace_root=workspace_root)
            if any(part.startswith(".") for part in Path(rel).parts):
                continue
            matched.append({"path": rel, "kind": "directory" if path.is_dir() else "file"})
        if root_relative == "." and not matched:
            matched = []
    rows = matched[:limit]
    dropped = len(matched) - len(rows)
    if not rows:
        return {
            "ok": True,
            "status": "no_results",
            "response_text": f"No files or directories matched inside `{relative_path(target, workspace_root=workspace_root)}`.",
            "details": {"path": relative_path(target, workspace_root=workspace_root), "entries": [], "truncated": False},
        }
    rendered = [f"Workspace tree under `{relative_path(target, workspace_root=workspace_root)}`:"]
    for row in rows:
        marker = "/" if row["kind"] == "directory" else ""
        rendered.append(f"- {row['path']}{marker}")
    if dropped:
        rendered.append(
            f"\n…and {dropped} more entr(ies) not listed ({len(matched)} in total, listed "
            "alphabetically). This tree is a prefix, not the whole project: it cannot support any "
            "conclusion that something is ABSENT here."
        )
    return {
        "ok": True,
        # A truncated tree used to carry status "executed", so nothing downstream could tell a
        # complete answer from a paged one.
        "status": "truncated" if dropped else "executed",
        "response_text": "\n".join(rendered),
        "details": {
            "path": relative_path(target, workspace_root=workspace_root),
            "entries": rows,
            "total": len(matched),
            "dropped": dropped,
            "truncated": bool(dropped),
        },
    }


def symbol_search_workspace(arguments: dict[str, Any], *, workspace_root: Path) -> dict[str, Any]:
    symbol = str(arguments.get("symbol") or arguments.get("query") or "").strip()
    if not symbol:
        return {
            "ok": False,
            "status": "invalid_arguments",
            "response_text": "workspace.symbol_search needs a non-empty `symbol`.",
            "details": {"symbol": "", "matches": []},
        }
    target = resolve_workspace_path(arguments.get("path"), workspace_root=workspace_root)
    glob_pattern = str(arguments.get("glob") or "**/*").strip() or "**/*"
    limit = max(1, min(int(arguments.get("limit") or 50), _SYMBOL_SEARCH_CEILING))
    escaped = re.escape(symbol)
    patterns = [(kind, re.compile(template.format(symbol=escaped))) for kind, template in _SYMBOL_DEFINITION_TEMPLATES]
    matches: list[dict[str, Any]] = []
    scan_limit = workspace_scan_limit(arguments)
    scan, truncated = iter_workspace_files_with_status(
        target,
        workspace_root=workspace_root,
        glob_pattern=glob_pattern,
        limit=scan_limit,
    )
    for path in scan:
        if not is_probably_text(path):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for index, line in enumerate(lines, start=1):
            kind = ""
            for candidate_kind, pattern in patterns:
                if pattern.search(line):
                    kind = candidate_kind
                    break
            if not kind and symbol not in line:
                continue
            if not kind:
                kind = "reference"
            rel = relative_path(path, workspace_root=workspace_root)
            matches.append({"path": rel, "line": index, "kind": kind, "snippet": line.strip()[:220]})
            if len(matches) >= limit:
                break
        if len(matches) >= limit:
            break
    if not matches:
        status = "truncated_no_results" if truncated else "no_results"
        return {
            "ok": True,
            "status": status,
            "response_text": (
                f'No symbol matches for "{symbol}" were found in the workspace.'
                + (f" The eligible-file scan was truncated at {scan_limit} files, so this is not a complete negative result." if truncated else "")
            ),
            "details": {"symbol": symbol, "matches": [], "truncated": truncated, "scan_limit": scan_limit},
        }
    rendered = [f'Symbol matches for "{symbol}":']
    for row in matches:
        rendered.append(f"- {row['path']}:{row['line']} [{row['kind']}] {row['snippet']}")
    return {
        "ok": True,
        "status": "truncated" if truncated else "executed",
        "response_text": "\n".join(rendered) + (f"\n\nThe eligible-file scan was truncated at {scan_limit} files." if truncated else ""),
        "details": {
            "symbol": symbol,
            "matches": matches,
            "match_count": len(matches),
            "truncated": truncated,
            "scan_limit": scan_limit,
        },
    }


def apply_unified_diff_workspace(
    arguments: dict[str, Any],
    *,
    workspace_root: Path,
    session_id: str | None,
    reviewed_destinations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    patch_text = str(arguments.get("patch") or arguments.get("diff") or "").strip()
    if not patch_text:
        return {
            "ok": False,
            "status": "invalid_arguments",
            "response_text": "workspace.apply_unified_diff needs a non-empty `patch`.",
            "details": {"paths": []},
        }
    touched_paths = _extract_patch_paths(patch_text)
    if not touched_paths:
        return {
            "ok": False,
            "status": "invalid_arguments",
            "response_text": "No patch targets were found in the supplied unified diff.",
            "details": {"paths": []},
        }
    if reviewed_destinations is not None:
        # An approved code-task patch: only its reviewed destinations, written pinned.
        return _apply_protected_unified_diff(
            patch_text=patch_text,
            touched_paths=touched_paths,
            workspace_root=workspace_root,
            session_id=session_id,
            reviewed_destinations=list(reviewed_destinations),
        )
    snapshots: list[dict[str, Any]] = []
    for relative in touched_paths:
        target = resolve_workspace_path(relative, workspace_root=workspace_root, write=True)
        existed_before = target.exists()
        before_text = target.read_text(encoding="utf-8", errors="replace") if existed_before and target.is_file() else ""
        snapshots.append(
            {
                "path": relative_path(target, workspace_root=workspace_root),
                "existed_before": existed_before,
                "before_text": before_text,
                "before_mtime_ns": int(target.stat().st_mtime_ns) if existed_before and target.is_file() else 0,
                # Same before_mode precision write_file/replace_in_file's mutation records carry --
                # a rolled-back patch on an executable file must restore that file's original mode,
                # not just its content.
                "before_mode": (target.stat().st_mode & 0o7777) if existed_before and target.is_file() else None,
            }
        )

    applied_with = ""
    errors: list[str] = []
    git_available = shutil.which("git")
    if git_available:
        git_cmd = [git_available, "-C", str(workspace_root), "apply", "--whitespace=nowarn", "--recount", "-"]
        try:
            git_result = subprocess.run(git_cmd, input=patch_text, text=True, capture_output=True, timeout=60)
        except subprocess.TimeoutExpired:
            errors.append("git apply timed out after 60s")
            git_result = None
        if git_result is not None and git_result.returncode == 0:
            applied_with = "git_apply"
        else:
            errors.append((git_result.stderr or git_result.stdout or "").strip())
    if not applied_with:
        try:
            _apply_unified_diff_python(patch_text, workspace_root=workspace_root)
            applied_with = "python_fallback"
        except Exception as exc:
            errors.append(str(exc).strip())
    if not applied_with:
        patch_available = shutil.which("patch")
        if patch_available:
            strip = "1" if "/dev/null" in patch_text or " a/" in patch_text or " b/" in patch_text or "\na/" in patch_text else "0"
            # `--fuzz=0` is load-bearing, not a style choice: GNU patch's default fuzz factor (2)
            # will drop mismatched context lines and search nearby for an approximate match, which
            # is exactly the "minor whitespace tolerance applies the patch to the wrong location"
            # failure this tool must not have. Fuzz=0 forces an exact context match at this,
            # the last-resort engine, so a bad match surfaces as a clean apply_failed instead of a
            # silent wrong-location write.
            patch_cmd = [patch_available, f"-p{strip}", "-d", str(workspace_root), "--forward", "--batch", "--fuzz=0"]
            try:
                patch_result = subprocess.run(patch_cmd, input=patch_text, text=True, capture_output=True, timeout=60)
            except subprocess.TimeoutExpired:
                errors.append("patch timed out after 60s")
                patch_result = None
            if patch_result is not None and patch_result.returncode == 0:
                applied_with = "patch"
            else:
                errors.append((patch_result.stderr or patch_result.stdout or "").strip())
    if not applied_with:
        error_text = "; ".join(item for item in errors if item) or "No supported patch engine was available."
        return {
            "ok": False,
            "status": _classify_patch_apply_failure(errors),
            "response_text": f"I could not apply the unified diff: {error_text}",
            "details": {"paths": touched_paths, "errors": errors},
        }

    artifacts: list[dict[str, Any]] = []
    mutation_changes: list[dict[str, Any]] = []
    changed_paths: list[str] = []
    for snapshot in snapshots:
        target = resolve_workspace_path(snapshot["path"], workspace_root=workspace_root, write=True)
        exists_after = target.exists()
        after_text = target.read_text(encoding="utf-8", errors="replace") if exists_after and target.is_file() else ""
        if exists_after and snapshot["before_text"].endswith("\n") and after_text and not after_text.endswith("\n"):
            after_text = after_text + "\n"
            atomic_write_text(target, after_text)
        if exists_after and target.is_file():
            force_fresh_source_timestamp(target, minimum_mtime_ns=int(snapshot.get("before_mtime_ns") or 0))
        action = "updated"
        if not snapshot["existed_before"] and exists_after:
            action = "created"
        elif snapshot["existed_before"] and not exists_after:
            action = "deleted"
        artifacts.append(
            build_file_diff_artifact(
                path=snapshot["path"],
                action=action,
                before=snapshot["before_text"],
                after=after_text,
                extra={"engine": applied_with},
            )
        )
        # A patch that applied cleanly and changed nothing is not a success. `git apply` exits 0 for
        # an already-applied patch, and the engine below reported that as "Applied unified diff:",
        # so a model was told its edit landed when the file on disk was untouched - it then verified
        # against its own false report and moved on. Existence is part of the comparison: creating
        # an empty file is a real change even though both texts are "".
        if snapshot["before_text"] != after_text or bool(snapshot["existed_before"]) != bool(exists_after):
            changed_paths.append(snapshot["path"])
        mutation_changes.append(
            {
                "path": snapshot["path"],
                "action": action,
                "existed_before": bool(snapshot["existed_before"]),
                "existed_after": bool(exists_after),
                "before_text": snapshot["before_text"],
                "after_text": after_text,
                "before_mode": snapshot.get("before_mode"),
            }
        )
    if not changed_paths:
        # Refused BEFORE a mutation record is written: recording a mutation that moved nothing would
        # put a revert entry in the activity panel for an edit that never happened. The model needs
        # to know its patch was a no-op so it can re-read and re-plan, rather than verify against a
        # success it was handed.
        return {
            "ok": False,
            "status": "no_change",
            "response_text": (
                "The patch applied without error but changed nothing on disk"
                f" ({', '.join(touched_paths) or 'no paths named'}). It is most likely already"
                " applied, or it targets content that is no longer there. Re-read the file before"
                " patching it again."
            ),
            "details": {
                "paths": touched_paths,
                "changed_paths": [],
                "artifacts": artifacts,
                "engine": applied_with,
            },
        }

    mutation_record = record_workspace_mutation(
        session_id=session_id,
        workspace_root=workspace_root,
        intent="workspace.apply_unified_diff",
        changes=mutation_changes,
    )
    rendered = ["Applied unified diff:"]
    for path in changed_paths:
        rendered.append(f"- {path}")
    return {
        "ok": True,
        "status": "executed",
        "response_text": "\n".join(rendered),
        "details": {
            "paths": touched_paths,
            # The paths the patch NAMED and the paths it actually moved are different facts. A
            # caller checking whether an edit landed has to read the second one.
            "changed_paths": changed_paths,
            "artifacts": artifacts,
            "engine": applied_with,
            "mutation_record": {
                "mutation_id": str(mutation_record.get("mutation_id") or "").strip(),
                "session_key": str(mutation_record.get("session_key") or "").strip(),
            },
        },
    }


_GIT_METADATA_LINES = ("diff --git ", "index ")
_GIT_UNSUPPORTED_HEADERS = (
    "rename from ", "rename to ", "copy from ", "copy to ", "similarity index ", "dissimilarity index ",
    "old mode ", "new mode ", "deleted file mode ", "GIT binary patch", "Binary files ",
)


def _protected_patch_text(patch_text: str) -> tuple[str, str]:
    """The patch as the protected writer renders it, or ("", header) for the first header it does not apply.

    git's file-separator metadata -- `diff --git`, `index`, and a new file's default `new file mode 100644`
    -- names no content and is dropped; the Python engine stops a hunk at any line it cannot place, so
    leaving these in splits a multi-file git patch mid-hunk. Headers that change something the engine does
    not apply (renames, copies, modes, binary content, deletions) are reported instead. Hunk lines always
    carry a one-character prefix, so no content line can match a header."""
    kept: list[str] = []
    for line in str(patch_text or "").splitlines(keepends=True):
        header = line.rstrip("\n")
        if header.startswith(_GIT_UNSUPPORTED_HEADERS) or (
            header.startswith("new file mode ") and header != "new file mode 100644"
        ):
            return "", header
        if header.startswith(_GIT_METADATA_LINES) or header == "new file mode 100644":
            continue
        kept.append(line)
    return "".join(kept), ""


# Characters `str.splitlines` treats as line boundaries besides "\n". A unified diff counts lines by "\n"
# alone, so any of these in a patched file or in the patch would move a hunk or rewrite line endings.
_NOT_DIFF_LINE_BREAKS = ("\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029")


def _protected_render_obstacle(patch_text: str, files: list[tuple[str, bytes | None]]) -> str:
    """Why this module's Python engine would not reproduce an approved patch byte for byte, or "" if it would.

    The engine splits lines with `str.splitlines`, ends every line it adds with "\\n", and skips
    `\\ No newline at end of file`. So the protected writer applies a patch only when the patch and the
    reviewed bytes of every file it touches are UTF-8 text whose only line boundary is "\\n", each such file
    ends with a newline (or is empty), and the patch carries no no-newline marker. ``files`` pairs each path
    with its reviewed bytes (None for a file that does not exist yet)."""
    if any(mark in patch_text for mark in _NOT_DIFF_LINE_BREAKS):
        return "the patch contains carriage returns or other characters Python treats as line breaks"
    if any(line.startswith("\\") for line in patch_text.split("\n")):
        return "the patch changes whether a file ends with a newline"
    for relative, reviewed in files:
        if reviewed is None:
            continue
        try:
            decoded = reviewed.decode("utf-8")
        except UnicodeDecodeError:
            return f"`{relative}` is not UTF-8 text"
        if any(mark in decoded for mark in _NOT_DIFF_LINE_BREAKS):
            return f"`{relative}` contains carriage returns or other characters Python treats as line breaks"
        if decoded and not decoded.endswith("\n"):
            return f"`{relative}` does not end with a newline"
    return ""


def _compensate_protected_writes(
    written: list[tuple[Path, str, tuple[int, int]]],
    *,
    snapshots: dict[str, dict[str, Any]],
    verified_text: dict[str, str],
) -> dict[str, Any]:
    """Return each file an approved patch already wrote to its state before the patch, newest first.

    A file that existed gets its reviewed bytes and recorded mode back through the pinned writer, only while
    it still holds the bytes this patch wrote. A file the patch created is removed through
    `pinned_remove_created_file`, only while it is still the entry the patch created, holding those bytes.
    A file another writer edited, replaced (even with the same bytes) or turned into a link stays as that
    writer left it, and so does one the compensation could not write or remove. The result names what
    happened to every file: ``restored_paths``, ``removed_paths``, ``already_restored_paths`` (a new file
    already gone), ``restore_failures`` and ``changed_paths`` -- the files still different from their state
    before the patch -- ``restore_complete``, and ``note``, the sentence that says so."""
    restored: list[str] = []
    removed: list[str] = []
    already: list[str] = []
    failures: list[dict[str, str]] = []
    for path, text, identity in reversed(written):
        snapshot = snapshots[str(path)]
        relative = str(snapshot["path"])
        written_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        try:
            if snapshot["existed_before"]:
                pinned_atomic_write_text(path, verified_text[str(path)], expected_prior_sha256=written_sha256,
                                         mode=snapshot.get("before_mode"))
                restored.append(relative)
            elif pinned_remove_created_file(path, created_identity=identity, expected_sha256=written_sha256) == "removed":
                removed.append(relative)
            else:
                already.append(relative)
        except OSError as exc:  # refused (edited, replaced, a link) or failed: the file stays as it is
            failures.append({"path": relative, "compensation": "restore" if snapshot["existed_before"] else "remove",
                             "reason": str(exc).replace(str(path), relative)})
    note = ""
    if restored:
        note += f" Restored to their reviewed bytes: {', '.join(restored)}."
    if removed:
        note += f" Removed the files it had created: {', '.join(removed)}."
    if already:
        note += f" Already back to their state before the patch: {', '.join(already)}."
    if failures:
        note += (" NOT restored, and still different from before the patch: "
                 + "; ".join(f"{row['path']} ({row['reason']})" for row in failures) + ".")
    return {"restored_paths": restored, "removed_paths": removed, "already_restored_paths": already,
            "restore_failures": failures, "changed_paths": [row["path"] for row in failures],
            "restore_complete": not failures, "note": note}


def _apply_protected_unified_diff(
    *,
    patch_text: str,
    touched_paths: list[str],
    workspace_root: Path,
    session_id: str | None,
    reviewed_destinations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply an APPROVED patch to exactly the destinations its proposal recorded.

    Before any byte moves, every file the patch names must have a recorded destination, still
    resolve to that canonical target, and still hold the reviewed bytes; the patch is rendered by
    this module's own Python engine (`_apply_unified_diff_python`, collecting instead of writing),
    and each file is then written inside its pinned parent directory with its reviewed bytes
    re-checked just before the rename (`pinned_atomic_write_text`). External engines are not used
    here: they open files by path after any check could run. A patch this engine cannot render
    exactly, or one that deletes a file, is refused before any byte changes -- the same change can be
    proposed as `workspace.write_file` or `workspace.replace_in_file`. So is a patch whose bytes the engine
    would not reproduce (`_protected_render_obstacle`), which takes `workspace.write_file`. Rendering reads
    the bytes verified here, never the path again. If a later file is refused or fails part-way, every file
    already written is returned to its state before the patch (`_compensate_protected_writes`), and the
    result names each file restored or removed and each one left changed."""
    by_path = {str(row.get("path") or ""): row for row in reviewed_destinations if isinstance(row, dict)}

    def refusal(status: str, text: str, **extra: Any) -> dict[str, Any]:
        return {"ok": False, "status": status, "response_text": text,
                "details": {"paths": touched_paths, "changed_paths": [], "engine": "protected_python", **extra}}

    planned: dict[str, dict[str, Any]] = {}
    for relative in touched_paths:
        record = by_path.get(relative)
        if record is None:
            return refusal("destination_unrecorded",
                           f"`{relative}` is changed by this patch but was not recorded when it was reviewed; "
                           "nothing was written. Re-propose the patch so every file it changes is reviewed.")
        try:
            target = resolve_workspace_path(relative, workspace_root=workspace_root)
        except ValueError:
            target = None
        if target is None or str(target) != str(record.get("target") or ""):
            return refusal("destination_changed",
                           f"`{relative}` no longer resolves to the file that was reviewed; nothing was written. "
                           "Re-propose against the files as they are now.")
        reviewed_bytes = target.read_bytes() if target.is_file() else None
        actual = hashlib.sha256(reviewed_bytes).hexdigest() if reviewed_bytes is not None else ""
        if actual != str(record.get("prior_sha256") or ""):
            return refusal("stale_base",
                           f"`{relative}` no longer holds the bytes that were reviewed; nothing was written. "
                           "Re-read it and re-propose.")
        planned[str(target)] = {"relative": relative, "record": record, "target": target, "bytes": reviewed_bytes}

    obstacle = _protected_render_obstacle(
        patch_text, [(str(entry["relative"]), entry["bytes"]) for entry in planned.values()]
    )
    if obstacle:
        return refusal("protected_patch_unsupported",
                       f"The protected writer cannot apply this approved patch byte for byte: {obstacle}. Nothing was "
                       "written. Propose the change as `workspace.write_file` with the full reviewed content, and "
                       "approve that.")
    # Rendered from exactly the bytes verified above, never from a later read of the path.
    verified_text = {key: (entry["bytes"] or b"").decode("utf-8") for key, entry in planned.items()}

    renderable, unsupported_header = _protected_patch_text(patch_text)
    if unsupported_header:
        return refusal("protected_patch_unsupported",
                       f"This approved patch carries `{unsupported_header}`, which the protected writer does not apply; "
                       "nothing was written. Propose the change as `workspace.write_file` (the full reviewed content) "
                       "or `workspace.replace_in_file`, and approve that.")
    rendered: list[tuple[Path, str | None]] = []
    try:
        _apply_unified_diff_python(
            renderable,
            workspace_root=workspace_root,
            sink=lambda path, text: rendered.append((path, text)),
            source=lambda path: verified_text.get(str(path), ""),
        )
    except ValueError as exc:
        return refusal("protected_patch_unsupported",
                       f"This approved patch cannot be applied exactly as reviewed ({exc}); nothing was written. "
                       "Propose the same change as `workspace.write_file` (the full reviewed content) or "
                       "`workspace.replace_in_file`, and approve that.")
    if any(text is None for _path, text in rendered):
        return refusal("protected_patch_unsupported",
                       "An approved patch that deletes a file is not applied through the protected writer; nothing "
                       "was written. Propose the remaining changes as `workspace.write_file` or "
                       "`workspace.replace_in_file`, and remove the file through a reviewed action.")
    if any(str(path) not in planned for path, _text in rendered):
        return refusal("destination_changed",
                       "The patch renders a file that is not one of its reviewed destinations; nothing was written.")

    snapshots: dict[str, dict[str, Any]] = {}
    for path, _text in rendered:
        entry = planned[str(path)]
        existed = path.exists() and path.is_file()
        snapshots[str(path)] = {
            "path": relative_path(path, workspace_root=workspace_root),
            "existed_before": existed,
            "before_text": path.read_text(encoding="utf-8", errors="replace") if existed else "",
            "before_mtime_ns": int(path.stat().st_mtime_ns) if existed else 0,
            "before_mode": (path.stat().st_mode & 0o7777) if existed else None,
            "reviewed_sha256": str(entry["record"].get("prior_sha256") or ""),
        }

    written: list[tuple[Path, str, tuple[int, int]]] = []
    for path, text in rendered:
        snapshot = snapshots[str(path)]
        try:
            identity = pinned_atomic_write_text(path, str(text), expected_prior_sha256=snapshot["reviewed_sha256"])
        except OSError as exc:
            compensation = _compensate_protected_writes(written, snapshots=snapshots, verified_text=verified_text)
            if isinstance(exc, (DestinationChangedError, ReviewedBaseChangedError)):
                status = "stale_base" if isinstance(exc, ReviewedBaseChangedError) else "destination_changed"
                cause = f"`{snapshot['path']}` changed while the approved patch was being written; the patch was stopped."
            else:
                status = "write_failed"
                cause = f"`{snapshot['path']}` could not be written ({exc.strerror or exc}); the patch was stopped."
            note = compensation.pop("note")
            return refusal(status, cause + note, reason=str(exc), **compensation)
        written.append((path, str(text), identity))

    artifacts: list[dict[str, Any]] = []
    mutation_changes: list[dict[str, Any]] = []
    changed_paths: list[str] = []
    for path, text in rendered:
        snapshot = snapshots[str(path)]
        after_text = str(text)
        force_fresh_source_timestamp(path, minimum_mtime_ns=int(snapshot.get("before_mtime_ns") or 0))
        action = "updated" if snapshot["existed_before"] else "created"
        artifacts.append(build_file_diff_artifact(path=snapshot["path"], action=action, before=snapshot["before_text"],
                                                  after=after_text, extra={"engine": "protected_python"}))
        if snapshot["before_text"] != after_text or not snapshot["existed_before"]:
            changed_paths.append(snapshot["path"])
        mutation_changes.append({"path": snapshot["path"], "action": action, "existed_before": bool(snapshot["existed_before"]),
                                 "existed_after": True, "before_text": snapshot["before_text"], "after_text": after_text,
                                 "before_mode": snapshot.get("before_mode")})
    if not changed_paths:
        return {"ok": False, "status": "no_change",
                "response_text": ("The patch applied without error but changed nothing on disk"
                                  f" ({', '.join(touched_paths)}). Re-read the file before patching it again."),
                "details": {"paths": touched_paths, "changed_paths": [], "artifacts": artifacts, "engine": "protected_python"}}
    mutation_record = record_workspace_mutation(
        session_id=session_id, workspace_root=workspace_root, intent="workspace.apply_unified_diff", changes=mutation_changes
    )
    return {
        "ok": True,
        "status": "executed",
        "response_text": "\n".join(["Applied unified diff:"] + [f"- {path}" for path in changed_paths]),
        "details": {
            "paths": touched_paths,
            "changed_paths": changed_paths,
            "artifacts": artifacts,
            "engine": "protected_python",
            "mutation_record": {
                "mutation_id": str(mutation_record.get("mutation_id") or "").strip(),
                "session_key": str(mutation_record.get("session_key") or "").strip(),
            },
        },
    }


def _extract_patch_paths(patch_text: str) -> list[str]:
    candidates: list[str] = []
    for match in _PATCH_TARGET_RE.finditer(str(patch_text or "")):
        raw = str(match.group("path") or "").strip()
        if not raw or raw == "/dev/null":
            continue
        clean = raw
        if clean.startswith("a/") or clean.startswith("b/"):
            clean = clean[2:]
        if clean not in candidates:
            candidates.append(clean)
    return candidates


def _classify_patch_apply_failure(errors: list[str]) -> str:
    """Turn the three engines' raw stderr/exception text into one of the distinct failure statuses
    CORE INVARIANT 5 requires (ambiguous match, stale base, syntax-invalid) instead of one opaque
    `apply_failed` for every reason a patch can fail to land.

    The Python fallback (`_apply_unified_diff_python`) is the most diagnostic of the three engines:
    it fails with a specific, worded exception for each distinct cause, where `git apply`/`patch`
    only return free-text stderr. Its wording is checked first because it is the most reliable
    signal; git/patch stderr is checked only as a fallback when the Python engine wasn't reached
    (e.g., a malformed diff `git apply` also rejected before the Python engine ran).
    """
    combined = " ".join(str(item or "") for item in errors)
    lowered = combined.lower()
    if "malformed unified diff" in lowered or "unsupported unified diff line" in lowered or "overlapping hunks" in lowered:
        return "invalid_patch_syntax"
    if "context mismatch" in lowered or "removal mismatch" in lowered:
        return "stale_base"
    if "can't find file to patch" in lowered or "no file to patch" in lowered:
        return "target_missing"
    return "apply_failed"


def _normalize_patch_path(raw_path: str) -> str:
    clean = str(raw_path or "").strip()
    if clean.startswith("a/") or clean.startswith("b/"):
        clean = clean[2:]
    return clean


def _apply_unified_diff_python(patch_text: str, *, workspace_root: Path, sink: Any = None, source: Any = None) -> None:
    # ``sink(path, text)`` receives each rendered file in patch order instead of the write (``None``
    # text for a deletion); without one, each file is written or removed as it is rendered.
    # ``source(path)`` supplies a file's current text instead of a read of the path ("" for a file that
    # does not exist); the protected writer renders from the bytes it verified.
    lines = str(patch_text or "").splitlines()
    index = 0
    applied_any = False
    while index < len(lines):
        line = lines[index]
        if not line.startswith("--- "):
            index += 1
            continue
        old_raw = str(line[4:] or "").strip()
        index += 1
        if index >= len(lines) or not lines[index].startswith("+++ "):
            raise ValueError("Malformed unified diff: missing `+++` header.")
        new_raw = str(lines[index][4:] or "").strip()
        index += 1
        target_raw = new_raw if new_raw != "/dev/null" else old_raw
        target_path = resolve_workspace_path(_normalize_patch_path(target_raw), workspace_root=workspace_root, write=True)
        if source is not None:
            before_lines = str(source(target_path) or "").splitlines(keepends=True)
        else:
            before_lines = (
                target_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
                if target_path.exists() and target_path.is_file()
                else []
            )
        cursor = 0
        rendered: list[str] = []
        saw_hunk = False
        while index < len(lines):
            current = lines[index]
            if current.startswith("--- "):
                break
            if not current.startswith("@@ "):
                index += 1
                continue
            match = _HUNK_HEADER_RE.match(current)
            if not match:
                raise ValueError(f"Malformed unified diff hunk header: {current}")
            saw_hunk = True
            old_start = max(0, int(match.group("old_start") or "0") - 1)
            if old_start < cursor:
                raise ValueError("Malformed unified diff: overlapping hunks are not supported.")
            rendered.extend(before_lines[cursor:old_start])
            cursor = old_start
            index += 1
            while index < len(lines):
                hunk_line = lines[index]
                if hunk_line.startswith("@@ ") or hunk_line.startswith("--- "):
                    break
                if hunk_line == r"\ No newline at end of file":
                    index += 1
                    continue
                prefix = hunk_line[:1]
                body = hunk_line[1:]
                if prefix == " ":
                    if cursor >= len(before_lines) or before_lines[cursor].rstrip("\n") != body:
                        raise ValueError(f"Unified diff context mismatch for `{target_path.name}`.")
                    rendered.append(before_lines[cursor])
                    cursor += 1
                elif prefix == "-":
                    if cursor >= len(before_lines) or before_lines[cursor].rstrip("\n") != body:
                        raise ValueError(f"Unified diff removal mismatch for `{target_path.name}`.")
                    cursor += 1
                elif prefix == "+":
                    rendered.append(body + "\n")
                else:
                    raise ValueError(f"Unsupported unified diff line: {hunk_line}")
                index += 1
        if not saw_hunk:
            raise ValueError("Malformed unified diff: no hunks were found.")
        rendered.extend(before_lines[cursor:])
        if sink is not None:
            sink(target_path, None if new_raw == "/dev/null" else "".join(rendered))
        elif new_raw == "/dev/null":
            if target_path.exists():
                target_path.unlink()
        else:
            atomic_write_text(target_path, "".join(rendered))
        applied_any = True
    if not applied_any:
        raise ValueError("Malformed unified diff: no file headers were found.")
