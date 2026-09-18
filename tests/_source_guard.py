"""Deliberate source mutation that survives being killed.

`try: mutate ... finally: restore` is correct for every failure Python can see: an assertion, an
exception, a non-zero exit. It is not correct for the failures a proof harness actually meets --
SIGKILL from a supervisor, a worker reaped on a timeout, the machine losing power, someone pressing
ctrl-C twice. `finally` does not run, and a **tracked production file is left holding a deliberate
defect**. The next run then measures a tree nobody intended, and it looks green.

Two mechanisms, because neither alone is enough:

1. **A journal on disk, written BEFORE the mutation.** It holds the original bytes. If the process
   dies at any point after that, the bytes are still recoverable from a file that has nothing to do
   with the dead process.
2. **Recovery at startup.** `recover_orphaned()` restores from any journal left behind by a previous
   run and reports what it repaired. A journal is only deleted after its file is verified back to
   the original.

The order matters and is the whole design: journal first, mutate second, verify-then-unjournal last.
Mutating before journalling leaves a window where the defect is on disk and the original is nowhere.

A green proof over a dirty tracked tree is not a result, so `assert_tree_is_clean()` is the other
half: the controller checks before it declares anything, and fails closed if the tree is dirty.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import signal
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Journals live inside the repo on purpose: a run killed on a machine whose temp directory is
#: cleared on reboot must still be recoverable from the checkout itself. Gitignored.
JOURNAL_DIR = REPO_ROOT / ".vool_source_journal"


def _journal_path(relative_path: str) -> Path:
    return JOURNAL_DIR / (relative_path.replace(os.sep, "__").replace("/", "__") + ".json")


def _write_journal(relative_path: str, original: bytes) -> Path:
    """Write the journal ATOMICALLY. There is no moment where it exists but is incomplete.

    The previous version opened the destination with `"w"`, which truncates immediately. The matrix
    also wrote the journal twice -- once in `_apply`, once in the loop -- so the second write
    truncated a good journal while the source on disk was ALREADY mutated. A kill inside that window
    left a zero-byte journal beside a modified tracked file, and `recover_orphaned` had nothing to
    recover from. The window was real and is the reason for every line below.

    Temp file in the same directory, fsync the DATA, then `os.replace` -- atomic on POSIX and
    Windows -- then fsync the DIRECTORY so the rename itself survives a power loss. A reader
    therefore sees either the old journal or the new one, never a half of either.
    """
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    path = _journal_path(relative_path)
    payload = {
        "path": relative_path,
        "original_b64": base64.b64encode(original).decode("ascii"),
        "sha256": hashlib.sha256(original).hexdigest(),
        "bytes": len(original),
        "pid": os.getpid(),
    }
    tmp = path.with_suffix(".json.partial")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    with contextlib.suppress(OSError):  # directory fsync is not available everywhere
        fd = os.open(str(JOURNAL_DIR), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return path


class JournalIntegrityError(RuntimeError):
    """A journal exists but cannot be trusted. Never repaired by guessing; always fail closed."""


def _read_journal(journal: Path) -> dict:
    """Parse and VERIFY one journal. Raises `JournalIntegrityError` rather than returning junk."""
    raw = journal.read_bytes()
    if not raw.strip():
        raise JournalIntegrityError(f"{journal.name} is empty -- a kill landed inside a write")
    try:
        payload = json.loads(raw.decode("utf-8"))
        original = base64.b64decode(payload["original_b64"])
        relative_path = str(payload["path"])
    except Exception as exc:
        raise JournalIntegrityError(f"{journal.name} is unreadable: {type(exc).__name__}: {exc}") from exc
    digest = payload.get("sha256")
    if digest and hashlib.sha256(original).hexdigest() != digest:
        raise JournalIntegrityError(f"{journal.name} does not match its own checksum")
    size = payload.get("bytes")
    if size is not None and int(size) != len(original):
        raise JournalIntegrityError(f"{journal.name} claims {size} bytes and carries {len(original)}")
    candidate = (REPO_ROOT / relative_path).resolve()
    try:
        candidate.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise JournalIntegrityError(f"{journal.name} escapes the repository: {relative_path!r}") from exc
    return {"path": relative_path, "original": original}


def _trusted_head_bytes(relative_path: str) -> bytes:
    """Bytes Git HEAD records for a tracked path, or fail closed."""
    git = next(
        (path for path in (Path("/usr/bin/git"), Path("/bin/git"), Path("/usr/local/bin/git")) if path.is_file()),
        None,
    )
    if git is None:
        raise JournalIntegrityError("trusted git executable is unavailable")
    completed = subprocess.run(
        [str(git), "-C", str(REPO_ROOT), "show", f"HEAD:{relative_path}"],
        cwd="/",
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "LC_ALL": "C"},
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise JournalIntegrityError(
            f"{relative_path!r} is not trusted tracked source at HEAD: "
            + completed.stderr.decode("utf-8", "replace")[:160]
        )
    return completed.stdout


def _journal_entries_and_problems() -> tuple[dict[Path, dict], list[str]]:
    """Read every journal, then validate provenance and cross-journal uniqueness."""
    entries: dict[Path, dict] = {}
    problems: list[str] = []
    by_path: dict[str, list[Path]] = {}
    for journal in sorted(JOURNAL_DIR.glob("*.json")):
        try:
            entry = _read_journal(journal)
        except JournalIntegrityError as exc:
            problems.append(str(exc))
            continue
        entries[journal] = entry
        by_path.setdefault(entry["path"], []).append(journal)
    for relative_path, journals in by_path.items():
        if len(journals) > 1:
            problems.append(
                f"multiple journals claim {relative_path!r}: "
                + ", ".join(journal.name for journal in journals)
            )
    for journal, entry in entries.items():
        try:
            trusted = _trusted_head_bytes(entry["path"])
        except JournalIntegrityError as exc:
            problems.append(f"{journal.name}: {exc}")
            continue
        if entry["original"] != trusted:
            problems.append(
                f"{journal.name}: journal original disagrees with git HEAD for {entry['path']!r}"
            )
    return entries, list(dict.fromkeys(problems))


def _invalidate_bytecode(path: Path) -> None:
    """Drop any `.pyc` for `path`.

    Two mutations of one file can be the same size inside one mtime second, and CPython will then
    serve the first one's bytecode to the second run -- which reads as a survivor. Measured, not
    hypothetical: it produced a false survivor in this harness.
    """
    cache = path.parent / "__pycache__"
    if cache.is_dir():
        for stale in cache.glob(f"{path.stem}.*.pyc"):
            stale.unlink(missing_ok=True)


@contextlib.contextmanager
def mutated_source(relative_path: str, new_bytes: bytes) -> Iterator[Path]:
    """Put `new_bytes` on disk at `relative_path`, restoring it however this block ends.

    Survives an exception through `finally`, and survives a kill through the journal: the original
    is on disk before the mutation is, so a later `recover_orphaned()` can put it back.
    """
    path = REPO_ROOT / relative_path
    original = path.read_bytes()
    journal = _write_journal(relative_path, original)
    try:
        path.write_bytes(new_bytes)
        _invalidate_bytecode(path)
        yield path
    finally:
        path.write_bytes(original)
        _invalidate_bytecode(path)
        if path.read_bytes() == original:
            journal.unlink(missing_ok=True)
        # If it does not match, the journal STAYS, so the next `recover_orphaned()` repairs it.


def recover_orphaned() -> list[str]:
    """Restore anything a previous run left mutated. Returns the paths repaired.

    Idempotent, and safe to call when nothing is wrong: with no journals it does nothing. A journal
    whose file already matches the original is a clean exit that died before unlinking, so it is
    simply removed.
    """
    if not JOURNAL_DIR.is_dir():
        return []
    repaired: list[str] = []
    # A `.partial` is a write that never completed. The destination journal is untouched by
    # definition -- `os.replace` is atomic -- so the partial is debris, not data.
    for partial in sorted(JOURNAL_DIR.glob("*.partial")):
        partial.unlink(missing_ok=True)
    entries, problems = _journal_entries_and_problems()
    if problems:
        # Validate the WHOLE set before changing one byte. Otherwise an earlier valid-looking
        # journal can restore a path and a later conflicting one can clobber it again.
        return []
    for journal, entry in entries.items():
        relative_path, original = entry["path"], entry["original"]
        path = REPO_ROOT / relative_path
        # ``original`` was just proven byte-identical to git HEAD. It is therefore trusted source
        # truth rather than merely a journal's story about itself.
        if path.read_bytes() != original:
            path.write_bytes(original)
            _invalidate_bytecode(path)
            repaired.append(relative_path)
        journal.unlink(missing_ok=True)
    with contextlib.suppress(OSError):
        JOURNAL_DIR.rmdir()
    return repaired


def dirty_tracked_paths() -> list[str]:
    """Tracked files git reports as modified. Empty is the only acceptable state after a proof."""
    completed = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=False,
    )
    if completed.returncode != 0:
        # Not a git checkout, or git is unavailable. Say so rather than reporting a clean tree.
        return [f"<git status unavailable: rc={completed.returncode} {completed.stderr.strip()[:120]}>"]
    return [line[3:].strip() for line in completed.stdout.splitlines() if line.strip()]


def unrecoverable_journals() -> list[str]:
    """Journals that exist and cannot be trusted. Any of these means the source state is UNKNOWN."""
    if not JOURNAL_DIR.is_dir():
        return []
    _entries, problems = _journal_entries_and_problems()
    return problems


def assert_tree_is_clean(context: str, *, executable_only: bool = False) -> list[str]:
    """Fail closed on a dirty tree. Returns the dirt it tolerated, which is never hidden.

    `executable_only` narrows the refusal to files pytest can import or execute -- anything ending
    `.py`. That narrowing is a judgement, so it is stated rather than assumed: a modified `.py` file
    can change what every mutation in the run measures, and a modified `docs/*.md` cannot. A blanket
    refusal that fires on an unrelated documentation edit is a check people route around, and a
    check people route around protects nothing.

    Either way the FULL dirty list is returned so the caller can print it. Tolerated is not silent.
    """
    # A journal that cannot be read means a mutation may still be on disk and its original may be
    # gone. That is worse than a dirty tree, because nothing can say WHICH state the source is in.
    broken = unrecoverable_journals()
    if broken:
        raise AssertionError(
            f"{context}: a mutation journal is unreadable, so the state of tracked source is "
            "UNKNOWN and no result can be trusted:\n  " + "\n  ".join(broken)
        )
    dirty = dirty_tracked_paths()
    blocking = [path for path in dirty if path.endswith(".py")] if executable_only else list(dirty)
    if blocking:
        raise AssertionError(
            f"{context}: tracked source is modified, so no result from this run can be trusted:\n  "
            + "\n  ".join(blocking)
        )
    return dirty


def install_restore_on_signal(restore: "callable[[], None]") -> None:  # noqa: UP037
    """Restore on SIGTERM/SIGINT, then die of the original signal.

    `finally` does not run when a supervisor sends SIGTERM, which is exactly how a proof worker is
    reaped on a timeout. Re-raising with the default handler afterwards keeps the exit status
    accurate: the run was killed, and it should still look killed.
    """

    def _handler(signum, _frame):
        with contextlib.suppress(Exception):
            restore()
        with contextlib.suppress(Exception):
            recover_orphaned()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    for signum in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(Exception):
            signal.signal(signum, _handler)


if __name__ == "__main__":  # pragma: no cover - used by the kill test as a child process
    # `python -m tests._source_guard <relative_path> <marker>` mutates a file, announces that it
    # has, and then blocks forever waiting to be killed. The kill test uses it to produce exactly
    # the state `finally` cannot clean up.
    import time

    target, marker = sys.argv[1], sys.argv[2]
    source = (REPO_ROOT / target).read_bytes()
    with mutated_source(target, source + f"\n# {marker}\n".encode()):
        print("MUTATED", flush=True)
        while True:
            time.sleep(0.05)  # wait to be killed; SIGKILL will not let the `finally` above run
