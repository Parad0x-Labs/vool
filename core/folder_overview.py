"""Ground a "what is this project about / check this folder" answer in the REAL folder on disk.

A small local model (qwen3:8b/14b) is unreliable at emitting a workspace-read tool call, so when
asked to look at a folder it tends to say "I can't access folders on your machine" and then
confabulate a project from whatever leaked into its context (old chat titles, render filenames).
This module reads the actual directory — top-level listing, detected stack, README — and returns a
grounded overview the fast-path serves directly, so the answer is what is genuinely there, never a
guess. It is confined to the workspace root it is handed (already resolved + confined server-side)
and size-caps every read.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

# Folders that are noise for an "explain this project" overview — build output, caches, VCS internals.
_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env", "dist", "build", ".next",
    ".turbo", ".cache", ".idea", ".vscode", "target", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "coverage", ".gradle", ".tox", "vendor", ".parcel-cache",
}
_README_NAMES = ("README.md", "README.MD", "Readme.md", "readme.md", "README", "README.txt", "readme.txt")
# First-matching manifest -> the stack it implies. Order matters only for readability of the list.
_MANIFEST_STACK = (
    ("package.json", "Node.js / JavaScript"),
    ("tsconfig.json", "TypeScript"),
    ("pyproject.toml", "Python"),
    ("requirements.txt", "Python"),
    ("setup.py", "Python"),
    ("Cargo.toml", "Rust"),
    ("go.mod", "Go"),
    ("pom.xml", "Java (Maven)"),
    ("build.gradle", "Java/Kotlin (Gradle)"),
    ("Gemfile", "Ruby"),
    ("composer.json", "PHP"),
    ("CMakeLists.txt", "C/C++ (CMake)"),
    ("Dockerfile", "Docker"),
    ("index.html", "Static web"),
    ("Anchor.toml", "Solana / Anchor"),
)
_MAX_ENTRIES = 40
_README_PARAGRAPH_CHARS = 600


def _safe_iterdir(root: Path) -> list[Path]:
    try:
        # Folders first, then files, each alphabetical — a stable, readable order.
        return sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except OSError:
        return []


def _summarize_readme(text: str) -> str:
    """The README's own title + first real paragraph, so the summary is the project's own words."""
    title = ""
    paragraph = ""
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not title and line.startswith("# "):
            title = line.lstrip("#").strip()
            continue
        if paragraph:
            continue
        # Skip headings, badge/image lines, and blank lines when hunting the first real sentence.
        if line and not line.startswith("#") and not line.startswith("![") and not line.startswith("[!"):
            paragraph = line[:_README_PARAGRAPH_CHARS].strip()
    if title and paragraph:
        return f"**{title}** — {paragraph} (from its README)"
    if title:
        return f"**{title}** (from its README)"
    if paragraph:
        return f"{paragraph} (from its README)"
    return ""


def _detected_stack(file_names: set[str]) -> list[str]:
    stack: list[str] = []
    for manifest, label in _MANIFEST_STACK:
        if manifest in file_names and label not in stack:
            stack.append(label)
    return stack


def _unbound_guidance(path: str = "") -> str:
    where = f" (`{path}` isn't a folder I can open)" if path else ""
    return (
        "This chat isn't pointed at a project folder yet" + where + ", so there's nothing on disk "
        "for me to read.\n\n"
        "Two ways to fix that:\n"
        "- Click **+ Project** in the sidebar and pick the folder — every chat inside it works in "
        "that folder.\n"
        "- Or just give me a path, e.g. `read ~/Desktop/my-app/README.md`, and I'll open it right here."
    )


def _empty_guidance(path: str, *, project_bound: bool) -> str:
    if project_bound:
        return (
            f"This project's folder — `{path}` — is empty (no files or subfolders yet). "
            "Add files there and I'll read them, or give me a path to look at."
        )
    return (
        f"The workspace folder — `{path}` — is empty. To have me read a real project, open this chat "
        "inside a project (the **+ Project** button) or give me a path like `read ~/Desktop/my-app`."
    )


def build_folder_overview(workspace_root: str, *, project_bound: bool = False) -> str:
    """A grounded, deterministic overview of ``workspace_root`` — real listing + stack + README.

    Returns clear guidance (never a hallucinated project) when the folder is missing or empty.
    """
    raw = str(workspace_root or "").strip()
    if not raw:
        return _unbound_guidance()
    root = Path(os.path.expanduser(raw))
    try:
        if not root.is_dir():
            return _unbound_guidance(path=str(root))
    except OSError:
        return _unbound_guidance(path=str(root))

    entries = [p for p in _safe_iterdir(root) if p.name not in _SKIP_DIRS and not p.name.startswith(".")]
    dirs = [p for p in entries if p.is_dir()]
    files = [p for p in entries if p.is_file()]
    if not dirs and not files:
        return _empty_guidance(str(root), project_bound=project_bound)

    file_names = {p.name for p in files}
    stack = _detected_stack(file_names)

    readme_summary = ""
    for readme_name in _README_NAMES:
        candidate = root / readme_name
        try:
            if candidate.is_file():
                readme_summary = _summarize_readme(candidate.read_text(encoding="utf-8", errors="replace")[:4000])
                break
        except OSError:
            continue

    lines: list[str] = [f"Here's what's actually in `{root}`:", ""]
    if readme_summary:
        lines.extend([readme_summary, ""])
    if stack:
        lines.extend([f"**Stack:** {', '.join(stack)}.", ""])
    lines.append(
        f"**Top level** ({len(dirs)} folder{'' if len(dirs) == 1 else 's'}, "
        f"{len(files)} file{'' if len(files) == 1 else 's'}):"
    )
    shown = 0
    for directory in dirs:
        if shown >= _MAX_ENTRIES:
            break
        lines.append(f"- 📁 {directory.name}/")
        shown += 1
    for file in files:
        if shown >= _MAX_ENTRIES:
            break
        lines.append(f"- 📄 {file.name}")
        shown += 1
    remaining = (len(dirs) + len(files)) - shown
    if remaining > 0:
        lines.append(f"- …and {remaining} more")
    lines.extend(["", "Want me to open any of these? Say `read <filename>`, or ask about a specific part."])
    return "\n".join(lines)


# Directories excluded from an EXACT count. Narrower than `_SKIP_DIRS` on purpose: an overview may
# treat vendored or built code as noise, but a count the user was told not to estimate can only
# exclude what is genuinely not project content — VCS internals, caches, virtualenvs, dependency
# installs. Whatever is excluded is stated in the answer, so the number is always reproducible.
_MEASUREMENT_SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".venv", "venv", "env", "node_modules",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
}
_MEASUREMENT_MAX_FILE_BYTES = 64 * 1024 * 1024
_MEASUREMENT_CHUNK = 1024 * 1024
# Traversal budgets. Generous by a wide margin over any real repository (a large tree measures in
# a few seconds and a few tens of megabytes), but finite: an exactness claim must TERMINATE, and a
# workspace pathological enough to hit a budget gets an honest "not exact" answer, not a hang.
_MEASUREMENT_MAX_ENTRIES = 250_000
_MEASUREMENT_MAX_TOTAL_BYTES = 512 * 1024 * 1024
_MEASUREMENT_MAX_SECONDS = 30.0

#: Line-count outcomes that are properties of the FILE itself — stable if the walk were retried,
#: and reported as exclusions. The race outcomes ("vanished"/"race"/"mutated"/"post-stat-failed")
#: mean the WORKSPACE changed under the walk — or its state could not be verified afterwards —
#: which is why they make the measurement incomplete instead of just an exclusion. The budget
#: outcomes ("total-byte-budget"/"time-budget") mean the reader stopped at the walk's remaining
#: budget mid-file.
_FILE_LINE_OUTCOMES = ("binary", "too-large", "unreadable")
_FILE_RACE_OUTCOMES = ("vanished", "race", "mutated", "post-stat-failed")


@dataclass(frozen=True)
class _FileLineRead:
    """The typed outcome of reading one file.

    `outcome` is "measured" when `lines` is an exact count of the bytes actually read; one of the
    file-property outcomes ("binary"/"too-large"/"unreadable") when the file itself is not
    line-countable; one of the race outcomes when the workspace changed underneath the read or its
    post-read state could not be verified; or a budget outcome when the reader stopped at the
    walk's remaining budget. `bytes_read` is what was actually read — the walk's byte accounting
    is built from this, never from a later stat that may fail or disagree.
    """

    outcome: str
    lines: int = 0
    bytes_read: int = 0


@dataclass(frozen=True)
class WorkspaceFileMeasurement:
    """The typed result of one exact-count traversal.

    `complete` IS the exactness claim: True only when every directory under the root was streamed
    to its end, every matched file was counted, none of the budgets fired, and every entry was
    verified unchanged after reading. `termination_reason` is "" exactly when `complete` is True,
    and otherwise names why exactness could not be established. Callers render the exact answer
    only from a complete measurement; an incomplete one is a bounded partial, never dressed up as
    exact.
    """

    complete: bool
    count: int
    largest_relpath: str
    largest_lines: int
    skipped: dict[str, int]
    unscannable_directories: tuple[str, ...]
    raced_entries: tuple[str, ...]
    termination_reason: str


def _count_file_lines(path, *, remaining_bytes=None, deadline=None) -> _FileLineRead:
    """Exact newline count of a text file, bounded by the walk's remaining budget.

    Returns a `_FileLineRead`. Read in binary chunks so encoding can never fail the count; a NUL
    byte in the first chunk marks a binary blob, because a line count of binary data is a
    fabricated number.

    The open is race-hardened: `O_NOFOLLOW` where the platform supports it, and an fstat/lstat
    dev+ino comparison so the object OPENED is proven to be the object ENUMERATED. The chunk loop
    enforces every budget that can bite mid-file — the per-file ceiling, the walk's remaining
    total-byte budget, and the deadline — so a file growing while read cannot outread them; the
    request size is clamped to the remaining budget, so no material over-read happens either.
    After reading, the file is verified twice more: fstat on the open fd, and lstat after close,
    must still be the same object with the same size and mtime as enumerated. Any unverifiable or
    changed post-read state is a race outcome ("race"/"mutated"/"post-stat-failed") — never an
    exact count.
    """
    import errno

    try:
        before = os.lstat(path)
    except OSError:
        return _FileLineRead(outcome="vanished")
    if before.st_size > _MEASUREMENT_MAX_FILE_BYTES:
        return _FileLineRead(outcome="too-large")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    lines = 0
    total_read = 0
    first_chunk = True
    last_byte = b""
    try:
        fd = os.open(path, flags)
        try:
            opened = os.fstat(fd)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                return _FileLineRead(outcome="race")
            while True:
                if deadline is not None and time.monotonic() > deadline:
                    return _FileLineRead(
                        outcome="time-budget", lines=lines, bytes_read=total_read
                    )
                if remaining_bytes is not None:
                    if total_read >= remaining_bytes:
                        return _FileLineRead(
                            outcome="total-byte-budget", lines=lines, bytes_read=total_read
                        )
                    request = min(_MEASUREMENT_CHUNK, remaining_bytes - total_read)
                else:
                    request = _MEASUREMENT_CHUNK
                # The per-file ceiling is a BYTE bound on every read, not only on the verdict:
                # each request is clamped to the remaining allowance plus ONE probe byte, which
                # is the only way to tell "exactly at ceiling" (measurable) from "over ceiling"
                # (too-large). Neither the requested nor the accepted size may exceed ceiling+1,
                # and the total-byte clamp above stays absolute - the probe never spends bytes
                # the walk no longer has.
                request = min(request, _MEASUREMENT_MAX_FILE_BYTES - total_read + 1)
                chunk = os.read(fd, request)
                if not chunk:
                    break
                if first_chunk:
                    if b"\x00" in chunk:
                        return _FileLineRead(
                            outcome="binary", bytes_read=total_read + len(chunk)
                        )
                    first_chunk = False
                lines += chunk.count(b"\n")
                last_byte = chunk[-1:]
                total_read += len(chunk)
                if total_read > _MEASUREMENT_MAX_FILE_BYTES:
                    return _FileLineRead(outcome="too-large", lines=lines, bytes_read=total_read)
            if total_read and last_byte != b"\n":
                lines += 1
            post = os.fstat(fd)
            if (post.st_dev, post.st_ino) != (before.st_dev, before.st_ino):
                return _FileLineRead(outcome="race", lines=lines, bytes_read=total_read)
            if post.st_size != before.st_size or post.st_mtime_ns != before.st_mtime_ns:
                return _FileLineRead(outcome="mutated", lines=lines, bytes_read=total_read)
        finally:
            os.close(fd)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return _FileLineRead(outcome="race")
        return _FileLineRead(outcome="unreadable")
    try:
        after = os.lstat(path)
    except OSError:
        return _FileLineRead(outcome="post-stat-failed", lines=lines, bytes_read=total_read)
    if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
        return _FileLineRead(outcome="race", lines=lines, bytes_read=total_read)
    if after.st_size != before.st_size or after.st_mtime_ns != before.st_mtime_ns:
        return _FileLineRead(outcome="mutated", lines=lines, bytes_read=total_read)
    return _FileLineRead(outcome="measured", lines=lines, bytes_read=total_read)


def _relative_or_absolute(path, root: Path) -> str:
    """The path as the workspace-relative POSIX form the user sees, or absolute if outside."""
    try:
        return Path(path).relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _collect_exact_file_facts(
    root: Path,
    extension: str,
    *,
    max_entries: int,
    max_total_bytes: int,
    max_seconds: float,
) -> WorkspaceFileMeasurement:
    """Walk `root` once and return the typed measurement.

    `largest_*` is the largest file by LINE count among the counted files that could be read as
    text — a size ranking would answer a different question ("largest" in bytes is not what a line
    count asks), and counting lines requires reading each file, which is exactly what happens.
    Equal-line ties break on the normalized workspace-relative path, never on filesystem
    enumeration order. Symlinked directories are never followed, and matched files are opened
    no-follow with a dev/ino identity check, so the count cannot be tricked into reading or
    ranking anything outside the workspace it was handed.
    """
    suffix = f".{extension.lower()}"
    deadline = time.monotonic() + max_seconds
    count = 0
    matched_bytes = 0
    largest_relpath = ""
    largest_lines = -1
    skipped: dict[str, int] = {"binary": 0, "too-large": 0, "unreadable": 0}
    unscannable: list[str] = []
    raced: list[str] = []
    termination_reason = ""
    scanned = 0
    stopped = False
    stack = [root]
    while stack and not stopped:
        current = stack.pop()
        try:
            # Entries are STREAMED, never materialized: the entry and time budgets are enforced
            # inside the iteration, so a directory with millions of entries costs at most
            # max_entries + one lookahead, not its own size in memory.
            with os.scandir(current) as directory:
                for child in directory:
                    scanned += 1
                    if scanned > max_entries:
                        termination_reason = (
                            f"entry budget exceeded after {max_entries} entries"
                        )
                        stopped = True
                        break
                    if time.monotonic() > deadline:
                        termination_reason = (
                            f"time budget exceeded after {max_seconds:.0f}s"
                        )
                        stopped = True
                        break
                    name = child.name
                    try:
                        is_dir = child.is_dir(follow_symlinks=False)
                        is_file = child.is_file(follow_symlinks=False)
                    except OSError:
                        raced.append(name)
                        continue
                    if is_dir:
                        if name not in _MEASUREMENT_SKIP_DIRS:
                            stack.append(Path(child.path))
                        continue
                    if not is_file or not name.lower().endswith(suffix):
                        continue

                    read = _count_file_lines(
                        child.path,
                        remaining_bytes=max(0, max_total_bytes - matched_bytes),
                        deadline=deadline,
                    )
                    relative = _relative_or_absolute(child.path, root)
                    if read.outcome in _FILE_RACE_OUTCOMES:
                        raced.append(relative)
                        continue
                    if read.outcome == "total-byte-budget":
                        termination_reason = (
                            "byte budget exceeded after "
                            f"{matched_bytes + read.bytes_read:,} matched-file bytes"
                        )
                        stopped = True
                        break
                    if read.outcome == "time-budget":
                        termination_reason = (
                            f"time budget exceeded after {max_seconds:.0f}s"
                        )
                        stopped = True
                        break
                    count += 1
                    matched_bytes += read.bytes_read
                    if read.outcome == "measured":
                        if read.lines > largest_lines or (
                            read.lines == largest_lines and relative < largest_relpath
                        ):
                            largest_lines = read.lines
                            largest_relpath = relative
                    else:
                        skipped[read.outcome] = skipped.get(read.outcome, 0) + 1
        except OSError:
            relative = _relative_or_absolute(current, root)
            unscannable.append(relative)
            continue
    if not termination_reason and raced:
        termination_reason = (
            "entries swapped or vanished during measurement: " + ", ".join(raced[:8])
        )
    if not termination_reason and unscannable:
        termination_reason = "unscannable directories: " + ", ".join(unscannable[:8])
    return WorkspaceFileMeasurement(
        complete=not termination_reason,
        count=count,
        largest_relpath=largest_relpath,
        largest_lines=largest_lines,
        skipped=skipped,
        unscannable_directories=tuple(unscannable),
        raced_entries=tuple(raced),
        termination_reason=termination_reason,
    )


def measure_workspace_files(
    workspace_root: str,
    extension: str,
    *,
    max_entries: int = _MEASUREMENT_MAX_ENTRIES,
    max_total_bytes: int = _MEASUREMENT_MAX_TOTAL_BYTES,
    max_seconds: float = _MEASUREMENT_MAX_SECONDS,
) -> WorkspaceFileMeasurement | None:
    """The typed, race-hardened, bounded measurement of `workspace_root`'s matching files.

    Returns None when there is no usable root — the caller keeps its existing behaviour. Every
    number in the result was computed from disk during this call; `complete` is the exactness
    claim, and `termination_reason` names any bound or obstruction that made it not exact.
    """
    wanted = str(extension or "").strip().lstrip(".").lower()
    raw = str(workspace_root or "").strip()
    if not wanted or not raw:
        return None
    root = Path(os.path.expanduser(raw))
    try:
        if not root.is_dir():
            return None
    except OSError:
        return None
    return _collect_exact_file_facts(
        root,
        wanted,
        max_entries=max(1, int(max_entries)),
        max_total_bytes=max(1, int(max_total_bytes)),
        max_seconds=max(0.0, float(max_seconds)),
    )


def build_exact_file_facts(
    workspace_root: str,
    extension: str,
    *,
    label: str = "",
    max_entries: int = _MEASUREMENT_MAX_ENTRIES,
    max_total_bytes: int = _MEASUREMENT_MAX_TOTAL_BYTES,
    max_seconds: float = _MEASUREMENT_MAX_SECONDS,
) -> str | None:
    """A counted, never-estimated answer to "how many <ext> files, and which has the most lines".

    The folder overview above answers "what is this project"; this answers a MEASUREMENT of it.
    Serving the overview for a measurement was the recorded defect: an exact-count request came
    back as "Top level (30 folders, 48 files)" — the tree's own folder/file counts masquerading as
    the requested number — with none of the three requested facts in the answer.

    Returns the fact text, or None when there is no usable root (the caller keeps its existing
    behaviour). The exact wording is served ONLY from a COMPLETE measurement; when a budget fired,
    a directory could not be scanned, or entries changed mid-walk, the reply is a bounded partial
    that names why exactness could not be established — never dressed up as exact.
    """
    wanted = str(extension or "").strip().lstrip(".").lower()
    raw = str(workspace_root or "").strip()
    if not wanted or not raw:
        return None
    root = Path(os.path.expanduser(raw))
    try:
        if not root.is_dir():
            return None
    except OSError:
        return None

    facts = _collect_exact_file_facts(
        root,
        wanted,
        max_entries=max(1, int(max_entries)),
        max_total_bytes=max(1, int(max_total_bytes)),
        max_seconds=max(0.0, float(max_seconds)),
    )
    subject = f"{label} files" if label else f".{wanted} files"
    scope = (
        f"Scope: every file anywhere under the workspace root ending in `.{wanted}`, excluding the "
        f"standard non-project directories ({', '.join(sorted(_MEASUREMENT_SKIP_DIRS))}). "
        "Line counts are exact reads of every file, not estimates."
    )

    if not facts.complete:
        partial_scope = (
            f"Scope: files under the workspace root ending in `.{wanted}`, excluding the standard "
            "non-project directories. This is NOT offered as an exact count: the traversal ended "
            "early, so files were not all seen."
        )
        lines: list[str] = [
            f"Partial count — exactness could not be established "
            f"({facts.termination_reason}).",
            "",
            f"- {subject.capitalize()} (.{wanted}) counted so far: {facts.count:,}",
        ]
        if facts.largest_relpath:
            lines.append(
                f"- Largest by line count so far: `{facts.largest_relpath}` — "
                f"{facts.largest_lines:,} lines"
            )
        lines.extend(["", partial_scope])
        return "\n".join(lines)

    if facts.count == 0:
        return (
            f"Counted from the bound workspace `{root}`: no {subject} were found — the count is 0.\n\n{scope}"
        )

    lines = [
        f"Counted, not estimated, from the bound workspace `{root}`.",
        "",
        f"- {subject.capitalize()} (.{wanted}): {facts.count:,}",
    ]
    if facts.largest_relpath:
        lines.append(
            f"- Largest by line count: `{facts.largest_relpath}` — "
            f"{facts.largest_lines:,} lines"
        )
    noted = [f"{total} {reason}" for reason, total in sorted(facts.skipped.items()) if total]
    if noted:
        lines.append(
            f"- Not line-counted ({', '.join(noted)}): these files were counted above but are not "
            "readable text, so they are excluded from the largest-file ranking."
        )
    lines.extend(["", scope])
    return "\n".join(lines)


__all__ = ["build_folder_overview", "build_exact_file_facts", "measure_workspace_files"]
