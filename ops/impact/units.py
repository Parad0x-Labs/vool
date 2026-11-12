"""Code-unit identity, and the mapping from a git diff to the units it changed.

A *code unit* is the smallest thing this repository can execute, and therefore the smallest
thing a proof can be *observed* to reach:

    core/task_router.py::route_task          a function or method
    core/task_router.py::Router.decide       a method, qualified the way CPython qualifies it
    core/task_router.py::<module>            the import-time body of a module
    config/release/update_channel.json       a non-Python asset, which nothing executes

Units are the currency of the whole governor. Everything downstream -- which invariants a
change puts at risk, which proofs are required, whether those proofs actually ran the changed
code -- is stated in terms of them. Paths are repo-relative POSIX so a unit key is identical
on every machine and inside a coverage artifact committed to the repo.

Line numbers are deliberately NOT part of a unit key. A unit must survive an edit that shifts
the file, or every diff would appear to change every unit below it.
"""

from __future__ import annotations

import ast
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

MODULE_SCOPE = "<module>"

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

# Derived files are not source. A checkout whose ignore rules do not happen to cover bytecode
# must not have its compiled output reported as a changed asset needing a proof.
_DERIVED_SUFFIXES = (".pyc", ".pyo", ".pyd", ".so", ".o", ".class")
_DERIVED_PARTS = ("__pycache__/", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/", ".egg-info/")


def is_derived(path: str) -> bool:
    return path.endswith(_DERIVED_SUFFIXES) or any(part in path for part in _DERIVED_PARTS)


def unit_key(path: str, qualname: str | None) -> str:
    """Render the canonical string form of a unit."""
    if qualname is None:
        return path
    return f"{path}::{qualname}"


def split_unit(key: str) -> tuple[str, str | None]:
    """Inverse of :func:`unit_key`."""
    path, sep, qualname = key.partition("::")
    return (path, qualname if sep else None)


@dataclass(frozen=True)
class UnitSpan:
    """The line range a named callable occupies, including its decorators."""

    start: int
    end: int
    qualname: str


def index_units(source: str) -> tuple[UnitSpan, ...]:
    """Index every named callable in ``source``, qualified as CPython qualifies it.

    Lambdas, comprehensions and generator expressions are deliberately not indexed. They have
    code objects at runtime, but they are not independently addressable behavior: a line inside
    one resolves to the innermost *named* unit containing it, which is what a reader looking at
    a diff would call the changed thing.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return ()

    spans: list[UnitSpan] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                qualname = f"{prefix}{child.name}"
                start = child.lineno
                for decorator in child.decorator_list:
                    start = min(start, decorator.lineno)
                spans.append(UnitSpan(start, child.end_lineno or child.lineno, qualname))
                visit(child, f"{qualname}.<locals>.")
            elif isinstance(child, ast.ClassDef):
                qualname = f"{prefix}{child.name}"
                visit(child, f"{qualname}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return tuple(spans)


def qualname_for_line(spans: tuple[UnitSpan, ...], line: int) -> str:
    """Return the innermost unit containing ``line``; module scope when no callable does.

    Import-time statements -- imports, constants, class bodies, dataclass field defaults --
    all land on ``<module>``, because import-time is when they actually execute.
    """
    best: UnitSpan | None = None
    for span in spans:
        if span.start <= line <= span.end:
            if best is None or (span.end - span.start) < (best.end - best.start):
                best = span
    return best.qualname if best is not None else MODULE_SCOPE


@dataclass(frozen=True)
class ChangedUnits:
    """What a diff changed, expressed as units rather than as files."""

    units: tuple[str, ...]
    paths: tuple[str, ...]
    assets: tuple[str, ...]


def _git(repo_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed ({completed.returncode}): {completed.stderr.strip()}")
    return completed.stdout


def _blob_at(repo_root: Path, ref: str, path: str) -> str | None:
    completed = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout


def _worktree_text(repo_root: Path, path: str) -> str | None:
    candidate = repo_root / path
    try:
        return candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


@dataclass(frozen=True)
class _Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int


_C_ESCAPES = {
    "a": b"\a", "b": b"\b", "t": b"\t", "n": b"\n",
    "v": b"\v", "f": b"\f", "r": b"\r", '"': b'"', "\\": b"\\",
}


def _unquote(raw: str) -> str:
    """Undo git's C-style path quoting.

    With the default `core.quotepath`, git renders any path outside plain ASCII as a quoted,
    backslash-escaped string: ``"a/caf\\303\\251.py"``. Read literally that is a path that does
    not exist, so the file resolves to nothing and its units vanish from the report -- a silent
    miss, in the direction that looks like success.
    """
    if len(raw) < 2 or not (raw.startswith('"') and raw.endswith('"')):
        return raw
    body = raw[1:-1]
    out = bytearray()
    index = 0
    while index < len(body):
        char = body[index]
        if char == "\\" and index + 1 < len(body):
            following = body[index + 1]
            if following in _C_ESCAPES:
                out += _C_ESCAPES[following]
                index += 2
                continue
            octal = body[index + 1 : index + 4]
            if len(octal) == 3 and all(digit in "01234567" for digit in octal):
                out.append(int(octal, 8))
                index += 4
                continue
        out += char.encode("utf-8")
        index += 1
    return out.decode("utf-8", errors="replace")


def _side_path(raw: str) -> str | None:
    target = _unquote(raw.strip().rstrip("\t"))
    if target == "/dev/null":
        return None
    return target[2:] if target.startswith(("a/", "b/")) else target


def parse_diff(diff_text: str) -> tuple[dict[tuple[str | None, str | None], list[_Hunk]], set[tuple[str, str]]]:
    """Parse ``git diff --unified=0`` into ``((old, new) -> hunks, renamed pairs)``.

    ``old_path`` is None for an added file and ``new_path`` is None for a deleted one, so a
    rename that also edits the file yields both sides and both get mapped.

    Renames are collected separately because a pure rename emits ``rename from``/``rename to``
    and no hunks at all -- no ``---``, no ``+++``, nothing for a hunk parser to find. Moving a
    governed file to a new name would otherwise be the simplest way to leave governance without
    anything noticing.
    """
    result: dict[tuple[str | None, str | None], list[_Hunk]] = {}
    renamed: set[tuple[str, str]] = set()
    old_path: str | None = None
    new_path: str | None = None
    rename_source: str | None = None
    key: tuple[str | None, str | None] | None = None

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            old_path = None
            new_path = None
            rename_source = None
            key = None
            continue
        if line.startswith(("rename from ", "copy from ")):
            rename_source = _unquote(line.split(" from ", 1)[1].strip())
            continue
        if line.startswith(("rename to ", "copy to ")):
            destination = _unquote(line.split(" to ", 1)[1].strip())
            if rename_source and destination:
                renamed.add((rename_source, destination))
                old_path, new_path = rename_source, destination
                key = (old_path, new_path)
                result.setdefault(key, [])
            continue
        if line.startswith("--- "):
            old_path = _side_path(line[4:])
            continue
        if line.startswith("+++ "):
            new_path = _side_path(line[4:])
            key = (old_path, new_path)
            result.setdefault(key, [])
            continue
        if line.startswith("Binary files "):
            # A binary change carries no hunks; record the pair so it still counts as touched.
            match = re.match(r"^Binary files (.+?) and (.+?) differ$", line)
            if match:
                result.setdefault((_side_path(match.group(1)), _side_path(match.group(2))), [])
            continue
        if line.startswith("@@") and key is not None:
            match = _HUNK_RE.match(line)
            if not match:
                continue
            old_start = int(match.group(1))
            old_count = 1 if match.group(2) is None else int(match.group(2))
            new_start = int(match.group(3))
            new_count = 1 if match.group(4) is None else int(match.group(4))
            result[key].append(_Hunk(old_start, old_count, new_start, new_count))
    return result, renamed


def _all_units(source: str | None, path: str) -> set[str]:
    """Every unit in a file, for changes with no hunks to narrow them down."""
    if source is None:
        return set()
    found = {unit_key(path, MODULE_SCOPE)}
    found.update(unit_key(path, span.qualname) for span in index_units(source))
    return found


def _units_for_side(source: str | None, path: str, hunks: list[_Hunk], *, side: str) -> set[str]:
    if source is None:
        return set()
    spans = index_units(source)
    found: set[str] = set()
    for hunk in hunks:
        start = hunk.old_start if side == "old" else hunk.new_start
        count = hunk.old_count if side == "old" else hunk.new_count
        if count <= 0:
            # A pure insertion has no lines on the old side (and vice versa). The neighbouring
            # line is still the place the edit landed, so attribute it there rather than drop it.
            found.add(unit_key(path, qualname_for_line(spans, max(1, start))))
            continue
        for line in range(start, start + count):
            found.add(unit_key(path, qualname_for_line(spans, line)))
    return found


def changed_units(
    repo_root: Path,
    base_ref: str,
    *,
    head_ref: str | None = None,
) -> ChangedUnits:
    """Map the diff between ``base_ref`` and the working tree (or ``head_ref``) to units.

    Both sides of every hunk are mapped. A deleted function exists only on the old side, and a
    change that deletes behavior is exactly the kind a governor must not miss.
    """
    range_arg = f"{base_ref}..{head_ref}" if head_ref else base_ref
    diff_text = _git(
        repo_root,
        "diff",
        "--unified=0",
        "--no-color",
        "--no-ext-diff",
        "--find-renames",
        range_arg,
    )

    units: set[str] = set()
    assets: set[str] = set()
    paths: set[str] = set()

    if head_ref is None:
        # `git diff <base>` does not mention a file git has never seen. Left out, the whole of a
        # brand-new module would be invisible to the governor -- which is precisely the change
        # most in need of being looked at.
        untracked = _git(repo_root, "ls-files", "--others", "--exclude-standard")
        for line in untracked.splitlines():
            # `ls-files` C-quotes exactly the way the diff header does.
            path = _unquote(line.strip())
            if not path or is_derived(path):
                continue
            paths.add(path)
            if not path.endswith(".py"):
                assets.add(path)
                continue
            source = _worktree_text(repo_root, path)
            if source is None:
                assets.add(path)
                continue
            units.add(unit_key(path, MODULE_SCOPE))
            units.update(unit_key(path, span.qualname) for span in index_units(source))

    hunks_by_pair, renamed = parse_diff(diff_text)
    for (old_path, new_path), hunks in hunks_by_pair.items():
        if any(path is not None and is_derived(path) for path in (old_path, new_path)):
            continue
        for path in (old_path, new_path):
            if path is None:
                continue
            paths.add(path)
        # A rename changes the identity of everything in the file even when no line moved: every
        # declared surface, every recorded edge and every import naming the old path is now wrong.
        moved = old_path is not None and new_path is not None and (old_path, new_path) in renamed
        if new_path is not None and new_path.endswith(".py"):
            source = _worktree_text(repo_root, new_path) if head_ref is None else _blob_at(repo_root, head_ref, new_path)
            units |= _all_units(source, new_path) if moved else _units_for_side(source, new_path, hunks, side="new")
        elif new_path is not None:
            assets.add(new_path)
        if old_path is not None and old_path.endswith(".py"):
            before = _blob_at(repo_root, base_ref, old_path)
            units |= _all_units(before, old_path) if moved else _units_for_side(before, old_path, hunks, side="old")
        elif old_path is not None:
            assets.add(old_path)

    return ChangedUnits(
        units=tuple(sorted(units)),
        paths=tuple(sorted(paths)),
        assets=tuple(sorted(assets)),
    )
